"""Request and response bodies for user administration."""
from __future__ import annotations

import re
from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core import clock
from app.core.enums import SELECTABLE_GENDERS, Designation, Gender, HotelOccupancy, Role, UserStatus
from app.schemas.common import UTCInstant

_PHONE_CHARS = re.compile(r"\+?[\d\s\-()]+")

PHONE_MESSAGE = "Enter a phone number with 10 to 15 digits, e.g. 040 2345 6789."
MOBILE_MESSAGE = "Enter a 10-digit mobile number starting with 6, 7, 8 or 9 - no country code."
_MOBILE = re.compile(r"[6-9]\d{9}")
GENDER_MESSAGE = "Choose Male or Female."


def tidy_name(value: str | None) -> str | None:
    return " ".join(value.split()) if value else value


def tidy_email(value: str | None) -> str | None:
    return value.strip().lower() if value else value


def tidy_phone(value: str | None) -> str | None:
    """A phone number with its spacing collapsed, or None when it is blank.

    Lenient on format, because people write Indian numbers every way there is -
    "98765 43210", "+91-98765-43210", "(040) 2345 6789" - but strict on the
    digit count, which is what catches a number with a digit missing. Ten is a
    mobile number; fifteen is the international maximum.
    """
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    digits = sum(ch.isdigit() for ch in cleaned)
    if not _PHONE_CHARS.fullmatch(cleaned) or not 10 <= digits <= 15:
        raise ValueError(PHONE_MESSAGE)
    return cleaned


def tidy_mobile(value: str | None) -> str | None:
    """An employee's Indian mobile number as its bare ten digits, or None.

    Staff numbers are stored one way so that "no two employees share a number"
    can be checked, and so a number is shown and searched the same everywhere.
    A pasted "+91 98765-43210" or "098765 43210" is accepted and stored as
    9876543210; anything that is not then ten digits starting 6-9 is refused.
    """
    if value is None:
        return None
    digits = re.sub(r"[\s\-()]", "", value)
    if not digits:
        return None
    if digits.startswith("+91") and len(digits) == 13:
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _MOBILE.fullmatch(digits):
        raise ValueError(MOBILE_MESSAGE)
    return digits


def selectable_gender(value: Gender) -> Gender:
    """Male or Female. The other two values exist only on rows saved before
    gender had to be chosen, and room sharing treats them as 'never share'."""
    if value not in SELECTABLE_GENDERS:
        raise ValueError(GENDER_MESSAGE)
    return value


#: Shared with the self-service profile schemas, so a phone number or a name is
#: tidied and checked the same way whoever types it.
PersonName = Annotated[str, AfterValidator(tidy_name)]
WorkEmail = Annotated[EmailStr, AfterValidator(tidy_email)]
PhoneNumber = Annotated[str | None, AfterValidator(tidy_phone)]
#: An employee's (or a cab driver's) mobile: ten digits, starting 6-9.
MobileNumber = Annotated[str | None, AfterValidator(tidy_mobile)]
SelectableGender = Annotated[Gender, AfterValidator(selectable_gender)]


class UserCreate(BaseModel):
    email: WorkEmail
    full_name: PersonName = Field(min_length=2, max_length=160)
    role: Role = Role.GROUND_STAFF
    designation: Designation | None = None
    #: Required: it decides who may share a room, and nobody can guess it later.
    gender: SelectableGender
    phone: MobileNumber = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_state: str | None = Field(default=None, max_length=80)
    #: The city or constituency they are based in.
    base_location: str | None = Field(default=None, max_length=120)
    department_id: int | None = None
    #: The manager they report to. Ground staff only, and only a MANAGER.
    manager_id: int | None = None
    #: If this user is an external commercial partner (Role.VENDOR), linked vendor company.
    vendor_id: int | None = None
    #: Default hotel room occupancy entitlement: Single or Double (evaluated by Manager).
    hotel_occupancy_entitlement: HotelOccupancy = HotelOccupancy.SINGLE
    #: Email the invitation link to them. Either way the link comes back in the
    #: response, for the admin to copy and share themselves.
    send_email: bool = True


#: Columns that cannot be emptied. Without this an explicit null reaches the
#: NOT NULL column and the database answers with a 500.
_NOT_NULL = {
    "full_name": "Full name cannot be blank.",
    "role": "App access cannot be blank.",
    "gender": GENDER_MESSAGE,
    "email": "Work email cannot be blank.",
}


class UserUpdate(BaseModel):
    """Every field optional - this is a partial update.

    Status is not here: it changes through POST /users/{id}/status, which owns
    the exit date, the sign-in block and the audit line. Unknown fields are
    refused rather than dropped, so a client still sending `is_active` hears
    about it instead of believing it worked.
    """

    model_config = ConfigDict(extra="forbid")

    email: WorkEmail | None = None
    full_name: PersonName | None = Field(default=None, min_length=2, max_length=160)
    role: Role | None = None
    designation: Designation | None = None
    gender: SelectableGender | None = None
    phone: MobileNumber = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_state: str | None = Field(default=None, max_length=80)
    base_location: str | None = Field(default=None, max_length=120)
    department_id: int | None = None
    manager_id: int | None = None
    vendor_id: int | None = None
    hotel_occupancy_entitlement: HotelOccupancy | None = None
    upi_id: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder_name: str | None = None
    ifsc_code: str | None = None

    @field_validator(*_NOT_NULL, mode="before")
    @classmethod
    def _not_null(cls, value, info):
        if value is None:
            raise ValueError(_NOT_NULL[info.field_name])
        return value


class UserStatusChange(BaseModel):
    status: UserStatus
    #: Only for LEFT (and DELETED): the day they left. Defaults to today, India
    #: time. Starts the 90-day clock on their ID documents.
    exited_on: date | None = None
    #: Why, in the admin's words. Kept in the activity log.
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("exited_on")
    @classmethod
    def _not_in_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.local_today():
            raise ValueError("The exit date cannot be in the future.")
        return value

    @field_validator("reason")
    @classmethod
    def _tidy_reason(cls, value: str | None) -> str | None:
        cleaned = " ".join((value or "").split())
        return cleaned or None


class OpenTrips(BaseModel):
    """Trips this person is still on that have not happened yet - what an admin
    should look at before deactivating them."""

    pending: int
    approved: int
    booked: int
    total: int


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    employee_code: str | None = None
    role: Role
    designation: Designation | None = None
    gender: Gender
    phone: str | None = None
    base_state: str | None = None
    base_location: str | None = None
    department_id: int | None = None
    department_name: str | None = None
    #: Who they report to, for ground staff in a team.
    manager_id: int | None = None
    manager_name: str | None = None
    vendor_id: int | None = None
    vendor_name: str | None = None
    hotel_occupancy_entitlement: HotelOccupancy = HotelOccupancy.SINGLE
    upi_id: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder_name: str | None = None
    ifsc_code: str | None = None
    status: UserStatus
    status_changed_at: UTCInstant | None = None
    #: Mirrors status == ACTIVE.
    is_active: bool
    exited_on: date | None = None
    last_login_at: UTCInstant | None = None
    created_at: UTCInstant

    #: False while an invite is outstanding, so the UI can show "Invited"
    #: rather than pretending the account is ready.
    has_password: bool
    is_locked: bool


class UserListResponse(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_email: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    action: str
    entity_type: str
    entity_id: int | None = None
    summary: str
    changes: dict | None = None
    reason: str | None = None
    ip_address: str | None = None
    created_at: UTCInstant


class AuditListResponse(BaseModel):
    items: list[AuditRead]
    total: int
    page: int
    page_size: int
