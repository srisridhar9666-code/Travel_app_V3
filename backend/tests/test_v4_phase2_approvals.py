"""
Phase 2 Automated Verification:
- Universal Mandatory Two-Tier Approval Pipeline (SOW Section 6)
- Manager Occupancy Evaluation (SOW Section 6 & 7)
- Corporate Travel Policy Engine & City Lodging Limits (SOW Section 4.2)
- Vendor Assignment & READY_FOR_VENDOR State Transition (SOW Section 6)
"""
from dataclasses import dataclass
from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient

from app.core.enums import (
    CityTier,
    HotelOccupancy,
    ManagerRecommendation,
    RequestPriority,
    RequestStatus,
    RequestType,
    Role,
    TravellerStatus,
    VendorKind,
    FOOD_ALLOWANCE_PER_DAY,
    LOCAL_TRANSIT_UNBILLED_MAX,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.user import User
from app.models.vendor import Vendor
from app.services import policy, notifications

TENANT = "designboxed"


@dataclass(frozen=True)
class Ref:
    id: int
    role: Role
    email: str
    name: str = ""


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def auth_header(ref: Ref) -> dict[str, str]:
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(db):
    project = Project(tenant_id=TENANT, name="V4 Field Deployment", code="V4-PROJ-01")
    db.add(project)
    db.commit()

    admin = User(
        tenant_id=TENANT,
        email="ops_admin@designboxed.com",
        full_name="Operations Admin",
        role=Role.ADMIN,
        password_hash="x",
    )
    manager = User(
        tenant_id=TENANT,
        email="area_manager@designboxed.com",
        full_name="Area Manager",
        role=Role.MANAGER,
        password_hash="x",
    )
    db.add_all([admin, manager])
    db.commit()

    staff_with_mgr = User(
        tenant_id=TENANT,
        email="ground_staff1@designboxed.com",
        full_name="Ground Staff One",
        role=Role.GROUND_STAFF,
        manager_id=manager.id,
        hotel_occupancy_entitlement=HotelOccupancy.SINGLE,
        password_hash="x",
    )
    staff_solo = User(
        tenant_id=TENANT,
        email="ground_solo@designboxed.com",
        full_name="Solo Field Staff",
        role=Role.GROUND_STAFF,
        manager_id=None,
        hotel_occupancy_entitlement=HotelOccupancy.SINGLE,
        password_hash="x",
    )
    vendor = Vendor(
        tenant_id=TENANT,
        name="Taj Corporate Hospitality Services",
        kind=VendorKind.HOTEL,
        contact_name="Sunil Verma",
        email="vendor@tajservices.com",
        phone="9876543210",
    )
    db.add_all([staff_with_mgr, staff_solo, vendor])
    db.commit()

    return {
        "project_id": project.id,
        "admin": Ref(admin.id, admin.role, admin.email, admin.full_name),
        "manager": Ref(manager.id, manager.role, manager.email, manager.full_name),
        "staff": Ref(staff_with_mgr.id, staff_with_mgr.role, staff_with_mgr.email, staff_with_mgr.full_name),
        "solo": Ref(staff_solo.id, staff_solo.role, staff_solo.email, staff_solo.full_name),
        "vendor_id": vendor.id,
        "vendor_name": vendor.name,
    }


def create_hotel_request(client, user_ref: Ref, project_id: int, city="Mumbai", days_ahead=5):
    check_in = date.today() + timedelta(days=days_ahead)
    check_out = check_in + timedelta(days=2)
    resp = client.post(
        "/requests",
        headers=auth_header(user_ref),
        json={
            "request_type": "HOTEL",
            "project_id": project_id,
            "hotel_city": city,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "travel_reason": "Field operations campaign survey",
            "priority": "HIGH",
            "traveller_ids": [user_ref.id],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Policy Engine Verification
# ---------------------------------------------------------------------------

def test_city_tier_classification():
    # Category A: Metros
    assert policy.classify_city("Mumbai") == CityTier.CATEGORY_A
    assert policy.classify_city("Delhi") == CityTier.CATEGORY_A
    assert policy.classify_city("New Delhi") == CityTier.CATEGORY_A
    assert policy.classify_city("Bengaluru") == CityTier.CATEGORY_A
    assert policy.classify_city("Bangalore Central") == CityTier.CATEGORY_A
    assert policy.classify_city("Kolkata") == CityTier.CATEGORY_A
    assert policy.classify_city("Chennai") == CityTier.CATEGORY_A
    assert policy.classify_city("Hyderabad") == CityTier.CATEGORY_A

    # Category B: Capitals & Tier 2
    assert policy.classify_city("Lucknow") == CityTier.CATEGORY_B
    assert policy.classify_city("Jaipur") == CityTier.CATEGORY_B
    assert policy.classify_city("Chandigarh") == CityTier.CATEGORY_B
    assert policy.classify_city("Bhopal") == CityTier.CATEGORY_B
    assert policy.classify_city("Patna") == CityTier.CATEGORY_B
    assert policy.classify_city("Pune") == CityTier.CATEGORY_B

    # Category C: All others
    assert policy.classify_city("Aligarh") == CityTier.CATEGORY_C
    assert policy.classify_city("Barabanki") == CityTier.CATEGORY_C
    assert policy.classify_city("Dharamsala Rural") == CityTier.CATEGORY_C


def test_lodging_rate_ceilings():
    # Cat A: ₹1,500 / ₹2,000 / ₹2,500
    assert policy.lodging_ceiling("Mumbai", HotelOccupancy.SINGLE) == 1500
    assert policy.lodging_ceiling("Mumbai", HotelOccupancy.DOUBLE) == 2000
    assert policy.lodging_ceiling("Mumbai", HotelOccupancy.TRIPLE) == 2500

    # Cat B: ₹1,000 / ₹1,500 / ₹2,000
    assert policy.lodging_ceiling("Lucknow", HotelOccupancy.SINGLE) == 1000
    assert policy.lodging_ceiling("Lucknow", HotelOccupancy.DOUBLE) == 1500
    assert policy.lodging_ceiling("Lucknow", HotelOccupancy.TRIPLE) == 2000

    # Cat C: ₹750 / ₹1,200 / ₹1,500
    assert policy.lodging_ceiling("Barabanki", HotelOccupancy.SINGLE) == 750
    assert policy.lodging_ceiling("Barabanki", HotelOccupancy.DOUBLE) == 1200
    assert policy.lodging_ceiling("Barabanki", HotelOccupancy.TRIPLE) == 1500


def test_lodging_rate_validation():
    # Compliant rate in Mumbai
    res1 = policy.validate_lodging_rate("Mumbai", 1400, HotelOccupancy.SINGLE)
    assert res1["is_compliant"] is True
    assert res1["excess"] == 0.0

    # Non-compliant rate in Mumbai (exceeds ₹1,500 limit by ₹300)
    res2 = policy.validate_lodging_rate("Mumbai", 1800, HotelOccupancy.SINGLE)
    assert res2["is_compliant"] is False
    assert res2["excess"] == 300.0


def test_policy_api_endpoints(client, env):
    # Overview endpoint
    resp = client.get("/policies/overview", headers=auth_header(env["staff"]))
    assert resp.status_code == 200
    data = resp.json()
    assert data["food_allowance_per_day"] == FOOD_ALLOWANCE_PER_DAY
    assert data["local_transit_unbilled_max"] == LOCAL_TRANSIT_UNBILLED_MAX
    assert data["lodging_limits"]["CATEGORY_A"]["SINGLE"] == 1500

    # Lodging ceiling query endpoint
    resp = client.get(
        "/policies/lodging-ceiling",
        headers=auth_header(env["staff"]),
        params={"city": "Bengaluru", "occupancy": "DOUBLE"},
    )
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["tier"] == "CATEGORY_A"
    assert detail["limit_per_night"] == 2000


# ---------------------------------------------------------------------------
# 2. Strict Two-Tier Approval Pipeline Tests (SOW Section 6)
# ---------------------------------------------------------------------------

def test_admin_approval_blocked_before_manager_approval(client, env):
    """Admin approval is strictly blocked (HTTP 422) if manager has not recommended."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Mumbai")
    req_id = req["id"]
    traveller_id = req["travellers"][0]["id"]

    # Admin attempts to approve before manager review
    resp = client.post(
        f"/requests/{req_id}/travellers/{traveller_id}/decide",
        headers=auth_header(env["admin"]),
        json={
            "to_status": "APPROVED",
            "reason": "Admin pre-approval attempt",
        },
    )
    assert resp.status_code == 422
    assert "Manager approval (L1) is required before Admin can approve" in resp.json()["detail"]


def test_admin_approval_blocked_if_manager_rejected(client, env):
    """Admin approval is strictly blocked (HTTP 422) if manager rejected the request."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Mumbai")
    req_id = req["id"]
    traveller_id = req["travellers"][0]["id"]

    # Manager rejects with reason
    m_resp = client.post(
        f"/requests/{req_id}/recommendation",
        headers=auth_header(env["manager"]),
        json={
            "recommendation": "NOT_RECOMMENDED",
            "comment": "Team deployment redirected elsewhere",
        },
    )
    assert m_resp.status_code == 200

    # Admin attempts to approve rejected request
    resp = client.post(
        f"/requests/{req_id}/travellers/{traveller_id}/decide",
        headers=auth_header(env["admin"]),
        json={
            "to_status": "APPROVED",
            "reason": "Overriding rejection without waiver",
        },
    )
    assert resp.status_code == 422
    assert "was not recommended by their manager" in resp.json()["detail"]


def test_manager_approval_then_admin_approval_flow(client, env):
    """Full Happy Path: Manager approves (L1) -> Admin approves (L2)."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Mumbai")
    req_id = req["id"]
    traveller_id = req["travellers"][0]["id"]

    # Step 1: Manager approves (L1)
    m_resp = client.post(
        f"/requests/{req_id}/recommendation",
        headers=auth_header(env["manager"]),
        json={
            "recommendation": "RECOMMENDED",
            "comment": "Approved for official survey work",
        },
    )
    assert m_resp.status_code == 200
    assert m_resp.json()["travellers"][0]["manager_recommendation"] == "RECOMMENDED"

    # Step 2: Admin approves (L2)
    a_resp = client.post(
        f"/requests/{req_id}/travellers/{traveller_id}/decide",
        headers=auth_header(env["admin"]),
        json={
            "to_status": "APPROVED",
            "reason": "Approved and policy compliant",
        },
    )
    assert a_resp.status_code == 200
    data = a_resp.json()
    assert data["travellers"][0]["status"] == "APPROVED"
    assert data["status"] == "APPROVED"


def test_solo_staff_without_manager_can_be_approved_directly(client, env):
    """Solo staff with no reporting manager can be approved by Admin directly."""
    req = create_hotel_request(client, env["solo"], env["project_id"], city="Delhi")
    req_id = req["id"]
    traveller_id = req["travellers"][0]["id"]

    # Admin approves directly
    resp = client.post(
        f"/requests/{req_id}/travellers/{traveller_id}/decide",
        headers=auth_header(env["admin"]),
        json={
            "to_status": "APPROVED",
            "reason": "Direct management approval",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["travellers"][0]["status"] == "APPROVED"


# ---------------------------------------------------------------------------
# 3. Manager Occupancy Evaluation & Policy Enrichment
# ---------------------------------------------------------------------------

def test_manager_occupancy_evaluation_during_approval(client, env):
    """Manager evaluates Single -> Double Occupancy on Hotel Request during L1 approval."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Kolkata")
    req_id = req["id"]
    assert req["hotel_occupancy"] == "SINGLE"
    assert req["city_tier"] == "CATEGORY_A"
    assert req["lodging_limit_per_night"] == 1500

    # Manager approves and changes occupancy to DOUBLE
    m_resp = client.post(
        f"/requests/{req_id}/recommendation",
        headers=auth_header(env["manager"]),
        json={
            "recommendation": "RECOMMENDED",
            "comment": "Double occupancy approved for joint field assessment",
            "hotel_occupancy": "DOUBLE",
        },
    )
    assert m_resp.status_code == 200
    updated = m_resp.json()
    assert updated["hotel_occupancy"] == "DOUBLE"
    # Lodging ceiling automatically updates to Category A Double limit (₹2,000)
    assert updated["lodging_limit_per_night"] == 2000


def test_admin_vendor_assignment_transitions_to_ready_for_vendor(client, env):
    """Assigning vendor upon Admin approval sets status to READY_FOR_VENDOR."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Mumbai")
    req_id = req["id"]
    traveller_id = req["travellers"][0]["id"]

    # Manager recommends
    client.post(
        f"/requests/{req_id}/recommendation",
        headers=auth_header(env["manager"]),
        json={"recommendation": "RECOMMENDED", "comment": "Approved for dispatch"},
    )

    # Admin approves with vendor assignment
    resp = client.post(
        f"/requests/{req_id}/travellers/{traveller_id}/decide",
        headers=auth_header(env["admin"]),
        json={
            "to_status": "APPROVED",
            "reason": "Final approval and vendor dispatch",
            "assigned_vendor_id": env["vendor_id"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_vendor_id"] == env["vendor_id"]
    assert data["assigned_vendor_name"] == env["vendor_name"]
    assert data["status"] == "READY_FOR_VENDOR"


def test_dedicated_assign_vendor_endpoint(client, env):
    """Admin uses POST /requests/{id}/assign-vendor to dispatch or reassign vendor."""
    req = create_hotel_request(client, env["staff"], env["project_id"], city="Mumbai")
    req_id = req["id"]

    # Assign vendor via dedicated endpoint
    resp = client.post(
        f"/requests/{req_id}/assign-vendor",
        headers=auth_header(env["admin"]),
        json={"vendor_id": env["vendor_id"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["assigned_vendor_id"] == env["vendor_id"]
    assert data["assigned_vendor_name"] == env["vendor_name"]
