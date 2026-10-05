"""
Writing a cost and a vendor onto travellers - the database half of
`services/costs.py`, which does the arithmetic.

Two screens record cost: the cost panel on a request, and the booking window,
where the admin types what the ticket or the cab cost while marking it booked.
Both go through here, so the rules are the same on either: only a trip that is
happening has a cost, a cost billed on an approved invoice cannot change, and
an invoice still being prepared follows the new amount.
"""
from __future__ import annotations

from fastapi import HTTPException, status

from sqlalchemy.orm import Session

from app.core.enums import TravellerStatus
from app.models.base import naive_utcnow
from app.models.request import RequestTraveller
from app.models.user import User
from app.models.vendor import Vendor
from app.services import costs, vendors


def assert_costable(traveller: RequestTraveller) -> None:
    """A cost belongs to a trip that is happening.

    Recording spend against a rejected traveller would quietly inflate a
    campaign's total with money nobody paid.
    """
    if traveller.status in (TravellerStatus.REJECTED, TravellerStatus.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{traveller.user.full_name} was "
                f"{str(traveller.status).lower()}, so there is no cost to record."
            ),
        )


class VendorChoice:
    """What the caller said about who was paid: nothing (each traveller keeps
    theirs), a vendor, or null to clear it."""

    def __init__(self, sent: bool, vendor: Vendor | None):
        self.sent = sent
        self.vendor = vendor

    def id_for(self, traveller: RequestTraveller) -> int | None:
        if not self.sent:
            return traveller.vendor_id
        return self.vendor.id if self.vendor is not None else None


def vendor_choice(
    db: Session,
    tenant_id: str,
    travellers: list[RequestTraveller],
    *,
    sent: bool,
    vendor_id: int | None,
) -> VendorChoice:
    """The vendor to record, checked: one of the organisation's, and active
    unless it is the one these travellers already have."""
    if not sent:
        return VendorChoice(False, None)
    keeping = {t.vendor_id for t in travellers if t.vendor_id is not None}
    return VendorChoice(True, vendors.pick(db, tenant_id, vendor_id, keeping=keeping))


def apply(
    db: Session,
    *,
    traveller: RequestTraveller,
    amount,
    note: str | None,
    actor: User,
    vendor: VendorChoice,
) -> dict:
    """Write one traveller's cost (and vendor, when chosen). Returns the change
    for the activity log. The caller has already run the invoice guard."""
    before = traveller.cost_amount
    vendor_before = vendors.name_of(traveller)
    traveller.cost_amount = costs.to_money(amount) if amount is not None else None
    traveller.cost_currency = costs.DEFAULT_CURRENCY
    traveller.cost_note = (note or "").strip() or None
    traveller.cost_entered_by_id = actor.id
    traveller.cost_entered_at = naive_utcnow()
    if vendor.sent:
        traveller.vendor = vendor.vendor
        traveller.vendor_id = vendor.id_for(traveller)
    change = {
        "traveller": traveller.user.full_name,
        "from": str(before) if before is not None else None,
        "to": str(traveller.cost_amount) if traveller.cost_amount is not None else None,
    }
    vendor_after = vendors.name_of(traveller)
    if vendor_after != vendor_before:
        change["vendor"] = {"from": vendor_before, "to": vendor_after}
    return change


def paid_to(vendor: VendorChoice) -> str:
    """", paid to Sri Travels" for an activity-log summary, or nothing."""
    return f", paid to {vendor.vendor.name}" if vendor.sent and vendor.vendor else ""
