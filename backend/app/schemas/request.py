"""Request and response bodies for travel, cab and hotel requests."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import (
    CabExtensionStatus,
    CabTrip,
    CabType,
    CancellationStatus,
    ConflictKind,
    ConflictSeverity,
    Designation,
    InvoiceStatus,
    LOCAL_CAB_MAX_KM,
    MAX_CAB_DISTANCE_KM,
    ManagerRecommendation,
    RequestPriority,
    RequestStatus,
    RequestType,
    RoomSharingChoice,
    TravelMode,
    TravellerStatus,
)
from app.schemas.common import UTCInstant
from app.schemas.user import MobileNumber, PersonName

#: Which fields each request type actually uses. Also the list the revision diff
#: is taken over, so a field absent here is a field no one can amend.
EDITABLE_FIELDS = (
    "project_id",
    "origin_state",
    "destination_state",
    "hotel_state",
    "other_project_name",
    "travel_reason",
    "priority",
    "mode",
    "origin",
    "destination",
    "pickup_city",
    "drop_city",
    "start_at",
    "end_at",
    "hotel_city",
    "check_in",
    "check_out",
    "cab_type",
    "cab_trip",
    "cab_distance_km",
    "notes",
)


class RequestBody(BaseModel):
    """Shared shape of a create or a full edit.

    Type-specific validation lives here rather than in the router so a draft, a
    submission and an amendment are all held to the same rules.
    """

    request_type: RequestType
    project_id: int
    traveller_ids: list[int] = Field(default_factory=list)

    mode: TravelMode | None = None
    #: Captured alongside the city so deployment can be grouped by state
    #: without a join per row.
    origin_state: str | None = Field(default=None, max_length=80)
    destination_state: str | None = Field(default=None, max_length=80)
    hotel_state: str | None = Field(default=None, max_length=80)

    origin: str | None = Field(default=None, max_length=160)
    destination: str | None = Field(default=None, max_length=160)
    #: A cab's city or constituency, beside its street address. The state is
    #: origin_state / destination_state.
    pickup_city: str | None = Field(default=None, max_length=120)
    drop_city: str | None = Field(default=None, max_length=120)
    start_at: datetime | None = None
    end_at: datetime | None = None

    hotel_city: str | None = Field(default=None, max_length=120)
    check_in: date | None = None
    check_out: date | None = None

    #: A cab's size, local or outstation, and - outstation - roughly how far.
    #: Defaulted for a cab and cleared for anything else by the validator.
    cab_type: CabType | None = None
    cab_trip: CabTrip | None = None
    cab_distance_km: int | None = None

    #: Mandatory. An admin deciding on a trip needs to know what it is for,
    #: and "because I was asked to" in a free-text note was not reliably there.
    travel_reason: str = Field(min_length=5, max_length=500)

    #: How soon the requester needs a decision. Defaults to MEDIUM so an older
    #: client that never sends it still gets the honest middle answer.
    priority: RequestPriority = RequestPriority.MEDIUM

    #: Only when the chosen campaign is the fallback "Other" one. Validated in
    #: the service, which is where the project is actually looked up.
    other_project_name: str | None = Field(default=None, max_length=160)

    notes: str | None = Field(default=None, max_length=2000)
    is_draft: bool = False

    @model_validator(mode="after")
    def _shape_matches_type(self):
        if self.request_type is RequestType.HOTEL:
            if not self.hotel_city:
                raise ValueError("A hotel request needs a city.")
            if self.check_in is None:
                raise ValueError("A hotel request needs a check-in date.")
            if self.check_out is not None and self.check_out <= self.check_in:
                raise ValueError("Check-out must be after check-in.")
            # A stay has no route; carrying one would confuse the conflict rules,
            # which decide by shape rather than by request type alone.
            self.mode = None
            self.origin = self.destination = None
            self.origin_state = self.destination_state = None
            self.pickup_city = self.drop_city = None
            self.start_at = self.end_at = None
            self.cab_type = self.cab_trip = self.cab_distance_km = None
        else:
            if not self.origin:
                raise ValueError("A travel request needs a pickup or origin.")
            if not self.destination:
                raise ValueError("A travel request needs a drop or destination.")
            if self.start_at is None:
                raise ValueError("A travel request needs a departure date and time.")
            if self.end_at is not None and self.end_at < self.start_at:
                raise ValueError("Arrival cannot be before departure.")
            if self.request_type is RequestType.LOCAL_CAB:
                self.mode = TravelMode.CAB
                if not (self.origin_state and self.pickup_city):
                    raise ValueError("Pick the state and city the cab picks up in.")
                if not (self.destination_state and self.drop_city):
                    raise ValueError("Pick the state and city the cab drops in.")
                self._check_cab_distance()
            else:
                if self.mode is None or self.mode is TravelMode.CAB:
                    raise ValueError("Choose flight, train or bus for a long-distance request.")
                self.pickup_city = self.drop_city = None
                self.cab_type = self.cab_trip = self.cab_distance_km = None
            self.hotel_city = self.hotel_state = None
            self.check_in = self.check_out = None

        # Naive datetimes throughout: these are wall-clock values, and a browser
        # that helpfully appends a zone must not shift someone's 06:00 flight.
        for field in ("start_at", "end_at"):
            value = getattr(self, field)
            if value is not None and value.tzinfo is not None:
                object.__setattr__(self, field, value.replace(tzinfo=None))
        return self

    def _check_cab_distance(self) -> None:
        """Local is within LOCAL_CAB_MAX_KM and needs no distance; outstation
        needs the requester's estimate, so the admin can quote it to a vendor.

        An unset size or trip means the common case - any car, in town - so an
        older client that never sends them still raises a valid cab.
        """
        self.cab_type = self.cab_type or CabType.NO_PREFERENCE
        self.cab_trip = self.cab_trip or CabTrip.LOCAL
        km = self.cab_distance_km
        if km is not None and km < 1:
            raise ValueError("Enter the distance in whole kilometres, 1 or more.")
        if self.cab_trip is CabTrip.LOCAL:
            if km is not None and km >= LOCAL_CAB_MAX_KM:
                raise ValueError(
                    f"A local cab stays within {LOCAL_CAB_MAX_KM} km. "
                    "Choose Outstation for a longer trip."
                )
        elif km is None or not LOCAL_CAB_MAX_KM <= km <= MAX_CAB_DISTANCE_KM:
            raise ValueError(
                f"An outstation cab needs the approximate distance: {LOCAL_CAB_MAX_KM} to "
                f"{MAX_CAB_DISTANCE_KM} km. Under {LOCAL_CAB_MAX_KM} km, choose Local."
            )


class RequestCreate(RequestBody):
    #: Hotel only: the requester's own room choice, made in the form from the
    #: colleagues offered there. SHARE_EXISTING is an ask an admin confirms.
    room_sharing: RoomSharingChoice | None = None
    share_with_user_id: int | None = None

    @model_validator(mode="after")
    def _room_choice_fits(self):
        if self.room_sharing in (None, RoomSharingChoice.NOT_OFFERED):
            self.room_sharing = None
            self.share_with_user_id = None
            return self
        if self.request_type is not RequestType.HOTEL:
            raise ValueError("A room choice only goes with a hotel request.")
        if self.room_sharing is RoomSharingChoice.SHARE_EXISTING and self.share_with_user_id is None:
            raise ValueError("Choosing to share needs the colleague to share with.")
        if self.room_sharing is not RoomSharingChoice.SHARE_EXISTING:
            self.share_with_user_id = None
        return self


class RequestEdit(RequestBody):
    """A full replacement. The diff is computed server-side against what is
    stored, so a partial PATCH would make "what changed" ambiguous."""


class RoomSharingChoicePayload(BaseModel):
    traveller_id: int
    choice: RoomSharingChoice
    share_with_user_id: int | None = None

    @model_validator(mode="after")
    def _share_needs_a_person(self):
        if self.choice is RoomSharingChoice.SHARE_EXISTING and self.share_with_user_id is None:
            raise ValueError("Choosing to share needs the colleague to share with.")
        if self.choice is not RoomSharingChoice.SHARE_EXISTING:
            self.share_with_user_id = None
        return self


class RoomAllotPayload(BaseModel):
    """An admin's room decision for one hotel traveller: share with this
    colleague (both are paired and told), or `null` for a room of their own."""

    share_with_user_id: int | None = None


class CancelPayload(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class TravellerRead(BaseModel):
    id: int
    user_id: int
    full_name: str
    email: str
    designation: Designation | None = None
    status: TravellerStatus
    is_requester: bool
    room_sharing: RoomSharingChoice
    share_with_user_id: int | None = None
    share_with_name: str | None = None
    share_confirmed: bool = False
    #: Admins only, on hotel stays: same-gender colleagues staying in the same
    #: city on overlapping nights, who this traveller could be put in a room with.
    room_matches: list["CoStayMatchRead"] = Field(default_factory=list)
    #: A confirmed ticket file is there to download (My requests).
    ticket_ready: bool = False

    # --- the decision, once one has been taken (Phase 4) -------------------
    decided_by_name: str | None = None
    decided_at: UTCInstant | None = None
    decision_reason: str | None = None
    booking_reference: str | None = None
    booking_details: dict | None = None
    #: The uploaded ticket to open from this row (GET /tickets/{id}/file):
    #: the confirmed one, else the newest under review. On the admin queue list only.
    ticket_id: int | None = None
    #: Every file sent with this person's booking - a booking can carry
    #: several (outward and return, a voucher and its invoice). An admin sees
    #: the ones still under review too; the traveller and whoever raised the
    #: trip see the confirmed ones, which they can download.
    ticket_files: list["TicketFileRead"] = Field(default_factory=list)

    # --- the manager's view, before an admin decides (two-level approval) ----
    #: Who this traveller reports to, if that manager's account is active.
    #: Shown to anyone who can see the request.
    manager_id: int | None = None
    manager_name: str | None = None
    #: What the manager said. Only an admin, or this traveller's own manager,
    #: is shown it; everyone else - the traveller included - reads None.
    manager_recommendation: ManagerRecommendation | None = None
    manager_comment: str | None = None
    manager_reviewed_at: UTCInstant | None = None
    manager_reviewed_by_name: str | None = None

    # --- cost (SOW 2 and 6, addendum C1). Admin-only; see the router. -------
    cost_amount: Decimal | None = None
    cost_currency: str | None = None
    cost_note: str | None = None
    cost_entered_by_name: str | None = None
    #: Who was paid for this person's trip, and the invoice it is billed on.
    #: Admin-only, like cost. A cost on an approved invoice is locked.
    vendor_id: int | None = None
    vendor_name: str | None = None
    invoice_id: int | None = None
    invoice_number: str | None = None
    invoice_status: InvoiceStatus | None = None


class ConflictRead(BaseModel):
    user_id: int
    user_name: str
    kind: ConflictKind
    severity: ConflictSeverity
    message: str
    other_request_id: int | None = None
    other_request_type: RequestType | None = None
    other_summary: str | None = None


class CoStayMatchRead(BaseModel):
    """What the requester is shown about a colleague already in that city.

    Name and designation, per the C2 assumption - see `services/costay.py`.
    """

    user_id: int
    full_name: str
    designation: Designation | None = None
    request_id: int
    hotel_city: str
    check_in: date
    check_out: date | None = None
    overlapping_nights: int
    status: str


class TicketFileRead(BaseModel):
    """One file on a traveller's booking, as a row shows it."""

    id: int
    file_name: str | None = None
    file_size: int | None = None
    #: Booked with it, and so downloadable by the traveller.
    confirmed: bool = False


# TravellerRead lists these, and is declared first.
TravellerRead.model_rebuild()


class RevisionRead(BaseModel):
    revision_number: int
    editor_name: str | None = None
    created_at: UTCInstant
    summary: str
    changes: dict[str, dict] | None = None


class RequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_type: RequestType
    status: RequestStatus
    is_editable: bool
    is_draft: bool
    is_cancelled: bool
    cancel_reason: str | None = None

    project_id: int
    project_name: str
    project_code: str

    requester_id: int
    requester_name: str

    mode: TravelMode | None = None
    origin: str | None = None
    destination: str | None = None
    pickup_city: str | None = None
    drop_city: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None

    hotel_city: str | None = None
    check_in: date | None = None
    check_out: date | None = None

    # --- cab: asked for, sent, and extended. Null on anything but a cab. -----
    cab_type: CabType | None = None
    cab_trip: CabTrip | None = None
    cab_distance_km: int | None = None
    #: The car an admin recorded as sent, with its number and driver. Shown to
    #: everyone who can see the request: the travellers need it to find the car.
    booked_cab_type: CabType | None = None
    cab_vehicle_number: str | None = None
    cab_driver_name: str | None = None
    cab_driver_phone: str | None = None
    cab_booked_by_name: str | None = None
    cab_booked_at: UTCInstant | None = None
    #: An ask to cancel a trip already approved or booked, and its answer.
    cancellation_status: CancellationStatus | None = None
    cancellation_reason: str | None = None
    cancellation_requested_by_name: str | None = None
    cancellation_requested_at: UTCInstant | None = None
    cancellation_decided_by_name: str | None = None
    cancellation_decided_at: UTCInstant | None = None
    cancellation_comment: str | None = None
    #: Whether the reader's cancel goes straight through or becomes an ask, and
    #: whether they may answer a pending ask - so the screen offers the right button.
    cancel_needs_approval: bool = False
    can_decide_cancellation: bool = False
    cancelled_by_name: str | None = None
    cab_extension_status: CabExtensionStatus | None = None
    cab_extension_reason: str | None = None
    cab_extension_requested_by_name: str | None = None
    cab_extension_requested_at: UTCInstant | None = None
    cab_extension_decided_by_name: str | None = None
    cab_extension_decided_at: UTCInstant | None = None
    cab_extension_comment: str | None = None
    #: How many extra days the older "one more day" ask added; end_at already
    #: includes them.
    cab_extended_days: int = 0

    # --- extensions: a cab kept longer, a stay made longer -------------------
    #: The trip this one carries on, when it is an extension.
    extends_request_id: int | None = None
    #: How that trip was booked - the car and driver, or the hotel - so the
    #: admin booking this one sees it, and can book it the same way.
    previous_booking: str | None = None
    #: The extension carrying this trip on, while one is live (not cancelled
    #: or turned down). Extend that one to go further.
    extended_by_request_id: int | None = None
    #: Whether the person reading may extend this trip now. Worked out here
    #: so the screen and POST /extend apply the same rule.
    can_extend: bool = False
    #: While they may: the last day they can, the day the ride or stay ends.
    #: Until midnight that day (India time); after it, extra days are a new
    #: request.
    extend_until: date | None = None

    travel_reason: str | None = None
    priority: RequestPriority = RequestPriority.MEDIUM
    other_project_name: str | None = None
    origin_state: str | None = None
    destination_state: str | None = None
    hotel_state: str | None = None
    notes: str | None = None
    submitted_at: UTCInstant | None = None
    created_at: UTCInstant
    updated_at: UTCInstant

    travellers: list[TravellerRead]
    #: How many amendments have been saved since submission. The admin queue
    #: shows this as "edited N times" - addendum A1.
    edit_count: int = 0
    conflicts: list[ConflictRead] = Field(default_factory=list)
    costay_matches: list[CoStayMatchRead] = Field(default_factory=list)

    #: True once every traveller has been decided one way or the other. The
    #: admin queue uses it to drop a request out of the "awaiting" tab.
    is_decided: bool = False


class RequestListResponse(BaseModel):
    items: list[RequestRead]
    total: int
    page: int
    page_size: int


class QueueExport(BaseModel):
    """Every request in one admin queue tab, for the CSV export.

    Not paged: an export that silently stops at page one is worse than none.
    Capped instead, with `truncated` saying so, the same way the travel log is.
    """

    status: RequestStatus
    total: int
    truncated: bool
    items: list[RequestRead]


class ConflictCheckRequest(RequestBody):
    """A dry run of the conflict and co-stay checks, before anything is saved.

    The reason plays no part in finding a clash, so a form checked before its
    reason is typed is answered rather than refused. The real write still
    insists on one.
    """

    request_id: int | None = None
    travel_reason: str = Field(default="", max_length=500)


class ConflictCheckResponse(BaseModel):
    conflicts: list[ConflictRead]
    costay_matches: list[CoStayMatchRead]


class ColleagueRead(BaseModel):
    """A person as the co-traveller picker sees them.

    Deliberately thin. Ground staff cannot read the employee directory, and a
    picker needs a name to choose from - not an email, a phone number or an
    account state. Gender is absent too: the co-stay matcher needs it, the person
    choosing a colleague does not.
    """

    id: int
    full_name: str
    designation: Designation | None = None


# --- Phase 4: admin decisions ---------------------------------------------


class BookingDetails(BaseModel):
    """What the traveller needs on the day, beside the booking reference. All
    optional: a train has a coach and berth, a hotel an address, and an admin
    fills what the ticket gives them."""

    carrier: str | None = Field(default=None, max_length=120)          # airline, railway, bus operator
    service_number: str | None = Field(default=None, max_length=60)    # 6E-4412, 12723, ...
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    seat: str | None = Field(default=None, max_length=60)              # seat, coach / berth
    hotel_name: str | None = Field(default=None, max_length=160)
    hotel_address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=300)            # check-in time, terminal, ...

    @model_validator(mode="after")
    def _tidy(self):
        for name in ("carrier", "service_number", "seat", "hotel_name", "hotel_address", "notes"):
            value = getattr(self, name)
            setattr(self, name, " ".join(value.split()) or None if value else None)
        for name in ("depart_at", "arrive_at"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is not None:
                # Trip times are India wall-clock time, like start_at.
                setattr(self, name, value.replace(tzinfo=None))
        if self.depart_at and self.arrive_at and self.arrive_at < self.depart_at:
            raise ValueError("Arrival cannot be before departure.")
        return self

    def stored(self) -> dict | None:
        """The non-empty fields, as the JSON column keeps them."""
        data = {k: v for k, v in self.model_dump(mode="json").items() if v not in (None, "")}
        return data or None


class DecisionPayload(BaseModel):
    """One admin decision on one traveller.

    `reason` is mandatory on every decision - an approval as much as a
    rejection. An approval with no stated reason is the one most likely to be
    questioned months later, and "it was approved" is not an answer.

    `conflict_override_reason` is mandatory when approving someone who has a live
    clash, and is recorded separately as OVERRIDE_CONFLICT (addendum B6).

    `notify_employee` lets the admin suppress the email - a decision taken while
    the traveller is standing next to them does not need one, and a batch being
    tidied up retrospectively should not fill inboxes. The in-app notice and the
    ledger entry are written regardless: the record is not optional, only the
    email is.
    """

    to_status: TravellerStatus
    reason: str | None = Field(default=None, max_length=500)
    booking_reference: str | None = Field(default=None, max_length=120)
    #: Only when marking booked: flight/train/bus or hotel details.
    booking_details: BookingDetails | None = None
    conflict_override_reason: str | None = Field(default=None, max_length=500)
    notify_employee: bool = True
    #: Only when marking booked: the uploaded ticket the booking is from. It is
    #: confirmed with the booking and attached to the traveller's email.
    ticket_id: int | None = None

    @model_validator(mode="after")
    def _target_is_a_decision(self):
        if self.to_status is TravellerStatus.PENDING:
            raise ValueError("A decision cannot put a traveller back to pending.")
        if self.to_status is not TravellerStatus.BOOKED:
            self.ticket_id = None
        return self


class BatchDecisionItem(DecisionPayload):
    traveller_id: int


class BatchDecisionPayload(BaseModel):
    """Several travellers on one request, decided in one transaction.

    This is what partial approval looks like from the admin queue: tick three
    people, reject the fourth, press once. Either the whole set lands or none of
    it does, so the queue can never show a half-applied decision.
    """

    decisions: list[BatchDecisionItem] = Field(min_length=1)


class RecommendationPayload(BaseModel):
    """A manager's advice on their team members' part of one request.

    `traveller_ids` are traveller rows on the request (the same ids decisions
    use). Left out, it covers every member of the manager's team on the
    request who is still pending - the usual case, one person on one trip.
    """

    recommendation: ManagerRecommendation
    comment: str = Field(min_length=3, max_length=500)
    traveller_ids: list[int] | None = None

    @field_validator("comment")
    @classmethod
    def _tidy_comment(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 3:
            raise ValueError("Add a comment for the admin, in a few words.")
        return cleaned


class QueueCounts(BaseModel):
    """Headline counts for the admin queue tabs."""

    awaiting: int
    partially_approved: int
    approved: int
    booked: int
    rejected: int
    cancelled: int
    expired: int
    with_conflicts: int
    edited: int
    #: HIGH-priority requests still waiting on someone (awaiting or partly
    #: approved). The queue's banner, so urgent work is not lost in a long tab.
    high_priority: int = 0
    #: The same, split by tab, so the banner opens the one the work is on.
    high_priority_awaiting: int = 0
    high_priority_partial: int = 0
    #: Requests still waiting on an admin where someone pending has a manager
    #: who has not recommended yet. For a manager, only their own team counts -
    #: so it is the number waiting on them.
    awaiting_manager: int = 0
    #: Cabs whose travellers asked to keep them one more day, still waiting on
    #: an admin. For a manager, only their own team's cabs.
    cab_extensions: int = 0
    #: Decided trips whose requester asked to cancel, waiting on an answer.
    cancellations: int = 0
    #: Extensions - a cab kept longer, a stay made longer - with someone still
    #: waiting for an admin. Time-critical: the extra day is usually tomorrow.
    extensions: int = 0


# --- Cabs: the car sent, and one more day ---------------------------------


def _tidy_text(value: str) -> str:
    return " ".join(value.split())


class CabCar(BaseModel):
    """The car an admin actually sent: which size, its number plate and driver."""

    booked_cab_type: CabType
    vehicle_number: str = Field(min_length=4, max_length=20)
    driver_name: PersonName = Field(min_length=2, max_length=120)
    driver_phone: MobileNumber = Field(max_length=32)

    @field_validator("booked_cab_type")
    @classmethod
    def _a_real_car(cls, value: CabType) -> CabType:
        if value is CabType.NO_PREFERENCE:
            raise ValueError("Choose the cab that was sent: Dzire or Ertiga.")
        return value

    @field_validator("vehicle_number")
    @classmethod
    def _plate(cls, value: str) -> str:
        """Upper case with single spaces - "ts 09  ea 1234" is "TS 09 EA 1234" -
        and at least four letters or digits, so a stray dash is not a plate."""
        cleaned = _tidy_text(value).upper()
        if sum(ch.isalnum() for ch in cleaned) < 4:
            raise ValueError("Enter the vehicle number as it is on the plate, e.g. TS 09 EA 1234.")
        return cleaned

    @model_validator(mode="after")
    def _phone_is_required(self):
        if not self.driver_phone:
            raise ValueError("Enter the driver's phone number.")
        return self


class CabBookingPayload(CabCar):
    """The car sent, recorded or changed from Cab details.

    `notify` is for a screen that records the cab while marking the traveller
    booked: the booking notice then carries these details, so one message
    reaches them rather than two.
    """

    notify: bool = True
    #: The cab operator who was paid, recorded for everyone riding (approved
    #: or booked). Left out, each keeps the vendor they had. Admin-only, like
    #: cost: the travellers are never told it.
    vendor_id: int | None = None


class CancellationDecision(BaseModel):
    """An admin's or manager's answer to an ask to cancel a decided trip. A
    rejection needs a comment: the requester is shown why the trip stands."""

    approve: bool
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reject_needs_comment(self):
        self.comment = _tidy_text(self.comment or "") or None
        if not self.approve and (self.comment is None or len(self.comment) < 3):
            raise ValueError("Add a comment saying why - the requester is shown it.")
        return self


class CabExtensionDecision(BaseModel):
    """An admin's answer to an extension. A rejection needs a comment: the
    traveller is shown it, and "no" with no reason helps nobody plan."""

    approve: bool
    comment: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _reject_needs_comment(self):
        self.comment = _tidy_text(self.comment or "") or None
        if not self.approve and (self.comment is None or len(self.comment) < 3):
            raise ValueError("Add a comment saying why - the traveller is shown it.")
        return self


# --- Extensions: a cab kept longer, a stay made longer ----------------------

#: The most days one extension may add. Past a month it is a new trip, and a
#: slip in the month is the likelier story.
MAX_EXTENSION_DAYS = 31


class ExtensionAsk(BaseModel):
    """Carry a decided cab or stay on for more days.

    A cab gives when it is wanted on the first extra day and until when; a
    stay gives its new check-out. The travellers default to everyone riding
    or staying; whoever asks must be one of them.
    """

    reason: str = Field(min_length=3, max_length=500)
    #: Traveller rows on the trip being extended. Left out: everyone on it
    #: who is approved or booked.
    traveller_ids: list[int] | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    check_out: date | None = None
    #: Left out, the trip's own priority.
    priority: RequestPriority | None = None

    @field_validator("reason")
    @classmethod
    def _tidy_reason(cls, value: str) -> str:
        cleaned = _tidy_text(value)
        if len(cleaned) < 3:
            raise ValueError("Say why it is needed for longer.")
        return cleaned

    @model_validator(mode="after")
    def _times(self):
        for field in ("start_at", "end_at"):
            value = getattr(self, field)
            if value is not None and value.tzinfo is not None:
                # Wall-clock, like every trip time.
                setattr(self, field, value.replace(tzinfo=None))
        if self.start_at and self.end_at and self.end_at < self.start_at:
            raise ValueError("The cab cannot be let go before it picks up.")
        if self.traveller_ids is not None and len(self.traveller_ids) != len(set(self.traveller_ids)):
            raise ValueError("The same traveller is named twice.")
        return self


# --- Booking: one step, everything the traveller needs -----------------------


class BookingPayload(BaseModel):
    """Book one or more approved travellers on a request in one go.

    Everyone named gets the same reference, details and files - a group PNR, a
    cab they share, rooms on one hotel booking - in one email, their managers
    copied. A cost is the total for all of them, split evenly. The files are
    tickets already uploaded for these travellers (POST /requests/{id}/tickets).
    """

    traveller_ids: list[int] = Field(min_length=1, max_length=50)
    #: Required, except on a cab with its car given: the plate stands in.
    booking_reference: str | None = Field(default=None, max_length=120)
    booking_details: BookingDetails | None = None
    #: The note to the traveller, kept as the decision's reason.
    note: str = Field(min_length=3, max_length=500)
    #: A cab only: the car sent.
    cab: CabCar | None = None
    ticket_ids: list[int] = Field(default_factory=list, max_length=20)
    cost_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    #: Recorded for everyone booked when sent (null clears it); left out,
    #: each keeps the vendor they had.
    vendor_id: int | None = None
    notify: bool = True

    @field_validator("note", "booking_reference")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _tidy_text(value) or None

    @model_validator(mode="after")
    def _distinct(self):
        if len(self.traveller_ids) != len(set(self.traveller_ids)):
            raise ValueError("The same traveller is named twice.")
        if len(self.ticket_ids) != len(set(self.ticket_ids)):
            raise ValueError("The same file is named twice.")
        if self.note is None or len(self.note) < 3:
            raise ValueError("Add a note for the traveller.")
        return self
