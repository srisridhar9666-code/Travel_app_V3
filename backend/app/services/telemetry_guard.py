"""
Telemetry Guard & Regularization Service (SOW Section 8 & Admin Policies).

Enforces trip closure & checkout verification while supporting:
1. Admin toggle to turn telemetry checkout enforcement ON/OFF
2. Configurable monthly missed photos / unclosed trip regularization allowance (e.g. 5/month)
3. Manager & Admin one-click override / unlock capability for employees
"""
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import RequestType, StayVerificationStatus, TelemetryStatus
from app.models.request import TravelRequest
from app.models.setting import TenantSetting
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.user import User


def get_or_create_tenant_settings(db: Session, tenant_id: str) -> TenantSetting:
    setting = db.execute(
        select(TenantSetting).where(TenantSetting.tenant_id == tenant_id)
    ).scalar_one_or_none()
    if not setting:
        setting = TenantSetting(
            tenant_id=tenant_id,
            require_telemetry_checkout=True,
            max_monthly_missed_photos=5,
        )
        db.add(setting)
        db.commit()
        db.refresh(setting)
    return setting


def get_user_unclosed_telemetry(db: Session, tenant_id: str, user_id: int) -> tuple[list[CabTelemetry], list[HotelStayVerification]]:
    unclosed_cabs = list(
        db.execute(
            select(CabTelemetry)
            .join(TravelRequest, CabTelemetry.request_id == TravelRequest.id)
            .where(
                CabTelemetry.tenant_id == tenant_id,
                TravelRequest.requester_id == user_id,
                CabTelemetry.status == TelemetryStatus.IN_PROGRESS,
            )
        ).scalars().all()
    )
    unclosed_hotels = list(
        db.execute(
            select(HotelStayVerification)
            .where(
                HotelStayVerification.tenant_id == tenant_id,
                HotelStayVerification.user_id == user_id,
                HotelStayVerification.status == StayVerificationStatus.CHECKED_IN,
            )
        ).scalars().all()
    )
    return unclosed_cabs, unclosed_hotels


def check_booking_telemetry_lock(db: Session, user: User, request_type: RequestType) -> None:
    """Enforce active booking lock according to admin settings and monthly regularization allowance."""
    if user.is_admin:
        return

    # Only local cabs and hotels require checkout verification
    if request_type not in (RequestType.LOCAL_CAB, RequestType.HOTEL):
        return

    settings = get_or_create_tenant_settings(db, user.tenant_id)
    if not settings.require_telemetry_checkout:
        return

    unclosed_cabs, unclosed_hotels = get_user_unclosed_telemetry(db, user.tenant_id, user.id)
    total_unclosed = len(unclosed_cabs) + len(unclosed_hotels)
    if total_unclosed == 0:
        return

    # Check if unlocked by manager or admin
    if user.telemetry_unlocked_at is not None:
        latest_created = max(
            [c.created_at for c in unclosed_cabs] + [h.created_at for h in unclosed_hotels]
        )
        u_at = user.telemetry_unlocked_at
        if u_at.tzinfo is not None and latest_created.tzinfo is None:
            latest_created = latest_created.replace(tzinfo=timezone.utc)
        elif u_at.tzinfo is None and latest_created.tzinfo is not None:
            u_at = u_at.replace(tzinfo=timezone.utc)
        if u_at >= latest_created:
            return

    # Allow if within monthly regularization limit
    if total_unclosed <= settings.max_monthly_missed_photos:
        return

    # Exceeded monthly regularization limit: block until checkout or manager/admin unlock
    req_type_name = "cab" if request_type == RequestType.LOCAL_CAB else "hotel"
    first_req_id = unclosed_cabs[0].request_id if unclosed_cabs else unclosed_hotels[0].request_id

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"Cannot book another {req_type_name}: You have {total_unclosed} unclosed trip(s) "
            f"without final photos/checkout (e.g. Request #{first_req_id}), exceeding your monthly "
            f"regularization limit of {settings.max_monthly_missed_photos}. "
            "Please complete checkout or ask your Manager/Admin to remove the lock."
        ),
    )
