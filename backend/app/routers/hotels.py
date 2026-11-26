"""
Saved Hotels Directory Router (SOW Section 1.1 & 7).

Allows vendors and admins to view, search, and register hotels in the database
so they can be instantly auto-filled when submitting stay options for employee trips.
"""
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import or_, select

from app.core.deps import CurrentUser, DbSession
from app.core.enums import Role
from app.models.hotel import SavedHotel
from app.schemas.hotel import SavedHotelCreate, SavedHotelRead, SavedHotelUpdate

router = APIRouter(prefix="/hotels", tags=["hotels"])


def _to_read(hotel: SavedHotel) -> SavedHotelRead:
    return SavedHotelRead(
        id=hotel.id,
        hotel_name=hotel.hotel_name,
        city=hotel.city,
        state=hotel.state,
        address=hotel.address,
        landmark=hotel.landmark,
        google_maps_url=hotel.google_maps_url,
        room_category=hotel.room_category,
        nightly_rate=hotel.nightly_rate,
        contact_phone=hotel.contact_phone,
        amenities=hotel.amenities or [],
        vendor_notes=hotel.vendor_notes,
        vendor_id=hotel.vendor_id,
        vendor_name=hotel.vendor.name if hotel.vendor else None,
        is_active=hotel.is_active,
        created_at=hotel.created_at,
        updated_at=hotel.updated_at,
    )


@router.get("", response_model=list[SavedHotelRead])
def list_saved_hotels(
    db: DbSession,
    actor: CurrentUser,
    city: Annotated[str | None, Query()] = None,
    query: Annotated[str | None, Query()] = None,
    vendor_id: Annotated[int | None, Query()] = None,
    active_only: Annotated[bool, Query()] = True,
) -> list[SavedHotelRead]:
    """Retrieve saved hotels matching optional city, vendor, or search query."""
    stmt = select(SavedHotel).where(SavedHotel.tenant_id == actor.tenant_id)

    if active_only:
        stmt = stmt.where(SavedHotel.is_active.is_(True))

    if city:
        city_clean = city.strip().lower()
        stmt = stmt.where(SavedHotel.city.ilike(f"%{city_clean}%"))

    # Vendor scoping: if actor is a vendor, show their hotels plus general (unassigned) hotels
    if actor.role == Role.VENDOR:
        if actor.vendor_id:
            stmt = stmt.where(
                or_(SavedHotel.vendor_id == actor.vendor_id, SavedHotel.vendor_id.is_(None))
            )
    elif vendor_id is not None:
        stmt = stmt.where(SavedHotel.vendor_id == vendor_id)

    if query:
        term = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                SavedHotel.hotel_name.ilike(term),
                SavedHotel.landmark.ilike(term),
                SavedHotel.address.ilike(term),
                SavedHotel.city.ilike(term),
            )
        )

    stmt = stmt.order_by(SavedHotel.hotel_name.asc())
    rows = db.execute(stmt).scalars().all()
    return [_to_read(h) for h in rows]


@router.post("", response_model=SavedHotelRead, status_code=status.HTTP_201_CREATED)
def create_saved_hotel(
    payload: SavedHotelCreate,
    db: DbSession,
    actor: CurrentUser,
) -> SavedHotelRead:
    """Save a hotel to the organization directory."""
    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN, Role.VENDOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators and vendors can add hotels to the directory.",
        )

    assigned_vendor_id = actor.vendor_id if actor.role == Role.VENDOR else payload.vendor_id

    hotel = SavedHotel(
        tenant_id=actor.tenant_id,
        vendor_id=assigned_vendor_id,
        hotel_name=payload.hotel_name.strip(),
        city=payload.city.strip(),
        state=payload.state.strip() if payload.state else None,
        address=payload.address.strip(),
        landmark=payload.landmark.strip() if payload.landmark else None,
        google_maps_url=payload.google_maps_url.strip() if payload.google_maps_url else None,
        room_category=payload.room_category.strip() or "Standard",
        nightly_rate=payload.nightly_rate,
        contact_phone=payload.contact_phone.strip() if payload.contact_phone else None,
        amenities=payload.amenities,
        vendor_notes=payload.vendor_notes.strip() if payload.vendor_notes else None,
        is_active=True,
    )
    db.add(hotel)
    db.commit()
    db.refresh(hotel)
    return _to_read(hotel)


@router.patch("/{hotel_id}", response_model=SavedHotelRead)
def update_saved_hotel(
    hotel_id: int,
    payload: SavedHotelUpdate,
    db: DbSession,
    actor: CurrentUser,
) -> SavedHotelRead:
    hotel = db.get(SavedHotel, hotel_id)
    if not hotel or hotel.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hotel not found.")

    if actor.role == Role.VENDOR and hotel.vendor_id != actor.vendor_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit another vendor's hotel.")

    updates = payload.model_dump(exclude_unset=True)
    for field, val in updates.items():
        if isinstance(val, str):
            val = val.strip()
        setattr(hotel, field, val)

    db.commit()
    db.refresh(hotel)
    return _to_read(hotel)


@router.delete("/{hotel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_hotel(
    hotel_id: int,
    db: DbSession,
    actor: CurrentUser,
) -> None:
    hotel = db.get(SavedHotel, hotel_id)
    if not hotel or hotel.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hotel not found.")

    if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN, Role.SYSTEM_ADMIN):
        if actor.role == Role.VENDOR and hotel.vendor_id == actor.vendor_id:
            pass
        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")

    db.delete(hotel)
    db.commit()
