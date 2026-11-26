"""
User administration (SOW section 5, directory management).

Accounts are created by an admin and activated by the person themselves through
an emailed invite link. There is no self-registration - addendum B10.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction, HotelOccupancy, Role, TokenPurpose, UserStatus
from app.models.base import naive_utcnow
from app.models.department import Department
from app.models.user import User
from app.routers.auth import INVITE_VALID_HOURS, _issue_token, build_invite_url
from app.schemas.auth import InviteLinkResponse
from app.schemas.bulk import ImportPreview, ImportResult
from app.schemas.user import (
    OpenTrips,
    UserCreate,
    UserListResponse,
    UserRead,
    UserStatusChange,
    UserUpdate,
)
from app.services import accounts, audit, bulk_import, history, locations
from app.services import email as email_service

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/users", tags=["users"])


def _to_read(user: User) -> UserRead:
    now = naive_utcnow()
    return UserRead(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        employee_code=user.employee_code,
        role=user.role,
        designation=user.designation,
        gender=user.gender,
        phone=user.phone,
        base_state=user.base_state,
        base_location=user.base_location,
        department_id=user.department_id,
        department_name=user.department_name,
        manager_id=user.manager_id,
        manager_name=user.manager_name,
        vendor_id=user.vendor_id,
        vendor_name=user.vendor_name,
        hotel_occupancy_entitlement=getattr(user, "hotel_occupancy_entitlement", HotelOccupancy.SINGLE) or HotelOccupancy.SINGLE,
        upi_id=getattr(user, "upi_id", None),
        bank_name=getattr(user, "bank_name", None),
        bank_account_number=getattr(user, "bank_account_number", None),
        account_holder_name=getattr(user, "account_holder_name", None),
        ifsc_code=getattr(user, "ifsc_code", None),
        status=user.status,
        status_changed_at=user.status_changed_at,
        is_active=user.is_active,
        exited_on=user.exited_on,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        has_password=user.has_password,
        is_locked=bool(user.locked_until and user.locked_until > now),
    )


def _guard_role_assignment(actor: User, target_role: Role | None) -> None:
    """Nobody grants a role above their own.

    Without this, any admin could promote someone - or, through a friend,
    themselves - past the role that governs user management.
    """
    if target_role is Role.SYSTEM_ADMIN:
        # Admin and system admin differed only in data retention, which now
        # sits with the super admin; the role remains only so old rows load.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="System admin is no longer a separate role - choose Admin, or Super admin.",
        )
    if target_role is not None and accounts.outranks(target_role, actor.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only a {target_role.replace('_', ' ').lower()} or above can grant that role.",
        )


def _check_manager(db: Session, actor: User, manager_id: int | None, role: Role) -> None:
    """A team member reports to an active manager of this organisation.

    One level only: ground staff report to a manager, and nobody else reports
    to anyone. 422, because a stale pick from another tab is a bad value in the
    form, not a missing page.
    """
    if manager_id is None:
        return
    if role is not Role.GROUND_STAFF:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only ground staff report to a manager.",
        )
    manager = db.get(User, manager_id)
    if (
        manager is None
        or manager.tenant_id != actor.tenant_id
        or manager.role is not Role.MANAGER
        or manager.status is not UserStatus.ACTIVE
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Pick an active manager for them to report to.",
        )


def _get_target(db: Session, actor: User, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


def _check_department(db: Session, actor: User, department_id: int | None) -> None:
    """A department from this organisation, or none. 422, because a stale id
    from another tab is a bad value in the form, not a missing page."""
    if department_id is None:
        return
    found = db.get(Department, department_id)
    if found is None or found.tenant_id != actor.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown department."
        )


def _email_taken(db: Session, tenant_id: str, email: str, *, other_than: int | None = None) -> None:
    """409 when the address already belongs to someone.

    A deleted account keeps its address - it can be restored, with its history
    - so the message says where to find it rather than leaving the admin
    puzzled that nobody on the list has it.
    """
    stmt = select(User).where(User.tenant_id == tenant_id, User.email == email)
    if other_than is not None:
        stmt = stmt.where(User.id != other_than)
    existing = db.execute(stmt).scalar_one_or_none()
    if existing is None:
        return
    if existing.status is UserStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{email} belongs to a deleted account - restore it from the team list "
                "(Status: Deleted)."
            ),
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=f"{email} already has an account."
    )


@router.get("", response_model=UserListResponse)
def list_users(
    actor: AdminUser,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=120)] = None,
    role: Annotated[Role | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    user_status: Annotated[UserStatus | None, Query(alias="status")] = None,
    department_id: Annotated[int | None, Query()] = None,
    manager_id: Annotated[int | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 25,
) -> UserListResponse:
    """Everyone except deleted accounts, unless asked for those by status.

    Deactivated and left people stay on the list: they still have history,
    and an admin looking for them should find them.
    """
    filters = [User.tenant_id == actor.tenant_id]
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(
                User.full_name.like(like),
                User.email.like(like),
                User.employee_code.like(like),
                User.base_location.like(like),
                User.base_state.like(like),
                Department.name.like(like),
            )
        )
    if role is not None:
        filters.append(User.role == role)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if user_status is None:
        filters.append(User.status != UserStatus.DELETED)
    else:
        filters.append(User.status == user_status)
    if department_id is not None:
        filters.append(User.department_id == department_id)
    if manager_id is not None:
        filters.append(User.manager_id == manager_id)

    # Joined for the search on its name; the department itself loads with the
    # user anyway.
    joined = Department, Department.id == User.department_id
    total = db.execute(
        select(func.count(User.id)).select_from(User).outerjoin(*joined).where(*filters)
    ).scalar_one()
    rows = (
        db.execute(
            select(User)
            .outerjoin(*joined)
            .where(*filters)
            .order_by(User.full_name)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    return UserListResponse(
        items=[_to_read(u) for u in rows], total=total, page=page, page_size=page_size
    )


@router.post("", response_model=InviteLinkResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate, actor: AdminUser, request: Request, db: DbSession
) -> InviteLinkResponse:
    return invite_account(db, actor, payload, request)


def invite_account(
    db: Session,
    actor: User,
    payload: UserCreate,
    request: Request,
    *,
    before_commit: Callable[[User], None] | None = None,
) -> InviteLinkResponse:
    """Create the account and its one-time invite link, and commit.

    Shared by the Team page and by an admin approving a manager's request to
    add someone, so both check, record and send exactly the same way.
    `before_commit` gets the new account while it can still join the same
    transaction - the approval records itself there.
    """
    _guard_role_assignment(actor, payload.role)
    _email_taken(db, actor.tenant_id, payload.email)
    accounts.assert_phone_free(db, actor.tenant_id, payload.phone)
    department_id = payload.department_id
    if department_id is None and payload.manager_id is not None:
        mgr = db.get(User, payload.manager_id)
        if mgr and mgr.department_id is not None:
            department_id = mgr.department_id

    _check_department(db, actor, department_id)
    _check_manager(db, actor, payload.manager_id, payload.role)
    # "hyd" under Telangana is stored as Hyderabad, as a request's places are.
    base_state, base_location = locations.canonical(
        db, actor.tenant_id, payload.base_state, payload.base_location
    )

    user = User(
        tenant_id=actor.tenant_id,
        email=payload.email,
        full_name=payload.full_name,
        role=payload.role,
        designation=payload.designation,
        gender=payload.gender,
        phone=payload.phone,
        employee_code=payload.employee_code,
        base_state=base_state,
        base_location=base_location,
        department_id=department_id,
        manager_id=payload.manager_id,
        vendor_id=payload.vendor_id,
        hotel_occupancy_entitlement=payload.hotel_occupancy_entitlement,
        password_hash=None,  # set by the invitee, never by the admin
        created_by_id=actor.id,
    )
    db.add(user)
    db.flush()

    raw, token = _issue_token(
        db, user, TokenPurpose.INVITE, valid_hours=INVITE_VALID_HOURS, created_by=actor
    )

    audit.record(
        db,
        action=AuditAction.CREATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} invited {user.full_name} ({user.email}) as {user.role}",
        changes={
            "email": {"from": None, "to": user.email},
            "full_name": {"from": None, "to": user.full_name},
            "role": {"from": None, "to": str(user.role)},
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    if before_commit is not None:
        before_commit(user)
    db.commit()

    invite_url = build_invite_url(raw)
    logger.info("Invite issued for %s", user.email)
    # The link is also returned to the inviting admin, over an authenticated
    # request, so they can pass it on themselves - instead of the email, when
    # they chose that, or when the email did not go.
    if not payload.send_email:
        return InviteLinkResponse(
            detail=f"{user.full_name} invited. Share this link with them.",
            invite_url=invite_url,
            expires_at=token.expires_at,
            email_sent=False,
            purpose=str(TokenPurpose.INVITE),
        )
    sent = email_service.send_account_link(
        user.email, user.full_name, invite_url, purpose="invite", valid_hours=INVITE_VALID_HOURS
    )
    return InviteLinkResponse(
        detail=(
            f"{user.full_name} invited. The link has been emailed to {user.email}."
            if sent.ok
            else f"{user.full_name} invited, but the email was not sent. Send them this link."
        ),
        invite_url=invite_url,
        expires_at=token.expires_at,
        email_sent=sent.ok,
        email_detail=None if sent.ok else sent.detail,
        purpose=str(TokenPurpose.INVITE),
    )


# ---------------------------------------------------------------------------
# Bulk import. Declared before "/{user_id}" so the literal paths win.
# ---------------------------------------------------------------------------

@router.get("/import/template")
def import_template(actor: AdminUser) -> Response:
    """A starter CSV with the headers and one worked example row."""
    return Response(
        content=bulk_import.template_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="team-import-template.csv"'},
    )


@router.post("/import/preview", response_model=ImportPreview)
async def import_preview(
    actor: AdminUser, db: DbSession, file: Annotated[UploadFile, File()]
) -> ImportPreview:
    """Parse and validate without writing anything.

    Creating a hundred accounts, each with an invitation, is not something an
    admin should first understand the shape of after it has happened.
    """
    raw = await file.read()
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"That file is larger than the {settings.max_upload_mb} MB limit.",
        )
    return bulk_import.parse(raw, db, actor.tenant_id)


@router.post("/import", response_model=ImportResult)
async def import_commit(
    actor: AdminUser,
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
    file: Annotated[UploadFile, File()],
) -> ImportResult:
    """Create every importable row, each with an invitation link.

    Re-parses the same file rather than trusting a preview the client echoes
    back, so what gets written is always what the server itself validated.
    Invalid rows are skipped, not fatal - one bad line should not cost the
    admin the other ninety-nine.
    """
    raw = await file.read()
    preview = bulk_import.parse(raw, db, actor.tenant_id)

    if preview.file_errors and not preview.rows:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=preview.file_errors[0],
        )

    invite_urls: dict[str, str] = {}
    names: dict[str, str] = {}
    errors: list[str] = []
    created = 0
    departments: dict[str, Department] = {}

    for row in preview.rows:
        if not row.importable or not row.email or not row.full_name or row.gender is None:
            continue

        _guard_role_assignment(actor, row.role)
        department = (
            _department_for_import(db, actor, request, row.department, departments)
            if row.department
            else None
        )

        user = User(
            tenant_id=actor.tenant_id,
            email=row.email,
            full_name=row.full_name,
            role=row.role,
            designation=row.designation,
            gender=row.gender,
            phone=row.phone,
            employee_code=row.employee_code,
            base_state=row.base_state,
            base_location=row.base_location,
            department_id=department.id if department else None,
            password_hash=None,
            created_by_id=actor.id,
        )
        db.add(user)
        db.flush()

        token_raw, _ = _issue_token(
            db, user, TokenPurpose.INVITE, valid_hours=INVITE_VALID_HOURS, created_by=actor
        )
        invite_urls[row.email] = build_invite_url(token_raw)
        names[row.email] = row.full_name
        created += 1

    if created:
        audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="user",
            summary=(
                f"{actor.full_name} bulk-imported {created} account(s) "
                f"from {file.filename or 'a CSV'}"
            ),
            changes={"imported": {"from": None, "to": sorted(invite_urls)}},
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    db.commit()

    # Sent after the response, one by one: a hundred SMTP round trips inside
    # the request would outlast the browser's patience. The links are in the
    # response regardless, so nothing is lost if a message bounces.
    problem = email_service.configuration_problem()
    if created and problem is None:
        background.add_task(_email_invites, invite_urls, names)

    return ImportResult(
        created=created,
        skipped=preview.skipped,
        invite_urls=invite_urls,
        errors=errors + preview.file_errors,
        emailing=bool(created) and problem is None,
        email_detail=problem,
    )


def _department_for_import(
    db: Session,
    actor: User,
    request: Request,
    name: str,
    cache: dict[str, Department],
) -> Department:
    """The department a row names, created the first time the file mentions it.

    Cached by lower-cased name for the request, so fifty rows saying "Field
    Operations" make one department and one audit line, not fifty.
    """
    key = name.casefold()
    if key in cache:
        return cache[key]
    found = db.execute(
        select(Department).where(Department.tenant_id == actor.tenant_id, Department.name == name)
    ).scalar_one_or_none()
    if found is None:
        found = Department(tenant_id=actor.tenant_id, name=name)
        db.add(found)
        db.flush()
        audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="department",
            entity_id=found.id,
            summary=f"{actor.full_name} added the department {found.name} (bulk import)",
            changes={"name": {"from": None, "to": found.name}},
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    cache[key] = found
    return found


def _email_invites(invite_urls: dict[str, str], names: dict[str, str]) -> None:
    for address, url in invite_urls.items():
        email_service.send_account_link(
            address, names.get(address, ""), url, purpose="invite", valid_hours=INVITE_VALID_HOURS
        )


@router.get("/{user_id}", response_model=UserRead)
def get_user(user_id: int, actor: AdminUser, db: DbSession) -> UserRead:
    return _to_read(_get_target(db, actor, user_id))


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    actor: AdminUser,
    request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> UserRead:
    user = _get_target(db, actor, user_id)
    accounts.assert_may_manage(actor, user)

    updates = payload.model_dump(exclude_unset=True)
    if updates.get("role") is not user.role:
        _guard_role_assignment(actor, updates.get("role"))

    # Nobody edits their own role - it would let an admin lock the
    # organisation out of its own admin tier. Nor their own sign-in email from
    # here: that needs their current password, on My profile, or a stolen
    # admin session could move the account somewhere its owner cannot reach.
    if user.id == actor.id:
        if "role" in updates and updates["role"] != user.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role.",
            )
        if "email" in updates and updates["email"] != user.email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Change your own sign-in email from My profile - it needs your current password.",
            )

    if "role" in updates:
        accounts.assert_keeps_a_system_admin(db, user, new_role=updates["role"])
    if "email" in updates and updates["email"] != user.email:
        _email_taken(db, actor.tenant_id, updates["email"], other_than=user.id)
    if updates.get("phone"):
        accounts.assert_phone_free(db, actor.tenant_id, updates["phone"], other_than=user.id)
    if "department_id" in updates:
        _check_department(db, actor, updates["department_id"])
    if "base_state" in updates or "base_location" in updates:
        # Checked as a pair, because a city is only canonical inside its state.
        updates["base_state"], updates["base_location"] = locations.canonical(
            db,
            actor.tenant_id,
            updates.get("base_state", user.base_state),
            updates.get("base_location", user.base_location),
        )
    new_role = updates.get("role", user.role)
    if "manager_id" in updates:
        _check_manager(db, actor, updates["manager_id"], new_role)
    elif new_role is not Role.GROUND_STAFF and user.manager_id is not None:
        # Promoted out of a team: they report to nobody now.
        updates["manager_id"] = None
    stops_managing = user.role is Role.MANAGER and new_role is not Role.MANAGER

    old_email = user.email
    before = {key: getattr(user, key) for key in updates}
    for key, value in updates.items():
        setattr(user, key, value)
    after = {key: getattr(user, key) for key in updates}
    if stops_managing:
        accounts.release_team(db, manager=user, actor=actor, request=request)
    if user.email != old_email:
        # Invite and reset links went to the old address; left live, whoever
        # holds that mailbox could still set the password. Reinvite re-sends.
        accounts.burn_outstanding_tokens(db, user)

    changes = audit.diff(before, after)
    if changes:
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=user.id,
            summary=f"{actor.full_name} updated {user.full_name} ({', '.join(sorted(changes))})",
            changes=changes,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=request,
        )
    try:
        db.commit()
    except IntegrityError:
        # The address was taken between the check and the write.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{updates.get('email', 'That email')} already has an account.",
        ) from None
    db.refresh(user)

    if user.email != old_email:
        background.add_task(
            email_service.send_email_changed_notice,
            old_email,
            user.full_name,
            user.email,
            by=actor.full_name,
        )
    return _to_read(user)


@router.post("/{user_id}/status", response_model=UserRead)
def change_status(
    user_id: int,
    payload: UserStatusChange,
    actor: AdminUser,
    request: Request,
    db: DbSession,
) -> UserRead:
    """Activate, deactivate, mark as left, or delete.

    Only Active can sign in; the change takes effect on the person's very next
    request, because every request re-reads their row. Their unused invite or
    reset links are spent, so none can be redeemed later. Their trips are left
    alone - the admin is warned about them first (GET .../open-trips).
    """
    user = _get_target(db, actor, user_id)
    if user.id == actor.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot change your own status.",
        )
    accounts.assert_may_manage(actor, user)
    accounts.assert_keeps_a_system_admin(db, user, new_status=payload.status)

    accounts.set_status(
        db,
        user=user,
        new_status=payload.status,
        actor=actor,
        exited_on=payload.exited_on,
        reason=payload.reason,
        request=request,
    )
    if user.role is Role.MANAGER and payload.status is not UserStatus.ACTIVE:
        accounts.release_team(db, manager=user, actor=actor, request=request)
    db.commit()
    db.refresh(user)
    return _to_read(user)


@router.get("/{user_id}/open-trips", response_model=OpenTrips)
def open_trips(user_id: int, actor: AdminUser, db: DbSession) -> OpenTrips:
    """Trips this person is on that are not over yet, for the status dialog."""
    return OpenTrips(**accounts.open_trips(db, _get_target(db, actor, user_id)))


@router.post("/{user_id}/reinvite", response_model=InviteLinkResponse)
def reinvite(
    user_id: int,
    actor: AdminUser,
    request: Request,
    db: DbSession,
    send_email: Annotated[bool, Query(description="Email it as well as returning it")] = True,
) -> InviteLinkResponse:
    """Issue a fresh invite link, or a password reset link once they have a
    password. Invalidates any earlier link of the same kind.

    This is also how an admin resets someone's password: they never see or set
    it, they hand over a one-time link.
    """
    user = _get_target(db, actor, user_id)
    accounts.assert_may_manage(actor, user)
    if accounts.blocked_message(user):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Set {user.full_name}'s status to Active before sending a new link.",
        )

    purpose = TokenPurpose.INVITE if not user.has_password else TokenPurpose.PASSWORD_RESET
    raw, token = _issue_token(
        db, user, purpose, valid_hours=INVITE_VALID_HOURS, created_by=actor
    )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} issued a new {purpose.lower().replace('_', ' ')} link for {user.email}",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()

    url = build_invite_url(raw)
    if not send_email:
        return InviteLinkResponse(
            detail=f"New link generated for {user.full_name}. Share it with them.",
            invite_url=url,
            expires_at=token.expires_at,
            email_sent=False,
            purpose=str(purpose),
        )
    sent = email_service.send_account_link(
        user.email,
        user.full_name,
        url,
        purpose="invite" if purpose is TokenPurpose.INVITE else "reset",
        valid_hours=INVITE_VALID_HOURS,
    )
    return InviteLinkResponse(
        detail=(
            f"New link emailed to {user.email}."
            if sent.ok
            else f"New link generated for {user.full_name}, but the email was not sent."
        ),
        invite_url=url,
        expires_at=token.expires_at,
        email_sent=sent.ok,
        email_detail=None if sent.ok else sent.detail,
        purpose=str(purpose),
    )


@router.post("/{user_id}/unlock", response_model=UserRead)
def unlock(user_id: int, actor: AdminUser, request: Request, db: DbSession) -> UserRead:
    """Clear a failed-attempt lockout without waiting it out."""
    user = _get_target(db, actor, user_id)
    accounts.assert_may_manage(actor, user)

    user.locked_until = None
    user.failed_login_count = 0
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=user.id,
        summary=f"{actor.full_name} unlocked {user.full_name}'s account",
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )
    db.commit()
    db.refresh(user)
    return _to_read(user)


@router.get("/{user_id}/travel-history")
def travel_history(
    user_id: int,
    actor: CurrentUser,
    db: DbSession,
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> dict:
    """Where this person has been, and with whom (SOW section 5).

    Built from the traveller rows rather than from requests, so a movement someone
    was *tagged onto* appears too - they were in the cab even though the request
    was not theirs.

    Anyone may read their own timeline; only an admin may read someone else's.
    Costs are included only for admins: a ground staff member reading their own
    history has no business seeing what the company paid for their seat.
    """
    target = db.get(User, user_id)
    if target is None or target.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    # Their own, an admin, or their manager - who sees where the team went,
    # never what it cost.
    if actor.id != user_id and not actor.is_admin and target.manager_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own travel history.",
        )

    result = history.build(
        db,
        user_id=user_id,
        tenant_id=actor.tenant_id,
        since=since,
        until=until,
        include_costs=actor.is_admin,
    )
    result["full_name"] = target.full_name
    result["email"] = target.email
    result["designation"] = str(target.designation) if target.designation else None
    return result


@router.get("/{user_id}/self-check", response_model=UserRead, include_in_schema=False)
def self_check(user_id: int, actor: CurrentUser, db: DbSession) -> UserRead:
    """A non-admin may read only their own row. Used by the profile screen."""
    if actor.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted.")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _to_read(user)
