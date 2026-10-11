"""
Notices, their delivery record, and the preferences that govern them.

Split from the ticket router because this is a different concern: tickets are
about one request, these endpoints are about a person's inbox and an admin's
answer to "was this person told?".

Three audiences, three shapes:

* **Everyone** reads their own in-app notices and marks them read, and switches
  off the categories of email they do not want.
* **Admins** read the whole delivery ledger, retry refused mail and run the
  reminder jobs by hand.
* **The scheduler** runs the same jobs on a loop; the manual endpoint exists so
  the behaviour is testable and demonstrable without waiting.

Read state is in-app only. Nothing here can know whether an email was opened,
and pretending otherwise would put a number on the screen that is not true.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.enums import AuditAction, NotificationChannel, NotificationStatus
from app.models.request import Notification
from app.models.user import User
from app.schemas.ticket import (
    EmailSettings,
    EmailStatus,
    EmailTestRequest,
    EmailTestResult,
    EmailTogglePayload,
    JobResult,
    NotificationLedger,
    NotificationRow,
    PreferencesRead,
    PreferenceUpdate,
    SchedulerStatus,
)
from app.core import ratelimit
from app.services import audit, notifications, reminders, scheduler
from app.services import email as email_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _row(entry: Notification, user_name: str | None) -> NotificationRow:
    """One shaper for both the ledger and the personal list, so the two views
    cannot drift apart as columns are added."""
    return NotificationRow(
        id=entry.id,
        user_id=entry.user_id,
        user_name=user_name,
        kind=entry.kind,
        category=entry.category,
        title=entry.title,
        body=entry.body,
        channel=entry.channel,
        status=entry.status,
        to_address=entry.to_address,
        cc_addresses=entry.cc_addresses,
        subject=entry.subject,
        attempts=entry.attempts,
        sent_at=entry.sent_at,
        last_error=entry.last_error,
        request_id=entry.request_id,
        read_at=entry.read_at,
        created_at=entry.created_at,
    )


# ---------------------------------------------------------------------------
# Literal paths before /{notification_id}
# ---------------------------------------------------------------------------


@router.get("/mine", response_model=list[NotificationRow])
def my_notices(user: CurrentUser, db: DbSession) -> list[NotificationRow]:
    """This person's own in-app notices, newest first."""
    rows = (
        db.execute(
            select(Notification)
            .where(
                Notification.user_id == user.id,
                Notification.tenant_id == user.tenant_id,
                Notification.channel == NotificationChannel.IN_APP,
            )
            .order_by(Notification.id.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )
    return [_row(n, user.full_name) for n in rows]


@router.get("/unread-count")
def unread(user: CurrentUser, db: DbSession) -> dict:
    """Drives the bell. Cheap enough to poll."""
    return {"unread": notifications.unread_count(db, user)}


@router.post("/read")
def read_all(user: CurrentUser, db: DbSession) -> dict:
    """Mark every in-app notice read."""
    return {"marked": notifications.mark_read(db, user=user)}


@router.get("/preferences", response_model=PreferencesRead)
def get_preferences(user: CurrentUser, db: DbSession) -> PreferencesRead:
    return PreferencesRead(
        email=notifications.preferences_for(db, user),
        unread=notifications.unread_count(db, user),
    )


@router.patch("/preferences", response_model=PreferencesRead)
def set_preferences(
    payload: PreferenceUpdate, user: CurrentUser, http_request: Request, db: DbSession
) -> PreferencesRead:
    """Switch one category of email on or off for yourself.

    Only the caller's own preferences, and only categories that may be switched
    off - a request to silence decisions is refused rather than quietly ignored,
    because silently keeping them on would be worse than saying no.
    """
    try:
        notifications.set_preference(
            db, user=user, category=payload.category, enabled=payload.enabled
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{payload.category} notices cannot be switched off - they are how "
                "you find out what happened to your own travel."
            ),
        ) from exc

    switched = "on" if payload.enabled else "off"
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="notification_preference",
        entity_id=user.id,
        summary=f"{user.full_name} turned {payload.category} email {switched}",
        changes={"category": str(payload.category), "enabled": payload.enabled},
        tenant_id=user.tenant_id,
        actor=user,
        request=http_request,
    )
    db.commit()
    return PreferencesRead(
        email=notifications.preferences_for(db, user),
        unread=notifications.unread_count(db, user),
    )


# ---------------------------------------------------------------------------
# Admin: the delivery ledger
# ---------------------------------------------------------------------------


@router.get("/ledger", response_model=NotificationLedger)
def ledger(
    actor: AdminUser,
    db: DbSession,
    notification_status: Annotated[NotificationStatus | None, Query(alias="status")] = None,
    channel: Annotated[NotificationChannel | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> NotificationLedger:
    """Who was told what, on which channel, and whether it arrived."""
    filters = [Notification.tenant_id == actor.tenant_id]
    if notification_status is not None:
        filters.append(Notification.status == notification_status)
    if channel is not None:
        filters.append(Notification.channel == channel)
    if search:
        like = f"%{search.strip()}%"
        filters.append(
            or_(
                Notification.to_address.like(like),
                Notification.subject.like(like),
                Notification.title.like(like),
            )
        )

    total = db.execute(
        select(func.count()).select_from(Notification).where(*filters)
    ).scalar_one()

    rows = db.execute(
        select(Notification, User.full_name)
        .join(User, User.id == Notification.user_id, isouter=True)
        .where(*filters)
        .order_by(Notification.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    return NotificationLedger(
        items=[_row(n, name) for n, name in rows],
        total=total,
        page=page,
        page_size=page_size,
        summary=notifications.ledger_summary(db, actor.tenant_id),
    )


@router.post("/retry")
def retry(actor: AdminUser, http_request: Request, db: DbSession) -> dict:
    """Re-attempt refused email. Suppressed rows are left alone deliberately."""
    result = notifications.retry_failed(db, actor.tenant_id)
    sent, attempted = result["sent"], result["attempted"]
    audit.record(
        db,
        action=AuditAction.NOTIFY,
        entity_type="notification",
        summary=f"{actor.full_name} retried failed email: {sent} of {attempted} sent",
        changes=result,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    return result


# ---------------------------------------------------------------------------
# Admin: the scheduler
# ---------------------------------------------------------------------------


@router.get("/scheduler", response_model=SchedulerStatus)
def scheduler_status(actor: AdminUser, db: DbSession) -> SchedulerStatus:
    """What the background loop is doing, and what it would find."""
    settings = get_settings()
    counts = reminders.pending_counts(db, actor.tenant_id)
    return SchedulerStatus(
        enabled=settings.scheduler_enabled,
        running=scheduler.is_running(),
        interval_minutes=settings.scheduler_interval_minutes,
        travel_reminder_days=counts["travel_reminder_days"],
        stale_after_days=counts["stale_after_days"],
        failed_email=counts["failed_email"],
    )


@router.post("/run-jobs", response_model=list[JobResult])
def run_jobs(actor: AdminUser, http_request: Request, db: DbSession) -> list[JobResult]:
    """Run the reminder jobs now, without waiting for the loop.

    Safe to press twice: every notice the jobs write is deduplicated by event, so
    a second run sends nothing rather than sending everything again.
    """
    results = reminders.run_all(db, actor.tenant_id)
    audit.record(
        db,
        action=AuditAction.NOTIFY,
        entity_type="notification",
        summary=f"{actor.full_name} ran the reminder jobs by hand",
        changes={"results": results},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    return [JobResult(**r) for r in results]


# ---------------------------------------------------------------------------
# Admin: is email actually going out?
# ---------------------------------------------------------------------------


@router.get("/email/status", response_model=EmailStatus)
def email_status(actor: AdminUser) -> EmailStatus:
    """The email settings this server is running with, and what is missing.

    Cheap - no connection to the mail server - so the page can load it on open.
    Exists because "emails are not going" otherwise has to be debugged by reading
    a server log on someone else's machine.
    """
    return EmailStatus(
        problem=email_service.configuration_problem(),
        settings=EmailSettings(**email_service.effective_settings()),
    )


@router.post("/email/test", response_model=EmailTestResult)
def send_test_email(
    payload: EmailTestRequest, actor: AdminUser, http_request: Request, db: DbSession
) -> EmailTestResult:
    """Send one real message and report how far it got, in the server's words.

    To the admin's own address unless another is given. Rate limited, since it
    makes the server sign in to the mail account each time, and audited, since
    it sends mail from the company account.
    """
    # Per admin, not per address: the address comes from a header anyone can set.
    allowed, retry_after = ratelimit.EMAIL_TEST.check(f"test email:{actor.id}")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many test emails. Try again in about {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    to = str(payload.to or actor.email or "").strip()
    if not to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your account has no email address - type one to send the test to.",
        )

    result = email_service.send_test(to)
    audit.record(
        db,
        action=AuditAction.NOTIFY,
        entity_type="notification",
        summary=(
            f"{actor.full_name} sent a test email to {to}: "
            f"{'delivered to the mail server' if result['ok'] else 'failed at ' + result['stage']}"
        ),
        changes={"stage": result["stage"], "error": result["error"]},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    return EmailTestResult(**result, settings=EmailSettings(**email_service.effective_settings()))


@router.post("/email/toggle", response_model=EmailStatus)
def toggle_email(
    payload: EmailTogglePayload, actor: AdminUser, http_request: Request, db: DbSession
) -> EmailStatus:
    """Turn the email service on or off."""
    email_service.set_email_enabled(payload.enabled)
    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="email_service",
        summary=f"{actor.full_name} {'enabled' if payload.enabled else 'disabled'} email services",
        changes={"enabled": payload.enabled},
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    return EmailStatus(
        problem=email_service.configuration_problem(),
        settings=EmailSettings(**email_service.effective_settings()),
    )



# ---------------------------------------------------------------------------
# Per-notice
# ---------------------------------------------------------------------------


@router.post("/{notification_id}/read")
def read_one(notification_id: int, user: CurrentUser, db: DbSession) -> dict:
    """Mark one notice read.

    Scoped to the caller, so a stranger's id marks nothing and reports nothing.
    There is deliberately no difference here between "already read" and "not
    yours" - the second would let ids be probed.
    """
    return {
        "marked": notifications.mark_read(db, user=user, notification_id=notification_id)
    }
