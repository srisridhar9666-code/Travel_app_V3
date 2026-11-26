"""
Vendors: the travel agents, cab operators and hotels the organisation pays.

Admins and system admins keep the list - add, correct, switch off, switch back
on. Every admin tier reads it, the super admin included, because they approve
the invoices raised against these vendors and need to see who is being paid.
A vendor is never deleted: past costs and invoices point at it.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.core.deps import AdminUser, DbSession, InvoiceEditor
from app.core.enums import AuditAction, Gender, Role, UserStatus
from app.core.security import hash_password
from app.models.invoice import Invoice
from app.models.request import RequestTraveller
from app.models.user import User
from app.models.vendor import Vendor
from app.schemas.vendor import (
    VendorCreate,
    VendorLoginRequest,
    VendorLoginResponse,
    VendorRead,
    VendorUpdate,
)
from app.services import audit

router = APIRouter(prefix="/vendors", tags=["vendors"])

#: The columns an edit can touch, as the activity log names them.
_FIELDS = ("name", "kind", "contact_name", "phone", "email", "gstin", "notes")


def _counts(db: DbSession, tenant_id: str, ids: list[int]) -> tuple[dict[int, int], dict[int, int], dict[int, User]]:
    """Costs recorded against each vendor, invoices raised against them, and linked portal users.

    Grouped queries for the whole list rather than queries per row.
    """
    if not ids:
        return {}, {}, {}
    travellers = dict(
        db.execute(
            select(RequestTraveller.vendor_id, func.count(RequestTraveller.id))
            .where(RequestTraveller.vendor_id.in_(ids))
            .group_by(RequestTraveller.vendor_id)
        ).all()
    )
    invoices = dict(
        db.execute(
            select(Invoice.vendor_id, func.count(Invoice.id))
            .where(Invoice.tenant_id == tenant_id, Invoice.vendor_id.in_(ids))
            .group_by(Invoice.vendor_id)
        ).all()
    )
    users = db.execute(
        select(User).where(User.tenant_id == tenant_id, User.vendor_id.in_(ids))
    ).scalars().all()
    portal_users: dict[int, User] = {}
    for u in users:
        if u.vendor_id is not None and u.vendor_id not in portal_users:
            portal_users[u.vendor_id] = u

    return travellers, invoices, portal_users


def _read(
    vendor: Vendor,
    travellers: dict[int, int],
    invoices: dict[int, int],
    portal_users: dict[int, User] | None = None,
) -> VendorRead:
    portal_user = portal_users.get(vendor.id) if portal_users else None
    return VendorRead(
        id=vendor.id,
        name=vendor.name,
        kind=vendor.kind,
        contact_name=vendor.contact_name,
        phone=vendor.phone,
        email=vendor.email,
        gstin=vendor.gstin,
        notes=vendor.notes,
        is_active=vendor.is_active,
        created_at=vendor.created_at,
        traveller_count=travellers.get(vendor.id, 0),
        invoice_count=invoices.get(vendor.id, 0),
        portal_user_id=portal_user.id if portal_user else None,
        portal_email=portal_user.email if portal_user else None,
        portal_user_status=portal_user.status.value if portal_user else None,
    )


def _one(db: DbSession, vendor: Vendor) -> VendorRead:
    return _read(vendor, *_counts(db, vendor.tenant_id, [vendor.id]))


def _get(db: DbSession, actor: User, vendor_id: int) -> Vendor:
    found = db.get(Vendor, vendor_id)
    if found is None or found.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found.")
    return found


def _name_free(db: DbSession, tenant_id: str, name: str, *, besides: int | None = None) -> None:
    # An equality match is enough: the column's collation ignores case, which
    # is also what the unique index enforces.
    clash = db.execute(
        select(Vendor).where(Vendor.tenant_id == tenant_id, Vendor.name == name)
    ).scalar_one_or_none()
    if clash is not None and clash.id != besides:
        state = "" if clash.is_active else " (switched off - switch it back on instead)"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"There is already a vendor called {clash.name}{state}.",
        )


@router.get("", response_model=list[VendorRead])
def list_vendors(
    actor: AdminUser,
    db: DbSession,
    active: Annotated[bool | None, Query()] = None,
) -> list[VendorRead]:
    """Every vendor, active ones first, by name. `active` narrows to one side."""
    filters = [Vendor.tenant_id == actor.tenant_id]
    if active is not None:
        filters.append(Vendor.is_active.is_(active))
    rows = list(
        db.execute(
            select(Vendor).where(*filters).order_by(Vendor.is_active.desc(), Vendor.name)
        ).scalars()
    )
    travellers, invoices, portal_users = _counts(db, actor.tenant_id, [v.id for v in rows])
    return [_read(v, travellers, invoices, portal_users) for v in rows]


@router.post("", response_model=VendorRead, status_code=status.HTTP_201_CREATED)
def create_vendor(
    payload: VendorCreate, actor: InvoiceEditor, request: Request, db: DbSession
) -> VendorRead:
    _name_free(db, actor.tenant_id, payload.name)
    data = payload.model_dump(exclude={"create_login", "login_email", "login_password"})
    vendor = Vendor(tenant_id=actor.tenant_id, **data)
    db.add(vendor)
    db.flush()

    if payload.create_login:
        target_email = (payload.login_email or payload.email or "").strip().lower()
        if not target_email:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="An email address is required to create a vendor portal login.",
            )
        existing = db.execute(
            select(User).where(User.tenant_id == actor.tenant_id, User.email == target_email)
        ).scalar_one_or_none()
        raw_pw = payload.login_password or "Vendor@123"
        if existing:
            existing.vendor_id = vendor.id
            existing.role = Role.VENDOR
            existing.password_hash = hash_password(raw_pw)
            existing.status = UserStatus.ACTIVE
        else:
            new_user = User(
                tenant_id=actor.tenant_id,
                email=target_email,
                full_name=payload.contact_name or payload.name,
                role=Role.VENDOR,
                vendor_id=vendor.id,
                password_hash=hash_password(raw_pw),
                status=UserStatus.ACTIVE,
                gender=Gender.UNDISCLOSED,
                phone=payload.phone,
            )
            db.add(new_user)

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="vendor",
        entity_id=vendor.id,
        summary=f"{actor.full_name} added the vendor {vendor.name}",
        changes=audit.diff({}, {f: getattr(vendor, f) for f in _FIELDS}),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(vendor)
    return _one(db, vendor)


@router.patch("/{vendor_id}", response_model=VendorRead)
def update_vendor(
    vendor_id: int,
    payload: VendorUpdate,
    actor: InvoiceEditor,
    request: Request,
    db: DbSession,
) -> VendorRead:
    """Correct a vendor's details. Only the fields sent change; saving the same
    values again writes nothing to the log."""
    vendor = _get(db, actor, vendor_id)
    sent = payload.model_dump(exclude_unset=True)
    if "name" in sent:
        _name_free(db, actor.tenant_id, sent["name"], besides=vendor.id)

    before = {f: getattr(vendor, f) for f in _FIELDS}
    for field, value in sent.items():
        setattr(vendor, field, value)
    changes = audit.diff(before, {f: getattr(vendor, f) for f in _FIELDS})
    if changes:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="vendor",
            entity_id=vendor.id,
            summary=f"{actor.full_name} updated the vendor {vendor.name}",
            changes=changes,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
        db.commit()
        db.refresh(vendor)
    return _one(db, vendor)


def _switch(db: DbSession, actor: User, vendor_id: int, on: bool, request: Request) -> VendorRead:
    vendor = _get(db, actor, vendor_id)
    if vendor.is_active is not on:
        vendor.is_active = on
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="vendor",
            entity_id=vendor.id,
            summary=(
                f"{actor.full_name} {'switched on' if on else 'switched off'} "
                f"the vendor {vendor.name}"
            ),
            changes={"is_active": {"from": not on, "to": on}},
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
        db.commit()
        db.refresh(vendor)
    return _one(db, vendor)


@router.post("/{vendor_id}/deactivate", response_model=VendorRead)
def deactivate_vendor(
    vendor_id: int, actor: InvoiceEditor, request: Request, db: DbSession
) -> VendorRead:
    """Stop offering this vendor for new costs. Costs already recorded keep it,
    and its invoices can still be finished, so its last bills get settled."""
    return _switch(db, actor, vendor_id, False, request)


@router.post("/{vendor_id}/activate", response_model=VendorRead)
def activate_vendor(
    vendor_id: int, actor: InvoiceEditor, request: Request, db: DbSession
) -> VendorRead:
    return _switch(db, actor, vendor_id, True, request)


@router.post("/{vendor_id}/login", response_model=VendorLoginResponse)
def create_or_reset_vendor_login(
    vendor_id: int,
    payload: VendorLoginRequest,
    actor: InvoiceEditor,
    db: DbSession,
) -> VendorLoginResponse:
    """Create or reset credentials for a vendor portal user."""
    vendor = _get(db, actor, vendor_id)

    portal_user = db.execute(
        select(User).where(User.tenant_id == actor.tenant_id, User.vendor_id == vendor.id)
    ).scalars().first()

    target_email = (payload.email or (portal_user.email if portal_user else vendor.email) or "").strip().lower()
    if not target_email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Vendor email is required to create a portal login.",
        )
    target_name = payload.full_name or vendor.contact_name or vendor.name
    raw_pw = payload.password or "Vendor@123"

    if portal_user:
        portal_user.email = target_email
        portal_user.full_name = target_name
        portal_user.password_hash = hash_password(raw_pw)
        portal_user.status = UserStatus.ACTIVE
        portal_user.role = Role.VENDOR
        msg = f"Portal login updated for {vendor.name}"
    else:
        clash = db.execute(
            select(User).where(User.tenant_id == actor.tenant_id, User.email == target_email)
        ).scalar_one_or_none()
        if clash:
            clash.vendor_id = vendor.id
            clash.role = Role.VENDOR
            clash.password_hash = hash_password(raw_pw)
            clash.status = UserStatus.ACTIVE
            portal_user = clash
            msg = f"Existing user {target_email} linked as portal login for {vendor.name}"
        else:
            portal_user = User(
                tenant_id=actor.tenant_id,
                email=target_email,
                full_name=target_name,
                role=Role.VENDOR,
                vendor_id=vendor.id,
                password_hash=hash_password(raw_pw),
                status=UserStatus.ACTIVE,
                gender=Gender.UNDISCLOSED,
                phone=vendor.phone,
            )
            db.add(portal_user)
            msg = f"Portal login created for {vendor.name}"

    db.commit()
    db.refresh(portal_user)
    return VendorLoginResponse(
        success=True,
        user_id=portal_user.id,
        email=portal_user.email,
        full_name=portal_user.full_name,
        message=msg,
        temporary_password=raw_pw,
    )

