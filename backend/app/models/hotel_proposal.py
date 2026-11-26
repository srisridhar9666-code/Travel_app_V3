"""
Hotel Proposals submitted by contracted vendors for employee stays (SOW Section 1.1 & 7).

When an admin approves a hotel request and dispatches it to a vendor, the vendor
submits 2 to 5 available options with full address, landmark, Google Maps URL,
room category, amenities, and nightly rate. The employee selects their preferred
stay, and the system matches same-gender roommates if Double Occupancy is active.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Enum as SAEnum, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import HotelProposalStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class HotelProposal(Base, TenantMixin, TimestampMixin):
    __tablename__ = "hotel_proposals"
    __table_args__ = (
        Index("ix_hotel_proposals_request_vendor", "tenant_id", "request_id", "vendor_id"),
        Index("ix_hotel_proposals_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    hotel_name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    landmark: Mapped[str | None] = mapped_column(String(160), nullable=True)
    google_maps_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    room_category: Mapped[str] = mapped_column(String(80), nullable=False, default="Standard")

    nightly_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    amenities: Mapped[list | None] = mapped_column(JSON, nullable=True)

    status: Mapped[HotelProposalStatus] = mapped_column(
        SAEnum(HotelProposalStatus, native_enum=False, length=20, validate_strings=True),
        default=HotelProposalStatus.PROPOSED,
        nullable=False,
    )

    selected_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    selected_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    vendor_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    request = relationship("TravelRequest", back_populates="hotel_proposals", foreign_keys=[request_id])
    vendor = relationship("Vendor", foreign_keys=[vendor_id], lazy="joined")
    selected_by = relationship("User", foreign_keys=[selected_by_id], lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HotelProposal {self.id} {self.hotel_name} rate={self.nightly_rate} status={self.status}>"
