"""
Domain vocabulary for the whole system.

The SOW describes a single status per request, but selective approvals mean the
real decision unit is the individual traveller. So status lives on the traveller
row and the request-level value is derived from its travellers - see
`derive_request_status` below.
"""
from enum import StrEnum


class Role(StrEnum):
    """What a user may do. Orthogonal to Designation."""

    SUPER_ADMIN = "SUPER_ADMIN"     # everything a system admin can; approves invoices
    SYSTEM_ADMIN = "SYSTEM_ADMIN"   # user management, settings, audit log
    ADMIN = "ADMIN"                 # fulfilment: approve, book, upload tickets
    MANAGER = "MANAGER"             # their own team: members (admin-approved), campaigns
    GROUND_STAFF = "GROUND_STAFF"   # raise and edit own requests


#: Seniority. Someone may grant, or manage the account of, only a role at or
#: below their own - so no admin can promote themselves past the tier that
#: governs accounts, and nobody below the top can touch the top.
ROLE_RANK: dict[Role, int] = {
    Role.GROUND_STAFF: 0,
    Role.MANAGER: 1,
    Role.ADMIN: 2,
    Role.SYSTEM_ADMIN: 3,
    Role.SUPER_ADMIN: 4,
}

#: Who works the queue and sees everything, costs included.
ADMIN_ROLES = frozenset({Role.ADMIN, Role.SYSTEM_ADMIN, Role.SUPER_ADMIN})

#: Who manages accounts at the top tier. The last active one is protected.
ACCOUNT_ROLES = frozenset({Role.SYSTEM_ADMIN, Role.SUPER_ADMIN})


class TeamChangeKind(StrEnum):
    """What a manager asked to do to their team. An admin approves each one."""

    ADD = "ADD"
    EDIT = "EDIT"
    REMOVE = "REMOVE"


class TeamChangeStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"   # withdrawn by the manager before a decision


class Designation(StrEnum):
    """Where someone sits in the field hierarchy. Reporting only in V1;
    approval routing on designation is explicitly out of scope."""

    EXECUTIVE = "EXECUTIVE"
    TEAM_LEAD = "TEAM_LEAD"
    MANAGER = "MANAGER"


class Gender(StrEnum):
    """Drives the room-sharing policy. Anything that is not an exact match
    between two people falls back to separate rooms.

    Only MALE and FEMALE can be chosen now (SELECTABLE_GENDERS). OTHER and
    UNDISCLOSED stay so rows saved before that rule still load; an admin sets
    a real value the next time they edit the person."""

    MALE = "MALE"
    FEMALE = "FEMALE"
    OTHER = "OTHER"
    UNDISCLOSED = "UNDISCLOSED"


#: What a person can be recorded as, on any form or import.
SELECTABLE_GENDERS = frozenset({Gender.MALE, Gender.FEMALE})


class UserStatus(StrEnum):
    """Where someone stands with the organisation. Only ACTIVE can sign in;
    `User.is_active` mirrors that, so queries that filter on it keep working."""

    ACTIVE = "ACTIVE"            # can sign in
    DEACTIVATED = "DEACTIVATED"  # suspended or on leave; reactivate any time
    LEFT = "LEFT"                # left the organisation; exited_on is set
    DELETED = "DELETED"          # removed from the team list; history is kept


class ProjectStatus(StrEnum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"   # hidden from request dropdowns, history preserved


class RequestType(StrEnum):
    LONG_DISTANCE = "LONG_DISTANCE"   # flight / train / bus
    LOCAL_CAB = "LOCAL_CAB"
    HOTEL = "HOTEL"


class TravelMode(StrEnum):
    FLIGHT = "FLIGHT"
    TRAIN = "TRAIN"
    BUS = "BUS"
    CAB = "CAB"


class CabType(StrEnum):
    """The car a cab request asks for, and the one an admin records as sent.

    Named by size rather than model so a vendor swapping a Dzire for an Aura
    does not need a new value; the label carries the model staff know.
    NO_PREFERENCE is only ever asked for - an admin always records a real car.
    """

    NO_PREFERENCE = "NO_PREFERENCE"
    SEDAN = "SEDAN"   # Dzire, 4 seats
    SUV = "SUV"       # Ertiga, 7 seats


CAB_TYPE_LABELS: dict[CabType, str] = {
    CabType.NO_PREFERENCE: "No preference",
    CabType.SEDAN: "Dzire (4 seats)",
    CabType.SUV: "Ertiga (7 seats)",
}


class CabTrip(StrEnum):
    """How far a cab goes. Vendors price local and outstation trips differently,
    so the admin booking it needs to know which this is before calling one."""

    LOCAL = "LOCAL"
    OUTSTATION = "OUTSTATION"


#: The one place the local/outstation line is drawn. A trip under this many
#: kilometres is local; at or over it, outstation, with a distance the
#: requester estimates.
LOCAL_CAB_MAX_KM = 80

#: Further than any road trip in the country. A typo beyond it ("25000") is a
#: slip, not a journey.
MAX_CAB_DISTANCE_KM = 5000

CAB_TRIP_LABELS: dict[CabTrip, str] = {
    CabTrip.LOCAL: f"Local (within {LOCAL_CAB_MAX_KM} km)",
    CabTrip.OUTSTATION: "Outstation",
}


class CancellationStatus(StrEnum):
    """An ask to cancel a trip someone has already approved or booked. Until
    an admin or the requester's manager approves it, the trip stands."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CabExtensionStatus(StrEnum):
    """Where an ask to keep a booked cab one more day stands. One at a time per
    request; after a decision the traveller may ask again for another day."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RequestPriority(StrEnum):
    """How soon the requester needs a decision. Set by them, read by admins."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: Sort order for queues: high first.
PRIORITY_RANK = {RequestPriority.HIGH: 0, RequestPriority.MEDIUM: 1, RequestPriority.LOW: 2}


class RequestStatus(StrEnum):
    """Derived from the traveller rows; never set directly by a caller."""

    DRAFT = "DRAFT"                             # not yet visible to admins
    SUBMITTED = "SUBMITTED"                     # in the queue, still editable
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"   # some travellers decided
    APPROVED = "APPROVED"                       # all approved, no tickets yet
    BOOKED = "BOOKED"                           # tickets attached and confirmed
    REJECTED = "REJECTED"                       # every traveller rejected
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"                         # travel date passed while pending


class TravellerStatus(StrEnum):
    """The real decision unit. One row per person on a request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    BOOKED = "BOOKED"
    CANCELLED = "CANCELLED"


#: Once any traveller leaves PENDING the request is locked against edits.
#: This is the "editable until first admin action" rule.
_DECIDED = {TravellerStatus.APPROVED, TravellerStatus.REJECTED, TravellerStatus.BOOKED}

#: What an admin may do to a traveller row next. A decision is not undoable in
#: V1 - correcting one goes through cancel-and-reraise, same as an edit after the
#: lock - so every decided state leads only to CANCELLED.
ALLOWED_TRAVELLER_TRANSITIONS: dict[TravellerStatus, set[TravellerStatus]] = {
    TravellerStatus.PENDING: {
        TravellerStatus.APPROVED,
        TravellerStatus.REJECTED,
        TravellerStatus.CANCELLED,
    },
    # Booking is a second step, deliberately: approved means the trip is
    # sanctioned, booked means a ticket exists. See addendum B2.
    TravellerStatus.APPROVED: {TravellerStatus.BOOKED, TravellerStatus.CANCELLED},
    TravellerStatus.BOOKED: {TravellerStatus.CANCELLED},
    TravellerStatus.REJECTED: {TravellerStatus.CANCELLED},
    TravellerStatus.CANCELLED: set(),
}


#: Statuses that occupy a person's calendar for conflict detection.
#: DRAFT and anything rejected or cancelled never blocks a new request.
ACTIVE_TRAVELLER_STATUSES = {
    TravellerStatus.PENDING,
    TravellerStatus.APPROVED,
    TravellerStatus.BOOKED,
}


class ManagerRecommendation(StrEnum):
    """What a traveller's manager said about their trip, before an admin decides.

    Advice, not a decision: the admin is the final authority and may decide
    before the manager answers, or against what they said. Held per traveller,
    like status, because on a group request each person may have a different
    manager.
    """

    RECOMMENDED = "RECOMMENDED"
    NOT_RECOMMENDED = "NOT_RECOMMENDED"


class RoomSharingChoice(StrEnum):
    """What the requester picked when the system offered a co-stay."""

    NOT_OFFERED = "NOT_OFFERED"
    SHARE_EXISTING = "SHARE_EXISTING"
    SEPARATE_ROOM = "SEPARATE_ROOM"
    SEPARATE_HOTEL = "SEPARATE_HOTEL"


class ConflictKind(StrEnum):
    OVERLAPPING_TRAVEL = "OVERLAPPING_TRAVEL"
    OVERLAPPING_STAY = "OVERLAPPING_STAY"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"


class ConflictSeverity(StrEnum):
    """Conflicts warn rather than block. An admin may approve anyway, but only
    with a typed reason that lands in the audit log."""

    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


class AuditAction(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    CANCEL = "CANCEL"
    BOOK = "BOOK"
    UPLOAD = "UPLOAD"
    EXTRACT = "EXTRACT"
    NOTIFY = "NOTIFY"
    OVERRIDE_CONFLICT = "OVERRIDE_CONFLICT"
    RECOMMEND = "RECOMMEND"             # a manager's advice on a team member's trip
    VIEW_SENSITIVE = "VIEW_SENSITIVE"   # ID proof opened - PII access trail
    EXPORT = "EXPORT"                   # a document left the system, e.g. an invoice CSV


class VendorKind(StrEnum):
    """Who an organisation pays for travel. Reporting only: any vendor can be
    recorded against any trip, because a travel agent books hotels too."""

    TRAVEL_AGENT = "TRAVEL_AGENT"
    CAB = "CAB"
    HOTEL = "HOTEL"
    OTHER = "OTHER"


class InvoiceStatus(StrEnum):
    """Where a vendor's invoice stands. Admins prepare it (DRAFT), send it for
    approval (SUBMITTED), and only a super admin decides it. A rejected one goes
    back to the admins to fix and send again; an approved one never changes.
    """

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


#: Invoice states an admin may still edit. Everything but approved: editing a
#: rejected one is how it gets fixed, and editing a submitted one is allowed
#: (the super admins are told it changed) rather than forcing a reject first.
EDITABLE_INVOICE_STATUSES = frozenset(
    {InvoiceStatus.DRAFT, InvoiceStatus.SUBMITTED, InvoiceStatus.REJECTED}
)


class IdProofType(StrEnum):
    """Government identity documents ground staff travel on.

    The number itself is encrypted at rest and only ever decrypted through an
    endpoint that writes a VIEW_SENSITIVE audit row - see addendum B8.
    """

    AADHAAR = "AADHAAR"
    PAN = "PAN"
    PASSPORT = "PASSPORT"
    DRIVING_LICENCE = "DRIVING_LICENCE"
    VOTER_ID = "VOTER_ID"
    OTHER = "OTHER"


class TokenPurpose(StrEnum):
    """Single-use token flavours. Both share one table and one redemption path."""

    INVITE = "INVITE"                   # set your first password
    PASSWORD_RESET = "PASSWORD_RESET"   # set a replacement password


class NotificationChannel(StrEnum):
    """How a notice reaches someone.

    `IN_APP` is always written and always delivered - it is a row. Everything
    else is a transport that can refuse. Adding SMS is a value here plus a sender
    in `notifications.SENDERS`, which is what addendum C3 means by
    channel-agnostic.
    """

    EMAIL = "EMAIL"
    IN_APP = "IN_APP"


class NotificationCategory(StrEnum):
    """What a notice is *about*, so people can turn off the parts they do not
    want without losing the parts they need.

    Coarse on purpose. Per-event opt-outs would be a settings screen nobody
    reads, and the four groups below are the ones staff actually distinguish.
    """

    DECISIONS = "DECISIONS"       # your request was approved or rejected
    BOOKINGS = "BOOKINGS"         # tickets confirmed, references issued
    ROOM_SHARING = "ROOM_SHARING"  # a colleague asked to share your room
    REMINDERS = "REMINDERS"       # nudges: travel coming up, requests going stale
    NEW_REQUESTS = "NEW_REQUESTS"  # admins and managers: a request needs their answer


#: Which category each notification kind belongs to. A kind that is missing here
#: is treated as a DECISION - the safest default, because decisions are the
#: notices someone would most regret not receiving.
NOTIFICATION_CATEGORIES: dict[str, NotificationCategory] = {
    "REQUEST_APPROVED": NotificationCategory.DECISIONS,
    "REQUEST_REJECTED": NotificationCategory.DECISIONS,
    "REQUEST_CANCELLED": NotificationCategory.DECISIONS,
    "REQUEST_BOOKED": NotificationCategory.BOOKINGS,
    "BOOKING_CONFIRMED": NotificationCategory.BOOKINGS,
    "COSTAY_REQUESTED": NotificationCategory.ROOM_SHARING,
    # A confirmed shared room is a decision about where someone sleeps.
    "COSTAY_CONFIRMED": NotificationCategory.DECISIONS,
    "TRAVEL_REMINDER": NotificationCategory.REMINDERS,
    "REQUEST_STALE": NotificationCategory.REMINDERS,
    "REQUEST_SUBMITTED": NotificationCategory.NEW_REQUESTS,
    "TEAM_REQUEST_SUBMITTED": NotificationCategory.NEW_REQUESTS,
    "MANAGER_RECOMMENDED": NotificationCategory.NEW_REQUESTS,
    # A manager's in-app copy of a decision on a team member's trip. The email
    # itself reaches them as a Cc on the traveller's, so this never mails.
    "DECISION_COPY": NotificationCategory.DECISIONS,
    "TEAM_CHANGE_REQUESTED": NotificationCategory.NEW_REQUESTS,
    "CANCELLATION_REQUESTED": NotificationCategory.NEW_REQUESTS,
    "CANCELLATION_APPROVED": NotificationCategory.DECISIONS,
    "CANCELLATION_REJECTED": NotificationCategory.DECISIONS,
    "TEAM_CHANGE_APPROVED": NotificationCategory.DECISIONS,
    "TEAM_CHANGE_REJECTED": NotificationCategory.DECISIONS,
    # The car, its number and the driver's phone: what a booking is for a cab.
    "CAB_DETAILS": NotificationCategory.BOOKINGS,
    "CAB_EXTENSION_REQUESTED": NotificationCategory.NEW_REQUESTS,
    "CAB_EXTENSION_APPROVED": NotificationCategory.DECISIONS,
    "CAB_EXTENSION_REJECTED": NotificationCategory.DECISIONS,
    # Vendor invoices: the super admins are asked to approve one, and told when
    # one waiting on them changes; whoever prepared it hears the decision.
    "INVOICE_SUBMITTED": NotificationCategory.NEW_REQUESTS,
    "INVOICE_CHANGED": NotificationCategory.NEW_REQUESTS,
    "INVOICE_APPROVED": NotificationCategory.DECISIONS,
    "INVOICE_REJECTED": NotificationCategory.DECISIONS,
    # Paid, or a payment taken back: the preparers' in-app notice.
    "INVOICE_PAID": NotificationCategory.DECISIONS,
}


def category_of(kind: str) -> NotificationCategory:
    return NOTIFICATION_CATEGORIES.get(kind, NotificationCategory.DECISIONS)


#: Categories a person may switch off for themselves. Decisions are deliberately
#: absent: being told your own travel was rejected is not marketing, and an
#: opt-out there would produce staff who turn up at airports.
OPTIONAL_CATEGORIES = {
    NotificationCategory.BOOKINGS,
    NotificationCategory.ROOM_SHARING,
    NotificationCategory.REMINDERS,
    NotificationCategory.NEW_REQUESTS,
}

#: Categories only someone who answers requests receives - admins deciding
#: them, managers recommending their team's - so only they are offered the
#: switch.
APPROVER_CATEGORIES = {NotificationCategory.NEW_REQUESTS}


class NotificationStatus(StrEnum):
    QUEUED = "QUEUED"
    SENT = "SENT"
    FAILED = "FAILED"
    READ = "READ"
    #: Deliberately not delivered - the channel is switched off, or the address
    #: is outside the development allowlist. Distinct from FAILED, which means
    #: we tried and the server said no.
    SUPPRESSED = "SUPPRESSED"


class TicketStatus(StrEnum):
    """Where an uploaded ticket is in the extract-review-confirm flow.

    The SOW has upload set a request to Booked immediately. One model misparse
    would then book a wrong PNR and email it out, so a human sits in the middle -
    see addendum B3. Nothing reaches a traveller before CONFIRMED.
    """

    UPLOADED = "UPLOADED"       # on disk, not yet read
    EXTRACTING = "EXTRACTING"   # handed to the model
    EXTRACTED = "EXTRACTED"     # fields proposed, awaiting a human
    CONFIRMED = "CONFIRMED"     # an admin accepted it; the traveller is booked
    FAILED = "FAILED"           # the model could not be reached or made no sense
    DISCARDED = "DISCARDED"     # wrong file, replaced by another upload


def derive_request_status(
    traveller_statuses: list[TravellerStatus],
    *,
    is_draft: bool = False,
    is_cancelled: bool = False,
    travel_date_passed: bool = False,
) -> RequestStatus:
    """Collapse per-traveller statuses into the one value shown on a request.

    Ordering matters: an explicit draft or cancellation wins over anything the
    travellers say, then the fully-settled cases, then the mixed case.

    `travel_date_passed` is the only input that is not a traveller status, and it
    produces `EXPIRED` in exactly one situation: the date has gone and **nobody**
    was decided. A request where some travellers were approved and one was missed
    keeps `PARTIALLY_APPROVED`, because that says more about what happened than
    "expired" does.
    """
    if is_draft:
        return RequestStatus.DRAFT
    if is_cancelled:
        return RequestStatus.CANCELLED
    if not traveller_statuses:
        return RequestStatus.EXPIRED if travel_date_passed else RequestStatus.SUBMITTED

    live = [s for s in traveller_statuses if s is not TravellerStatus.CANCELLED]
    if not live:
        return RequestStatus.CANCELLED
    if travel_date_passed and all(s is TravellerStatus.PENDING for s in live):
        return RequestStatus.EXPIRED
    if all(s is TravellerStatus.REJECTED for s in live):
        return RequestStatus.REJECTED
    if all(s is TravellerStatus.BOOKED for s in live):
        return RequestStatus.BOOKED

    settled = [s for s in live if s is not TravellerStatus.PENDING]
    if not settled:
        return RequestStatus.SUBMITTED
    if len(settled) < len(live):
        return RequestStatus.PARTIALLY_APPROVED

    # Everyone is decided: approved/booked mix, with rejections allowed.
    if any(s is TravellerStatus.BOOKED for s in live):
        return RequestStatus.PARTIALLY_APPROVED
    return RequestStatus.APPROVED


def is_editable(
    traveller_statuses: list[TravellerStatus],
    *,
    is_draft: bool = False,
    is_cancelled: bool = False,
) -> bool:
    """A request stays editable until an admin acts on any traveller.

    A draft always is. A cancelled one never is - including the case where every
    traveller has dropped out, which leaves nothing to edit even though no admin
    ever approved or rejected anyone.
    """
    if is_cancelled:
        return False
    if is_draft:
        return True
    if traveller_statuses and all(s is TravellerStatus.CANCELLED for s in traveller_statuses):
        return False
    return not any(s in _DECIDED for s in traveller_statuses)
