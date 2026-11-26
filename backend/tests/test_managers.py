"""
Managers, the super admin, and the team changes managers ask admins to make.

* Roles are ranked: ground staff < manager < admin < system admin < super admin.
  Nobody grants or manages a role above their own.
* Ground staff can report to one manager - one level, nobody else reports to
  anyone - and a manager who stops managing releases their team.
* A manager sees their team's people and trips, never a cost, and asks an admin
  to add, edit or remove members. Nothing changes until an admin approves; the
  manager is told the decision, with the admin's comment, either way.
"""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock, ratelimit
from app.core.enums import (
    Gender,
    RequestType,
    Role,
    TeamChangeStatus,
    TravellerStatus,
    UserStatus,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.department import Department
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.team_change import TeamChange
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


def person(db, name, email, role=Role.GROUND_STAFF, **extra):
    user = User(tenant_id=TENANT, email=email, full_name=name, role=role,
                gender=Gender.MALE, password_hash="x", **extra)
    db.add(user)
    db.commit()
    return user


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def org(db):
    field_ops = Department(tenant_id=TENANT, name="Field Operations")
    db.add(field_ops)
    db.commit()
    owner = person(db, "Sridhar Rao", "sridhar@designboxed.com", Role.SUPER_ADMIN)
    sysadmin = person(db, "Kiran Das", "kiran@designboxed.com", Role.SYSTEM_ADMIN)
    admin = person(db, "Priya Shah", "priya@designboxed.com", Role.ADMIN)
    lead = person(db, "Anil Mehta", "anil@designboxed.com", Role.MANAGER,
                  department_id=field_ops.id)
    other_lead = person(db, "Divya Nair", "divya@designboxed.com", Role.MANAGER)
    ravi = person(db, "Ravi Kumar", "ravi@designboxed.com", manager_id=lead.id)
    meena = person(db, "Meena Iyer", "meena@designboxed.com", manager_id=other_lead.id)
    return dict(owner=owner, sysadmin=sysadmin, admin=admin, lead=lead,
                other_lead=other_lead, ravi=ravi, meena=meena, dept=field_ops)


def invite_body(**overrides):
    body = dict(email="new.person@designboxed.com", full_name="New Person",
                gender="FEMALE", send_email=False)
    body.update(overrides)
    return body


class TestRoleRanks:
    @pytest.mark.parametrize("granter, role, allowed", [
        ("admin", "MANAGER", True),
        ("admin", "ADMIN", True),
        ("admin", "SUPER_ADMIN", False),
        ("sysadmin", "SUPER_ADMIN", True),  # Super Admin and System Admin both are same
        ("owner", "SUPER_ADMIN", True),
    ])
    def test_nobody_grants_a_role_above_their_own(self, client, org, granter, role, allowed):
        r = client.post("/users", headers=auth(org[granter]), json=invite_body(role=role))
        assert (r.status_code == 201) is allowed, r.text

    @pytest.mark.parametrize("who", ["admin", "owner"])
    def test_system_admin_is_no_longer_granted(self, client, org, who):
        r = client.post("/users", headers=auth(org[who]), json=invite_body(role="SYSTEM_ADMIN"))
        assert r.status_code == 422
        assert "no longer a separate role" in r.json()["detail"]

    def test_an_admin_cannot_switch_off_the_super_admin(self, client, org):
        r = client.post(f"/users/{org['owner'].id}/status", headers=auth(org["admin"]),
                        json={"status": "DEACTIVATED"})
        assert r.status_code == 403

    def test_every_admin_reads_the_activity_log(self, client, org):
        for who in ("admin", "sysadmin", "owner"):
            assert client.get("/audit", headers=auth(org[who])).status_code == 200
        assert client.get("/audit", headers=auth(org["lead"])).status_code == 403
        assert client.get("/audit", headers=auth(org["ravi"])).status_code == 403


class TestReportsTo:
    def test_ground_staff_can_be_invited_reporting_to_a_manager(self, client, db, org):
        r = client.post("/users", headers=auth(org["admin"]),
                        json=invite_body(manager_id=org["lead"].id))
        assert r.status_code == 201, r.text
        created = db.execute(
            select(User).where(User.email == "new.person@designboxed.com")
        ).scalar_one()
        assert created.manager_id == org["lead"].id
        assert created.manager_name == "Anil Mehta"

    @pytest.mark.parametrize("body", [
        dict(manager_id="admin"),                     # not a manager
        dict(manager_id="lead", role="ADMIN"),        # only ground staff report
    ])
    def test_a_bad_manager_pick_is_refused(self, client, org, body):
        body = {k: org[v].id if k == "manager_id" else v for k, v in body.items()}
        r = client.post("/users", headers=auth(org["admin"]), json=invite_body(**body))
        assert r.status_code == 422

    def test_promoting_a_member_clears_who_they_report_to(self, client, db, org):
        r = client.patch(f"/users/{org['ravi'].id}", headers=auth(org["admin"]),
                         json={"role": "ADMIN"})
        assert r.status_code == 200, r.text
        assert r.json()["manager_id"] is None

    def test_a_manager_who_stops_managing_releases_their_team(self, client, db, org):
        r = client.patch(f"/users/{org['lead'].id}", headers=auth(org["admin"]),
                         json={"role": "GROUND_STAFF"})
        assert r.status_code == 200, r.text
        db.refresh(org["ravi"])
        assert org["ravi"].manager_id is None

    def test_an_invite_refuses_a_number_someone_has(self, client, db, org):
        org["ravi"].phone = "9876543210"
        db.commit()
        r = client.post("/users", headers=auth(org["admin"]),
                        json=invite_body(phone="+91 98765 43210"))
        assert r.status_code == 409
        assert "Ravi Kumar" in r.json()["detail"]
        r = client.post("/users", headers=auth(org["admin"]), json=invite_body(phone="91234 56780"))
        assert r.status_code == 201, r.text

    def test_the_list_filters_by_manager(self, client, org):
        r = client.get("/users", headers=auth(org["admin"]),
                       params={"manager_id": org["lead"].id})
        assert [u["full_name"] for u in r.json()["items"]] == ["Ravi Kumar"]


class TestTeamChanges:
    def ask_add(self, client, org, **overrides):
        body = dict(email="sana@designboxed.com", full_name="Sana Khan", gender="FEMALE",
                    note="Joining the Pune survey")
        body.update(overrides)
        return client.post("/team/changes/add", headers=auth(org["lead"]), json=body)

    def test_a_manager_sees_only_their_own_team(self, client, org):
        r = client.get("/team/members", headers=auth(org["lead"]))
        assert [m["full_name"] for m in r.json()] == ["Ravi Kumar"]
        assert client.get("/team/members", headers=auth(org["ravi"])).status_code == 403
        assert client.get("/team/members", headers=auth(org["admin"])).status_code == 403

    def test_an_approved_add_invites_them_into_the_team(self, client, db, org):
        r = self.ask_add(client, org)
        assert r.status_code == 201, r.text
        change_id = r.json()["id"]
        assert r.json()["status"] == "PENDING"

        # Every admin is told; nothing exists yet.
        told = db.execute(select(Notification).where(
            Notification.kind == "TEAM_CHANGE_REQUESTED", Notification.channel == "IN_APP"
        )).scalars().all()
        assert {n.user_id for n in told} == {org["owner"].id, org["sysadmin"].id, org["admin"].id}
        assert db.execute(select(User).where(User.email == "sana@designboxed.com")).scalar() is None

        r = client.post(f"/team/changes/{change_id}/approve", headers=auth(org["admin"]),
                        json={"comment": "Welcome aboard", "send_email": False})
        assert r.status_code == 200, r.text
        assert r.json()["invite"]["invite_url"]
        assert r.json()["invite"]["email_sent"] is False
        assert r.json()["change"]["status"] == "APPROVED"
        assert r.json()["change"]["decided_by_name"] == "Priya Shah"

        sana = db.execute(select(User).where(User.email == "sana@designboxed.com")).scalar_one()
        assert sana.role is Role.GROUND_STAFF
        assert sana.manager_id == org["lead"].id
        assert sana.department_id == org["dept"].id
        assert r.json()["change"]["target_user_id"] == sana.id

        note = db.execute(select(Notification).where(
            Notification.user_id == org["lead"].id, Notification.kind == "TEAM_CHANGE_APPROVED",
        )).scalars().first()
        assert "Welcome aboard" in note.body

    def test_the_same_person_cannot_be_asked_for_twice(self, client, org):
        assert self.ask_add(client, org).status_code == 201
        assert self.ask_add(client, org).status_code == 409
        assert self.ask_add(client, org, email=org["meena"].email).status_code == 409

    def test_an_edit_keeps_only_what_changes_and_applies_on_approval(self, client, db, org):
        ravi = org["ravi"]
        r = client.post(f"/team/changes/edit/{ravi.id}", headers=auth(org["lead"]),
                        json={"full_name": "Ravi Kumar", "phone": "+91 98765 43210"})
        assert r.status_code == 201, r.text
        assert r.json()["payload"] == {"phone": {"from": None, "to": "9876543210"}}

        # One open ask per person at a time.
        again = client.post(f"/team/changes/edit/{ravi.id}", headers=auth(org["lead"]),
                            json={"designation": "TEAM_LEAD"})
        assert again.status_code == 409

        r = client.post(f"/team/changes/{r.json()['id']}/approve", headers=auth(org["admin"]),
                        json={})
        assert r.status_code == 200, r.text
        db.refresh(ravi)
        assert ravi.phone == "9876543210"

    def test_an_edit_that_changes_nothing_is_refused(self, client, org):
        r = client.post(f"/team/changes/edit/{org['ravi'].id}", headers=auth(org["lead"]),
                        json={"full_name": "Ravi Kumar"})
        assert r.status_code == 422

    def test_a_removal_marks_them_left_once_approved(self, client, db, org):
        r = client.post(f"/team/changes/remove/{org['ravi'].id}", headers=auth(org["lead"]),
                        json={"status": "LEFT", "reason": "Moved to another company"})
        assert r.status_code == 201, r.text
        r = client.post(f"/team/changes/{r.json()['id']}/approve", headers=auth(org["admin"]),
                        json={})
        assert r.status_code == 200, r.text
        db.refresh(org["ravi"])
        assert org["ravi"].status is UserStatus.LEFT
        assert org["ravi"].exited_on == clock.local_today()

    def test_only_their_own_members(self, client, org):
        r = client.post(f"/team/changes/edit/{org['meena'].id}", headers=auth(org["lead"]),
                        json={"phone": "+91 98765 43210"})
        assert r.status_code == 404

    def test_a_rejection_needs_a_reason_and_the_manager_hears_it(self, client, db, org):
        change_id = self.ask_add(client, org).json()["id"]
        r = client.post(f"/team/changes/{change_id}/reject", headers=auth(org["admin"]),
                        json={"comment": ""})
        assert r.status_code == 422

        r = client.post(f"/team/changes/{change_id}/reject", headers=auth(org["admin"]),
                        json={"comment": "Hiring is frozen this quarter"})
        assert r.status_code == 200, r.text
        assert r.json()["change"]["status"] == "REJECTED"
        note = db.execute(select(Notification).where(
            Notification.user_id == org["lead"].id, Notification.kind == "TEAM_CHANGE_REJECTED",
        )).scalars().first()
        assert "Hiring is frozen" in note.body

        # Decided once.
        again = client.post(f"/team/changes/{change_id}/approve", headers=auth(org["admin"]),
                            json={})
        assert again.status_code == 409

    def test_a_manager_can_withdraw_but_not_decide(self, client, db, org):
        change_id = self.ask_add(client, org).json()["id"]
        assert client.post(f"/team/changes/{change_id}/approve",
                           headers=auth(org["lead"]), json={}).status_code == 403
        # Another manager cannot even see it.
        assert client.post(f"/team/changes/{change_id}/cancel",
                           headers=auth(org["other_lead"])).status_code == 404
        r = client.post(f"/team/changes/{change_id}/cancel", headers=auth(org["lead"]))
        assert r.json()["status"] == "CANCELLED"

    def test_lists_are_scoped_and_count_what_waits(self, client, org):
        self.ask_add(client, org)
        mine = client.get("/team/changes", headers=auth(org["lead"])).json()
        theirs = client.get("/team/changes", headers=auth(org["other_lead"])).json()
        everyone = client.get("/team/changes", headers=auth(org["admin"]),
                              params={"status": "PENDING"}).json()
        assert (mine["pending"], theirs["pending"], everyone["pending"]) == (1, 0, 1)
        assert len(theirs["items"]) == 0

    def test_every_step_is_in_the_activity_log(self, client, db, org):
        change_id = self.ask_add(client, org).json()["id"]
        client.post(f"/team/changes/{change_id}/reject", headers=auth(org["admin"]),
                    json={"comment": "Not this month"})
        rows = db.execute(select(AuditLog).where(
            AuditLog.entity_type == "team_change", AuditLog.entity_id == change_id
        ).order_by(AuditLog.id)).scalars().all()
        assert [str(r.action) for r in rows] == ["SUBMIT", "REJECT"]

    def test_a_change_waits_on_a_manager_who_is_still_one(self, client, db, org):
        change_id = self.ask_add(client, org).json()["id"]
        org["lead"].role = Role.GROUND_STAFF
        db.commit()
        r = client.post(f"/team/changes/{change_id}/approve", headers=auth(org["admin"]),
                        json={})
        assert r.status_code == 409
        assert db.get(TeamChange, change_id).status is TeamChangeStatus.PENDING


class TestWhatAManagerSees:
    @pytest.fixture
    def trips(self, db, org):
        project = Project(tenant_id=TENANT, name="Monsoon Survey", code="CMP-2026-0001")
        db.add(project)
        db.flush()

        def trip(who):
            day = clock.local_today() + timedelta(days=5)
            row = TravelRequest(
                tenant_id=TENANT, request_type=RequestType.HOTEL, project_id=project.id,
                requester_id=who.id, hotel_city="Pune", hotel_state="Maharashtra",
                check_in=day, check_out=day + timedelta(days=1), travel_reason="Visit",
                submitted_at=naive_utcnow(),
            )
            row.travellers = [RequestTraveller(user_id=who.id, cost_amount=4200,
                                               status=TravellerStatus.BOOKED)]
            db.add(row)
            db.flush()
            return row

        mine, theirs = trip(org["ravi"]), trip(org["meena"])
        db.commit()
        return mine, theirs

    def test_the_team_view_holds_their_trips_and_no_costs(self, client, org, trips):
        mine, theirs = trips
        r = client.get("/requests", headers=auth(org["lead"]), params={"mine": "false"})
        items = r.json()["items"]
        assert [i["id"] for i in items] == [mine.id]
        assert items[0]["travellers"][0]["cost_amount"] is None

        assert client.get(f"/requests/{mine.id}", headers=auth(org["lead"])).status_code == 200
        assert client.get(f"/requests/{theirs.id}", headers=auth(org["lead"])).status_code == 404

    def test_their_own_view_is_still_their_own(self, client, org, trips):
        r = client.get("/requests", headers=auth(org["lead"]))
        assert r.json()["items"] == []

    def test_a_members_travel_history_without_costs(self, client, org, trips):
        r = client.get(f"/users/{org['ravi'].id}/travel-history", headers=auth(org["lead"]))
        assert r.status_code == 200, r.text
        assert all(e["cost_amount"] is None for e in r.json()["entries"])
        r = client.get(f"/users/{org['meena'].id}/travel-history", headers=auth(org["lead"]))
        assert r.status_code == 403


class TestManagersAndCampaigns:
    def test_a_manager_creates_and_edits_but_does_not_archive(self, client, org):
        r = client.post("/projects", headers=auth(org["lead"]), json={"name": "Pune Survey"})
        assert r.status_code == 201, r.text
        project_id = r.json()["id"]
        assert r.json()["code"].startswith("CMP-")

        r = client.patch(f"/projects/{project_id}", headers=auth(org["lead"]),
                         json={"client_name": "Acme"})
        assert r.status_code == 200, r.text

        r = client.patch(f"/projects/{project_id}", headers=auth(org["lead"]),
                         json={"status": "ARCHIVED"})
        assert r.status_code == 403
        assert client.post(f"/projects/{project_id}/archive",
                           headers=auth(org["lead"])).status_code == 403

    def test_ground_staff_cannot_create_one(self, client, org):
        r = client.post("/projects", headers=auth(org["ravi"]), json={"name": "Pune Survey"})
        assert r.status_code == 403
