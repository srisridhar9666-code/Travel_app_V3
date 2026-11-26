"""
Test suite for Sriyatra Master Specification v4.0 - Phase 4:
In-Transit Cab Telemetry & Hotel Stay GPS Verification.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.core.enums import (
    CabTrip,
    CabType,
    Gender,
    HotelOccupancy,
    RequestType,
    Role,
    StayVerificationStatus,
    TelemetryStatus,
    TravellerStatus,
    VendorKind,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.user import User
from app.models.vendor import Vendor
from app.services import notifications

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
    project = Project(tenant_id=TENANT, name="V4 Field Deployment", code="V4-PROJ-04")
    db.add(project)
    db.commit()

    admin = User(
        tenant_id=TENANT,
        email="admin_tel@designboxed.com",
        full_name="Operations Admin",
        role=Role.ADMIN,
        gender=Gender.MALE,
        password_hash="x",
    )
    finance = User(
        tenant_id=TENANT,
        email="finance_tel@designboxed.com",
        full_name="Finance Auditor",
        role=Role.FINANCE,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    staff = User(
        tenant_id=TENANT,
        email="ground_tel@designboxed.com",
        full_name="Vikrant Ground",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        password_hash="x",
    )
    unrelated_user = User(
        tenant_id=TENANT,
        email="unrelated@designboxed.com",
        full_name="Unrelated Staff",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    db.add_all([admin, finance, staff, unrelated_user])
    db.commit()

    return {
        "project_id": project.id,
        "admin": Ref(admin.id, admin.role, admin.email, admin.full_name),
        "finance": Ref(finance.id, finance.role, finance.email, finance.full_name),
        "staff": Ref(staff.id, staff.role, staff.email, staff.full_name),
        "unrelated": Ref(unrelated_user.id, unrelated_user.role, unrelated_user.email, unrelated_user.full_name),
    }


def create_cab_request(client, user_ref: Ref, project_id: int, cab_trip: CabTrip = CabTrip.LOCAL) -> int:
    payload = {
        "request_type": "LOCAL_CAB",
        "project_id": project_id,
        "origin_state": "Telangana",
        "pickup_city": "Hyderabad",
        "destination_state": "Telangana",
        "drop_city": "Hyderabad",
        "origin": "Gachibowli",
        "destination": "Secunderabad",
        "start_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()[:19],
        "cab_trip": cab_trip.value,
        "cab_type": "SEDAN",
        "cab_distance_km": 40 if cab_trip == CabTrip.LOCAL else 250,
        "travel_reason": "Field deployment cab",
        "priority": "HIGH",
        "traveller_ids": [user_ref.id],
    }
    resp = client.post("/requests", json=payload, headers=auth_header(user_ref))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def create_hotel_request(client, user_ref: Ref, project_id: int) -> int:
    payload = {
        "request_type": "HOTEL",
        "project_id": project_id,
        "hotel_state": "Karnataka",
        "hotel_city": "Bengaluru",
        "check_in": (date.today() + timedelta(days=2)).isoformat(),
        "check_out": (date.today() + timedelta(days=5)).isoformat(),
        "travel_reason": "Field deployment hotel",
        "priority": "HIGH",
        "traveller_ids": [user_ref.id],
    }
    resp = client.post("/requests", json=payload, headers=auth_header(user_ref))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- Cab Telemetry Tests ----------------------------------------------------

def test_cab_trip_start_and_end_distance_calculation(client, env, db):
    """Ground staff starts morning trip, ends evening trip; delta distance and duration computed."""
    req_id = create_cab_request(client, env["staff"], env["project_id"])

    # Start morning trip
    start_payload = {
        "start_odometer_km": 14250.0,
        "start_odometer_photo_url": "telemetry/14250_start.jpg",
        "start_gps_lat": 17.4399,
        "start_gps_lng": 78.3489,
        "driver_name": "Ramesh Kumar",
        "driver_phone": "9876543210",
        "vehicle_number": "TS09EA1234",
        "vehicle_model": "Swift Dzire",
    }
    start_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/start",
        json=start_payload,
        headers=auth_header(env["staff"]),
    )
    assert start_resp.status_code == 201, start_resp.text
    data = start_resp.json()
    assert data["status"] == "IN_PROGRESS"
    assert data["start_odometer_km"] == 14250.0
    telemetry_id = data["id"]

    # End evening trip
    end_payload = {
        "end_odometer_km": 14325.5,
        "end_odometer_photo_url": "telemetry/14325_end.jpg",
        "end_gps_lat": 17.4405,
        "end_gps_lng": 78.3495,
    }
    end_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/end",
        json=end_payload,
        headers=auth_header(env["staff"]),
    )
    assert end_resp.status_code == 200, end_resp.text
    end_data = end_resp.json()
    assert end_data["status"] == "COMPLETED"
    assert end_data["end_odometer_km"] == 14325.5
    assert end_data["total_km"] == 75.5
    assert end_data["is_local_exceeded"] is False
    assert end_data["local_excess_km"] == 0.0


def test_cab_trip_end_less_than_start_odometer_rejected(client, env, db):
    """Ending odometer reading cannot be less than starting reading (raises 422)."""
    req_id = create_cab_request(client, env["staff"], env["project_id"])

    start_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/start",
        json={"start_odometer_km": 20000.0},
        headers=auth_header(env["staff"]),
    )
    assert start_resp.status_code == 201
    telemetry_id = start_resp.json()["id"]

    # Try to set end reading lower than start
    end_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/end",
        json={"end_odometer_km": 19950.0},
        headers=auth_header(env["staff"]),
    )
    assert end_resp.status_code == 422
    assert "cannot be less than start odometer reading" in end_resp.json()["detail"]


def test_local_cab_80km_package_excess_detection(client, env, db):
    """When a local cab trip exceeds 80 km package ceiling, excess is flagged and computed."""
    req_id = create_cab_request(client, env["staff"], env["project_id"], cab_trip=CabTrip.LOCAL)

    start_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/start",
        json={"start_odometer_km": 10000.0},
        headers=auth_header(env["staff"]),
    )
    telemetry_id = start_resp.json()["id"]

    # Trip records 115 km (excess of 35 km over 80 km package)
    end_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/end",
        json={"end_odometer_km": 10115.0},
        headers=auth_header(env["staff"]),
    )
    assert end_resp.status_code == 200
    data = end_resp.json()
    assert data["total_km"] == 115.0
    assert data["is_local_exceeded"] is True
    assert data["local_excess_km"] == 35.0


def test_admin_or_finance_verifies_cab_telemetry(client, env, db):
    """Only Admin or Finance may audit and verify cab telemetry."""
    req_id = create_cab_request(client, env["staff"], env["project_id"])

    start_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/start",
        json={"start_odometer_km": 1000.0},
        headers=auth_header(env["staff"]),
    )
    telemetry_id = start_resp.json()["id"]

    client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/end",
        json={"end_odometer_km": 1050.0},
        headers=auth_header(env["staff"]),
    )

    # Ground staff cannot verify
    fail_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/verify",
        json={"notes": "Checked meters"},
        headers=auth_header(env["staff"]),
    )
    assert fail_resp.status_code == 403

    # Finance successfully verifies
    verify_resp = client.post(
        f"/requests/{req_id}/cab-telemetry/{telemetry_id}/verify",
        json={"notes": "Odometer snaps verified against vendor invoice."},
        headers=auth_header(env["finance"]),
    )
    assert verify_resp.status_code == 200, verify_resp.text
    vdata = verify_resp.json()
    assert vdata["status"] == "VERIFIED"
    assert vdata["is_verified"] is True
    assert vdata["verified_by_id"] == env["finance"].id


# --- Hotel Stay Verification Tests ------------------------------------------

def test_hotel_stay_check_in_and_check_out_with_folio(client, env, db):
    """Ground staff checks into hotel (room, GPS) and checks out (folio bill upload, GPS)."""
    req_id = create_hotel_request(client, env["staff"], env["project_id"])

    # Check-in
    check_in_payload = {
        "room_number": "402-A",
        "check_in_lat": 12.9716,
        "check_in_lng": 77.5946,
    }
    in_resp = client.post(
        f"/requests/{req_id}/hotel-verification/check-in",
        json=check_in_payload,
        headers=auth_header(env["staff"]),
    )
    assert in_resp.status_code == 201, in_resp.text
    in_data = in_resp.json()
    assert in_data["status"] == "CHECKED_IN"
    assert in_data["room_number"] == "402-A"
    assert in_data["check_in_time"] is not None
    verification_id = in_data["id"]

    # Check-out
    check_out_payload = {
        "folio_bill_url": "telemetry/hotel_folio_402.pdf",
        "check_out_lat": 12.9719,
        "check_out_lng": 77.5949,
    }
    out_resp = client.post(
        f"/requests/{req_id}/hotel-verification/{verification_id}/check-out",
        json=check_out_payload,
        headers=auth_header(env["staff"]),
    )
    assert out_resp.status_code == 200, out_resp.text
    out_data = out_resp.json()
    assert out_data["status"] == "CHECKED_OUT"
    assert out_data["folio_bill_url"] == "telemetry/hotel_folio_402.pdf"
    assert out_data["check_out_time"] is not None
    assert out_data["reimbursement_prompted"] is True


def test_hotel_stay_finance_verification(client, env, db):
    """Finance desk verifies the hotel stay and uploaded folio bill."""
    req_id = create_hotel_request(client, env["staff"], env["project_id"])

    in_resp = client.post(
        f"/requests/{req_id}/hotel-verification/check-in",
        json={"room_number": "205"},
        headers=auth_header(env["staff"]),
    )
    verification_id = in_resp.json()["id"]

    client.post(
        f"/requests/{req_id}/hotel-verification/{verification_id}/check-out",
        json={"folio_bill_url": "telemetry/folio.pdf"},
        headers=auth_header(env["staff"]),
    )

    verify_resp = client.post(
        f"/requests/{req_id}/hotel-verification/{verification_id}/verify",
        json={"notes": "Folio tax invoice matched against corporate booking."},
        headers=auth_header(env["finance"]),
    )
    assert verify_resp.status_code == 200
    assert verify_resp.json()["status"] == "VERIFIED"
    assert verify_resp.json()["is_verified"] is True
    assert verify_resp.json()["verified_by_name"] == env["finance"].name


def test_unrelated_user_blocked_from_telemetry(client, env, db):
    """User not related to request is blocked (HTTP 403) from viewing or submitting telemetry."""
    req_id = create_cab_request(client, env["staff"], env["project_id"])

    resp = client.get(
        f"/requests/{req_id}/cab-telemetry",
        headers=auth_header(env["unrelated"]),
    )
    assert resp.status_code == 403
