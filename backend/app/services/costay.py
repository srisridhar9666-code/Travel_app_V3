"""
Room sharing: who could share, and who may (SOW section 3, addendum B7 / C2).

Two rules, deliberately separate.

**Who could share** is a calendar question. Someone is a candidate if they hold a
live hotel traveller row - `ACTIVE_TRAVELLER_STATUSES` on a request that is
neither a draft nor cancelled - in the same city, on at least one of the same
nights. Pending counts as well as booked: the point of the prompt is to catch the
pairing before two rooms are reserved, and by the time both are BOOKED the saving
has already been missed.

**Who may share** is a policy question, and the policy is narrow. A shared room
is offered only on an **exact** `Gender` match between two people who have stated
a binary value. `OTHER` and `UNDISCLOSED` never share - not because the pairing
would be wrong, but because this system has not asked them, and a room is not
something to infer consent for. They are offered a separate room, silently; the
requester is never told a candidate was filtered out on gender grounds.

Open question **C2** is answered here by assumption, pending the client:

* the prompt shows the colleague's **name and designation** - this is an internal
  ops tool, and "1 colleague available" is not enough to make a decision on;
* the requester's choice is a *request*. `SHARE_EXISTING` sets
  `room_sharing` but leaves `share_confirmed_at` null until an **admin**
  confirms it in Phase 4;
* the colleague is **notified** when someone asks to share with them.

The colleague's own consent is not required. If the client wants it, the shape to
change is `confirm_share` plus one more column on `request_travellers` - the
matching and the disclosure rules above do not move.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    ACTIVE_TRAVELLER_STATUSES,
    Gender,
    RequestType,
    RoomSharingChoice,
)
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications

#: Genders that may be paired into one room, and only with themselves.
_SHAREABLE = {Gender.MALE, Gender.FEMALE}


def may_share_room(a: Gender | None, b: Gender | None) -> bool:
    """An exact match between two stated binary values, and nothing else.

    `OTHER` with `OTHER` is deliberately false: identical labels are not consent,
    and the fallback (a separate room) costs money rather than dignity.
    """
    if a is None or b is None:
        return False
    return a is b and a in _SHAREABLE


@dataclass(frozen=True)
class CoStayMatch:
    """A colleague who could share, as shown to the requester."""

    user_id: int
    full_name: str
    designation: str | None
    request_id: int
    hotel_city: str
    check_in: date
    check_out: date | None
    overlapping_nights: int
    status: str
    hotel_name: str | None = None
    same_hotel: bool = False


def _overlap_nights(a_in: date, a_out: date, b_in: date, b_out: date) -> int:
    start = max(a_in, b_in)
    end = min(a_out, b_out)
    return max((end - start).days, 0)


def find_matches(
    db: Session,
    *,
    tenant_id: str,
    for_user: User,
    city: str,
    check_in: date | None,
    check_out: date | None,
    exclude_request_id: int | None = None,
    upcoming_from: date | None = None,
    hotel_name: str | None = None,
) -> list[CoStayMatch]:
    """Colleagues this person could be offered a shared room with.

    Already filtered by the gender policy, so the caller never sees a candidate
    it must then reject - and never learns that one existed.

    Without dates yet, `upcoming_from` lists everyone of the same gender with a
    stay in that city still to come (or under way) - so the form can say who is
    there as soon as a city is picked, and the dates can be lined up. Those
    carry no nights in common until dates are given.
    """
    if not city:
        return []
    if check_in is None and upcoming_from is None:
        return []

    out = None
    if check_in is not None:
        out = check_out or date.fromordinal(check_in.toordinal() + 1)
        if out <= check_in:
            out = date.fromordinal(check_in.toordinal() + 1)

    rows = db.execute(
        select(RequestTraveller, TravelRequest, User)
        .join(TravelRequest, TravelRequest.id == RequestTraveller.request_id)
        .join(User, User.id == RequestTraveller.user_id)
        .where(
            TravelRequest.tenant_id == tenant_id,
            TravelRequest.request_type == RequestType.HOTEL,
            TravelRequest.is_draft.is_(False),
            TravelRequest.is_cancelled.is_(False),
            RequestTraveller.status.in_(ACTIVE_TRAVELLER_STATUSES),
            User.id != for_user.id,
            User.is_active.is_(True),
        )
    ).all()

    matches: list[CoStayMatch] = []
    seen: set[int] = set()

    for traveller, request, colleague in rows:
        if exclude_request_id is not None and request.id == exclude_request_id:
            continue
        if colleague.id in seen:
            continue
        if not request.hotel_city or request.hotel_city.strip().casefold() != city.strip().casefold():
            continue
        if not may_share_room(for_user.gender, colleague.gender):
            continue

        their_in = request.check_in
        if their_in is None:
            continue
        their_out = request.check_out or date.fromordinal(their_in.toordinal() + 1)
        if their_out <= their_in:
            their_out = date.fromordinal(their_in.toordinal() + 1)

        if check_in is None:
            # No dates yet: anyone still to check out counts, nights unknown.
            if their_out <= upcoming_from:
                continue
            nights = 0
        else:
            nights = _overlap_nights(check_in, out, their_in, their_out)
            if nights <= 0:
                continue

        colleague_hotel = None
        if getattr(request, "selected_hotel_proposal", None):
            colleague_hotel = request.selected_hotel_proposal.hotel_name
        elif traveller.booking_details and isinstance(traveller.booking_details, dict):
            colleague_hotel = traveller.booking_details.get("hotel_name")

        is_same_hotel = False
        if hotel_name and colleague_hotel:
            is_same_hotel = colleague_hotel.strip().casefold() == hotel_name.strip().casefold()

        seen.add(colleague.id)
        matches.append(
            CoStayMatch(
                user_id=colleague.id,
                full_name=colleague.full_name,
                designation=str(colleague.designation) if colleague.designation else None,
                request_id=request.id,
                hotel_city=request.hotel_city,
                check_in=their_in,
                check_out=request.check_out,
                overlapping_nights=nights,
                status=str(traveller.status),
                hotel_name=colleague_hotel,
                same_hotel=is_same_hotel,
            )
        )

    # Exact same hotel matches first, then most nights in common, then soonest stay.
    matches.sort(key=lambda m: (not m.same_hotel, -m.overlapping_nights, m.check_in, m.full_name))
    return matches


def notify_share_request(
    db: Session,
    *,
    tenant_id: str,
    colleague_id: int,
    requester: User,
    request: TravelRequest,
) -> None:
    """Tell the colleague that someone has asked to share their room (C2).

    Sent when the choice is made rather than when an admin confirms it, so the
    other person hears it from the system before they hear it in a corridor.
    Goes through the ledger, so the email attempt is recorded like any other.
    """
    colleague = db.get(User, colleague_id)
    if colleague is None:
        return

    where = request.hotel_city or "the same city"
    short = (
        f"{requester.full_name} has requested to share your accommodation in "
        f"{where} from {request.check_in} to {request.check_out}. "
        "An admin will confirm this before anything is booked."
    )
    greeting = colleague.full_name.split()[0] if colleague.full_name else "there"

    notifications.notify(
        db,
        tenant_id=tenant_id,
        user=colleague,
        kind="COSTAY_REQUESTED",
        title="A colleague has asked to share your room",
        body=short,
        request_id=request.id,
        email_subject=f"Room sharing request - {where}",
        email_body="\n".join(
            [
                f"Hello {greeting},",
                "",
                short,
                "",
                "If this does not suit you, tell your admin before it is confirmed.",
            ]
        ),
    )


def clear_share(traveller: RequestTraveller) -> None:
    """Drop any share this traveller held, including an admin confirmation.

    Called when an edit moves the stay, because a confirmation granted against
    one set of dates says nothing about another.
    """
    traveller.room_sharing = RoomSharingChoice.NOT_OFFERED
    traveller.share_with_user_id = None
    traveller.share_confirmed_by_id = None
    traveller.share_confirmed_at = None


def live_stay(db: Session, *, user_id: int, request_id: int) -> RequestTraveller | None:
    """That person's live traveller row on that hotel request, if any."""
    return db.execute(
        select(RequestTraveller).where(
            RequestTraveller.request_id == request_id,
            RequestTraveller.user_id == user_id,
            RequestTraveller.status.in_(ACTIVE_TRAVELLER_STATUSES),
        )
    ).scalars().first()


def pair(a: RequestTraveller, b: RequestTraveller, *, confirmed_by: User | None) -> None:
    """Put two travellers in one room, each pointing at the other.

    With `confirmed_by`, the pairing is signed off (an admin allotting it);
    without, it is a requester's ask that still waits for an admin.
    Strictly verifies same-gender pairing (SOW Section 7).
    """
    from app.models.base import naive_utcnow

    gender_a = a.user.gender if a.user else None
    gender_b = b.user.gender if b.user else None
    if not may_share_room(gender_a, gender_b):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Shared rooms are strictly for colleagues of the same gender (Male with Male, Female with Female).",
        )

    for mine, theirs in ((a, b), (b, a)):
        mine.room_sharing = RoomSharingChoice.SHARE_EXISTING
        mine.share_with_user_id = theirs.user_id
        mine.share_confirmed_by_id = confirmed_by.id if confirmed_by else None
        mine.share_confirmed_at = naive_utcnow() if confirmed_by else None


def notify_shared_room(
    db: Session, *, tenant_id: str, traveller: RequestTraveller, partner: User,
    request: TravelRequest, admin: User,
) -> list:
    """Tell one traveller who they will share a room with. Returns the rows."""
    person = traveller.user
    if person is None:
        return []
    where = request.hotel_city or "the hotel"
    when = f"{request.check_in:%d %b} to {request.check_out:%d %b %Y}" if request.check_out else f"from {request.check_in:%d %b %Y}"
    short = (
        f"You will share a room with {partner.full_name} in {where}, {when}. "
        f"Arranged by {admin.full_name}."
    )
    return notifications.notify(
        db,
        tenant_id=tenant_id,
        user=person,
        kind="COSTAY_CONFIRMED",
        title=f"Shared room with {partner.full_name}",
        body=short,
        request_id=request.id,
        email_subject=f"Room sharing confirmed - {where}",
        email_body=f"Hello {person.full_name.split()[0]},\n\n{short}\n\nTell your admin if this does not work for you.",
        deliver_now=False,
    )

