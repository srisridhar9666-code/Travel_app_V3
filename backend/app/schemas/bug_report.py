"""Pydantic schemas for in-app bug reports."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import UTCInstant


BugReportStatus = Literal["OPEN", "IN_PROGRESS", "RESOLVED"]


class BugReportUpdate(BaseModel):
    status: BugReportStatus
    resolution_note: str | None = Field(default=None, max_length=2000)


class BugReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    reporter_id: int
    reporter_name: str
    reporter_email: str | None = None
    user_role: str | None = None
    page_url: str | None = None
    page_title: str | None = None
    browser_info: str | None = None
    comment: str
    has_screenshot: bool = False
    status: str
    resolution_note: str | None = None
    resolved_by_id: int | None = None
    resolved_by_name: str | None = None
    resolved_at: UTCInstant | None = None
    created_at: UTCInstant
    updated_at: UTCInstant
