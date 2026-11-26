"""Request and response bodies for vendors."""
from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.core.enums import VendorKind
from app.schemas.common import UTCInstant
from app.schemas.user import PersonName, PhoneNumber, tidy_email

#: Fifteen letters and digits. The real GSTIN has a state code, a PAN and a
#: check digit, but a strict check would refuse a number typed from a bill with
#: one character misread, and that is a question for the accountant, not a
#: form. The shape catches what matters: a PAN or a phone number in the wrong box.
_GSTIN = re.compile(r"[0-9A-Z]{15}")

GSTIN_MESSAGE = "A GSTIN is 15 letters and digits, e.g. 36AABCD1234E1Z5."


def _tidy(value: object) -> object:
    """Inner spacing collapsed and a blank treated as not given."""
    if isinstance(value, str):
        cleaned = " ".join(value.split())
        return cleaned or None
    return value


class _VendorFields(BaseModel):
    @field_validator("name", "contact_name", "notes", mode="before", check_fields=False)
    @classmethod
    def _tidy_text(cls, value: object) -> object:
        return _tidy(value)

    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _blank_email(cls, value: object) -> object:
        return _tidy(value)

    @field_validator("email", check_fields=False)
    @classmethod
    def _lower_email(cls, value: str | None) -> str | None:
        return tidy_email(value)

    @field_validator("gstin", mode="before", check_fields=False)
    @classmethod
    def _gstin(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        cleaned = "".join(value.split()).upper()
        if not cleaned:
            return None
        if not _GSTIN.fullmatch(cleaned):
            raise ValueError(GSTIN_MESSAGE)
        return cleaned


class VendorCreate(_VendorFields):
    name: str = Field(min_length=2, max_length=160)
    kind: VendorKind = VendorKind.OTHER
    contact_name: PersonName | None = Field(default=None, max_length=120)
    phone: PhoneNumber = Field(default=None, max_length=32)
    email: EmailStr | None = Field(default=None, max_length=255)
    gstin: str | None = Field(default=None, max_length=15)
    notes: str | None = Field(default=None, max_length=500)
    create_login: bool = False
    login_email: EmailStr | None = None
    login_password: str | None = None


class VendorUpdate(_VendorFields):
    """Only the fields sent are changed. Switching a vendor off has its own
    endpoint, so it is a deliberate act with its own line in the log."""

    name: str | None = Field(default=None, min_length=2, max_length=160)
    kind: VendorKind | None = None
    contact_name: PersonName | None = Field(default=None, max_length=120)
    phone: PhoneNumber = Field(default=None, max_length=32)
    email: EmailStr | None = Field(default=None, max_length=255)
    gstin: str | None = Field(default=None, max_length=15)
    notes: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _name_and_kind_stay(self):
        # The optional fields can be cleared by sending null; these two cannot.
        for field in ("name", "kind"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"A vendor needs a {field}.")
        return self


class VendorRead(BaseModel):
    id: int
    name: str
    kind: VendorKind
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    gstin: str | None = None
    notes: str | None = None
    is_active: bool
    created_at: UTCInstant
    #: Travellers whose cost was paid to this vendor, and invoices raised
    #: against them: what a person deciding to switch one off wants to know.
    traveller_count: int = 0
    invoice_count: int = 0
    #: Linked portal user information
    portal_user_id: int | None = None
    portal_email: str | None = None
    portal_user_status: str | None = None


class VendorLoginRequest(BaseModel):
    email: EmailStr | None = None
    password: str | None = None
    full_name: str | None = None


class VendorLoginResponse(BaseModel):
    success: bool
    user_id: int
    email: str
    full_name: str
    message: str
    temporary_password: str | None = None

