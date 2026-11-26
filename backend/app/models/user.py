"""
The person and their credentials.

Users and employees are one table rather than two. Every one of the ~110 people
in this system needs both a login and a travel profile, so splitting them would
buy a mandatory 1:1 join and nothing else. ID proofs live in their own table
(Phase 2) because they are multi-valued and carry their own access rules.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, Enum as SAEnum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.enums import ADMIN_ROLES, Designation, Gender, HotelOccupancy, Role, UserStatus
from app.database import Base
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime


class User(Base, TenantMixin, TimestampMixin):
    __tablename__ = "users"
    __table_args__ = (
        # Email is unique per tenant, not globally, so a second company can
        # onboard someone who already exists under the first.
        Index("uq_users_tenant_email", "tenant_id", "email", unique=True),
        Index("ix_users_tenant_role", "tenant_id", "role"),
        Index("ix_users_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- identity -----------------------------------------------------------
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    employee_code: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Null until the invite is accepted - an invited user exists but cannot
    # authenticate yet.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # --- authorisation ------------------------------------------------------
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, native_enum=False, length=20, validate_strings=True),
        default=Role.GROUND_STAFF,
        nullable=False,
    )

    # --- travel profile (SOW section 5) -------------------------------------
    # Designation is hierarchy for reporting only. Approval routing on it is
    # explicitly out of scope for V1 - see addendum B11.
    designation: Mapped[Designation | None] = mapped_column(
        SAEnum(Designation, native_enum=False, length=20, validate_strings=True),
        nullable=True,
    )
    # Drives the room-sharing policy. Anything other than an exact match between
    # two people falls back to separate rooms - see addendum B7.
    gender: Mapped[Gender] = mapped_column(
        SAEnum(Gender, native_enum=False, length=20, validate_strings=True),
        default=Gender.UNDISCLOSED,
        nullable=False,
    )
    #: Default hotel room occupancy entitlement: Single (default) or Double (SOW Section 1.1 item 2).
    #: Evaluated & configured on the team member's profile by their Manager for all their bookings.
    hotel_occupancy_entitlement: Mapped[HotelOccupancy] = mapped_column(
        SAEnum(HotelOccupancy, native_enum=False, length=20, validate_strings=True),
        default=HotelOccupancy.SINGLE,
        server_default=HotelOccupancy.SINGLE.value,
        nullable=False,
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    upi_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bank_account_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    account_holder_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ifsc_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    #: Where they are based: a state and a city or constituency from the place
    #: list, like a request's origin. `base_location` is the city; it predates
    #: `base_state` and may still hold free text on older rows.
    base_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    base_location: Mapped[str | None] = mapped_column(String(120), nullable=True)

    #: Which part of the organisation they work in. Admins add departments as
    #: they need them; this is reporting only and grants no permissions.
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    department = relationship("Department", lazy="joined")

    #: The manager a team member reports to. One level: only ground staff
    #: report to someone, and only to a MANAGER (checked by the routers).
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", name="fk_users_manager_id"),
        nullable=True,
        index=True,
    )

    #: If this user is an external commercial partner / vendor (Role.VENDOR),
    #: links to the Vendor master row (SOW Section 1.1).
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL", name="fk_users_vendor_id"),
        nullable=True,
        index=True,
    )
    vendor = relationship("Vendor", foreign_keys=[vendor_id], lazy="joined")

    # --- preferences --------------------------------------------------------
    # Persisted server-side so the theme follows the user across devices,
    # rather than living only in one browser's localStorage.
    theme_preference: Mapped[str] = mapped_column(String(10), default="system", nullable=False)

    # --- account state ------------------------------------------------------
    #: The authority on whether someone can sign in: only ACTIVE can. Change it
    #: through services/accounts.set_status, which keeps the rules in one place.
    status: Mapped[UserStatus] = mapped_column(
        SAEnum(UserStatus, native_enum=False, length=20, validate_strings=True),
        default=UserStatus.ACTIVE,
        server_default=UserStatus.ACTIVE.value,
        nullable=False,
    )
    status_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    #: Mirrors `status == ACTIVE` (kept in step by `_mirror_status`), so every
    #: query that filters on it - reminders, co-stay, tagging - keeps working.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: The day they left the organisation. Distinct from deactivation, which
    #: only stops them signing in - a contractor can be suspended for a week
    #: without having left. Retention counts from this date, not from
    #: deactivation, so a temporary suspension never triggers a purge.
    exited_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    #: Tokens issued before this instant are refused (deps.get_current_user), so
    #: changing or resetting a password signs out every other device.
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    #: When an Admin or Manager unlocks an employee who exceeded missed telemetry limits
    telemetry_unlocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by = relationship("User", remote_side=[id], lazy="noload", foreign_keys=[created_by_id])
    manager = relationship("User", remote_side=[id], lazy="select", foreign_keys=[manager_id])

    @validates("status")
    def _mirror_status(self, _key: str, value: UserStatus | str) -> UserStatus:
        status = UserStatus(value)
        self.is_active = status is UserStatus.ACTIVE
        return status

    @property
    def department_name(self) -> str | None:
        return self.department.name if self.department is not None else None

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash)

    @property
    def manager_name(self) -> str | None:
        return self.manager.full_name if self.manager is not None else None

    @property
    def vendor_name(self) -> str | None:
        return self.vendor.name if self.vendor is not None else None

    @property
    def active_manager(self) -> "User | None":
        """The manager who answers for this person's trips right now.

        None when their manager's account is switched off: nobody is left to
        recommend, so a request must not look as if it is waiting on them, and
        nobody should be copied on mail they can no longer act on.
        """
        manager = self.manager
        return manager if manager is not None and manager.is_active else None

    @property
    def is_admin(self) -> bool:
        return self.role in ADMIN_ROLES

    @property
    def is_manager(self) -> bool:
        return self.role is Role.MANAGER

    @property
    def is_finance(self) -> bool:
        return self.role in {Role.FINANCE, Role.ADMIN, Role.SYSTEM_ADMIN, Role.SUPER_ADMIN}

    @property
    def is_vendor(self) -> bool:
        return self.role is Role.VENDOR

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.id} {self.email} {self.role}>"
