"""
Vendor invoices (vendor reconciliation).

Admins and system admins pick the booked trips a vendor's bill covers for a
period, and the invoice adds up the costs the travel desk already recorded -
nobody types an amount. It stays editable until a super admin approves it;
only a super admin approves or rejects, and never one who prepared it. Every
admin tier can read and download invoices, and every step - downloads
included - is in the activity log under entity "invoice".

The rules about money (what can be billed, lines following costs, the lock
after approval) live in `services/invoices.py`; this module is the workflow.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, noload, selectinload

from app.core import clock
from app.core.deps import AdminUser, DbSession, InvoiceEditor, SuperAdminUser
from app.core.enums import EDITABLE_INVOICE_STATUSES, AuditAction, InvoiceStatus, Role
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.invoice import Invoice, InvoiceLine
from app.models.request import RequestTraveller
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.invoice import (
    EligibleRow,
    InvoiceCreate,
    InvoiceDecision,
    InvoiceEvent,
    InvoiceLineRead,
    InvoiceList,
    InvoicePayment,
    InvoiceRead,
    InvoiceRejection,
    InvoiceSummary,
    InvoiceUpdate,
)
from app.services import audit, costs, notifications
from app.services import invoices as svc

router = APIRouter(prefix="/invoices", tags=["invoices"])

#: How many times create tries the next invoice number after losing it to an
#: invoice created in the same moment.
_NUMBER_ATTEMPTS = 3

#: Statuses a draft can be deleted from: nothing has been approved, and a
#: submitted one is withdrawn by a super admin rejecting it, not by vanishing.
_DELETABLE = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.REJECTED})
_SUBMITTABLE = frozenset({InvoiceStatus.DRAFT, InvoiceStatus.REJECTED})


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _name(person: User | None) -> str | None:
    return person.full_name if person is not None else None


def _summary(invoice: Invoice, line_count: int) -> InvoiceSummary:
    return InvoiceSummary(
        id=invoice.id,
        number=invoice.number,
        vendor_id=invoice.vendor_id,
        vendor_name=invoice.vendor.name,
        vendor_kind=invoice.vendor.kind,
        period_start=invoice.period_start,
        period_end=invoice.period_end,
        status=invoice.status,
        currency=invoice.currency,
        total_amount=invoice.total_amount,
        line_count=line_count,
        vendor_invoice_ref=invoice.vendor_invoice_ref,
        created_by_name=_name(invoice.created_by),
        created_at=invoice.created_at,
        submitted_at=invoice.submitted_at,
        decided_at=invoice.decided_at,
        paid_on=invoice.paid_on,
        payment_reference=invoice.payment_reference,
    )


def _line_read(line: InvoiceLine, problem: str | None) -> InvoiceLineRead:
    traveller = line.traveller
    request = traveller.request
    return InvoiceLineRead(
        id=line.id,
        traveller_id=line.request_traveller_id,
        request_id=line.request_id,
        traveller_name=traveller.user.full_name if traveller.user else "",
        employee_code=traveller.user.employee_code if traveller.user else None,
        request_type=request.request_type,
        travel_date=line.travel_date,
        description=line.description,
        project_code=request.project.code if request.project else None,
        booking_reference=traveller.booking_reference,
        amount=line.amount,
        problem=problem,
    )


def _history(db: Session, invoice: Invoice) -> list[InvoiceEvent]:
    """The invoice's life from the activity log, oldest first: who created,
    edited, submitted, decided and downloaded it, with their comments."""
    rows = db.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == invoice.tenant_id,
            AuditLog.entity_type == "invoice",
            AuditLog.entity_id == invoice.id,
            *svc.since_created(invoice),
        )
        .order_by(AuditLog.id)
        .limit(500)
    ).scalars()
    return [
        InvoiceEvent(
            action=str(row.action),
            actor_name=row.actor_name,
            at=row.created_at,
            summary=row.summary,
            comment=row.reason,
        )
        for row in rows
    ]


def _read(db: Session, invoice: Invoice, viewer: User) -> InvoiceRead:
    found = svc.problems(db, invoice)
    editor = viewer.role in svc.EDITOR_ROLES
    open_ = invoice.status in EDITABLE_INVOICE_STATUSES
    vendor = invoice.vendor
    return InvoiceRead(
        **_summary(invoice, len(invoice.lines)).model_dump(),
        vendor_gstin=vendor.gstin,
        vendor_contact_name=vendor.contact_name,
        vendor_phone=vendor.phone,
        vendor_email=vendor.email,
        notes=invoice.notes,
        updated_by_name=_name(invoice.updated_by),
        updated_at=invoice.updated_at,
        submitted_by_name=_name(invoice.submitted_by),
        decided_by_name=_name(invoice.decided_by),
        decision_comment=invoice.decision_comment,
        paid_by_name=_name(invoice.paid_by),
        paid_at=invoice.paid_at,
        lines=[_line_read(line, found.get(line.id)) for line in invoice.lines],
        history=_history(db, invoice),
        can_edit=editor and open_,
        can_submit=editor
        and invoice.status in _SUBMITTABLE
        and bool(invoice.lines)
        and not found,
        can_delete=editor and invoice.status in _DELETABLE,
        can_decide=(
            viewer.role is Role.SUPER_ADMIN
            and invoice.status is InvoiceStatus.SUBMITTED
            and not svc.prepared_by(db, invoice, viewer)
        ),
        can_record_payment=(
            viewer.role is Role.SUPER_ADMIN and invoice.status is InvoiceStatus.APPROVED
        ),
    )


def _get(db: Session, actor: User, invoice_id: int) -> Invoice:
    """The invoice with its lines, each line's trip and request in the same
    round trips - a detail page of a month's trips is otherwise a query a line."""
    invoice = db.execute(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.lines)
            .joinedload(InvoiceLine.traveller)
            .joinedload(RequestTraveller.request)
        )
    ).unique().scalar_one_or_none()
    if invoice is None or invoice.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invoice not found.")
    return invoice


def _vendor(db: Session, actor: User, vendor_id: int) -> Vendor:
    """Any of the organisation's vendors, switched off or not: an invoice for
    a vendor no longer used is how their last bills get settled."""
    vendor = db.get(Vendor, vendor_id)
    if vendor is None or vendor.tenant_id != actor.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="That vendor is not on your list. Pick one from Vendors.",
        )
    return vendor


@router.get("", response_model=InvoiceList)
def list_invoices(
    actor: AdminUser,
    db: DbSession,
    invoice_status: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    vendor_id: Annotated[int | None, Query()] = None,
    payment: Annotated[
        Literal["paid", "unpaid"] | None,
        Query(description="Approved invoices that are paid, or still to be paid"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> InvoiceList:
    """Newest first. `counts` is per status for the vendor filter, whatever the
    status filter - the numbers on the tabs - and `payment_counts` splits the
    approved ones into paid and still to be paid."""
    scope = [Invoice.tenant_id == actor.tenant_id]
    if vendor_id is not None:
        scope.append(Invoice.vendor_id == vendor_id)
    filters = list(scope)
    if invoice_status is not None:
        filters.append(Invoice.status == invoice_status)
    if payment is not None:
        filters.append(Invoice.status == InvoiceStatus.APPROVED)
        filters.append(
            Invoice.paid_on.is_not(None) if payment == "paid" else Invoice.paid_on.is_(None)
        )

    rows = list(
        db.execute(
            select(Invoice)
            .where(*filters)
            .options(noload(Invoice.lines))
            .order_by(Invoice.id.desc())
            .limit(limit)
        )
        .unique()
        .scalars()
    )
    line_counts = (
        dict(
            db.execute(
                select(InvoiceLine.invoice_id, func.count(InvoiceLine.id))
                .where(InvoiceLine.invoice_id.in_([r.id for r in rows]))
                .group_by(InvoiceLine.invoice_id)
            ).all()
        )
        if rows
        else {}
    )
    by_status = dict(
        db.execute(
            select(Invoice.status, func.count(Invoice.id)).where(*scope).group_by(Invoice.status)
        ).all()
    )
    counts = {s.value: by_status.get(s, 0) for s in InvoiceStatus}
    paid = db.execute(
        select(func.count(Invoice.id)).where(
            *scope, Invoice.status == InvoiceStatus.APPROVED, Invoice.paid_on.is_not(None)
        )
    ).scalar_one()
    return InvoiceList(
        items=[_summary(r, line_counts.get(r.id, 0)) for r in rows],
        counts=counts,
        payment_counts={"paid": paid, "unpaid": counts[InvoiceStatus.APPROVED.value] - paid},
        total=sum(counts.values()),
    )


@router.get("/eligible/why")
def why_not_listed(
    actor: AdminUser,
    db: DbSession,
    vendor_id: Annotated[int, Query()],
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
) -> dict[str, int]:
    """Counts of the period's trips that cannot go on this vendor's invoice,
    by reason - shown when the list of billable trips comes back empty."""
    svc.check_period(start, end)
    _vendor(db, actor, vendor_id)
    return svc.why_not_listed(
        db, tenant_id=actor.tenant_id, vendor_id=vendor_id, start=start, end=end
    )


@router.get("/eligible", response_model=list[EligibleRow])
def eligible(
    actor: AdminUser,
    db: DbSession,
    vendor_id: Annotated[int, Query()],
    start: Annotated[date, Query()],
    end: Annotated[date, Query()],
    invoice_id: Annotated[int | None, Query()] = None,
    include_unassigned: Annotated[bool, Query()] = False,
) -> list[EligibleRow]:
    """The trips an invoice for this vendor and period could carry.

    With `invoice_id`, that invoice's own lines come first, marked, even if
    something has happened to them since - each with the reason it can no
    longer be billed, so the admin sees why it will come off.
    """
    svc.check_period(start, end)
    vendor = _vendor(db, actor, vendor_id)
    invoice = _get(db, actor, invoice_id) if invoice_id is not None else None

    rows: list[EligibleRow] = []
    seen: set[int] = set()
    if invoice is not None:
        taken = svc.billed_on(
            db, (line.request_traveller_id for line in invoice.lines), besides=invoice.id
        )
        for line in invoice.lines:
            traveller = line.traveller
            # Judged against the vendor and period being looked at now, which
            # the admin may not have saved yet.
            problem = (
                None
                if invoice.status is InvoiceStatus.APPROVED
                else svc.refusal(traveller, vendor=vendor, start=start, end=end, taken=taken)
            )
            rows.append(
                _eligible_row(traveller, on_this_invoice=True, problem=problem, amount=line.amount)
            )
            seen.add(traveller.id)

    for traveller in svc.eligible(
        db,
        tenant_id=actor.tenant_id,
        vendor_id=vendor.id,
        start=start,
        end=end,
        include_unassigned=include_unassigned,
        invoice_id=invoice_id,
    ):
        if traveller.id not in seen:
            rows.append(_eligible_row(traveller))
    rows.sort(key=lambda r: (r.travel_date or date.max, r.request_id, r.traveller_id))
    return rows


def _eligible_row(traveller, *, on_this_invoice=False, problem=None, amount=None) -> EligibleRow:
    request = traveller.request
    user = traveller.user
    return EligibleRow(
        traveller_id=traveller.id,
        request_id=request.id,
        traveller_name=user.full_name if user else "",
        employee_code=user.employee_code if user else None,
        request_type=request.request_type,
        travel_date=svc.travel_date(traveller),
        trip=svc.trip_summary(request),
        project_code=request.project.code if request.project else "",
        project_name=request.project.name if request.project else "",
        booking_reference=traveller.booking_reference,
        amount=(
            costs.to_money(traveller.cost_amount)
            if traveller.cost_amount is not None
            else (amount if amount is not None else svc.ZERO)
        ),
        vendor_id=traveller.vendor_id,
        on_this_invoice=on_this_invoice,
        problem=problem,
    )


@router.get("/{invoice_id}", response_model=InvoiceRead)
def get_invoice(invoice_id: int, actor: AdminUser, db: DbSession) -> InvoiceRead:
    return _read(db, _get(db, actor, invoice_id), actor)


# ---------------------------------------------------------------------------
# Preparing: admins and system admins
# ---------------------------------------------------------------------------


def _header(invoice: Invoice) -> dict:
    """The fields an edit can change, as the activity log names them."""
    return {
        "vendor": invoice.vendor.name,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "vendor_invoice_ref": invoice.vendor_invoice_ref,
        "notes": invoice.notes,
    }


def _flush_lines(db: Session) -> None:
    """Write the lines now, so a trip another admin billed in the same moment
    is a clear refusal rather than a 500 at commit."""
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "One of these trips was just put on another invoice. "
                "Reload the list and pick again."
            ),
        ) from None


def _done(db: Session, background: BackgroundTasks, queued: list[int]) -> None:
    db.commit()
    background.add_task(notifications.deliver_queued, queued)


@router.post("", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
def create_invoice(
    payload: InvoiceCreate,
    actor: InvoiceEditor,
    http_request: Request,
    db: DbSession,
) -> InvoiceRead:
    """Start a draft for one vendor and period, with the trips picked. The
    number is given here and never changes; the total is the lines' sum."""
    vendor = _vendor(db, actor, payload.vendor_id)
    travellers = svc.load_travellers(db, actor.tenant_id, payload.traveller_ids)

    invoice: Invoice | None = None
    lost: set[str] = set()
    for _ in range(_NUMBER_ATTEMPTS):
        candidate = Invoice(
            tenant_id=actor.tenant_id,
            number=svc.next_number(db, actor.tenant_id, also_taken=lost),
            vendor_id=vendor.id,
            period_start=payload.period_start,
            period_end=payload.period_end,
            status=InvoiceStatus.DRAFT,
            currency=costs.DEFAULT_CURRENCY,
            total_amount=svc.ZERO,
            vendor_invoice_ref=payload.vendor_invoice_ref,
            notes=payload.notes,
            created_by_id=actor.id,
            updated_by_id=actor.id,
        )
        candidate.vendor = vendor
        try:
            # A savepoint, so losing the race for a number undoes only this insert.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
        except IntegrityError:
            lost.add(candidate.number)
            continue
        invoice = candidate
        break
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not give this invoice a number. Please try again.",
        )

    lines = svc.replace_lines(db, invoice, travellers)
    moved = svc.refresh(invoice)
    _flush_lines(db)
    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=(
            f"{actor.full_name} created {invoice.number} for {vendor.name}, "
            f"{svc.period_label(invoice)}: {len(invoice.lines)} trips, "
            f"{svc.inr(invoice.total_amount)}"
        ),
        changes={
            **audit.diff({}, {"number": invoice.number, **_header(invoice)}),
            **lines,
            **moved,
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(invoice)
    return _read(db, invoice, actor)


@router.patch("/{invoice_id}", response_model=InvoiceRead)
def update_invoice(
    invoice_id: int,
    payload: InvoiceUpdate,
    actor: InvoiceEditor,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> InvoiceRead:
    """Change anything but the number, until it is approved.

    `traveller_ids`, when sent, is the whole new set of lines. A rejected
    invoice goes back to draft when edited - it is being fixed. A submitted
    one stays submitted, and the super admins are told it changed, rather than
    making someone reject it first just to correct a line.
    """
    invoice = _get(db, actor, invoice_id)
    if invoice.status not in EDITABLE_INVOICE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} is approved, so it can no longer change.",
        )
    sent = payload.model_dump(exclude_unset=True)
    before = _header(invoice)

    if "vendor_id" in sent and sent["vendor_id"] != invoice.vendor_id:
        vendor = _vendor(db, actor, sent["vendor_id"])
        invoice.vendor_id = vendor.id
        invoice.vendor = vendor
    start = sent.get("period_start", invoice.period_start)
    end = sent.get("period_end", invoice.period_end)
    svc.check_period(start, end)
    invoice.period_start, invoice.period_end = start, end
    for field in ("vendor_invoice_ref", "notes"):
        if field in sent:
            setattr(invoice, field, sent[field])

    # Keeping the lines it has still means checking them: a new vendor or
    # period can leave some of them out.
    ids = (
        sent["traveller_ids"]
        if "traveller_ids" in sent
        else [line.request_traveller_id for line in invoice.lines]
    )
    travellers = svc.load_travellers(db, actor.tenant_id, ids)
    lines = svc.replace_lines(db, invoice, travellers)
    moved = svc.refresh(invoice)
    changes = {**audit.diff(before, _header(invoice)), **lines, **moved}
    if not changes:
        return _read(db, invoice, actor)

    was = invoice.status
    if was is InvoiceStatus.SUBMITTED and not invoice.lines:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A submitted invoice needs at least one trip. Keep one, or ask a "
                "super admin to reject it so it can be deleted."
            ),
        )
    if was is InvoiceStatus.REJECTED:
        invoice.status = InvoiceStatus.DRAFT
        changes["status"] = {"from": was.value, "to": InvoiceStatus.DRAFT.value}
    invoice.updated_by_id = actor.id
    _flush_lines(db)

    tail = {
        InvoiceStatus.REJECTED: ", which goes back to draft",
        InvoiceStatus.SUBMITTED: " while it waits for approval",
    }.get(was, "")
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=(
            f"{actor.full_name} edited {invoice.number}{tail}: "
            f"{len(invoice.lines)} trips, {svc.inr(invoice.total_amount)}"
        ),
        changes=changes,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    queued: list[int] = []
    if was is InvoiceStatus.SUBMITTED:
        queued = svc.tell_super_admins(
            db,
            invoice,
            actor,
            kind="INVOICE_CHANGED",
            headline=f"{invoice.number} changed while waiting for you",
            what=(
                f"{actor.full_name} changed {invoice.number} from {invoice.vendor.name}. "
                f"It now has {len(invoice.lines)} trips, {svc.inr(invoice.total_amount)}. "
                "Check it again before approving."
            ),
        )
    _done(db, background, queued)
    db.refresh(invoice)
    return _read(db, invoice, actor)


@router.post("/{invoice_id}/submit", response_model=InvoiceRead)
def submit_invoice(
    invoice_id: int,
    actor: InvoiceEditor,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> InvoiceRead:
    """Send a draft, or a fixed rejected one, to the super admins to approve.
    Needs at least one trip, and every trip still billable."""
    invoice = _get(db, actor, invoice_id)
    if invoice.status not in _SUBMITTABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} is already {invoice.status.value.lower()}.",
        )
    moved = svc.refresh(invoice)
    svc.refuse_problems(db, invoice, "submitted")
    if not invoice.lines:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Add at least one trip before submitting this invoice.",
        )
    was = invoice.status
    invoice.status = InvoiceStatus.SUBMITTED
    invoice.submitted_by_id = actor.id
    invoice.submitted_by = actor
    invoice.submitted_at = naive_utcnow()
    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=(
            f"{actor.full_name} submitted {invoice.number} for approval: "
            f"{len(invoice.lines)} trips, {svc.inr(invoice.total_amount)}"
        ),
        changes={"status": {"from": was.value, "to": invoice.status.value}, **moved},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    queued = svc.tell_super_admins(
        db,
        invoice,
        actor,
        kind="INVOICE_SUBMITTED",
        headline=f"Invoice {invoice.number} to approve",
        what=(
            f"{actor.full_name} sent {invoice.number} from {invoice.vendor.name} for approval: "
            f"{len(invoice.lines)} trips, {svc.inr(invoice.total_amount)}."
        ),
    )
    _done(db, background, queued)
    db.refresh(invoice)
    return _read(db, invoice, actor)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_invoice(
    invoice_id: int, actor: InvoiceEditor, http_request: Request, db: DbSession
) -> Response:
    """Throw away a draft or a rejected invoice. Its trips become free to bill
    on another; its number is never given out again."""
    invoice = _get(db, actor, invoice_id)
    if invoice.status not in _DELETABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{invoice.number} is {invoice.status.value.lower()}, so it cannot be deleted. "
                + (
                    "Ask a super admin to reject it first."
                    if invoice.status is InvoiceStatus.SUBMITTED
                    else "An approved invoice is a record of what was paid."
                )
            ),
        )
    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=(
            f"{actor.full_name} deleted {invoice.number} for {invoice.vendor.name} "
            f"({len(invoice.lines)} trips, {svc.inr(invoice.total_amount)})"
        ),
        changes={
            "number": invoice.number,
            "status": invoice.status.value,
            "total": str(invoice.total_amount),
            "lines_removed": [svc.line_text(line) for line in invoice.lines[:50]],
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.delete(invoice)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Deciding: super admins only
# ---------------------------------------------------------------------------


def _decidable(db: Session, invoice: Invoice, actor: User) -> None:
    if invoice.status is not InvoiceStatus.SUBMITTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{invoice.number} is {invoice.status.value.lower()}, "
                "so there is nothing to decide. Only a submitted invoice can be."
            ),
        )
    if svc.prepared_by(db, invoice, actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You helped prepare this invoice, so another super admin must decide it.",
        )


def _decide(invoice: Invoice, actor: User, outcome: InvoiceStatus, comment: str | None) -> None:
    invoice.status = outcome
    invoice.decided_by_id = actor.id
    invoice.decided_by = actor
    invoice.decided_at = naive_utcnow()
    invoice.decision_comment = comment


@router.post("/{invoice_id}/approve", response_model=InvoiceRead)
def approve_invoice(
    invoice_id: int,
    payload: InvoiceDecision,
    actor: SuperAdminUser,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> InvoiceRead:
    """Approve the invoice as it stands. The lines are frozen from now on and
    the costs behind them locked; whoever prepared it is told."""
    invoice = _get(db, actor, invoice_id)
    _decidable(db, invoice, actor)
    if payload.paid and payload.paid_on is not None and payload.paid_on > clock.local_today():
        # Checked before anything moves, so a bad date never half-approves.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The payment date is in the future. Record it once it is paid.",
        )

    moved = svc.refresh(invoice)
    if moved:
        # Kept even if the approval is refused below: the lines are right, and
        # the page the approver reloads should show them.
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="invoice",
            entity_id=invoice.id,
            summary=f"{invoice.number} followed the travellers' current costs before approval",
            changes=moved,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=http_request,
        )
        db.commit()
    svc.refuse_problems(db, invoice, "approved")
    if (
        payload.expected_total is not None
        and costs.to_money(payload.expected_total) != invoice.total_amount
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"The total of {invoice.number} is now {svc.inr(invoice.total_amount)}, not "
                f"{svc.inr(payload.expected_total)} - a cost changed since you opened it. "
                "Check the lines and approve again."
            ),
        )

    _decide(invoice, actor, InvoiceStatus.APPROVED, payload.comment)
    audit.record(
        db,
        action=AuditAction.APPROVE,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=(
            f"{actor.full_name} approved {invoice.number} for {invoice.vendor.name}: "
            f"{len(invoice.lines)} trips, {svc.inr(invoice.total_amount)}"
        ),
        changes={
            "status": {"from": InvoiceStatus.SUBMITTED.value, "to": invoice.status.value},
            "total": str(invoice.total_amount),
        },
        reason=payload.comment,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    if payload.paid:
        _record_paid(db, invoice, actor, payload.paid_on, payload.payment_reference, http_request)
    queued = svc.tell_preparers(db, invoice, actor)
    _done(db, background, queued)
    db.refresh(invoice)
    return _read(db, invoice, actor)


def _record_paid(
    db: Session,
    invoice: Invoice,
    actor: User,
    paid_on: date | None,
    reference: str | None,
    http_request: Request,
) -> None:
    changes = svc.mark_paid(invoice, actor, paid_on=paid_on, reference=reference)
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"{actor.full_name} marked {invoice.number} {svc.payment_phrase(invoice)}",
        changes=changes,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )


@router.post("/{invoice_id}/payment", response_model=InvoiceRead)
def record_payment(
    invoice_id: int,
    payload: InvoicePayment,
    actor: SuperAdminUser,
    http_request: Request,
    db: DbSession,
) -> InvoiceRead:
    """Mark an approved invoice paid - the day the money went and the bank's
    reference - or, correcting a mistake, not paid after all (with a reason).
    Super admins only, like the approval. The preparers are told in the app."""
    invoice = _get(db, actor, invoice_id)
    if payload.paid:
        _record_paid(db, invoice, actor, payload.paid_on, payload.payment_reference, http_request)
    else:
        changes = svc.mark_unpaid(invoice)
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="invoice",
            entity_id=invoice.id,
            summary=f"{actor.full_name} marked {invoice.number} as not paid after all",
            changes=changes,
            reason=payload.comment,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=http_request,
        )
    svc.tell_preparers_of_payment(db, invoice, actor)
    db.commit()
    db.refresh(invoice)
    return _read(db, invoice, actor)


@router.post("/{invoice_id}/reject", response_model=InvoiceRead)
def reject_invoice(
    invoice_id: int,
    payload: InvoiceRejection,
    actor: SuperAdminUser,
    http_request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> InvoiceRead:
    """Send it back with a comment saying what to fix. The admins edit it -
    which puts it back to draft - and submit it again."""
    invoice = _get(db, actor, invoice_id)
    _decidable(db, invoice, actor)
    _decide(invoice, actor, InvoiceStatus.REJECTED, payload.comment)
    audit.record(
        db,
        action=AuditAction.REJECT,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"{actor.full_name} rejected {invoice.number} for {invoice.vendor.name}",
        changes={"status": {"from": InvoiceStatus.SUBMITTED.value, "to": invoice.status.value}},
        reason=payload.comment,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    queued = svc.tell_preparers(db, invoice, actor)
    _done(db, background, queued)
    db.refresh(invoice)
    return _read(db, invoice, actor)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

#: A cell Excel or Sheets would run as a formula gets a leading apostrophe,
#: the same guard the web app's own CSV downloads use.
_FORMULA = re.compile(r"^[=+\-@\t\r]")


def _cell(value) -> object:
    if isinstance(value, str) and _FORMULA.match(value):
        return f"'{value}"
    return value


def _csv(invoice: Invoice) -> str:
    """A header block saying what the invoice is and where it stands, then one
    row per trip and the total - what an accountant files beside the vendor's
    own bill."""
    status_line = invoice.status.value.title()
    if invoice.decided_by is not None and invoice.status in (
        InvoiceStatus.APPROVED, InvoiceStatus.REJECTED
    ):
        when = clock.time_label(clock.to_local(invoice.decided_at)) if invoice.decided_at else ""
        status_line += f" by {invoice.decided_by.full_name} on {when}"
    header = [
        ("Invoice", invoice.number),
        ("Status", status_line),
        ("Vendor", invoice.vendor.name),
        ("GSTIN", invoice.vendor.gstin or ""),
        ("Vendor's bill number", invoice.vendor_invoice_ref or ""),
        ("Period", svc.period_label(invoice)),
        ("Currency", invoice.currency),
        ("Total", f"{invoice.total_amount:.2f}"),
        ("Prepared by", _name(invoice.created_by) or ""),
    ]
    payment = svc.payment_label(invoice)
    if payment:
        header.append(("Payment", payment))
    if invoice.decision_comment:
        header.append(("Comment", invoice.decision_comment))
    if invoice.notes:
        header.append(("Notes", invoice.notes))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for label, value in header:
        writer.writerow([label, _cell(value)])
    writer.writerow([])
    writer.writerow(
        ["Line", "Travel date", "Description", "Booking reference", "Request", "Amount (INR)"]
    )
    for number, line in enumerate(invoice.lines, start=1):
        writer.writerow([
            number,
            line.travel_date.isoformat() if line.travel_date else "",
            _cell(line.description),
            _cell(line.traveller.booking_reference or ""),
            f"{line.request_id}",
            f"{line.amount:.2f}",
        ])
    writer.writerow(["", "", "", "", "Total", f"{invoice.total_amount:.2f}"])
    # The byte-order mark makes Excel read names and the middle dot as UTF-8.
    return "﻿" + buffer.getvalue()


@router.get("/{invoice_id}/export.csv")
def export_invoice(
    invoice_id: int, actor: AdminUser, http_request: Request, db: DbSession
) -> Response:
    """The invoice as a spreadsheet. A copy leaving the system is logged."""
    invoice = _get(db, actor, invoice_id)
    content = _csv(invoice)
    audit.record(
        db,
        action=AuditAction.EXPORT,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"{actor.full_name} downloaded {invoice.number} as CSV",
        changes={"format": "csv", "status": invoice.status.value, "total": str(invoice.total_amount)},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    vendor = re.sub(r"[^A-Za-z0-9]+", "-", invoice.vendor.name).strip("-")[:40]
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{invoice.number}-{vendor}.csv"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{invoice_id}/printed", status_code=status.HTTP_204_NO_CONTENT)
def record_print(
    invoice_id: int, actor: AdminUser, http_request: Request, db: DbSession
) -> Response:
    """The print view says it was opened for a PDF, so that copy is in the log
    beside the CSV downloads. The page itself is drawn in the browser."""
    invoice = _get(db, actor, invoice_id)
    audit.record(
        db,
        action=AuditAction.EXPORT,
        entity_type="invoice",
        entity_id=invoice.id,
        summary=f"{actor.full_name} opened {invoice.number} to print or save as PDF",
        changes={"format": "pdf", "status": invoice.status.value, "total": str(invoice.total_amount)},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
