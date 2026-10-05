"""
Carrying a decided cab or stay on for more days.

The work on site ran over: the cab is wanted tomorrow as well, or two more
nights at the hotel. Once an admin has acted the trip is locked (addendum A1),
so the traveller does not edit it - they extend it, and the extension is a
request of its own, linked to the trip it carries on (`extends_request_id`).

Why a new request rather than moving the end date:

* **The booking may differ.** Most days the same car and driver come back, but
  sometimes the vendor sends another; a hotel may have no room for the extra
  nights. A request of its own gets its own car, its own confirmation, its own
  ticket file - and the admin books it "the same as before" in one tap when
  nothing changed.
* **The money differs.** An extra day costs extra, possibly from another
  vendor, and the first days may already be on an approved invoice. Its own
  travellers carry their own cost, so nothing billed is ever reopened.
* **It is decided like anything else.** Admins approve it from the queue, the
  manager is asked to recommend it, and conflict detection sees the extra days.

The extension starts where the trip ended: a stay checks in on the old
check-out day; a cab picks up after the old one was let go. One live
extension per trip - to go further, extend the extension.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import clock
from app.core.enums import AuditAction, RequestType, TravellerStatus
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import MAX_EXTENSION_DAYS, ExtensionAsk
from app.services import audit
from app.services import requests as svc


def _refuse(code: int, detail: str) -> None:
    raise HTTPException(status_code=code, detail=detail)


def stay_starts(request: TravelRequest) -> date:
    """The night an extension of this stay begins: the old check-out day."""
    return request.check_out or (request.check_in + timedelta(days=1))


def _travellers(request: TravelRequest, asker: User, ids: list[int] | None) -> list[User]:
    """Who the extension is for: the riders named, or all of them; the asker
    always, first, because they raise it."""
    riding = [t for t in request.travellers if t.status in svc.RIDING and t.user is not None]
    if ids is None:
        chosen = riding
    else:
        by_id = {t.id: t for t in request.travellers}
        unknown = [i for i in ids if i not in by_id]
        if unknown:
            _refuse(status.HTTP_404_NOT_FOUND, "Someone named is not on this trip.")
        chosen = [by_id[i] for i in ids]
        off = [t.user.full_name for t in chosen if t.status not in svc.RIDING]
        if off:
            _refuse(
                status.HTTP_409_CONFLICT,
                f"{', '.join(off)} is not approved or booked on this trip, so cannot be "
                "carried on.",
            )
    people = [asker] + [t.user for t in chosen if t.user_id != asker.id]
    left = [p.full_name for p in people[1:] if not p.is_active]
    if left:
        _refuse(
            status.HTTP_409_CONFLICT,
            f"{', '.join(left)} has left or been switched off - leave them off the extension.",
        )
    return people


def _cab_window(request: TravelRequest, payload: ExtensionAsk) -> None:
    if payload.start_at is None:
        _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Say when the cab is wanted on the extra day.",
        )
    was_until = request.end_at or request.start_at
    if payload.start_at <= was_until:
        _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The extension has to start after the cab booked now is let go.",
        )
    last = payload.end_at or payload.start_at
    if (last.date() - was_until.date()).days > MAX_EXTENSION_DAYS:
        _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"One extension covers at most {MAX_EXTENSION_DAYS} days. "
            "For longer, raise a new request.",
        )


def _stay_nights(request: TravelRequest, payload: ExtensionAsk) -> date:
    starts = stay_starts(request)
    if payload.check_out is None:
        _refuse(status.HTTP_422_UNPROCESSABLE_CONTENT, "Pick the new check-out date.")
    if payload.check_out <= starts:
        _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The new check-out has to be after {starts.strftime('%d %b %Y')}, "
            "when the current stay ends.",
        )
    if (payload.check_out - starts).days > MAX_EXTENSION_DAYS:
        _refuse(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"One extension covers at most {MAX_EXTENSION_DAYS} nights. "
            "For longer, raise a new request.",
        )
    return starts


def extend(
    db: Session,
    *,
    request: TravelRequest,
    asker: User,
    payload: ExtensionAsk,
    http_request: Request | None = None,
) -> tuple[TravelRequest, list[int]]:
    """Raise the extension, straight into the queue. Returns it and the email
    ids queued for after the response (admins, and the managers asked to
    recommend it). The caller owns the commit."""
    refusal = svc.extension_refusal(db, request, asker)
    if refusal is not None:
        _refuse(*refusal)
    svc.resolve_project(
        db, request.project_id, request.tenant_id, other_name=request.other_project_name
    )
    people = _travellers(request, asker, payload.traveller_ids)

    child = TravelRequest(
        tenant_id=request.tenant_id,
        request_type=request.request_type,
        project_id=request.project_id,
        other_project_name=request.other_project_name,
        requester_id=asker.id,
        extends_request_id=request.id,
        travel_reason=payload.reason,
        priority=payload.priority or request.priority,
        notes=request.notes,
        is_draft=False,
    )
    if request.request_type is RequestType.LOCAL_CAB:
        _cab_window(request, payload)
        for field in (
            "mode", "origin", "destination", "origin_state", "destination_state",
            "pickup_city", "drop_city", "cab_trip", "cab_distance_km",
        ):
            setattr(child, field, getattr(request, field))
        # Ask for the size that actually went, so the vendor quotes the same car.
        child.cab_type = request.booked_cab_type or request.cab_type
        child.start_at = payload.start_at
        child.end_at = payload.end_at
        span = f"until {clock.time_label(payload.end_at or payload.start_at)}"
    else:
        child.hotel_city = request.hotel_city
        child.hotel_state = request.hotel_state
        child.check_in = _stay_nights(request, payload)
        child.check_out = payload.check_out
        nights = (child.check_out - child.check_in).days
        span = f"{nights} more night{'s' if nights != 1 else ''}, to {child.check_out:%d %b %Y}"

    child.travellers = [
        RequestTraveller(user_id=person.id, status=TravellerStatus.PENDING) for person in people
    ]
    db.add(child)
    db.flush()

    # Written against the trip being extended, so its own history says it was.
    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=(
            f"{asker.full_name} asked to extend request {request.id} ({span}) "
            f"as request {child.id}"
        ),
        changes={
            "extended_by": child.id,
            "travellers": [p.full_name for p in people],
        },
        reason=payload.reason,
        tenant_id=request.tenant_id,
        actor=asker,
        request=http_request,
    )
    queued = svc.record_submission(
        db, request=child, actor=asker, tenant_id=request.tenant_id, http_request=http_request
    )
    return child, queued
