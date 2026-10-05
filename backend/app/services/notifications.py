"""
The notification ledger (addendum B11, C3).

Every notice to a person is a row, and delivery is a separate recorded act
against that row. The SOW says "via email integration" and stops there; what a
fulfilment team actually needs is to be able to answer "was this person told?"
six weeks later, and the only honest way to answer that is a ledger.

Four things this module is responsible for:

* **Channels are a registry, not a branch.** `SENDERS` maps a channel to the
  function that delivers it. Adding SMS is an entry here and a value on the
  enum - that is what C3 means by channel-agnostic, and it is why there is no
  `if channel == EMAIL` anywhere below.
* **Statuses stay distinct.** `SENT`, `SUPPRESSED` (nobody tried, on purpose)
  and `FAILED` (we tried, we were refused) are three different facts. Collapsing
  them makes the ledger worthless for the one question it exists to answer.
* **Preferences are respected on the way out**, not filtered on the way in: a
  notice someone has opted out of is still written in app, because the in-app row
  *is* the record and hiding it would hide the trail from the person it is about.
* **Reminders cannot repeat.** Anything a scheduled job sends carries a
  `dedupe_key`, and the database - not this code - holds the guarantee.

Nothing here raises. A booking that could not be emailed is still a booking; the
failure belongs on the notification row, not in the caller's transaction.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.enums import (
    APPROVER_CATEGORIES,
    OPTIONAL_CATEGORIES,
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    category_of,
)
from app.models.base import naive_utcnow
from app.models.preference import NotificationPreference
from app.models.request import Notification
from app.models.user import User
from app.services import email, storage

logger = logging.getLogger(__name__)

#: Beyond this many tries an address is not going to start working, and retrying
#: forever just hammers the SMTP server.
MAX_ATTEMPTS = 3

#: A QUEUED email this old was owed an after-response send that never happened
#: - the server restarted first - so the retry job sends it instead.
STRANDED_AFTER = timedelta(minutes=5)


@dataclass(frozen=True)
class AttachedFile:
    """A stored file to send with an email - a traveller's ticket."""

    path: str
    name: str
    content_type: str | None = None


def _send_email(
    to_address: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[email.Attachment] | None = None,
) -> email.Sent:
    """Late-bound on purpose.

    Looking `email.send` up at call time rather than storing the function object
    means a test - or a future fake transport - can replace it on the module and
    have the registry honour that. A registry holding the original reference
    would silently ignore the swap, which is exactly the kind of bug that lets a
    test suite send real mail.

    `cc` and `attachments` are passed only when there is something to pass, so
    a stand-in transport written for the plain three-argument call keeps working.
    """
    extra: dict = {}
    if cc:
        extra["cc"] = cc
    if attachments:
        extra["attachments"] = attachments
    return email.send(to_address, subject, body, **extra)


#: Channel -> transport. The one place to add SMS. Each takes the address,
#: subject and body, and optionally who to copy.
SENDERS: dict[NotificationChannel, Callable[..., email.Sent]] = {
    NotificationChannel.EMAIL: _send_email,
}


def _signature() -> str:
    return "\n\n— Sriyatra, your travel desk\nThis is an automated message; replies are not monitored."


def wants(db: Session, user: User, category: NotificationCategory, channel: NotificationChannel) -> bool:
    """Whether this person still wants this kind of notice on this channel.

    Absence of a row means yes: a new joiner gets everything, and an opt-out is
    something someone actively did. Categories outside `OPTIONAL_CATEGORIES`
    cannot be switched off however the table is edited - being told your own
    travel was rejected is not a subscription.
    """
    if category not in OPTIONAL_CATEGORIES:
        return True

    row = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.category == category,
            NotificationPreference.channel == channel,
        )
    ).scalar_one_or_none()
    return True if row is None else row.enabled


def notify(
    db: Session,
    *,
    tenant_id: str,
    user: User,
    kind: str,
    title: str,
    body: str,
    request_id: int | None = None,
    email_subject: str | None = None,
    email_body: str | None = None,
    send_email: bool = True,
    dedupe_key: str | None = None,
    deliver_now: bool = True,
    cc_users: list[User] | None = None,
    attachment: AttachedFile | None = None,
    attachments: list[AttachedFile] | None = None,
) -> list[Notification]:
    """Record a notice and try to deliver it.

    The in-app row is written first and is always SENT - it exists the moment it
    is committed. The email row is written QUEUED and then attempted, so a crash
    between the two leaves evidence that delivery was owed.

    With a `dedupe_key`, a second call for the same event and person is a no-op
    and returns nothing. That is what makes the reminder jobs safe to run on a
    loop.

    `deliver_now=False` leaves the email row QUEUED for `deliver_queued` to send
    after the response, so the person who caused it is not kept waiting on the
    mail server - or on its timeout, when the mail server is unreachable.

    `cc_users` are copied on the email - a traveller's manager on a decision.
    Only active people with an address are copied, and only on a message that
    goes at all: the email is the recipient's, so their preferences decide it.
    Anything the people copied should see in the app is the caller's to write.

    `attachment` goes with the email only - a ticket file, read from storage
    when the message is sent. `attachments` is the same for several files.
    """
    category = category_of(kind)

    if dedupe_key and _already_sent(db, user_id=user.id, dedupe_key=dedupe_key):
        return []

    rows: list[Notification] = []

    in_app = Notification(
        tenant_id=tenant_id,
        user_id=user.id,
        kind=kind,
        category=category,
        title=title,
        body=body,
        request_id=request_id,
        channel=NotificationChannel.IN_APP,
        status=NotificationStatus.SENT,
        sent_at=naive_utcnow(),
        dedupe_key=dedupe_key,
    )
    db.add(in_app)
    rows.append(in_app)

    # Someone who has left or been switched off still gets the in-app row - it
    # is the record that they were told - but no more mail. (`is_active`
    # mirrors status == ACTIVE.)
    wants_email = (
        send_email
        and user.is_active
        and bool(user.email)
        and wants(db, user, category, NotificationChannel.EMAIL)
    )
    if wants_email:
        mail = Notification(
            tenant_id=tenant_id,
            user_id=user.id,
            kind=kind,
            category=category,
            title=title,
            body=email_body or body,
            request_id=request_id,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.QUEUED,
            to_address=user.email,
            cc_addresses=_cc_line(user, cc_users),
            subject=(email_subject or title)[:255],
            dedupe_key=dedupe_key,
            attachment_path=attachment.path if attachment else None,
            attachment_name=(attachment.name[:255] if attachment else None),
            attachment_type=(attachment.content_type if attachment else None),
            attachments=_stored_files(attachments),
        )
        db.add(mail)
        db.flush()
        if deliver_now:
            deliver(mail)
        rows.append(mail)

    db.flush()
    return rows


def _stored_files(files: list[AttachedFile] | None) -> list[dict] | None:
    """Files as the `attachments` column keeps them, each once."""
    kept: list[dict] = []
    seen: set[str] = set()
    for item in files or []:
        if not item.path or item.path in seen:
            continue
        seen.add(item.path)
        kept.append({"path": item.path, "name": item.name[:255], "type": item.content_type})
    return kept or None


#: The To column is 500 characters; past that the rest are mailed on their own.
_TO_MAX = 500


def notify_group(
    db: Session,
    *,
    tenant_id: str,
    people: list[User],
    kind: str,
    title: str,
    body: str,
    request_id: int | None = None,
    email_subject: str | None = None,
    email_body: str | None = None,
    cc_users: list[User] | None = None,
    attachments: list[AttachedFile] | None = None,
    send_email: bool = True,
    deliver_now: bool = True,
) -> list[Notification]:
    """One notice to several people at once: each gets their in-app row, and
    one email goes to all of them together, with `cc_users` copied.

    For things that happened to a group as one - four colleagues booked into
    the same cab. Four near-identical emails, each copying the same manager,
    is how a manager learns to ignore the travel desk; one message with
    everyone on it is what a person at a travel desk would send.

    Each person's own preferences still decide whether they are on the email.
    The row belongs to the first person on it; the others are in its To line,
    so "was this person told?" is answered by their in-app row and that line.
    """
    category = category_of(kind)
    rows: list[Notification] = []
    unique: list[User] = []
    for person in people:
        if person is not None and all(person.id != p.id for p in unique):
            unique.append(person)

    for person in unique:
        in_app = Notification(
            tenant_id=tenant_id,
            user_id=person.id,
            kind=kind,
            category=category,
            title=title,
            body=body,
            request_id=request_id,
            channel=NotificationChannel.IN_APP,
            status=NotificationStatus.SENT,
            sent_at=naive_utcnow(),
        )
        db.add(in_app)
        rows.append(in_app)

    mailable = [
        p for p in unique
        if send_email and p.is_active and p.email and "@" in p.email
        and wants(db, p, category, NotificationChannel.EMAIL)
    ]
    # Everyone that fits on one To line shares a message; anyone past it gets
    # the same message on their own rather than being dropped.
    batches: list[list[User]] = []
    for person in mailable:
        if batches and len(", ".join(p.email for p in [*batches[-1], person])) <= _TO_MAX:
            batches[-1].append(person)
        else:
            batches.append([person])

    for batch in batches:
        lead = batch[0]
        mail = Notification(
            tenant_id=tenant_id,
            user_id=lead.id,
            kind=kind,
            category=category,
            title=title,
            body=email_body or body,
            request_id=request_id,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.QUEUED,
            to_address=", ".join(p.email for p in batch),
            cc_addresses=_cc_line(lead, cc_users, also_to=batch),
            subject=(email_subject or title)[:255],
            attachments=_stored_files(attachments),
        )
        db.add(mail)
        db.flush()
        if deliver_now:
            deliver(mail)
        rows.append(mail)

    db.flush()
    return rows


#: The column is 500 characters; addresses past that are left off whole rather
#: than cut in half.
_CC_MAX = 500


def _cc_line(
    recipient: User, cc_users: list[User] | None, *, also_to: list[User] | None = None
) -> str | None:
    """The Cc list as stored on the email row: active people with an address,
    each once, never anyone the message is already addressed to."""
    seen = {
        person.email.lower()
        for person in [recipient, *(also_to or [])]
        if person is not None and person.email
    }
    kept: list[str] = []
    for person in cc_users or []:
        address = (person.email or "").strip()
        if not person.is_active or "@" not in address or address.lower() in seen:
            continue
        if len(", ".join([*kept, address])) > _CC_MAX:
            break
        seen.add(address.lower())
        kept.append(address)
    return ", ".join(kept) or None


def cc_list(notification: Notification) -> list[str]:
    """The addresses an email row copies, as a list."""
    return [a.strip() for a in (notification.cc_addresses or "").split(",") if a.strip()]


def _already_sent(db: Session, *, user_id: int, dedupe_key: str) -> bool:
    return (
        db.execute(
            select(Notification.id)
            .where(
                Notification.user_id == user_id,
                Notification.dedupe_key == dedupe_key,
            )
            .limit(1)
        ).scalar_one_or_none()
        is not None
    )


def deliver(notification: Notification) -> Notification:
    """Attempt one row on its channel and record the outcome on it.

    Mutates the row; the caller's commit persists it. Called inline today because
    a hundred users generate a handful of messages a day. When that stops being
    true the same function runs from a worker against QUEUED rows - the ledger is
    already shaped for it.
    """
    sender = SENDERS.get(notification.channel)
    if sender is None:
        return notification   # in-app, or a channel with no transport yet

    notification.attempts += 1
    body = notification.body
    files: list[email.Attachment] = []
    wanted = [
        (notification.attachment_path, notification.attachment_name, notification.attachment_type)
    ] + [
        (item.get("path"), item.get("name"), item.get("type"))
        for item in (notification.attachments or [])
        if isinstance(item, dict)
    ]
    missing = 0
    for path, name, content_type in wanted:
        if not path:
            continue
        try:
            files.append(
                email.Attachment(
                    name=name or "attachment",
                    content_type=content_type or "application/octet-stream",
                    data=storage.read(path),
                )
            )
        except Exception:   # a missing file must not stop the message itself
            logger.warning("Attachment for notification %s could not be read", notification.id)
            missing += 1
    if missing:
        body += (
            "\n\n(The file could not be attached - it is on My requests in the app.)"
            if missing == 1
            else f"\n\n({missing} files could not be attached - they are on My requests in the app.)"
        )
    args = (
        notification.to_address or "",
        notification.subject or notification.title,
        f"{body}{_signature()}",
        cc_list(notification),
    )
    result = sender(*args, attachments=files) if files else sender(*args)

    if result.ok:
        notification.status = NotificationStatus.SENT
        notification.sent_at = naive_utcnow()
        notification.last_error = None
    elif result.suppressed:
        notification.status = NotificationStatus.SUPPRESSED
        notification.last_error = (result.detail or "")[:500]
    else:
        notification.status = NotificationStatus.FAILED
        notification.last_error = (result.detail or "")[:500]

    return notification


def deliver_queued(notification_ids: list[int], session_factory=None) -> None:
    """Send email rows left QUEUED by `notify(deliver_now=False)`.

    Runs after the response, in a session of its own (the endpoint releases its
    session before returning). Rows already handled - by a retry, say - are
    skipped, and each row is committed as soon as it is sent so the retry job,
    which picks up QUEUED rows a few minutes old, never sends one twice.
    """
    if not notification_ids:
        return
    if session_factory is None:
        from app.database import SessionLocal

        session_factory = SessionLocal
    with session_factory() as db:
        rows = (
            db.execute(
                select(Notification).where(
                    Notification.id.in_(notification_ids),
                    Notification.status == NotificationStatus.QUEUED,
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            deliver(row)
            db.commit()


def retry_failed(db: Session, tenant_id: str, *, limit: int = 50) -> dict:
    """Re-attempt refused messages, and any left QUEUED by a restart.

    FAILED rows under the attempt cap, plus QUEUED rows old enough that the
    after-response send (`deliver_queued`) clearly never ran. SUPPRESSED was a
    decision, not a fault, and retrying it would defeat the guard that produced
    it.
    """
    stranded = naive_utcnow() - STRANDED_AFTER
    rows = (
        db.execute(
            select(Notification)
            .where(
                Notification.tenant_id == tenant_id,
                Notification.channel.in_(list(SENDERS)),
                or_(
                    Notification.status == NotificationStatus.FAILED,
                    and_(
                        Notification.status == NotificationStatus.QUEUED,
                        Notification.created_at < stranded,
                    ),
                ),
                Notification.attempts < MAX_ATTEMPTS,
            )
            .order_by(Notification.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    sent = 0
    for row in rows:
        deliver(row)
        if row.status is NotificationStatus.SENT:
            sent += 1

    db.commit()
    return {"attempted": len(rows), "sent": sent, "still_failing": len(rows) - sent}


def mark_read(db: Session, *, user: User, notification_id: int | None = None) -> int:
    """Mark one in-app notice read, or all of them.

    Only in-app rows: "read" is a fact about the bell in this application, and
    nothing here can know whether an email was opened.
    """
    filters = [
        Notification.user_id == user.id,
        Notification.channel == NotificationChannel.IN_APP,
        Notification.read_at.is_(None),
    ]
    if notification_id is not None:
        filters.append(Notification.id == notification_id)

    rows = db.execute(select(Notification).where(*filters)).scalars().all()
    now = naive_utcnow()
    for row in rows:
        row.read_at = now
    db.commit()
    return len(rows)


def unread_count(db: Session, user: User) -> int:
    return len(
        db.execute(
            select(Notification.id).where(
                Notification.user_id == user.id,
                Notification.channel == NotificationChannel.IN_APP,
                Notification.read_at.is_(None),
            )
        )
        .scalars()
        .all()
    )


def preferences_for(db: Session, user: User) -> dict[str, bool]:
    """This person's email preferences, one entry per switchable category.

    "New requests" is offered only to the people who receive them: admins, who
    decide requests, and managers, who recommend their team's.
    """
    rows = (
        db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user.id,
                NotificationPreference.channel == NotificationChannel.EMAIL,
            )
        )
        .scalars()
        .all()
    )
    stored = {str(r.category): r.enabled for r in rows}
    answers_requests = user.is_admin or user.is_manager
    offered = OPTIONAL_CATEGORIES if answers_requests else OPTIONAL_CATEGORIES - APPROVER_CATEGORIES
    return {str(c): stored.get(str(c), True) for c in sorted(offered, key=str)}


def set_preference(
    db: Session, *, user: User, category: NotificationCategory, enabled: bool
) -> None:
    """Turn one category of email on or off for one person.

    Upsert rather than insert: a preference is a single fact per person and
    category, and toggling it twice should not leave two rows disagreeing.
    """
    if category not in OPTIONAL_CATEGORIES:
        raise ValueError(f"{category} cannot be switched off")

    row = db.execute(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.category == category,
            NotificationPreference.channel == NotificationChannel.EMAIL,
        )
    ).scalar_one_or_none()

    if row is None:
        row = NotificationPreference(
            user_id=user.id,
            category=category,
            channel=NotificationChannel.EMAIL,
            enabled=enabled,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Two tabs, one person, same moment. The other write is as good as
            # this one; take it and apply the value on top.
            db.rollback()
            row = db.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user.id,
                    NotificationPreference.category == category,
                    NotificationPreference.channel == NotificationChannel.EMAIL,
                )
            ).scalar_one()
            row.enabled = enabled
    else:
        row.enabled = enabled


def ledger_summary(db: Session, tenant_id: str) -> dict:
    """Counts by status, for the dashboard and for answering "did it go out?"."""
    rows = db.execute(
        select(Notification.status, Notification.channel).where(
            Notification.tenant_id == tenant_id
        )
    ).all()

    summary = {str(s): 0 for s in NotificationStatus}
    emails = 0
    for status, channel in rows:
        summary[str(status)] += 1
        if channel is not NotificationChannel.IN_APP:
            emails += 1
    return {"total": len(rows), "emails": emails, "by_status": summary}
