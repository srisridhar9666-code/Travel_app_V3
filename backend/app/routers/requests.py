"""
Travel, cab and hotel requests (SOW section 3).

Ground staff raise and amend; admins read everything and confirm room shares.
Approval itself is Phase 4 and deliberately absent here.

Two things shape this module:

* **Drafts are private.** A draft is visible to its owner and to nobody else -
  not to an admin, not to a co-traveller tagged on it. It also never occupies
  anyone's calendar, so it cannot cause a conflict warning for someone who has
  not been told it exists.
* **Conflicts warn.** Every write path returns its warnings in the response body
  rather than rejecting the write (addendum B6). The only place a conflict can
  stop anything is Phase 4, where an admin must type a reason to approve over
  one.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.deps import AdminOrManager, AdminUser, CurrentUser, DbSession, ManagerUser
from app.core.enums import (
    ACTIVE_TRAVELLER_STATUSES,
    AuditAction,
    CabExtensionStatus,
    CancellationStatus,
    PRIORITY_RANK,
    RequestPriority,
    RequestStatus,
    RequestType,
    RoomSharingChoice,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import (
    BatchDecisionPayload,
    BookingPayload,
    CabBookingPayload,
    CabExtensionDecision,
    CancelPayload,
    CancellationDecision,
    CoStayMatchRead,
    ColleagueRead,
    ConflictCheckRequest,
    ConflictCheckResponse,
    DecisionPayload,
    ExtensionAsk,
    QueueCounts,
    QueueExport,
    RecommendationPayload,
    RequestCreate,
    RequestEdit,
    RequestListResponse,
    RequestRead,
    RevisionRead,
    RoomAllotPayload,
    RoomSharingChoicePayload,
)
from app.services import (
    audit,
    booking,
    cabs,
    cancellations,
    costay,
    decisions,
    extensions,
    notifications,
    recommendations,
)
from app.services import requests as svc

router = APIRouter(prefix="/requests", tags=["requests"])

#: Request statuses that still need an admin: the awaiting and partly approved
#: tabs. Their high-priority rows are what the queue banner counts.
WAITING = frozenset({RequestStatus.SUBMITTED, RequestStatus.PARTIALLY_APPROVED})

#: The two-level approval filter. "waiting": someone still waiting on an admin
#: has a manager who has not recommended yet. "reviewed": a manager has given
#: their view. For a manager both mean their own team only.
Review = Literal["waiting", "reviewed"]

#: Cabs whose travellers used the older "one more day" ask and are still
#: waiting on an admin. They sit on whichever tab their travellers' status puts
#: them, so the queue lists them on their own as well. (Extending a trip now
#: raises a linked request - see `extensions_only`.)
Extension = Literal["pending"]
Cancellation = Literal["pending"]


def _extension_pending(row: TravelRequest) -> bool:
    return row.cab_extension_status is CabExtensionStatus.PENDING and not row.is_cancelled


def _load(db: Session, request_id: int, user: User) -> TravelRequest:
    """Fetch a request the caller is allowed to see.

    Everything the caller may not see is a 404 rather than a 403 - a 403 would
    confirm that someone else's draft exists.
    """
    row = db.get(TravelRequest, request_id)
    if row is None or row.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

    if row.is_draft and row.requester_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")

    on_it = row.requester_id == user.id or any(t.user_id == user.id for t in row.travellers)
    if not on_it and not user.is_admin and not _leads_someone_on(row, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Request not found.")
    return row


def _leads_someone_on(row: TravelRequest, user: User) -> bool:
    """Whether a manager's team member raised this request or travels on it."""
    if not user.is_manager:
        return False
    people = [row.requester, *(t.user for t in row.travellers)]
    return any(p is not None and p.manager_id == user.id for p in people)


def _team_and_self(user: User):
    """The ids a manager answers for: their own and their team members'."""
    return select(User.id).where(or_(User.id == user.id, User.manager_id == user.id))


def _assert_owner(row: TravelRequest, user: User) -> None:
    """Only the person who raised a request may amend it.

    An admin who disagrees with a request rejects it; they do not rewrite it
    under the requester's name.
    """
    if row.requester_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the person who raised this request can change it.",
        )


# ---------------------------------------------------------------------------
# Literal paths first - a /{request_id} route declared above these would match
# "check" as an int and 422.
# ---------------------------------------------------------------------------


@router.post("/check", response_model=ConflictCheckResponse)
def check(payload: ConflictCheckRequest, user: CurrentUser, db: DbSession) -> ConflictCheckResponse:
    """Dry-run the conflict and co-stay checks for a request being typed.

    Saves nothing. The form calls this as dates change so the warning appears
    while there is still time to act on it, and the same checks run again on the
    real write - this endpoint is a convenience, never the enforcement point.
    """
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )
    probe = TravelRequest(
        tenant_id=user.tenant_id,
        requester_id=user.id,
        project_id=payload.project_id,
        request_type=payload.request_type,
    )
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(probe, payload)
    probe.id = payload.request_id  # so an edit does not conflict with itself
    probe.travellers = [RequestTraveller(user_id=p.id) for p in people]

    return ConflictCheckResponse(
        conflicts=svc.check_conflicts(db, tenant_id=user.tenant_id, request=probe),
        costay_matches=svc.check_costay(
            db, tenant_id=user.tenant_id, request=probe, for_user=user
        ),
    )


@router.get("/room-matches", response_model=list[CoStayMatchRead])
def room_matches(
    user: CurrentUser,
    db: DbSession,
    city: Annotated[str, Query(min_length=1, max_length=120)],
    check_in: Annotated[date | None, Query()] = None,
    check_out: Annotated[date | None, Query()] = None,
    request_id: Annotated[int | None, Query()] = None,
) -> list[CoStayMatchRead]:
    """Colleagues of the caller's gender staying in this city, for the hotel form.

    Answers as soon as a city is picked: with dates, those whose stay overlaps
    (most nights in common first); without, everyone with a stay there still to
    come, so the dates can be lined up. Same rules as the offer on a saved
    request - the gender policy filters before anything is returned.
    """
    matches = costay.find_matches(
        db,
        tenant_id=user.tenant_id,
        for_user=user,
        city=city,
        check_in=check_in,
        check_out=check_out,
        exclude_request_id=request_id,
        upcoming_from=None if check_in else clock.local_today(),
    )
    return [CoStayMatchRead(**vars(m)) for m in matches]


def _choose_room(
    db: Session,
    *,
    row: TravelRequest,
    user: User,
    choice: RoomSharingChoice,
    share_with_user_id: int | None,
) -> None:
    """Record the requester's room choice from the form on their own traveller
    row. Sharing is checked against the live offer, so only a colleague who
    could actually be offered is accepted; it stays an ask until an admin
    confirms it."""
    mine = next((t for t in row.travellers if t.user_id == user.id), None)
    if mine is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A room choice is for your own stay - you are not on this one.",
        )
    if choice is RoomSharingChoice.SHARE_EXISTING:
        offered = {
            m.user_id
            for m in costay.find_matches(
                db,
                tenant_id=user.tenant_id,
                for_user=user,
                city=row.hotel_city or "",
                check_in=row.check_in,
                check_out=row.check_out,
                exclude_request_id=row.id,
            )
        }
        if share_with_user_id not in offered:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="That colleague is not staying there on those nights, so a shared room cannot be asked for.",
            )
    mine.room_sharing = choice
    mine.share_with_user_id = share_with_user_id


def _tell_share_colleague(db: Session, row: TravelRequest, user: User) -> None:
    """Once the request is visible, tell the colleague an ask to share was made."""
    mine = next((t for t in row.travellers if t.user_id == user.id), None)
    if (
        mine is not None
        and mine.room_sharing is RoomSharingChoice.SHARE_EXISTING
        and mine.share_with_user_id
        and mine.share_confirmed_at is None
    ):
        costay.notify_share_request(
            db,
            tenant_id=user.tenant_id,
            colleague_id=mine.share_with_user_id,
            requester=user,
            request=row,
        )


def _matching(
    db: Session,
    user: User,
    *,
    mine: bool,
    request_status: RequestStatus | None,
    request_type: RequestType | None,
    project_id: int | None,
    search: str | None,
    priority: RequestPriority | None,
    sort: str,
    review: Review | None = None,
    extension: Extension | None = None,
    cancellation: Cancellation | None = None,
    extensions_only: bool = False,
) -> list[TravelRequest]:
    """Every request the caller may see that matches the filters, in order.

    Shared by the paged list and the queue export, so a CSV always holds exactly
    the rows the tab it came from would page through.
    """
    filters = [TravelRequest.tenant_id == user.tenant_id]

    if mine or not (user.is_admin or user.is_manager):
        on_request = select(RequestTraveller.request_id).where(RequestTraveller.user_id == user.id)
        filters.append(
            or_(TravelRequest.requester_id == user.id, TravelRequest.id.in_(on_request))
        )
    elif not user.is_admin:
        # A manager's wider view is their team's trips - never the whole
        # organisation's.
        people = _team_and_self(user)
        on_request = select(RequestTraveller.request_id).where(RequestTraveller.user_id.in_(people))
        filters.append(
            or_(TravelRequest.requester_id.in_(people), TravelRequest.id.in_(on_request))
        )

    # Someone else's draft does not exist as far as this list is concerned.
    filters.append(
        or_(TravelRequest.is_draft.is_(False), TravelRequest.requester_id == user.id)
    )

    if request_type is not None:
        filters.append(TravelRequest.request_type == request_type)
    if cancellation == "pending":
        filters += [
            TravelRequest.cancellation_status == CancellationStatus.PENDING,
            TravelRequest.is_cancelled.is_(False),
        ]
    if extension == "pending":
        filters += [
            TravelRequest.cab_extension_status == CabExtensionStatus.PENDING,
            TravelRequest.is_cancelled.is_(False),
        ]
    if extensions_only:
        filters.append(TravelRequest.extends_request_id.is_not(None))
    if project_id is not None:
        filters.append(TravelRequest.project_id == project_id)
    if priority is not None:
        filters.append(TravelRequest.priority == priority)
    if search:
        needle = search.strip()
        like = f"%{needle}%"
        # People: whoever raised it, and everyone travelling on it - by name,
        # employee code or email. Places, notes and the campaign as before, and
        # a bare number finds that request.
        people = select(User.id).where(
            User.tenant_id == user.tenant_id,
            or_(User.full_name.like(like), User.employee_code.like(like), User.email.like(like)),
        )
        on_board = select(RequestTraveller.request_id).where(RequestTraveller.user_id.in_(people))
        campaigns = select(Project.id).where(
            Project.tenant_id == user.tenant_id,
            or_(Project.name.like(like), Project.code.like(like)),
        )
        matches = [
            TravelRequest.requester_id.in_(people),
            TravelRequest.id.in_(on_board),
            TravelRequest.origin.like(like),
            TravelRequest.destination.like(like),
            TravelRequest.pickup_city.like(like),
            TravelRequest.drop_city.like(like),
            TravelRequest.hotel_city.like(like),
            TravelRequest.notes.like(like),
            TravelRequest.project_id.in_(campaigns),
            TravelRequest.other_project_name.like(like),
        ]
        if needle.isdigit():
            matches.append(TravelRequest.id == int(needle))
        filters.append(or_(*matches))

    rows = (
        db.execute(select(TravelRequest).where(*filters).order_by(TravelRequest.id.desc()))
        .scalars()
        .unique()
        .all()
    )

    # Status is derived from the traveller rows, so it cannot be filtered in SQL
    # without duplicating the derivation in two places. At roughly a hundred
    # staff the list is small enough that filtering in Python is honest and
    # cheap; if it ever is not, the fix is a materialised column maintained by
    # the same function, not a second copy of the rule.
    if request_status is not None:
        rows = [r for r in rows if svc.status_of(r) is request_status]

    # The same reasoning applies to the manager's review: it is a fact about
    # traveller rows and who they report to now, so it is read off the rows.
    if review == "waiting":
        rows = [
            r for r in rows
            if svc.status_of(r) in WAITING and recommendations.waits_on(r, user)
        ]
    elif review == "reviewed":
        rows = [r for r in rows if recommendations.reviewed_by(r, user)]

    # The rows are already in memory, so "high first" is a stable re-sort of a
    # newest-first list rather than a second query.
    if sort == "priority":
        rows = sorted(rows, key=lambda r: PRIORITY_RANK.get(r.priority, 1))
    return list(rows)


@router.get("", response_model=RequestListResponse)
def list_requests(
    user: CurrentUser,
    db: DbSession,
    mine: Annotated[bool, Query(description="Only requests this person is on")] = True,
    request_status: Annotated[RequestStatus | None, Query(alias="status")] = None,
    request_type: Annotated[RequestType | None, Query(alias="type")] = None,
    project_id: Annotated[int | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    priority: Annotated[RequestPriority | None, Query()] = None,
    sort: Annotated[Literal["newest", "priority"], Query()] = "newest",
    review: Annotated[Review | None, Query(description="Two-level approval filter")] = None,
    extension: Annotated[
        Extension | None, Query(description="Cabs waiting on a one-more-day decision")
    ] = None,
    cancellation: Annotated[
        Cancellation | None, Query(description="Decided trips whose requester asked to cancel")
    ] = None,
    extensions_only: Annotated[
        bool, Query(description="Only extensions: a cab kept longer, a stay made longer")
    ] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> RequestListResponse:
    """The caller's requests, newest first (or high priority first).

    Ground staff always see only what they are on, whatever `mine` says. Admins
    can widen to the whole tenant and managers to their team - minus everyone
    else's drafts. Costs are left out for everyone but admins.

    `review=waiting` is a manager's "needs my recommendation" list (and, for
    an admin, every request still waiting on a manager); `review=reviewed` is
    what a manager has already given their view on. `extension=pending` is
    every cab still waiting for an admin to say whether it is kept a day longer.
    """
    rows = _matching(
        db,
        user,
        mine=mine,
        request_status=request_status,
        request_type=request_type,
        project_id=project_id,
        search=search,
        priority=priority,
        sort=sort,
        review=review,
        extension=extension,
        cancellation=cancellation,
        extensions_only=extensions_only,
    )

    total = len(rows)
    window = rows[(page - 1) * page_size : page * page_size]

    # Conflicts are computed for the page being returned, not the whole result
    # set, so a warning is visible at a glance rather than only after opening a
    # request. Co-stay matches are left out: they are only actionable on one
    # request at a time, and GET /requests/{id} carries them.
    return RequestListResponse(
        items=[
            svc.to_read(
                db,
                r,
                tenant_id=user.tenant_id,
                reader=user,
                with_conflicts=True,
                with_tickets=user.is_admin,
            )
            for r in window
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def _read_and_release(db: Session, row: TravelRequest, user: User) -> RequestRead:
    """The response, built before the request's session is let go.

    For the endpoints that queue emails to send after the response: FastAPI
    closes `get_db`'s session only after background tasks finish, so otherwise
    this connection would stay checked out for as long as those sends take -
    a mail server timeout per admin, when it cannot be reached.
    """
    result = svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)
    db.close()
    return result


@router.post("", response_model=RequestRead, status_code=status.HTTP_201_CREATED)
def create_request(
    payload: RequestCreate,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Raise a request, as a draft or straight into the queue.

    Conflicts are returned alongside the created request rather than preventing
    it: the requester may know something the calendar does not.
    """
    svc.resolve_project(
        db, payload.project_id, user.tenant_id, other_name=payload.other_project_name
    )
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )

    row = TravelRequest(
        tenant_id=user.tenant_id,
        requester_id=user.id,
        request_type=payload.request_type,
        project_id=payload.project_id,
        is_draft=payload.is_draft,
    )
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(row, payload)
    row.travellers = [
        RequestTraveller(user_id=p.id, status=TravellerStatus.PENDING) for p in people
    ]
    db.add(row)
    db.flush()
    if payload.room_sharing is not None:
        _choose_room(
            db, row=row, user=user, choice=payload.room_sharing,
            share_with_user_id=payload.share_with_user_id,
        )

    if payload.is_draft:
        audit.record(
            db,
            action=AuditAction.CREATE,
            entity_type="travel_request",
            entity_id=row.id,
            summary=f"{user.full_name} started a draft {row.request_type} request",
            tenant_id=user.tenant_id,
            actor=user,
            request=http_request,
        )
    else:
        queued = svc.record_submission(
            db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
        )
        _tell_share_colleague(db, row, user)
        background.add_task(notifications.deliver_queued, queued)

    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.get("/queue/counts", response_model=QueueCounts)
def queue_counts(
    actor: AdminOrManager,
    db: DbSession,
    request_type: Annotated[RequestType | None, Query(alias="type")] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    priority: Annotated[RequestPriority | None, Query()] = None,
    review: Annotated[Review | None, Query()] = None,
    extensions_only: Annotated[bool, Query()] = False,
) -> QueueCounts:
    """Headline numbers for the admin queue tabs.

    Counted over the whole tenant rather than the current page, because the tab
    labels have to be true regardless of which page is open. With a type, a
    search or a priority, only the requests matching it count, by the same rule
    the tab's list applies - so "Booked 2" under "High priority" means two high-priority
    bookings, not every booking. Drafts are excluded: they are not in the queue
    and their owners have not asked for them to be.
    """
    rows = [
        row
        for row in _matching(
            db,
            actor,
            mine=False,
            request_status=None,
            request_type=request_type,
            project_id=None,
            search=search,
            priority=priority,
            sort="newest",
            review=review,
            extensions_only=extensions_only,
        )
        if not row.is_draft
    ]

    tally = {s: 0 for s in RequestStatus}
    conflicted = 0
    edited = 0
    on_manager = 0
    extensions = 0
    extension_asks = 0
    cancel_asks = 0
    urgent = {s: 0 for s in WAITING}
    for row in rows:
        row_status = svc.status_of(row)
        tally[row_status] += 1
        if _extension_pending(row):
            extensions += 1
        if row.extends_request_id is not None and row_status in WAITING:
            extension_asks += 1
        if cancellations.is_pending(row) and cancellations.may_decide(row, actor):
            cancel_asks += 1
        if row.priority is RequestPriority.HIGH and row_status in WAITING:
            urgent[row_status] += 1
        if row_status in WAITING and recommendations.waits_on(row, actor):
            on_manager += 1
        if svc.edit_count(db, row.id) > 0:
            edited += 1
        # Only requests still awaiting a decision are worth flagging as clashing:
        # a booked trip's clash is history, not a thing to act on.
        if any(t.status is TravellerStatus.PENDING for t in row.travellers) and svc.check_conflicts(
            db, tenant_id=actor.tenant_id, request=row
        ):
            conflicted += 1

    return QueueCounts(
        awaiting=tally[RequestStatus.SUBMITTED],
        partially_approved=tally[RequestStatus.PARTIALLY_APPROVED],
        approved=tally[RequestStatus.APPROVED],
        booked=tally[RequestStatus.BOOKED],
        rejected=tally[RequestStatus.REJECTED],
        cancelled=tally[RequestStatus.CANCELLED],
        expired=tally[RequestStatus.EXPIRED],
        with_conflicts=conflicted,
        edited=edited,
        high_priority=sum(urgent.values()),
        high_priority_awaiting=urgent[RequestStatus.SUBMITTED],
        high_priority_partial=urgent[RequestStatus.PARTIALLY_APPROVED],
        awaiting_manager=on_manager,
        cab_extensions=extensions,
        cancellations=cancel_asks,
        extensions=extension_asks,
    )


@router.get("/queue/export", response_model=QueueExport)
def export_queue(
    actor: AdminUser,
    db: DbSession,
    request_status: Annotated[RequestStatus, Query(alias="status")],
    request_type: Annotated[RequestType | None, Query(alias="type")] = None,
    project_id: Annotated[int | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    priority: Annotated[RequestPriority | None, Query()] = None,
    review: Annotated[Review | None, Query()] = None,
    extensions_only: Annotated[bool, Query()] = False,
) -> QueueExport:
    """Every request in one queue tab, for the admin's CSV.

    The list is paged at 100 and leaves cost out; a spreadsheet needs every row
    and the cost, so this returns the whole tab read as the admin. Not audited,
    like the travel-log export, which carries the same names and costs.
    """
    rows = _matching(
        db,
        actor,
        mine=False,
        request_status=request_status,
        request_type=request_type,
        project_id=project_id,
        search=search,
        priority=priority,
        sort="priority",
        review=review,
        extensions_only=extensions_only,
    )
    total = len(rows)
    rows = rows[: svc.MAX_EXPORT_ROWS]
    # Clash warnings only matter while someone still has to decide; on a booked
    # or rejected tab they would cost a detection pass per row for nothing.
    with_conflicts = request_status in WAITING
    return QueueExport(
        status=request_status,
        total=total,
        truncated=total > len(rows),
        items=[
            svc.to_read(
                db,
                r,
                tenant_id=actor.tenant_id,
                viewer=actor,
                with_conflicts=with_conflicts,
                with_costay=False,
            )
            for r in rows
        ],
    )


@router.get("/colleagues", response_model=list[ColleagueRead])
def colleagues(user: CurrentUser, db: DbSession) -> list[ColleagueRead]:
    """Who this person can tag onto a request.

    A separate endpoint rather than /users, which is admin-only and returns the
    whole employee record. Tagging a colleague needs a name; it does not need
    their email, phone or account state.
    """
    rows = (
        db.execute(
            select(User)
            .where(
                User.tenant_id == user.tenant_id,
                User.is_active.is_(True),
                User.id != user.id,
            )
            .order_by(User.full_name)
        )
        .scalars()
        .all()
    )
    return [
        ColleagueRead(id=u.id, full_name=u.full_name, designation=u.designation) for u in rows
    ]


# ---------------------------------------------------------------------------
# Per-request routes
# ---------------------------------------------------------------------------


@router.get("/{request_id}", response_model=RequestRead)
def get_request(request_id: int, user: CurrentUser, db: DbSession) -> RequestRead:
    row = _load(db, request_id, user)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)


@router.put("/{request_id}", response_model=RequestRead)
def edit_request(
    request_id: int,
    payload: RequestEdit,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Amend a request while it is still unlocked, recording what changed.

    The body is a full replacement rather than a patch, because a revision has to
    say what the request *was* and what it *became* - and a partial body leaves
    that ambiguous for any field it omits.
    """
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    svc.assert_editable(row)

    svc.resolve_project(
        db, payload.project_id, user.tenant_id, other_name=payload.other_project_name
    )
    people = svc.resolve_travellers(
        db, requester=user, traveller_ids=payload.traveller_ids, tenant_id=user.tenant_id
    )

    was_draft = row.is_draft
    was_on = {t.user_id for t in row.travellers}
    before = svc.snapshot(row)
    svc.canonicalise_places(db, user.tenant_id, payload)
    svc.apply_body(row, payload)
    svc.sync_travellers(db, request=row, people=people)
    after = svc.snapshot(row)

    changes = audit.diff(before, after)
    if changes:
        svc.clear_stale_shares(row, changes)

    if was_draft and not payload.is_draft:
        # Leaving draft is a submission, not an amendment: revision 1 is the
        # version the admin queue first sees, whatever churn preceded it.
        queued = svc.record_submission(
            db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
        )
        background.add_task(notifications.deliver_queued, queued)
    elif changes and not row.is_draft:
        svc.write_revision(
            db,
            request=row,
            editor=user,
            summary=f"Edited {svc.describe_changes(changes)}",
            changes=changes,
        )
        # A manager's view was of the trip as it was. Clear it and ask again,
        # so an admin never reads "recommended" against dates nobody saw.
        cleared = svc.clear_recommendations(row)
        audit.record(
            db,
            action=AuditAction.UPDATE,
            entity_type="travel_request",
            entity_id=row.id,
            summary=(
                f"{user.full_name} edited request {row.id} ({svc.describe_changes(changes)})"
                + ("; the manager's recommendation was cleared" if cleared else "")
            ),
            changes=changes,
            tenant_id=user.tenant_id,
            actor=user,
            request=http_request,
        )
        background.add_task(
            notifications.deliver_queued,
            svc.ask_managers_after_edit(
                db, request=row, actor=user, tenant_id=user.tenant_id,
                cleared=cleared, was_on=was_on,
            ),
        )

    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.post("/{request_id}/submit", response_model=RequestRead)
def submit_request(
    request_id: int,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Move a draft into the admin queue."""
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    if not row.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This request has already been submitted."
        )

    queued = svc.record_submission(
        db, request=row, actor=user, tenant_id=user.tenant_id, http_request=http_request
    )
    _tell_share_colleague(db, row, user)
    background.add_task(notifications.deliver_queued, queued)
    db.commit()
    db.refresh(row)
    return _read_and_release(db, row, user)


@router.post("/{request_id}/cancel", response_model=RequestRead)
def cancel_request(
    request_id: int,
    payload: CancelPayload,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Withdraw a request, with a reason (addendum B4).

    The requester or an admin may cancel. An admin's cancel is immediate. A
    requester's is too until an admin has approved or booked someone on it;
    after that it becomes an ask an admin or their manager approves.
    """
    row = _load(db, request_id, user)
    if row.requester_id != user.id and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You cannot cancel this request."
        )
    if row.is_cancelled:
        return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user)

    # Once someone on it is approved or booked, a ticket may exist: the
    # requester asks, and an admin or their manager decides.
    if cancellations.needs_approval(row, user):
        queued = cancellations.ask(
            db, request=row, asker=user, reason=payload.reason, http_request=http_request
        )
        db.commit()
        background.add_task(notifications.deliver_queued, queued)
        db.refresh(row)
        return _read_and_release(db, row, user)

    was_asked = cancellations.is_pending(row)
    cancellations.cancel(
        db, request=row, actor=user, reason=payload.reason, http_request=http_request
    )
    if was_asked:
        # An admin cancelling outright answers any ask that was waiting.
        row.cancellation_status = CancellationStatus.APPROVED
        row.cancellation_decided_by_id = user.id
        row.cancellation_decided_at = naive_utcnow()
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user)


@router.post("/{request_id}/cancellation/decide", response_model=RequestRead)
def decide_cancellation(
    request_id: int,
    payload: CancellationDecision,
    actor: AdminOrManager,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Approve the ask to cancel (the trip is cancelled) or reject it with a
    comment. For an admin, or the requester's manager."""
    row = _load(db, request_id, actor)
    queued = cancellations.decide(
        db,
        request=row,
        decider=actor,
        approve=payload.approve,
        comment=payload.comment,
        http_request=http_request,
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, actor)


@router.get("/{request_id}/revisions", response_model=list[RevisionRead])
def request_revisions(request_id: int, user: CurrentUser, db: DbSession) -> list[RevisionRead]:
    """The full edit history, newest first. This is what the admin queue expands
    behind "edited N times"."""
    row = _load(db, request_id, user)
    return svc.revisions_of(db, row.id)


@router.post("/{request_id}/room-sharing", response_model=RequestRead)
def set_room_sharing(
    request_id: int,
    payload: RoomSharingChoicePayload,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Record what the requester chose when offered a co-stay (SOW section 3).

    Choosing to share is a *request*, not a booking: it stays unconfirmed until
    an admin signs it off, and the colleague is told straight away. That is the
    C2 assumption - see `services/costay.py`.
    """
    row = _load(db, request_id, user)
    _assert_owner(row, user)
    svc.assert_editable(row)

    if row.request_type is not RequestType.HOTEL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Room sharing only applies to a hotel request.",
        )

    traveller = next((t for t in row.travellers if t.id == payload.traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )

    if payload.choice is RoomSharingChoice.SHARE_EXISTING:
        colleague = db.get(User, payload.share_with_user_id)
        if colleague is None or colleague.tenant_id != user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="That colleague was not found."
            )
        # The gender policy is enforced here as well as in the matcher: the
        # matcher decides what to *offer*, this decides what may be *saved*, and
        # a hand-rolled POST must not slip past the first one.
        if not costay.may_share_room(traveller.user.gender, colleague.gender):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="These two travellers cannot be offered a shared room.",
            )

    before = str(traveller.room_sharing)
    costay.clear_share(traveller)
    traveller.room_sharing = payload.choice
    traveller.share_with_user_id = payload.share_with_user_id

    if payload.choice is RoomSharingChoice.SHARE_EXISTING:
        costay.notify_share_request(
            db,
            tenant_id=user.tenant_id,
            colleague_id=payload.share_with_user_id,
            requester=user,
            request=row,
        )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=(
            f"{user.full_name} set room sharing to {payload.choice} "
            f"for {traveller.user.full_name} on request {row.id}"
        ),
        changes={"room_sharing": {"from": before, "to": str(payload.choice)}},
        tenant_id=user.tenant_id,
        actor=user,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=user.tenant_id, viewer=user, with_conflicts=True)


@router.post("/{request_id}/travellers/{traveller_id}/confirm-share", response_model=RequestRead)
def confirm_share(
    request_id: int,
    traveller_id: int,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """An admin signs off a shared room (C2).

    Nothing books two people into one room until this happens. If the client
    decides the colleague must consent as well, their acceptance becomes a second
    precondition here rather than a new flow.
    """
    row = _load(db, request_id, actor)
    traveller = next((t for t in row.travellers if t.id == traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )
    if traveller.room_sharing is not RoomSharingChoice.SHARE_EXISTING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This traveller has not asked to share a room.",
        )

    traveller.share_confirmed_by_id = actor.id
    traveller.share_confirmed_at = naive_utcnow()

    audit.record(
        db,
        action=AuditAction.APPROVE,
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=(
            f"{actor.full_name} confirmed a shared room for {traveller.user.full_name} "
            f"with {traveller.share_with.full_name if traveller.share_with else 'a colleague'}"
        ),
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor)


@router.post("/{request_id}/travellers/{traveller_id}/room", response_model=RequestRead)
def allot_room(
    request_id: int,
    traveller_id: int,
    payload: RoomAllotPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """An admin decides where a hotel traveller sleeps.

    With a colleague: the two are put in one room - each traveller row points at
    the other and is confirmed - provided they may share (same stated gender)
    and the colleague really is staying in that city on overlapping nights.
    Both are told. With `null`: a room of their own, undoing any pairing on
    both sides.
    """
    row = _load(db, request_id, actor)
    if row.request_type is not RequestType.HOTEL or row.is_cancelled or row.is_draft:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Rooms are allotted on a submitted hotel request that is not cancelled.",
        )
    traveller = _traveller_or_404(row, traveller_id)
    if traveller.status not in ACTIVE_TRAVELLER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{traveller.user.full_name} is no longer on this stay.",
        )
    before = {
        "room_sharing": str(traveller.room_sharing),
        "share_with": traveller.share_with.full_name if traveller.share_with else None,
    }

    def release(person: RequestTraveller) -> None:
        """Undo the other half of a pairing this traveller was in."""
        partner_id = person.share_with_user_id
        if not partner_id:
            return
        for other in db.execute(
            select(RequestTraveller)
            .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
            .where(
                TravelRequest.tenant_id == actor.tenant_id,
                RequestTraveller.user_id == partner_id,
                RequestTraveller.share_with_user_id == person.user_id,
            )
        ).scalars():
            costay.clear_share(other)
            other.room_sharing = RoomSharingChoice.SEPARATE_ROOM

    queued: list[int] = []
    if payload.share_with_user_id is None:
        release(traveller)
        costay.clear_share(traveller)
        traveller.room_sharing = RoomSharingChoice.SEPARATE_ROOM
        summary = f"{actor.full_name} gave {traveller.user.full_name} a room of their own on request {row.id}"
    else:
        colleague = db.get(User, payload.share_with_user_id)
        if colleague is None or colleague.tenant_id != actor.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That colleague was not found.")
        if not costay.may_share_room(traveller.user.gender, colleague.gender):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="These two travellers cannot share a room - shared rooms are for the same gender only.",
            )
        match = next(
            (
                m
                for m in costay.find_matches(
                    db,
                    tenant_id=actor.tenant_id,
                    for_user=traveller.user,
                    city=row.hotel_city or "",
                    check_in=row.check_in,
                    check_out=row.check_out,
                    exclude_request_id=row.id,
                )
                if m.user_id == colleague.id
            ),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{colleague.full_name} is not staying in {row.hotel_city} on any of these nights.",
            )
        other = costay.live_stay(db, user_id=colleague.id, request_id=match.request_id)
        if other is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{colleague.full_name}'s stay is no longer live.",
            )
        if (
            other.room_sharing is RoomSharingChoice.SHARE_EXISTING
            and other.share_confirmed_at is not None
            and other.share_with_user_id not in (None, traveller.user_id)
        ):
            busy = db.get(User, other.share_with_user_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{colleague.full_name} already shares a room with {busy.full_name if busy else 'someone else'}.",
            )
        if traveller.share_with_user_id not in (None, colleague.id):
            release(traveller)
        costay.pair(traveller, other, confirmed_by=actor)
        db.flush()
        for person, partner in ((traveller, colleague), (other, traveller.user)):
            stay = row if person is traveller else db.get(TravelRequest, match.request_id)
            queued += svc.queued_emails(
                costay.notify_shared_room(
                    db, tenant_id=actor.tenant_id, traveller=person, partner=partner,
                    request=stay, admin=actor,
                )
            )
        summary = (
            f"{actor.full_name} put {traveller.user.full_name} and {colleague.full_name} "
            f"in one room in {row.hotel_city} (requests {row.id} and {match.request_id})"
        )

    audit.record(
        db,
        action=AuditAction.UPDATE,
        entity_type="request_traveller",
        entity_id=traveller.id,
        summary=summary,
        changes={
            "room_sharing": {"from": before["room_sharing"], "to": str(traveller.room_sharing)},
            "share_with": {
                "from": before["share_with"],
                "to": traveller.share_with.full_name if traveller.share_with else None,
            },
        },
        tenant_id=actor.tenant_id,
        actor=actor,
        request=http_request,
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, actor)


# ---------------------------------------------------------------------------
# Admin decisions (SOW section 4). The decision unit is one traveller.
# ---------------------------------------------------------------------------


def _traveller_or_404(row: TravelRequest, traveller_id: int) -> RequestTraveller:
    traveller = next((t for t in row.travellers if t.id == traveller_id), None)
    if traveller is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That traveller is not on this request."
        )
    return traveller


def _decidable(row: TravelRequest) -> None:
    if row.is_draft:
        # Unreachable through _load, which hides other people's drafts, but an
        # admin deciding their own draft would approve something never submitted.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has not been submitted yet.",
        )
    if row.is_cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has been cancelled.",
        )


@router.post("/{request_id}/travellers/{traveller_id}/decide", response_model=RequestRead)
def decide_traveller(
    request_id: int,
    traveller_id: int,
    payload: DecisionPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Approve, reject, book or cancel one person on a request.

    Deciding one traveller locks the request against further edits by the
    requester - that is addendum A1, and it happens as a consequence of the
    status change rather than as a separate flag.
    """
    row = _load(db, request_id, actor)
    _decidable(row)
    traveller = _traveller_or_404(row, traveller_id)

    decisions.apply(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        request=row,
        traveller=traveller,
        decision=decisions.Decision(
            traveller_id=traveller.id,
            to_status=payload.to_status,
            reason=payload.reason,
            booking_reference=payload.booking_reference,
            booking_details=payload.booking_details.stored() if payload.booking_details else None,
            conflict_override_reason=payload.conflict_override_reason,
            ticket_id=payload.ticket_id,
        ),
        http_request=http_request,
        notify=payload.notify_employee,
    )
    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor, with_conflicts=True)


@router.post("/{request_id}/decide", response_model=RequestRead)
def decide_batch(
    request_id: int,
    payload: BatchDecisionPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
) -> RequestRead:
    """Decide several travellers on one request in a single transaction.

    This is what partial approval looks like from the queue: tick three people,
    reject the fourth, press once. One failure rolls the whole set back, so the
    queue can never show a half-applied decision - and the ledger never records
    one either.
    """
    row = _load(db, request_id, actor)
    _decidable(row)

    seen: set[int] = set()
    for item in payload.decisions:
        if item.traveller_id in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The same traveller appears twice in this batch.",
            )
        seen.add(item.traveller_id)

        traveller = _traveller_or_404(row, item.traveller_id)
        decisions.apply(
            db,
            tenant_id=actor.tenant_id,
            actor=actor,
            request=row,
            traveller=traveller,
            decision=decisions.Decision(
                traveller_id=traveller.id,
                to_status=item.to_status,
                reason=item.reason,
                booking_reference=item.booking_reference,
                booking_details=item.booking_details.stored() if item.booking_details else None,
                conflict_override_reason=item.conflict_override_reason,
                ticket_id=item.ticket_id,
            ),
            http_request=http_request,
            notify=item.notify_employee,
        )

    db.commit()
    db.refresh(row)
    return svc.to_read(db, row, tenant_id=actor.tenant_id, viewer=actor, with_conflicts=True)


@router.post("/{request_id}/book", response_model=RequestRead)
def book_travellers(
    request_id: int,
    payload: BookingPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Book one or more approved travellers in one step: reference, details,
    the car for a cab, the files, the cost and who was paid - and one email to
    them all, their managers copied, every file attached. All or nothing."""
    row = _load(db, request_id, actor)
    _decidable(row)
    queued = booking.book(db, request=row, admin=actor, payload=payload, http_request=http_request)
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, actor)


# ---------------------------------------------------------------------------
# The manager's recommendation: the first of the two levels.
# ---------------------------------------------------------------------------


@router.post("/{request_id}/recommendation", response_model=RequestRead)
def recommend(
    request_id: int,
    payload: RecommendationPayload,
    manager: ManagerUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """A manager recommends their team members' trip, or does not, with a comment.

    Covers their own people on the request who are still pending - or the ones
    named in `traveller_ids`. They may change their mind until an admin
    decides; every version is logged. The admins are told, comment included,
    and the admin's decision stays final either way.
    """
    row = _load(db, request_id, manager)
    queued = recommendations.record(
        db,
        request=row,
        manager=manager,
        recommendation=payload.recommendation,
        comment=payload.comment,
        traveller_ids=payload.traveller_ids,
        http_request=http_request,
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, manager)


# ---------------------------------------------------------------------------
# Cabs: the car that was sent. Extending a cab or a stay.
# ---------------------------------------------------------------------------


@router.put("/{request_id}/cab-booking", response_model=RequestRead)
def record_cab(
    request_id: int,
    payload: CabBookingPayload,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Record the car sent for a cab - size, number plate, driver - or change it.

    Allowed once someone on the cab is approved or booked, and as often as the
    vendor swaps cars; each change is logged with what it replaced, and
    everyone riding is told, their manager copied. Nobody's status moves:
    booking a traveller is still a decision with a booking reference.
    """
    row = _load(db, request_id, actor)
    queued = cabs.record_booking(
        db, request=row, admin=actor, payload=payload, http_request=http_request
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, actor)


@router.post(
    "/{request_id}/extend", response_model=RequestRead, status_code=status.HTTP_201_CREATED
)
def extend_trip(
    request_id: int,
    payload: ExtensionAsk,
    user: CurrentUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Carry a decided cab or stay on for more days, with a reason.

    For someone approved or booked on it. The extension is a new request,
    linked to this one, straight into the admin queue: decided and booked like
    any other, so a different car or room, and its own cost, are no trouble.
    Returns the extension.
    """
    row = _load(db, request_id, user)
    child, queued = extensions.extend(
        db, request=row, asker=user, payload=payload, http_request=http_request
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(child)
    return _read_and_release(db, child, user)


@router.post("/{request_id}/cab-extension/decide", response_model=RequestRead)
def decide_cab_extension(
    request_id: int,
    payload: CabExtensionDecision,
    actor: AdminUser,
    http_request: Request,
    db: DbSession,
    background: BackgroundTasks,
) -> RequestRead:
    """Approve one more day - the cab's end time moves a day later - or reject
    it with a comment. The travellers are told, their managers copied."""
    row = _load(db, request_id, actor)
    queued = cabs.decide_extension(
        db,
        request=row,
        admin=actor,
        approve=payload.approve,
        comment=payload.comment,
        http_request=http_request,
    )
    db.commit()
    background.add_task(notifications.deliver_queued, queued)
    db.refresh(row)
    return _read_and_release(db, row, actor)
