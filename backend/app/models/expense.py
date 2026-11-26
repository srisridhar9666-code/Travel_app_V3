"""
Post-Travel Expense Reimbursement Module (SOW Section 1.1, 4.3 & 9).

Field employees claim reimbursement for out-of-pocket expenses incurred during trips:
- Daily food allowance: flat ₹400/day across all cities (itemized receipts required).
- Local conveyance: auto/rickshaw up to ₹150/day unbilled; >₹150 or taxi/tolls/parking at actuals with receipts.
- Miscellaneous field expenses: incidental supplies with written justification.

Workflow:
Employee Submits -> Manager Reviews & Endorses -> Finance Audits & Deducts -> Finance Settles with Bank UTR.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum as SAEnum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ExpenseCategory, ExpenseStatus, PaymentMode
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class ExpenseClaim(Base, TenantMixin, TimestampMixin):
    """An employee expense reimbursement claim header (SOW Section 9.2)."""

    __tablename__ = "expense_claims"
    __table_args__ = (
        Index("uq_expense_claims_code", "tenant_id", "claim_code", unique=True),
        Index("ix_expense_claims_user_status", "tenant_id", "user_id", "status"),
        Index("ix_expense_claims_project", "tenant_id", "project_id"),
        Index("ix_expense_claims_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Generated unique claim code, e.g. EXP-2026-0001
    claim_code: Mapped[str] = mapped_column(String(32), nullable=False)

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )

    destination_city: Mapped[str] = mapped_column(String(120), nullable=False)
    travel_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    travel_end_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Employee payment mode (how they paid: ONLINE or CASH)
    employee_payment_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Type of expense: FOOD, TRAVEL, OTHERS
    expense_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expense_type_other: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Employee remarks
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Employee payout receiving account details
    upi_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    account_holder_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    attachment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[ExpenseStatus] = mapped_column(
        SAEnum(ExpenseStatus, native_enum=False, length=20, validate_strings=True),
        default=ExpenseStatus.DRAFT,
        nullable=False,
    )

    # Claim financials
    total_claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_approved_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    total_deducted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)

    # L1: Direct Manager Endorsement
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    manager_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    manager_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    manager_endorsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # L2: Finance Audit & Deductions
    finance_auditor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    finance_audited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finance_deduction_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Settlement & Payout by Finance
    payment_mode: Mapped[PaymentMode | None] = mapped_column(
        SAEnum(PaymentMode, native_enum=False, length=20, validate_strings=True), nullable=True
    )
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    bank_utr_number: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    settled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settled_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    user = relationship("User", foreign_keys=[user_id], lazy="joined")
    project = relationship("Project", foreign_keys=[project_id], lazy="joined")
    request = relationship("TravelRequest", foreign_keys=[request_id])
    manager = relationship("User", foreign_keys=[manager_id])
    finance_auditor = relationship("User", foreign_keys=[finance_auditor_id])
    settled_by = relationship("User", foreign_keys=[settled_by_id])

    items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="claim",
        cascade="all, delete-orphan",
        order_by="ExpenseItem.expense_date",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExpenseClaim {self.claim_code} user={self.user_id} status={self.status} claimed={self.total_claimed_amount}>"


class ExpenseItem(Base, TenantMixin, TimestampMixin):
    """Itemized receipt line attached to an expense claim (SOW Section 9.2)."""

    __tablename__ = "expense_items"
    __table_args__ = (
        Index("ix_expense_items_claim", "tenant_id", "claim_id"),
        Index("ix_expense_items_category", "tenant_id", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    claim_id: Mapped[int] = mapped_column(
        ForeignKey("expense_claims.id", ondelete="CASCADE"), nullable=False, index=True
    )

    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    category: Mapped[ExpenseCategory] = mapped_column(
        SAEnum(ExpenseCategory, native_enum=False, length=20, validate_strings=True), nullable=False
    )

    vendor_merchant_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bill_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bill_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    claimed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    approved_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    deducted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    deduction_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    receipt_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_receipt_attached: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    justification: Mapped[str | None] = mapped_column(String(500), nullable=True)

    claim: Mapped[ExpenseClaim] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ExpenseItem {self.id} claim={self.claim_id} {self.category} claimed={self.claimed_amount}>"
