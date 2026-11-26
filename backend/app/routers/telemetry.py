"""
In-Transit Cab Telemetry & Hotel Stay GPS Verification Router (SOW Section 8 & 9).
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.enums import (
    AuditAction,
    CabTrip,
    Role,
    StayVerificationStatus,
    TelemetryStatus,
    RequestType,
)
from app.core.deps import CurrentUser, DbSession
from app.core.security import decode_access_token
from app.models.request import RequestTraveller, TravelRequest
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.user import User
from app.schemas.telemetry import (
    CabTelemetryRead,
    CabTripEnd,
    CabTripStart,
    ForceClosePayload,
    HotelCheckIn,
    HotelCheckOut,
    HotelStayVerificationRead,
    TelemetrySettingsRead,
    TelemetrySettingsUpdate,
    UserTelemetryStatusRead,
    VerificationReview,
)
from app.services import audit, geocoding, notifications, storage
from app.services.telemetry_guard import (
    get_or_create_tenant_settings,
    get_user_unclosed_telemetry,
)

router = APIRouter(tags=["telemetry"])

LOCAL_CAB_MAX_KM = 80


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_access(request: TravelRequest, user: User) -> None:
    """Ensure user is requester, traveller, assigned vendor, or admin/finance."""
    if user.role in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        return
    if request.requester_id == user.id:
        return
    if any(t.user_id == user.id for t in request.travellers):
        return
    if request.assigned_vendor_id and user.vendor_id == request.assigned_vendor_id:
        return
    if user.role == Role.MANAGER and any(
        t.manager_id == user.id or (t.user and t.user.manager_id == user.id)
        for t in request.travellers
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You do not have permission to view or submit telemetry for this request.",
    )


# --- File Upload Endpoint ---------------------------------------------------

@router.post("/telemetry/upload", status_code=status.HTTP_201_CREATED)
async def upload_telemetry_file(
    file: Annotated[UploadFile, File()],
    actor: CurrentUser,
) -> dict[str, str]:
    """Upload odometer photo or hotel folio tax invoice bill."""
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File size cannot exceed 10 MB.",
        )
    extension = storage.validate(file, data)
    path = storage.save_in("telemetry", actor.id, data, extension)
    return {
        "file_path": path,
        "file_name": (file.filename or "telemetry_upload")[:255],
    }


_optional_bearer = HTTPBearer(auto_error=False)


@router.get("/telemetry/file")
def get_telemetry_file(
    path: Annotated[str, Query(description="Relative storage path of the file")],
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
    token: Annotated[str | None, Query(description="Optional query token for direct tab links")] = None,
) -> Response:
    """Serve uploaded odometer photos, hotel folio bills, or reimbursement expense receipts."""
    # Authentication check via Bearer header or query token
    raw_token = credentials.credentials if credentials and credentials.credentials else token
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    payload = decode_access_token(raw_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    user = db.get(User, user_id)
    if not user or not user.is_active or user.tenant_id != payload.get("tenant"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    clean_path = path.replace("\\", "/").strip().lstrip("/")
    if not clean_path.startswith("telemetry/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path.")

    try:
        data = storage.read(clean_path)
    except Exception:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file not found.")

    ext = Path(clean_path).suffix.lower()
    content_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext, "application/octet-stream")

    file_name = Path(clean_path).name
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="{file_name}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )


# --- Cab Telemetry Endpoints -------------------------------------------------

def _cab_to_read(req: TravelRequest | None, t: CabTelemetry) -> CabTelemetryRead:
    total = float(t.total_km) if t.total_km is not None else None
    is_local = req.cab_trip == CabTrip.LOCAL if req else False
    exceeded = is_local and total is not None and total > LOCAL_CAB_MAX_KM
    excess = max(0.0, total - LOCAL_CAB_MAX_KM) if exceeded and total is not None else 0.0
    return CabTelemetryRead(
        id=t.id,
        request_id=t.request_id,
        traveller_id=t.traveller_id,
        driver_name=t.driver_name,
        driver_phone=t.driver_phone,
        vehicle_number=t.vehicle_number,
        vehicle_model=t.vehicle_model,
        start_odometer_km=float(t.start_odometer_km) if t.start_odometer_km is not None else None,
        start_odometer_photo_url=t.start_odometer_photo_url,
        start_photo_source=t.start_photo_source,
        start_time_ist=t.start_time_ist,
        start_gps_lat=float(t.start_gps_lat) if t.start_gps_lat is not None else None,
        start_gps_lng=float(t.start_gps_lng) if t.start_gps_lng is not None else None,
        start_location_name=t.start_location_name,
        end_odometer_km=float(t.end_odometer_km) if t.end_odometer_km is not None else None,
        end_odometer_photo_url=t.end_odometer_photo_url,
        end_photo_source=t.end_photo_source,
        end_time_ist=t.end_time_ist,
        end_gps_lat=float(t.end_gps_lat) if t.end_gps_lat is not None else None,
        end_gps_lng=float(t.end_gps_lng) if t.end_gps_lng is not None else None,
        end_location_name=t.end_location_name,
        total_km=total,
        total_duration_minutes=t.total_duration_minutes,
        status=t.status,
        is_local_exceeded=exceeded,
        local_excess_km=excess,
        is_verified=t.is_verified,
        verified_by_id=t.verified_by_id,
        verified_by_name=t.verified_by.full_name if t.verified_by else None,
        verified_at=t.verified_at,
        verification_notes=t.verification_notes,
        force_closed_by_id=t.force_closed_by_id,
        force_closed_by_name=t.force_closed_by.full_name if t.force_closed_by else None,
        force_closed_at=t.force_closed_at,
        force_close_reason=t.force_close_reason,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _hotel_to_read(v: HotelStayVerification, *, fallback_user_name: str | None = None) -> HotelStayVerificationRead:
    return HotelStayVerificationRead(
        id=v.id,
        request_id=v.request_id,
        traveller_id=v.traveller_id,
        user_id=v.user_id,
        user_name=v.user.full_name if v.user else fallback_user_name,
        room_number=v.room_number,
        check_in_time=v.check_in_time,
        check_in_lat=float(v.check_in_lat) if v.check_in_lat is not None else None,
        check_in_lng=float(v.check_in_lng) if v.check_in_lng is not None else None,
        check_in_location_name=v.check_in_location_name,
        check_in_photo_url=v.check_in_photo_url,
        check_in_photo_source=v.check_in_photo_source,
        check_in_remarks=v.check_in_remarks,
        check_out_time=v.check_out_time,
        check_out_lat=float(v.check_out_lat) if v.check_out_lat is not None else None,
        check_out_lng=float(v.check_out_lng) if v.check_out_lng is not None else None,
        check_out_location_name=v.check_out_location_name,
        folio_bill_url=v.folio_bill_url,
        check_out_photo_source=v.check_out_photo_source,
        check_out_remarks=v.check_out_remarks,
        status=v.status,
        reimbursement_prompted=v.reimbursement_prompted,
        is_verified=v.is_verified,
        verified_by_id=v.verified_by_id,
        verified_by_name=v.verified_by.full_name if v.verified_by else None,
        verified_at=v.verified_at,
        verification_notes=v.verification_notes,
        force_closed_by_id=v.force_closed_by_id,
        force_closed_by_name=v.force_closed_by.full_name if v.force_closed_by else None,
        force_closed_at=v.force_closed_at,
        force_close_reason=v.force_close_reason,
        created_at=v.created_at,
        updated_at=v.updated_at,
    )


@router.get("/requests/{request_id}/cab-telemetry", response_model=list[CabTelemetryRead])
def get_cab_telemetries(
    request_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> list[CabTelemetryRead]:
    """Retrieve all daily telemetry records for a cab request."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    telemetries = (
        db.execute(
            select(CabTelemetry)
            .where(CabTelemetry.request_id == request_id)
            .order_by(CabTelemetry.created_at.asc())
        )
        .scalars()
        .all()
    )

    dirty = False
    for t in telemetries:
        if t.start_location_name is None and t.start_gps_lat is not None and t.start_gps_lng is not None:
            resolved = geocoding.reverse_geocode(t.start_gps_lat, t.start_gps_lng)
            if resolved:
                t.start_location_name = resolved
                dirty = True
        if t.end_location_name is None and t.end_gps_lat is not None and t.end_gps_lng is not None:
            resolved = geocoding.reverse_geocode(t.end_gps_lat, t.end_gps_lng)
            if resolved:
                t.end_location_name = resolved
                dirty = True
    if dirty:
        db.commit()

    return [_cab_to_read(req, t) for t in telemetries]


@router.post(
    "/requests/{request_id}/cab-telemetry/start",
    response_model=CabTelemetryRead,
    status_code=status.HTTP_201_CREATED,
)
def start_cab_trip(
    request_id: int,
    payload: CabTripStart,
    db: DbSession,
    actor: CurrentUser,
) -> CabTelemetryRead:
    """Record morning trip start: odometer reading, odometer photo, GPS, and timestamp."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    # Resolve traveller id if actor is one of the travellers
    traveller_id = None
    matching_traveller = next((t for t in req.travellers if t.user_id == actor.id), None)
    if matching_traveller:
        traveller_id = matching_traveller.id
    elif req.travellers:
        traveller_id = req.travellers[0].id

    now = _utcnow()
    start_loc_name = geocoding.reverse_geocode(payload.start_gps_lat, payload.start_gps_lng)
    telemetry = CabTelemetry(
        tenant_id=actor.tenant_id,
        request_id=req.id,
        traveller_id=traveller_id,
        driver_name=payload.driver_name or req.cab_driver_name,
        driver_phone=payload.driver_phone or req.cab_driver_phone,
        vehicle_number=payload.vehicle_number or req.cab_vehicle_number,
        vehicle_model=payload.vehicle_model or (req.booked_cab_type.value if req.booked_cab_type else None),
        start_odometer_km=Decimal(str(payload.start_odometer_km)),
        start_odometer_photo_url=payload.start_odometer_photo_url,
        start_photo_source=payload.start_photo_source or "FILE_UPLOAD",
        start_time_ist=now,
        start_gps_lat=Decimal(str(payload.start_gps_lat)) if payload.start_gps_lat is not None else None,
        start_gps_lng=Decimal(str(payload.start_gps_lng)) if payload.start_gps_lng is not None else None,
        start_location_name=start_loc_name,
        status=TelemetryStatus.IN_PROGRESS,
    )
    db.add(telemetry)
    db.commit()
    db.refresh(telemetry)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.CAPTURE_ODOMETER,
        entity_type="CabTelemetry",
        entity_id=telemetry.id,
        summary=f"Trip started with odometer {payload.start_odometer_km} km",
    )

    return _cab_to_read(req, telemetry)


@router.post(
    "/requests/{request_id}/cab-telemetry/{telemetry_id}/end",
    response_model=CabTelemetryRead,
)
def end_cab_trip(
    request_id: int,
    telemetry_id: int,
    payload: CabTripEnd,
    db: DbSession,
    actor: CurrentUser,
) -> CabTelemetryRead:
    """Record evening trip end: odometer reading, photo, GPS, and auto-compute delta KM."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    telemetry = db.get(CabTelemetry, telemetry_id)
    if not telemetry or telemetry.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telemetry record not found.")

    if telemetry.status != TelemetryStatus.IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Cannot end a trip that is already in {telemetry.status.value} status.",
        )

    end_km = Decimal(str(payload.end_odometer_km))
    if telemetry.start_odometer_km is not None and end_km < telemetry.start_odometer_km:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"End odometer reading ({end_km} km) cannot be less than start odometer reading ({telemetry.start_odometer_km} km).",
        )

    now = _utcnow()
    telemetry.end_odometer_km = end_km
    telemetry.end_odometer_photo_url = payload.end_odometer_photo_url
    telemetry.end_photo_source = payload.end_photo_source or "FILE_UPLOAD"
    telemetry.end_time_ist = now
    telemetry.end_gps_lat = Decimal(str(payload.end_gps_lat)) if payload.end_gps_lat is not None else None
    telemetry.end_gps_lng = Decimal(str(payload.end_gps_lng)) if payload.end_gps_lng is not None else None
    telemetry.end_location_name = geocoding.reverse_geocode(payload.end_gps_lat, payload.end_gps_lng)

    # Compute delta distance
    if telemetry.start_odometer_km is not None:
        telemetry.total_km = max(Decimal("0.0"), end_km - telemetry.start_odometer_km)

    # Compute duration
    if telemetry.start_time_ist:
        diff_secs = (now - telemetry.start_time_ist.replace(tzinfo=timezone.utc)).total_seconds()
        telemetry.total_duration_minutes = max(0, int(diff_secs / 60))

    telemetry.status = TelemetryStatus.COMPLETED
    db.commit()
    db.refresh(telemetry)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.CAPTURE_ODOMETER,
        entity_type="CabTelemetry",
        entity_id=telemetry.id,
        summary=f"Trip completed: {telemetry.total_km} km in {telemetry.total_duration_minutes} mins",
    )

    return _cab_to_read(req, telemetry)


@router.post(
    "/requests/{request_id}/cab-telemetry/{telemetry_id}/force-close",
    response_model=CabTelemetryRead,
)
def force_close_cab_trip(
    request_id: int,
    telemetry_id: int,
    payload: ForceClosePayload,
    db: DbSession,
    actor: CurrentUser,
) -> CabTelemetryRead:
    """Allow Admin or Manager to force close an in-progress cab trip so employee can book again."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Managers and Admins can force close cab trips.",
        )
    telemetry = db.get(CabTelemetry, telemetry_id)
    if not telemetry or telemetry.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telemetry record not found.")

    now = _utcnow()
    telemetry.status = TelemetryStatus.COMPLETED
    telemetry.force_closed_by_id = actor.id
    telemetry.force_closed_at = now
    telemetry.force_close_reason = payload.reason
    if telemetry.end_time_ist is None:
        telemetry.end_time_ist = now
    if telemetry.end_odometer_km is None and telemetry.start_odometer_km is not None:
        telemetry.end_odometer_km = telemetry.start_odometer_km
        telemetry.total_km = Decimal("0.0")

    db.commit()
    db.refresh(telemetry)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="CabTelemetry",
        entity_id=telemetry.id,
        summary=f"Cab telemetry force-closed by {actor.full_name}: {payload.reason}",
    )

    req = db.get(TravelRequest, request_id)
    return _cab_to_read(req, telemetry)


@router.post(
    "/requests/{request_id}/cab-telemetry/{telemetry_id}/verify",
    response_model=CabTelemetryRead,
)
def verify_cab_telemetry(
    request_id: int,
    telemetry_id: int,
    payload: VerificationReview,
    db: DbSession,
    actor: CurrentUser,
) -> CabTelemetryRead:
    """Admin or Finance audits and verifies cab telemetry against vendor invoices."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin or Finance personnel may verify cab telemetry.",
        )

    telemetry = db.get(CabTelemetry, telemetry_id)
    if not telemetry or telemetry.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telemetry record not found.")

    telemetry.is_verified = True
    telemetry.status = TelemetryStatus.VERIFIED
    telemetry.verified_by_id = actor.id
    telemetry.verified_at = _utcnow()
    telemetry.verification_notes = payload.notes
    db.commit()
    db.refresh(telemetry)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="CabTelemetry",
        entity_id=telemetry.id,
        summary=f"Cab telemetry #{telemetry.id} verified by {actor.full_name}",
    )

    req = db.get(TravelRequest, request_id)
    return _cab_to_read(req, telemetry)


# --- Hotel Stay Verification Endpoints ---------------------------------------

@router.get(
    "/requests/{request_id}/hotel-verification",
    response_model=list[HotelStayVerificationRead],
)
def get_hotel_verifications(
    request_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> list[HotelStayVerificationRead]:
    """Retrieve all check-in/check-out verification records for a hotel stay request."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    verifications = (
        db.execute(
            select(HotelStayVerification)
            .where(HotelStayVerification.request_id == request_id)
            .order_by(HotelStayVerification.created_at.asc())
        )
        .scalars()
        .all()
    )

    dirty = False
    for v in verifications:
        if v.check_in_location_name is None and v.check_in_lat is not None and v.check_in_lng is not None:
            resolved = geocoding.reverse_geocode(v.check_in_lat, v.check_in_lng)
            if resolved:
                v.check_in_location_name = resolved
                dirty = True
        if v.check_out_location_name is None and v.check_out_lat is not None and v.check_out_lng is not None:
            resolved = geocoding.reverse_geocode(v.check_out_lat, v.check_out_lng)
            if resolved:
                v.check_out_location_name = resolved
                dirty = True
    if dirty:
        db.commit()

    return [_hotel_to_read(v) for v in verifications]


@router.post(
    "/requests/{request_id}/hotel-verification/check-in",
    response_model=HotelStayVerificationRead,
    status_code=status.HTTP_201_CREATED,
)
def hotel_check_in(
    request_id: int,
    payload: HotelCheckIn,
    db: DbSession,
    actor: CurrentUser,
) -> HotelStayVerificationRead:
    """Record hotel arrival check-in: room number, GPS, and timestamp."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    traveller_id = None
    matching_traveller = next((t for t in req.travellers if t.user_id == actor.id), None)
    if matching_traveller:
        traveller_id = matching_traveller.id
    elif req.travellers:
        traveller_id = req.travellers[0].id

    now = _utcnow()
    checkin_loc_name = geocoding.reverse_geocode(payload.check_in_lat, payload.check_in_lng)
    verification = HotelStayVerification(
        tenant_id=actor.tenant_id,
        request_id=req.id,
        traveller_id=traveller_id,
        user_id=actor.id,
        room_number=payload.room_number.strip(),
        check_in_time=now,
        check_in_lat=Decimal(str(payload.check_in_lat)) if payload.check_in_lat is not None else None,
        check_in_lng=Decimal(str(payload.check_in_lng)) if payload.check_in_lng is not None else None,
        check_in_location_name=checkin_loc_name,
        check_in_photo_url=payload.check_in_photo_url,
        check_in_photo_source=payload.check_in_photo_source,
        check_in_remarks=payload.check_in_remarks,
        status=StayVerificationStatus.CHECKED_IN,
    )
    db.add(verification)
    db.commit()
    db.refresh(verification)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.CHECK_IN,
        entity_type="HotelStayVerification",
        entity_id=verification.id,
        summary=f"Checked into hotel room {payload.room_number}",
    )

    return _hotel_to_read(verification, fallback_user_name=actor.full_name)


@router.post(
    "/requests/{request_id}/hotel-verification/{verification_id}/check-out",
    response_model=HotelStayVerificationRead,
)
def hotel_check_out(
    request_id: int,
    verification_id: int,
    payload: HotelCheckOut,
    db: DbSession,
    actor: CurrentUser,
) -> HotelStayVerificationRead:
    """Record departure check-out: folio bill invoice upload, GPS, and trigger reimbursement alert."""
    req = db.get(TravelRequest, request_id)
    if not req or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _ensure_access(req, actor)

    verification = db.get(HotelStayVerification, verification_id)
    if not verification or verification.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification record not found.")

    now = _utcnow()
    verification.check_out_time = now
    verification.folio_bill_url = payload.folio_bill_url
    verification.check_out_photo_source = payload.check_out_photo_source
    verification.check_out_remarks = payload.check_out_remarks
    verification.check_out_lat = Decimal(str(payload.check_out_lat)) if payload.check_out_lat is not None else None
    verification.check_out_lng = Decimal(str(payload.check_out_lng)) if payload.check_out_lng is not None else None
    verification.check_out_location_name = geocoding.reverse_geocode(payload.check_out_lat, payload.check_out_lng)
    verification.status = StayVerificationStatus.CHECKED_OUT
    verification.reimbursement_prompted = True
    db.commit()
    db.refresh(verification)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.CHECK_OUT,
        entity_type="HotelStayVerification",
        entity_id=verification.id,
        summary=f"Checked out of hotel room {verification.room_number} with folio bill",
    )

    return _hotel_to_read(verification, fallback_user_name=actor.full_name)


@router.post(
    "/requests/{request_id}/hotel-verification/{verification_id}/force-close",
    response_model=HotelStayVerificationRead,
)
def force_close_hotel_stay(
    request_id: int,
    verification_id: int,
    payload: ForceClosePayload,
    db: DbSession,
    actor: CurrentUser,
) -> HotelStayVerificationRead:
    """Allow Admin or Manager to force close an in-progress hotel stay so employee can book again."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Managers and Admins can force close hotel stays.",
        )
    verification = db.get(HotelStayVerification, verification_id)
    if not verification or verification.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification record not found.")

    now = _utcnow()
    verification.status = StayVerificationStatus.CHECKED_OUT
    verification.check_out_time = now
    verification.force_closed_by_id = actor.id
    verification.force_closed_at = now
    verification.force_close_reason = payload.reason
    verification.reimbursement_prompted = True
    db.commit()
    db.refresh(verification)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="HotelStayVerification",
        entity_id=verification.id,
        summary=f"Hotel stay force-closed by {actor.full_name}: {payload.reason}",
    )

    return _hotel_to_read(verification, fallback_user_name=actor.full_name)


@router.post(
    "/requests/{request_id}/hotel-verification/{verification_id}/verify",
    response_model=HotelStayVerificationRead,
)
def verify_hotel_stay(
    request_id: int,
    verification_id: int,
    payload: VerificationReview,
    db: DbSession,
    actor: CurrentUser,
) -> HotelStayVerificationRead:
    """Admin or Finance verifies hotel stay folio and GPS alignment."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.FINANCE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Admin or Finance personnel may verify hotel stay records.",
        )

    verification = db.get(HotelStayVerification, verification_id)
    if not verification or verification.request_id != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification record not found.")

    verification.is_verified = True
    verification.status = StayVerificationStatus.VERIFIED
    verification.verified_by_id = actor.id
    verification.verified_at = _utcnow()
    verification.verification_notes = payload.notes
    db.commit()
    db.refresh(verification)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="HotelStayVerification",
        entity_id=verification.id,
        summary=f"Hotel stay verification #{verification.id} verified by {actor.full_name}",
    )

    return _hotel_to_read(verification)


# --- Telemetry Policy & Regularization Settings Endpoints --------------------

@router.get("/telemetry/settings", response_model=TelemetrySettingsRead)
def get_telemetry_settings(
    db: DbSession,
    actor: CurrentUser,
) -> TelemetrySettingsRead:
    """Retrieve organization telemetry policy settings."""
    setting = get_or_create_tenant_settings(db, actor.tenant_id)
    return TelemetrySettingsRead(
        require_telemetry_checkout=setting.require_telemetry_checkout,
        max_monthly_missed_photos=setting.max_monthly_missed_photos,
        ai_ticket_autofill_enabled=setting.ai_ticket_autofill_enabled,
    )


@router.patch("/telemetry/settings", response_model=TelemetrySettingsRead)
def update_telemetry_settings(
    payload: TelemetrySettingsUpdate,
    db: DbSession,
    actor: CurrentUser,
) -> TelemetrySettingsRead:
    """Admin updates organization policy settings (telemetry checkout lock, missed photos, AI autofill)."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can update organization telemetry settings.",
        )

    setting = get_or_create_tenant_settings(db, actor.tenant_id)
    if payload.require_telemetry_checkout is not None:
        setting.require_telemetry_checkout = payload.require_telemetry_checkout
    if payload.max_monthly_missed_photos is not None:
        setting.max_monthly_missed_photos = payload.max_monthly_missed_photos
    if payload.ai_ticket_autofill_enabled is not None:
        setting.ai_ticket_autofill_enabled = payload.ai_ticket_autofill_enabled

    db.commit()
    db.refresh(setting)
    return TelemetrySettingsRead(
        require_telemetry_checkout=setting.require_telemetry_checkout,
        max_monthly_missed_photos=setting.max_monthly_missed_photos,
        ai_ticket_autofill_enabled=setting.ai_ticket_autofill_enabled,
    )


@router.get("/telemetry/user-status/{user_id}", response_model=UserTelemetryStatusRead)
def get_user_telemetry_status(
    user_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> UserTelemetryStatusRead:
    """Check how many unclosed trips an employee has and whether they are locked."""
    user = db.get(User, user_id)
    if not user or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    settings = get_or_create_tenant_settings(db, actor.tenant_id)
    cabs, hotels = get_user_unclosed_telemetry(db, actor.tenant_id, user.id)
    total_unclosed = len(cabs) + len(hotels)

    is_locked = False
    if settings.require_telemetry_checkout and total_unclosed > settings.max_monthly_missed_photos:
        is_locked = True
        if user.telemetry_unlocked_at is not None:
            latest_created = max([c.created_at for c in cabs] + [h.created_at for h in hotels])
            u_at = user.telemetry_unlocked_at
            if u_at.tzinfo is not None and latest_created.tzinfo is None:
                latest_created = latest_created.replace(tzinfo=timezone.utc)
            elif u_at.tzinfo is None and latest_created.tzinfo is not None:
                u_at = u_at.replace(tzinfo=timezone.utc)
            if u_at >= latest_created:
                is_locked = False

    return UserTelemetryStatusRead(
        user_id=user.id,
        user_name=user.full_name,
        missed_cabs_count=len(cabs),
        missed_hotels_count=len(hotels),
        total_missed_count=total_unclosed,
        max_allowed=settings.max_monthly_missed_photos,
        is_locked=is_locked,
        telemetry_unlocked_at=user.telemetry_unlocked_at,
        require_telemetry_checkout=settings.require_telemetry_checkout,
    )


@router.post("/telemetry/unlock-user/{user_id}")
def unlock_user_telemetry(
    user_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> dict[str, str]:
    """Manager or Admin removes the active booking lock for an employee."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.MANAGER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only Managers and Admins can unlock employee booking restrictions.",
        )

    user = db.get(User, user_id)
    if not user or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.telemetry_unlocked_at = _utcnow()
    db.commit()

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.APPROVE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} unlocked booking restriction for {user.full_name}",
    )

    return {"message": f"Successfully unlocked {user.full_name}. They can now book trips."}

