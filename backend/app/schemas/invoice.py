"""Request and response bodies for vendor invoices.

No body here carries an amount. Every line's money is the traveller's cost as
the travel desk recorded it, and the total is the sum of the lines - both
worked out by the server, so a client cannot send a number that disagrees with
the ledger. A `total_amount` sent anyway is refused rather than ignored, so a
caller who thought they could set it finds out.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import InvoiceStatus, RequestType, VendorKind
from app.schemas.common import UTCInstant

#: The longest period one invoice may cover. A year is more than any vendor
#: bills for at once; beyond it, a typo in the year is the likelier story.
MAX_PERIOD_DAYS = 366

#: Most travellers one invoice can carry - far above a month of any vendor's
#: trips, there so a runaway request cannot hold a worker.
MAX_LINES = 1000


def _tidy(value: object) -> object:
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    return value


def check_period(start: date | None, end: date | None) -> None:
    if start is None or end is None:
        return
    if start > end:
        raise ValueError("The period starts after it ends. Check the dates.")
    if (end - start).days >= MAX_PERIOD_DAYS:
        raise ValueError("One invoice can cover at most a year. Pick a shorter period.")


class _InvoiceFields(BaseModel):
    # Forbid unknown fields: `total_amount` and line amounts are the server's
    # to work out, and a client sending one should hear so.
    model_config = ConfigDict(extra="forbid")

    @field_validator("vendor_invoice_ref", "notes", mode="before", check_fields=False)
    @classmethod
    def _tidy_text(cls, value: object) -> object:
        return _tidy(value)

    @field_validator("traveller_ids", check_fields=False)
    @classmethod
    def _distinct(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("The same trip is picked twice.")
        return value


class InvoiceCreate(_InvoiceFields):
    vendor_id: int
    period_start: date
    period_end: date
    #: Traveller rows (the ids the request read model gives each person on a
    #: trip). May be empty for a draft; submitting needs at least one.
    traveller_ids: list[int] = Field(default_factory=list, max_length=MAX_LINES)
    vendor_invoice_ref: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _period(self):
        check_period(self.period_start, self.period_end)
        return self


class InvoiceUpdate(_InvoiceFields):
    """Only the fields sent change. `traveller_ids`, when sent, is the whole
    new set of lines: anything left out comes off the invoice."""

    vendor_id: int | None = None
    period_start: date | None = None
    period_end: date | None = None
    traveller_ids: list[int] | None = Field(default=None, max_length=MAX_LINES)
    vendor_invoice_ref: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _required_stay(self):
        for field in ("vendor_id", "period_start", "period_end", "traveller_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field.replace('_', ' ').capitalize()} cannot be empty.")
        return self


class InvoiceDecision(BaseModel):
    """A super admin's approval. The comment is optional on an approval.

    `expected_total` is the total the approver was looking at. Lines follow
    the travellers' costs until approval, so a cost changed while the page was
    open would otherwise be approved unseen; with it, that is refused and the
    approver looks again.

    `paid` records that the money has already gone, in the same step - the
    usual case when the bill is settled the day it is approved. Left false,
    the invoice is approved and still to be paid.
    """

    comment: str | None = Field(default=None, max_length=500)
    expected_total: Decimal | None = Field(default=None, ge=0)
    paid: bool = False
    #: The day it was paid; today when left out.
    paid_on: date | None = None
    payment_reference: str | None = Field(default=None, max_length=80)

    @field_validator("comment", "payment_reference", mode="before")
    @classmethod
    def _tidy_comment(cls, value: object) -> object:
        return _tidy(value)


class InvoicePayment(BaseModel):
    """Paid, or - correcting a mistake - not paid after all.

    Marking paid takes the day the money went (today when left out) and,
    usually, the bank's reference for it. Taking it back needs a comment:
    an invoice going from paid to unpaid is the one an auditor asks about.
    """

    paid: bool
    paid_on: date | None = None
    payment_reference: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment", "payment_reference", mode="before")
    @classmethod
    def _tidy_text(cls, value: object) -> object:
        return _tidy(value)

    @model_validator(mode="after")
    def _undo_needs_a_reason(self):
        if not self.paid and (self.comment is None or len(self.comment) < 3):
            raise ValueError("Say why it is not paid after all - the change is kept in the log.")
        return self


class InvoiceRejection(BaseModel):
    """A rejection always says why: it is what the admins go and fix."""

    comment: str = Field(min_length=3, max_length=500)

    @field_validator("comment", mode="before")
    @classmethod
    def _tidy_comment(cls, value: object) -> object:
        cleaned = _tidy(value)
        if cleaned is None or (isinstance(cleaned, str) and len(cleaned) < 3):
            raise ValueError("Say why it is rejected, so the admins know what to fix.")
        return cleaned


class EligibleRow(BaseModel):
    """One booked traveller's cost that a vendor's invoice could carry."""

    traveller_id: int
    request_id: int
    traveller_name: str
    employee_code: str | None = None
    request_type: RequestType
    travel_date: date | None = None
    trip: str
    project_code: str
    project_name: str
    booking_reference: str | None = None
    amount: Decimal
    #: None when nobody recorded who was paid; saving it onto an invoice
    #: records the invoice's vendor.
    vendor_id: int | None = None
    #: Already a line on the invoice being edited.
    on_this_invoice: bool = False
    #: Only for a line already on the invoice: why it can no longer be billed
    #: (cancelled, cost removed, paid to someone else). It cannot be kept.
    problem: str | None = None


class InvoiceLineRead(BaseModel):
    id: int
    traveller_id: int
    request_id: int
    traveller_name: str
    employee_code: str | None = None
    request_type: RequestType | None = None
    travel_date: date | None = None
    description: str
    project_code: str | None = None
    booking_reference: str | None = None
    amount: Decimal
    #: Why this line cannot be billed as it stands - the trip was cancelled, or
    #: its cost removed, after it was added. Submitting and approving are
    #: refused until it is dealt with. Always None once approved: the line is a
    #: record then, not a question.
    problem: str | None = None


class InvoiceEvent(BaseModel):
    """One step in an invoice's life, from the activity log."""

    action: str
    actor_name: str | None = None
    at: UTCInstant
    summary: str
    comment: str | None = None


class InvoiceSummary(BaseModel):
    id: int
    number: str
    vendor_id: int
    vendor_name: str
    vendor_kind: VendorKind
    period_start: date
    period_end: date
    status: InvoiceStatus
    currency: str
    total_amount: Decimal
    line_count: int
    vendor_invoice_ref: str | None = None
    created_by_name: str | None = None
    created_at: UTCInstant
    submitted_at: UTCInstant | None = None
    decided_at: UTCInstant | None = None
    #: An approved invoice with no `paid_on` is approved and still to be paid.
    paid_on: date | None = None
    payment_reference: str | None = None


class InvoiceRead(InvoiceSummary):
    vendor_gstin: str | None = None
    vendor_contact_name: str | None = None
    vendor_phone: str | None = None
    vendor_email: str | None = None
    notes: str | None = None
    updated_by_name: str | None = None
    updated_at: UTCInstant
    submitted_by_name: str | None = None
    decided_by_name: str | None = None
    decision_comment: str | None = None
    paid_by_name: str | None = None
    paid_at: UTCInstant | None = None
    lines: list[InvoiceLineRead]
    history: list[InvoiceEvent] = Field(default_factory=list)
    # What the viewer may do now - the same rules the endpoints enforce, so a
    # button is only offered when pressing it would work.
    can_edit: bool = False
    can_submit: bool = False
    can_delete: bool = False
    can_decide: bool = False
    #: A super admin, on an approved invoice: mark it paid, or correct it.
    can_record_payment: bool = False


class InvoiceList(BaseModel):
    items: list[InvoiceSummary]
    #: Invoices per status, whatever the filter: the tabs' counts.
    counts: dict[str, int]
    #: Approved invoices split by whether they are paid yet: {"paid", "unpaid"}.
    payment_counts: dict[str, int] = Field(default_factory=dict)
    total: int
