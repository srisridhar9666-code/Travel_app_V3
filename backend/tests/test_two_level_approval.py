"""
Two-level approval: the traveller's manager recommends, an admin decides.

* Raising a request asks each traveller's manager for a recommendation. The
  manager gives one - recommended or not, always with a comment - for their own
  team members only, and may change it while the traveller is still pending.
* Admins hear about it and see it on the queue; nobody else reads the comment.
  The admin is the final authority and is never made to wait for a manager.
* The decision email goes to the traveller with their manager in Cc, quoting
  the admin's reason and the manager's recommendation. The manager also gets an
  in-app copy, but no second email.
"""
from dataclasses import dataclass
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.core import clock, ratelimit
from app.core.enums import (
    Gender,
    ManagerRecommendation,
    NotificationChannel,
    RequestType,
    Role,
    TravellerStatus,
    UserStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import email, notifications

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
    """Who someone is, without the ORM row. The request endpoints close the
    session they were handed - the test's own - which detaches every row in
    it, so the fixture keeps plain values; `db.get` fetches a live row."""

    id: int
    role: Role
    email: str


@pytest.fixture
def org(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
    db.add(project)
    db.commit()
    owner = person(db, "Sridhar Rao", "sridhar@designboxed.com", Role.SUPER_ADMIN)
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    lead = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER)
    other_lead = person(db, "Divya Nair", "divya@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=lead.id)
    sana = person(db, "Sana Khan", "sana@designboxed.com", manager_id=lead.id)
    meena = person(db, "Meena Iyer", "meena@designboxed.com", manager_id=other_lead.id)
    loner = person(db, "Kiran Das", "kiran@designboxed.com")
    people = dict(owner=owner, admin=admin, lead=lead, other_lead=other_lead,
                  ravi=ravi, sana=sana, meena=meena, loner=loner)
    refs = {key: Ref(u.id, u.role, u.email) for key, u in people.items()}
    return dict(refs, project=Ref(project.id, Role.GROUND_STAFF, ""))


def switch_off(db, ref, status=UserStatus.DEACTIVATED):
    db.get(User, ref.id).status = status
    db.commit()


def raise_hotel(client, org, who="ravi", travellers=None, **overrides):
    """POST a hotel request as `who`, for `travellers` (names in org)."""
    day = clock.local_today() + timedelta(days=10)
    body = dict(
        request_type="HOTEL",
        project_id=org["project"].id,
        hotel_city="Pune",
        hotel_state="Maharashtra",
        check_in=str(day),
        check_out=str(day + timedelta(days=2)),
        travel_reason="Store audit in Pune",
        traveller_ids=[org[t].id for t in (travellers or [who])],
    )
    body.update(overrides)
    r = client.post("/requests", headers=auth(org[who]), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def recommend(client, org, request_id, who="lead", verdict="RECOMMENDED",
              comment="Needed for the Pune store audit", **extra):
    return client.post(
        f"/requests/{request_id}/recommendation",
        headers=auth(org[who]),
        json={"recommendation": verdict, "comment": comment, **extra},
    )


def decide(client, org, request_id, traveller_id, to="APPROVED", reason="Client walkthrough"):
    return client.post(
        f"/requests/{request_id}/decide",
        headers=auth(org["admin"]),
        json={"decisions": [{"traveller_id": traveller_id, "to_status": to, "reason": reason}]},
    )


def notices(db, kind, *, user=None, channel=NotificationChannel.IN_APP):
    query = select(Notification).where(Notification.kind == kind, Notification.channel == channel)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return db.execute(query).scalars().all()


# ---------------------------------------------------------------------------
# Level one: the manager is asked, and answers
# ---------------------------------------------------------------------------


class TestTheManagerIsAsked:
    def test_raising_a_request_asks_the_travellers_manager(self, client, db, org):
        made = raise_hotel(client, org)
        told = notices(db, "TEAM_REQUEST_SUBMITTED")
        assert [n.user_id for n in told] == [org["lead"].id]
        assert told[0].title == "Ravi Kumar's request needs your recommendation"
        assert told[0].request_id == made["id"]

        mail = notices(db, "TEAM_REQUEST_SUBMITTED", channel=NotificationChannel.EMAIL)
        assert [m.to_address for m in mail] == ["anil@designboxed.com"]
        assert mail[0].body.rstrip().endswith("/team-approvals")
        # The admins are still told, as before.
        assert {n.user_id for n in notices(db, "REQUEST_SUBMITTED")} == {
            org["owner"].id, org["admin"].id,
        }

    def test_each_manager_hears_only_about_their_own_people(self, client, db, org):
        raise_hotel(client, org, travellers=["ravi", "meena", "loner"])
        told = {n.user_id: n for n in notices(db, "TEAM_REQUEST_SUBMITTED")}
        assert set(told) == {org["lead"].id, org["other_lead"].id}
        assert "Meena Iyer" in told[org["other_lead"].id].body
        assert "Ravi" not in told[org["other_lead"].id].body
        assert told[org["other_lead"].id].title == (
            "A request for Meena Iyer needs your recommendation"
        )

    def test_a_manager_is_not_told_about_a_request_they_raised(self, client, db, org):
        raise_hotel(client, org, who="lead", travellers=["ravi"])
        assert notices(db, "TEAM_REQUEST_SUBMITTED") == []

    def test_a_switched_off_manager_is_not_asked(self, client, db, org):
        switch_off(db, org["lead"])
        made = raise_hotel(client, org)
        assert notices(db, "TEAM_REQUEST_SUBMITTED") == []
        # Nobody is left to recommend, so nothing says it waits on them.
        traveller = made["travellers"][0]
        assert traveller["manager_id"] is None and traveller["manager_name"] is None

    def test_a_draft_asks_nobody_until_it_is_submitted(self, client, db, org):
        made = raise_hotel(client, org, is_draft=True)
        assert notices(db, "TEAM_REQUEST_SUBMITTED") == []
        r = client.post(f"/requests/{made['id']}/submit", headers=auth(org["ravi"]))
        assert r.status_code == 200, r.text
        assert len(notices(db, "TEAM_REQUEST_SUBMITTED")) == 1


class TestWhoMayRecommend:
    def test_only_the_travellers_manager(self, client, org):
        made = raise_hotel(client, org)
        assert recommend(client, org, made["id"], who="ravi").status_code == 403
        # Admins may call the endpoint role-wise, though only a direct manager has pending team members.
        assert recommend(client, org, made["id"], who="admin").status_code == 409
        # Another manager cannot even see the request.
        assert recommend(client, org, made["id"], who="other_lead").status_code == 404
        assert recommend(client, org, made["id"], who="lead").status_code == 200

    def test_a_request_with_none_of_their_team_still_pending_is_refused(self, client, org):
        made = raise_hotel(client, org)
        decide(client, org, made["id"], made["travellers"][0]["id"], to="REJECTED", reason="Budget closed")
        r = recommend(client, org, made["id"])
        assert r.status_code == 409
        assert "already decided" in r.json()["detail"]

    def test_a_cancelled_request_is_refused(self, client, org):
        made = raise_hotel(client, org)
        client.post(f"/requests/{made['id']}/cancel", headers=auth(org["ravi"]),
                    json={"reason": "Trip called off"})
        r = recommend(client, org, made["id"])
        assert r.status_code == 409
        assert "cancelled" in r.json()["detail"]

    def test_someone_elses_draft_does_not_exist(self, client, org):
        made = raise_hotel(client, org, is_draft=True)
        assert recommend(client, org, made["id"]).status_code == 404

    @pytest.mark.parametrize("body", [
        {"recommendation": "RECOMMENDED", "comment": ""},
        {"recommendation": "RECOMMENDED", "comment": "  ok  "},
        {"recommendation": "RECOMMENDED"},
        {"recommendation": "MAYBE", "comment": "Not sure about this"},
        {"recommendation": "RECOMMENDED", "comment": "x" * 501},
    ])
    def test_a_verdict_and_a_real_comment_are_required(self, client, org, body):
        made = raise_hotel(client, org)
        r = client.post(f"/requests/{made['id']}/recommendation",
                        headers=auth(org["lead"]), json=body)
        assert r.status_code == 422

    def test_named_travellers_must_be_their_own_and_still_pending(self, client, org):
        made = raise_hotel(client, org, travellers=["ravi", "sana", "meena"])
        by_name = {t["full_name"]: t["id"] for t in made["travellers"]}

        r = recommend(client, org, made["id"], traveller_ids=[by_name["Meena Iyer"]])
        assert r.status_code == 404
        assert "your own team" in r.json()["detail"]
        assert recommend(client, org, made["id"], traveller_ids=[999999]).status_code == 404
        assert recommend(client, org, made["id"], traveller_ids=[]).status_code == 422

        decide(client, org, made["id"], by_name["Sana Khan"], to="REJECTED", reason="Not needed")
        r = recommend(client, org, made["id"], traveller_ids=[by_name["Sana Khan"]])
        assert r.status_code == 409
        assert "Sana Khan" in r.json()["detail"]


class TestRecommending:
    def test_it_is_stored_logged_and_sent_to_the_admins(self, client, db, org):
        made = raise_hotel(client, org)
        r = recommend(client, org, made["id"], comment="  Needed   for the audit ")
        assert r.status_code == 200, r.text
        traveller = r.json()["travellers"][0]
        assert traveller["manager_recommendation"] == "RECOMMENDED"
        assert traveller["manager_comment"] == "Needed for the audit"
        assert traveller["manager_reviewed_by_name"] == "Anil Mehta"
        assert traveller["manager_reviewed_at"]
        # Advice, not a decision: the request is still waiting on an admin.
        assert traveller["status"] == "PENDING"
        assert r.json()["status"] == "SUBMITTED"

        log = db.execute(select(AuditLog).where(
            AuditLog.action == "RECOMMEND", AuditLog.entity_id == traveller["id"]
        )).scalars().all()
        assert len(log) == 1
        assert log[0].reason == "Needed for the audit"
        assert log[0].summary == f"Anil Mehta recommended Ravi Kumar on request {made['id']}"

        told = notices(db, "MANAGER_RECOMMENDED")
        assert {n.user_id for n in told} == {org["owner"].id, org["admin"].id}
        assert "Recommended - Needed for the audit" in told[0].body
        mail = notices(db, "MANAGER_RECOMMENDED", channel=NotificationChannel.EMAIL)
        # Only operational admins get the email (Super Admin does not receive recommendation emails).
        assert len(mail) == 1
        assert mail[0].to_address == org["admin"].email
        assert "Their comment: Needed for the audit" in mail[0].body
        assert mail[0].body.rstrip().endswith("/approvals")

    def test_it_can_be_changed_while_still_pending(self, client, db, org):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"])
        r = recommend(client, org, made["id"], verdict="NOT_RECOMMENDED",
                      comment="Sana is already covering Pune")
        assert r.status_code == 200, r.text
        traveller = r.json()["travellers"][0]
        assert traveller["manager_recommendation"] == "NOT_RECOMMENDED"
        assert traveller["manager_comment"] == "Sana is already covering Pune"

        log = db.execute(select(AuditLog).where(
            AuditLog.action == "RECOMMEND", AuditLog.entity_id == traveller["id"]
        ).order_by(AuditLog.id)).scalars().all()
        assert len(log) == 2
        assert "did not recommend" in log[1].summary
        assert "changed their recommendation" in log[1].summary
        assert log[1].changes["recommendation"] == {
            "from": "RECOMMENDED", "to": "NOT_RECOMMENDED",
        }

    def test_on_a_group_request_it_covers_only_their_team(self, client, db, org):
        made = raise_hotel(client, org, travellers=["ravi", "sana", "meena"])
        by_name = {t["full_name"]: t["id"] for t in made["travellers"]}

        r = recommend(client, org, made["id"], traveller_ids=[by_name["Sana Khan"]])
        assert r.status_code == 200, r.text
        rows = {t.user_id: t for t in db.get(TravelRequest, made["id"]).travellers}
        assert rows[org["sana"].id].manager_recommendation is ManagerRecommendation.RECOMMENDED
        assert rows[org["ravi"].id].manager_recommendation is None

        # Without names it covers every one of their people still pending -
        # never someone else's.
        r = recommend(client, org, made["id"], verdict="NOT_RECOMMENDED", comment="Too many")
        assert r.status_code == 200
        db.expire_all()
        rows = {t.user_id: t for t in db.get(TravelRequest, made["id"]).travellers}
        assert rows[org["ravi"].id].manager_recommendation is ManagerRecommendation.NOT_RECOMMENDED
        assert rows[org["sana"].id].manager_recommendation is ManagerRecommendation.NOT_RECOMMENDED
        assert rows[org["meena"].id].manager_recommendation is None

    def test_an_edit_clears_it_and_asks_again(self, client, db, org):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"])
        day = clock.local_today() + timedelta(days=20)
        r = client.put(f"/requests/{made['id']}", headers=auth(org["ravi"]), json=dict(
            request_type="HOTEL", project_id=org["project"].id, hotel_city="Pune",
            hotel_state="Maharashtra", check_in=str(day), check_out=str(day + timedelta(days=1)),
            travel_reason="Store audit in Pune", traveller_ids=[org["ravi"].id],
        ))
        assert r.status_code == 200, r.text
        assert r.json()["travellers"][0]["manager_recommendation"] is None

        asked = notices(db, "TEAM_REQUEST_SUBMITTED", user=org["lead"])
        assert [n.title for n in asked] == [
            "Ravi Kumar's request needs your recommendation",
            "Ravi Kumar's request needs your recommendation again",
        ]
        edit = db.execute(select(AuditLog).where(
            AuditLog.entity_type == "travel_request", AuditLog.action == "UPDATE"
        )).scalars().one()
        assert "recommendation was cleared" in edit.summary


# ---------------------------------------------------------------------------
# Who reads what
# ---------------------------------------------------------------------------


class TestWhoSeesTheRecommendation:
    def test_admins_and_the_manager_see_it_the_traveller_does_not(self, client, org):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"], comment="Private view for the admin")

        for who in ("admin", "lead"):
            r = client.get(f"/requests/{made['id']}", headers=auth(org[who]))
            traveller = r.json()["travellers"][0]
            assert traveller["manager_comment"] == "Private view for the admin", who
            assert traveller["manager_name"] == "Anil Mehta"

        r = client.get(f"/requests/{made['id']}", headers=auth(org["ravi"]))
        traveller = r.json()["travellers"][0]
        assert traveller["manager_name"] == "Anil Mehta"
        assert traveller["manager_recommendation"] is None
        assert traveller["manager_comment"] is None
        assert traveller["manager_reviewed_by_name"] is None

    def test_a_colleague_on_a_group_request_does_not_see_it(self, client, org):
        made = raise_hotel(client, org, who="meena", travellers=["meena", "ravi"])
        recommend(client, org, made["id"], comment="Ravi is needed there")
        r = client.get(f"/requests/{made['id']}", headers=auth(org["meena"]))
        ravi = next(t for t in r.json()["travellers"] if t["full_name"] == "Ravi Kumar")
        assert ravi["manager_comment"] is None

    def test_the_admin_queue_list_carries_it_and_no_costs_for_the_manager(self, client, db, org):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"])
        row = db.get(TravelRequest, made["id"]).travellers[0]
        row.cost_amount = 4200
        db.commit()

        admin_list = client.get("/requests", headers=auth(org["admin"]),
                                params={"mine": "false"}).json()["items"]
        assert admin_list[0]["travellers"][0]["manager_recommendation"] == "RECOMMENDED"

        team = client.get("/requests", headers=auth(org["lead"]),
                          params={"mine": "false"}).json()["items"]
        assert team[0]["travellers"][0]["manager_recommendation"] == "RECOMMENDED"
        assert team[0]["travellers"][0]["cost_amount"] is None


class TestTheQueue:
    def test_waiting_and_reviewed_lists_for_a_manager(self, client, org):
        first = raise_hotel(client, org)
        second = raise_hotel(client, org, who="sana",
                             check_in=str(clock.local_today() + timedelta(days=30)),
                             check_out=str(clock.local_today() + timedelta(days=31)))
        raise_hotel(client, org, who="meena")

        def ids(who, review):
            r = client.get("/requests", headers=auth(org[who]),
                           params={"mine": "false", "review": review})
            assert r.status_code == 200, r.text
            return sorted(i["id"] for i in r.json()["items"])

        assert ids("lead", "waiting") == sorted([first["id"], second["id"]])
        assert ids("lead", "reviewed") == []

        recommend(client, org, first["id"])
        assert ids("lead", "waiting") == [second["id"]]
        assert ids("lead", "reviewed") == [first["id"]]
        # Divya's list is her own team's.
        assert len(ids("other_lead", "waiting")) == 1
        assert ids("other_lead", "reviewed") == []
        assert client.get("/requests", headers=auth(org["lead"]),
                          params={"mine": "false", "review": "maybe"}).status_code == 422

    def test_a_decided_request_leaves_the_waiting_list(self, client, org):
        made = raise_hotel(client, org)
        decide(client, org, made["id"], made["travellers"][0]["id"], to="REJECTED",
               reason="Budget is closed")
        r = client.get("/requests", headers=auth(org["lead"]),
                       params={"mine": "false", "review": "waiting"})
        assert r.json()["items"] == []

    def test_counts_say_how_many_still_wait_on_a_manager(self, client, org):
        first = raise_hotel(client, org)
        raise_hotel(client, org, who="meena")
        raise_hotel(client, org, who="loner")   # no manager to wait on

        def waiting(who):
            return client.get("/requests/queue/counts",
                              headers=auth(org[who])).json()["awaiting_manager"]

        assert waiting("admin") == 2
        assert waiting("lead") == 1
        recommend(client, org, first["id"])
        assert waiting("admin") == 1
        assert waiting("lead") == 0

        r = client.get("/requests", headers=auth(org["admin"]),
                       params={"mine": "false", "review": "waiting"})
        assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# Level two: the admin decides, the manager is copied
# ---------------------------------------------------------------------------


class TestTheDecision:
    def test_the_admin_decides_and_the_manager_is_copied(self, client, db, org, outbox):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"], comment="Needed for the audit")
        outbox.clear()

        r = decide(client, org, made["id"], made["travellers"][0]["id"],
                   reason="Client walkthrough on the 14th")
        assert r.status_code == 200, r.text

        mail = notices(db, "REQUEST_APPROVED", user=org["ravi"],
                       channel=NotificationChannel.EMAIL)
        assert len(mail) == 1
        assert mail[0].cc_addresses == "anil@designboxed.com"
        assert "Reason: Client walkthrough on the 14th" in mail[0].body
        assert "Your manager's recommendation: Recommended - Needed for the audit" in mail[0].body

        sent = [m for m in outbox.messages if m["to"] == "ravi@designboxed.com"]
        assert len(sent) == 1
        assert sent[0]["cc"] == ["anil@designboxed.com"]

        # The manager has an in-app copy and no second email of their own.
        copy = notices(db, "DECISION_COPY", user=org["lead"])
        assert len(copy) == 1
        assert copy[0].title == "Ravi Kumar's request was approved"
        assert "by Priya Shah" in copy[0].body
        assert notices(db, "DECISION_COPY", channel=NotificationChannel.EMAIL) == []
        assert not [m for m in outbox.messages if m["to"] == "anil@designboxed.com"]

    def test_the_admin_never_waits_for_the_manager(self, client, db, org):
        made = raise_hotel(client, org)
        r = decide(client, org, made["id"], made["travellers"][0]["id"], to="REJECTED",
                   reason="Budget is closed")
        assert r.status_code == 200, r.text
        assert r.json()["travellers"][0]["status"] == "REJECTED"

        mail = notices(db, "REQUEST_REJECTED", user=org["ravi"],
                       channel=NotificationChannel.EMAIL)[0]
        assert mail.cc_addresses == "anil@designboxed.com"
        assert "manager's recommendation" not in mail.body

    def test_nobody_is_copied_without_an_active_manager(self, client, db, org):
        made = raise_hotel(client, org, who="loner")
        decide(client, org, made["id"], made["travellers"][0]["id"])
        mail = notices(db, "REQUEST_APPROVED", channel=NotificationChannel.EMAIL)[0]
        assert mail.cc_addresses is None
        assert notices(db, "DECISION_COPY") == []

        made = raise_hotel(client, org)
        switch_off(db, org["lead"], UserStatus.LEFT)
        decide(client, org, made["id"], made["travellers"][0]["id"])
        mail = notices(db, "REQUEST_APPROVED", user=org["ravi"],
                       channel=NotificationChannel.EMAIL)[0]
        assert mail.cc_addresses is None
        assert notices(db, "DECISION_COPY") == []

    def test_the_ledger_shows_who_was_copied(self, client, org):
        made = raise_hotel(client, org)
        recommend(client, org, made["id"])
        decide(client, org, made["id"], made["travellers"][0]["id"])
        rows = client.get("/notifications/ledger", headers=auth(org["admin"]),
                          params={"channel": "EMAIL", "search": "approved"}).json()["items"]
        assert [r["cc_addresses"] for r in rows] == ["anil@designboxed.com"]


# ---------------------------------------------------------------------------
# Preferences and the mail transport
# ---------------------------------------------------------------------------


class TestPreferences:
    def test_managers_may_switch_new_requests_off_ground_staff_are_not_offered_it(
        self, client, db, org
    ):
        offered = client.get("/notifications/preferences", headers=auth(org["lead"])).json()
        assert "NEW_REQUESTS" in offered["email"]
        offered = client.get("/notifications/preferences", headers=auth(org["ravi"])).json()
        assert "NEW_REQUESTS" not in offered["email"]

        r = client.patch("/notifications/preferences", headers=auth(org["lead"]),
                         json={"category": "NEW_REQUESTS", "enabled": False})
        assert r.status_code == 200, r.text
        raise_hotel(client, org)
        assert len(notices(db, "TEAM_REQUEST_SUBMITTED", user=org["lead"])) == 1
        assert notices(db, "TEAM_REQUEST_SUBMITTED", channel=NotificationChannel.EMAIL) == []


class FakeSMTP:
    sent: list = []

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FakeSMTP.sent.append(message)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestTheCcLine:
    @pytest.fixture
    def wire(self, monkeypatch):
        FakeSMTP.sent = []
        monkeypatch.setattr(email, "_outbox", None)
        monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)

        def configure(**overrides):
            settings = Settings(**{
                "email_enabled": True, "email_allowlist": "", "smtp_port": 587,
                "smtp_username": "ops@example.com", "smtp_app_password": "secret",
                "email_from": "ops@example.com", **overrides,
            })
            monkeypatch.setattr(email, "get_settings", lambda: settings)

        return configure

    def test_copies_go_on_the_cc_header(self, wire):
        wire()
        sent = email.send("ravi@example.com", "s", "b",
                          cc=["anil@example.com", "ravi@example.com", "not-an-address"])
        assert sent.ok
        assert FakeSMTP.sent[-1]["Cc"] == "anil@example.com"

    def test_each_copy_obeys_the_allowlist(self, wire):
        wire(email_allowlist="ravi@example.com")
        assert email.send("ravi@example.com", "s", "b", cc=["anil@example.com"]).ok
        assert FakeSMTP.sent[-1]["Cc"] is None

        wire(email_allowlist="anil@example.com")
        held = email.send("ravi@example.com", "s", "b", cc=["anil@example.com"])
        assert held.suppressed
        assert FakeSMTP.sent == [FakeSMTP.sent[0]]

    def test_switched_off_mail_copies_nobody(self, wire):
        wire(email_enabled=False)
        assert email.send("ravi@example.com", "s", "b", cc=["anil@example.com"]).suppressed
        assert FakeSMTP.sent == []

    def test_only_active_people_with_an_address_are_copied(self, db, org):
        switch_off(db, org["sana"])
        ravi, lead, sana = (db.get(User, org[k].id) for k in ("ravi", "lead", "sana"))
        rows = notifications.notify(
            db, tenant_id=TENANT, user=ravi, kind="REQUEST_APPROVED",
            title="t", body="b", cc_users=[lead, sana, ravi, lead], deliver_now=False,
        )
        mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
        assert mail.cc_addresses == "anil@designboxed.com"
        assert notifications.cc_list(mail) == ["anil@designboxed.com"]


def test_a_traveller_row_reports_whether_it_awaits_its_manager(db, org):
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=org["project"].id,
        requester_id=org["ravi"].id, hotel_city="Pune", check_in=clock.local_today(),
        travel_reason="Visit", submitted_at=naive_utcnow(),
    )
    row.travellers = [
        RequestTraveller(user_id=org["ravi"].id),
        RequestTraveller(user_id=org["loner"].id),
        RequestTraveller(user_id=org["sana"].id, status=TravellerStatus.APPROVED),
    ]
    db.add(row)
    db.commit()
    ravi, loner, sana = row.travellers
    assert ravi.awaits_manager is True
    assert loner.awaits_manager is False     # nobody to ask
    assert sana.awaits_manager is False      # already decided
    ravi.manager_recommendation = ManagerRecommendation.RECOMMENDED
    assert ravi.awaits_manager is False
