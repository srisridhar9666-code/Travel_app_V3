"""
Booking in one step: the ticket uploaded in the booking window is confirmed
with the booking, and the traveller's email carries it as an attachment.

The details the admin checked are what the email lists; the ticket file is
what is attached; and a confirmed ticket is the one the traveller can download
from My requests afterwards.
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    Gender,
    NotificationChannel,
    RequestType,
    Role,
    TicketStatus,
    TravelMode,
    TravellerStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.services import notifications, storage

TENANT = "designboxed"
PDF = b"%PDF-1.4 the ticket"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(storage, "read", lambda path: PDF)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@dataclass
class Ref:
    id: int
    role: Role


def auth(ref):
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def trip(db):
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, gender=Gender.FEMALE, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, gender=Gender.MALE, password_hash="x")
    sana = User(tenant_id=TENANT, email="sana@designboxed.com", full_name="Sana Khan",
                role=Role.GROUND_STAFF, gender=Gender.FEMALE, password_hash="x")
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
    db.add_all([admin, ravi, sana, project])
    db.commit()
    day = clock.now_local().replace(tzinfo=None, microsecond=0) + timedelta(days=9)
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.LONG_DISTANCE, mode=TravelMode.FLIGHT,
        project_id=project.id, requester_id=ravi.id, origin="Hyderabad", destination="Pune",
        start_at=day, travel_reason="Store audit", submitted_at=naive_utcnow(),
    )
    row.travellers = [
        RequestTraveller(user_id=ravi.id, status=TravellerStatus.APPROVED),
        RequestTraveller(user_id=sana.id, status=TravellerStatus.APPROVED),
    ]
    db.add(row)
    db.commit()
    return dict(admin=Ref(admin.id, admin.role), ravi=Ref(ravi.id, ravi.role),
                request=row.id, traveller=row.travellers[0].id, other=row.travellers[1].id)


def upload(db, w, traveller_id=None, status=TicketStatus.EXTRACTED):
    ticket = TicketDocument(
        tenant_id=TENANT, request_id=w["request"], traveller_id=traveller_id or w["traveller"],
        status=status, file_path="tickets/1/abc.pdf", file_name="IndiGo-QK8T2M.pdf",
        content_type="application/pdf", booking_reference="QK8T2N", carrier="Indigo",
    )
    db.add(ticket)
    db.commit()
    return ticket.id


def book(client, w, **extra):
    return client.post(
        f"/requests/{w['request']}/decide",
        headers=auth(w["admin"]),
        json={"decisions": [{
            "traveller_id": w["traveller"], "to_status": "BOOKED", "reason": "Ticket booked",
            "booking_reference": "QK8T2M",
            "booking_details": {"carrier": "IndiGo", "service_number": "6E 4412", "seat": "14C"},
            **extra,
        }]},
    )


def email_for(db, kind="REQUEST_BOOKED"):
    return db.execute(
        select(Notification).where(
            Notification.kind == kind, Notification.channel == NotificationChannel.EMAIL
        )
    ).scalars().first()


def test_the_ticket_is_confirmed_and_attached(client, db, trip, outbox):
    ticket_id = upload(db, trip)
    r = book(client, trip, ticket_id=ticket_id)
    assert r.status_code == 200, r.text

    ticket = db.get(TicketDocument, ticket_id)
    db.refresh(ticket)
    assert ticket.status is TicketStatus.CONFIRMED
    # The admin's checked values are the final word; the model's read is kept.
    assert ticket.confirmed_reference == "QK8T2M" and ticket.booking_reference == "QK8T2N"
    assert ticket.carrier == "IndiGo"

    mail = email_for(db)
    assert mail.attachment_name == "IndiGo-QK8T2M.pdf"
    assert "6E 4412" in mail.body and "14C" in mail.body
    assert "Your ticket is attached" in mail.body
    sent = [m for m in outbox.messages if m["to"] == "ravi@designboxed.com"]
    assert sent and sent[-1]["attachments"] == ["IndiGo-QK8T2M.pdf"]

    # And it is now the ticket the traveller downloads.
    got = client.get(f"/requests/{trip['request']}/travellers/{trip['traveller']}/ticket",
                     headers=auth(trip["ravi"]))
    assert got.status_code == 200 and got.content == PDF


def test_without_a_ticket_the_email_has_the_details_only(client, db, trip, outbox):
    r = book(client, trip)
    assert r.status_code == 200, r.text
    mail = email_for(db)
    assert mail.attachment_path is None
    assert "6E 4412" in mail.body and "attached" not in mail.body
    sent = [m for m in outbox.messages if m["to"] == "ravi@designboxed.com"]
    assert sent and sent[-1]["attachments"] == []


def test_someone_elses_ticket_is_refused(client, db, trip):
    other = upload(db, trip, traveller_id=trip["other"])
    r = book(client, trip, ticket_id=other)
    assert r.status_code == 404
    assert db.get(TravelRequest, trip["request"]).travellers[0].status is TravellerStatus.APPROVED


def test_a_discarded_ticket_is_refused(client, db, trip):
    gone = upload(db, trip, status=TicketStatus.DISCARDED)
    assert book(client, trip, ticket_id=gone).status_code == 409


def test_a_ticket_only_goes_with_a_booking(client, db, trip):
    ticket_id = upload(db, trip)
    r = client.post(
        f"/requests/{trip['request']}/decide",
        headers=auth(trip["admin"]),
        json={"decisions": [{"traveller_id": trip["traveller"], "to_status": "CANCELLED",
                             "reason": "Trip called off", "ticket_id": ticket_id}]},
    )
    assert r.status_code == 200, r.text
    ticket = db.get(TicketDocument, ticket_id)
    db.refresh(ticket)
    assert ticket.status is TicketStatus.EXTRACTED


def test_a_file_that_cannot_be_read_still_sends_the_email(client, db, trip, outbox, monkeypatch):
    def missing(path):
        raise FileNotFoundError(path)

    monkeypatch.setattr(storage, "read", missing)
    ticket_id = upload(db, trip)
    assert book(client, trip, ticket_id=ticket_id).status_code == 200
    sent = [m for m in outbox.messages if m["to"] == "ravi@designboxed.com"]
    assert sent and sent[-1]["attachments"] == []
    assert "could not be attached" in sent[-1]["body"]


def test_the_ticket_confirm_path_attaches_it_too(client, db, trip, outbox):
    ticket_id = upload(db, trip)
    r = client.post(f"/tickets/{ticket_id}/confirm", headers=auth(trip["admin"]),
                    json={"booking_reference": "QK8T2M"})
    assert r.status_code == 200, r.text
    mail = email_for(db, "BOOKING_CONFIRMED")
    assert mail.attachment_name == "IndiGo-QK8T2M.pdf"
    assert any(m["attachments"] == ["IndiGo-QK8T2M.pdf"] for m in outbox.messages)


def test_the_ticket_confirm_path_can_skip_attachment(client, db, trip, outbox):
    ticket_id = upload(db, trip)
    r = client.post(
        f"/tickets/{ticket_id}/confirm",
        headers=auth(trip["admin"]),
        json={"booking_reference": "QK8T2M", "attach_ticket": False},
    )
    assert r.status_code == 200, r.text
    mail = email_for(db, "BOOKING_CONFIRMED")
    assert mail.attachment_name is None
    assert "attached" not in mail.body
    sent = [m for m in outbox.messages if m["to"] == "ravi@designboxed.com"]
    assert sent and sent[-1]["attachments"] == []


def test_notify_keeps_the_file_for_a_retry(db, trip):
    person = db.get(User, trip["ravi"].id)
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="BOOKING_CONFIRMED", title="Booked",
        body="Booked", deliver_now=False,
        attachment=notifications.AttachedFile(path="tickets/1/abc.pdf", name="t.pdf",
                                              content_type="application/pdf"),
    )
    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert (mail.attachment_path, mail.attachment_name) == ("tickets/1/abc.pdf", "t.pdf")
    in_app = next(r for r in rows if r.channel is NotificationChannel.IN_APP)
    assert in_app.attachment_path is None
