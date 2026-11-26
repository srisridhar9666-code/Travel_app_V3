"""Request and response bodies for the authentication endpoints."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.enums import Designation, Gender, HotelOccupancy, Role, UserStatus
from app.schemas.common import UTCInstant
from app.schemas.user import MobileNumber, PersonName, WorkEmail


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserProfile(BaseModel):
    """The signed-in user, as the client needs them."""

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
    theme_preference: str
    status: UserStatus
    is_active: bool
    last_login_at: UTCInstant | None = None
    #: When they last changed or reset their password, for "last changed" on
    #: the profile page.
    password_changed_at: UTCInstant | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: UTCInstant
    user: UserProfile


class SetPasswordRequest(BaseModel):
    """Redeems an invite or reset token and sets the first/replacement password."""

    token: str = Field(min_length=10, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class PasswordChangedResponse(BaseModel):
    """A password change signs out every other device - including the token
    that made the request - so the caller gets a fresh one to carry on with."""

    detail: str
    access_token: str
    token_type: str = "bearer"
    expires_at: UTCInstant


class ProfileUpdateRequest(BaseModel):
    """What anyone may change about themselves without a password.

    Unknown fields are refused, not ignored: a body carrying `role` or
    `status` is someone trying something, and should hear no.
    """

    model_config = ConfigDict(extra="forbid")

    full_name: PersonName | None = Field(default=None, min_length=2, max_length=160)
    phone: MobileNumber = Field(default=None, max_length=32)
    upi_id: str | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    account_holder_name: str | None = None
    ifsc_code: str | None = None


class EmailChangeRequest(BaseModel):
    """The sign-in address is where reset links go, so changing it needs the
    current password: a session left open on a shared phone must not be enough
    to take the account over for good."""

    new_email: WorkEmail
    current_password: str = Field(min_length=1, max_length=256)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class TokenPreview(BaseModel):
    """What a token redemption page shows before asking for a password.

    Deliberately thin: enough to confirm the right link was opened, never enough
    to enumerate accounts by guessing tokens.
    """

    full_name: str
    email: EmailStr
    purpose: str


class ThemePreferenceRequest(BaseModel):
    theme_preference: str = Field(pattern="^(light|dark|system)$")


class MessageResponse(BaseModel):
    detail: str


class InviteLinkResponse(BaseModel):
    """Returned to an admin after creating or re-inviting a user.

    The link is emailed to the person, and also returned here so the admin can
    pass it on when mail is off or bounced. The raw token appears here exactly
    once and is not stored.
    """

    detail: str
    invite_url: str | None = None
    expires_at: UTCInstant | None = None
    #: Whether the link reached the mail server. None on the forgot-password
    #: answer, which must not reveal anything about the account.
    email_sent: bool | None = None
    #: Why it did not, when it did not - "EMAIL_ENABLED is not true", an SMTP
    #: authentication error - so the admin can fix the cause, not just retry.
    email_detail: str | None = None
    #: INVITE or PASSWORD_RESET, so the admin is told which kind of link it is.
    purpose: str | None = None
