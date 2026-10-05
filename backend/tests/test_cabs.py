"""
Cabs: what is asked for, the car that was sent, and one more day.

* A cab asks for a size - no preference, a Dzire (4 seats) or an Ertiga (7
  seats) - and says whether it is local (within LOCAL_CAB_MAX_KM) or
  outstation, with an approximate distance for outstation. Edits go through
  the revision trail like every other field.
* Once someone on it is approved, an admin records the car sent, its plate and
  driver, and may change them. Each change is logged, everyone riding is told,
  their manager copied. Nobody's status moves.
* After an admin has acted, the requester or a traveller may ask to keep the
  cab one more day. An admin approves - end_at moves a day, so conflict
  detection sees the longer booking - or rejects with a comment.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    LOCAL_CAB_MAX_KM,
    CabExtensionStatus,
    CabTrip,
    CabType,
    Gender,
    NotificationChannel,
    Role,
    TravellerStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, TravelRequest
from app.models.user import User
from app.schemas.request import RequestBody
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


def person(db, name, email_address, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email_address, full_name=name, role=role,
                gender=Gender.MALE, password_hash="x", **extra)
    db.add(user)
    db.commit()
    return user


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class Ref:
    """Who someone is, without the ORM row: the request endpoints close the
    test's session, which detaches every row in it."""

    id: int
    role: Role
    email: str


@pytest.fixture
def org(db):
    project = Project(tenant_id=TENANT, name="Temple Trail", code="CMP-2026-0002")
    db.add(project)
    db.commit()
    owner = person(db, "Sridhar Rao", "sridhar@designboxed.com", Role.SUPER_ADMIN)
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    lead = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER)
    other_lead = person(db, "Divya Nair", "divya@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=lead.id)
    sana = person(db, "Sana Khan", "sana@designboxed.com", manager_id=lead.id)
    meena = person(db, "Meena Iyer", "meena@designboxed.com", manager_id=other_lead.id)
    people = dict(owner=owner, admin=admin, lead=lead, other_lead=other_lead,
                  ravi=ravi, sana=sana, meena=meena)
    refs = {key: Ref(u.id, u.role, u.email) for key, u in people.items()}
    return dict(refs, project=Ref(project.id, Role.GROUND_STAFF, ""))


DAY = clock.local_today() + timedelta(days=10)
START = datetime(DAY.year, DAY.month, DAY.day, 6, 0)
END = datetime(DAY.year, DAY.month, DAY.day, 20, 0)


def cab_body(org, travellers=("ravi",), **overrides):
    body = dict(
        request_type="LOCAL_CAB",
        project_id=org["project"].id,
        origin="Banjara Hills",
        origin_state="Telangana",
        pickup_city="Hyderabad",
        destination="Tirumala temple",
        destination_state="Andhra Pradesh",
        drop_city="Tirupati",
        start_at=START.isoformat(),
        end_at=END.isoformat(),
        travel_reason="Store audits along the temple route",
        cab_type="SUV",
        cab_trip="OUTSTATION",
        cab_distance_km=560,
        traveller_ids=[org[t].id for t in travellers],
    )
    body.update(overrides)
    return body


def raise_cab(client, org, who="ravi", travellers=("ravi",), **overrides):
    r = client.post("/requests", headers=auth(org[who]),
                    json=cab_body(org, travellers=travellers, **overrides))
    assert r.status_code == 201, r.text
    return r.json()


def decide(client, org, request_id, traveller_id, to="APPROVED", reason="Audit run", **extra):
    r = client.post(
        f"/requests/{request_id}/decide",
        headers=auth(org["admin"]),
        json={"decisions": [{"traveller_id": traveller_id, "to_status": to, "reason": reason,
                             **extra}]},
    )
    assert r.status_code == 200, r.text
    return r.json()


def approved_cab(client, org, **overrides):
    made = raise_cab(client, org, **overrides)
    for traveller in made["travellers"]:
        decide(client, org, made["id"], traveller["id"])
    return made


CAR = dict(booked_cab_type="SUV", vehicle_number="ts 09  ea 1234",
           driver_name="Suresh  Reddy", driver_phone="+91 98765 43210")


def record_cab(client, org, request_id, who="admin", **overrides):
    return client.put(f"/requests/{request_id}/cab-booking", headers=auth(org[who]),
                      json={**CAR, **overrides})


def ask(db, org, request_id, who="ravi", reason="Audit runs into a second day"):
    """An ask made with the older "one more day" button. Extending is a linked
    request now (test_extensions.py), but an ask made before that is still
    answered from Approvals - so it is planted as it was stored."""
    row = db.get(TravelRequest, request_id)
    row.cab_extension_status = CabExtensionStatus.PENDING
    row.cab_extension_reason = reason
    row.cab_extension_requested_by_id = org[who].id
    row.cab_extension_requested_at = naive_utcnow()
    row.cab_extension_decided_by_id = None
    row.cab_extension_decided_at = None
    row.cab_extension_comment = None
    db.commit()


def answer(client, org, request_id, approve=True, comment=None, who="admin"):
    body = {"approve": approve}
    if comment is not None:
        body["comment"] = comment
    return client.post(f"/requests/{request_id}/cab-extension/decide",
                       headers=auth(org[who]), json=body)


def notices(db, kind, *, user=None, channel=NotificationChannel.IN_APP):
    query = select(Notification).where(Notification.kind == kind, Notification.channel == channel)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return db.execute(query.order_by(Notification.id)).scalars().all()


def log(db, request_id, action):
    return db.execute(
        select(AuditLog).where(
            AuditLog.entity_type == "travel_request",
            AuditLog.entity_id == request_id,
            AuditLog.action == action,
        ).order_by(AuditLog.id)
    ).scalars().all()


def counts(client, org, who="admin"):
    r = client.get("/requests/queue/counts", headers=auth(org[who]))
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# What a cab asks for
# ---------------------------------------------------------------------------


FORM = dict(
    request_type="LOCAL_CAB", project_id=1, travel_reason="Store audit",
    origin="Banjara Hills", destination="RGIA Airport", start_at="2026-10-05T06:00",
    origin_state="Telangana", pickup_city="Hyderabad",
    destination_state="Telangana", drop_city="Hyderabad",
)


class TestWhatACabAsksFor:
    def test_by_default_any_cab_in_town(self):
        body = RequestBody(**FORM)
        assert body.cab_type is CabType.NO_PREFERENCE
        assert body.cab_trip is CabTrip.LOCAL
        assert body.cab_distance_km is None

    def test_a_local_trip_may_give_a_distance_under_the_line(self):
        body = RequestBody(**FORM, cab_type="SEDAN", cab_distance_km=LOCAL_CAB_MAX_KM - 1)
        assert body.cab_type is CabType.SEDAN and body.cab_distance_km == LOCAL_CAB_MAX_KM - 1

    @pytest.mark.parametrize("km", [LOCAL_CAB_MAX_KM, 250])
    def test_a_local_trip_stays_under_the_line(self, km):
        with pytest.raises(ValidationError, match=f"within {LOCAL_CAB_MAX_KM} km"):
            RequestBody(**FORM, cab_trip="LOCAL", cab_distance_km=km)

    @pytest.mark.parametrize("km", [None, LOCAL_CAB_MAX_KM - 1, 5001])
    def test_outstation_needs_a_distance_from_the_line_to_5000(self, km):
        with pytest.raises(ValidationError, match="approximate distance"):
            RequestBody(**FORM, cab_trip="OUTSTATION", cab_distance_km=km)

    @pytest.mark.parametrize("km", [LOCAL_CAB_MAX_KM, 5000])
    def test_outstation_accepts_the_edges(self, km):
        assert RequestBody(**FORM, cab_trip="OUTSTATION", cab_distance_km=km).cab_distance_km == km

    @pytest.mark.parametrize("km", [0, -5])
    def test_a_distance_is_a_positive_number(self, km):
        with pytest.raises(ValidationError, match="1 or more"):
            RequestBody(**FORM, cab_distance_km=km)

    def test_an_unknown_cab_type_is_refused(self):
        with pytest.raises(ValidationError):
            RequestBody(**FORM, cab_type="LIMOUSINE")

    def test_flights_and_hotels_carry_no_cab_fields(self):
        flight = RequestBody(**{**FORM, "request_type": "LONG_DISTANCE", "mode": "FLIGHT",
                                "origin": "Hyderabad", "destination": "Pune",
                                "cab_type": "SUV", "cab_trip": "OUTSTATION",
                                "cab_distance_km": 600})
        assert (flight.cab_type, flight.cab_trip, flight.cab_distance_km) == (None, None, None)
        hotel = RequestBody(**{**FORM, "request_type": "HOTEL", "hotel_city": "Pune",
                               "check_in": "2026-10-05", "cab_type": "SUV"})
        assert (hotel.cab_type, hotel.cab_trip, hotel.cab_distance_km) == (None, None, None)


class TestRaisingAndEditingACab:
    def test_it_is_stored_returned_and_told_to_the_admins(self, client, db, org):
        made = raise_cab(client, org)
        assert (made["cab_type"], made["cab_trip"], made["cab_distance_km"]) == (
            "SUV", "OUTSTATION", 560,
        )
        assert made["booked_cab_type"] is None and made["cab_extended_days"] == 0
        assert made["cab_extension_status"] is None and made["can_extend"] is False

        told = notices(db, "REQUEST_SUBMITTED")
        assert told and "Ertiga (7 seats), outstation, about 560 km" in told[0].body
        # So is the manager asked for a recommendation.
        assert "Ertiga (7 seats)" in notices(db, "TEAM_REQUEST_SUBMITTED")[0].body

    def test_the_dry_run_check_holds_the_same_rules(self, client, org):
        body = cab_body(org, cab_distance_km=None)
        r = client.post("/requests/check", headers=auth(org["ravi"]), json=body)
        assert r.status_code == 422
        assert "approximate distance" in r.text

    def test_the_dry_run_check_does_not_wait_for_the_reason(self, client, org):
        body = cab_body(org)
        body.pop("travel_reason", None)
        r = client.post("/requests/check", headers=auth(org["ravi"]), json=body)
        assert r.status_code == 200, r.text

    def test_an_edit_is_a_revision_with_readable_labels(self, client, db, org):
        made = raise_cab(client, org)
        body = cab_body(org, cab_type="SEDAN", cab_trip="LOCAL", cab_distance_km=40)
        r = client.put(f"/requests/{made['id']}", headers=auth(org["ravi"]), json=body)
        assert r.status_code == 200, r.text
        assert (r.json()["cab_type"], r.json()["cab_trip"], r.json()["cab_distance_km"]) == (
            "SEDAN", "LOCAL", 40,
        )

        revisions = client.get(f"/requests/{made['id']}/revisions",
                               headers=auth(org["ravi"])).json()
        latest = revisions[0]
        assert latest["changes"]["cab_type"] == {"from": "SUV", "to": "SEDAN"}
        assert latest["changes"]["cab_trip"] == {"from": "OUTSTATION", "to": "LOCAL"}
        assert latest["changes"]["cab_distance_km"] == {"from": 560, "to": 40}
        assert "cab type" in latest["summary"]
        assert "distance (km)" in latest["summary"]

    def test_changing_a_cab_into_a_hotel_drops_its_cab_fields(self, client, org):
        made = raise_cab(client, org)
        body = cab_body(org, request_type="HOTEL", hotel_city="Tirupati",
                        hotel_state="Andhra Pradesh", check_in=str(DAY),
                        check_out=str(DAY + timedelta(days=1)))
        r = client.put(f"/requests/{made['id']}", headers=auth(org["ravi"]), json=body)
        assert r.status_code == 200, r.text
        assert (r.json()["cab_type"], r.json()["cab_trip"], r.json()["cab_distance_km"]) == (
            None, None, None,
        )


# ---------------------------------------------------------------------------
# The car that was sent
# ---------------------------------------------------------------------------


class TestWhoMayRecordTheCab:
    @pytest.mark.parametrize("who", ["ravi", "lead"])
    def test_only_an_admin(self, client, db, org, who):
        made = approved_cab(client, org)
        assert record_cab(client, org, made["id"], who=who).status_code == 403

    def test_any_admin_tier(self, client, org):
        made = approved_cab(client, org)
        assert record_cab(client, org, made["id"], who="owner").status_code == 200

    def test_only_on_a_cab(self, client, org):
        r = client.post("/requests", headers=auth(org["ravi"]), json=cab_body(
            org, request_type="HOTEL", hotel_city="Tirupati", hotel_state="Andhra Pradesh",
            check_in=str(DAY), check_out=str(DAY + timedelta(days=1)),
        ))
        hotel = r.json()
        decide(client, org, hotel["id"], hotel["travellers"][0]["id"])
        r = record_cab(client, org, hotel["id"])
        assert r.status_code == 400
        assert "cab request" in r.json()["detail"]

    def test_only_once_someone_is_approved(self, client, org):
        made = raise_cab(client, org)
        r = record_cab(client, org, made["id"])
        assert r.status_code == 409
        assert "Approve someone" in r.json()["detail"]

        decide(client, org, made["id"], made["travellers"][0]["id"], to="REJECTED",
               reason="Not this week")
        assert record_cab(client, org, made["id"]).status_code == 409

    def test_not_on_a_cancelled_request(self, client, org):
        made = approved_cab(client, org)
        client.post(f"/requests/{made['id']}/cancel", headers=auth(org["admin"]),
                    json={"reason": "Audit moved"})
        r = record_cab(client, org, made["id"])
        assert r.status_code == 409
        assert "cancelled" in r.json()["detail"]

    @pytest.mark.parametrize("bad", [
        {"booked_cab_type": "NO_PREFERENCE"},
        {"booked_cab_type": "LIMOUSINE"},
        {"vehicle_number": "--"},
        {"vehicle_number": "TS 09 EA 1234 5678 9012"},
        {"driver_name": "S"},
        {"driver_phone": "12345"},
        {"driver_phone": None},
        {"driver_phone": "   "},
    ])
    def test_the_details_are_checked(self, client, org, bad):
        made = approved_cab(client, org)
        assert record_cab(client, org, made["id"], **bad).status_code == 422


class TestRecordingTheCab:
    def test_it_is_stored_tidied_and_moves_no_status(self, client, db, org):
        made = approved_cab(client, org)
        r = record_cab(client, org, made["id"])
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["booked_cab_type"] == "SUV"
        assert got["cab_vehicle_number"] == "TS 09 EA 1234"
        assert got["cab_driver_name"] == "Suresh Reddy"
        assert got["cab_driver_phone"] == "9876543210"
        assert got["cab_booked_by_name"] == "Priya Shah" and got["cab_booked_at"]
        assert [t["status"] for t in got["travellers"]] == ["APPROVED"]

        entry = log(db, made["id"], "UPDATE")[-1]
        assert entry.summary == (
            f"Priya Shah recorded the cab for request {made['id']}: "
            "Ertiga (7 seats) TS 09 EA 1234, driver Suresh Reddy, 9876543210"
        )
        assert entry.changes["cab_vehicle_number"] == {"from": None, "to": "TS 09 EA 1234"}

    def test_everyone_riding_is_told_with_their_manager_copied(self, client, db, org):
        made = raise_cab(client, org, travellers=("ravi", "sana", "meena"))
        by_name = {t["full_name"]: t["id"] for t in made["travellers"]}
        decide(client, org, made["id"], by_name["Ravi Kumar"])
        decide(client, org, made["id"], by_name["Meena Iyer"])
        decide(client, org, made["id"], by_name["Sana Khan"], to="REJECTED", reason="Not needed")
        assert record_cab(client, org, made["id"]).status_code == 200

        told = notices(db, "CAB_DETAILS")
        assert {n.user_id for n in told} == {org["ravi"].id, org["meena"].id}
        assert told[0].title == "Your cab is arranged: Ertiga (7 seats) TS 09 EA 1234"
        assert "Driver Suresh Reddy, 9876543210" in told[0].body

        mail = {m.to_address: m for m in notices(db, "CAB_DETAILS",
                                                  channel=NotificationChannel.EMAIL)}
        assert set(mail) == {"ravi@designboxed.com", "meena@designboxed.com"}
        assert mail["ravi@designboxed.com"].cc_addresses == "anil@designboxed.com"
        assert mail["meena@designboxed.com"].cc_addresses == "divya@designboxed.com"
        assert "Vehicle      TS 09 EA 1234" in mail["ravi@designboxed.com"].body
        assert "Anil Mehta is copied on this email." in mail["ravi@designboxed.com"].body
        # The managers also get the in-app copy every decision gives them.
        copies = notices(db, "DECISION_COPY", user=org["lead"])
        assert any("TS 09 EA 1234" in c.body for c in copies)

    def test_the_travellers_see_it_and_still_no_cost(self, client, db, org):
        made = approved_cab(client, org)
        db.get(TravelRequest, made["id"]).travellers[0].cost_amount = 4200
        db.commit()
        record_cab(client, org, made["id"])
        got = client.get(f"/requests/{made['id']}", headers=auth(org["ravi"])).json()
        assert got["cab_vehicle_number"] == "TS 09 EA 1234"
        assert got["cab_driver_phone"] == "9876543210"
        assert got["travellers"][0]["cost_amount"] is None

    def test_a_swapped_cab_is_logged_with_what_it_replaced(self, client, db, org):
        made = approved_cab(client, org)
        record_cab(client, org, made["id"])
        r = record_cab(client, org, made["id"], booked_cab_type="SEDAN",
                       vehicle_number="TS 07 UB 5678")
        assert r.status_code == 200, r.text
        assert r.json()["booked_cab_type"] == "SEDAN"

        entry = log(db, made["id"], "UPDATE")[-1]
        assert entry.summary.startswith(f"Priya Shah changed the cab for request {made['id']}")
        assert entry.changes == {
            "booked_cab_type": {"from": "SUV", "to": "SEDAN"},
            "cab_vehicle_number": {"from": "TS 09 EA 1234", "to": "TS 07 UB 5678"},
        }
        told = notices(db, "CAB_DETAILS", user=org["ravi"])
        assert told[-1].title == "Your cab has changed: Dzire (4 seats) TS 07 UB 5678"

    def test_saving_the_same_details_again_changes_nothing(self, client, db, org):
        made = approved_cab(client, org)
        record_cab(client, org, made["id"])
        before = (len(log(db, made["id"], "UPDATE")), len(notices(db, "CAB_DETAILS")))
        assert record_cab(client, org, made["id"]).status_code == 200
        assert (len(log(db, made["id"], "UPDATE")), len(notices(db, "CAB_DETAILS"))) == before

    def test_recorded_while_booking_the_booking_notice_carries_it(self, client, db, org):
        made = approved_cab(client, org)
        r = record_cab(client, org, made["id"], notify=False)
        assert r.status_code == 200
        assert notices(db, "CAB_DETAILS") == []
        assert len(log(db, made["id"], "UPDATE")) == 1

        decide(client, org, made["id"], made["travellers"][0]["id"], to="BOOKED",
               reason="Vendor confirmed", booking_reference="TS 09 EA 1234")
        booked = notices(db, "REQUEST_BOOKED", user=org["ravi"])
        assert "Your cab: Ertiga (7 seats) TS 09 EA 1234, driver Suresh Reddy" in booked[0].body
        mail = notices(db, "REQUEST_BOOKED", channel=NotificationChannel.EMAIL)
        assert "Cab asked for: Ertiga (7 seats), outstation, about 560 km" in mail[0].body
        assert mail[0].cc_addresses == "anil@designboxed.com"

    def test_a_booked_traveller_can_have_their_cab_changed(self, client, db, org):
        made = approved_cab(client, org)
        decide(client, org, made["id"], made["travellers"][0]["id"], to="BOOKED",
               reason="Vendor confirmed", booking_reference="VND-88")
        assert record_cab(client, org, made["id"]).status_code == 200
        assert len(notices(db, "CAB_DETAILS", user=org["ravi"])) == 1


# ---------------------------------------------------------------------------
# One more day
# ---------------------------------------------------------------------------


class TestOlderAsksStillWaiting:
    def test_the_old_ask_endpoint_is_gone(self, client, org):
        made = approved_cab(client, org)
        r = client.post(f"/requests/{made['id']}/cab-extension", headers=auth(org["ravi"]),
                        json={"reason": "Audit runs into a second day"})
        assert r.status_code in (404, 405)

    def test_the_queue_counts_and_lists_them(self, client, db, org):
        made = approved_cab(client, org)
        mine = approved_cab(client, org, who="meena", travellers=("meena",))
        ask(db, org, made["id"])
        ask(db, org, mine["id"], who="meena")

        assert counts(client, org)["cab_extensions"] == 2
        assert counts(client, org, who="lead")["cab_extensions"] == 1
        assert counts(client, org, who="other_lead")["cab_extensions"] == 1
        listed = client.get("/requests", headers=auth(org["admin"]),
                            params={"mine": False, "extension": "pending"}).json()
        assert {r["id"] for r in listed["items"]} == {made["id"], mine["id"]}

        # A cancelled cab's ask is moot.
        client.post(f"/requests/{mine['id']}/cancel", headers=auth(org["admin"]),
                    json={"reason": "Audit moved"})
        assert counts(client, org)["cab_extensions"] == 1
        listed = client.get("/requests", headers=auth(org["admin"]),
                            params={"mine": False, "extension": "pending"}).json()
        assert [r["id"] for r in listed["items"]] == [made["id"]]


class TestDecidingAnotherDay:
    @pytest.mark.parametrize("who", ["ravi", "lead"])
    def test_only_an_admin(self, client, db, org, who):
        made = approved_cab(client, org)
        ask(db, org, made["id"])
        assert answer(client, org, made["id"], who=who).status_code == 403

    def test_only_when_one_is_waiting(self, client, org):
        made = approved_cab(client, org)
        r = answer(client, org, made["id"])
        assert r.status_code == 409
        assert "no extension waiting" in r.json()["detail"]

    @pytest.mark.parametrize("comment", [None, "", "  n "])
    def test_a_rejection_needs_a_comment(self, client, db, org, comment):
        made = approved_cab(client, org)
        ask(db, org, made["id"])
        assert answer(client, org, made["id"], approve=False, comment=comment).status_code == 422

    def test_approving_keeps_the_cab_a_day_longer(self, client, db, org):
        made = approved_cab(client, org)
        record_cab(client, org, made["id"])
        ask(db, org, made["id"])
        r = answer(client, org, made["id"], comment="Fine, vendor agreed")
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["end_at"].startswith((END + timedelta(days=1)).isoformat())
        assert got["cab_extended_days"] == 1
        assert got["cab_extension_status"] == "APPROVED"
        assert got["cab_extension_comment"] == "Fine, vendor agreed"
        assert got["cab_extension_decided_by_name"] == "Priya Shah"

        entry = log(db, made["id"], "APPROVE")[-1]
        assert "approved Ravi Kumar's ask to extend cab request" in entry.summary
        assert entry.changes["cab_extended_days"] == {"from": 0, "to": 1}
        assert entry.changes["end_at"]["to"].startswith((END + timedelta(days=1)).isoformat())
        assert counts(client, org)["cab_extensions"] == 0

    def test_the_travellers_are_told_with_their_manager_copied(self, client, db, org):
        made = approved_cab(client, org)
        record_cab(client, org, made["id"])
        ask(db, org, made["id"])
        answer(client, org, made["id"])

        told = notices(db, "CAB_EXTENSION_APPROVED", user=org["ravi"])
        assert len(told) == 1 and "kept one more day" in told[0].body
        mail = notices(db, "CAB_EXTENSION_APPROVED", channel=NotificationChannel.EMAIL)
        assert [m.to_address for m in mail] == ["ravi@designboxed.com"]
        assert mail[0].cc_addresses == "anil@designboxed.com"
        assert "Vehicle      TS 09 EA 1234" in mail[0].body
        assert any("one more day approved" in c.body
                   for c in notices(db, "DECISION_COPY", user=org["lead"]))

    def test_rejecting_leaves_the_booking_alone(self, client, db, org):
        made = approved_cab(client, org)
        ask(db, org, made["id"])
        r = answer(client, org, made["id"], approve=False, comment="Vendor has no car free")
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["cab_extension_status"] == "REJECTED"
        assert got["cab_extension_comment"] == "Vendor has no car free"
        assert got["end_at"].startswith(END.isoformat()) and got["cab_extended_days"] == 0

        assert log(db, made["id"], "REJECT")[-1].reason == "Vendor has no car free"
        told = notices(db, "CAB_EXTENSION_REJECTED", user=org["ravi"])
        assert "Comment: Vendor has no car free" in told[0].body

    def test_after_a_decision_they_may_ask_for_another_day(self, client, db, org):
        made = approved_cab(client, org)
        ask(db, org, made["id"])
        answer(client, org, made["id"], approve=False, comment="Not today")

        ask(db, org, made["id"])
        answer(client, org, made["id"])
        ask(db, org, made["id"], reason="Still more stores")
        got = answer(client, org, made["id"]).json()
        assert got["cab_extended_days"] == 2
        assert got["end_at"].startswith((END + timedelta(days=2)).isoformat())
        row = db.get(TravelRequest, made["id"])
        assert row.cab_extension_status is CabExtensionStatus.APPROVED

    def test_whoever_asked_hears_the_answer(self, client, db, org):
        made = approved_cab(client, org, travellers=("ravi", "sana"))
        ask(db, org, made["id"], who="sana")
        answer(client, org, made["id"])
        assert {n.user_id for n in notices(db, "CAB_EXTENSION_APPROVED")} == {
            org["ravi"].id, org["sana"].id,
        }

    def test_a_cancelled_cab_cannot_be_extended(self, client, db, org):
        made = approved_cab(client, org)
        ask(db, org, made["id"])
        client.post(f"/requests/{made['id']}/cancel", headers=auth(org["admin"]),
                    json={"reason": "Audit moved"})
        assert answer(client, org, made["id"]).status_code == 409

    def test_the_longer_booking_takes_part_in_conflict_detection(self, client, db, org):
        made = approved_cab(client, org)
        next_day = START + timedelta(days=1, hours=4)
        later = dict(
            origin="Tirupati bus stand", destination="Kalahasti temple",
            drop_city="Srikalahasti", start_at=next_day.isoformat(),
            end_at=(next_day + timedelta(hours=3)).isoformat(),
            cab_trip="LOCAL", cab_distance_km=40,
        )
        check = cab_body(org, **later)
        clashes = client.post("/requests/check", headers=auth(org["ravi"]), json=check).json()
        assert clashes["conflicts"] == []

        ask(db, org, made["id"])
        answer(client, org, made["id"])
        clashes = client.post("/requests/check", headers=auth(org["ravi"]), json=check).json()
        assert [c["other_request_id"] for c in clashes["conflicts"]] == [made["id"]]


def test_the_traveller_status_is_untouched_by_the_cab_flow(client, db, org):
    made = approved_cab(client, org)
    record_cab(client, org, made["id"])
    ask(db, org, made["id"])
    answer(client, org, made["id"])
    row = db.get(TravelRequest, made["id"])
    assert [t.status for t in row.travellers] == [TravellerStatus.APPROVED]


def test_the_travel_reminder_names_the_car_and_driver(client, db, org):
    from app.services import reminders

    made = approved_cab(client, org)
    record_cab(client, org, made["id"], notify=False)
    decide(client, org, made["id"], made["travellers"][0]["id"], to="BOOKED",
           reason="Vendor confirmed", booking_reference="VND-88")
    reminders.remind_travellers(db, TENANT, today=DAY - timedelta(days=1))
    told = notices(db, "TRAVEL_REMINDER", user=org["ravi"])
    assert "Cab: Ertiga (7 seats) TS 09 EA 1234, driver Suresh Reddy" in told[0].body
    mail = notices(db, "TRAVEL_REMINDER", channel=NotificationChannel.EMAIL)
    assert "  Cab          Ertiga (7 seats) TS 09 EA 1234" in mail[0].body
