"""Tests for Sriyatra V4 Phase 1: Foundation Models, Roles & Policies."""
from datetime import date, datetime, timezone
from decimal import Decimal
import pytest

from app.core.enums import (
    FOOD_ALLOWANCE_PER_DAY,
    LOCAL_TRANSIT_UNBILLED_MAX,
    ExpenseCategory,
    ExpenseStatus,
    HotelOccupancy,
    HotelProposalStatus,
    PaymentMode,
    RequestPriority,
    RequestType,
    Role,
    ROLE_RANK,
    StayVerificationStatus,
    TelemetryStatus,
    UserStatus,
    Gender,
)
from app.models.expense import ExpenseClaim, ExpenseItem
from app.models.hotel_proposal import HotelProposal
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.user import User
from app.models.vendor import Vendor, VendorKind
from app.schemas.expense import ExpenseItemCreate
from app.services import accounts


def test_v4_role_hierarchy():
    """Verify 6 discrete ranked roles according to SOW Section 1.1 with Super Admin and System Admin equal."""
    assert Role.SUPER_ADMIN in Role
    assert Role.SYSTEM_ADMIN in Role
    assert Role.ADMIN in Role
    assert Role.FINANCE in Role
    assert Role.MANAGER in Role
    assert Role.GROUND_STAFF in Role
    assert Role.VENDOR in Role

    # Super Admin and System Admin both are same
    assert ROLE_RANK[Role.SUPER_ADMIN] == ROLE_RANK[Role.SYSTEM_ADMIN]
    assert not accounts.outranks(Role.SUPER_ADMIN, Role.SYSTEM_ADMIN)
    assert not accounts.outranks(Role.SYSTEM_ADMIN, Role.SUPER_ADMIN)

    # Seniority ranking check
    assert ROLE_RANK[Role.SUPER_ADMIN] > ROLE_RANK[Role.ADMIN]
    assert ROLE_RANK[Role.SYSTEM_ADMIN] > ROLE_RANK[Role.ADMIN]
    assert ROLE_RANK[Role.ADMIN] > ROLE_RANK[Role.FINANCE]
    assert ROLE_RANK[Role.FINANCE] > ROLE_RANK[Role.MANAGER]
    assert ROLE_RANK[Role.MANAGER] > ROLE_RANK[Role.VENDOR]
    assert ROLE_RANK[Role.VENDOR] > ROLE_RANK[Role.GROUND_STAFF]

    # Outranking logic
    assert accounts.outranks(Role.ADMIN, Role.FINANCE)
    assert accounts.outranks(Role.ADMIN, Role.MANAGER)
    assert accounts.outranks(Role.ADMIN, Role.VENDOR)
    assert accounts.outranks(Role.SUPER_ADMIN, Role.ADMIN)
    assert not accounts.outranks(Role.MANAGER, Role.ADMIN)


def test_v4_hotel_proposal_model(db):
    """Verify HotelProposal creation and relations (SOW Section 7)."""
    vendor = Vendor(tenant_id="db_test", name="Taj Group", kind=VendorKind.HOTEL)
    db.add(vendor)

    user = User(
        tenant_id="db_test",
        email="test_traveler@designboxed.com",
        full_name="Test Traveler",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
    )
    db.add(user)

    project = Project(tenant_id="db_test", code="CMP-2026-0001", name="State Campaign")
    db.add(project)
    db.flush()

    req = TravelRequest(
        tenant_id="db_test",
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=user.id,
        hotel_city="Patna",
        check_in=date(2026, 12, 1),
        check_out=date(2026, 12, 5),
        hotel_occupancy=HotelOccupancy.SINGLE,
        travel_reason="Field campaign meeting",
    )
    db.add(req)
    db.flush()

    proposal = HotelProposal(
        tenant_id="db_test",
        request_id=req.id,
        vendor_id=vendor.id,
        hotel_name="Hotel Maurya Patna",
        address="Fraser Road, South Gandhi Maidan",
        landmark="Gandhi Maidan",
        google_maps_url="https://maps.google.com/?q=Hotel+Maurya+Patna",
        room_category="Deluxe",
        nightly_rate=Decimal("1450.00"),
        amenities=["Wi-Fi", "Breakfast", "AC"],
        status=HotelProposalStatus.PROPOSED,
    )
    db.add(proposal)
    db.flush()

    assert proposal.id is not None
    assert proposal.hotel_name == "Hotel Maurya Patna"
    assert proposal.status == HotelProposalStatus.PROPOSED
    assert proposal.nightly_rate == Decimal("1450.00")
    assert req.hotel_proposals == [proposal]


def test_v4_cab_telemetry_model(db):
    """Verify CabTelemetry start/end odometer and distance calculation (SOW Section 8.1)."""
    user = User(
        tenant_id="db_test",
        email="cab_traveler@designboxed.com",
        full_name="Cab Traveler",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
    )
    db.add(user)
    project = Project(tenant_id="db_test", code="CMP-2026-0002", name="Field Survey")
    db.add(project)
    db.flush()

    req = TravelRequest(
        tenant_id="db_test",
        request_type=RequestType.LOCAL_CAB,
        project_id=project.id,
        requester_id=user.id,
        origin="Hotel Maurya",
        destination="Hotel Maurya",
        pickup_city="Patna",
        drop_city="Patna",
        travel_reason="Daily field inspection",
    )
    db.add(req)
    db.flush()

    telemetry = CabTelemetry(
        tenant_id="db_test",
        request_id=req.id,
        driver_name="Ramesh Kumar",
        driver_phone="9876543210",
        vehicle_number="BR 01 AB 1234",
        vehicle_model="Maruti Dzire",
        start_odometer_km=Decimal("45200.0"),
        start_odometer_photo_url="https://storage.local/meter_start.jpg",
        start_time_ist=datetime.now(timezone.utc),
        status=TelemetryStatus.IN_PROGRESS,
    )
    db.add(telemetry)
    db.flush()

    assert telemetry.calculate_distance() is None

    # Evening conclude
    telemetry.end_odometer_km = Decimal("45275.5")
    telemetry.end_odometer_photo_url = "https://storage.local/meter_end.jpg"
    telemetry.end_time_ist = datetime.now(timezone.utc)
    telemetry.total_km = telemetry.calculate_distance()
    telemetry.status = TelemetryStatus.COMPLETED
    db.flush()

    assert telemetry.total_km == Decimal("75.5")
    assert telemetry.status == TelemetryStatus.COMPLETED


def test_v4_hotel_stay_verification(db):
    """Verify HotelStayVerification check-in and check-out with folio (SOW Section 8.2)."""
    user = User(
        tenant_id="db_test",
        email="stay_traveler@designboxed.com",
        full_name="Stay Traveler",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
    )
    db.add(user)
    project = Project(tenant_id="db_test", code="CMP-2026-0003", name="Media Briefing")
    db.add(project)
    db.flush()

    req = TravelRequest(
        tenant_id="db_test",
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=user.id,
        hotel_city="Ranchi",
        travel_reason="Media strategy meet",
    )
    db.add(req)
    db.flush()

    stay = HotelStayVerification(
        tenant_id="db_test",
        request_id=req.id,
        user_id=user.id,
        check_in_time=datetime.now(timezone.utc),
        room_number="Room 304",
        status=StayVerificationStatus.CHECKED_IN,
    )
    db.add(stay)
    db.flush()

    assert stay.room_number == "Room 304"
    assert stay.status == StayVerificationStatus.CHECKED_IN

    # Departure check-out & folio bill upload
    stay.check_out_time = datetime.now(timezone.utc)
    stay.folio_bill_url = "https://storage.local/folio_bill_304.pdf"
    stay.status = StayVerificationStatus.CHECKED_OUT
    stay.reimbursement_prompted = True
    db.flush()

    assert stay.status == StayVerificationStatus.CHECKED_OUT
    assert stay.reimbursement_prompted is True
    assert stay.folio_bill_url == "https://storage.local/folio_bill_304.pdf"


def test_v4_expense_claim_workflow(db):
    """Verify ExpenseClaim & ExpenseItem creation and finance payout settlement (SOW Section 9)."""
    user = User(
        tenant_id="db_test",
        email="field_emp@designboxed.com",
        full_name="Field Employee",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
    )
    db.add(user)
    project = Project(tenant_id="db_test", code="CMP-2026-0004", name="Constituency Drive")
    db.add(project)
    db.flush()

    claim = ExpenseClaim(
        tenant_id="db_test",
        claim_code="EXP-2026-0001",
        user_id=user.id,
        project_id=project.id,
        destination_city="Varanasi",
        travel_start_date=date(2026, 11, 10),
        travel_end_date=date(2026, 11, 12),
        status=ExpenseStatus.SUBMITTED,
        total_claimed_amount=Decimal("1250.00"),
    )
    db.add(claim)
    db.flush()

    # Item 1: Daily Food ₹400
    item1 = ExpenseItem(
        tenant_id="db_test",
        claim_id=claim.id,
        expense_date=date(2026, 11, 10),
        category=ExpenseCategory.FOOD,
        vendor_merchant_name="Annapurna Restaurant",
        bill_number="BILL-101",
        bill_date=date(2026, 11, 10),
        claimed_amount=Decimal("400.00"),
        receipt_url="https://storage.local/food_bill_101.jpg",
        is_receipt_attached=True,
    )
    # Item 2: Auto conveyance ₹120 (unbilled allowed under ₹150)
    item2 = ExpenseItem(
        tenant_id="db_test",
        claim_id=claim.id,
        expense_date=date(2026, 11, 10),
        category=ExpenseCategory.LOCAL_TRANSIT,
        vendor_merchant_name="Local Auto",
        claimed_amount=Decimal("120.00"),
        is_receipt_attached=False,
        justification="Station to hotel auto",
    )
    db.add_all([item1, item2])
    db.flush()

    assert len(claim.items) == 2

    # Manager endorses
    claim.manager_endorsed = True
    claim.manager_notes = "Verified official duty"
    claim.status = ExpenseStatus.MANAGER_ENDORSED
    db.flush()

    assert claim.status == ExpenseStatus.MANAGER_ENDORSED

    # Finance audits & approves
    claim.status = ExpenseStatus.FINANCE_APPROVED
    claim.total_approved_amount = Decimal("520.00")
    claim.total_deducted_amount = Decimal("0.00")
    db.flush()

    assert claim.status == ExpenseStatus.FINANCE_APPROVED

    # Finance settles with Bank UTR number
    claim.payment_mode = PaymentMode.NEFT
    claim.payment_date = date(2026, 11, 15)
    claim.bank_utr_number = "SBIN000123456789"
    claim.settled_at = datetime.now(timezone.utc)
    claim.status = ExpenseStatus.SETTLED
    db.flush()

    assert claim.status == ExpenseStatus.SETTLED
    assert claim.bank_utr_number == "SBIN000123456789"
    assert claim.payment_mode == PaymentMode.NEFT


def test_v4_expense_policy_schema_validation():
    """Verify policy ceiling constants and flexible ExpenseItemCreate schema submissions."""
    assert FOOD_ALLOWANCE_PER_DAY == 400
    assert LOCAL_TRANSIT_UNBILLED_MAX == 150

    # Flexible food submission without receipt is accepted
    food_item = ExpenseItemCreate(
        expense_date=date(2026, 11, 10),
        category=ExpenseCategory.FOOD,
        vendor_merchant_name="Lunch Dhaba",
        claimed_amount=Decimal("350.00"),
        receipt_url=None,
        is_receipt_attached=False,
    )
    assert food_item.claimed_amount == Decimal("350.00")

    # Local transit <= 150 without receipt is permitted
    item_ok = ExpenseItemCreate(
        expense_date=date(2026, 11, 10),
        category=ExpenseCategory.LOCAL_TRANSIT,
        vendor_merchant_name="Local Auto",
        claimed_amount=Decimal("150.00"),
        receipt_url=None,
        is_receipt_attached=False,
    )
    assert item_ok.claimed_amount == Decimal("150.00")

    # Flexible local transit > 150 without receipt is also permitted on submission
    item_over = ExpenseItemCreate(
        expense_date=date(2026, 11, 10),
        category=ExpenseCategory.LOCAL_TRANSIT,
        vendor_merchant_name="Long Auto Ride",
        claimed_amount=Decimal("180.00"),
        receipt_url=None,
        is_receipt_attached=False,
    )
    assert item_over.claimed_amount == Decimal("180.00")


from fastapi.testclient import TestClient
from app.core.security import create_access_token
from app.database import get_db
from app.main import app


def test_v4_manager_occupancy_endpoint(db, monkeypatch):
    """Test PUT /team/members/{id}/occupancy endpoint."""
    def same_session():
        yield db

    app.dependency_overrides[get_db] = same_session
    try:
        manager = User(
            tenant_id="db_test",
            email="man_vipul@designboxed.com",
            full_name="Vipul Manager",
            role=Role.MANAGER,
            gender=Gender.MALE,
            password_hash="hashed_pw",
        )
        db.add(manager)
        db.flush()

        member = User(
            tenant_id="db_test",
            email="mem_kiran@designboxed.com",
            full_name="Kiran Field",
            role=Role.GROUND_STAFF,
            gender=Gender.FEMALE,
            manager_id=manager.id,
            hotel_occupancy_entitlement=HotelOccupancy.SINGLE,
            password_hash="hashed_pw",
        )
        db.add(member)
        db.flush()

        token, _ = create_access_token(user_id=manager.id, role=str(manager.role), tenant_id="db_test")
        client = TestClient(app)

        res = client.put(
            f"/team/members/{member.id}/occupancy",
            headers={"Authorization": f"Bearer {token}"},
            json={"hotel_occupancy_entitlement": "DOUBLE"},
        )
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["hotel_occupancy_entitlement"] == "DOUBLE"

        # Verify DB persisted
        db.refresh(member)
        assert member.hotel_occupancy_entitlement == HotelOccupancy.DOUBLE
    finally:
        app.dependency_overrides.pop(get_db, None)
    """Verify Manager can configure team member hotel occupancy entitlement for all their bookings (SOW Section 1.1 item 2)."""
    manager = User(
        tenant_id="db_test",
        email="manager_anil@designboxed.com",
        full_name="Anil Manager",
        role=Role.MANAGER,
        gender=Gender.MALE,
    )
    db.add(manager)
    db.flush()

    member = User(
        tenant_id="db_test",
        email="member_sunil@designboxed.com",
        full_name="Sunil Member",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        manager_id=manager.id,
        hotel_occupancy_entitlement=HotelOccupancy.SINGLE,  # default
    )
    db.add(member)
    db.flush()

    # Initial entitlement is Single
    assert member.hotel_occupancy_entitlement == HotelOccupancy.SINGLE

    # Manager evaluates member and promotes to Double Occupancy for all future bookings
    member.hotel_occupancy_entitlement = HotelOccupancy.DOUBLE
    db.flush()

    assert member.hotel_occupancy_entitlement == HotelOccupancy.DOUBLE

    # When Sunil creates a hotel request, occupancy entitlement carries through
    project = Project(tenant_id="db_test", code="CMP-2026-0005", name="Field Op")
    db.add(project)
    db.flush()

    req = TravelRequest(
        tenant_id="db_test",
        request_type=RequestType.HOTEL,
        project_id=project.id,
        requester_id=member.id,
        hotel_city="Bhopal",
        hotel_occupancy=member.hotel_occupancy_entitlement,
        travel_reason="Strategy field visit",
    )
    db.add(req)
    db.flush()

    assert req.hotel_occupancy == HotelOccupancy.DOUBLE
