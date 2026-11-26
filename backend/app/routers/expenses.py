"""
Post-Travel Expense Reimbursements & Finance Settlement Desk Router (SOW Section 10).
"""
import csv
import io
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.core.deps import CurrentUser, DbSession
from app.core.enums import (
    AuditAction,
    ExpenseCategory,
    ExpenseStatus,
    PaymentMode,
    Role,
)
from app.models.expense import ExpenseClaim, ExpenseItem
from app.models.project import Project
from app.models.request import TravelRequest
from app.models.user import User
from app.schemas.expense import (
    DisbursementSettlement,
    ExpenseClaimCreate,
    ExpenseClaimRead,
    ExpenseItemRead,
    FinanceAudit,
    ManagerEndorsement,
)
from app.services import audit

router = APIRouter(prefix="/expenses", tags=["expenses"])

FOOD_ALLOWANCE_PER_DAY = Decimal("400.00")
LOCAL_TRANSIT_UNBILLED_MAX = Decimal("150.00")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _generate_claim_code(db: DbSession, tenant_id: str) -> str:
    year = datetime.now().year
    prefix = f"EXP-{year}-"
    # Find highest sequence for this year
    last_claim = (
        db.execute(
            select(ExpenseClaim.claim_code)
            .where(
                ExpenseClaim.tenant_id == tenant_id,
                ExpenseClaim.claim_code.like(f"{prefix}%"),
            )
            .order_by(ExpenseClaim.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if last_claim:
        try:
            seq = int(last_claim.split("-")[-1]) + 1
        except ValueError:
            seq = 1
    else:
        seq = 1
    return f"{prefix}{seq:04d}"


def _compute_policy_flags(claim: ExpenseClaim) -> tuple[bool, float, bool, float]:
    """Compute food and unbilled transit excess amounts across claim items."""
    food_by_date: dict[str, Decimal] = {}
    unbilled_transit_by_date: dict[str, Decimal] = {}

    for item in claim.items:
        dt_str = item.expense_date.isoformat()
        amt = item.claimed_amount
        if item.category == ExpenseCategory.FOOD:
            food_by_date[dt_str] = food_by_date.get(dt_str, Decimal("0.00")) + amt
        elif item.category == ExpenseCategory.LOCAL_TRANSIT and not item.is_receipt_attached:
            unbilled_transit_by_date[dt_str] = unbilled_transit_by_date.get(dt_str, Decimal("0.00")) + amt

    food_excess = Decimal("0.00")
    for d, total in food_by_date.items():
        if total > FOOD_ALLOWANCE_PER_DAY:
            food_excess += (total - FOOD_ALLOWANCE_PER_DAY)

    transit_excess = Decimal("0.00")
    for d, total in unbilled_transit_by_date.items():
        if total > LOCAL_TRANSIT_UNBILLED_MAX:
            transit_excess += (total - LOCAL_TRANSIT_UNBILLED_MAX)

    return (
        food_excess > 0,
        float(food_excess),
        transit_excess > 0,
        float(transit_excess),
    )


def _to_claim_read(claim: ExpenseClaim) -> ExpenseClaimRead:
    food_exceeded, food_excess, transit_exceeded, transit_excess = _compute_policy_flags(claim)
    items_read = [
        ExpenseItemRead(
            id=it.id,
            claim_id=it.claim_id,
            expense_date=it.expense_date,
            category=it.category,
            vendor_merchant_name=it.vendor_merchant_name,
            bill_number=it.bill_number,
            bill_date=it.bill_date,
            claimed_amount=float(it.claimed_amount),
            approved_amount=float(it.approved_amount),
            deducted_amount=float(it.deducted_amount),
            deduction_reason=it.deduction_reason,
            receipt_url=it.receipt_url,
            is_receipt_attached=it.is_receipt_attached,
            justification=it.justification,
            created_at=it.created_at,
            updated_at=it.updated_at,
        )
        for it in claim.items
    ]

    return ExpenseClaimRead(
        id=claim.id,
        claim_code=claim.claim_code,
        user_id=claim.user_id,
        user_name=claim.user.full_name if claim.user else None,
        user_email=claim.user.email if claim.user else None,
        project_id=claim.project_id,
        project_name=claim.project.name if claim.project else None,
        request_id=claim.request_id,
        destination_city=claim.destination_city,
        travel_start_date=claim.travel_start_date,
        travel_end_date=claim.travel_end_date,
        employee_payment_mode=claim.employee_payment_mode,
        expense_type=claim.expense_type,
        expense_type_other=claim.expense_type_other,
        remarks=claim.remarks,
        upi_id=claim.upi_id,
        bank_name=claim.bank_name,
        bank_account_number=claim.bank_account_number,
        account_holder_name=claim.account_holder_name,
        ifsc_code=claim.ifsc_code,
        attachment_url=claim.attachment_url,
        status=claim.status,
        total_claimed_amount=float(claim.total_claimed_amount),
        total_approved_amount=float(claim.total_approved_amount),
        total_deducted_amount=float(claim.total_deducted_amount),
        manager_id=claim.manager_id,
        manager_name=claim.manager.full_name if claim.manager else None,
        manager_reviewed_at=claim.manager_reviewed_at,
        manager_notes=claim.manager_notes,
        manager_endorsed=claim.manager_endorsed,
        finance_auditor_id=claim.finance_auditor_id,
        finance_auditor_name=claim.finance_auditor.full_name if claim.finance_auditor else None,
        finance_audited_at=claim.finance_audited_at,
        finance_deduction_notes=claim.finance_deduction_notes,
        payment_mode=claim.payment_mode,
        payment_date=claim.payment_date,
        bank_utr_number=claim.bank_utr_number,
        settled_at=claim.settled_at,
        settled_by_id=claim.settled_by_id,
        settled_by_name=claim.settled_by.full_name if claim.settled_by else None,
        items=items_read,
        is_food_cap_exceeded=food_exceeded,
        food_excess_amount=food_excess,
        is_unbilled_transit_exceeded=transit_exceeded,
        unbilled_transit_excess_amount=transit_excess,
        created_at=claim.created_at,
        updated_at=claim.updated_at,
    )


# --- Endpoints ---------------------------------------------------------------

@router.get("", response_model=list[ExpenseClaimRead])
def list_expense_claims(
    db: DbSession,
    actor: CurrentUser,
    status_filter: ExpenseStatus | None = Query(None, alias="status"),
    user_id: int | None = None,
    project_id: int | None = None,
) -> list[ExpenseClaimRead]:
    """List expense reimbursement claims accessible to the authenticated user."""
    stmt = select(ExpenseClaim).where(ExpenseClaim.tenant_id == actor.tenant_id)

    # Role visibility scope
    if actor.role in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        pass  # Global visibility
    elif actor.role == Role.MANAGER:
        # Claims by user themselves OR where they are the designated manager
        stmt = stmt.where(
            (ExpenseClaim.user_id == actor.id) | (ExpenseClaim.manager_id == actor.id)
        )
    else:
        # Ground staff: own claims only
        stmt = stmt.where(ExpenseClaim.user_id == actor.id)

    if status_filter:
        stmt = stmt.where(ExpenseClaim.status == status_filter)
    if user_id:
        stmt = stmt.where(ExpenseClaim.user_id == user_id)
    if project_id:
        stmt = stmt.where(ExpenseClaim.project_id == project_id)

    stmt = stmt.order_by(ExpenseClaim.id.desc())
    claims = db.execute(stmt).scalars().all()
    return [_to_claim_read(c) for c in claims]


@router.get("/export")
def export_expense_claims_csv(
    db: DbSession,
    actor: CurrentUser,
) -> Response:
    """Export expense claims to CSV for accounting and finance audit."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Finance and Admin personnel can export reimbursement claims.",
        )

    claims = (
        db.execute(
            select(ExpenseClaim)
            .where(ExpenseClaim.tenant_id == actor.tenant_id)
            .order_by(ExpenseClaim.id.desc())
        )
        .scalars()
        .all()
    )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Claim Code",
        "Employee Name",
        "Employee Email",
        "Campaign Code",
        "Campaign Name",
        "Destination City",
        "Travel Start",
        "Travel End",
        "Status",
        "Claimed (INR)",
        "Approved (INR)",
        "Deducted (INR)",
        "Manager Endorsed",
        "Payment Mode",
        "Bank UTR Number",
        "Payment Date",
        "Settled At",
    ])

    for c in claims:
        writer.writerow([
            c.claim_code,
            c.user.full_name if c.user else "",
            c.user.email if c.user else "",
            c.project.code if c.project else "",
            c.project.name if c.project else "",
            c.destination_city,
            c.travel_start_date.isoformat(),
            c.travel_end_date.isoformat(),
            c.status.value,
            f"{c.total_claimed_amount:.2f}",
            f"{c.total_approved_amount:.2f}",
            f"{c.total_deducted_amount:.2f}",
            "Yes" if c.manager_endorsed else "No",
            c.payment_mode.value if c.payment_mode else "",
            c.bank_utr_number or "",
            c.payment_date.isoformat() if c.payment_date else "",
            c.settled_at.isoformat() if c.settled_at else "",
        ])

    return Response(
        content=out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="sriyatra_expense_claims.csv"'},
    )


@router.get("/{claim_id}", response_model=ExpenseClaimRead)
def get_expense_claim(
    claim_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> ExpenseClaimRead:
    """Retrieve details for a single expense claim."""
    claim = db.get(ExpenseClaim, claim_id)
    if not claim or claim.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense claim not found.")

    # Access check
    if actor.role in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        pass
    elif actor.role == Role.MANAGER and (claim.user_id == actor.id or claim.manager_id == actor.id):
        pass
    elif claim.user_id == actor.id:
        pass
    else:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    return _to_claim_read(claim)


@router.post("", response_model=ExpenseClaimRead, status_code=status.HTTP_201_CREATED)
def create_expense_claim(
    payload: ExpenseClaimCreate,
    db: DbSession,
    actor: CurrentUser,
) -> ExpenseClaimRead:
    """Submit a post-travel itemized expense claim for manager and finance review."""
    project = db.get(Project, payload.project_id)
    if not project or project.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign project not found.")

    if payload.travel_end_date < payload.travel_start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Expense end date cannot be earlier than start date.",
        )

    # Validate that selected dates do not overlap with employee's previous claims
    overlap_claim = (
        db.execute(
            select(ExpenseClaim).where(
                ExpenseClaim.tenant_id == actor.tenant_id,
                ExpenseClaim.user_id == actor.id,
                ExpenseClaim.status.notin_([ExpenseStatus.REJECTED]),
                ExpenseClaim.travel_start_date <= payload.travel_end_date,
                ExpenseClaim.travel_end_date >= payload.travel_start_date,
            )
        )
        .scalars()
        .first()
    )
    if overlap_claim:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Selected expense dates ({payload.travel_start_date} to {payload.travel_end_date}) "
                f"overlap with your previous claim {overlap_claim.claim_code} "
                f"({overlap_claim.travel_start_date} to {overlap_claim.travel_end_date})."
            ),
        )

    # Save payment & bank details on employee's profile for future convenience
    if payload.upi_id:
        actor.upi_id = payload.upi_id.strip()
    if payload.bank_name:
        actor.bank_name = payload.bank_name.strip()
    if payload.bank_account_number:
        actor.bank_account_number = payload.bank_account_number.strip()
    if payload.account_holder_name:
        actor.account_holder_name = payload.account_holder_name.strip()
    if payload.ifsc_code:
        actor.ifsc_code = payload.ifsc_code.strip()

    claim_code = _generate_claim_code(db, actor.tenant_id)
    claim = ExpenseClaim(
        tenant_id=actor.tenant_id,
        claim_code=claim_code,
        user_id=actor.id,
        project_id=project.id,
        request_id=payload.request_id,
        destination_city=payload.destination_city.strip(),
        travel_start_date=payload.travel_start_date,
        travel_end_date=payload.travel_end_date,
        employee_payment_mode=payload.employee_payment_mode,
        expense_type=payload.expense_type,
        expense_type_other=payload.expense_type_other,
        remarks=payload.remarks,
        upi_id=payload.upi_id,
        bank_name=payload.bank_name,
        bank_account_number=payload.bank_account_number,
        account_holder_name=payload.account_holder_name,
        ifsc_code=payload.ifsc_code,
        attachment_url=payload.attachment_url,
        status=ExpenseStatus.SUBMITTED,
        manager_id=actor.manager_id,
        total_claimed_amount=Decimal("0.00"),
    )
    db.add(claim)
    db.flush()

    total_claimed = Decimal("0.00")
    if payload.items:
        for item_in in payload.items:
            amt = Decimal(str(item_in.claimed_amount))
            receipt = item_in.receipt_url or payload.attachment_url
            has_receipt = bool(receipt)
            item = ExpenseItem(
                tenant_id=actor.tenant_id,
                claim_id=claim.id,
                expense_date=item_in.expense_date,
                category=item_in.category,
                vendor_merchant_name=item_in.vendor_merchant_name.strip() if item_in.vendor_merchant_name else (payload.expense_type or "Expense"),
                bill_number=item_in.bill_number.strip() if item_in.bill_number else None,
                bill_date=item_in.bill_date,
                claimed_amount=amt,
                approved_amount=amt,  # Default to claimed before finance audit
                deducted_amount=Decimal("0.00"),
                receipt_url=receipt,
                is_receipt_attached=has_receipt,
                justification=item_in.justification or payload.remarks,
            )
            total_claimed += amt
            db.add(item)
    elif payload.total_amount and payload.total_amount > 0:
        amt = Decimal(str(payload.total_amount))
        cat = ExpenseCategory.FOOD if payload.expense_type == "FOOD" else (
            ExpenseCategory.LOCAL_TRANSIT if payload.expense_type == "TRAVEL" else ExpenseCategory.MISCELLANEOUS
        )
        receipt = payload.attachment_url
        has_receipt = bool(receipt)
        item = ExpenseItem(
            tenant_id=actor.tenant_id,
            claim_id=claim.id,
            expense_date=payload.travel_start_date,
            category=cat,
            vendor_merchant_name=payload.expense_type or "Expense",
            bill_number=None,
            bill_date=payload.travel_start_date,
            claimed_amount=amt,
            approved_amount=amt,
            deducted_amount=Decimal("0.00"),
            receipt_url=receipt,
            is_receipt_attached=has_receipt,
            justification=payload.remarks,
        )
        total_claimed += amt
        db.add(item)

    claim.total_claimed_amount = total_claimed
    claim.total_approved_amount = total_claimed
    db.commit()
    db.refresh(claim)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.SUBMIT_EXPENSE,
        entity_type="ExpenseClaim",
        entity_id=claim.id,
        summary=f"Expense claim {claim.claim_code} submitted for INR {total_claimed}",
    )

    return _to_claim_read(claim)


@router.post("/{claim_id}/endorse", response_model=ExpenseClaimRead)
def endorse_expense_claim(
    claim_id: int,
    payload: ManagerEndorsement,
    db: DbSession,
    actor: CurrentUser,
) -> ExpenseClaimRead:
    """Direct manager reviews and endorses team member's expense claim (L1 Review)."""
    claim = db.get(ExpenseClaim, claim_id)
    if not claim or claim.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense claim not found.")

    # Manager or Admin permissions
    if actor.role in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN):
        pass
    elif actor.role == Role.MANAGER and claim.manager_id == actor.id:
        pass
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the designated team manager or Admin can endorse this expense claim.",
        )

    if claim.status != ExpenseStatus.SUBMITTED:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot endorse an expense claim in {claim.status.value} status.",
        )

    claim.manager_endorsed = payload.endorsed
    claim.manager_reviewed_at = _utcnow()
    claim.manager_notes = payload.notes
    claim.status = ExpenseStatus.MANAGER_ENDORSED if payload.endorsed else ExpenseStatus.REJECTED
    db.commit()
    db.refresh(claim)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.ENDORSE_EXPENSE,
        entity_type="ExpenseClaim",
        entity_id=claim.id,
        summary=f"Expense claim {claim.claim_code} {'endorsed' if payload.endorsed else 'rejected'} by manager {actor.full_name}",
    )

    return _to_claim_read(claim)


@router.post("/{claim_id}/audit", response_model=ExpenseClaimRead)
def audit_expense_claim(
    claim_id: int,
    payload: FinanceAudit,
    db: DbSession,
    actor: CurrentUser,
) -> ExpenseClaimRead:
    """Finance desk performs itemized audit, applies deductions, and approves claim (L2 Review)."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Finance and Admin personnel can audit expense claims.",
        )

    claim = db.get(ExpenseClaim, claim_id)
    if not claim or claim.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense claim not found.")

    if claim.status not in (ExpenseStatus.MANAGER_ENDORSED, ExpenseStatus.SUBMITTED):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot audit an expense claim in {claim.status.value} status.",
        )

    now = _utcnow()
    claim.finance_auditor_id = actor.id
    claim.finance_audited_at = now
    claim.finance_deduction_notes = payload.notes

    if payload.decision == ExpenseStatus.REJECTED:
        claim.status = ExpenseStatus.REJECTED
        claim.total_approved_amount = Decimal("0.00")
        claim.total_deducted_amount = claim.total_claimed_amount
        for item in claim.items:
            item.approved_amount = Decimal("0.00")
            item.deducted_amount = item.claimed_amount
            item.deduction_reason = payload.notes or "Claim rejected by Finance."
    elif payload.decision in (ExpenseStatus.FINANCE_APPROVED, ExpenseStatus.PARTIALLY_APPROVED):
        # Apply itemized deductions
        deduction_map = {d.item_id: d for d in payload.deductions}
        total_approved = Decimal("0.00")
        total_deducted = Decimal("0.00")

        for item in claim.items:
            if item.id in deduction_map:
                ded_spec = deduction_map[item.id]
                ded_amt = Decimal(str(ded_spec.deducted_amount))
                if ded_amt > item.claimed_amount:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Deduction of INR {ded_amt} on line #{item.id} exceeds claimed amount {item.claimed_amount}.",
                    )
                item.deducted_amount = ded_amt
                item.deduction_reason = ded_spec.deduction_reason
                item.approved_amount = item.claimed_amount - ded_amt
            else:
                item.approved_amount = item.claimed_amount
                item.deducted_amount = Decimal("0.00")

            total_approved += item.approved_amount
            total_deducted += item.deducted_amount

        claim.total_approved_amount = total_approved
        claim.total_deducted_amount = total_deducted
        claim.status = (
            ExpenseStatus.PARTIALLY_APPROVED
            if total_deducted > Decimal("0.00")
            else ExpenseStatus.FINANCE_APPROVED
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid audit decision. Must be FINANCE_APPROVED, PARTIALLY_APPROVED, or REJECTED.",
        )

    db.commit()
    db.refresh(claim)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.AUDIT_EXPENSE,
        entity_type="ExpenseClaim",
        entity_id=claim.id,
        summary=f"Expense claim {claim.claim_code} audited: {claim.status.value} (Approved: {claim.total_approved_amount}, Deducted: {claim.total_deducted_amount})",
    )

    return _to_claim_read(claim)


@router.post("/{claim_id}/settle", response_model=ExpenseClaimRead)
def settle_expense_claim(
    claim_id: int,
    payload: DisbursementSettlement,
    db: DbSession,
    actor: CurrentUser,
) -> ExpenseClaimRead:
    """Finance desk records final bank payout with UTR number and transitions claim to SETTLED."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Finance and Admin personnel can settle expense reimbursements.",
        )

    claim = db.get(ExpenseClaim, claim_id)
    if not claim or claim.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense claim not found.")

    if claim.status not in (ExpenseStatus.FINANCE_APPROVED, ExpenseStatus.PARTIALLY_APPROVED):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot settle an expense claim in {claim.status.value} status. Must be FINANCE_APPROVED or PARTIALLY_APPROVED.",
        )

    now = _utcnow()
    claim.payment_mode = payload.payment_mode
    claim.payment_date = payload.payment_date
    claim.bank_utr_number = payload.bank_utr_number.strip()
    claim.settled_at = now
    claim.settled_by_id = actor.id
    claim.status = ExpenseStatus.SETTLED
    db.commit()
    db.refresh(claim)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.SETTLE_EXPENSE,
        entity_type="ExpenseClaim",
        entity_id=claim.id,
        summary=f"Expense claim {claim.claim_code} settled via {payload.payment_mode.value} with UTR {payload.bank_utr_number}",
    )

    return _to_claim_read(claim)
