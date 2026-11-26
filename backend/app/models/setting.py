"""
Tenant Policy and Workflow Configuration Settings.

Governs organization-wide rules such as requiring photo upload/checkout
before an employee can book subsequent trips, and monthly missed photo
regularization allowances.
"""
from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TenantMixin, TimestampMixin


class TenantSetting(Base, TenantMixin, TimestampMixin):
    __tablename__ = "tenant_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_settings_tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Whether unclosed trips / missing checkout photos prevent booking next trip
    require_telemetry_checkout: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: How many unclosed/missed photos an employee is allowed to miss per month before lock engages
    max_monthly_missed_photos: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    #: Whether uploaded booking tickets automatically run AI extraction/autofilling
    ai_ticket_autofill_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenantSetting {self.tenant_id} require_telemetry={self.require_telemetry_checkout} max_missed={self.max_monthly_missed_photos}>"
