"""
Hotel Proposal API (SOW Section 1.1 & 7).

Manages vendor hotel sourcing: vendors submit 2 to 5 available hotel options
with room categories, amenities, map links, and nightly rates; employees select
their preferred hotel option within policy limits.
"""
from __future__ import annotations

from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.enums import (
    AuditAction,
    HotelOccupancy,
    HotelProposalStatus,
    RequestType,
    Role,
)
from app.models.base import naive_utcnow
from app.models.hotel import SavedHotel
from app.models.hotel_proposal import HotelProposal
from app.models.request import TravelRequest
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.hotel_proposal import (
    BatchHotelProposalsCreate,
    HotelProposalCreate,
    HotelProposalRead,
)
from app.services import audit, notifications, policy

router = APIRouter(prefix="/requests/{request_id}/hotel-proposals", tags=["hotel-proposals"])


def _load_request(db: DbSession, request_id: int, actor: User) -> TravelRequest:
    req = db.get(TravelRequest, request_id)
    if req is None or req.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    if req.request_type is not RequestType.HOTEL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hotel proposals are only applicable for hotel requests.",
        )
    return req


def _can_manage_proposals(actor: User, req: TravelRequest) -> bool:
    """Admins or the assigned contracted vendor can submit/manage proposals."""
    if actor.is_admin:
        return True
    if actor.role is Role.VENDOR and actor.vendor_id is not None:
        return req.assigned_vendor_id is None or req.assigned_vendor_id == actor.vendor_id
    return False


def _to_read(prop: HotelProposal, req: TravelRequest, *, hide_cost: bool = False) -> HotelProposalRead:
    occupancy = getattr(req, "hotel_occupancy", HotelOccupancy.SINGLE) or HotelOccupancy.SINGLE
    ceiling = Decimal(str(policy.lodging_ceiling(req.hotel_city, occupancy)))
    rate = Decimal(str(prop.nightly_rate))
    is_compliant = rate <= ceiling
    excess = max(Decimal("0.00"), rate - ceiling)

    return HotelProposalRead(
        id=prop.id,
        request_id=prop.request_id,
        vendor_id=prop.vendor_id,
        vendor_name=prop.vendor.name if prop.vendor else None,
        hotel_name=prop.hotel_name,
        address=prop.address,
        landmark=prop.landmark,
        google_maps_url=prop.google_maps_url,
        room_category=prop.room_category,
        nightly_rate=None if hide_cost else rate,
        amenities=prop.amenities or [],
        status=prop.status,
        is_policy_compliant=is_compliant,
        is_within_policy=is_compliant,
        policy_rate_limit=None if hide_cost else ceiling,
        policy_limit_per_night=None if hide_cost else ceiling,
        excess_amount=None if hide_cost else excess,
        excess_per_night=None if hide_cost else excess,
        selected_by_id=prop.selected_by_id,
        selected_by_name=prop.selected_by.full_name if prop.selected_by else None,
        selected_at=prop.selected_at,
        vendor_notes=prop.vendor_notes,
        created_at=prop.created_at,
        updated_at=prop.updated_at,
    )


@router.get("", response_model=list[HotelProposalRead])
def list_hotel_proposals(
    request_id: int,
    actor: CurrentUser,
    db: DbSession,
) -> list[HotelProposalRead]:
    """List all hotel proposals submitted for this booking request."""
    req = _load_request(db, request_id, actor)
    proposals = (
        db.execute(
            select(HotelProposal)
            .where(
                HotelProposal.request_id == request_id,
                HotelProposal.tenant_id == actor.tenant_id,
            )
            .order_by(HotelProposal.nightly_rate.asc())
        )
        .scalars()
        .all()
    )
    hide_cost = actor.role == Role.GROUND_STAFF
    return [_to_read(p, req, hide_cost=hide_cost) for p in proposals]


@router.post("", response_model=list[HotelProposalRead], status_code=status.HTTP_201_CREATED)
def submit_hotel_proposals(
    request_id: int,
    payload: BatchHotelProposalsCreate | list[HotelProposalCreate] | HotelProposalCreate,
    actor: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> list[HotelProposalRead]:
    """Vendor or Admin submits available hotel options (SOW Section 7: 2 to 5 options recommended)."""
    req = _load_request(db, request_id, actor)

    if not _can_manage_proposals(actor, req):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only contracted vendors or Operations Admins can submit hotel proposals.",
        )

    # Determine vendor_id
    if actor.role is Role.VENDOR:
        vendor_id = actor.vendor_id
        if vendor_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your user account is not linked to a registered vendor profile.",
            )
    else:
        # Admin submitting: use assigned vendor or fallback
        vendor_id = req.assigned_vendor_id
        if vendor_id is None:
            # Pick first active hotel vendor in tenant
            first_vendor = db.execute(
                select(Vendor).where(
                    Vendor.tenant_id == actor.tenant_id,
                    Vendor.is_active.is_(True),
                )
            ).scalars().first()
            if first_vendor:
                vendor_id = first_vendor.id
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Please assign a vendor to this request before submitting proposals.",
                )

    if req.assigned_vendor_id is None:
        req.assigned_vendor_id = vendor_id

    # Normalize options list
    if isinstance(payload, BatchHotelProposalsCreate):
        options = payload.options if payload.options else payload.proposals
    elif isinstance(payload, dict):
        options = payload.get("options") or payload.get("proposals") or []
    elif isinstance(payload, list):
        options = payload
    else:
        options = [payload]

    if not options:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Submit at least 1 hotel proposal option.",
        )

    created_proposals: list[HotelProposal] = []
    vendor = db.get(Vendor, vendor_id)
    vendor_name = vendor.name if vendor else "Assigned Vendor"

    for opt in options:
        prop = HotelProposal(
            tenant_id=actor.tenant_id,
            request_id=req.id,
            vendor_id=vendor_id,
            hotel_name=opt.hotel_name.strip(),
            address=opt.address.strip(),
            landmark=opt.landmark.strip() if opt.landmark else None,
            google_maps_url=opt.google_maps_url.strip() if opt.google_maps_url else None,
            room_category=opt.room_category.strip(),
            nightly_rate=opt.nightly_rate,
            amenities=opt.amenities,
            vendor_notes=opt.vendor_notes.strip() if opt.vendor_notes else None,
            status=HotelProposalStatus.PROPOSED,
        )
        db.add(prop)
        created_proposals.append(prop)

        # Auto-save to organization hotel directory if requested
        if opt.save_to_directory:
            city_name = req.hotel_city or req.destination or "Local"
            existing_saved = db.execute(
                select(SavedHotel).where(
                    SavedHotel.tenant_id == actor.tenant_id,
                    SavedHotel.hotel_name == opt.hotel_name.strip(),
                    SavedHotel.city == city_name,
                )
            ).scalars().first()
            if not existing_saved:
                saved = SavedHotel(
                    tenant_id=actor.tenant_id,
                    vendor_id=vendor_id,
                    hotel_name=opt.hotel_name.strip(),
                    city=city_name,
                    state=req.hotel_state or req.destination_state,
                    address=opt.address.strip(),
                    landmark=opt.landmark.strip() if opt.landmark else None,
                    google_maps_url=opt.google_maps_url.strip() if opt.google_maps_url else None,
                    room_category=opt.room_category.strip() or "Standard",
                    nightly_rate=opt.nightly_rate,
                    amenities=opt.amenities,
                    vendor_notes=opt.vendor_notes.strip() if opt.vendor_notes else None,
                    is_active=True,
                )
                db.add(saved)

    audit.record(
        db,
        action=AuditAction.PROPOSE_HOTEL,
        entity_type="travel_request",
        entity_id=req.id,
        summary=f"{vendor_name} submitted {len(options)} hotel proposal option(s) for request {req.id}",
        changes={"proposal_count": len(options)},
        reason="Vendor hotel sourcing fulfillment",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )

    # Notify requester
    if req.requester:
        notifications.notify(
            db,
            tenant_id=actor.tenant_id,
            user=req.requester,
            kind="HOTEL_PROPOSALS",
            title=f"Hotel options received for {req.hotel_city}",
            body=(
                f"{vendor_name} submitted {len(options)} hotel options for your stay in "
                f"{req.hotel_city}. Please review and select your preferred hotel."
            ),
            request_id=req.id,
            send_email=True,
        )

    db.commit()
    for p in created_proposals:
        db.refresh(p)

    return [_to_read(p, req) for p in created_proposals]


@router.post("/{proposal_id}/select", response_model=HotelProposalRead)
def select_hotel_proposal(
    request_id: int,
    proposal_id: int,
    actor: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> HotelProposalRead:
    """Requester or Admin selects their preferred hotel option (SOW Section 7)."""
    req = _load_request(db, request_id, actor)

    # Requester or Admin only
    is_requester = req.requester_id == actor.id
    if not (is_requester or actor.is_admin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the requester or an Operations Admin can select the preferred hotel.",
        )

    proposal = db.get(HotelProposal, proposal_id)
    if proposal is None or proposal.request_id != request_id or proposal.tenant_id != actor.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hotel proposal option not found for this request.",
        )

    now = naive_utcnow()

    # Mark all other proposals for this request as rejected
    other_proposals = (
        db.execute(
            select(HotelProposal).where(
                HotelProposal.request_id == request_id,
                HotelProposal.id != proposal_id,
                HotelProposal.tenant_id == actor.tenant_id,
            )
        )
        .scalars()
        .all()
    )
    for other in other_proposals:
        other.status = HotelProposalStatus.REJECTED

    proposal.status = HotelProposalStatus.SELECTED
    proposal.selected_by_id = actor.id
    proposal.selected_at = now
    req.selected_hotel_proposal_id = proposal.id

    audit.record(
        db,
        action=AuditAction.SELECT_HOTEL,
        entity_type="hotel_proposal",
        entity_id=proposal.id,
        summary=f"{actor.full_name} selected hotel '{proposal.hotel_name}' (₹{proposal.nightly_rate}/night) for request {req.id}",
        changes={
            "selected_proposal_id": proposal.id,
            "hotel_name": proposal.hotel_name,
            "nightly_rate": str(proposal.nightly_rate),
        },
        reason="Employee preferred hotel selection",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )

    # Notify assigned vendor that employee chose the hotel so vendor can issue voucher
    if req.assigned_vendor:
        # Find vendor user if exists
        vendor_users = db.execute(
            select(User).where(
                User.tenant_id == actor.tenant_id,
                User.vendor_id == req.assigned_vendor_id,
                User.is_active.is_(True),
            )
        ).scalars().all()
        for v_user in vendor_users:
            notifications.notify(
                db,
                tenant_id=actor.tenant_id,
                user=v_user,
                kind="HOTEL_PROPOSAL_SELECTED",
                title=f"Hotel Selected: {proposal.hotel_name} for Request #{req.id}",
                body=(
                    f"{actor.full_name} selected {proposal.hotel_name} for stay in {req.hotel_city}. "
                    "Please issue the double/single booking voucher."
                ),
                request_id=req.id,
                send_email=True,
            )

    db.commit()
    db.refresh(proposal)
    return _to_read(proposal, req, hide_cost=(actor.role == Role.GROUND_STAFF))


@router.delete("/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_hotel_proposal(
    request_id: int,
    proposal_id: int,
    actor: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> None:
    """Admin or submitting vendor can delete an unselected hotel proposal."""
    req = _load_request(db, request_id, actor)

    if not _can_manage_proposals(actor, req):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins or the submitting vendor can remove hotel proposals.",
        )

    proposal = db.get(HotelProposal, proposal_id)
    if proposal is None or proposal.request_id != request_id or proposal.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hotel proposal not found.")

    if proposal.status is HotelProposalStatus.SELECTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a hotel proposal that has already been selected by the employee.",
        )

    db.delete(proposal)
    audit.record(
        db,
        action=AuditAction.DELETE,
        entity_type="hotel_proposal",
        entity_id=proposal_id,
        summary=f"{actor.full_name} deleted proposal '{proposal.hotel_name}' on request {req.id}",
        reason="Vendor proposal retracted/deleted",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
