"""Request and response bodies for a manager's team and the changes they ask for."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core import clock
from app.core.enums import Designation, HotelOccupancy, TeamChangeKind, TeamChangeStatus, UserStatus
from app.schemas.auth import InviteLinkResponse
from app.schemas.common import UTCInstant
from app.schemas.user import MobileNumber, PersonName, SelectableGender, WorkEmail


def _tidy(value: str | None) -> str | None:
    cleaned = " ".join((value or "").split())
    return cleaned or None


class _Noted(BaseModel):
    #: The manager's own words on why, shown to the admin who decides.
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def _tidy_note(cls, value: str | None) -> str | None:
        return _tidy(value)


class TeamAddRequest(_Noted):
    """Someone new for the manager's team. They join as ground staff in the
    manager's department, reporting to the manager, once an admin approves."""

    email: WorkEmail
    full_name: PersonName = Field(min_length=2, max_length=160)
    designation: Designation | None = None
    gender: SelectableGender
    phone: MobileNumber = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_state: str | None = Field(default=None, max_length=80)
    base_location: str | None = Field(default=None, max_length=120)
    hotel_occupancy_entitlement: HotelOccupancy = HotelOccupancy.SINGLE


#: What a manager may ask to change about a member. Role, department, email and
#: status stay with admins.
EDITABLE_FIELDS = (
    "full_name",
    "designation",
    "phone",
    "employee_code",
    "base_state",
    "base_location",
    "hotel_occupancy_entitlement",
)


class TeamEditRequest(_Noted):
    """Only the fields sent are asked for; the rest stay as they are."""

    full_name: PersonName | None = Field(default=None, min_length=2, max_length=160)
    designation: Designation | None = None
    phone: MobileNumber = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=40)
    base_state: str | None = Field(default=None, max_length=80)
    base_location: str | None = Field(default=None, max_length=120)
    hotel_occupancy_entitlement: HotelOccupancy | None = None


class TeamMemberOccupancyUpdate(BaseModel):
    """Direct occupancy evaluation by manager for their team member (SOW Section 1.1 item 2)."""
    hotel_occupancy_entitlement: HotelOccupancy


class TeamRemoveRequest(_Noted):
    """Take someone off the team: they have left, or should be switched off."""

    status: Literal[UserStatus.LEFT, UserStatus.DEACTIVATED] = UserStatus.LEFT
    exited_on: date | None = None
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def _tidy_reason(cls, value: str) -> str:
        cleaned = _tidy(value)
        if cleaned is None or len(cleaned) < 3:
            raise ValueError("Say why, in a few words.")
        return cleaned

    @field_validator("exited_on")
    @classmethod
    def _not_in_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.local_today():
            raise ValueError("The exit date cannot be in the future.")
        return value


class TeamChangeRead(BaseModel):
    id: int
    kind: TeamChangeKind
    status: TeamChangeStatus
    requested_by_id: int
    requested_by_name: str | None = None
    target_user_id: int | None = None
    #: The member's name - for an ADD, the name asked for.
    target_name: str | None = None
    #: What was asked, as sent: the new person's details, the fields to change
    #: with their current values beside them, or the reason for removal.
    payload: dict[str, Any]
    note: str | None = None
    decided_by_name: str | None = None
    decided_at: UTCInstant | None = None
    decision_comment: str | None = None
    created_at: UTCInstant


class TeamChangeList(BaseModel):
    items: list[TeamChangeRead]
    pending: int


class TeamApprove(BaseModel):
    comment: str | None = Field(default=None, max_length=500)
    #: For an ADD: email the new person their invite link. The link comes back
    #: either way, for the admin to copy.
    send_email: bool = True

    @field_validator("comment")
    @classmethod
    def _tidy_comment(cls, value: str | None) -> str | None:
        return _tidy(value)


class TeamReject(BaseModel):
    comment: str = Field(min_length=3, max_length=500)

    @field_validator("comment")
    @classmethod
    def _tidy_comment(cls, value: str) -> str:
        cleaned = _tidy(value)
        if cleaned is None or len(cleaned) < 3:
            raise ValueError("Tell the manager why, in a few words.")
        return cleaned


class TeamDecision(BaseModel):
    change: TeamChangeRead
    #: The new person's invite, when an ADD was approved.
    invite: InviteLinkResponse | None = None

