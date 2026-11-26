"""ORM models. Importing this package registers every table on Base.metadata,
which is what Alembic's autogenerate walks."""
from app.models.audit import GENESIS_HASH, AuditLog
from app.models.auth_token import AuthToken
from app.models.base import TenantMixin, TimestampMixin, UTCDateTime, naive_utcnow, utcnow
from app.models.bug_report import BugReport
from app.models.department import Department
from app.models.expense import ExpenseClaim, ExpenseItem
from app.models.hotel import SavedHotel
from app.models.hotel_proposal import HotelProposal
from app.models.id_proof import IdProof
from app.models.invoice import Invoice, InvoiceLine
from app.models.location import Location
from app.models.preference import NotificationPreference
from app.models.project import Project
from app.models.request import (
    Notification,
    RequestRevision,
    RequestTraveller,
    TravelRequest,
)
from app.models.setting import TenantSetting
from app.models.team_change import TeamChange
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.ticket import TicketDocument
from app.models.user import User
from app.models.vendor import Vendor

__all__ = [
    "AuditLog",
    "AuthToken",
    "BugReport",
    "CabTelemetry",
    "Department",
    "ExpenseClaim",
    "ExpenseItem",
    "GENESIS_HASH",
    "HotelProposal",
    "HotelStayVerification",
    "IdProof",
    "Invoice",
    "InvoiceLine",
    "Location",
    "Notification",
    "NotificationPreference",
    "Project",
    "RequestRevision",
    "RequestTraveller",
    "SavedHotel",
    "TravelRequest",
    "TeamChange",
    "TenantMixin",
    "TenantSetting",
    "TicketDocument",
    "TimestampMixin",
    "UTCDateTime",
    "User",
    "Vendor",
    "naive_utcnow",
    "utcnow",
]
