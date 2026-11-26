"""
A manager's team, and the changes they ask an admin to make to it.

Managers see the people who report to them - details and travel, never costs -
and ask to add, edit or remove them. Nothing happens to an account until an
admin approves: the ask is held as a `TeamChange` with exactly what was asked,
and the manager is told the decision either way, with the admin's comment.
Every ask and every decision is in the activity log.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import AdminOrManager, AdminUser, DbSession, ManagerUser
from app.core.enums import (
    ADMIN_ROLES,
    AuditAction,
    NotificationChannel,
    NotificationStatus,
    Role,
    TeamChangeKind,
    TeamChangeStatus,
    UserStatus,
)
from app.models.base import naive_utcnow
from app.models.team_change import TeamChange
from app.models.user import User
from app.routers.users import _email_taken, invite_account
from app.routers.users import _to_read as _member_read
from app.schemas.team import (
    EDITABLE_FIELDS,
    TeamAddRequest,
    TeamApprove,
    TeamChangeList,
    TeamChangeRead,
    TeamDecision,
    TeamEditRequest,
    TeamMemberOccupancyUpdate,
    TeamReject,
    TeamRemoveRequest,
)
from app.schemas.user import UserCreate, UserRead
from app.services import accounts, audit, locations, notifications

router = APIRouter(prefix="/team", tags=["team"])

KIND_LABEL = {
    TeamChangeKind.ADD: "add",
    TeamChangeKind.EDIT: "change the details of",
    TeamChangeKind.REMOVE: "remove",
}


def _change_read(change: TeamChange) -> TeamChangeRead:
    target = change.target_user
    return TeamChangeRead(
        id=change.id,
        kind=change.kind,
        status=change.status,
        requested_by_id=change.requested_by_id,
        requested_by_name=change.requested_by.full_name if change.requested_by else None,
        target_user_id=change.target_user_id,
        target_name=target.full_name if target else change.payload.get("full_name"),
        payload=change.payload,
        note=change.note,
        decided_by_name=change.decided_by.full_name if change.decided_by else None,
        decided_at=change.decided_at,
        decision_comment=change.decision_comment,
        created_at=change.created_at,
    )


def _members(db: Session, manager: User) -> list[User]:
    return list(
        db.execute(
            select(User)
            .where(
                User.tenant_id == manager.tenant_id,
                User.manager_id == manager.id,
                User.status != UserStatus.DELETED,
            )
            .order_by(User.full_name)
        )
        .scalars()
        .all()
    )


def _member_or_404(db: Session, manager: User, user_id: int) -> User:
    member = db.get(User, user_id)
    if (
        member is None
        or member.tenant_id != manager.tenant_id
        or member.manager_id != manager.id
        or member.status is UserStatus.DELETED
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That person is not on your team."
        )
    return member


def _no_pending_for(db: Session, member: User) -> None:
    waiting = db.execute(
        select(func.count(TeamChange.id)).where(
            TeamChange.tenant_id == member.tenant_id,
            TeamChange.target_user_id == member.id,
            TeamChange.status == TeamChangeStatus.PENDING,
        )
    ).scalar_one()
    if waiting:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A request about {member.full_name} is already waiting for an admin. "
                "Withdraw it first, or wait for the decision."
            ),
        )


def _pending_adds(db: Session, tenant_id: str) -> list[TeamChange]:
    return list(
        db.execute(
            select(TeamChange).where(
                TeamChange.tenant_id == tenant_id,
                TeamChange.kind == TeamChangeKind.ADD,
                TeamChange.status == TeamChangeStatus.PENDING,
            )
        )
        .scalars()
        .all()
    )


def _change_or_404(db: Session, actor: User, change_id: int) -> TeamChange:
    change = db.get(TeamChange, change_id)
    if change is None or change.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    if not actor.is_admin and change.requested_by_id != actor.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return change


def _still_pending(change: TeamChange) -> None:
    if change.status is not TeamChangeStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This request was already {change.status.value.lower()}.",
        )


def _subject(change: TeamChange) -> str:
    name = change.target_user.full_name if change.target_user else change.payload.get("full_name")
    return f"{KIND_LABEL[change.kind]} {name}"


def _queued_emails(rows) -> list[int]:
    return [
        r.id
        for r in rows
        if r.channel == NotificationChannel.EMAIL and r.status == NotificationStatus.QUEUED
    ]


def _tell_admins(db: Session, change: TeamChange, manager: User) -> list[int]:
    admins = db.execute(
        select(User).where(
            User.tenant_id == manager.tenant_id,
            User.role.in_(ADMIN_ROLES),
            User.is_active.is_(True),
        )
    ).scalars().all()
    link = f"{get_settings().frontend_base_url.rstrip('/')}/team"
    what = f"{manager.full_name} asked to {_subject(change)}"
    queued: list[int] = []
    for admin in admins:
        lines = [
            f"Hello {admin.full_name.split()[0] if admin.full_name else 'there'},",
            "",
            f"{what}. It is waiting for an admin to approve or reject.",
        ]
        if change.note:
            lines.append(f"Their note: {change.note}")
        lines += ["", f"Review it on Team: {link}"]
        queued += _queued_emails(
            notifications.notify(
                db,
                tenant_id=manager.tenant_id,
                user=admin,
                kind="TEAM_CHANGE_REQUESTED",
                title=f"Team change from {manager.full_name}",
                body=f"{what}." + (f" Note: {change.note}" if change.note else ""),
                email_subject=f"Team change to approve: {what}"[:255],
                email_body="\n".join(lines),
                deliver_now=False,
            )
        )
    return queued


def _tell_manager(db: Session, change: TeamChange, admin: User) -> list[int]:
    manager = change.requested_by
    if manager is None:
        return []
    approved = change.status is TeamChangeStatus.APPROVED
    verdict = "approved" if approved else "rejected"
    what = f"Your request to {_subject(change)} was {verdict} by {admin.full_name}"
    comment = f" Comment: {change.decision_comment}" if change.decision_comment else ""
    link = f"{get_settings().frontend_base_url.rstrip('/')}/my-team"
    return _queued_emails(
        notifications.notify(
            db,
            tenant_id=manager.tenant_id,
            user=manager,
            kind="TEAM_CHANGE_APPROVED" if approved else "TEAM_CHANGE_REJECTED",
            title=f"Team change {verdict}",
            body=f"{what}.{comment}",
            email_subject=f"Team change {verdict}: {_subject(change)}"[:255],
            email_body=f"Hello {manager.full_name.split()[0]},\n\n{what}.{comment}\n\nSee your team: {link}",
            deliver_now=False,
        )
    )


def _record(
    db: Session,
    change: TeamChange,
    actor: User,
    action: AuditAction,
    summary: str,
    request: Request,
    reason: str | None = None,
) -> None:
    audit.record(
        db,
        action=action,
        entity_type="team_change",
        entity_id=change.id,
        summary=summary,
        changes={"kind": str(change.kind), "status": str(change.status), "asked": change.payload},
        reason=reason,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=request,
    )


def _open(
    db: Session,
    manager: User,
    kind: TeamChangeKind,
    payload: dict,
    note: str | None,
    request: Request,
    background: BackgroundTasks,
    target: User | None = None,
) -> TeamChangeRead:
    change = TeamChange(
        tenant_id=manager.tenant_id,
        kind=kind,
        requested_by_id=manager.id,
        target_user_id=target.id if target else None,
        payload=audit.jsonable(payload),
        note=note,
    )
    db.add(change)
    db.flush()
    db.refresh(change)
    _record(
        db,
        change,
        manager,
        AuditAction.SUBMIT,
        f"{manager.full_name} asked to {_subject(change)}",
        request,
        reason=note,
    )
    queued = _tell_admins(db, change, manager)
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(change)
    return _change_read(change)


# ---------------------------------------------------------------------------
# The manager's side
# ---------------------------------------------------------------------------


@router.get("/members", response_model=list[UserRead])
def members(manager: ManagerUser, db: DbSession) -> list[UserRead]:
    """Everyone who reports to the caller, deleted accounts aside."""
    return [_member_read(m) for m in _members(db, manager)]


@router.put("/members/{user_id}/occupancy", response_model=UserRead)
def update_member_occupancy(
    user_id: int,
    payload: TeamMemberOccupancyUpdate,
    manager: ManagerUser,
    request: Request,
    db: DbSession,
) -> UserRead:
    """Manager evaluates and updates team member's hotel room occupancy entitlement (Single vs Double).

    This profile entitlement governs all hotel bookings for this employee (SOW Section 1.1 item 2).
    """
    member = _member_or_404(db, manager, user_id)
    old_val = str(member.hotel_occupancy_entitlement)
    new_val = str(payload.hotel_occupancy_entitlement)

    member.hotel_occupancy_entitlement = payload.hotel_occupancy_entitlement

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="user",
        entity_id=member.id,
        summary=f"Manager {manager.full_name} set hotel occupancy entitlement of {member.full_name} to {new_val}",
        changes={"hotel_occupancy_entitlement": {"from": old_val, "to": new_val}},
        tenant_id=manager.tenant_id,
        actor=manager,
        request=request,
    )
    db.commit()
    db.refresh(member)
    return _member_read(member)


@router.post("/changes/add", response_model=TeamChangeRead, status_code=status.HTTP_201_CREATED)
def ask_to_add(
    payload: TeamAddRequest,
    manager: ManagerUser,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> TeamChangeRead:
    """Ask for someone new on the team. On approval they are invited as ground
    staff in the manager's department, reporting to the manager."""
    _email_taken(db, manager.tenant_id, payload.email)
    accounts.assert_phone_free(db, manager.tenant_id, payload.phone)
    if any(c.payload.get("email") == payload.email for c in _pending_adds(db, manager.tenant_id)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.email} is already waiting for an admin to approve.",
        )
    base_state, base_location = locations.canonical(
        db, manager.tenant_id, payload.base_state, payload.base_location
    )
    asked = payload.model_dump(exclude={"note"}, mode="json")
    asked.update(base_state=base_state, base_location=base_location)
    return _open(db, manager, TeamChangeKind.ADD, asked, payload.note, request, background)


@router.post("/changes/edit/{user_id}", response_model=TeamChangeRead, status_code=status.HTTP_201_CREATED)
def ask_to_edit(
    user_id: int,
    payload: TeamEditRequest,
    manager: ManagerUser,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> TeamChangeRead:
    """Ask to change a member's details. Only fields that differ are kept, each
    with its current value beside it so the admin sees exactly what moves."""
    member = _member_or_404(db, manager, user_id)
    _no_pending_for(db, member)

    sent = payload.model_dump(exclude_unset=True, exclude={"note"}, mode="json")
    if "base_state" in sent or "base_location" in sent:
        sent["base_state"], sent["base_location"] = locations.canonical(
            db,
            manager.tenant_id,
            sent.get("base_state", member.base_state),
            sent.get("base_location", member.base_location),
        )
    current = audit.jsonable({field: getattr(member, field) for field in EDITABLE_FIELDS})
    asked = {
        field: {"from": current[field], "to": value}
        for field, value in sent.items()
        if field in EDITABLE_FIELDS and value != current[field]
    }
    if "phone" in asked:
        accounts.assert_phone_free(
            db, manager.tenant_id, asked["phone"]["to"], other_than=member.id
        )
    if not asked:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Nothing would change - those are already their details.",
        )
    return _open(
        db, manager, TeamChangeKind.EDIT, asked, payload.note, request, background, target=member
    )


@router.post("/changes/remove/{user_id}", response_model=TeamChangeRead, status_code=status.HTTP_201_CREATED)
def ask_to_remove(
    user_id: int,
    payload: TeamRemoveRequest,
    manager: ManagerUser,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> TeamChangeRead:
    """Ask for a member to be marked as left, or switched off."""
    member = _member_or_404(db, manager, user_id)
    _no_pending_for(db, member)
    if member.status is payload.status:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{member.full_name} is already {payload.status.value.lower()}.",
        )
    asked = payload.model_dump(exclude={"note"}, mode="json")
    return _open(
        db, manager, TeamChangeKind.REMOVE, asked, payload.note, request, background, target=member
    )


@router.post("/changes/{change_id}/cancel", response_model=TeamChangeRead)
def withdraw(change_id: int, manager: ManagerUser, request: Request, db: DbSession) -> TeamChangeRead:
    """Withdraw an ask before an admin has decided it."""
    change = _change_or_404(db, manager, change_id)
    if change.requested_by_id != manager.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    _still_pending(change)
    change.status = TeamChangeStatus.CANCELLED
    change.decided_at = naive_utcnow()
    _record(
        db, change, manager, AuditAction.CANCEL,
        f"{manager.full_name} withdrew their request to {_subject(change)}", request,
    )
    db.commit()
    db.refresh(change)
    return _change_read(change)


# ---------------------------------------------------------------------------
# Both sides
# ---------------------------------------------------------------------------


@router.get("/changes", response_model=TeamChangeList)
def list_changes(
    actor: AdminOrManager,
    db: DbSession,
    change_status: Annotated[TeamChangeStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TeamChangeList:
    """A manager's own asks, or every ask for an admin - newest first. `pending`
    is the number still waiting, whatever the filter."""
    scope = [TeamChange.tenant_id == actor.tenant_id]
    if not actor.is_admin:
        scope.append(TeamChange.requested_by_id == actor.id)
    filters = list(scope)
    if change_status is not None:
        filters.append(TeamChange.status == change_status)

    rows = db.execute(
        select(TeamChange).where(*filters).order_by(TeamChange.id.desc()).limit(limit)
    ).scalars().all()
    pending = db.execute(
        select(func.count(TeamChange.id)).where(
            *scope, TeamChange.status == TeamChangeStatus.PENDING
        )
    ).scalar_one()
    return TeamChangeList(items=[_change_read(c) for c in rows], pending=pending)


# ---------------------------------------------------------------------------
# The admin's side
# ---------------------------------------------------------------------------


def _decided(change: TeamChange, admin: User, outcome: TeamChangeStatus, comment: str | None) -> None:
    change.status = outcome
    change.decided_by_id = admin.id
    change.decided_at = naive_utcnow()
    change.decision_comment = comment


def _manager_still_leads(change: TeamChange) -> User:
    manager = change.requested_by
    if manager is None or manager.role is not Role.MANAGER or manager.status is not UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Whoever asked is no longer an active manager. Reject it, and make the change on Team instead.",
        )
    return manager


def _target_still_on_team(change: TeamChange, manager: User) -> User:
    target = change.target_user
    if target is None or target.manager_id != manager.id or target.status is UserStatus.DELETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That person no longer reports to this manager. Reject the request instead.",
        )
    return target


@router.post("/changes/{change_id}/approve", response_model=TeamDecision)
def approve(
    change_id: int,
    payload: TeamApprove,
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> TeamDecision:
    """Make the change the manager asked for, exactly as asked, and tell them."""
    change = _change_or_404(db, admin, change_id)
    _still_pending(change)
    manager = _manager_still_leads(change)
    summary = f"{admin.full_name} approved {manager.full_name}'s request to {_subject(change)}"
    queued: list[int] = []

    def settle(new_member: User | None = None) -> None:
        nonlocal queued
        if new_member is not None:
            change.target_user_id = new_member.id
        _decided(change, admin, TeamChangeStatus.APPROVED, payload.comment)
        _record(db, change, admin, AuditAction.APPROVE, summary, request, reason=payload.comment)
        queued = _tell_manager(db, change, admin)

    invite = None
    if change.kind is TeamChangeKind.ADD:
        asked = change.payload
        invite = invite_account(
            db,
            admin,
            UserCreate(
                email=asked["email"],
                full_name=asked["full_name"],
                role=Role.GROUND_STAFF,
                designation=asked.get("designation"),
                gender=asked["gender"],
                phone=asked.get("phone"),
                employee_code=asked.get("employee_code"),
                base_state=asked.get("base_state"),
                base_location=asked.get("base_location"),
                department_id=manager.department_id,
                manager_id=manager.id,
                send_email=payload.send_email,
            ),
            request,
            before_commit=settle,
        )
    else:
        target = _target_still_on_team(change, manager)
        if change.kind is TeamChangeKind.EDIT:
            before = {f: getattr(target, f) for f in change.payload}
            if "phone" in change.payload:
                # Someone else may have taken the number while this waited.
                accounts.assert_phone_free(
                    db, admin.tenant_id, change.payload["phone"]["to"], other_than=target.id
                )
            for field, values in change.payload.items():
                setattr(target, field, values["to"])
            audit.record(
                db,
                action=AuditAction.UPDATE,
                entity_type="user",
                entity_id=target.id,
                summary=f"{admin.full_name} updated {target.full_name}, as {manager.full_name} asked",
                changes=audit.diff(before, {f: getattr(target, f) for f in change.payload}),
                tenant_id=admin.tenant_id,
                actor=admin,
                request=request,
            )
        else:
            asked = change.payload
            accounts.set_status(
                db,
                user=target,
                new_status=UserStatus(asked["status"]),
                actor=admin,
                exited_on=asked.get("exited_on"),
                reason=asked.get("reason"),
                request=request,
            )
        settle()
        db.commit()

    background.add_task(notifications.deliver_queued, queued)
    db.refresh(change)
    return TeamDecision(change=_change_read(change), invite=invite)


@router.post("/changes/{change_id}/reject", response_model=TeamDecision)
def reject(
    change_id: int,
    payload: TeamReject,
    admin: AdminUser,
    request: Request,
    background: BackgroundTasks,
    db: DbSession,
) -> TeamDecision:
    """Turn the ask down, with the reason the manager will read."""
    change = _change_or_404(db, admin, change_id)
    _still_pending(change)
    _decided(change, admin, TeamChangeStatus.REJECTED, payload.comment)
    who = change.requested_by.full_name if change.requested_by else "A manager"
    _record(
        db, change, admin, AuditAction.REJECT,
        f"{admin.full_name} rejected {who}'s request to {_subject(change)}",
        request, reason=payload.comment,
    )
    queued = _tell_manager(db, change, admin)
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(change)
    return TeamDecision(change=_change_read(change))
