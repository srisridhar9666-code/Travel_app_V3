"""Request and response bodies for ticket upload, review and confirmation."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    TicketStatus,
)
from app.schemas.common import UTCInstant


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    traveller_id: int
    traveller_name: str
    status: TicketStatus

    file_name: str | None = None
    file_size: int | None = None
    content_type: str | None = None
    uploaded_by_name: str | None = None
    created_at: UTCInstant

    # --- what the model proposed --------------------------------------------
    booking_reference: str | None = None
    carrier: str | None = None
    service_number: str | None = None
    passenger_name: str | None = None
    origin: str | None = None
    destination: str | None = None
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    hotel_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    #: The fare the model read. Pre-fills the cost field; never saved as a cost
    #: without a human confirming it.
    fare_amount: Decimal | None = None
    fare_currency: str | None = None

    confidence: dict[str, float] | None = None
    #: Fields the model was unsure of, so the reviewer knows where to look first.
    needs_review: list[str] = Field(default_factory=list)
    model_id: str | None = None
    extraction_error: str | None = None
    extracted_at: UTCInstant | None = None

    # --- the human step ------------------------------------------------------
    confirmed_by_name: str | None = None
    confirmed_at: UTCInstant | None = None
    confirmed_reference: str | None = None

    #: Differences between the ticket and what was asked for. Advisory: a ticket
    #: that does not match the request is usually a real problem, occasionally a
    #: deliberate change, and never something to reject automatically.
    mismatches: list[str] = Field(default_factory=list)


class CombinedRead(BaseModel):
    """Several files read together, as one proposal for the booking window.

    Like every extracted value it is a proposal: the admin checks it before
    anything is saved. `notes` says what to look at - two references, names
    that differ, a file that could not be read.
    """

    files: int
    files_read: int
    booking_reference: str | None = None
    carrier: str | None = None
    service_number: str | None = None
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    hotel_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    fare_total: Decimal | None = None
    fare_currency: str | None = None
    notes: list[str] = Field(default_factory=list)


class ConfirmPayload(BaseModel):
    """The admin accepting a ticket, with whatever they corrected.

    The reference is required and pre-filled from the extraction rather than
    taken from it silently - addendum B3 exists because a model misparse must not
    be able to book a wrong PNR on its own. The same applies to the fare: it is
    offered, and it only becomes a number in a financial report once someone has
    looked at it (C1).
    """

    booking_reference: str = Field(min_length=2, max_length=120)
    carrier: str | None = Field(default=None, max_length=120)
    service_number: str | None = Field(default=None, max_length=60)
    #: What this traveller's trip cost. Optional here because a fare is not
    #: always known at booking; the costs endpoints fill it in later.
    cost_amount: Decimal | None = Field(default=None, ge=0, le=Decimal("10000000"))
    #: Skip the traveller email, for a correction that should not re-notify.
    notify: bool = True


class NotificationRow(BaseModel):
    """One line of the delivery ledger, as an admin sees it."""

    id: int
    user_id: int
    user_name: str | None = None
    kind: str
    category: NotificationCategory
    title: str
    body: str
    channel: NotificationChannel
    status: NotificationStatus
    to_address: str | None = None
    #: Who the email was copied to, comma separated.
    cc_addresses: str | None = None
    subject: str | None = None
    attempts: int
    sent_at: UTCInstant | None = None
    last_error: str | None = None
    request_id: int | None = None
    read_at: UTCInstant | None = None
    created_at: UTCInstant


class NotificationLedger(BaseModel):
    items: list[NotificationRow]
    total: int
    page: int
    page_size: int
    summary: dict


class PreferenceUpdate(BaseModel):
    """One category switched on or off for the signed-in person."""

    category: NotificationCategory
    enabled: bool


class PreferencesRead(BaseModel):
    """Email preferences, one entry per switchable category.

    `DECISIONS` is absent on purpose - see `OPTIONAL_CATEGORIES`. The screen says
    so rather than showing a toggle that does nothing.
    """

    email: dict[str, bool]
    unread: int


class JobResult(BaseModel):
    job: str
    notified: int | None = None
    considered: int | None = None
    stale: int | None = None
    attempted: int | None = None
    sent: int | None = None
    still_failing: int | None = None
    error: str | None = None


class SchedulerStatus(BaseModel):
    enabled: bool
    running: bool
    interval_minutes: int
    travel_reminder_days: int
    stale_after_days: int
    failed_email: int


class EmailEnvFile(BaseModel):
    """The .env this process reads. Key names only, never values."""

    path: str
    exists: bool
    encoding: str | None = None
    modified_at: UTCInstant | None = None
    keys: list[str] = Field(default_factory=list)
    #: Keys the app does not read, mapped to the setting it probably meant.
    unknown_keys: dict[str, str | None] = Field(default_factory=dict)


class EmailSettings(BaseModel):
    enabled: bool
    host: str
    port: int
    security: str
    username: str | None = None
    #: "set (16 characters)" or "not set" - the value itself never leaves.
    password: str
    password_looks_wrong: bool
    from_address: str | None = None
    from_name: str
    allowlist: list[str]
    links_point_to: str
    env_file: EmailEnvFile
    from_environment: list[str]
    started_at: UTCInstant
    restart_needed: bool


class EmailStatus(BaseModel):
    problem: str | None = None
    settings: EmailSettings


class EmailTestRequest(BaseModel):
    to: EmailStr | None = None


class EmailTestResult(BaseModel):
    ok: bool
    to: str
    #: How far it got: config, connect, login, send, done.
    stage: str
    error: str | None = None
    hint: str | None = None
    #: False when EMAIL_ALLOWLIST would hold back ordinary notices to this address.
    allowlisted: bool
    settings: EmailSettings
