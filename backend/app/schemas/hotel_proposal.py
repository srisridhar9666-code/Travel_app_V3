"""
Pydantic schemas for Vendor Hotel Proposals (SOW Section 1.1 & 7).
"""
from __future__ import annotations

from decimal import Decimal
from pydantic import BaseModel, Field

from app.core.enums import HotelProposalStatus
from app.schemas.common import UTCInstant


class HotelProposalCreate(BaseModel):
    hotel_name: str = Field(min_length=2, max_length=160)
    address: str = Field(min_length=3, max_length=255)
    landmark: str | None = Field(default=None, max_length=160)
    google_maps_url: str | None = Field(default=None, max_length=500)
    room_category: str = Field(default="Standard", max_length=80)
    nightly_rate: Decimal = Field(gt=0, decimal_places=2)
    amenities: list[str] | None = None
    vendor_notes: str | None = Field(default=None, max_length=500)
    save_to_directory: bool = False


class HotelProposalRead(BaseModel):
    id: int
    request_id: int
    vendor_id: int
    vendor_name: str | None = None
    hotel_name: str
    address: str
    landmark: str | None = None
    google_maps_url: str | None = None
    room_category: str
    nightly_rate: Decimal | None = None
    amenities: list[str] | None = None
    status: HotelProposalStatus
    is_policy_compliant: bool = True
    is_within_policy: bool = True
    policy_rate_limit: Decimal | None = None
    policy_limit_per_night: Decimal | None = None
    excess_amount: Decimal | None = Decimal("0.00")
    excess_per_night: Decimal | None = Decimal("0.00")
    selected_by_id: int | None = None
    selected_by_name: str | None = None
    selected_at: UTCInstant | None = None
    vendor_notes: str | None = None
    created_at: UTCInstant
    updated_at: UTCInstant


class BatchHotelProposalsCreate(BaseModel):
    """Vendor submits 1 to 5 options at once (SOW Section 7)."""
    options: list[HotelProposalCreate] = Field(default_factory=list, min_length=0, max_length=10)
    proposals: list[HotelProposalCreate] = Field(default_factory=list, min_length=0, max_length=10)
