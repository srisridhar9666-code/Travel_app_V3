"""
Scheduled nudges, and the rules that stop them becoming noise.

Three jobs, all of them idempotent. Every notice they write carries a
`dedupe_key` naming the *event* rather than the run, so the same trip cannot be
reminded about twice however often the scheduler fires or however many workers
are running. The database holds that guarantee, not this code.

The hard part of a reminder system is not sending; it is not sending. A team
that receives a duplicate nudge twice learns to ignore all of them, and a
reminder nobody reads is worse than none because it still costs the sender's
reputation. So each job below is narrow on purpose, and each one says in its
docstring what it deliberately does not cover.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.enums import (
    ADMIN_ROLES,
    NotificationStatus,
    RequestType,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications

logger = logging.getLogger(__name__)

#: How far ahead a traveller is reminded. Two days is enough to fix a problem
#: and near enough that the trip is real; a week out it is just another email.
TRAVEL_REMINDER_DAYS = 2

#: How long a request may sit undecided before admins are told about it. Ground
#: staff book travel days ahead, so three days of silence is a real problem
#: rather than an ordinary queue.
STALE_AFTER_DAYS = 3


def _starts_on(request: TravelRequest) -> date | None:
    if request.request_type is RequestType.HOTEL:
        return request.check_in
    return request.start_at.date() if request.start_at else None


def remind_travellers(db: Session, tenant_id: str, *, today: date | None = None) -> dict:
    """Tell people their trip is coming up.

    Only travellers who are **booked**: someone still awaiting a decision does
    not need reminding of a trip that may not happen, and someone approved but
    unticketed needs the admin chased, not themselves. Deliberately not covered:
    the return leg, which is a separate request and gets its own reminder.
    """
    today = today or clock.local_today()
    horizon = today + timedelta(days=TRAVEL_REMINDER_DAYS)

    rows = (
        db.execute(
            select(RequestTraveller, TravelRequest, User)
            .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
            .join(User, User.id == RequestTraveller.user_id)
            .where(
                TravelRequest.tenant_id == tenant_id,
                TravelRequest.is_draft.is_(False),
                TravelRequest.is_cancelled.is_(False),
                RequestTraveller.status == TravellerStatus.BOOKED,
                User.is_active.is_(True),
            )
        )
        .all()
    )

    sent = 0
    for traveller, request, person in rows:
        starts = _starts_on(request)
        if starts is None or not (today <= starts <= horizon):
            continue

        where = (
            request.hotel_city
            if request.request_type is RequestType.HOTEL
            else request.route_label(" to ")
        )
        when = (
            clock.time_label(request.start_at, "%d %b")
            if request.start_at
            else starts.strftime("%d %b")
        )
        reference = traveller.booking_reference or "see your confirmation"
        greeting = person.full_name.split()[0] if person.full_name else "there"
        # The morning of a cab, the plate and the driver's phone are what is
        # actually looked for, so they ride along when an admin recorded them.
        car = request.cab_sent_label if request.request_type is RequestType.LOCAL_CAB else None

        written = notifications.notify(
            db,
            tenant_id=tenant_id,
            user=person,
            kind="TRAVEL_REMINDER",
            title=f"Coming up: {where}",
            body=f"Your trip to {where} starts {when}. Reference {reference}."
            + (f" Cab: {car}." if car else ""),
            request_id=request.id,
            cc_users=[person.active_manager] if person.active_manager else None,
            email_subject=f"Reminder - {where} on {starts.strftime('%d %b')}",
            email_body="\n".join(
                [
                    f"Hello {greeting},",
                    "",
                    "A reminder that your trip is coming up.",
                    "",
                    f"  Trip         {where}",
                    f"  Starts       {when}",
                    f"  Reference    {reference}",
                    *([f"  Cab          {car}"] if car else []),
                    "",
                    "Carry photo ID that matches the name on the booking.",
                ]
            ),
            # The event is this person on this trip - not this run of the job.
            dedupe_key=f"travel-reminder:{traveller.id}",
        )
        if written:
            sent += 1

    db.commit()
    return {"job": "remind_travellers", "notified": sent, "considered": len(rows)}


def remind_admins_of_stale_requests(
    db: Session, tenant_id: str, *, today: date | None = None
) -> dict:
    """Tell admins about requests nobody has decided.

    One notice per stale request, to every admin - not a per-traveller nudge,
    because the admin's unit of work is the request. Deliberately not covered:
    escalation. Nobody is paged, and a request that stays stale is reported once,
    not daily, because a daily repeat of the same line is how a team learns to
    filter the sender.
    """
    today = today or clock.local_today()
    cutoff = naive_utcnow() - timedelta(days=STALE_AFTER_DAYS)

    stale = (
        db.execute(
            select(TravelRequest).where(
                TravelRequest.tenant_id == tenant_id,
                TravelRequest.is_draft.is_(False),
                TravelRequest.is_cancelled.is_(False),
                TravelRequest.submitted_at.is_not(None),
                TravelRequest.submitted_at < cutoff,
            )
        )
        .scalars()
        .unique()
        .all()
    )

    # Only those still genuinely waiting, and only those whose travel has not
    # already gone by - chasing an admin about last week is pointless.
    waiting = [
        r
        for r in stale
        if any(t.status is TravellerStatus.PENDING for t in r.travellers)
        and (_starts_on(r) is None or _starts_on(r) >= today)
    ]
    if not waiting:
        return {"job": "remind_admins_of_stale_requests", "notified": 0, "stale": 0}

    admins = (
        db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role.in_(ADMIN_ROLES),
                User.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )

    sent = 0
    for request in waiting:
        pending = sum(1 for t in request.travellers if t.status is TravellerStatus.PENDING)
        where = (
            request.hotel_city
            if request.request_type is RequestType.HOTEL
            else request.route_label(" to ")
        )
        age = (naive_utcnow() - request.submitted_at).days
        for admin in admins:
            written = notifications.notify(
                db,
                tenant_id=tenant_id,
                user=admin,
                kind="REQUEST_STALE",
                title=f"Undecided for {age} days: {where}",
                body=(
                    f"Request {request.id} ({where}) has {pending} traveller(s) "
                    f"still awaiting a decision after {age} days."
                ),
                request_id=request.id,
                email_subject=f"Request {request.id} is still undecided",
                email_body="\n".join(
                    [
                        f"Hello {admin.full_name.split()[0] if admin.full_name else 'there'},",
                        "",
                        f"Request {request.id} ({where}), raised by "
                        f"{request.requester.full_name if request.requester else 'a colleague'}, "
                        f"has {pending} traveller(s) still awaiting a decision "
                        f"after {age} days.",
                        "",
                        "Open the approvals queue to decide it.",
                    ]
                ),
                dedupe_key=f"stale-request:{request.id}",
            )
            if written:
                sent += 1

    db.commit()
    return {
        "job": "remind_admins_of_stale_requests",
        "notified": sent,
        "stale": len(waiting),
    }


def retry_undelivered(db: Session, tenant_id: str) -> dict:
    """Re-attempt email the server refused. Suppressions are left alone."""
    result = notifications.retry_failed(db, tenant_id)
    return {"job": "retry_undelivered", **result}


#: The jobs the scheduler runs, in order. Retry goes last so that anything the
#: reminder jobs failed to send this cycle is picked up on the next one rather
#: than immediately - a refusal is usually the server asking us to slow down.
JOBS = (remind_travellers, remind_admins_of_stale_requests, retry_undelivered)


def run_all(db: Session, tenant_id: str) -> list[dict]:
    """Run every job once, reporting what each did.

    One job failing must not stop the others: they are independent, and a broken
    reminder should not also stop refused email being retried.
    """
    results = []
    for job in JOBS:
        try:
            results.append(job(db, tenant_id))
        except Exception as exc:
            logger.exception("Scheduled job %s failed", job.__name__)
            db.rollback()
            results.append({"job": job.__name__, "error": f"{type(exc).__name__}: {exc}"[:200]})
    return results


def pending_counts(db: Session, tenant_id: str) -> dict:
    """What the jobs would find, without sending anything. For the admin screen."""
    return {
        "failed_email": len(
            db.execute(
                select(Notification.id).where(
                    Notification.tenant_id == tenant_id,
                    Notification.status == NotificationStatus.FAILED,
                    Notification.attempts < notifications.MAX_ATTEMPTS,
                )
            )
            .scalars()
            .all()
        ),
        "travel_reminder_days": TRAVEL_REMINDER_DAYS,
        "stale_after_days": STALE_AFTER_DAYS,
    }
