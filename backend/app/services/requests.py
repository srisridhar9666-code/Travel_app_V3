"""
Building, editing and describing requests.

The router stays thin because two things here are easy to get wrong and must
happen identically on every path:

* **the edit window** - a request is editable only while every traveller is still
  PENDING (addendum A1), and the check has to run before anything is mutated, not
  after; and
* **the revision trail** - every accepted amendment writes one append-only row
  carrying a field-level diff, so an admin can see what changed between the
  version they read and the version in front of them.

Revisions start at submission. Revision 1 is the request as first submitted and
carries no diff; each later number is an amendment. Edits made while a request is
still a private draft do not write revisions - there is no admin to disclose them
to yet, and draft churn would bury the amendments that matter.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.enums import (
    ACTIVE_TRAVELLER_STATUSES,
    ADMIN_ROLES,
    AuditAction,
    CancellationStatus,
    NotificationChannel,
    NotificationStatus,
    RequestPriority,
    RequestStatus,
    RequestType,
    RoomSharingChoice,
    TicketStatus,
    TravellerStatus,
    derive_request_status,
    is_editable,
)
from app.models.base import naive_utcnow
from app.models.invoice import Invoice, InvoiceLine
from app.models.project import Project
from app.models.request import RequestRevision, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.schemas.request import (
    EDITABLE_FIELDS,
    ConflictRead,
    CoStayMatchRead,
    RequestBody,
    RequestRead,
    RevisionRead,
    TicketFileRead,
    TravellerRead,
)
from app.services import audit, conflicts, costay, locations, notifications
from app.services.seed import OTHER_PROJECT_CODE

#: The most rows one queue export returns. Far above a real tab today; there so
#: a runaway export cannot hold a worker for minutes.
MAX_EXPORT_ROWS = 5000

# Human labels for the diff, so a revision reads as English rather than as column
# names. Anything not listed falls back to the column name itself.
FIELD_LABELS = {
    "project_id": "campaign",
    "other_project_name": "campaign name",
    "travel_reason": "reason for travel",
    "priority": "priority",
    "mode": "mode",
    "origin": "origin",
    "origin_state": "origin state",
    "destination_state": "destination state",
    "hotel_state": "hotel state",
    "destination": "destination",
    "pickup_city": "pickup city",
    "drop_city": "drop city",
    "start_at": "departure",
    "end_at": "arrival",
    "hotel_city": "city",
    "check_in": "check-in",
    "check_out": "check-out",
    "cab_type": "cab type",
    "cab_trip": "local or outstation",
    "cab_distance_km": "distance (km)",
    "notes": "notes",
    "travellers": "travellers",
}


def snapshot(request: TravelRequest) -> dict[str, Any]:
    """The comparable state of a request, for the revision diff.

    Travellers are captured as a sorted list of ids so that adding or dropping
    someone shows up as a change like any other field.
    """
    state: dict[str, Any] = {field: getattr(request, field) for field in EDITABLE_FIELDS}
    state["travellers"] = sorted(t.user_id for t in request.travellers)
    return state


def next_revision_number(db: Session, request_id: int) -> int:
    highest = db.execute(
        select(func.max(RequestRevision.revision_number)).where(
            RequestRevision.request_id == request_id
        )
    ).scalar()
    return (highest or 0) + 1


def write_revision(
    db: Session,
    *,
    request: TravelRequest,
    editor: User,
    summary: str,
    changes: dict | None,
) -> RequestRevision:
    """Append one revision row. Nothing here ever updates an existing one."""
    revision = RequestRevision(
        request_id=request.id,
        revision_number=next_revision_number(db, request.id),
        editor_id=editor.id,
        summary=summary[:300],
        # Same hazard as the audit ledger: a raw date or enum in here raises at
        # INSERT and takes the edit down with it.
        changes=audit.jsonable(changes) if changes else None,
    )
    db.add(revision)
    db.flush()
    return revision


def edit_count(db: Session, request_id: int) -> int:
    """Amendments since submission. Revision 1 is the submission itself."""
    highest = db.execute(
        select(func.max(RequestRevision.revision_number)).where(
            RequestRevision.request_id == request_id
        )
    ).scalar()
    return max((highest or 0) - 1, 0)


def request_is_editable(request: TravelRequest) -> bool:
    return is_editable(
        request.traveller_statuses,
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
    )


def travel_date_passed(request: TravelRequest, *, today: date | None = None) -> bool:
    """Whether the trip itself is in the past.

    Only meaningful alongside the traveller statuses: a request nobody decided
    before the date went by is EXPIRED, one that was decided is simply history.
    """
    ends = request.travel_ends_on
    return ends is not None and ends < (today or clock.local_today())


def status_of(request: TravelRequest) -> RequestStatus:
    """The one place a request-level status is produced."""
    return derive_request_status(
        request.traveller_statuses,
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
        travel_date_passed=travel_date_passed(request),
    )


def assert_editable(request: TravelRequest) -> None:
    """Refuse an amendment once an admin has acted.

    409 rather than 403: the caller has every right to edit this request, the
    request has simply moved past the point where editing means anything.
    """
    if request_is_editable(request):
        return
    if request.is_cancelled:
        detail = "This request has been cancelled and can no longer be edited."
    else:
        detail = (
            "An admin has already acted on this request, so it is locked. "
            "Cancel it and raise a new one if the plan has changed."
        )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


#: Travellers a booked cab is actually carrying: approved, or booked. Only
#: they are told about the car, and only while someone is can it be extended.
RIDING = frozenset({TravellerStatus.APPROVED, TravellerStatus.BOOKED})


#: What can be carried on for more days: a cab kept longer, a stay made longer.
#: A flight or train is a fixed journey - more travel is a new request.
EXTENDABLE = frozenset({RequestType.LOCAL_CAB, RequestType.HOTEL})

#: An extension in one of these is over - turned down, withdrawn or let lapse -
#: so the trip it would have carried on may be extended again.
_DEAD = frozenset({RequestStatus.REJECTED, RequestStatus.CANCELLED, RequestStatus.EXPIRED})


def live_extension(db: Session, request: TravelRequest) -> TravelRequest | None:
    """The extension carrying this trip on: the newest one still alive."""
    if request.id is None or request.request_type not in EXTENDABLE:
        return None
    rows = db.execute(
        select(TravelRequest)
        .where(
            TravelRequest.extends_request_id == request.id,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
        )
        .order_by(TravelRequest.id.desc())
    ).scalars()
    return next((row for row in rows if status_of(row) not in _DEAD), None)


def extension_deadline(request: TravelRequest) -> date | None:
    """The last day a trip can still be extended: the day the ride or stay
    ends. Until midnight that day (India time) it carries on; after that the
    extra days are a new request. A cab ends on the day it is let go (or, with
    no end time, the day it picks up); a stay on its check-out day."""
    if request.request_type is RequestType.HOTEL:
        if request.check_in is None:
            return None
        return request.check_out or (request.check_in + timedelta(days=1))
    last = request.end_at or request.start_at
    return last.date() if last else None


def extension_refusal(
    db: Session,
    request: TravelRequest,
    user: User | None,
    *,
    live: TravelRequest | None | bool = False,
) -> tuple[int, str] | None:
    """Why this person may not extend this trip, or None if they may.

    The one statement of the rule: POST /extend raises what it returns, and the
    read model's `can_extend` is "this returned None", so the button is only
    offered when the ask would be accepted. `live` is the trip's live
    extension when the caller has already looked it up.
    """
    if request.request_type not in EXTENDABLE:
        return (
            status.HTTP_400_BAD_REQUEST,
            "Only a cab or a hotel stay can be extended. For more travel, raise a new request.",
        )
    mine = next(
        (t for t in request.travellers if user is not None and t.user_id == user.id), None
    )
    if mine is None:
        return status.HTTP_403_FORBIDDEN, "Only someone on this trip can extend it."
    if request.is_draft or request.is_cancelled:
        return status.HTTP_409_CONFLICT, "This request is not live, so it cannot be extended."
    if request_is_editable(request):
        # Until an admin acts, the requester can simply change the dates.
        return (
            status.HTTP_409_CONFLICT,
            "Nobody has decided this yet - change its dates instead of extending it.",
        )
    if mine.status not in RIDING:
        return (
            status.HTTP_409_CONFLICT,
            "You are not approved or booked on this trip, so it cannot be extended for you.",
        )
    if request.request_type is RequestType.LOCAL_CAB and request.start_at is None:
        return status.HTTP_409_CONFLICT, "This cab has no pickup time to carry on from."
    if request.request_type is RequestType.HOTEL and request.check_in is None:
        return status.HTTP_409_CONFLICT, "This stay has no check-in date to carry on from."
    deadline = extension_deadline(request)
    if deadline is not None and clock.local_today() > deadline:
        return (
            status.HTTP_409_CONFLICT,
            f"This trip ended on {deadline:%d %b %Y}, and a trip can only be extended until "
            "midnight on its last day. Raise a new request for the extra days.",
        )
    child = live_extension(db, request) if live is False else live
    if child:
        return (
            status.HTTP_409_CONFLICT,
            f"This trip is already extended by request {child.id}. "
            "Extend that one to go further.",
        )
    return None


def booking_label(request: TravelRequest | None) -> str | None:
    """How a trip was booked, in one line: the car and driver for a cab, the
    hotel and its confirmation for a stay. None when nothing is booked yet."""
    if request is None:
        return None
    if request.request_type is RequestType.LOCAL_CAB:
        return request.cab_sent_label
    for traveller in request.travellers:
        if traveller.status is not TravellerStatus.BOOKED:
            continue
        details = traveller.booking_details or {}
        place = ", ".join(
            part for part in (details.get("hotel_name"), details.get("hotel_address")) if part
        )
        parts = [part for part in (place, traveller.booking_reference) if part]
        if parts:
            return " · confirmation ".join(parts) if place else f"Confirmation {parts[0]}"
    return None


def resolve_project(
    db: Session, project_id: int, tenant_id: str, *, other_name: str | None = None
) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    if not project.accepts_requests:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campaign {project.code} is not accepting new requests.",
        )

    # Picking "Other" without saying what it is would leave the request in the
    # fallback campaign with nothing to triage it by - worse than not offering
    # the option at all.
    if project.code == OTHER_PROJECT_CODE and not (other_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Name the campaign this trip is for.",
        )

    return project


def resolve_travellers(
    db: Session, *, requester: User, traveller_ids: list[int], tenant_id: str
) -> list[User]:
    """The full set of people on this request.

    `traveller_ids` names the colleagues being tagged on; the requester is always
    one of the travellers on their own request. Raising a request on someone
    else's behalf is not a Phase 3 flow.
    """
    wanted = {uid for uid in traveller_ids if uid != requester.id}
    people = [requester]

    if wanted:
        found = (
            db.execute(select(User).where(User.id.in_(wanted), User.tenant_id == tenant_id))
            .scalars()
            .all()
        )
        missing = wanted - {u.id for u in found}
        if missing:
            names = ", ".join(str(m) for m in sorted(missing))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown colleague id(s): {names}.",
            )
        # is_active follows status (deactivated, left or deleted all clear it),
        # so this one check covers every way a colleague stops being taggable.
        inactive = [u.full_name for u in found if not u.is_active]
        if inactive:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"{', '.join(inactive)} has left or been deactivated - "
                    "remove them from this request."
                ),
            )
        people.extend(sorted(found, key=lambda u: u.full_name))

    return people


def canonicalise_places(db: Session, tenant_id: str, body: RequestBody) -> None:
    """Settle every picked or typed place on the body to the stored spelling.

    The form lets someone type a place under "Other" when the list does not
    have it. Conflict detection and co-stay matching compare places exactly, so
    a typed "hyd" has to become the listed "Hyderabad" before anything is saved
    or checked. A cab's pickup and drop are street addresses, not places on the
    list, so those are only tidied - but the city each is in is canonicalised
    like any other.
    """
    if body.request_type is RequestType.LOCAL_CAB:
        body.origin = locations.tidy(body.origin)
        body.destination = locations.tidy(body.destination)
        pairs = (("origin_state", "pickup_city"), ("destination_state", "drop_city"))
    else:
        pairs = (
            ("origin_state", "origin"),
            ("destination_state", "destination"),
            ("hotel_state", "hotel_city"),
        )

    for state_field, place_field in pairs:
        state, place = locations.canonical(
            db, tenant_id, getattr(body, state_field), getattr(body, place_field)
        )
        setattr(body, state_field, state)
        setattr(body, place_field, place)


def apply_body(request: TravelRequest, body: RequestBody) -> None:
    """Copy the validated body onto the row.

    Fields the body has already nulled out for this type are copied as nulls,
    which is how a request that changes shape stops carrying its old route.
    """
    request.request_type = body.request_type
    for field in EDITABLE_FIELDS:
        setattr(request, field, getattr(body, field))


def sync_travellers(
    db: Session, *, request: TravelRequest, people: list[User]
) -> tuple[list[str], list[str]]:
    """Reconcile the traveller rows to `people`, returning who was added and
    dropped. Only ever reached while the request is still editable, so this
    cannot destroy a row an admin has already decided."""
    wanted = {u.id: u for u in people}
    existing = {t.user_id: t for t in request.travellers}

    added = [u.full_name for uid, u in wanted.items() if uid not in existing]
    dropped = [
        t.user.full_name if t.user else str(uid)
        for uid, t in existing.items()
        if uid not in wanted
    ]

    for uid, traveller in list(existing.items()):
        if uid not in wanted:
            request.travellers.remove(traveller)

    for uid in wanted:
        if uid not in existing:
            request.travellers.append(
                RequestTraveller(user_id=uid, status=TravellerStatus.PENDING)
            )

    db.flush()
    return added, dropped


def check_conflicts(db: Session, *, tenant_id: str, request: TravelRequest) -> list[ConflictRead]:
    """Warnings for every person on this request. Never blocks - addendum B6."""
    found = conflicts.detect(
        db,
        tenant_id=tenant_id,
        candidate=conflicts.Itinerary.from_request(request),
        user_ids=[t.user_id for t in request.travellers],
        exclude_request_id=request.id,
    )
    return [ConflictRead(**vars(c)) for c in found]


def check_costay(
    db: Session, *, tenant_id: str, request: TravelRequest, for_user: User
) -> list[CoStayMatchRead]:
    """Colleagues the requester could share a room with, already gender-filtered."""
    if request.request_type is not RequestType.HOTEL or request.check_in is None:
        return []
    matches = costay.find_matches(
        db,
        tenant_id=tenant_id,
        for_user=for_user,
        city=request.hotel_city or "",
        check_in=request.check_in,
        check_out=request.check_out,
        exclude_request_id=request.id,
    )
    return [CoStayMatchRead(**vars(m)) for m in matches]


def room_matches_for(db: Session, *, tenant_id: str, request: TravelRequest) -> dict[int, list[CoStayMatchRead]]:
    """For an admin, per traveller on a live hotel stay: the same-gender
    colleagues in that city on overlapping nights they could share a room with.

    Travellers already in a confirmed shared room, or decided out of the trip,
    are left out - there is nothing to allot for them.
    """
    if (
        request.request_type is not RequestType.HOTEL
        or request.is_draft
        or request.is_cancelled
        or request.check_in is None
    ):
        return {}
    out: dict[int, list[CoStayMatchRead]] = {}
    for traveller in request.travellers:
        if traveller.user is None or traveller.status not in ACTIVE_TRAVELLER_STATUSES:
            continue
        if traveller.room_sharing is RoomSharingChoice.SHARE_EXISTING and traveller.share_confirmed_at:
            continue
        matches = costay.find_matches(
            db,
            tenant_id=tenant_id,
            for_user=traveller.user,
            city=request.hotel_city or "",
            check_in=request.check_in,
            check_out=request.check_out,
            exclude_request_id=request.id,
        )
        if matches:
            out[traveller.id] = [CoStayMatchRead(**vars(m)) for m in matches]
    return out


def confirmed_ticket_travellers(db: Session, request_id: int) -> set[int]:
    """Travellers on this request with a confirmed ticket they can download."""
    return set(
        db.execute(
            select(TicketDocument.traveller_id).where(
                TicketDocument.request_id == request_id,
                TicketDocument.status == TicketStatus.CONFIRMED,
                TicketDocument.file_path.is_not(None),
            )
        ).scalars()
    )


def ticket_per_traveller(db: Session, request_id: int) -> dict[int, int]:
    """The ticket document to show beside each traveller on a request.

    The confirmed one when there is one - that is what was booked - else the
    newest still being reviewed. A discarded ticket has no file left to show.
    """
    rows = db.execute(
        select(TicketDocument.id, TicketDocument.traveller_id, TicketDocument.status)
        .where(
            TicketDocument.request_id == request_id,
            TicketDocument.status != TicketStatus.DISCARDED,
            TicketDocument.file_path.is_not(None),
        )
        .order_by(TicketDocument.id.desc())
    ).all()
    chosen: dict[int, tuple[bool, int]] = {}
    for ticket_id, traveller_id, ticket_status in rows:
        confirmed = ticket_status is TicketStatus.CONFIRMED
        held = chosen.get(traveller_id)
        if held is None or (confirmed and not held[0]):
            chosen[traveller_id] = (confirmed, ticket_id)
    return {traveller_id: ticket_id for traveller_id, (_, ticket_id) in chosen.items()}


def ticket_files(
    db: Session, request: TravelRequest, reader: User | None
) -> dict[int, list[TicketFileRead]]:
    """The files on each traveller's booking that this reader may open.

    An admin sees every file not thrown away, booked ones first. Anyone else
    sees only booked files, and only their own or - for whoever raised the
    trip - their group's: the same people the download endpoint serves.
    """
    if reader is None or not request.travellers:
        return {}
    admin = reader.is_admin
    allowed = {
        t.id for t in request.travellers
        if admin or reader.id in (t.user_id, request.requester_id)
    }
    if not allowed:
        return {}
    filters = [
        TicketDocument.request_id == request.id,
        TicketDocument.traveller_id.in_(allowed),
        TicketDocument.file_path.is_not(None),
        TicketDocument.status != TicketStatus.DISCARDED,
    ]
    if not admin:
        filters.append(TicketDocument.status == TicketStatus.CONFIRMED)
    rows = db.execute(
        select(
            TicketDocument.id, TicketDocument.traveller_id, TicketDocument.file_name,
            TicketDocument.status,
        )
        .where(*filters)
        .order_by(TicketDocument.id)
    ).all()
    out: dict[int, list[TicketFileRead]] = {}
    for ticket_id, traveller_id, file_name, ticket_status in rows:
        out.setdefault(traveller_id, []).append(
            TicketFileRead(
                id=ticket_id,
                file_name=file_name,
                confirmed=ticket_status is TicketStatus.CONFIRMED,
            )
        )
    for files in out.values():
        files.sort(key=lambda f: not f.confirmed)
    return out


def sees_review(reader: User | None, traveller: RequestTraveller) -> bool:
    """Whether this reader may see a manager's recommendation and comment.

    Admins, who weigh it, and the traveller's own manager, who wrote it. Not
    the traveller or their colleagues on the request: it is advice to the
    admin, not a message to them.
    """
    if reader is None or traveller.user is None:
        return False
    return reader.is_admin or traveller.user.manager_id == reader.id


def invoices_for(db: Session, request: TravelRequest) -> dict[int, Invoice]:
    """The invoice each traveller on this request is billed on, if any. One
    query for the request; asked only when cost is being shown."""
    ids = [t.id for t in request.travellers]
    if not ids:
        return {}
    rows = db.execute(
        select(InvoiceLine.request_traveller_id, Invoice)
        .join(Invoice, InvoiceLine.invoice_id == Invoice.id)
        .where(InvoiceLine.request_traveller_id.in_(ids))
    ).all()
    return {traveller_id: invoice for traveller_id, invoice in rows}


def _traveller_read(
    t: RequestTraveller,
    request: TravelRequest,
    *,
    show_cost: bool,
    reader: User | None,
    tickets: dict[int, int],
    billed: dict[int, Invoice],
    room_matches: dict[int, list[CoStayMatchRead]] | None = None,
    confirmed: set[int] | None = None,
    files: dict[int, list[TicketFileRead]] | None = None,
) -> TravellerRead:
    manager = t.user.active_manager if t.user else None
    review = sees_review(reader, t)
    invoice = billed.get(t.id) if show_cost else None
    return TravellerRead(
        id=t.id,
        user_id=t.user_id,
        full_name=t.user.full_name if t.user else "",
        email=t.user.email if t.user else "",
        designation=t.user.designation if t.user else None,
        status=t.status,
        is_requester=t.user_id == request.requester_id,
        room_sharing=t.room_sharing,
        share_with_user_id=t.share_with_user_id,
        share_with_name=t.share_with.full_name if t.share_with else None,
        share_confirmed=t.share_confirmed_at is not None,
        room_matches=(room_matches or {}).get(t.id, []),
        ticket_ready=t.id in (confirmed or set()),
        decided_by_name=t.decided_by.full_name if t.decided_by else None,
        decided_at=t.decided_at,
        decision_reason=t.decision_reason,
        booking_reference=t.booking_reference,
        booking_details=t.booking_details,
        ticket_id=tickets.get(t.id),
        ticket_files=(files or {}).get(t.id, []),
        manager_id=manager.id if manager else None,
        manager_name=manager.full_name if manager else None,
        manager_recommendation=t.manager_recommendation if review else None,
        manager_comment=t.manager_comment if review else None,
        manager_reviewed_at=t.manager_reviewed_at if review else None,
        manager_reviewed_by_name=(
            t.manager_reviewed_by.full_name if review and t.manager_reviewed_by else None
        ),
        # Cost is admin-only. Ground staff seeing what a colleague's flight
        # cost is a personnel problem nobody asked for, and nothing in
        # section 6 needs it.
        cost_amount=t.cost_amount if show_cost else None,
        cost_currency=t.cost_currency if show_cost and t.cost_amount else None,
        cost_note=t.cost_note if show_cost else None,
        cost_entered_by_name=(
            t.cost_entered_by.full_name if show_cost and t.cost_entered_by else None
        ),
        vendor_id=t.vendor_id if show_cost else None,
        vendor_name=t.vendor.name if show_cost and t.vendor_id else None,
        invoice_id=invoice.id if invoice else None,
        invoice_number=invoice.number if invoice else None,
        invoice_status=invoice.status if invoice else None,
    )


def _name(person: User | None) -> str | None:
    return person.full_name if person is not None else None


def to_read(
    db: Session,
    request: TravelRequest,
    *,
    tenant_id: str,
    viewer: User | None = None,
    reader: User | None = None,
    with_conflicts: bool = False,
    with_costay: bool = True,
    with_tickets: bool = False,
) -> RequestRead:
    """The response shape of one request.

    `viewer` unlocks the admin extras - cost, and co-stay matches for them.
    `reader` is who the response is for, when that is not the viewer: the
    paged list leaves cost and matching out for everyone, but still has to
    show an admin or a manager the recommendations they may see. A caller that
    passes `viewer` need not pass `reader`.
    """
    show_cost = viewer is not None and viewer.is_admin
    reader = reader or viewer
    # Only the admin queue asks: ticket files are admin-only to fetch, and the
    # export and single reads have no use for the extra query per request.
    tickets = ticket_per_traveller(db, request.id) if with_tickets else {}
    billed = invoices_for(db, request) if show_cost else {}
    # Booked travellers with a ticket file they can download from My requests.
    confirmed = (
        confirmed_ticket_travellers(db, request.id)
        if any(t.status is TravellerStatus.BOOKED for t in request.travellers)
        else set()
    )
    # Who each hotel traveller could share a room with - the admin allots it.
    rooms = (
        room_matches_for(db, tenant_id=tenant_id, request=request)
        if reader is not None and reader.is_admin
        else {}
    )
    files = ticket_files(db, request, reader)
    travellers = [
        _traveller_read(
            t, request, show_cost=show_cost, reader=reader, tickets=tickets, billed=billed,
            room_matches=rooms, confirmed=confirmed, files=files,
        )
        for t in request.travellers
    ]
    live = live_extension(db, request)
    can_extend = (
        request.request_type in EXTENDABLE
        and extension_refusal(db, request, reader, live=live) is None
    )

    read = RequestRead(
        id=request.id,
        request_type=request.request_type,
        status=status_of(request),
        is_editable=request_is_editable(request),
        is_draft=request.is_draft,
        is_cancelled=request.is_cancelled,
        cancel_reason=request.cancel_reason,
        project_id=request.project_id,
        project_name=request.project.name if request.project else "",
        project_code=request.project.code if request.project else "",
        requester_id=request.requester_id,
        requester_name=request.requester.full_name if request.requester else "",
        mode=request.mode,
        origin=request.origin,
        destination=request.destination,
        pickup_city=request.pickup_city,
        drop_city=request.drop_city,
        start_at=request.start_at,
        end_at=request.end_at,
        hotel_city=request.hotel_city,
        check_in=request.check_in,
        check_out=request.check_out,
        cab_type=request.cab_type,
        cab_trip=request.cab_trip,
        cab_distance_km=request.cab_distance_km,
        booked_cab_type=request.booked_cab_type,
        cab_vehicle_number=request.cab_vehicle_number,
        cab_driver_name=request.cab_driver_name,
        cab_driver_phone=request.cab_driver_phone,
        cab_booked_by_name=_name(request.cab_booked_by),
        cab_booked_at=request.cab_booked_at,
        cancellation_status=request.cancellation_status,
        cancellation_reason=request.cancellation_reason,
        cancellation_requested_by_name=_name(request.cancellation_requested_by),
        cancellation_requested_at=request.cancellation_requested_at,
        cancellation_decided_by_name=_name(request.cancellation_decided_by),
        cancellation_decided_at=request.cancellation_decided_at,
        cancellation_comment=request.cancellation_comment,
        cancel_needs_approval=reader is not None and _cancel_needs_approval(request, reader),
        can_decide_cancellation=(
            reader is not None
            and request.cancellation_status is CancellationStatus.PENDING
            and not request.is_cancelled
            and _may_decide_cancellation(request, reader)
        ),
        cancelled_by_name=_name(request.cancelled_by),
        cab_extension_status=request.cab_extension_status,
        cab_extension_reason=request.cab_extension_reason,
        cab_extension_requested_by_name=_name(request.cab_extension_requested_by),
        cab_extension_requested_at=request.cab_extension_requested_at,
        cab_extension_decided_by_name=_name(request.cab_extension_decided_by),
        cab_extension_decided_at=request.cab_extension_decided_at,
        cab_extension_comment=request.cab_extension_comment,
        cab_extended_days=request.cab_extended_days or 0,
        extends_request_id=request.extends_request_id,
        previous_booking=booking_label(request.extends) if request.extends_request_id else None,
        extended_by_request_id=live.id if live is not None else None,
        can_extend=can_extend,
        extend_until=extension_deadline(request) if can_extend else None,
        travel_reason=request.travel_reason,
        priority=request.priority or RequestPriority.MEDIUM,
        origin_state=request.origin_state,
        destination_state=request.destination_state,
        hotel_state=request.hotel_state,
        other_project_name=request.other_project_name,
        notes=request.notes,
        submitted_at=request.submitted_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
        travellers=travellers,
        edit_count=edit_count(db, request.id),
        is_decided=bool(request.travellers)
        and all(t.status is not TravellerStatus.PENDING for t in request.travellers),
    )

    if with_conflicts:
        read.conflicts = check_conflicts(db, tenant_id=tenant_id, request=request)
        # Co-stay matches are about the viewer, so an export (one admin, many
        # requests) skips them rather than run the matcher once per row.
        if with_costay and viewer is not None:
            read.costay_matches = check_costay(
                db, tenant_id=tenant_id, request=request, for_user=viewer
            )
    return read


def revisions_of(db: Session, request_id: int) -> list[RevisionRead]:
    rows = (
        db.execute(
            select(RequestRevision)
            .where(RequestRevision.request_id == request_id)
            .order_by(RequestRevision.revision_number.desc())
        )
        .scalars()
        .all()
    )
    return [
        RevisionRead(
            revision_number=r.revision_number,
            editor_name=r.editor.full_name if r.editor else None,
            created_at=r.created_at,
            summary=r.summary,
            changes=r.changes,
        )
        for r in rows
    ]


def describe_changes(changes: dict) -> str:
    """What the revision row says it touched, in words rather than column names."""
    labels = [FIELD_LABELS.get(key, key) for key in sorted(changes)]
    if len(labels) <= 3:
        return ", ".join(labels)
    return f"{', '.join(labels[:3])} and {len(labels) - 3} more"


def clear_stale_shares(request: TravelRequest, changes: dict) -> None:
    """Drop room shares when the stay itself moves.

    An admin confirmed a share against particular dates in a particular city.
    Carrying that confirmation silently onto different dates would put two people
    in a room neither of them agreed to.
    """
    if not {"hotel_city", "check_in", "check_out"} & changes.keys():
        return
    for traveller in request.travellers:
        if traveller.room_sharing is not RoomSharingChoice.NOT_OFFERED:
            costay.clear_share(traveller)


def record_submission(
    db: Session, *, request: TravelRequest, actor: User, tenant_id: str, http_request=None
) -> list[int]:
    """Mark a request submitted, open its revision trail at 1, and tell the
    admins and the travellers' managers.

    Returns the ids of the emails it queued, for the caller to send once the
    response is on its way (`notifications.deliver_queued`).
    """
    request.is_draft = False
    request.submitted_at = naive_utcnow()
    db.flush()

    write_revision(db, request=request, editor=actor, summary="Raised", changes=None)
    audit.record(
        db,
        action=AuditAction.SUBMIT,
        entity_type="travel_request",
        entity_id=request.id,
        summary=(
            f"{actor.full_name} submitted a {request.request_type} request "
            f"for {len(request.travellers)} traveller(s)"
        ),
        changes=snapshot(request),
        tenant_id=tenant_id,
        actor=actor,
        request=http_request,
    )
    return notify_admins_of_submission(
        db, request=request, actor=actor, tenant_id=tenant_id
    ) + notify_managers_of_submission(db, request=request, actor=actor, tenant_id=tenant_id)


def _cancel_needs_approval(request: TravelRequest, reader: User) -> bool:
    from app.services.cancellations import needs_approval  # cancellations imports this module

    return needs_approval(request, reader)


def _may_decide_cancellation(request: TravelRequest, reader: User) -> bool:
    from app.services.cancellations import may_decide

    return may_decide(request, reader)


def trip_summary(request: TravelRequest) -> str:
    """One line a person can act on: what, where, when."""
    if request.request_type is RequestType.HOTEL:
        where = ", ".join(p for p in (request.hotel_city, request.hotel_state) if p)
        when = request.check_in.strftime("%d %b %Y") if request.check_in else "dates to be set"
        if request.check_out:
            when += f" to {request.check_out.strftime('%d %b %Y')}"
        return f"Hotel in {where}, {when}"
    kind = "Cab" if request.request_type is RequestType.LOCAL_CAB else str(request.mode or "Travel").title()
    when = clock.time_label(request.start_at) if request.start_at else "time to be set"
    line = f"{kind}: {request.route_label(' to ')}, {when}"
    # The vendor is chosen by size and distance, so the admin reads them first.
    asked = request.cab_asked_label if request.request_type is RequestType.LOCAL_CAB else None
    return f"{line} ({asked})" if asked else line


def notify_admins_of_submission(
    db: Session, *, request: TravelRequest, actor: User, tenant_id: str
) -> list[int]:
    """Tell every admin a request is waiting for a decision.

    In app always; by email unless the admin has switched "New requests" off.
    The admin who raised it is not told about their own request.
    """
    admins = (
        db.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.role.in_(ADMIN_ROLES),
                User.is_active.is_(True),
                User.id != actor.id,
            )
        )
        .scalars()
        .all()
    )
    if not admins:
        return []

    summary = trip_summary(request)
    names = ", ".join(t.user.full_name for t in request.travellers if t.user) or actor.full_name
    campaign = f"{request.project.code} - {request.project.name}" if request.project else None
    link = f"{get_settings().frontend_base_url.rstrip('/')}/approvals"
    priority = request.priority or RequestPriority.MEDIUM
    label = priority.value.title()
    # Only HIGH changes the subject and title: urgent mail should stand out in
    # an inbox, and marking every request would make the marker meaningless.
    urgent = priority is RequestPriority.HIGH
    extending = request.extends if request.extends_request_id else None

    queued: list[int] = []
    for admin in admins:
        greeting = admin.full_name.split()[0] if admin.full_name else "there"
        lines = [
            f"Hello {greeting},",
            "",
            (
                f"{actor.full_name} asked to extend request {extending.id}. It needs a decision."
                if extending is not None
                else f"{actor.full_name} raised a travel request that needs a decision."
            ),
            "",
            summary,
            f"Travellers: {names}",
        ]
        if extending is not None:
            before = booking_label(extending)
            lines.append(
                f"Booked before as: {before}" if before else "The trip it extends is not booked yet."
            )
        if campaign:
            lines.append(f"Campaign: {campaign}")
        if request.travel_reason:
            lines.append(f"Reason: {request.travel_reason}")
        lines.append(f"Priority: {label}")
        lines += ["", f"Review it: {link}"]

        rows = notifications.notify(
            db,
            tenant_id=tenant_id,
            user=admin,
            kind="REQUEST_SUBMITTED",
            title=(
                ("High priority: " if urgent else "")
                + f"{actor.full_name} asked to extend request {extending.id}"
                if extending is not None
                else f"High-priority request from {actor.full_name}"
                if urgent
                else f"New request from {actor.full_name}"
            ),
            body=f"{summary} - for {names}. Priority: {label}.",
            request_id=request.id,
            email_subject=(
                ("High priority - " if urgent else "")
                + ("Extension asked: " if extending is not None else "New travel request: ")
                + summary
            )[:255],
            email_body="\n".join(lines),
            deliver_now=False,
        )
        queued += [
            r.id for r in rows
            if r.channel == NotificationChannel.EMAIL and r.status == NotificationStatus.QUEUED
        ]
    return queued


def queued_emails(rows) -> list[int]:
    """The ids of the email rows left QUEUED for an after-response send."""
    return [
        r.id for r in rows
        if r.channel == NotificationChannel.EMAIL and r.status == NotificationStatus.QUEUED
    ]


def teams_on(request: TravelRequest) -> dict[int, tuple[User, list[RequestTraveller]]]:
    """The travellers still pending on a request, grouped under the active
    manager each reports to. Travellers with no manager are left out: there is
    nobody to ask."""
    teams: dict[int, tuple[User, list[RequestTraveller]]] = {}
    for traveller in request.travellers:
        manager = traveller.user.active_manager if traveller.user else None
        if manager is None or traveller.status is not TravellerStatus.PENDING:
            continue
        teams.setdefault(manager.id, (manager, []))[1].append(traveller)
    return teams


def notify_managers_of_submission(
    db: Session,
    *,
    request: TravelRequest,
    actor: User,
    tenant_id: str,
    edited: bool = False,
    only: set[int] | None = None,
) -> list[int]:
    """Ask each traveller's manager for their recommendation.

    One notice per manager, naming only their own people. A manager who raised
    the request themself is not told about it - they know, and can recommend
    from Team approvals. `edited` says their earlier recommendation was cleared
    by a change to the request, so they are being asked again; `only` limits
    the notice to those managers.
    """
    summary = trip_summary(request)
    link = f"{get_settings().frontend_base_url.rstrip('/')}/team-approvals"
    campaign = f"{request.project.code} - {request.project.name}" if request.project else None
    label = (request.priority or RequestPriority.MEDIUM).value.title()

    queued: list[int] = []
    for manager, members in teams_on(request).values():
        if manager.id == actor.id or (only is not None and manager.id not in only):
            continue
        names = ", ".join(t.user.full_name for t in members)
        whose = (
            f"{actor.full_name}'s request"
            if actor.manager_id == manager.id
            else f"A request for {names}"
        )
        asked = "needs your recommendation again" if edited else "needs your recommendation"
        title = f"{whose} {asked}"
        lines = [
            f"Hello {manager.full_name.split()[0] if manager.full_name else 'there'},",
            "",
            (
                f"{actor.full_name} changed a travel request for your team, so your earlier "
                "recommendation was cleared. Please look at it again."
                if edited
                else f"{actor.full_name} asked to extend request {request.extends_request_id} "
                "for your team."
                if request.extends_request_id
                else f"{actor.full_name} raised a travel request for your team."
            ),
            "",
            summary,
            f"Your team on it: {names}",
        ]
        if campaign:
            lines.append(f"Campaign: {campaign}")
        if request.travel_reason:
            lines.append(f"Reason: {request.travel_reason}")
        lines.append(f"Priority: {label}")
        lines += [
            "",
            "Recommend it or not, with a comment. An admin makes the final decision "
            "and sees what you said.",
            f"Team approvals: {link}",
        ]
        queued += queued_emails(
            notifications.notify(
                db,
                tenant_id=tenant_id,
                user=manager,
                kind="TEAM_REQUEST_SUBMITTED",
                title=title[:200],
                body=f"{summary} - for {names}. Priority: {label}.",
                request_id=request.id,
                email_subject=f"{title}: {summary}"[:255],
                email_body="\n".join(lines),
                deliver_now=False,
            )
        )
    return queued


def clear_recommendations(request: TravelRequest) -> list[RequestTraveller]:
    """Forget what managers said about a request that has since changed.

    A recommendation is advice about one version of a trip. Carried onto new
    dates or a new route it would tell the admin the manager agreed to
    something they never saw, so an edit wipes it and the manager is asked
    again. The old advice stays in the activity log.
    """
    cleared = []
    for traveller in request.travellers:
        if traveller.manager_recommendation is None:
            continue
        traveller.manager_recommendation = None
        traveller.manager_comment = None
        traveller.manager_reviewed_by_id = None
        traveller.manager_reviewed_at = None
        cleared.append(traveller)
    return cleared


def ask_managers_after_edit(
    db: Session,
    *,
    request: TravelRequest,
    actor: User,
    tenant_id: str,
    cleared: list[RequestTraveller],
    was_on: set[int],
) -> list[int]:
    """Who needs asking after a submitted request was changed.

    Managers whose recommendation the edit cleared are asked again, and the
    manager of anyone newly added is asked for the first time. A manager who
    has not answered yet already has the request waiting for them, and is left
    alone rather than told twice.
    """
    again = {
        t.user.active_manager.id
        for t in cleared
        if t.user is not None and t.user.active_manager is not None
    }
    first_time = {
        t.user.active_manager.id
        for t in request.travellers
        if t.user_id not in was_on and t.user is not None and t.user.active_manager is not None
    } - again
    queued: list[int] = []
    if again:
        queued += notify_managers_of_submission(
            db, request=request, actor=actor, tenant_id=tenant_id, edited=True, only=again
        )
    if first_time:
        queued += notify_managers_of_submission(
            db, request=request, actor=actor, tenant_id=tenant_id, only=first_time
        )
    return queued
