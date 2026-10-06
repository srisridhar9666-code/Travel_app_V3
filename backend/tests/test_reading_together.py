"""
Several ticket files for one booking: read together, and all downloadable.

An admin often has more than one file for a trip - the e-ticket and the agent's
invoice, two legs of a connecting flight, one ticket per traveller. Each is read
as it is uploaded; the booking window then fills from what they say together
(`extraction.combine`), and the traveller can download every one of them,
singly or as one zip.
"""
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core import clock, ratelimit
from app.core.enums import Gender, Role, TicketStatus, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.ticket import TicketDocument
from app.models.user import User
from app.services import extraction, storage
from app.services.extraction import Extraction, combine

TENANT = "designboxed"
READ = datetime(2026, 10, 5, 10, 0)


def ticket(**fields):
    """A ticket row as combine() sees it: read, with these fields."""
    base = dict(
        extracted_at=READ, extraction_error=None, booking_reference=None, carrier=None,
        service_number=None, passenger_name=None, depart_at=None, arrive_at=None,
        hotel_name=None, check_in=None, check_out=None, fare_amount=None, fare_currency=None,
    )
    base.update(fields)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# What the files say together
# ---------------------------------------------------------------------------


class TestCombine:
    def test_an_e_ticket_and_its_invoice_are_one_booking(self):
        got = combine([
            ticket(booking_reference="QK8T2M", carrier="IndiGo", service_number="6E 4412",
                   depart_at=datetime(2026, 10, 9, 6, 10), arrive_at=datetime(2026, 10, 9, 7, 40),
                   fare_amount=Decimal("4512.00"), fare_currency="INR"),
            ticket(booking_reference="qk8t2m", fare_amount=Decimal("4512.00"), fare_currency="INR"),
        ])
        assert got.files == got.files_read == 2
        assert got.booking_reference == "QK8T2M"
        assert got.carrier == "IndiGo" and got.service_number == "6E 4412"
        # Same PNR on both files: the fare is paid once, not twice.
        assert got.fare_total == Decimal("4512.00") and got.fare_currency == "INR"

    def test_two_legs_join_and_span_the_journey(self):
        got = combine([
            ticket(booking_reference="AB12CD", carrier="IndiGo", service_number="6E 101",
                   depart_at=datetime(2026, 10, 9, 6, 0), arrive_at=datetime(2026, 10, 9, 7, 30),
                   fare_amount=Decimal("3000"), fare_currency="INR"),
            ticket(booking_reference="EF34GH", carrier="Air India", service_number="AI 202",
                   depart_at=datetime(2026, 10, 9, 9, 0), arrive_at=datetime(2026, 10, 9, 10, 15),
                   fare_amount=Decimal("2500.50"), fare_currency="INR"),
        ])
        assert got.booking_reference == "AB12CD / EF34GH"
        assert got.carrier == "IndiGo / Air India"
        assert got.service_number == "6E 101 / AI 202"
        assert got.depart_at == datetime(2026, 10, 9, 6, 0)
        assert got.arrive_at == datetime(2026, 10, 9, 10, 15)
        assert got.fare_total == Decimal("5500.50")
        assert any("2 different references" in note for note in got.notes)

    def test_a_stay_runs_from_the_first_night_to_the_last(self):
        got = combine([
            ticket(hotel_name="Lemon Tree", check_in=date(2026, 10, 9), check_out=date(2026, 10, 11)),
            ticket(hotel_name="Lemon Tree", check_in=date(2026, 10, 11), check_out=date(2026, 10, 13)),
        ])
        assert got.hotel_name == "Lemon Tree"
        assert (got.check_in, got.check_out) == (date(2026, 10, 9), date(2026, 10, 13))

    def test_an_unread_file_is_counted_and_said(self):
        got = combine([
            ticket(booking_reference="QK8T2M"),
            ticket(extraction_error="could not read", extracted_at=READ),
        ])
        assert (got.files, got.files_read) == (2, 1)
        assert got.booking_reference == "QK8T2M"
        assert "1 file could not be read - check it by eye." in got.notes

    def test_nothing_read_proposes_nothing(self):
        got = combine([ticket(extracted_at=None), ticket(extraction_error="x")])
        assert got.files_read == 0 and got.booking_reference is None and got.fare_total is None
        assert "2 files could not be read - check them by eye." in got.notes

    def test_fares_in_two_currencies_are_not_added(self):
        got = combine([
            ticket(fare_amount=Decimal("100"), fare_currency="USD"),
            ticket(fare_amount=Decimal("4000"), fare_currency="INR"),
        ])
        assert got.fare_total is None
        assert any("INR, USD" in note for note in got.notes)

    def test_files_with_no_reference_each_count(self):
        got = combine([
            ticket(fare_amount=Decimal("700"), fare_currency="INR"),
            ticket(fare_amount=Decimal("800"), fare_currency="INR"),
        ])
        assert got.fare_total == Decimal("1500")

    def test_names_that_differ_are_pointed_out(self):
        got = combine([ticket(passenger_name="RAVI KUMAR"), ticket(passenger_name="SANA KHAN")])
        assert any("RAVI KUMAR, SANA KHAN" in note for note in got.notes)


# ---------------------------------------------------------------------------
# Through the API
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(storage, "read", lambda path: f"file at {path}".encode())
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


def auth(ref):
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(db):
    people = {}
    for key, name, role in (
        ("admin", "Priya Shah", Role.ADMIN), ("ravi", "Ravi Kumar", Role.GROUND_STAFF),
        ("sana", "Sana Khan", Role.GROUND_STAFF), ("meena", "Meena Iyer", Role.GROUND_STAFF),
    ):
        user = User(tenant_id=TENANT, email=f"{key}@designboxed.com", full_name=name, role=role,
                    gender=Gender.MALE, password_hash="x")
        db.add(user)
        db.commit()
        people[key] = Ref(user.id, user.role)
    project = Project(tenant_id=TENANT, name="Temple Trail", code="CMP-2026-0002")
    db.add(project)
    db.commit()
    start = clock.now_local().replace(tzinfo=None, microsecond=0) + timedelta(days=9)
    row = TravelRequest(tenant_id=TENANT, request_type="LONG_DISTANCE", mode="FLIGHT",
                        project_id=project.id, requester_id=people["ravi"].id,
                        origin="Hyderabad", destination="Pune", start_at=start,
                        travel_reason="Audit", submitted_at=clock.now_local().replace(tzinfo=None))
    row.travellers = [
        RequestTraveller(user_id=people["ravi"].id, status=TravellerStatus.APPROVED),
        RequestTraveller(user_id=people["sana"].id, status=TravellerStatus.APPROVED),
    ]
    db.add(row)
    db.commit()
    return dict(people, request=row.id, ravi_row=row.travellers[0].id,
                sana_row=row.travellers[1].id)


def add_file(db, org, name, *, traveller="ravi_row", status=TicketStatus.EXTRACTED, **fields):
    row = TicketDocument(tenant_id=TENANT, request_id=org["request"], traveller_id=org[traveller],
                         status=status, file_path=f"tickets/{org['request']}/{name}-{len(fields)}",
                         file_name=name, content_type="application/pdf",
                         extracted_at=READ, **fields)
    db.add(row)
    db.commit()
    return row.id


class TestReadTogetherEndpoint:
    def test_the_booking_window_reads_every_file(self, client, db, org):
        one = add_file(db, org, "eticket.pdf", booking_reference="QK8T2M", carrier="IndiGo",
                       fare_amount=Decimal("4512"), fare_currency="INR")
        two = add_file(db, org, "invoice.pdf", booking_reference="QK8T2M",
                       fare_amount=Decimal("4512"), fare_currency="INR")
        r = client.get(f"/requests/{org['request']}/tickets/combined",
                       params={"ids": [one, two]}, headers=auth(org["admin"]))
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["files"] == 2 and got["booking_reference"] == "QK8T2M"
        assert got["fare_total"] == "4512.00"

    def test_only_this_requests_live_files(self, client, db, org):
        gone = add_file(db, org, "old.pdf", status=TicketStatus.DISCARDED)
        r = client.get(f"/requests/{org['request']}/tickets/combined",
                       params={"ids": [gone]}, headers=auth(org["admin"]))
        assert r.status_code == 404
        r = client.get(f"/requests/{org['request']}/tickets/combined",
                       params={"ids": [987654]}, headers=auth(org["admin"]))
        assert r.status_code == 404

    def test_admins_only(self, client, db, org):
        one = add_file(db, org, "eticket.pdf")
        r = client.get(f"/requests/{org['request']}/tickets/combined",
                       params={"ids": [one]}, headers=auth(org["ravi"]))
        assert r.status_code == 403


class TestUpload:
    def test_a_file_is_read_as_it_lands(self, client, db, org, monkeypatch):
        monkeypatch.setattr(storage, "save_in", lambda *a, **k: "tickets/x/upload.pdf")
        monkeypatch.setattr(storage, "validate", lambda file, data: "pdf")
        monkeypatch.setattr(extraction, "extract", lambda data, content_type: Extraction(
            ok=True, fields={"booking_reference": "QK8T2M", "carrier": "IndiGo"},
            confidence={"booking_reference": 0.98}, model_id="test-model"))
        r = client.post(f"/requests/{org['request']}/tickets",
                        params={"traveller_id": org["ravi_row"]}, headers=auth(org["admin"]),
                        files={"file": ("eticket.pdf", b"%PDF-1.4 ticket", "application/pdf")})
        assert r.status_code == 201, r.text
        got = r.json()
        assert got["status"] == "EXTRACTED" and got["booking_reference"] == "QK8T2M"
        assert got["model_id"] == "test-model"

    def test_nothing_is_written_while_the_file_is_read(self, client, db, org, monkeypatch):
        """The read happens off the event loop, so the request must hold no lock
        across it: the audit chain's tail is locked from the first audit row to
        commit, and a second upload waiting on it would stall the loop."""
        monkeypatch.setattr(storage, "save_in", lambda *a, **k: "tickets/x/upload.pdf")
        monkeypatch.setattr(storage, "validate", lambda file, data: "pdf")
        audits_before = db.scalar(select(func.count()).select_from(AuditLog))
        seen = {}

        def extract(data, content_type):
            seen["tickets"] = db.scalar(select(func.count()).select_from(TicketDocument))
            seen["audits"] = db.scalar(select(func.count()).select_from(AuditLog)) - audits_before
            return Extraction(ok=True, fields={"booking_reference": "QK8T2M"}, model_id="m")

        monkeypatch.setattr(extraction, "extract", extract)
        r = client.post(f"/requests/{org['request']}/tickets",
                        params={"traveller_id": org["ravi_row"]}, headers=auth(org["admin"]),
                        files={"file": ("eticket.pdf", b"%PDF-1.4 ticket", "application/pdf")})
        assert r.status_code == 201, r.text
        assert seen == {"tickets": 0, "audits": 0}


class TestDownloadingEveryFile:
    def booked(self, db, org, names):
        ids = [add_file(db, org, name, status=TicketStatus.CONFIRMED, file_size=2048 * (i + 1))
               for i, name in enumerate(names)]
        return ids

    def test_the_traveller_gets_all_in_one_zip(self, client, db, org):
        self.booked(db, org, ["ticket.pdf", "ticket.pdf", "invoice.pdf"])
        r = client.get(f"/requests/{org['request']}/travellers/{org['ravi_row']}/tickets.zip",
                       headers=auth(org["ravi"]))
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "application/zip"
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert names == ["ticket.pdf", "ticket (2).pdf", "invoice.pdf"]

    def test_and_each_one_singly(self, client, db, org):
        ids = self.booked(db, org, ["ticket.pdf", "invoice.pdf"])
        got = client.get(f"/requests/{org['request']}", headers=auth(org["ravi"])).json()
        mine = next(t for t in got["travellers"] if t["id"] == org["ravi_row"])
        assert [f["file_name"] for f in mine["ticket_files"]] == ["ticket.pdf", "invoice.pdf"]
        # The size too, so the admin's Tickets count tells two files of one
        # name apart, as the Tickets list does.
        assert [f["file_size"] for f in mine["ticket_files"]] == [2048, 4096]
        for file_id in ids:
            r = client.get(f"/requests/{org['request']}/travellers/{org['ravi_row']}"
                           f"/tickets/{file_id}", headers=auth(org["ravi"]))
            assert r.status_code == 200

    def test_nobody_else_and_nothing_unbooked(self, client, db, org):
        self.booked(db, org, ["ticket.pdf"])
        url = f"/requests/{org['request']}/travellers/{org['ravi_row']}/tickets.zip"
        assert client.get(url, headers=auth(org["meena"])).status_code == 404
        # Sana is on the trip, but this is Ravi's file.
        assert client.get(url, headers=auth(org["sana"])).status_code == 404
        # An admin may.
        assert client.get(url, headers=auth(org["admin"])).status_code == 200
        add_file(db, org, "draft.pdf", traveller="sana_row")   # read, not booked
        sana = f"/requests/{org['request']}/travellers/{org['sana_row']}/tickets.zip"
        assert client.get(sana, headers=auth(org["sana"])).status_code == 404
