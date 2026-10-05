"""
Vendor invoices: what an organisation owes one vendor for one period.

An admin picks the booked trips a vendor's bill covers - each traveller's cost
as already recorded - and the invoice adds them up. Nothing about the money is
typed here: every line's amount is the traveller's cost, refreshed while the
invoice can still change and frozen when a super admin approves it, and the
total is always the sum of the lines. That is the reconciliation: the vendor's
bill on one side, what the travel desk recorded on the other.

A traveller's cost is billed on at most one invoice, which the unique index on
`invoice_lines.request_traveller_id` guarantees even against two admins saving
at the same moment.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import InvoiceStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime, naive_utcnow


class Invoice(Base, TenantMixin, TimestampMixin):
    __tablename__ = "invoices"
    __table_args__ = (
        # Numbers are how accounts and vendors refer to an invoice, so they have
        # to be unambiguous within a tenant.
        Index("uq_invoices_tenant_number", "tenant_id", "number", unique=True),
        Index("ix_invoices_tenant_status", "tenant_id", "status"),
        CheckConstraint("period_start <= period_end", name="ck_invoices_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: INV-<year>-<number>, given by the server - see `services/invoices.next_number`.
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    #: RESTRICT: a vendor with invoices is switched off, never deleted.
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT", name="fk_invoices_vendor"),
        nullable=False,
        index=True,
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        SAEnum(InvoiceStatus, native_enum=False, length=12, validate_strings=True),
        default=InvoiceStatus.DRAFT,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    #: Always the sum of the lines, worked out by the server. Never accepted
    #: from a client: a typed total is how a bill and a ledger stop agreeing.
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    #: The vendor's own bill number, to match this against their paperwork.
    vendor_invoice_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_invoices_created_by"),
        nullable=True,
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_invoices_updated_by"),
        nullable=True,
    )
    submitted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_invoices_submitted_by"),
        nullable=True,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_invoices_decided_by"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    #: The super admin's word. Required on a rejection: it is what the admins fix.
    decision_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- payment, after approval ---------------------------------------------
    #: Approving says the bill is right; paying it is a separate, later act.
    #: An approved invoice with no `paid_on` is approved but not yet paid. The
    #: super admin records the day the money went and, usually, the bank's
    #: reference for it, and can correct a mistaken one (logged with a reason).
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    paid_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_invoices_paid_by"),
        nullable=True,
    )
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    vendor = relationship("Vendor", lazy="joined")
    created_by = relationship("User", foreign_keys=[created_by_id], lazy="joined")
    updated_by = relationship("User", foreign_keys=[updated_by_id], lazy="joined")
    submitted_by = relationship("User", foreign_keys=[submitted_by_id], lazy="joined")
    decided_by = relationship("User", foreign_keys=[decided_by_id], lazy="joined")
    paid_by = relationship("User", foreign_keys=[paid_by_id], lazy="joined")
    lines: Mapped[list["InvoiceLine"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by=lambda: [InvoiceLine.travel_date, InvoiceLine.id],
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Invoice {self.id} {self.number} {self.status}>"


class InvoiceLine(Base):
    """One traveller's cost on one invoice.

    `amount` and `description` are snapshots: the amount follows the traveller's
    cost until the invoice is approved and is frozen from then on, and the
    description keeps the invoice readable whatever later happens to the
    request, its campaign or the person's name.
    """

    __tablename__ = "invoice_lines"
    __table_args__ = (
        UniqueConstraint("request_traveller_id", name="uq_invoice_lines_traveller"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE", name="fk_invoice_lines_invoice"),
        nullable=False,
        index=True,
    )
    #: RESTRICT: a billed cost is a financial record, and must not vanish with
    #: the row it came from.
    request_traveller_id: Mapped[int] = mapped_column(
        ForeignKey(
            "request_travellers.id", ondelete="RESTRICT", name="fk_invoice_lines_traveller"
        ),
        nullable=False,
    )
    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="RESTRICT", name="fk_invoice_lines_request"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: "Ravi Kumar · Flight: Hyderabad to Delhi · 12 Oct 2026 · CMP-2026-0002"
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    #: The day the trip starts (a hotel's check-in), which decides the period
    #: the line falls in. Kept for sorting and the export.
    travel_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=naive_utcnow, nullable=False)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    traveller = relationship("RequestTraveller", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InvoiceLine {self.id} invoice={self.invoice_id} traveller={self.request_traveller_id}>"
