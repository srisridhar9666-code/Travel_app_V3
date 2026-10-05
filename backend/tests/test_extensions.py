"""
Extending a decided cab or stay: a linked request of its own.

The work ran over - the cab is wanted tomorrow too, or two more nights at the
hotel. Someone riding asks; the extension goes into the queue like any other
request, linked to the trip it carries on, so it can be booked "the same as
before" or with a different car, and costed on its own.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import Gender, NotificationChannel, Role, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.project import Project
from app.models.request import Notification, TravelRequest
from app.models.user import User
from app.services import notifications

TENANT = "designboxed"


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
    db.add(project)
    db.commit()
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    lead = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=lead.id)
    sana = person(db, "Sana Khan", "sana@designboxed.com", manager_id=lead.id)
    meena = person(db, "Meena Iyer", "meena@designboxed.com")
    refs = {k: Ref(u.id, u.role) for k, u in
            dict(admin=admin, lead=lead, ravi=ravi, sana=sana, meena=meena).items()}
    return dict(refs, project=project.id)


DAY = clock.local_today() + timedelta(days=10)
START = datetime(DAY.year, DAY.month, DAY.day, 9, 0)
END = datetime(DAY.year, DAY.month, DAY.day, 18, 0)
NEXT_START = START + timedelta(days=1)
NEXT_END = END + timedelta(days=1)
CAR = dict(booked_cab_type="SEDAN", vehicle_number="TS 09 EA 1234",
           driver_name="Suresh Reddy", driver_phone="9876543210")


def raise_trip(client, org, kind="cab", who="ravi", travellers=("ravi",), **overrides):
    body = dict(project_id=org["project"], travel_reason="Store audits in the old city",
                traveller_ids=[org[t].id for t in travellers])
    if kind == "cab":
        body.update(request_type="LOCAL_CAB", origin="Banjara Hills", origin_state="Telangana",
                    pickup_city="Hyderabad", destination="Charminar",
                    destination_state="Telangana", drop_city="Hyderabad",
                    start_at=START.isoformat(), end_at=END.isoformat(), cab_type="SUV")
    elif kind == "hotel":
        body.update(request_type="HOTEL", hotel_city="Pune", hotel_state="Maharashtra",
                    check_in=str(DAY), check_out=str(DAY + timedelta(days=2)))
    else:
        body.update(request_type="LONG_DISTANCE", mode="FLIGHT", origin="Hyderabad",
                    destination="Pune", start_at=START.isoformat())
    body.update(overrides)
    r = client.post("/requests", headers=auth(org[who]), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def decide(client, org, request_id, traveller_id, to="APPROVED", **extra):
    r = client.post(f"/requests/{request_id}/decide", headers=auth(org["admin"]), json={
        "decisions": [{"traveller_id": traveller_id, "to_status": to,
                       "reason": "Needed on site", **extra}]})
    assert r.status_code == 200, r.text
    return r.json()


def approved(client, org, kind="cab", **kw):
    made = raise_trip(client, org, kind, **kw)
    for traveller in made["travellers"]:
        decide(client, org, made["id"], traveller["id"])
    return client.get(f"/requests/{made['id']}", headers=auth(org["admin"])).json()


def extend(client, org, request_id, who="ravi", **body):
    payload = {"reason": "Two more stores to audit tomorrow", **body}
    return client.post(f"/requests/{request_id}/extend", headers=auth(org[who]), json=payload)


def cab_next_day(**extra):
    return dict(start_at=NEXT_START.isoformat(), end_at=NEXT_END.isoformat(), **extra)


def notices(db, kind, *, user=None, channel=NotificationChannel.IN_APP):
    query = select(Notification).where(Notification.kind == kind, Notification.channel == channel)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return db.execute(query.order_by(Notification.id)).scalars().all()


# ---------------------------------------------------------------------------
# A cab kept another day
# ---------------------------------------------------------------------------


class TestExtendingACab:
    def test_it_is_a_new_request_linked_to_the_cab(self, client, db, org):
        made = approved(client, org)
        client.put(f"/requests/{made['id']}/cab-booking", headers=auth(org["admin"]),
                   json={**CAR, "notify": False})
        r = extend(client, org, made["id"], **cab_next_day())
        assert r.status_code == 201, r.text
        child = r.json()

        assert child["id"] != made["id"] and child["extends_request_id"] == made["id"]
        assert child["status"] == "SUBMITTED"
        assert child["request_type"] == "LOCAL_CAB"
        assert child["origin"] == "Banjara Hills" and child["drop_city"] == "Hyderabad"
        assert child["start_at"].startswith(NEXT_START.isoformat())
        assert child["end_at"].startswith(NEXT_END.isoformat())
        assert child["travel_reason"] == "Two more stores to audit tomorrow"
        # The size that actually went, so the vendor quotes the same car.
        assert child["cab_type"] == "SEDAN"
        assert child["previous_booking"].startswith("Dzire (4 seats) TS 09 EA 1234")
        assert [(t["full_name"], t["status"]) for t in child["travellers"]] == [
            ("Ravi Kumar", "PENDING")
        ]
        assert child["can_extend"] is False   # nobody has decided it yet

        parent = client.get(f"/requests/{made['id']}", headers=auth(org["ravi"])).json()
        assert parent["extended_by_request_id"] == child["id"]
        assert parent["can_extend"] is False
        # The cab itself is untouched: it still ends when it ended.
        assert parent["end_at"].startswith(END.isoformat())

    def test_the_admins_are_asked_and_the_manager_for_a_view(self, client, db, org):
        made = approved(client, org)
        child = extend(client, org, made["id"], **cab_next_day()).json()

        asked = notices(db, "REQUEST_SUBMITTED", user=org["admin"])[-1]
        assert asked.request_id == child["id"]
        assert f"asked to extend request {made['id']}" in asked.title
        mail = notices(db, "REQUEST_SUBMITTED", user=org["admin"],
                       channel=NotificationChannel.EMAIL)[-1]
        assert mail.subject.startswith("Extension asked: Cab:")
        assert "The trip it extends is not booked yet." in mail.body

        lead = notices(db, "TEAM_REQUEST_SUBMITTED", user=org["lead"])
        assert lead and lead[-1].request_id == child["id"]

        entry = db.execute(
            select(AuditLog).where(AuditLog.entity_type == "travel_request",
                                   AuditLog.entity_id == made["id"],
                                   AuditLog.action == "SUBMIT").order_by(AuditLog.id)
        ).scalars().all()[-1]
        assert entry.summary.startswith(f"Ravi Kumar asked to extend request {made['id']}")
        assert entry.reason == "Two more stores to audit tomorrow"

    def test_everyone_riding_is_carried_on_by_default(self, client, org):
        made = approved(client, org, travellers=("ravi", "sana"))
        child = extend(client, org, made["id"], **cab_next_day()).json()
        assert [t["full_name"] for t in child["travellers"]] == ["Ravi Kumar", "Sana Khan"]

    def test_or_only_those_named_and_always_whoever_asks(self, client, org):
        made = approved(client, org, travellers=("ravi", "sana"))
        sana_row = next(t["id"] for t in made["travellers"] if t["full_name"] == "Sana Khan")
        child = extend(client, org, made["id"], who="ravi",
                       traveller_ids=[sana_row], **cab_next_day()).json()
        assert [t["full_name"] for t in child["travellers"]] == ["Ravi Kumar", "Sana Khan"]

        made2 = approved(client, org, travellers=("ravi", "sana"),
                         start_at=(START + timedelta(days=3)).isoformat(),
                         end_at=(END + timedelta(days=3)).isoformat())
        ravi_row = next(t["id"] for t in made2["travellers"] if t["full_name"] == "Ravi Kumar")
        child = extend(client, org, made2["id"], traveller_ids=[ravi_row],
                       start_at=(NEXT_START + timedelta(days=3)).isoformat()).json()
        assert [t["full_name"] for t in child["travellers"]] == ["Ravi Kumar"]

    def test_the_extension_does_not_clash_with_the_cab_it_extends(self, client, org):
        made = approved(client, org)
        child = extend(client, org, made["id"], **cab_next_day()).json()
        assert child["conflicts"] == []

    def test_an_extension_can_be_extended_once_decided(self, client, org):
        made = approved(client, org)
        child = extend(client, org, made["id"], **cab_next_day()).json()
        decide(client, org, child["id"], child["travellers"][0]["id"])
        got = client.get(f"/requests/{child['id']}", headers=auth(org["ravi"])).json()
        assert got["can_extend"] is True
        again = extend(client, org, child["id"], start_at=(NEXT_START + timedelta(days=1)).isoformat())
        assert again.status_code == 201, again.text
        assert again.json()["extends_request_id"] == child["id"]

    def test_a_turned_down_extension_frees_the_trip_to_ask_again(self, client, org):
        made = approved(client, org)
        child = extend(client, org, made["id"], **cab_next_day()).json()
        decide(client, org, child["id"], child["travellers"][0]["id"], to="REJECTED")
        parent = client.get(f"/requests/{made['id']}", headers=auth(org["ravi"])).json()
        assert parent["extended_by_request_id"] is None and parent["can_extend"] is True
        assert extend(client, org, made["id"], **cab_next_day()).status_code == 201


class TestWhoMayExtend:
    def test_not_before_an_admin_has_acted(self, client, org):
        made = raise_trip(client, org)
        assert made["can_extend"] is False
        r = extend(client, org, made["id"], **cab_next_day())
        assert r.status_code == 409 and "change its dates" in r.json()["detail"]

    def test_only_someone_approved_on_it(self, client, org):
        made = raise_trip(client, org, travellers=("ravi", "sana"))
        ravi_row = next(t["id"] for t in made["travellers"] if t["full_name"] == "Ravi Kumar")
        decide(client, org, made["id"], ravi_row)
        got = client.get(f"/requests/{made['id']}", headers=auth(org["sana"])).json()
        assert got["can_extend"] is False
        r = extend(client, org, made["id"], who="sana", **cab_next_day())
        assert r.status_code == 409 and "not approved or booked" in r.json()["detail"]

    @pytest.mark.parametrize("who,code", [("admin", 403), ("meena", 404)])
    def test_nobody_off_the_trip(self, client, org, who, code):
        made = approved(client, org)
        assert extend(client, org, made["id"], who=who, **cab_next_day()).status_code == code

    def test_not_a_flight(self, client, org):
        made = approved(client, org, kind="flight")
        assert made["can_extend"] is False
        r = extend(client, org, made["id"], **cab_next_day())
        assert r.status_code == 400 and "Only a cab or a hotel stay" in r.json()["detail"]

    def test_not_a_cancelled_trip(self, client, org):
        made = approved(client, org)
        client.post(f"/requests/{made['id']}/cancel", headers=auth(org["admin"]),
                    json={"reason": "Audit moved"})
        assert extend(client, org, made["id"], **cab_next_day()).status_code == 409

    def test_one_live_extension_at_a_time(self, client, org):
        made = approved(client, org)
        child = extend(client, org, made["id"], **cab_next_day()).json()
        r = extend(client, org, made["id"], **cab_next_day())
        assert r.status_code == 409
        assert r.json()["detail"].startswith(f"This trip is already extended by request {child['id']}")

    @pytest.mark.parametrize("reason", ["", "  a ", "x" * 501])
    def test_a_reason_is_required(self, client, org, reason):
        made = approved(client, org)
        assert extend(client, org, made["id"], reason=reason, **cab_next_day()).status_code == 422

    def test_a_cab_needs_its_pickup_after_the_current_one_ends(self, client, org):
        made = approved(client, org)
        assert extend(client, org, made["id"]).status_code == 422
        r = extend(client, org, made["id"], start_at=(END - timedelta(hours=1)).isoformat())
        assert r.status_code == 422 and "after the cab booked now" in r.json()["detail"]
        # Later the same evening is fine - the work ran late, not into tomorrow.
        assert extend(client, org, made["id"],
                      start_at=(END + timedelta(hours=1)).isoformat()).status_code == 201

    def test_at_most_a_month_at_once(self, client, org):
        made = approved(client, org)
        r = extend(client, org, made["id"], start_at=NEXT_START.isoformat(),
                   end_at=(END + timedelta(days=40)).isoformat())
        assert r.status_code == 422 and "at most 31 days" in r.json()["detail"]

    def test_the_cab_cannot_be_let_go_before_it_picks_up(self, client, org):
        made = approved(client, org)
        r = extend(client, org, made["id"], start_at=NEXT_END.isoformat(),
                   end_at=NEXT_START.isoformat())
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# A stay made longer
# ---------------------------------------------------------------------------


class TestExtendingAStay:
    def test_it_checks_in_the_day_the_stay_checks_out(self, client, org):
        made = approved(client, org, kind="hotel")
        r = extend(client, org, made["id"], check_out=str(DAY + timedelta(days=4)))
        assert r.status_code == 201, r.text
        child = r.json()
        assert child["request_type"] == "HOTEL" and child["hotel_city"] == "Pune"
        assert child["check_in"] == str(DAY + timedelta(days=2))
        assert child["check_out"] == str(DAY + timedelta(days=4))
        assert child["extends_request_id"] == made["id"]
        # Back to back nights are not a clash.
        assert child["conflicts"] == []

    def test_the_new_check_out_is_after_the_old_one(self, client, org):
        made = approved(client, org, kind="hotel")
        assert extend(client, org, made["id"]).status_code == 422
        r = extend(client, org, made["id"], check_out=str(DAY + timedelta(days=2)))
        assert r.status_code == 422 and "has to be after" in r.json()["detail"]
        r = extend(client, org, made["id"], check_out=str(DAY + timedelta(days=40)))
        assert r.status_code == 422 and "at most 31 nights" in r.json()["detail"]

    def test_the_hotel_booked_before_is_shown(self, client, org):
        made = approved(client, org, kind="hotel")
        decide(client, org, made["id"], made["travellers"][0]["id"], to="BOOKED",
               booking_reference="LT-88812",
               booking_details={"hotel_name": "Lemon Tree", "hotel_address": "Hinjewadi, Pune"})
        child = extend(client, org, made["id"], check_out=str(DAY + timedelta(days=3))).json()
        assert child["previous_booking"] == "Lemon Tree, Hinjewadi, Pune · confirmation LT-88812"


# ---------------------------------------------------------------------------
# The admin queue
# ---------------------------------------------------------------------------


def test_the_queue_counts_and_lists_extensions_waiting(client, org):
    made = approved(client, org)
    plain = raise_trip(client, org, kind="hotel", who="meena", travellers=("meena",))
    child = extend(client, org, made["id"], **cab_next_day()).json()

    counts = client.get("/requests/queue/counts", headers=auth(org["admin"])).json()
    assert counts["extensions"] == 1
    listed = client.get("/requests", headers=auth(org["admin"]),
                        params={"mine": False, "extensions_only": True}).json()
    assert [r["id"] for r in listed["items"]] == [child["id"]]
    assert plain["id"] not in [r["id"] for r in listed["items"]]

    decide(client, org, child["id"], child["travellers"][0]["id"])
    counts = client.get("/requests/queue/counts", headers=auth(org["admin"])).json()
    assert counts["extensions"] == 0


def test_the_travellers_on_the_cab_are_untouched(client, db, org):
    made = approved(client, org)
    extend(client, org, made["id"], **cab_next_day())
    row = db.get(TravelRequest, made["id"])
    assert [t.status for t in row.travellers] == [TravellerStatus.APPROVED]
    assert row.end_at == END
