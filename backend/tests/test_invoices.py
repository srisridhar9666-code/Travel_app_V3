"""
Vendors and vendor invoices (vendor reconciliation).

* Admins and system admins keep the vendor list and prepare invoices; the
  super admin reads both and is the only one who approves or rejects an
  invoice - never one they helped prepare. Managers and ground staff see none
  of it.
* An invoice carries booked trips with a cost, paid to its vendor, travelling
  in its period. Its money is never typed: each line is the traveller's cost
  and the total is the sum, refreshed until approval and frozen at it. A
  traveller's cost is billed on one invoice at most.
* It stays editable until approved: a rejected one goes back to draft when
  edited, a submitted one stays submitted and the super admins are told.
  After approval the costs behind it are locked.
* Every step is in the activity log, downloads included, and the people who
  need to act or know are told.
"""
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core import clock, ratelimit
from app.core.enums import (
    AuditAction,
    Gender,
    InvoiceStatus,
    NotificationChannel,
    RequestType,
    Role,
    TravelMode,
    TravellerStatus,
    VendorKind,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.invoice import Invoice, InvoiceLine
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.models.vendor import Vendor
from app.services import audit as audit_service
from app.services import invoices as invoice_service
from app.services import notifications

TENANT = "designboxed"
YEAR = clock.local_today().year

#: A fixed month in the past, so trips in it are history, as billed trips are.
START = date(2026, 9, 1)
END = date(2026, 9, 30)
DAY = date(2026, 9, 12)


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    # The after-response send opens its own session on the real database.
    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


def person(db, name, email_address, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email_address, full_name=name, role=role,
                gender=Gender.MALE, password_hash="x", **extra)
    db.add(user)
    db.commit()
    return user


@dataclass(frozen=True)
class Ref:
    """Who someone is, without the ORM row: some request endpoints close the
    test's session, which detaches every row in it."""

    id: int
    role: Role
    email: str


def auth(ref):
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def vendor(db, name="Sai Travels", kind=VendorKind.TRAVEL_AGENT, active=True, **extra) -> int:
    row = Vendor(tenant_id=TENANT, name=name, kind=kind, is_active=active, **extra)
    db.add(row)
    db.commit()
    return row.id


@pytest.fixture
def org(db):
    project = Project(tenant_id=TENANT, name="Temple Trail", code="CMP-2026-0002")
    db.add(project)
    db.commit()
    people = dict(
        owner=person(db, "Sridhar Rao", "sridhar@designboxed.com", Role.SUPER_ADMIN),
        owner2=person(db, "Meera Rao", "meera@designboxed.com", Role.SUPER_ADMIN),
        sysadmin=person(db, "Kiran Das", "kiran@designboxed.com", Role.SYSTEM_ADMIN),
        admin=person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN),
        admin2=person(db, "Arjun Rao", "arjun@designboxed.com", Role.ADMIN),
        lead=person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER),
        ravi=person(db, "Ravi Kumar", "ravi@designboxed.com", employee_code="DB-101"),
        sana=person(db, "Sana Khan", "sana@designboxed.com"),
    )
    refs = {key: Ref(u.id, u.role, u.email) for key, u in people.items()}
    refs["lead"] = Ref(people["lead"].id, Role.MANAGER, "anil@designboxed.com")
    sai = vendor(db, "Sai Travels", gstin="36AABCD1234E1Z5")
    zoom = vendor(db, "Zoom Cabs", VendorKind.CAB)
    return dict(refs, project=project.id, sai=sai, zoom=zoom)


def trip(
    db,
    org,
    who="ravi",
    *,
    on=DAY,
    kind=RequestType.LONG_DISTANCE,
    cost="1500.00",
    vendor_key="sai",
    status=TravellerStatus.BOOKED,
    cancelled=False,
) -> int:
    """A trip with one traveller on it, straight into the database. Returns the
    traveller row's id - what an invoice line is made of."""
    request = TravelRequest(
        tenant_id=TENANT,
        request_type=kind,
        project_id=org["project"],
        requester_id=org[who].id,
        submitted_at=datetime(2026, 8, 20, 10, 0),
        travel_reason="Store audits",
        is_cancelled=cancelled,
    )
    if kind is RequestType.HOTEL:
        request.hotel_city, request.hotel_state = "Tirupati", "Andhra Pradesh"
        request.check_in, request.check_out = on, on + timedelta(days=2)
    else:
        request.mode = TravelMode.FLIGHT if kind is RequestType.LONG_DISTANCE else None
        request.origin, request.destination = "Hyderabad", "Tirupati"
        request.start_at = datetime(on.year, on.month, on.day, 9, 30)
    traveller = RequestTraveller(
        user_id=org[who].id,
        status=status,
        cost_amount=Decimal(cost) if cost is not None else None,
        vendor_id=org[vendor_key] if vendor_key else None,
        booking_reference="PNR123",
    )
    request.travellers.append(traveller)
    db.add(request)
    db.commit()
    return traveller.id


def body(org, travellers, **overrides):
    payload = dict(
        vendor_id=org["sai"],
        period_start=START.isoformat(),
        period_end=END.isoformat(),
        traveller_ids=list(travellers),
        vendor_invoice_ref="SAI/2026/118",
        notes="September flights",
    )
    payload.update(overrides)
    return payload


def create(client, org, travellers, who="admin", **overrides):
    r = client.post("/invoices", headers=auth(org[who]), json=body(org, travellers, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


def submit(client, org, invoice_id, who="admin"):
    return client.post(f"/invoices/{invoice_id}/submit", headers=auth(org[who]))


def approve(client, org, invoice_id, who="owner", **payload):
    return client.post(f"/invoices/{invoice_id}/approve", headers=auth(org[who]), json=payload)


def reject(client, org, invoice_id, who="owner", comment="Two fares do not match the bill"):
    return client.post(f"/invoices/{invoice_id}/reject", headers=auth(org[who]),
                       json={"comment": comment})


def submitted(client, org, travellers):
    made = create(client, org, travellers)
    r = submit(client, org, made["id"])
    assert r.status_code == 200, r.text
    return r.json()


def approved(client, org, travellers):
    made = submitted(client, org, travellers)
    r = approve(client, org, made["id"])
    assert r.status_code == 200, r.text
    return r.json()


def log(db, invoice_id, action=None):
    query = select(AuditLog).where(
        AuditLog.entity_type == "invoice", AuditLog.entity_id == invoice_id
    )
    if action is not None:
        query = query.where(AuditLog.action == action)
    return db.execute(query.order_by(AuditLog.id)).scalars().all()


def notices(db, kind, *, user=None, channel=NotificationChannel.IN_APP):
    query = select(Notification).where(Notification.kind == kind, Notification.channel == channel)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return db.execute(query.order_by(Notification.id)).scalars().all()


def request_of(db, traveller_id) -> int:
    return db.execute(
        select(RequestTraveller.request_id).where(RequestTraveller.id == traveller_id)
    ).scalar_one()


def set_cost(client, db, org, traveller_id, amount, who="admin", **extra):
    return client.post(
        f"/requests/{request_of(db, traveller_id)}/costs",
        headers=auth(org[who]),
        json={"amounts": [{"traveller_id": traveller_id, "amount": amount}], **extra},
    )


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


class TestVendors:
    @pytest.mark.parametrize("who", ["admin", "sysadmin"])
    def test_admins_and_system_admins_add_and_correct_them(self, client, db, org, who):
        r = client.post("/vendors", headers=auth(org[who]), json={
            "name": "  Balaji   Hotels ", "kind": "HOTEL", "gstin": "36 aabcd 9999 e1z5",
            "email": "Desk@BalajiHotels.in", "phone": "98765 43210",
        })
        assert r.status_code == 201, r.text
        made = r.json()
        assert made["name"] == "Balaji Hotels"
        assert made["gstin"] == "36AABCD9999E1Z5"
        assert made["email"] == "desk@balajihotels.in"
        assert made["is_active"] is True

        r = client.patch(f"/vendors/{made['id']}", headers=auth(org[who]),
                         json={"contact_name": "Lakshmi Devi", "notes": None})
        assert r.status_code == 200, r.text
        assert r.json()["contact_name"] == "Lakshmi Devi"
        actions = [row.action for row in db.execute(
            select(AuditLog).where(AuditLog.entity_type == "vendor",
                                   AuditLog.entity_id == made["id"])
        ).scalars()]
        assert actions == ["CREATE", "UPDATE"]

    def test_the_super_admin_reads_the_list_but_does_not_change_it(self, client, org):
        assert client.get("/vendors", headers=auth(org["owner"])).status_code == 200
        assert client.post("/vendors", headers=auth(org["owner"]),
                           json={"name": "New One"}).status_code == 403
        assert client.patch(f"/vendors/{org['sai']}", headers=auth(org["owner"]),
                            json={"notes": "x"}).status_code == 403
        assert client.post(f"/vendors/{org['sai']}/deactivate",
                           headers=auth(org["owner"])).status_code == 403

    @pytest.mark.parametrize("who", ["lead", "ravi"])
    def test_managers_and_staff_see_no_vendors(self, client, org, who):
        assert client.get("/vendors", headers=auth(org[who])).status_code == 403

    @pytest.mark.parametrize("gstin", ["12345", "36AABCD1234E1Z", "36AABCD1234E1Z5X", "36AABCD-234E1Z5"])
    def test_a_gstin_is_fifteen_letters_and_digits(self, client, org, gstin):
        r = client.post("/vendors", headers=auth(org["admin"]), json={"name": "Odd", "gstin": gstin})
        assert r.status_code == 422

    def test_names_are_unique_whatever_the_case(self, client, org):
        r = client.post("/vendors", headers=auth(org["admin"]), json={"name": "sai travels"})
        assert r.status_code == 409
        assert "already a vendor called Sai Travels" in r.json()["detail"]

    def test_switching_off_and_on_is_logged_and_filters_the_list(self, client, db, org):
        r = client.post(f"/vendors/{org['zoom']}/deactivate", headers=auth(org["admin"]))
        assert r.status_code == 200 and r.json()["is_active"] is False
        active = client.get("/vendors?active=true", headers=auth(org["admin"])).json()
        assert [v["name"] for v in active] == ["Sai Travels"]
        everyone = client.get("/vendors", headers=auth(org["admin"])).json()
        assert [v["name"] for v in everyone] == ["Sai Travels", "Zoom Cabs"]

        assert client.post(f"/vendors/{org['zoom']}/activate",
                           headers=auth(org["admin"])).json()["is_active"] is True
        switches = db.execute(select(AuditLog).where(
            AuditLog.entity_type == "vendor", AuditLog.entity_id == org["zoom"])).scalars().all()
        assert [s.changes["is_active"]["to"] for s in switches] == [False, True]


# ---------------------------------------------------------------------------
# Recording who was paid
# ---------------------------------------------------------------------------


class TestRecordingTheVendor:
    def test_a_cost_records_the_vendor_for_admins_only(self, client, db, org):
        traveller = trip(db, org, vendor_key=None, cost=None)
        r = set_cost(client, db, org, traveller, "2400.50", vendor_id=org["sai"])
        assert r.status_code == 200, r.text
        row = r.json()["travellers"][0]
        assert row["vendor_id"] == org["sai"] and row["vendor_name"] == "Sai Travels"
        assert db.get(RequestTraveller, traveller).vendor_id == org["sai"]

        request_id = request_of(db, traveller)
        own = client.get(f"/requests/{request_id}", headers=auth(org["ravi"])).json()
        assert own["travellers"][0]["vendor_name"] is None
        assert own["travellers"][0]["cost_amount"] is None

    def test_leaving_the_vendor_out_keeps_it(self, client, db, org):
        traveller = trip(db, org)
        assert set_cost(client, db, org, traveller, "999.00").status_code == 200
        assert db.get(RequestTraveller, traveller).vendor_id == org["sai"]

    def test_a_switched_off_vendor_cannot_be_newly_picked(self, client, db, org):
        traveller = trip(db, org)
        client.post(f"/vendors/{org['zoom']}/deactivate", headers=auth(org["admin"]))
        r = set_cost(client, db, org, traveller, "999.00", vendor_id=org["zoom"])
        assert r.status_code == 422
        assert "switched off" in r.json()["detail"]
        # ...but a cost already paid to a vendor since switched off can be corrected.
        client.post(f"/vendors/{org['sai']}/deactivate", headers=auth(org["admin"]))
        assert set_cost(client, db, org, traveller, "1000.00", vendor_id=org["sai"]).status_code == 200

    def test_another_organisations_vendor_is_refused(self, client, db, org):
        foreign = Vendor(tenant_id="elsewhere", name="Far Away Travels")
        db.add(foreign)
        db.commit()
        traveller = trip(db, org)
        assert set_cost(client, db, org, traveller, "10", vendor_id=foreign.id).status_code == 422

    def test_a_split_records_the_vendor_for_everyone_sharing(self, client, db, org):
        traveller = trip(db, org, vendor_key=None)
        r = client.post(f"/requests/{request_of(db, traveller)}/costs/split", headers=auth(org["admin"]),
                        json={"total_amount": "3000", "traveller_ids": [traveller],
                              "vendor_id": org["zoom"]})
        assert r.status_code == 200, r.text
        assert db.get(RequestTraveller, traveller).vendor_id == org["zoom"]

    def test_the_cab_booking_records_the_operator_for_everyone_riding(self, client, db, org):
        traveller = trip(db, org, kind=RequestType.LOCAL_CAB, vendor_key=None,
                         status=TravellerStatus.APPROVED, cost=None)
        request_id = request_of(db, traveller)
        r = client.put(f"/requests/{request_id}/cab-booking", headers=auth(org["admin"]), json={
            "booked_cab_type": "SEDAN", "vehicle_number": "TS 09 EA 1234",
            "driver_name": "Suresh Reddy", "driver_phone": "+91 98765 43210",
            "vendor_id": org["zoom"],
        })
        assert r.status_code == 200, r.text
        assert r.json()["travellers"][0]["vendor_name"] == "Zoom Cabs"
        entry = db.execute(select(AuditLog).where(
            AuditLog.entity_type == "travel_request", AuditLog.entity_id == request_id
        ).order_by(AuditLog.id.desc())).scalars().first()
        assert entry.changes["vendor"] == {"Ravi Kumar": {"from": None, "to": "Zoom Cabs"}}
        # The traveller is told about the car, never about who was paid.
        told = notices(db, "CAB_DETAILS")
        assert told and all("Zoom" not in n.body for n in told)

        # Naming only the vendor afterwards is logged, and tells nobody.
        before = len(notices(db, "CAB_DETAILS"))
        meru = vendor(db, "Meru Cabs", VendorKind.CAB)
        r = client.put(f"/requests/{request_id}/cab-booking", headers=auth(org["admin"]), json={
            "booked_cab_type": "SEDAN", "vehicle_number": "TS 09 EA 1234",
            "driver_name": "Suresh Reddy", "driver_phone": "+91 98765 43210", "vendor_id": meru,
        })
        assert r.status_code == 200, r.text
        assert len(notices(db, "CAB_DETAILS")) == before
        assert db.get(RequestTraveller, traveller).vendor_id == meru


# ---------------------------------------------------------------------------
# Which trips can be billed
# ---------------------------------------------------------------------------


def eligible(client, org, who="admin", **params):
    query = {"vendor_id": org["sai"], "start": START.isoformat(), "end": END.isoformat(), **params}
    r = client.get("/invoices/eligible", headers=auth(org[who]), params=query)
    assert r.status_code == 200, r.text
    return r.json()


class TestEligibleTrips:
    def test_only_booked_costed_trips_paid_to_the_vendor_in_the_period(self, client, db, org):
        good = trip(db, org, on=date(2026, 9, 3))
        hotel = trip(db, org, kind=RequestType.HOTEL, on=date(2026, 9, 30), cost="4200")
        trip(db, org, status=TravellerStatus.APPROVED)            # not booked yet
        trip(db, org, cost=None)                                  # no cost recorded
        trip(db, org, vendor_key="zoom")                          # someone else's
        trip(db, org, on=date(2026, 10, 1))                       # after the period
        trip(db, org, on=date(2026, 8, 31))                       # before it
        trip(db, org, cancelled=True)                             # cancelled trip
        trip(db, org, status=TravellerStatus.CANCELLED)           # cancelled traveller
        unassigned = trip(db, org, vendor_key=None)

        rows = eligible(client, org)
        assert [r["traveller_id"] for r in rows] == [good, hotel]
        first = rows[0]
        assert first["traveller_name"] == "Ravi Kumar"
        assert first["amount"] == "1500.00"
        assert first["project_code"] == "CMP-2026-0002"
        assert first["travel_date"] == "2026-09-03"
        assert first["trip"].startswith("Flight: Hyderabad to Tirupati")

        with_unassigned = eligible(client, org, include_unassigned="true")
        assert unassigned in [r["traveller_id"] for r in with_unassigned]

    def test_an_empty_list_comes_with_the_reasons(self, client, db, org):
        trip(db, org, status=TravellerStatus.APPROVED)            # not booked yet
        trip(db, org, cost=None)                                  # no cost recorded
        trip(db, org, cost=None)
        trip(db, org, vendor_key="zoom")                          # someone else's
        trip(db, org, vendor_key=None)                            # vendor never recorded
        trip(db, org, on=date(2026, 10, 1))                       # outside the period
        r = client.get("/invoices/eligible/why", headers=auth(org["admin"]),
                       params={"vendor_id": org["sai"], "start": START.isoformat(),
                               "end": END.isoformat()})
        assert r.status_code == 200, r.text
        assert r.json() == {
            "trips_in_period": 5, "not_booked_yet": 1, "booked_without_cost": 2,
            "other_vendor": 1, "no_vendor_recorded": 1, "already_invoiced": 0,
        }
        r = client.get("/invoices/eligible/why", headers=auth(org["ravi"]),
                       params={"vendor_id": org["sai"], "start": START.isoformat(),
                               "end": END.isoformat()})
        assert r.status_code == 403

    def test_a_trip_on_another_invoice_is_not_offered_again(self, client, db, org):
        traveller = trip(db, org)
        made = create(client, org, [traveller])
        assert eligible(client, org) == []
        # ...except to the invoice it is on, marked as already there.
        rows = eligible(client, org, invoice_id=made["id"])
        assert [(r["traveller_id"], r["on_this_invoice"]) for r in rows] == [(traveller, True)]

    def test_every_admin_tier_may_look_and_nobody_else(self, client, db, org):
        for who in ("admin", "sysadmin", "owner"):
            eligible(client, org, who=who)
        for who in ("lead", "ravi"):
            r = client.get("/invoices/eligible", headers=auth(org[who]),
                           params={"vendor_id": org["sai"], "start": "2026-09-01", "end": "2026-09-30"})
            assert r.status_code == 403

    def test_a_backwards_period_is_refused(self, client, org):
        r = client.get("/invoices/eligible", headers=auth(org["admin"]),
                       params={"vendor_id": org["sai"], "start": "2026-09-30", "end": "2026-09-01"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Preparing an invoice
# ---------------------------------------------------------------------------


class TestCreating:
    @pytest.mark.parametrize("who", ["admin", "sysadmin"])
    def test_a_draft_adds_up_the_recorded_costs(self, client, db, org, who):
        one = trip(db, org, cost="1500.00", on=date(2026, 9, 3))
        two = trip(db, org, "sana", cost="2250.50", on=date(2026, 9, 14))
        made = create(client, org, [two, one], who=who)

        assert made["number"] == f"INV-{YEAR}-0001"
        assert made["status"] == "DRAFT"
        assert made["total_amount"] == "3750.50"
        assert made["line_count"] == 2
        assert [line["traveller_id"] for line in made["lines"]] == [one, two]   # by date
        assert made["lines"][0]["description"] == (
            "Ravi Kumar · Flight: Hyderabad to Tirupati, 03 Sep 2026, 9:30 AM · CMP-2026-0002"
        )
        assert made["vendor_gstin"] == "36AABCD1234E1Z5"
        assert made["vendor_invoice_ref"] == "SAI/2026/118"
        assert made["can_edit"] and made["can_submit"] and made["can_delete"]
        assert not made["can_decide"]

        created = log(db, made["id"], "CREATE")
        assert len(created) == 1
        assert created[0].changes["number"] == {"from": None, "to": made["number"]}
        assert len(created[0].changes["lines_added"]) == 2

    def test_the_total_cannot_be_sent(self, client, db, org):
        traveller = trip(db, org)
        r = client.post("/invoices", headers=auth(org["admin"]),
                        json=body(org, [traveller], total_amount="1.00"))
        assert r.status_code == 422
        made = create(client, org, [traveller])
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"total_amount": "1.00"})
        assert r.status_code == 422
        assert db.get(Invoice, made["id"]).total_amount == Decimal("1500.00")

    @pytest.mark.parametrize("who", ["owner", "lead", "ravi"])
    def test_only_admins_and_system_admins_prepare_invoices(self, client, db, org, who):
        traveller = trip(db, org)
        r = client.post("/invoices", headers=auth(org[who]), json=body(org, [traveller]))
        assert r.status_code == 403

    def test_numbers_run_on_and_a_deleted_one_is_never_reused(self, client, db, org):
        first = create(client, org, [])
        second = create(client, org, [])
        assert second["number"] == f"INV-{YEAR}-0002"
        assert client.delete(f"/invoices/{second['id']}", headers=auth(org["admin"])).status_code == 204
        third = create(client, org, [])
        assert third["number"] == f"INV-{YEAR}-0003"
        assert first["number"] == f"INV-{YEAR}-0001"

    def test_a_trip_that_cannot_be_billed_is_refused_by_name(self, client, db, org):
        theirs = trip(db, org, vendor_key="zoom")
        r = client.post("/invoices", headers=auth(org["admin"]), json=body(org, [theirs]))
        assert r.status_code == 409
        assert "Ravi Kumar's trip on 12 Sep 2026 was paid to Zoom Cabs, not Sai Travels." in r.json()["detail"]

        late = trip(db, org, on=date(2026, 10, 2))
        r = client.post("/invoices", headers=auth(org["admin"]), json=body(org, [late]))
        assert r.status_code == 409 and "outside the invoice period" in r.json()["detail"]

        pending = trip(db, org, status=TravellerStatus.APPROVED)
        r = client.post("/invoices", headers=auth(org["admin"]), json=body(org, [pending]))
        assert r.status_code == 409 and "not booked" in r.json()["detail"]

    def test_a_cost_is_billed_once(self, client, db, org):
        traveller = trip(db, org)
        first = create(client, org, [traveller])
        r = client.post("/invoices", headers=auth(org["admin2"]), json=body(org, [traveller]))
        assert r.status_code == 409
        assert f"already on {first['number']}" in r.json()["detail"]
        lines = db.execute(select(InvoiceLine).where(
            InvoiceLine.request_traveller_id == traveller)).scalars().all()
        assert len(lines) == 1

    def test_the_database_holds_billed_once_too(self, db, org):
        traveller = trip(db, org)
        invoices = []
        for number in ("INV-T-1", "INV-T-2"):
            row = Invoice(tenant_id=TENANT, number=number, vendor_id=org["sai"],
                          period_start=START, period_end=END)
            db.add(row)
            db.flush()
            invoices.append(row)
        db.add(InvoiceLine(invoice_id=invoices[0].id, request_traveller_id=traveller,
                           request_id=request_of(db, traveller), amount=Decimal("1"), description="x"))
        db.flush()
        db.add(InvoiceLine(invoice_id=invoices[1].id, request_traveller_id=traveller,
                           request_id=request_of(db, traveller), amount=Decimal("1"), description="x"))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

    def test_an_unassigned_trip_gets_the_invoices_vendor(self, client, db, org):
        traveller = trip(db, org, vendor_key=None)
        made = create(client, org, [traveller])
        assert made["total_amount"] == "1500.00"
        assert db.get(RequestTraveller, traveller).vendor_id == org["sai"]
        assert log(db, made["id"], "CREATE")[0].changes["vendor_recorded_for"] == [
            "Ravi Kumar's trip on 12 Sep 2026"
        ]

    def test_an_unknown_trip_or_vendor_is_refused(self, client, db, org):
        r = client.post("/invoices", headers=auth(org["admin"]), json=body(org, [987654]))
        assert r.status_code == 404
        r = client.post("/invoices", headers=auth(org["admin"]), json=body(org, [], vendor_id=987654))
        assert r.status_code == 422
        r = client.post("/invoices", headers=auth(org["admin"]),
                        json=body(org, [], period_start="2026-09-30", period_end="2026-09-01"))
        assert r.status_code == 422


class TestEditing:
    def test_the_line_set_is_replaced_and_the_total_follows(self, client, db, org):
        one = trip(db, org, cost="1000")
        two = trip(db, org, "sana", cost="2000")
        three = trip(db, org, cost="500", on=date(2026, 9, 20))
        made = create(client, org, [one, two])
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin2"]),
                         json={"traveller_ids": [two, three], "notes": "Fixed the list"})
        assert r.status_code == 200, r.text
        edited = r.json()
        assert edited["total_amount"] == "2500.00"
        assert sorted(line["traveller_id"] for line in edited["lines"]) == sorted([two, three])
        assert edited["updated_by_name"] == "Arjun Rao"

        change = log(db, made["id"], "UPDATE")[-1].changes
        assert len(change["lines_added"]) == 1 and len(change["lines_removed"]) == 1
        assert change["notes"] == {"from": "September flights", "to": "Fixed the list"}
        assert change["total"] == {"from": "3000.00", "to": "2500.00"}
        # The trip taken off is free to bill on another invoice.
        assert one in [row["traveller_id"] for row in eligible(client, org)]

    def test_saving_nothing_new_writes_nothing(self, client, db, org):
        made = create(client, org, [trip(db, org)])
        before = len(log(db, made["id"]))
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"notes": "September flights"})
        assert r.status_code == 200
        assert len(log(db, made["id"])) == before

    def test_a_new_period_that_leaves_a_line_out_is_refused(self, client, db, org):
        made = create(client, org, [trip(db, org, on=date(2026, 9, 25))])
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"period_end": "2026-09-15"})
        assert r.status_code == 409
        assert "outside the invoice period" in r.json()["detail"]

    def test_the_super_admin_cannot_edit_or_delete(self, client, db, org):
        made = create(client, org, [trip(db, org)])
        assert client.patch(f"/invoices/{made['id']}", headers=auth(org["owner"]),
                            json={"notes": "x"}).status_code == 403
        assert client.delete(f"/invoices/{made['id']}", headers=auth(org["owner"])).status_code == 403
        assert client.post(f"/invoices/{made['id']}/submit",
                           headers=auth(org["owner"])).status_code == 403


# ---------------------------------------------------------------------------
# Submitting and deciding
# ---------------------------------------------------------------------------


class TestSubmitting:
    def test_it_goes_to_every_active_super_admin(self, client, db, org):
        made = create(client, org, [trip(db, org)])
        r = submit(client, org, made["id"])
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "SUBMITTED"
        assert r.json()["submitted_by_name"] == "Priya Shah"

        told = notices(db, "INVOICE_SUBMITTED")
        assert sorted(n.user_id for n in told) == sorted([org["owner"].id, org["owner2"].id])
        assert "Priya Shah sent INV-" in told[0].body and "INR 1,500.00" in told[0].body
        emails = notices(db, "INVOICE_SUBMITTED", channel=NotificationChannel.EMAIL)
        assert len(emails) == 2
        assert f"/invoices/{made['id']}" in emails[0].body
        assert len(log(db, made["id"], "SUBMIT")) == 1

    def test_an_empty_invoice_is_not_submitted(self, client, org):
        made = create(client, org, [])
        r = submit(client, org, made["id"])
        assert r.status_code == 422
        assert "at least one trip" in r.json()["detail"]

    def test_a_line_that_went_wrong_holds_it_back(self, client, db, org):
        traveller = trip(db, org)
        made = create(client, org, [traveller])
        db.get(RequestTraveller, traveller).status = TravellerStatus.CANCELLED
        db.commit()
        detail = client.get(f"/invoices/{made['id']}", headers=auth(org["admin"])).json()
        assert "was cancelled" in detail["lines"][0]["problem"]
        assert detail["can_submit"] is False
        r = submit(client, org, made["id"])
        assert r.status_code == 409 and "was cancelled" in r.json()["detail"]

    def test_submitting_twice_is_refused(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        assert submit(client, org, made["id"]).status_code == 409


class TestDeciding:
    @pytest.mark.parametrize("who", ["admin", "sysadmin", "lead", "ravi"])
    def test_only_a_super_admin_decides(self, client, db, org, who):
        made = submitted(client, org, [trip(db, org)])
        assert approve(client, org, made["id"], who=who).status_code == 403
        assert reject(client, org, made["id"], who=who).status_code == 403

    def test_approval_freezes_it_and_tells_who_prepared_it(self, client, db, org):
        made = create(client, org, [trip(db, org)], who="admin2")
        submit(client, org, made["id"], who="admin")
        r = approve(client, org, made["id"], comment="Matches the bill", expected_total="1500.00")
        assert r.status_code == 200, r.text
        decided = r.json()
        assert decided["status"] == "APPROVED"
        assert decided["decided_by_name"] == "Sridhar Rao"
        assert decided["decision_comment"] == "Matches the bill"
        assert not any(decided[k] for k in ("can_edit", "can_submit", "can_delete", "can_decide"))

        told = notices(db, "INVOICE_APPROVED")
        assert sorted(n.user_id for n in told) == sorted([org["admin"].id, org["admin2"].id])
        assert "Matches the bill" in told[0].body
        entry = log(db, made["id"], "APPROVE")[0]
        assert entry.reason == "Matches the bill" and entry.actor_user_id == org["owner"].id

        for attempt in (
            client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]), json={"notes": "x"}),
            client.delete(f"/invoices/{made['id']}", headers=auth(org["admin"])),
            submit(client, org, made["id"]),
            approve(client, org, made["id"], who="owner2"),
            reject(client, org, made["id"], who="owner2"),
        ):
            assert attempt.status_code == 409, attempt.text

    def test_a_rejection_needs_a_comment_and_goes_back_to_the_admins(self, client, db, org):
        traveller = trip(db, org)
        made = submitted(client, org, [traveller])
        r = client.post(f"/invoices/{made['id']}/reject", headers=auth(org["owner"]),
                        json={"comment": "  "})
        assert r.status_code == 422
        r = reject(client, org, made["id"])
        assert r.status_code == 200 and r.json()["status"] == "REJECTED"
        told = notices(db, "INVOICE_REJECTED", user=db.get(User, org["admin"].id))
        assert len(told) == 1 and "Two fares do not match the bill" in told[0].body
        assert log(db, made["id"], "REJECT")[0].reason == "Two fares do not match the bill"

        # Editing a rejected invoice is fixing it: back to draft, then again.
        extra = trip(db, org, "sana", cost="300")
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"traveller_ids": [traveller, extra]})
        assert r.status_code == 200 and r.json()["status"] == "DRAFT"
        assert log(db, made["id"], "UPDATE")[-1].changes["status"] == {
            "from": "REJECTED", "to": "DRAFT"}
        assert submit(client, org, made["id"]).json()["status"] == "SUBMITTED"
        assert approve(client, org, made["id"]).json()["total_amount"] == "1800.00"

    def test_a_rejected_one_may_be_deleted_but_not_a_submitted_one(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = client.delete(f"/invoices/{made['id']}", headers=auth(org["admin"]))
        assert r.status_code == 409 and "reject it first" in r.json()["detail"]
        reject(client, org, made["id"])
        assert client.delete(f"/invoices/{made['id']}", headers=auth(org["admin"])).status_code == 204
        assert db.get(Invoice, made["id"]) is None
        assert len(log(db, made["id"], "DELETE")) == 1

    def test_editing_a_submitted_one_keeps_it_submitted_and_says_so(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"vendor_invoice_ref": "SAI/2026/118-A"})
        assert r.status_code == 200 and r.json()["status"] == "SUBMITTED"
        told = notices(db, "INVOICE_CHANGED")
        assert sorted(n.user_id for n in told) == sorted([org["owner"].id, org["owner2"].id])
        r = client.patch(f"/invoices/{made['id']}", headers=auth(org["admin"]),
                         json={"traveller_ids": []})
        assert r.status_code == 422

    def test_nobody_approves_an_invoice_they_helped_prepare(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        promoted = db.get(User, org["admin"].id)
        promoted.role = Role.SUPER_ADMIN
        db.commit()
        as_owner = Ref(org["admin"].id, Role.SUPER_ADMIN, org["admin"].email)
        detail = client.get(f"/invoices/{made['id']}", headers=auth(as_owner)).json()
        assert detail["can_decide"] is False
        r = client.post(f"/invoices/{made['id']}/approve", headers=auth(as_owner), json={})
        assert r.status_code == 403
        assert approve(client, org, made["id"]).status_code == 200

    def test_log_rows_of_an_earlier_invoice_with_the_same_id_do_not_count(self, client, db, org):
        """A restored backup or a migration run down and up again starts ids
        over; the log still holds rows about the invoice that had the id."""
        made = submitted(client, org, [trip(db, org)])
        owner = db.get(User, org["owner"].id)
        audit_service.record(
            db, action=AuditAction.CREATE, entity_type="invoice", entity_id=made["id"],
            summary="Sridhar Rao created INV-2025-0009 for Old Vendor", tenant_id=TENANT,
            actor=owner,
        )
        db.get(Invoice, made["id"]).created_at = naive_utcnow() + timedelta(seconds=1)
        db.commit()
        detail = client.get(f"/invoices/{made['id']}", headers=auth(org["owner"])).json()
        assert all("INV-2025-0009" not in event["summary"] for event in detail["history"])
        assert detail["can_decide"] is True
        assert approve(client, org, made["id"]).status_code == 200

    def test_an_approval_of_a_total_that_moved_is_refused(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = approve(client, org, made["id"], expected_total="1400.00")
        assert r.status_code == 409
        assert "INR 1,500.00" in r.json()["detail"]
        assert db.get(Invoice, made["id"]).status is InvoiceStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Costs on an invoice
# ---------------------------------------------------------------------------


class TestCostsOnAnInvoice:
    def test_until_approval_a_new_cost_flows_onto_the_invoice(self, client, db, org):
        traveller = trip(db, org, cost="1500")
        made = submitted(client, org, [traveller])
        r = set_cost(client, db, org, traveller, "1650.25")
        assert r.status_code == 200, r.text
        assert r.json()["travellers"][0]["invoice_number"] == made["number"]
        assert r.json()["travellers"][0]["invoice_status"] == "SUBMITTED"

        invoice = client.get(f"/invoices/{made['id']}", headers=auth(org["owner"])).json()
        assert invoice["total_amount"] == "1650.25"
        assert invoice["lines"][0]["amount"] == "1650.25"
        assert notices(db, "INVOICE_CHANGED")
        followed = log(db, made["id"], "UPDATE")[-1]
        assert followed.changes["total"] == {"from": "1500.00", "to": "1650.25"}

    def test_after_approval_the_cost_and_vendor_are_locked(self, client, db, org):
        traveller = trip(db, org, cost="1500")
        made = approved(client, org, [traveller])

        r = set_cost(client, db, org, traveller, "1700")
        assert r.status_code == 409
        assert made["number"] in r.json()["detail"] and "approved" in r.json()["detail"]
        r = set_cost(client, db, org, traveller, "1500", vendor_id=org["zoom"])
        assert r.status_code == 409
        r = client.post(f"/requests/{request_of(db, traveller)}/costs/split", headers=auth(org["admin"]),
                        json={"total_amount": "99", "traveller_ids": [traveller]})
        assert r.status_code == 409
        # Saving what it already is changes nothing, so it is allowed.
        assert set_cost(client, db, org, traveller, "1500.00").status_code == 200

        frozen = db.get(Invoice, made["id"])
        assert frozen.total_amount == Decimal("1500.00")
        assert db.get(RequestTraveller, traveller).cost_amount == Decimal("1500.00")


# ---------------------------------------------------------------------------
# Reading and downloading
# ---------------------------------------------------------------------------


class TestReadingAndDownloading:
    def test_the_list_counts_each_status(self, client, db, org):
        create(client, org, [trip(db, org)])
        submitted(client, org, [trip(db, org, "sana")])
        r = client.get("/invoices", headers=auth(org["owner"]))
        assert r.status_code == 200
        listed = r.json()
        assert listed["counts"] == {"DRAFT": 1, "SUBMITTED": 1, "APPROVED": 0, "REJECTED": 0}
        assert listed["total"] == 2
        assert [i["line_count"] for i in listed["items"]] == [1, 1]
        only = client.get("/invoices?status=SUBMITTED", headers=auth(org["admin"])).json()
        assert [i["status"] for i in only["items"]] == ["SUBMITTED"]
        assert only["counts"]["DRAFT"] == 1
        for who in ("lead", "ravi"):
            assert client.get("/invoices", headers=auth(org[who])).status_code == 403

    def test_the_history_tells_the_story(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        reject(client, org, made["id"])
        detail = client.get(f"/invoices/{made['id']}", headers=auth(org["admin"])).json()
        assert [e["action"] for e in detail["history"]] == ["CREATE", "SUBMIT", "REJECT"]
        assert detail["history"][-1]["comment"] == "Two fares do not match the bill"
        assert detail["history"][-1]["actor_name"] == "Sridhar Rao"

    @pytest.mark.parametrize("who", ["admin", "owner"])
    def test_the_csv_is_the_invoice_and_its_download_is_logged(self, client, db, org, who):
        one = trip(db, org, cost="1500", on=date(2026, 9, 3))
        two = trip(db, org, "sana", kind=RequestType.HOTEL, cost="4200.75")
        made = approved(client, org, [one, two])
        r = client.get(f"/invoices/{made['id']}/export.csv", headers=auth(org[who]))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert f'filename="{made["number"]}-Sai-Travels.csv"' in r.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
        head = {row[0]: row[1] for row in rows[:10] if len(row) == 2}
        assert head["Invoice"] == made["number"]
        assert head["Vendor"] == "Sai Travels"
        assert head["GSTIN"] == "36AABCD1234E1Z5"
        assert head["Status"].startswith("Approved by Sridhar Rao")
        assert head["Total"] == "5700.75"
        table = rows[rows.index(
            ["Line", "Travel date", "Description", "Booking reference", "Request", "Amount (INR)"]
        ) + 1:]
        assert [row[1] for row in table[:2]] == ["2026-09-03", "2026-09-12"]
        assert [row[5] for row in table] == ["1500.00", "4200.75", "5700.75"]
        assert table[1][2].startswith("Sana Khan · Hotel in Tirupati")

        exported = log(db, made["id"], "EXPORT")
        assert len(exported) == 1 and exported[0].actor_user_id == org[who].id

    def test_a_note_cannot_smuggle_a_formula_into_the_spreadsheet(self, client, db, org):
        made = create(client, org, [trip(db, org)], notes="=HYPERLINK(\"http://x\")")
        text = client.get(f"/invoices/{made['id']}/export.csv",
                          headers=auth(org["admin"])).content.decode("utf-8-sig")
        assert "'=HYPERLINK" in text

    def test_opening_the_print_view_is_logged(self, client, db, org):
        made = create(client, org, [trip(db, org)])
        r = client.post(f"/invoices/{made['id']}/printed", headers=auth(org["owner"]))
        assert r.status_code == 204
        assert log(db, made["id"], "EXPORT")[0].changes["format"] == "pdf"

    def test_another_organisations_invoice_does_not_exist(self, client, db, org):
        made = create(client, org, [trip(db, org)])
        db.get(Invoice, made["id"]).tenant_id = "elsewhere"
        db.commit()
        assert client.get(f"/invoices/{made['id']}", headers=auth(org["admin"])).status_code == 404


def test_money_is_written_the_indian_way():
    assert invoice_service.inr(Decimal("1234567.5")) == "INR 12,34,567.50"
    assert invoice_service.inr(Decimal("999")) == "INR 999.00"
    assert invoice_service.inr(Decimal("100000")) == "INR 1,00,000.00"


# ---------------------------------------------------------------------------
# Payment: approved is not paid
# ---------------------------------------------------------------------------


def pay(client, org, invoice_id, who="owner", **payload):
    return client.post(f"/invoices/{invoice_id}/payment", headers=auth(org[who]),
                       json={"paid": True, **payload})


class TestPayment:
    def test_approved_is_still_to_be_paid(self, client, db, org):
        made = approved(client, org, [trip(db, org)])
        assert made["status"] == "APPROVED" and made["paid_on"] is None
        assert made["can_record_payment"] is True
        assert client.get(f"/invoices/{made['id']}", headers=auth(org["admin"])).json()[
            "can_record_payment"] is False

    def test_approving_and_paying_in_one_step(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = approve(client, org, made["id"], paid=True, paid_on=str(clock.local_today()),
                    payment_reference="  UTR 4411  ")
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["status"] == "APPROVED"
        assert got["paid_on"] == str(clock.local_today())
        assert got["payment_reference"] == "UTR 4411"
        assert got["paid_by_name"] == "Sridhar Rao"
        assert [e.action for e in log(db, made["id"])][-2:] == ["APPROVE", "UPDATE"]
        told = notices(db, "INVOICE_APPROVED", user=org["admin"])[-1]
        assert "Paid on" in told.body

    def test_a_future_payment_date_refuses_the_approval_too(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = approve(client, org, made["id"], paid=True,
                    paid_on=str(clock.local_today() + timedelta(days=1)))
        assert r.status_code == 422
        again = client.get(f"/invoices/{made['id']}", headers=auth(org["owner"])).json()
        assert again["status"] == "SUBMITTED"

    def test_marking_it_paid_later(self, client, db, org):
        made = approved(client, org, [trip(db, org)])
        r = pay(client, org, made["id"], payment_reference="UTR 9001")
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["paid_on"] == str(clock.local_today())
        entry = log(db, made["id"], "UPDATE")[-1]
        assert entry.summary.endswith(f"marked {made['number']} paid on "
                                      f"{clock.local_today():%d %b %Y}, ref UTR 9001")
        told = notices(db, "INVOICE_PAID", user=org["admin"])
        assert len(told) == 1 and told[0].title == f"Invoice {made['number']} paid"
        assert notices(db, "INVOICE_PAID", channel=NotificationChannel.EMAIL) == []

    def test_taking_a_payment_back_needs_a_reason(self, client, db, org):
        made = approved(client, org, [trip(db, org)])
        pay(client, org, made["id"])
        r = client.post(f"/invoices/{made['id']}/payment", headers=auth(org["owner"]),
                        json={"paid": False})
        assert r.status_code == 422
        r = client.post(f"/invoices/{made['id']}/payment", headers=auth(org["owner"]),
                        json={"paid": False, "comment": "Bank returned the transfer"})
        assert r.status_code == 200, r.text
        assert r.json()["paid_on"] is None and r.json()["payment_reference"] is None
        assert log(db, made["id"], "UPDATE")[-1].reason == "Bank returned the transfer"

    def test_only_an_approved_invoice_is_paid(self, client, db, org):
        made = submitted(client, org, [trip(db, org)])
        r = pay(client, org, made["id"])
        assert r.status_code == 409 and "not approved" in r.json()["detail"]

    @pytest.mark.parametrize("who", ["admin", "sysadmin", "lead"])
    def test_only_a_super_admin_records_it(self, client, db, org, who):
        made = approved(client, org, [trip(db, org)])
        assert pay(client, org, made["id"], who=who).status_code == 403

    def test_the_list_splits_paid_from_unpaid(self, client, db, org):
        one = approved(client, org, [trip(db, org)])
        approved(client, org, [trip(db, org, "sana")])
        pay(client, org, one["id"])
        listed = client.get("/invoices", headers=auth(org["admin"])).json()
        assert listed["payment_counts"] == {"paid": 1, "unpaid": 1}
        paid = client.get("/invoices?payment=paid", headers=auth(org["admin"])).json()
        assert [i["id"] for i in paid["items"]] == [one["id"]]
        unpaid = client.get("/invoices?payment=unpaid", headers=auth(org["admin"])).json()
        assert one["id"] not in [i["id"] for i in unpaid["items"]]

    def test_the_csv_says_whether_it_is_paid(self, client, db, org):
        made = approved(client, org, [trip(db, org)])

        def head():
            text = client.get(f"/invoices/{made['id']}/export.csv",
                              headers=auth(org["admin"])).content.decode("utf-8-sig")
            return {row[0]: row[1] for row in csv.reader(io.StringIO(text)) if len(row) == 2}
        assert head()["Payment"] == "Not paid yet"
        pay(client, org, made["id"], payment_reference="UTR 77")
        assert head()["Payment"] == f"Paid on {clock.local_today():%d %b %Y}, ref UTR 77"
