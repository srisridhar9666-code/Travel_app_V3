from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel, Field

from app.core.enums import StayVerificationStatus, TelemetryStatus
from app.schemas.common import UTCInstant


class CabTripStart(BaseModel):
    start_odometer_km: float = Field(..., ge=0, description="Morning starting odometer reading in km")
    start_odometer_photo_url: str | None = Field(None, description="Uploaded photo path of the starting odometer")
    start_photo_source: str | None = Field(None, description="CAMERA or FILE_UPLOAD")
    start_gps_lat: float | None = Field(None, ge=-90, le=90, description="Starting GPS Latitude")
    start_gps_lng: float | None = Field(None, ge=-180, le=180, description="Starting GPS Longitude")
    driver_name: str | None = None
    driver_phone: str | None = None
    vehicle_number: str | None = None
    vehicle_model: str | None = None
    notes: str | None = None


class CabTripEnd(BaseModel):
    end_odometer_km: float = Field(..., ge=0, description="Evening ending odometer reading in km")
    end_odometer_photo_url: str | None = Field(None, description="Uploaded photo path of the ending odometer")
    end_photo_source: str | None = Field(None, description="CAMERA or FILE_UPLOAD")
    end_gps_lat: float | None = Field(None, ge=-90, le=90, description="Ending GPS Latitude")
    end_gps_lng: float | None = Field(None, ge=-180, le=180, description="Ending GPS Longitude")
    notes: str | None = None


class CabTelemetryRead(BaseModel):
    id: int
    request_id: int
    traveller_id: int | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    vehicle_number: str | None = None
    vehicle_model: str | None = None
    start_odometer_km: float | None = None
    start_odometer_photo_url: str | None = None
    start_photo_source: str | None = None
    start_time_ist: UTCInstant | None = None
    start_gps_lat: float | None = None
    start_gps_lng: float | None = None
    start_location_name: str | None = None
    end_odometer_km: float | None = None
    end_odometer_photo_url: str | None = None
    end_photo_source: str | None = None
    end_time_ist: UTCInstant | None = None
    end_gps_lat: float | None = None
    end_gps_lng: float | None = None
    end_location_name: str | None = None
    total_km: float | None = None
    total_duration_minutes: int | None = None
    status: TelemetryStatus
    is_local_exceeded: bool = False
    local_excess_km: float = 0.0
    is_verified: bool
    verified_by_id: int | None = None
    verified_by_name: str | None = None
    verified_at: UTCInstant | None = None
    verification_notes: str | None = None
    force_closed_by_id: int | None = None
    force_closed_by_name: str | None = None
    force_closed_at: UTCInstant | None = None
    force_close_reason: str | None = None
    created_at: UTCInstant
    updated_at: UTCInstant

    class Config:
        from_attributes = True


class HotelCheckIn(BaseModel):
    room_number: str = Field(..., min_length=1, max_length=40, description="Assigned room number")
    check_in_lat: float | None = Field(None, ge=-90, le=90, description="Check-in GPS Latitude")
    check_in_lng: float | None = Field(None, ge=-180, le=180, description="Check-in GPS Longitude")
    check_in_photo_url: str | None = Field(None, description="Check-in photo URL")
    check_in_photo_source: str | None = Field(None, description="CAMERA or FILE_UPLOAD")
    check_in_remarks: str | None = Field(None, description="Check-in remarks / notes")


class HotelCheckOut(BaseModel):
    folio_bill_url: str | None = Field(None, description="Uploaded tax invoice/folio receipt photo/PDF")
    check_out_photo_source: str | None = Field(None, description="CAMERA or FILE_UPLOAD")
    check_out_remarks: str | None = Field(None, description="Check-out remarks / notes")
    check_out_lat: float | None = Field(None, ge=-90, le=90, description="Check-out GPS Latitude")
    check_out_lng: float | None = Field(None, ge=-180, le=180, description="Check-out GPS Longitude")


class HotelStayVerificationRead(BaseModel):
    id: int
    request_id: int
    traveller_id: int | None = None
    user_id: int
    user_name: str | None = None
    room_number: str | None = None
    check_in_time: UTCInstant | None = None
    check_in_lat: float | None = None
    check_in_lng: float | None = None
    check_in_location_name: str | None = None
    check_in_photo_url: str | None = None
    check_in_photo_source: str | None = None
    check_in_remarks: str | None = None
    check_out_time: UTCInstant | None = None
    check_out_lat: float | None = None
    check_out_lng: float | None = None
    check_out_location_name: str | None = None
    folio_bill_url: str | None = None
    check_out_photo_source: str | None = None
    check_out_remarks: str | None = None
    status: StayVerificationStatus
    reimbursement_prompted: bool = False
    is_verified: bool
    verified_by_id: int | None = None
    verified_by_name: str | None = None
    verified_at: UTCInstant | None = None
    verification_notes: str | None = None
    force_closed_by_id: int | None = None
    force_closed_by_name: str | None = None
    force_closed_at: UTCInstant | None = None
    force_close_reason: str | None = None
    created_at: UTCInstant
    updated_at: UTCInstant

    class Config:
        from_attributes = True


class VerificationReview(BaseModel):
    notes: str | None = None


class ForceClosePayload(BaseModel):
    reason: str = Field(..., min_length=2, max_length=300)


class TelemetrySettingsRead(BaseModel):
    require_telemetry_checkout: bool
    max_monthly_missed_photos: int
    ai_ticket_autofill_enabled: bool = True


class TelemetrySettingsUpdate(BaseModel):
    require_telemetry_checkout: bool | None = None
    max_monthly_missed_photos: int | None = Field(default=None, ge=0, le=100)
    ai_ticket_autofill_enabled: bool | None = None


class UserTelemetryStatusRead(BaseModel):
    user_id: int
    user_name: str
    missed_cabs_count: int
    missed_hotels_count: int
    total_missed_count: int
    max_allowed: int
    is_locked: bool
    telemetry_unlocked_at: datetime | None = None
    require_telemetry_checkout: bool

