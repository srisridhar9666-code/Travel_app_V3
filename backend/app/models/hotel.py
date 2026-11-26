"""
Saved Hotels Directory (SOW Section 1.1 & 7).

Contracted vendors and administrators can maintain a directory of frequently
used hotels with verified amenities, standard room rates, address, and landmark.
When sourcing or submitting hotel options, options can be instantly auto-filled
from this directory, and new hotel options can be saved directly for future use.
"""
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class SavedHotel(Base, TenantMixin, TimestampMixin):
    __tablename__ = "saved_hotels"
    __table_args__ = (
        Index("ix_saved_hotels_tenant_city", "tenant_id", "city"),
        Index("ix_saved_hotels_vendor", "tenant_id", "vendor_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Vendor who manages/offered this hotel (or null if general organization listing)
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )

    hotel_name: Mapped[str] = mapped_column(String(160), nullable=False)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    landmark: Mapped[str | None] = mapped_column(String(160), nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    room_category: Mapped[str] = mapped_column(String(80), default="Standard", nullable=False)
    nightly_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    vendor_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    vendor = relationship("Vendor", foreign_keys=[vendor_id], lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SavedHotel {self.id} {self.hotel_name} city={self.city}>"
