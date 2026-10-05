"""
Vendor invoices: reconciling what a vendor bills against what the travel desk
recorded, and the rules every path that touches a billed cost obeys.

An invoice is a set of booked trips - one traveller's cost each - for one
vendor and one period. The money is never typed onto it. Each line is the
traveller's cost as an admin recorded it, the total is the sum of the lines,
and both follow the costs while the invoice can still change:

* **Editable until approved.** Admins and system admins prepare an invoice
  (DRAFT), send it for approval (SUBMITTED), and fix it if a super admin
  rejects it. Lines are refreshed from the travellers' costs whenever the
  invoice is saved or submitted, and whenever one of those costs changes.
* **Frozen on approval.** Only a super admin approves, and never one who helped
  prepare it. From then on the lines are the record of what was paid, and the
  costs behind them are locked: changing one would make the ledger disagree
  with a bill that has been settled.
* **Billed once.** A traveller's cost is on at most one invoice - the unique
  index on `invoice_lines.request_traveller_id` holds that even against two
  admins saving at the same moment.

Only BOOKED travellers can be billed, the same rule the cost reports use for
"spent": a cost on an approved but unticketed trip is a forecast, and a vendor
does not bill for a seat that was never issued.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.core import clock
from app.core.enums import AuditAction, InvoiceStatus, RequestType, Role, TravellerStatus
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.invoice import Invoice, InvoiceLine
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.invoice import MAX_LINES
from app.schemas.invoice import check_period as period_rule
from app.services import audit, costs, notifications
from app.services.insights import movement_date
from app.services.requests import queued_emails, trip_summary

NUMBER_PREFIX = "INV"

#: Who can be billed: travellers with a ticket. See the module docstring.
BILLABLE = frozenset({TravellerStatus.BOOKED})

#: How many refusals one error message lists before saying "and N more".
_LISTED = 3

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Numbers and words
# ---------------------------------------------------------------------------


def next_number(
    db: Session,
    tenant_id: str,
    *,
    year: int | None = None,
    also_taken: Iterable[str] = (),
) -> str:
    """The next unused INV-<year>-<number> for this organisation.

    Counted like campaign IDs (`services/projects.next_code`), with one more
    source: the activity log. A deleted draft's number is never handed out
    again, because the log still names it, and two different bills under one
    number is the confusion numbering exists to prevent.

    `also_taken` names numbers a caller just lost an insert race for: its
    transaction's snapshot cannot see the winner's row.
    """
    year = year or clock.local_today().year
    prefix = f"{NUMBER_PREFIX}-{year}-"
    existing = db.execute(
        select(Invoice.number).where(
            Invoice.tenant_id == tenant_id, Invoice.number.like(f"{prefix}%")
        )
    ).scalars()
    logged = db.execute(
        select(AuditLog.changes).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.entity_type == "invoice",
            AuditLog.action == AuditAction.CREATE,
            AuditLog.summary.like(f"%{prefix}%"),
        )
    ).scalars()
    from_log = [
        (changes.get("number") or {}).get("to")
        for changes in logged
        if isinstance(changes, dict) and isinstance(changes.get("number"), dict)
    ]
    numbers = [
        int(number[len(prefix):])
        for number in (*existing, *from_log, *also_taken)
        if isinstance(number, str)
        and number.upper().startswith(prefix)
        and number[len(prefix):].isdigit()
    ]
    return f"{prefix}{max(numbers, default=0) + 1:04d}"


def inr(amount: Decimal | None) -> str:
    """"INR 1,23,456.00" - Indian digit grouping, for emails and the log.

    The rupee sign stays out of Python for the reason `services/costs.py`
    gives: it reaches Windows console logs.
    """
    value = costs.to_money(amount if amount is not None else ZERO)
    whole, _, paise = f"{abs(value):.2f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"INR {'-' if value < 0 else ''}{whole}.{paise}"


def day(value: date | None) -> str:
    return value.strftime("%d %b %Y") if value else "no date"


def period_label(invoice: Invoice) -> str:
    return f"{day(invoice.period_start)} to {day(invoice.period_end)}"


def travel_date(traveller: RequestTraveller) -> date | None:
    """The day that decides which period a trip is billed in: a hotel's
    check-in, otherwise the day it starts. Trip times are stored as the
    traveller typed them, in India time, so the date is India's."""
    return movement_date(traveller.request)


def describe(traveller: RequestTraveller) -> str:
    """The line's text, kept with it so the invoice reads the same whatever
    later happens to the person's name or the campaign:
    "Ravi Kumar · Flight: Hyderabad to Delhi, 12 Oct 2026, 09:30 · CMP-2026-0002"."""
    request = traveller.request
    name = traveller.user.full_name if traveller.user else "Unknown traveller"
    parts = [name, trip_summary(request)]
    if request.project is not None:
        parts.append(request.project.code)
    return " · ".join(parts)[:300]


def _trip_of(traveller: RequestTraveller) -> str:
    name = traveller.user.full_name if traveller.user else "A traveller"
    on = travel_date(traveller)
    return f"{name}'s trip on {day(on)}" if on else f"{name}'s trip"


def _listed(problems: list[str]) -> str:
    shown = " ".join(problems[:_LISTED])
    more = len(problems) - _LISTED
    return f"{shown} And {more} more." if more > 0 else shown


# ---------------------------------------------------------------------------
# Which trips an invoice may carry
# ---------------------------------------------------------------------------


def check_period(start: date, end: date) -> None:
    """The schema's period rule, for a PATCH that sends only one end."""
    try:
        period_rule(start, end)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from None


def _in_period(start: date, end: date):
    """Trips whose travel date falls in the period, as a WHERE clause.

    `start_at` is a wall-clock time, so the day boundaries are plain midnights.
    """
    first = datetime.combine(start, time.min)
    after = datetime.combine(end + timedelta(days=1), time.min)
    return or_(
        and_(
            TravelRequest.request_type == RequestType.HOTEL,
            TravelRequest.check_in >= start,
            TravelRequest.check_in <= end,
        ),
        and_(
            TravelRequest.request_type != RequestType.HOTEL,
            TravelRequest.start_at >= first,
            TravelRequest.start_at < after,
        ),
    )


def eligible(
    db: Session,
    *,
    tenant_id: str,
    vendor_id: int,
    start: date,
    end: date,
    include_unassigned: bool = False,
    invoice_id: int | None = None,
) -> list[RequestTraveller]:
    """Booked trips with a cost, paid to this vendor, travelling in the period,
    and on no other invoice - earliest first.

    `include_unassigned` adds trips whose vendor nobody recorded: saving one
    onto the invoice records this vendor for it. `invoice_id` is the invoice
    being edited, whose own lines do not count as "on another invoice".
    """
    on_another = select(InvoiceLine.request_traveller_id)
    if invoice_id is not None:
        on_another = on_another.where(InvoiceLine.invoice_id != invoice_id)
    paid_to = RequestTraveller.vendor_id == vendor_id
    if include_unassigned:
        paid_to = or_(paid_to, RequestTraveller.vendor_id.is_(None))

    rows = (
        db.execute(
            select(RequestTraveller)
            .join(TravelRequest, RequestTraveller.request_id == TravelRequest.id)
            .where(
                TravelRequest.tenant_id == tenant_id,
                TravelRequest.is_draft.is_(False),
                TravelRequest.is_cancelled.is_(False),
                RequestTraveller.status.in_(BILLABLE),
                RequestTraveller.cost_amount.is_not(None),
                paid_to,
                _in_period(start, end),
                RequestTraveller.id.not_in(on_another),
            )
            .options(joinedload(RequestTraveller.request))
            .limit(MAX_LINES)
        )
        .unique()
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda t: (travel_date(t) or date.max, t.request_id, t.id))


def why_not_listed(
    db: Session, *, tenant_id: str, vendor_id: int, start: date, end: date
) -> dict[str, int]:
    """What stops trips in the period from showing for this vendor, counted.

    For the invoice screen's empty state: "3 booked trips have no cost yet"
    tells the admin what to do next; an empty table does not.
    """
    in_period = (
        db.execute(
            select(RequestTraveller)
            .join(TravelRequest, RequestTraveller.request_id == TravelRequest.id)
            .where(
                TravelRequest.tenant_id == tenant_id,
                TravelRequest.is_draft.is_(False),
                TravelRequest.is_cancelled.is_(False),
                _in_period(start, end),
            )
        )
        .scalars()
        .all()
    )
    billed = set(
        db.execute(
            select(InvoiceLine.request_traveller_id).where(
                InvoiceLine.request_traveller_id.in_([t.id for t in in_period] or [0])
            )
        ).scalars()
    )
    counts = {
        "trips_in_period": len(in_period),
        "not_booked_yet": 0,
        "booked_without_cost": 0,
        "other_vendor": 0,
        "no_vendor_recorded": 0,
        "already_invoiced": 0,
    }
    for t in in_period:
        if t.status is not TravellerStatus.BOOKED:
            if t.status in (TravellerStatus.PENDING, TravellerStatus.APPROVED):
                counts["not_booked_yet"] += 1
        elif t.cost_amount is None:
            counts["booked_without_cost"] += 1
        elif t.id in billed:
            counts["already_invoiced"] += 1
        elif t.vendor_id is None:
            counts["no_vendor_recorded"] += 1
        elif t.vendor_id != vendor_id:
            counts["other_vendor"] += 1
    return counts


def billed_on(
    db: Session, traveller_ids: Iterable[int], *, besides: int | None = None
) -> dict[int, Invoice]:
    """The invoice each of these travellers is billed on, if any."""
    ids = list(traveller_ids)
    if not ids:
        return {}
    query = (
        select(InvoiceLine.request_traveller_id, Invoice)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .where(InvoiceLine.request_traveller_id.in_(ids))
    )
    if besides is not None:
        query = query.where(Invoice.id != besides)
    return {traveller_id: invoice for traveller_id, invoice in db.execute(query).all()}


def refusal(
    traveller: RequestTraveller,
    *,
    vendor: Vendor,
    start: date,
    end: date,
    taken: dict[int, Invoice],
) -> str | None:
    """Why this trip cannot be billed to this vendor for this period as things
    stand, or None.

    The same reasons whether the trip is being added or is already a line that
    something has since happened to - the trip cancelled, its cost removed, the
    vendor changed - so the admin reads one explanation either way. `taken` is
    the other invoices trips are already on.
    """
    request = traveller.request
    what = _trip_of(traveller)
    if request.is_cancelled or traveller.status is TravellerStatus.CANCELLED:
        return f"{what} was cancelled. Take it off this invoice."
    if traveller.status not in BILLABLE:
        return f"{what} is {traveller.status.value.lower()}, not booked, so it cannot be billed."
    if traveller.cost_amount is None:
        return f"{what} has no cost recorded. Record its cost first, or take it off."
    if traveller.vendor_id is not None and traveller.vendor_id != vendor.id:
        paid = traveller.vendor.name if traveller.vendor else "another vendor"
        return f"{what} was paid to {paid}, not {vendor.name}."
    on = travel_date(traveller)
    if on is None or not start <= on <= end:
        return f"{what} is outside the invoice period ({day(start)} to {day(end)})."
    other = taken.get(traveller.id)
    if other is not None:
        return f"{what} is already on {other.number}."
    return None


def refusal_on(
    traveller: RequestTraveller, invoice: Invoice, taken: dict[int, Invoice]
) -> str | None:
    """`refusal` for an invoice's own vendor and period."""
    return refusal(
        traveller,
        vendor=invoice.vendor,
        start=invoice.period_start,
        end=invoice.period_end,
        taken=taken,
    )


def problems(db: Session, invoice: Invoice) -> dict[int, str]:
    """Line id -> why it cannot be billed now. Empty once approved: an
    approved line is a record of what was paid, not a question."""
    if invoice.status is InvoiceStatus.APPROVED or not invoice.lines:
        return {}
    taken = billed_on(db, (line.request_traveller_id for line in invoice.lines), besides=invoice.id)
    found = {}
    for line in invoice.lines:
        reason = refusal_on(line.traveller, invoice, taken)
        if reason:
            found[line.id] = reason
    return found


def refuse_problems(db: Session, invoice: Invoice, doing: str) -> None:
    found = problems(db, invoice)
    if found:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} cannot be {doing} yet. {_listed(list(found.values()))}",
        )


def load_travellers(db: Session, tenant_id: str, ids: list[int]) -> list[RequestTraveller]:
    """The picked trips, in the order picked. Another organisation's trip reads
    as not found, like anything else outside the caller's tenant."""
    if not ids:
        return []
    rows = (
        db.execute(
            select(RequestTraveller)
            .join(TravelRequest, RequestTraveller.request_id == TravelRequest.id)
            .where(RequestTraveller.id.in_(ids), TravelRequest.tenant_id == tenant_id)
            .options(joinedload(RequestTraveller.request))
        )
        .unique()
        .scalars()
        .all()
    )
    found = {t.id: t for t in rows}
    if len(found) != len(set(ids)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Some of the picked trips were not found. Reload the list and pick again.",
        )
    return [found[i] for i in ids]


def line_text(line: InvoiceLine) -> str:
    return f"{line.description} ({inr(line.amount)})"


def replace_lines(db: Session, invoice: Invoice, travellers: list[RequestTraveller]) -> dict:
    """Make the invoice carry exactly these trips. Every one must be billable
    here; the refusal names each that is not. Returns what was added and
    removed, for the activity log."""
    taken = billed_on(db, (t.id for t in travellers), besides=invoice.id)
    refused = [reason for t in travellers if (reason := refusal_on(t, invoice, taken))]
    if refused:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_listed(refused))

    current = {line.request_traveller_id: line for line in invoice.lines}
    wanted = {t.id for t in travellers}
    removed = [line for traveller_id, line in current.items() if traveller_id not in wanted]
    for line in removed:
        invoice.lines.remove(line)

    added: list[InvoiceLine] = []
    for traveller in travellers:
        if traveller.id in current:
            continue
        line = InvoiceLine(
            request_traveller_id=traveller.id,
            request_id=traveller.request_id,
            amount=costs.to_money(traveller.cost_amount),
            description=describe(traveller),
            travel_date=travel_date(traveller),
        )
        line.traveller = traveller
        invoice.lines.append(line)
        added.append(line)

    out: dict = {}
    if added:
        out["lines_added"] = [line_text(line) for line in added[:50]]
    if removed:
        out["lines_removed"] = [line_text(line) for line in removed[:50]]
    return out


def refresh(invoice: Invoice) -> dict:
    """Bring every line up to the traveller's current cost and the total up to
    the sum of the lines - while the invoice can still change; an approved one
    is never touched.

    A trip whose vendor nobody recorded gets this invoice's vendor: putting it
    on the bill is the admin saying who was paid. Returns what moved, for the
    activity log.
    """
    if invoice.status is InvoiceStatus.APPROVED:
        return {}
    amounts: dict[str, dict] = {}
    recorded: list[str] = []
    for line in invoice.lines:
        traveller = line.traveller
        if traveller.vendor_id is None:
            traveller.vendor = invoice.vendor
            traveller.vendor_id = invoice.vendor_id
            recorded.append(_trip_of(traveller))
        if traveller.cost_amount is not None:
            amount = costs.to_money(traveller.cost_amount)
            if amount != line.amount:
                amounts[_trip_of(traveller)] = {"from": str(line.amount), "to": str(amount)}
                line.amount = amount
        line.description = describe(traveller)
        line.travel_date = travel_date(traveller)
    before = invoice.total_amount
    invoice.total_amount = costs.to_money(sum((line.amount for line in invoice.lines), ZERO))

    out: dict = {}
    if amounts:
        out["amounts"] = amounts
    if recorded:
        out["vendor_recorded_for"] = recorded
    if before is None or costs.to_money(before) != invoice.total_amount:
        out["total"] = {
            "from": str(before) if before is not None else None,
            "to": str(invoice.total_amount),
        }
    return out


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------

#: The roles that prepare invoices. Matches `deps.InvoiceEditor`; used where
#: a read model tells the page which buttons to offer.
EDITOR_ROLES = frozenset({Role.ADMIN, Role.SYSTEM_ADMIN})

_PREPARING = (AuditAction.CREATE, AuditAction.UPDATE, AuditAction.SUBMIT)


def since_created(invoice: Invoice) -> list:
    """Log rows about this invoice, not an earlier one that had its id.

    MySQL 8 never hands out a deleted row's id again in normal running, but a
    restored backup or a migration run down and up again starts the count
    over, and the log keeps rows for invoices long gone. The create row is
    written after the invoice in the same transaction, so nothing about this
    one is older than it.
    """
    return [AuditLog.created_at >= invoice.created_at] if invoice.created_at else []


def prepared_by(db: Session, invoice: Invoice, user: User) -> bool:
    """Whether this person had a hand in the invoice - created, edited or
    submitted it. The rule is that nobody approves their own bill. A super
    admin cannot edit one, but an admin promoted to super admin could have,
    so the log is asked as well as the columns."""
    if user.id in (invoice.created_by_id, invoice.updated_by_id, invoice.submitted_by_id):
        return True
    return (
        db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.tenant_id == invoice.tenant_id,
                AuditLog.entity_type == "invoice",
                AuditLog.entity_id == invoice.id,
                AuditLog.actor_user_id == user.id,
                AuditLog.action.in_(_PREPARING),
                *since_created(invoice),
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


# ---------------------------------------------------------------------------
# Billed costs: locked once approved, followed until then
# ---------------------------------------------------------------------------


def _same_money(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return costs.to_money(a) == costs.to_money(b)


def guard_cost_change(
    db: Session, changes: list[tuple[RequestTraveller, Decimal | None, int | None]]
) -> None:
    """Refuse to change the cost or vendor of a trip an approved invoice billed.

    `changes` is (traveller, new amount, new vendor id) for each one about to
    be written. Saving the values it already has is not a change, so the cost
    form can be saved with an approved trip on it.
    """
    billed = billed_on(db, (t.id for t, _, _ in changes))
    for traveller, amount, vendor_id in changes:
        invoice = billed.get(traveller.id)
        if invoice is None or invoice.status is not InvoiceStatus.APPROVED:
            continue
        if not _same_money(traveller.cost_amount, amount) or vendor_id != traveller.vendor_id:
            name = traveller.user.full_name if traveller.user else "This traveller"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{name}'s cost is on invoice {invoice.number}, which is approved, "
                    "so its cost and vendor can no longer change."
                ),
            )


def follow_costs(
    db: Session, travellers: Iterable[RequestTraveller], *, actor: User, http_request=None
) -> list[int]:
    """After costs or vendors changed, bring the invoices those trips are on up
    to date, log it, and tell the super admins about one waiting on them - the
    total they are about to approve has moved. Returns queued email ids."""
    billed = billed_on(db, (t.id for t in travellers))
    queued: list[int] = []
    for invoice in {inv.id: inv for inv in billed.values()}.values():
        if invoice.status is InvoiceStatus.APPROVED:
            continue
        moved = refresh(invoice)
        if not moved.get("amounts") and "total" not in moved:
            continue
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="invoice",
            entity_id=invoice.id,
            summary=(
                f"{invoice.number} followed a cost {actor.full_name} changed: "
                f"total now {inr(invoice.total_amount)}"
            ),
            changes=moved,
            tenant_id=invoice.tenant_id,
            actor=actor,
            request=http_request,
        )
        if invoice.status is InvoiceStatus.SUBMITTED:
            queued += tell_super_admins(
                db,
                invoice,
                actor,
                kind="INVOICE_CHANGED",
                headline=f"{invoice.number} changed while waiting for you",
                what=(
                    f"A cost on {invoice.number} from {invoice.vendor.name} was changed by "
                    f"{actor.full_name}. The total is now {inr(invoice.total_amount)}."
                ),
            )
    return queued


# ---------------------------------------------------------------------------
# Payment, after approval
# ---------------------------------------------------------------------------


def payment_label(invoice: Invoice) -> str | None:
    """"Paid on 05 Oct 2026, ref UTR123" or "Not paid yet" for an approved
    invoice; None for one not approved, where payment does not arise."""
    if invoice.status is not InvoiceStatus.APPROVED:
        return None
    if invoice.paid_on is None:
        return "Not paid yet"
    label = f"Paid on {day(invoice.paid_on)}"
    if invoice.payment_reference:
        label += f", ref {invoice.payment_reference}"
    return label


def payment_phrase(invoice: Invoice) -> str:
    """The payment label mid-sentence: "paid on 05 Oct 2026, ref UTR123"."""
    label = payment_label(invoice) or ""
    return label[:1].lower() + label[1:]


def mark_paid(
    invoice: Invoice, actor: User, *, paid_on: date | None, reference: str | None
) -> dict:
    """Record the payment. Only an approved invoice is paid, and never on a
    day still to come - a payment is recorded once it has happened. Returns
    the change for the activity log."""
    if invoice.status is not InvoiceStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} is not approved, so it cannot be paid yet.",
        )
    when = paid_on or clock.local_today()
    if when > clock.local_today():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The payment date is in the future. Record it once it is paid.",
        )
    before = {"paid_on": invoice.paid_on, "payment_reference": invoice.payment_reference}
    invoice.paid_on = when
    invoice.payment_reference = reference
    invoice.paid_by_id = actor.id
    invoice.paid_by = actor
    invoice.paid_at = naive_utcnow()
    return {
        key: {"from": before[key], "to": getattr(invoice, key)}
        for key in before
        if before[key] != getattr(invoice, key)
    }


def mark_unpaid(invoice: Invoice) -> dict:
    """Take a recorded payment back - a correction. Returns the change."""
    if invoice.paid_on is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{invoice.number} is not marked paid.",
        )
    change = {
        "paid_on": {"from": invoice.paid_on, "to": None},
        "payment_reference": {"from": invoice.payment_reference, "to": None},
    }
    invoice.paid_on = None
    invoice.payment_reference = None
    invoice.paid_by_id = None
    invoice.paid_by = None
    invoice.paid_at = None
    return change


# ---------------------------------------------------------------------------
# Telling people
# ---------------------------------------------------------------------------


def link(invoice: Invoice) -> str:
    return f"{get_settings().frontend_base_url.rstrip('/')}/invoices/{invoice.id}"


def _first_name(person: User) -> str:
    return person.full_name.split()[0] if person.full_name else "there"


def _facts(invoice: Invoice) -> list[str]:
    lines = [
        f"  Invoice      {invoice.number}",
        f"  Vendor       {invoice.vendor.name}",
        f"  Period       {period_label(invoice)}",
        f"  Trips        {len(invoice.lines)}",
        f"  Total        {inr(invoice.total_amount)}",
    ]
    if invoice.vendor_invoice_ref:
        lines.append(f"  Vendor's no. {invoice.vendor_invoice_ref}")
    return lines


def tell_super_admins(
    db: Session, invoice: Invoice, actor: User, *, kind: str, headline: str, what: str
) -> list[int]:
    """Every active super admin: they are the only ones who can approve."""
    approvers = db.execute(
        select(User).where(
            User.tenant_id == invoice.tenant_id,
            User.role == Role.SUPER_ADMIN,
            User.is_active.is_(True),
            User.id != actor.id,
        )
    ).scalars().all()
    queued: list[int] = []
    for person in approvers:
        body = [
            f"Hello {_first_name(person)},",
            "",
            what,
            "",
            *_facts(invoice),
            "",
            f"Approve or reject it here: {link(invoice)}",
        ]
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=invoice.tenant_id,
                user=person,
                kind=kind,
                title=headline[:200],
                body=what,
                email_subject=f"{headline} - {invoice.vendor.name}, {inr(invoice.total_amount)}"[:255],
                email_body="\n".join(body),
                deliver_now=False,
            )
        )
    return queued


def tell_preparers(db: Session, invoice: Invoice, actor: User) -> list[int]:
    """Whoever submitted the invoice and whoever created it - once each - hear
    the super admin's decision and comment."""
    approved = invoice.status is InvoiceStatus.APPROVED
    verdict = "approved" if approved else "rejected"
    comment = invoice.decision_comment
    what = f"{actor.full_name} {verdict} {invoice.number} from {invoice.vendor.name}, {inr(invoice.total_amount)}."
    if comment:
        what += f" Comment: {comment}"
    if approved:
        what += f" {payment_label(invoice)}."
    if not approved:
        what += " Fix it and submit it again."

    people: dict[int, User] = {}
    for person in (invoice.submitted_by, invoice.created_by):
        if person is not None and person.is_active and person.id != actor.id:
            people.setdefault(person.id, person)

    queued: list[int] = []
    for person in people.values():
        body = [
            f"Hello {_first_name(person)},",
            "",
            what,
            "",
            *_facts(invoice),
            "",
            f"See it here: {link(invoice)}",
        ]
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=invoice.tenant_id,
                user=person,
                kind="INVOICE_APPROVED" if approved else "INVOICE_REJECTED",
                title=f"Invoice {invoice.number} {verdict}",
                body=what,
                email_subject=f"Invoice {verdict}: {invoice.number} - {invoice.vendor.name}"[:255],
                email_body="\n".join(body),
                deliver_now=False,
            )
        )
    return queued


def tell_preparers_of_payment(db: Session, invoice: Invoice, actor: User) -> None:
    """Whoever prepared it hears, in the app, that it was paid - or that a
    recorded payment was taken back. In app only: nobody has to act on it."""
    people: dict[int, User] = {}
    for person in (invoice.submitted_by, invoice.created_by):
        if person is not None and person.is_active and person.id != actor.id:
            people.setdefault(person.id, person)
    paid = invoice.paid_on is not None
    for person in people.values():
        notifications.notify(
            db,
            tenant_id=invoice.tenant_id,
            user=person,
            kind="INVOICE_PAID",
            title=(
                f"Invoice {invoice.number} paid"
                if paid
                else f"Invoice {invoice.number} is not paid after all"
            ),
            body=(
                f"{actor.full_name} recorded {invoice.number} from {invoice.vendor.name}, "
                f"{inr(invoice.total_amount)}, as {payment_phrase(invoice)}."
            ),
            send_email=False,
        )
