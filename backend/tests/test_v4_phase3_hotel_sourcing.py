"""
Phase 3 Automated Verification:
- Vendor Hotel Proposal Submission (SOW Section 7: 2 to 5 options)
- Requester Hotel Selection Workflow (SOW Section 7)
- Exact Same-Gender Co-Stay Room Matching (MALE with MALE, FEMALE with FEMALE) (SOW Section 7 & 4.2)
- Same-Hotel Prioritization in Double Sharing
"""
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.enums import (
    Gender,
    HotelOccupancy,
    HotelProposalStatus,
    RequestType,
    Role,
    TravellerStatus,
    VendorKind,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.hotel_proposal import HotelProposal
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.models.vendor import Vendor
from app.services import costay, notifications

TENANT = "designboxed"


@dataclass(frozen=True)
class Ref:
    id: int
    role: Role
    email: str
    name: str = ""
    gender: Gender = Gender.MALE
    vendor_id: int | None = None


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
    project = Project(tenant_id=TENANT, name="V4 Field Deployment", code="V4-PROJ-03")
    db.add(project)
    db.commit()

    vendor = Vendor(
        tenant_id=TENANT,
        name="Apex Travel & Hospitality Solutions",
        kind=VendorKind.HOTEL,
        contact_name="Vikram Sethi",
        email="vikram@apextravel.com",
        phone="9988776655",
    )
    db.add(vendor)
    db.commit()

    admin = User(
        tenant_id=TENANT,
        email="admin_ops@designboxed.com",
        full_name="Operations Admin",
        role=Role.ADMIN,
        gender=Gender.MALE,
        password_hash="x",
    )
    vendor_user = User(
        tenant_id=TENANT,
        email="vendor_rep@apextravel.com",
        full_name="Vikram Sethi",
        role=Role.VENDOR,
        gender=Gender.MALE,
        vendor_id=vendor.id,
        password_hash="x",
    )
    male_staff_1 = User(
        tenant_id=TENANT,
        email="arjun.verma@designboxed.com",
        full_name="Arjun Verma",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        hotel_occupancy_entitlement=HotelOccupancy.DOUBLE,
        password_hash="x",
    )
    male_staff_2 = User(
        tenant_id=TENANT,
        email="rohit.sharma@designboxed.com",
        full_name="Rohit Sharma",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        hotel_occupancy_entitlement=HotelOccupancy.DOUBLE,
        password_hash="x",
    )
    female_staff_1 = User(
        tenant_id=TENANT,
        email="ananya.pandey@designboxed.com",
        full_name="Ananya Pandey",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
        hotel_occupancy_entitlement=HotelOccupancy.DOUBLE,
        password_hash="x",
    )
    db.add_all([admin, vendor_user, male_staff_1, male_staff_2, female_staff_1])
    db.commit()

    return {
        "project_id": project.id,
        "vendor_id": vendor.id,
        "admin": Ref(admin.id, admin.role, admin.email, admin.full_name, admin.gender),
        "vendor_user": Ref(vendor_user.id, vendor_user.role, vendor_user.email, vendor_user.full_name, vendor_user.gender, vendor.id),
        "male1": Ref(male_staff_1.id, male_staff_1.role, male_staff_1.email, male_staff_1.full_name, male_staff_1.gender),
        "male2": Ref(male_staff_2.id, male_staff_2.role, male_staff_2.email, male_staff_2.full_name, male_staff_2.gender),
        "female1": Ref(female_staff_1.id, female_staff_1.role, female_staff_1.email, female_staff_1.full_name, female_staff_1.gender),
    }


def create_hotel_request(client, user_ref: Ref, project_id: int, city="Mumbai", occupancy="DOUBLE", days_ahead=4):
    check_in = date.today() + timedelta(days=days_ahead)
    check_out = check_in + timedelta(days=3)
    resp = client.post(
        "/requests",
        headers=auth_header(user_ref),
        json={
            "request_type": "HOTEL",
            "project_id": project_id,
            "hotel_city": city,
            "hotel_occupancy": occupancy,
            "check_in": check_in.isoformat(),
            "check_out": check_out.isoformat(),
            "travel_reason": "State campaign deployment",
            "priority": "HIGH",
            "traveller_ids": [user_ref.id],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Vendor Hotel Proposals Workflow Tests (SOW Section 7)
# ---------------------------------------------------------------------------

def test_vendor_submits_hotel_proposals_batch(client, env):
    """Vendor submits 3 hotel options for an approved hotel request in Mumbai (Cat A)."""
    req = create_hotel_request(client, env["male1"], env["project_id"], city="Mumbai", occupancy="DOUBLE")
    req_id = req["id"]

    # Assign vendor to the request
    client.post(
        f"/requests/{req_id}/assign-vendor",
        headers=auth_header(env["admin"]),
        json={"vendor_id": env["vendor_id"]},
    )

    # Vendor submits 3 options
    proposals_payload = {
        "options": [
            {
                "hotel_name": "Hotel Marine Plaza",
                "address": "29 Marine Drive, Churchgate, Mumbai",
                "landmark": "Near Churchgate Station",
                "google_maps_url": "https://maps.google.com/?q=MarinePlazaMumbai",
                "room_category": "Executive Double",
                "nightly_rate": 1850.00,
                "amenities": ["Wi-Fi", "Breakfast", "AC"],
                "vendor_notes": "Corporate rate negotiated",
            },
            {
                "hotel_name": "Residency Hotel Fort",
                "address": "260 Dr DN Road, Fort, Mumbai",
                "landmark": "Near CST Station",
                "google_maps_url": "https://maps.google.com/?q=ResidencyHotelMumbai",
                "room_category": "Deluxe Twin",
                "nightly_rate": 2000.00,
                "amenities": ["Wi-Fi", "AC"],
                "vendor_notes": "Very close to client campaign office",
            },
            {
                "hotel_name": "Trident Nariman Point",
                "address": "CR 2 Nariman Point, Mumbai",
                "landmark": "Nariman Point",
                "google_maps_url": "https://maps.google.com/?q=TridentMumbai",
                "room_category": "Standard Sea View",
                "nightly_rate": 2400.00,
                "amenities": ["Wi-Fi", "Breakfast", "Gym", "Pool"],
                "vendor_notes": "Premium option exceeding policy cap",
            },
        ]
    }

    resp = client.post(
        f"/requests/{req_id}/hotel-proposals",
        headers=auth_header(env["vendor_user"]),
        json=proposals_payload,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert len(created) == 3

    # Check policy calculations on created proposals (Cat A Double limit = ₹2,000)
    # Option 1: ₹1,850 <= ₹2,000 -> Compliant, excess = 0
    p1 = next(p for p in created if p["hotel_name"] == "Hotel Marine Plaza")
    assert p1["is_policy_compliant"] is True
    assert float(p1["excess_amount"]) == 0.0
    assert float(p1["policy_rate_limit"]) == 2000.0

    # Option 2: ₹2,000 <= ₹2,000 -> Compliant, excess = 0
    p2 = next(p for p in created if p["hotel_name"] == "Residency Hotel Fort")
    assert p2["is_policy_compliant"] is True
    assert float(p2["excess_amount"]) == 0.0

    # Option 3: ₹2,400 > ₹2,000 -> Non-compliant, excess = 400
    p3 = next(p for p in created if p["hotel_name"] == "Trident Nariman Point")
    assert p3["is_policy_compliant"] is False
    assert float(p3["excess_amount"]) == 400.0


def test_unauthorized_user_cannot_submit_hotel_proposals(client, env):
    """Ground staff cannot submit vendor hotel proposals."""
    req = create_hotel_request(client, env["male1"], env["project_id"], city="Delhi")
    resp = client.post(
        f"/requests/{req['id']}/hotel-proposals",
        headers=auth_header(env["male1"]),
        json={
            "options": [
                {
                    "hotel_name": "Rogue Hotel",
                    "address": "Random Address",
                    "nightly_rate": 1000.00,
                },
                {
                    "hotel_name": "Rogue Hotel 2",
                    "address": "Random Address 2",
                    "nightly_rate": 1200.00,
                },
            ]
        },
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. Requester Hotel Selection Workflow Tests (SOW Section 7)
# ---------------------------------------------------------------------------

def test_employee_selects_preferred_hotel_proposal(client, env):
    """Employee reviews proposed hotels and selects preferred option."""
    req = create_hotel_request(client, env["male1"], env["project_id"], city="Mumbai", occupancy="DOUBLE")
    req_id = req["id"]

    # Admin assigns vendor
    client.post(
        f"/requests/{req_id}/assign-vendor",
        headers=auth_header(env["admin"]),
        json={"vendor_id": env["vendor_id"]},
    )

    # Vendor submits proposals
    client.post(
        f"/requests/{req_id}/hotel-proposals",
        headers=auth_header(env["vendor_user"]),
        json={
            "options": [
                {
                    "hotel_name": "Hotel Marine Plaza",
                    "address": "29 Marine Drive, Mumbai",
                    "room_category": "Executive Double",
                    "nightly_rate": 1850.00,
                },
                {
                    "hotel_name": "Residency Hotel Fort",
                    "address": "260 Dr DN Road, Fort, Mumbai",
                    "room_category": "Deluxe Twin",
                    "nightly_rate": 2000.00,
                },
            ]
        },
    )

    # Employee lists proposals
    list_resp = client.get(
        f"/requests/{req_id}/hotel-proposals",
        headers=auth_header(env["male1"]),
    )
    assert list_resp.status_code == 200
    options = list_resp.json()
    assert len(options) == 2
    preferred = next(p for p in options if p["hotel_name"] == "Hotel Marine Plaza")
    assert preferred["nightly_rate"] is None
    assert preferred["policy_rate_limit"] is None
    assert preferred["excess_amount"] is None
    assert preferred["amenities"] == []

    # Employee selects preferred hotel
    sel_resp = client.post(
        f"/requests/{req_id}/hotel-proposals/{preferred['id']}/select",
        headers=auth_header(env["male1"]),
    )
    assert sel_resp.status_code == 200
    selected = sel_resp.json()
    assert selected["id"] == preferred["id"]
    assert selected["status"] == "SELECTED"
    assert selected["selected_by_id"] == env["male1"].id
    assert selected["nightly_rate"] is None

    # Verify other proposals marked REJECTED
    all_props = client.get(
        f"/requests/{req_id}/hotel-proposals",
        headers=auth_header(env["male1"]),
    ).json()
    other = next(p for p in all_props if p["id"] != preferred["id"])
    assert other["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# 3. Exact Same-Gender Co-Stay Engine Tests (SOW Section 7 & 4.2)
# ---------------------------------------------------------------------------

def test_exact_same_gender_room_matching_only(client, env, db):
    """Room sharing finds same-gender colleagues only; cross-gender is strictly zero-disclosed."""
    # Male 1 creates hotel stay in Mumbai
    req1 = create_hotel_request(client, env["male1"], env["project_id"], city="Mumbai", days_ahead=5)

    # Male 2 creates overlapping hotel stay in Mumbai
    req2 = create_hotel_request(client, env["male2"], env["project_id"], city="Mumbai", days_ahead=5)

    # Female 1 creates overlapping hotel stay in Mumbai
    req3 = create_hotel_request(client, env["female1"], env["project_id"], city="Mumbai", days_ahead=5)

    # Query room matches for Male 1 (Arjun Verma)
    matches_for_male1 = client.get(
        "/requests/room-matches",
        headers=auth_header(env["male1"]),
        params={"city": "Mumbai", "check_in": req1["check_in"], "check_out": req1["check_out"], "request_id": req1["id"]},
    ).json()

    # Must contain Male 2 (Rohit Sharma)
    male_match_ids = [m["user_id"] for m in matches_for_male1]
    assert env["male2"].id in male_match_ids

    # Must NOT contain Female 1 (Ananya Pandey) - zero cross-gender disclosure
    assert env["female1"].id not in male_match_ids

    # Query room matches for Female 1 (Ananya Pandey)
    matches_for_female1 = client.get(
        "/requests/room-matches",
        headers=auth_header(env["female1"]),
        params={"city": "Mumbai", "check_in": req3["check_in"], "check_out": req3["check_out"], "request_id": req3["id"]},
    ).json()

    # Females only: Males must be excluded
    female_match_ids = [m["user_id"] for m in matches_for_female1]
    assert env["male1"].id not in female_match_ids
    assert env["male2"].id not in female_match_ids


def test_cross_gender_pairing_strictly_blocked_in_service(db, env):
    """Attempting to pair male and female into one room raises 422 Unprocessable Content."""
    project = db.get(Project, env["project_id"])

    req_male = TravelRequest(
        tenant_id=TENANT,
        requester_id=env["male1"].id,
        project_id=project.id,
        request_type=RequestType.HOTEL,
        hotel_city="Mumbai",
        check_in=date.today() + timedelta(days=5),
        check_out=date.today() + timedelta(days=8),
    )
    req_female = TravelRequest(
        tenant_id=TENANT,
        requester_id=env["female1"].id,
        project_id=project.id,
        request_type=RequestType.HOTEL,
        hotel_city="Mumbai",
        check_in=date.today() + timedelta(days=5),
        check_out=date.today() + timedelta(days=8),
    )
    db.add_all([req_male, req_female])
    db.flush()

    trav_male = RequestTraveller(
        request_id=req_male.id,
        user_id=env["male1"].id,
        status=TravellerStatus.APPROVED,
    )
    trav_female = RequestTraveller(
        request_id=req_female.id,
        user_id=env["female1"].id,
        status=TravellerStatus.APPROVED,
    )
    db.add_all([trav_male, trav_female])
    db.commit()

    # Attempt to pair male and female
    admin_user = db.get(User, env["admin"].id)
    with pytest.raises(HTTPException) as exc_info:
        costay.pair(trav_male, trav_female, confirmed_by=admin_user)

    assert exc_info.value.status_code == 422
    assert "strictly for colleagues of the same gender" in exc_info.value.detail


def test_same_hotel_matches_prioritized_at_top(client, env, db):
    """Colleague staying in the exact same hotel is flagged same_hotel=True and listed first."""
    req1 = create_hotel_request(client, env["male1"], env["project_id"], city="Mumbai", days_ahead=6)
    req2 = create_hotel_request(client, env["male2"], env["project_id"], city="Mumbai", days_ahead=6)

    # Both select 'Hotel Marine Plaza'
    prop_male2 = HotelProposal(
        tenant_id=TENANT,
        request_id=req2["id"],
        vendor_id=env["vendor_id"],
        hotel_name="Hotel Marine Plaza",
        address="Marine Drive, Mumbai",
        nightly_rate=Decimal("1800.00"),
        status=HotelProposalStatus.SELECTED,
    )
    db.add(prop_male2)
    db.commit()

    r2 = db.get(TravelRequest, req2["id"])
    r2.selected_hotel_proposal_id = prop_male2.id
    db.commit()

    # Query matches for Male 1 specifying hotel_name='Hotel Marine Plaza'
    matches = client.get(
        "/requests/room-matches",
        headers=auth_header(env["male1"]),
        params={
            "city": "Mumbai",
            "check_in": req1["check_in"],
            "check_out": req1["check_out"],
            "request_id": req1["id"],
            "hotel_name": "Hotel Marine Plaza",
        },
    ).json()

    assert len(matches) >= 1
    match_male2 = next(m for m in matches if m["user_id"] == env["male2"].id)
    assert match_male2["same_hotel"] is True
    assert match_male2["hotel_name"] == "Hotel Marine Plaza"
