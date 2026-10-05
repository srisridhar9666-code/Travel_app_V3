"""
Booking in one step (POST /requests/{id}/book): one or more approved travellers,
the car for a cab, several files, the cost and the vendor - and one email, the
travellers on To and their managers on Cc, with every file attached.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    Gender,
    NotificationChannel,
    NotificationStatus,
    Role,
    TicketStatus,
    TravellerStatus,
    VendorKind,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.models.vendor import Vendor
from app.services import notifications, storage

TENANT = "designboxed"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    def deliver_here(ids):
        # The after-response send, against the test's own session, so what
        # reached the wire can be read from the outbox.
        for notification_id in ids:
            row = db.get(Notification, notification_id)
            if row is not None and row.status is NotificationStatus.QUEUED:
                notifications.deliver(row)
        db.commit()

    monkeypatch.setattr(storage, "read", lambda path: b"%PDF-1.4 " + path.encode())
    monkeypatch.setattr(notifications, "deliver_queued", deliver_here)
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


def person(db, name, email_address, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email_address, full_name=name, role=role,
                gender=Gender.MALE, password_hash="x", **extra)
    db.add(user)
    db.commit()
    return user


def auth(ref):
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(db):
    project = Project(tenant_id=TENANT, name="Temple Trail", code="CMP-2026-0002")
    vendor = Vendor(tenant_id=TENANT, name="Sri Cabs", kind=VendorKind.CAB)
    db.add_all([project, vendor])
    db.commit()
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    anil = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER)
    divya = person(db, "Divya Nair", "divya@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=anil.id)
    sana = person(db, "Sana Khan", "sana@designboxed.com", manager_id=anil.id)
    meena = person(db, "Meena Iyer", "meena@designboxed.com", manager_id=divya.id)
    refs = {k: Ref(u.id, u.role) for k, u in dict(
        admin=admin, anil=anil, divya=divya, ravi=ravi, sana=sana, meena=meena).items()}
    return dict(refs, project=project.id, vendor=vendor.id)


DAY = clock.local_today() + timedelta(days=10)
START = datetime(DAY.year, DAY.month, DAY.day, 9, 0)
CAR = dict(booked_cab_type="SEDAN", vehicle_number="ts 09 ea 1234",
           driver_name="Suresh Reddy", driver_phone="9876543210")


def trip(db, org, kind="flight", travellers=("ravi",), status=TravellerStatus.APPROVED):
    row = TravelRequest(tenant_id=TENANT, project_id=org["project"],
                        requester_id=org[travellers[0]].id, travel_reason="Store audit",
                        submitted_at=clock.now_local().replace(tzinfo=None))
    if kind == "flight":
        row.request_type, row.mode = "LONG_DISTANCE", "FLIGHT"
        row.origin, row.destination, row.start_at = "Hyderabad", "Pune", START
    elif kind == "cab":
        row.request_type, row.mode = "LOCAL_CAB", "CAB"
        row.origin, row.destination = "Banjara Hills", "Charminar"
        row.pickup_city = row.drop_city = "Hyderabad"
        row.start_at, row.end_at = START, START + timedelta(hours=9)
    else:
        row.request_type, row.hotel_city = "HOTEL", "Pune"
        row.check_in, row.check_out = DAY, DAY + timedelta(days=2)
    row.travellers = [RequestTraveller(user_id=org[t].id, status=status) for t in travellers]
    db.add(row)
    db.commit()
    return row.id, {t: row.travellers[i].id for i, t in enumerate(travellers)}


def upload(db, request_id, traveller_id, name, status=TicketStatus.EXTRACTED):
    ticket = TicketDocument(tenant_id=TENANT, request_id=request_id, traveller_id=traveller_id,
                            status=status, file_path=f"tickets/{request_id}/{name}",
                            file_name=name, content_type="application/pdf")
    db.add(ticket)
    db.commit()
    return ticket.id


def book(client, org, request_id, traveller_ids, who="admin", **body):
    payload = {"traveller_ids": traveller_ids, "note": "Ticket booked", **body}
    return client.post(f"/requests/{request_id}/book", headers=auth(org[who]), json=payload)


def mails(db, kind="REQUEST_BOOKED"):
    return db.execute(select(Notification).where(
        Notification.kind == kind, Notification.channel == NotificationChannel.EMAIL,
    ).order_by(Notification.id)).scalars().all()


# ---------------------------------------------------------------------------
# One traveller
# ---------------------------------------------------------------------------


class TestBookingOneTraveller:
    def test_everything_in_one_step(self, client, db, org, outbox):
        rid, rows = trip(db, org)
        outward = upload(db, rid, rows["ravi"], "outward.pdf")
        back = upload(db, rid, rows["ravi"], "return.pdf", status=TicketStatus.FAILED)
        r = book(client, org, rid, [rows["ravi"]], booking_reference="QK8T2M",
                 booking_details={"carrier": "IndiGo", "service_number": "6E 4412"},
                 ticket_ids=[outward, back], cost_amount="4500.50", vendor_id=org["vendor"])
        assert r.status_code == 200, r.text
        got = r.json()["travellers"][0]
        assert got["status"] == "BOOKED" and got["booking_reference"] == "QK8T2M"
        assert got["cost_amount"] == "4500.50" and got["vendor_name"] == "Sri Cabs"
        assert [f["file_name"] for f in got["ticket_files"]] == ["outward.pdf", "return.pdf"]
        assert all(f["confirmed"] for f in got["ticket_files"])
        for ticket_id in (outward, back):
            ticket = db.get(TicketDocument, ticket_id)
            db.refresh(ticket)
            assert ticket.status is TicketStatus.CONFIRMED
            assert ticket.confirmed_reference == "QK8T2M"

    def test_one_email_with_the_manager_on_cc_and_every_file(self, client, db, org, outbox):
        rid, rows = trip(db, org)
        files = [upload(db, rid, rows["ravi"], n) for n in ("outward.pdf", "return.pdf")]
        book(client, org, rid, [rows["ravi"]], booking_reference="QK8T2M", ticket_ids=files)

        assert len(outbox.messages) == 1
        sent = outbox.messages[0]
        assert sent["to"] == "ravi@designboxed.com"
        assert sent["cc"] == ["anil@designboxed.com"]
        assert sorted(sent["attachments"]) == ["outward.pdf", "return.pdf"]
        assert "2 files are attached to this email" in sent["body"]
        assert "Anil Mehta is copied on this email." in sent["body"]
        # The manager hears in the app - and by no email of their own.
        assert [m.to_address for m in mails(db)] == ["ravi@designboxed.com"]
        copies = db.execute(select(Notification).where(
            Notification.user_id == org["anil"].id)).scalars().all()
        assert [(c.kind, c.channel) for c in copies] == [
            ("DECISION_COPY", NotificationChannel.IN_APP)
        ]

    def test_no_email_when_asked_but_the_notice_is_kept(self, client, db, org, outbox):
        rid, rows = trip(db, org)
        r = book(client, org, rid, [rows["ravi"]], booking_reference="QK8T2M", notify=False)
        assert r.status_code == 200, r.text
        assert outbox.messages == [] and mails(db) == []
        in_app = db.execute(select(Notification).where(
            Notification.kind == "REQUEST_BOOKED",
            Notification.user_id == org["ravi"].id)).scalars().all()
        assert len(in_app) == 1


# ---------------------------------------------------------------------------
# Several travellers together
# ---------------------------------------------------------------------------


class TestBookingTogether:
    def test_a_shared_cab_one_email_one_car_cost_split(self, client, db, org, outbox):
        rid, rows = trip(db, org, kind="cab", travellers=("ravi", "sana"))
        r = book(client, org, rid, [rows["ravi"], rows["sana"]], note="Cab booked",
                 cab=CAR, cost_amount="1000.01", vendor_id=org["vendor"])
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["cab_vehicle_number"] == "TS 09 EA 1234"
        # The plate stands in when the vendor gave no booking number.
        assert {t["booking_reference"] for t in got["travellers"]} == {"TS 09 EA 1234"}
        assert [t["cost_amount"] for t in got["travellers"]] == ["500.01", "500.00"]
        assert sum(Decimal(t["cost_amount"]) for t in got["travellers"]) == Decimal("1000.01")

        # One message: both travellers on To, their manager once on Cc. No
        # separate "your cab is arranged" mail on top of it.
        assert len(outbox.messages) == 1
        sent = outbox.messages[0]
        assert sent["to"] == "ravi@designboxed.com, sana@designboxed.com"
        assert sent["cc"] == ["anil@designboxed.com"]
        assert sent["body"].startswith("Hello Ravi and Sana,")
        assert "Booked together: Ravi Kumar and Sana Khan." in sent["body"]
        assert "Vehicle      TS 09 EA 1234" in sent["body"]
        assert mails(db, "CAB_DETAILS") == []

    def test_each_manager_is_copied(self, client, db, org, outbox):
        rid, rows = trip(db, org, kind="hotel", travellers=("ravi", "meena"))
        book(client, org, rid, [rows["ravi"], rows["meena"]], booking_reference="LT-881",
             booking_details={"hotel_name": "Lemon Tree"})
        sent = outbox.messages[0]
        assert sorted(sent["cc"]) == ["anil@designboxed.com", "divya@designboxed.com"]
        assert "Anil Mehta and Divya Nair are copied on this email." in sent["body"]
        assert "Check in" in sent["body"] and "Hotel" in sent["body"]

    def test_everyone_booked_can_download_the_files(self, client, db, org):
        rid, rows = trip(db, org, travellers=("ravi", "sana"))
        file_id = upload(db, rid, rows["ravi"], "group-pnr.pdf")
        book(client, org, rid, [rows["ravi"], rows["sana"]], booking_reference="QK8T2M",
             ticket_ids=[file_id])

        got = client.get(f"/requests/{rid}", headers=auth(org["sana"])).json()
        sana = next(t for t in got["travellers"] if t["full_name"] == "Sana Khan")
        assert [f["file_name"] for f in sana["ticket_files"]] == ["group-pnr.pdf"]
        r = client.get(f"/requests/{rid}/travellers/{sana['id']}/tickets/"
                       f"{sana['ticket_files'][0]['id']}", headers=auth(org["sana"]))
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        # Someone else's file is still not theirs to take.
        ravi = next(t for t in got["travellers"] if t["full_name"] == "Ravi Kumar")
        assert ravi["ticket_files"] == []
        r = client.get(f"/requests/{rid}/travellers/{ravi['id']}/tickets/{file_id}",
                       headers=auth(org["sana"]))
        assert r.status_code == 404

    def test_a_changed_car_reaches_someone_already_booked(self, client, db, org, outbox):
        rid, rows = trip(db, org, kind="cab", travellers=("ravi", "sana"))
        book(client, org, rid, [rows["ravi"]], cab=CAR)
        outbox.clear()
        book(client, org, rid, [rows["sana"]], cab={**CAR, "vehicle_number": "TS 10 AB 9999"})
        assert sorted(m["to"] for m in outbox.messages) == [
            "ravi@designboxed.com", "sana@designboxed.com"
        ]
        cab_mail = mails(db, "CAB_DETAILS")
        assert [m.to_address for m in cab_mail] == ["ravi@designboxed.com"]
        assert "Your cab has changed" in cab_mail[0].subject


# ---------------------------------------------------------------------------
# Refusals: nothing moves unless everything checks out
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_only_an_admin(self, client, db, org):
        rid, rows = trip(db, org)
        assert book(client, org, rid, [rows["ravi"]], who="ravi",
                    booking_reference="X1").status_code == 403

    def test_approve_first(self, client, db, org):
        rid, rows = trip(db, org, status=TravellerStatus.PENDING)
        r = book(client, org, rid, [rows["ravi"]], booking_reference="X1")
        assert r.status_code == 409 and "Approve them first" in r.json()["detail"]

    def test_all_or_nothing(self, client, db, org):
        rid, rows = trip(db, org, travellers=("ravi", "sana"))
        db.get(RequestTraveller, rows["sana"]).status = TravellerStatus.PENDING
        db.commit()
        r = book(client, org, rid, [rows["ravi"], rows["sana"]], booking_reference="X1")
        assert r.status_code == 409
        assert db.get(RequestTraveller, rows["ravi"]).status is TravellerStatus.APPROVED

    def test_a_reference_unless_a_cab_gives_its_plate(self, client, db, org):
        rid, rows = trip(db, org)
        r = book(client, org, rid, [rows["ravi"]])
        assert r.status_code == 400 and "booking reference" in r.json()["detail"]

    def test_a_car_only_on_a_cab(self, client, db, org):
        rid, rows = trip(db, org)
        r = book(client, org, rid, [rows["ravi"]], booking_reference="X1", cab=CAR)
        assert r.status_code == 400

    def test_files_from_elsewhere_or_thrown_away(self, client, db, org):
        rid, rows = trip(db, org)
        other, other_rows = trip(db, org, travellers=("meena",))
        theirs = upload(db, other, other_rows["meena"], "theirs.pdf")
        assert book(client, org, rid, [rows["ravi"]], booking_reference="X1",
                    ticket_ids=[theirs]).status_code == 404
        gone = upload(db, rid, rows["ravi"], "gone.pdf", status=TicketStatus.DISCARDED)
        assert book(client, org, rid, [rows["ravi"]], booking_reference="X1",
                    ticket_ids=[gone]).status_code == 409

    def test_the_same_traveller_twice(self, client, db, org):
        rid, rows = trip(db, org)
        assert book(client, org, rid, [rows["ravi"], rows["ravi"]],
                    booking_reference="X1").status_code == 422


# ---------------------------------------------------------------------------
# Booking an extension the same as before
# ---------------------------------------------------------------------------


def test_an_extension_is_booked_and_says_what_it_carries_on(client, db, org, outbox):
    rid, rows = trip(db, org, kind="cab")
    book(client, org, rid, [rows["ravi"]], cab=CAR)
    r = client.post(f"/requests/{rid}/extend", headers=auth(org["ravi"]), json={
        "reason": "Two more stores tomorrow",
        "start_at": (START + timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 201, r.text
    child = r.json()
    assert child["previous_booking"].startswith("Dzire (4 seats) TS 09 EA 1234")
    traveller = child["travellers"][0]["id"]
    client.post(f"/requests/{child['id']}/decide", headers=auth(org["admin"]), json={
        "decisions": [{"traveller_id": traveller, "to_status": "APPROVED", "reason": "Fine"}]})
    outbox.clear()

    r = book(client, org, child["id"], [traveller], cab=CAR, note="Same car as yesterday")
    assert r.status_code == 200, r.text
    body = outbox.messages[0]["body"]
    assert f"This carries on your booking on request {rid}." in body
    assert "Note from the travel desk: Same car as yesterday" in body
