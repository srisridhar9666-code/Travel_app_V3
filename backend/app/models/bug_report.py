"""
User-submitted bug reports with screen captures and contextual debugging metadata.
"""
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class BugReport(Base, TenantMixin, TimestampMixin):
    """In-app bug/issue report raised via the floating Raise Bug widget."""

    __tablename__ = "bug_reports"
    __table_args__ = (
        Index("ix_bug_reports_tenant_status", "tenant_id", "status"),
        Index("ix_bug_reports_reporter", "tenant_id", "reporter_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    browser_info: Mapped[str | None] = mapped_column(String(500), nullable=True)

    comment: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[str] = mapped_column(String(30), default="OPEN", nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    reporter = relationship("User", foreign_keys=[reporter_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])
