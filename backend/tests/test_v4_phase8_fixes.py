"""
Verification tests for V4 user-reported fixes:
- Vendor ticket download Role NameError fix
- Unchecked email checkbox suppresses decision emails (while keeping in-app log)
- Super Admin excluded from request submission & manager recommendation emails
- Single loop email (Manager in To, Operational Admins in Cc)
- Traveller manager_reviewed progress flag without leaking advice
- Reverse geocoded place names on CabTelemetry & HotelStayVerification
- In-app BugReport submission, screenshot serving, and admin resolution
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    CabTrip,
    CabType,
    Gender,
    NotificationChannel,
    RequestType,
    Role,
    TicketStatus,
    VendorKind,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.request import Notification, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.models.vendor import Vendor
from app.services import geocoding, notifications, storage

TENANT = "designboxed"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@dataclass(frozen=True)
class Ref:
    id: int
    role: Role
    email: str
    vendor_id: int | None = None


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="V4 Ops Fix", code="CMP-FIX-1")
    vendor = Vendor(tenant_id=TENANT, name="City Cabs", kind=VendorKind.CAB)
    db.add_all([project, vendor])
    db.flush()

    super_admin = User(
        tenant_id=TENANT, email="sa@designboxed.com", full_name="Super Admin",
        role=Role.SUPER_ADMIN, gender=Gender.MALE, password_hash="x",
    )
    ops_admin = User(
        tenant_id=TENANT, email="ops@designboxed.com", full_name="Ops Admin",
        role=Role.ADMIN, gender=Gender.FEMALE, password_hash="x",
    )
    manager = User(
        tenant_id=TENANT, email="mgr@designboxed.com", full_name="Team Manager",
        role=Role.MANAGER, gender=Gender.MALE, password_hash="x",
    )
    db.add_all([super_admin, ops_admin, manager])
    db.flush()

    staff = User(
        tenant_id=TENANT, email="emp@designboxed.com", full_name="Field Staff",
        role=Role.GROUND_STAFF, gender=Gender.MALE, manager_id=manager.id, password_hash="x",
    )
    vendor_user = User(
        tenant_id=TENANT, email="vendor@citycabs.in", full_name="City Cabs User",
        role=Role.VENDOR, vendor_id=vendor.id, gender=Gender.MALE, password_hash="x",
    )
    db.add_all([staff, vendor_user])
    db.commit()
    return {
        "project": Ref(project.id, Role.ADMIN, ""),
        "vendor": Ref(vendor.id, Role.VENDOR, ""),
        "super_admin": Ref(super_admin.id, super_admin.role, super_admin.email),
        "ops_admin": Ref(ops_admin.id, ops_admin.role, ops_admin.email),
        "manager": Ref(manager.id, manager.role, manager.email),
        "staff": Ref(staff.id, staff.role, staff.email),
        "vendor_user": Ref(vendor_user.id, vendor_user.role, vendor_user.email, vendor.id),
    }


def _raise_cab(client, w):
    start = (datetime.now() + timedelta(days=3)).replace(hour=9, minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=8)
    r = client.post(
        "/requests",
        headers=auth(w["staff"]),
        json={
            "request_type": "LOCAL_CAB",
            "project_id": w["project"].id,
            "origin_state": "Telangana",
            "pickup_city": "Hyderabad",
            "origin": "Hotel Taj",
            "destination_state": "Telangana",
            "drop_city": "Hyderabad",
            "destination": "Hotel Taj",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
            "cab_type": "SEDAN",
            "cab_trip": "LOCAL",
            "cab_distance_km": 45,
            "travel_reason": "Client field visits",
            "traveller_ids": [w["staff"].id],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_single_loop_email_and_no_super_admin_email(client, db, world):
    made = _raise_cab(client, world)

    emails = db.execute(
        select(Notification).where(
            Notification.request_id == made["id"],
            Notification.channel == NotificationChannel.EMAIL,
        )
    ).scalars().all()
    # Exactly 1 email to manager with ops_admin in CC; NO email to Super Admin
    assert len(emails) == 1
    assert emails[0].kind == "TEAM_REQUEST_SUBMITTED"
    assert emails[0].to_address == world["manager"].email
    assert emails[0].cc_addresses == world["ops_admin"].email
    assert world["super_admin"].email not in (emails[0].cc_addresses or "")

    # Employee sees manager_reviewed=False before L1 review
    emp_view = client.get(f"/requests/{made['id']}", headers=auth(world["staff"])).json()
    assert emp_view["travellers"][0]["manager_reviewed"] is False
    assert emp_view["travellers"][0]["manager_recommendation"] is None

    # Manager recommends -> 1 email to ops_admin, 0 emails to Super Admin
    r_rec = client.post(
        f"/requests/{made['id']}/recommendation",
        headers=auth(world["manager"]),
        json={"recommendation": "RECOMMENDED", "comment": "Approved for visits", "cab_type": "SUV"},
    )
    assert r_rec.status_code == 200, r_rec.text

    rec_emails = db.execute(
        select(Notification).where(
            Notification.request_id == made["id"],
            Notification.kind == "MANAGER_RECOMMENDED",
            Notification.channel == NotificationChannel.EMAIL,
        )
    ).scalars().all()
    assert len(rec_emails) == 1
    assert rec_emails[0].to_address == world["ops_admin"].email

    # Employee now sees manager_reviewed=True while private comment stays hidden
    emp_view2 = client.get(f"/requests/{made['id']}", headers=auth(world["staff"])).json()
    assert emp_view2["travellers"][0]["manager_reviewed"] is True
    assert emp_view2["travellers"][0]["manager_recommendation"] is None
    assert emp_view2["travellers"][0]["manager_comment"] is None


def test_unselected_email_checkbox_suppresses_decision_email(client, db, world):
    made = _raise_cab(client, world)
    client.post(
        f"/requests/{made['id']}/recommendation",
        headers=auth(world["manager"]),
        json={"recommendation": "RECOMMENDED", "comment": "Go ahead"},
    )

    traveller_id = made["travellers"][0]["id"]
    r_dec = client.post(
        f"/requests/{made['id']}/decide",
        headers=auth(world["ops_admin"]),
        json={
            "decisions": [
                {
                    "traveller_id": traveller_id,
                    "to_status": "APPROVED",
                    "reason": "Cab approved silently",
                    "notify_employee": False,
                }
            ],
            "assigned_vendor_id": world["vendor"].id,
        },
    )
    assert r_dec.status_code == 200, r_dec.text

    decision_emails = db.execute(
        select(Notification).where(
            Notification.request_id == made["id"],
            Notification.kind == "REQUEST_APPROVED",
            Notification.channel == NotificationChannel.EMAIL,
        )
    ).scalars().all()
    assert decision_emails == []

    # In-app notice is still written
    decision_in_app = db.execute(
        select(Notification).where(
            Notification.request_id == made["id"],
            Notification.kind == "REQUEST_APPROVED",
            Notification.channel == NotificationChannel.IN_APP,
        )
    ).scalars().all()
    assert len(decision_in_app) == 1


def test_vendor_ticket_download_does_not_raise_nameerror(client, db, world, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.upload_dir", str(tmp_path))
    made = _raise_cab(client, world)
    req_row = db.get(TravelRequest, made["id"])
    req_row.assigned_vendor_id = world["vendor"].id
    db.commit()

    rel_path = storage.save_in("tickets", world["vendor_user"].id, PNG_BYTES, ".png")
    ticket = TicketDocument(
        tenant_id=TENANT,
        request_id=made["id"],
        traveller_id=made["travellers"][0]["id"],
        status=TicketStatus.EXTRACTED,
        file_path=rel_path,
        file_name="slip.png",
        file_size=len(PNG_BYTES),
        content_type="image/png",
        uploaded_by_id=world["vendor_user"].id,
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)

    resp = client.get(f"/tickets/{ticket.id}/file", headers=auth(world["vendor_user"]))
    assert resp.status_code == 200
    assert resp.content == PNG_BYTES


def test_telemetry_reverse_geocodes_place_name(client, world, monkeypatch):
    made = _raise_cab(client, world)
    client.post(
        f"/requests/{made['id']}/recommendation",
        headers=auth(world["manager"]),
        json={"recommendation": "RECOMMENDED", "comment": "Approved"},
    )
    client.post(
        f"/requests/{made['id']}/decide",
        headers=auth(world["ops_admin"]),
        json={
            "decisions": [
                {
                    "traveller_id": made["travellers"][0]["id"],
                    "to_status": "APPROVED",
                    "reason": "Ok",
                    "notify_employee": False,
                }
            ]
        },
    )

    monkeypatch.setattr(
        geocoding,
        "reverse_geocode",
        lambda lat, lng: "Madhapur, Hyderabad, Telangana" if lat else None,
    )
    r_start = client.post(
        f"/requests/{made['id']}/cab-telemetry/start",
        headers=auth(world["staff"]),
        json={
            "start_odometer_km": 1000,
            "start_gps_lat": 17.4486,
            "start_gps_lng": 78.3908,
        },
    )
    assert r_start.status_code == 201, r_start.text
    assert r_start.json()["start_location_name"] == "Madhapur, Hyderabad, Telangana"


def test_bug_report_submission_and_admin_triage(client, world, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.storage.settings.upload_dir", str(tmp_path))

    # Mandatory comment check
    r_bad = client.post(
        "/bug-reports",
        headers=auth(world["staff"]),
        data={"comment": "hi", "page_url": "/requests"},
    )
    assert r_bad.status_code == 422

    # Valid submission with screenshot
    r_ok = client.post(
        "/bug-reports",
        headers=auth(world["staff"]),
        data={"comment": "Button alignment looks off on mobile", "page_url": "/requests"},
        files={"screenshot": ("screen.png", PNG_BYTES, "image/png")},
    )
    assert r_ok.status_code == 201, r_ok.text
    bug = r_ok.json()
    assert bug["has_screenshot"] is True
    assert bug["status"] == "OPEN"
    assert bug["page_url"] == "/requests"

    # Admin can fetch screenshot & resolve
    r_img = client.get(f"/bug-reports/{bug['id']}/screenshot", headers=auth(world["ops_admin"]))
    assert r_img.status_code == 200
    assert r_img.content == PNG_BYTES

    r_patch = client.patch(
        f"/bug-reports/{bug['id']}",
        headers=auth(world["ops_admin"]),
        json={"status": "RESOLVED", "resolution_note": "Fixed layout spacing"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["status"] == "RESOLVED"
    assert r_patch.json()["resolution_note"] == "Fixed layout spacing"


def test_ai_ticket_autofill_toggle(client, world, tmp_path, monkeypatch):
    from app.services import extraction

    monkeypatch.setattr("app.services.storage.settings.upload_dir", str(tmp_path))

    called = {"count": 0}

    def fake_extract(data, content_type):
        called["count"] += 1
        return extraction.Extraction(
            ok=True,
            model_id="test-model",
            fields={"booking_reference": "PNR999"},
            confidence={"booking_reference": 0.99},
            raw="{}",
        )

    monkeypatch.setattr(extraction, "extract", fake_extract)

    # Create and approve a request so ticket upload is allowed
    req_data = _raise_cab(client, world)
    trav_id = req_data["travellers"][0]["id"]
    client.post(
        f"/requests/{req_data['id']}/recommendation",
        headers=auth(world["manager"]),
        json={"recommendation": "RECOMMENDED", "comment": "Approved"},
    )
    client.post(
        f"/requests/{req_data['id']}/decide",
        headers=auth(world["ops_admin"]),
        json={
            "decisions": [
                {
                    "traveller_id": trav_id,
                    "to_status": "APPROVED",
                    "reason": "Ready to book",
                    "notify_employee": False,
                }
            ]
        },
    )

    # Turn off AI autofill as admin
    r_off = client.patch(
        "/telemetry/settings",
        headers=auth(world["ops_admin"]),
        json={"ai_ticket_autofill_enabled": False},
    )
    assert r_off.status_code == 200
    assert r_off.json()["ai_ticket_autofill_enabled"] is False

    r_up_off = client.post(
        f"/requests/{req_data['id']}/tickets?traveller_id={trav_id}",
        headers=auth(world["ops_admin"]),
        files={"file": ("ticket1.png", PNG_BYTES, "image/png")},
    )
    assert r_up_off.status_code == 201, r_up_off.text
    assert r_up_off.json()["status"] == "UPLOADED"
    assert called["count"] == 0

    # Turn AI autofill back on
    r_on = client.patch(
        "/telemetry/settings",
        headers=auth(world["ops_admin"]),
        json={"ai_ticket_autofill_enabled": True},
    )
    assert r_on.status_code == 200
    assert r_on.json()["ai_ticket_autofill_enabled"] is True

    r_up_on = client.post(
        f"/requests/{req_data['id']}/tickets?traveller_id={trav_id}",
        headers=auth(world["ops_admin"]),
        files={"file": ("ticket2.png", PNG_BYTES, "image/png")},
    )
    assert r_up_on.status_code == 201, r_up_on.text
    assert r_up_on.json()["status"] == "EXTRACTED"
    assert r_up_on.json()["booking_reference"] == "PNR999"
    assert called["count"] == 1
