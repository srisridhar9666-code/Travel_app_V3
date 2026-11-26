"""
In-Transit & On-Site Verification Telemetry (SOW Section 8).

1. Cab Telemetry: Morning start-of-day and evening end-of-day odometer photos,
   meter readings, GPS coordinates, and tamper-resistant IST timestamps.
2. Hotel Stay Verification: Manual check-in and check-out with timestamps, GPS,
   assigned room number, and folio bill uploads.
"""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Enum as SAEnum, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import StayVerificationStatus, TelemetryStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class CabTelemetry(Base, TenantMixin, TimestampMixin):
    """Daily Cab Hire Telemetry & In-Transit Odometer Tracking (SOW Section 8.1)."""

    __tablename__ = "cab_telemetries"
    __table_args__ = (
        Index("ix_cab_telemetry_request", "tenant_id", "request_id"),
        Index("ix_cab_telemetry_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    traveller_id: Mapped[int | None] = mapped_column(
        ForeignKey("request_travellers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Driver & vehicle details recorded at dispatch
    driver_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    vehicle_model: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # Start-of-day morning telemetry (Hotel -> Field)
    start_odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    start_odometer_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    start_photo_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    start_time_ist: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    start_gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    start_gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    start_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # End-of-day evening telemetry (Field -> Hotel)
    end_odometer_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    end_odometer_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    end_photo_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    end_time_ist: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    end_gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    end_gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    end_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Computed metrics
    total_km: Mapped[Decimal | None] = mapped_column(Numeric(10, 1), nullable=True)
    total_duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[TelemetryStatus] = mapped_column(
        SAEnum(TelemetryStatus, native_enum=False, length=20, validate_strings=True),
        default=TelemetryStatus.IN_PROGRESS,
        nullable=False,
    )

    # Audit & reconciliation against vendor bill
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Manager / Admin force close
    force_closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    force_closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    force_close_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    request = relationship("TravelRequest", back_populates="cab_telemetries", foreign_keys=[request_id])
    traveller = relationship("RequestTraveller", foreign_keys=[traveller_id])
    verified_by = relationship("User", foreign_keys=[verified_by_id])
    force_closed_by = relationship("User", foreign_keys=[force_closed_by_id])

    def calculate_distance(self) -> Decimal | None:
        if self.end_odometer_km is not None and self.start_odometer_km is not None:
            diff = self.end_odometer_km - self.start_odometer_km
            return max(Decimal("0.0"), diff)
        return None


class HotelStayVerification(Base, TenantMixin, TimestampMixin):
    """Manual Hotel Check-In / Check-Out Verification & Folio Capture (SOW Section 8.2)."""

    __tablename__ = "hotel_stay_verifications"
    __table_args__ = (
        Index("ix_stay_verification_request", "tenant_id", "request_id"),
        Index("ix_stay_verification_user", "tenant_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    request_id: Mapped[int] = mapped_column(
        ForeignKey("travel_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    traveller_id: Mapped[int | None] = mapped_column(
        ForeignKey("request_travellers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Arrival check-in
    check_in_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    check_in_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    check_in_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    check_in_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    room_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    check_in_photo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    check_in_photo_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    check_in_remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Departure check-out & folio
    check_out_time: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    check_out_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    check_out_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    check_out_location_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    folio_bill_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    check_out_photo_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    check_out_remarks: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[StayVerificationStatus] = mapped_column(
        SAEnum(StayVerificationStatus, native_enum=False, length=20, validate_strings=True),
        default=StayVerificationStatus.CHECKED_IN,
        nullable=False,
    )

    reimbursement_prompted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Manager / Admin force close
    force_closed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    force_closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    force_close_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)

    request = relationship("TravelRequest", back_populates="hotel_verifications", foreign_keys=[request_id])
    traveller = relationship("RequestTraveller", foreign_keys=[traveller_id])
    user = relationship("User", foreign_keys=[user_id])
    verified_by = relationship("User", foreign_keys=[verified_by_id])
    force_closed_by = relationship("User", foreign_keys=[force_closed_by_id])
