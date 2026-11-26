"""Request and response schemas for Saved Hotels Directory (SOW Section 1.1 & 7)."""
from __future__ import annotations

from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCInstant


class SavedHotelCreate(BaseModel):
    hotel_name: str = Field(min_length=2, max_length=160)
    city: str = Field(min_length=2, max_length=120)
    state: str | None = Field(default=None, max_length=80)
    address: str = Field(min_length=3, max_length=255)
    landmark: str | None = Field(default=None, max_length=160)
    google_maps_url: str | None = Field(default=None, max_length=500)
    room_category: str = Field(default="Standard", max_length=80)
    nightly_rate: Decimal = Field(default=Decimal("0.00"), ge=0)
    contact_phone: str | None = Field(default=None, max_length=32)
    amenities: list[str] = Field(default_factory=list)
    vendor_notes: str | None = Field(default=None, max_length=500)
    vendor_id: int | None = None


class SavedHotelUpdate(BaseModel):
    hotel_name: str | None = Field(default=None, min_length=2, max_length=160)
    city: str | None = Field(default=None, min_length=2, max_length=120)
    state: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, min_length=3, max_length=255)
    landmark: str | None = Field(default=None, max_length=160)
    google_maps_url: str | None = Field(default=None, max_length=500)
    room_category: str | None = Field(default=None, max_length=80)
    nightly_rate: Decimal | None = Field(default=None, ge=0)
    contact_phone: str | None = Field(default=None, max_length=32)
    amenities: list[str] | None = None
    vendor_notes: str | None = Field(default=None, max_length=500)
    vendor_id: int | None = None
    is_active: bool | None = None


class SavedHotelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hotel_name: str
    city: str
    state: str | None = None
    address: str
    landmark: str | None = None
    google_maps_url: str | None = None
    room_category: str = "Standard"
    nightly_rate: Decimal = Decimal("0.00")
    contact_phone: str | None = None
    amenities: list[str] | None = None
    vendor_notes: str | None = None
    vendor_id: int | None = None
    vendor_name: str | None = None
    is_active: bool = True
    created_at: UTCInstant
    updated_at: UTCInstant
