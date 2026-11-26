"""
My profile: changing your own name, phone, sign-in email and password - and an
admin changing someone else's email or sending them a reset link.

The rules that matter: a wrong password is a 400 (the web signs out on 401),
guessing is limited, a password change signs out every other device, and an
email change tells the old address.
"""
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.core import ratelimit
from app.core.enums import AuditAction, Gender, Role, TokenPurpose
from app.core.security import create_access_token, hash_password, verify_password
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.base import naive_utcnow
from app.models.department import Department
from app.models.user import User
from app.routers.auth import _issue_token

TENANT = "designboxed"
PASSWORD = "Monsoon-Ledger-42"
HASH = hash_password(PASSWORD)


@pytest.fixture
def client(db):
    def same_session():
        yield db

    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def people(db):
    def person(name, email, role):
        return User(tenant_id=TENANT, email=email, full_name=name, role=role,
                    gender=Gender.MALE, password_hash=HASH)

    boss = person("Sridhar Rao", "sridhar@designboxed.com", Role.SYSTEM_ADMIN)
    admin = person("Priya Shah", "priya@designboxed.com", Role.ADMIN)
    ravi = person("Ravi Kumar", "ravi@designboxed.com", Role.GROUND_STAFF)
    db.add_all([boss, admin, ravi])
    db.commit()
    return boss, admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def old_token(user, seconds_ago=120):
    """A token issued a while back, as another device would hold."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": str(user.id), "role": str(user.role), "tenant": TENANT,
         "iat": now - seconds_ago, "exp": now + 3600, "jti": "older"},
        get_settings().secret_key, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def audit_rows(db, user, action):
    return db.execute(
        select(AuditLog).where(
            AuditLog.entity_id == user.id, AuditLog.entity_type == "user", AuditLog.action == action
        )
    ).scalars().all()


class TestNameAndPhone:
    def test_anyone_may_change_their_own(self, client, db, people):
        _, _, ravi = people
        r = client.patch("/auth/me", headers=auth(ravi),
                         json={"full_name": "  Ravi   K ", "phone": "+91 98765 43210"})
        assert r.status_code == 200
        assert (r.json()["full_name"], r.json()["phone"]) == ("Ravi K", "9876543210")

        [row] = audit_rows(db, ravi, AuditAction.UPDATE)
        assert row.actor_user_id == ravi.id
        assert row.changes["full_name"] == {"from": "Ravi Kumar", "to": "Ravi K"}
        assert row.changes["phone"] == {"from": None, "to": "9876543210"}

    @pytest.mark.parametrize("body", [
        {"role": "SYSTEM_ADMIN"}, {"is_active": False}, {"status": "ACTIVE"},
        {"email": "me@x.com"}, {"department_id": 1},
    ])
    def test_nothing_else_can_ride_along(self, client, db, people, body):
        _, _, ravi = people
        r = client.patch("/auth/me", headers=auth(ravi), json={"full_name": "Ravi K", **body})
        assert r.status_code == 422
        db.refresh(ravi)
        assert ravi.full_name == "Ravi Kumar" and ravi.role is Role.GROUND_STAFF

    @pytest.mark.parametrize("phone, ok", [
        ("abc", False), ("12345", False), ("+91 98765 43210", True), ("98765 43210", True),
        ("098765 43210", True), ("(040) 2345-6789", False), ("5876543210", False),
        ("98765432101", False),
    ])
    def test_phone_rules(self, client, people, phone, ok):
        _, _, ravi = people
        r = client.patch("/auth/me", headers=auth(ravi), json={"phone": phone})
        assert (r.status_code == 200) is ok

    def test_it_is_stored_as_ten_digits(self, client, people):
        _, _, ravi = people
        r = client.patch("/auth/me", headers=auth(ravi), json={"phone": "+91-98765-43210"})
        assert r.json()["phone"] == "9876543210"

    def test_no_two_employees_share_a_number(self, client, db, people):
        _, admin, ravi = people
        admin.phone = "9876543210"
        db.commit()
        r = client.patch("/auth/me", headers=auth(ravi), json={"phone": "98765 43210"})
        assert r.status_code == 409
        # Someone editing their own profile is not told whose number it is.
        assert admin.full_name not in r.json()["detail"]
        # Keeping your own number is not a clash.
        assert client.patch("/auth/me", headers=auth(admin),
                            json={"phone": "9876543210"}).status_code == 200

    def test_a_blank_phone_clears_it(self, client, db, people):
        _, _, ravi = people
        ravi.phone = "9876543210"
        db.commit()
        r = client.patch("/auth/me", headers=auth(ravi), json={"phone": "  "})
        assert r.json()["phone"] is None


class TestEmail:
    def test_a_wrong_password_is_400_and_audited(self, client, db, people):
        _, _, ravi = people
        r = client.post("/auth/me/email", headers=auth(ravi),
                        json={"new_email": "ravi.new@designboxed.com", "current_password": "nope"})
        assert r.status_code == 400  # never 401: that would sign them out
        assert r.json()["detail"] == "Current password is incorrect."
        assert len(audit_rows(db, ravi, AuditAction.LOGIN_FAILED)) == 1
        db.refresh(ravi)
        assert ravi.email == "ravi@designboxed.com"

    def test_the_change_takes_effect_and_tells_the_old_address(self, client, db, people, outbox):
        _, _, ravi = people
        headers = auth(ravi)
        r = client.post("/auth/me/email", headers=headers, json={
            "new_email": "Ravi.New@DesignBoxed.com", "current_password": PASSWORD,
        })
        assert r.status_code == 200
        assert r.json()["email"] == "ravi.new@designboxed.com"

        assert client.post("/auth/login", json={
            "email": "ravi.new@designboxed.com", "password": PASSWORD}).status_code == 200
        assert client.post("/auth/login", json={
            "email": "ravi@designboxed.com", "password": PASSWORD}).status_code == 401
        # The session carries on: tokens name the person by id.
        assert client.get("/auth/me", headers=headers).status_code == 200

        notices = [m for m in outbox.messages if "sign-in email was changed" in m["subject"]]
        assert [m["to"] for m in notices] == ["ravi@designboxed.com"]
        assert "ravi.new@designboxed.com" in notices[0]["body"]

    def test_a_reset_link_sent_to_the_old_address_dies(self, client, db, people):
        _, _, ravi = people
        raw, _ = _issue_token(db, ravi, TokenPurpose.PASSWORD_RESET, valid_hours=2)
        db.commit()
        r = client.post("/auth/me/email", headers=auth(ravi), json={
            "new_email": "ravi.new@designboxed.com", "current_password": PASSWORD,
        })
        assert r.status_code == 200
        assert client.get(f"/auth/token/{raw}").status_code == 404
        r = client.post("/auth/set-password", json={"token": raw, "password": "Brand-new-sky-77"})
        assert r.status_code != 200
        db.refresh(ravi)
        assert verify_password(PASSWORD, ravi.password_hash)

    def test_an_address_in_use_is_409(self, client, people):
        _, admin, ravi = people
        r = client.post("/auth/me/email", headers=auth(ravi), json={
            "new_email": "PRIYA@designboxed.com", "current_password": PASSWORD,
        })
        assert r.status_code == 409

    def test_your_own_address_is_400(self, client, people):
        _, _, ravi = people
        r = client.post("/auth/me/email", headers=auth(ravi), json={
            "new_email": "ravi@designboxed.com", "current_password": PASSWORD,
        })
        assert r.status_code == 400


class TestPassword:
    def _change(self, client, headers, current=PASSWORD, new="Brand-new-sky-77"):
        return client.post("/auth/change-password", headers=headers,
                           json={"current_password": current, "new_password": new})

    def test_other_devices_are_signed_out_and_this_one_carries_on(self, client, db, people):
        _, _, ravi = people
        elsewhere = old_token(ravi)
        assert client.get("/auth/me", headers=elsewhere).status_code == 200

        r = self._change(client, auth(ravi))
        assert r.status_code == 200
        body = r.json()
        assert body["access_token"] and body["expires_at"]

        assert client.get("/auth/me", headers=elsewhere).status_code == 401
        fresh = {"Authorization": f"Bearer {body['access_token']}"}
        me = client.get("/auth/me", headers=fresh)
        assert me.status_code == 200
        assert me.json()["password_changed_at"] is not None

        db.refresh(ravi)
        assert verify_password("Brand-new-sky-77", ravi.password_hash)
        [row] = audit_rows(db, ravi, AuditAction.UPDATE)
        assert row.changes == {"password": {"from": "***", "to": "***"}}

    def test_the_same_password_again_is_refused(self, client, people):
        _, _, ravi = people
        r = self._change(client, auth(ravi), new=PASSWORD)
        assert r.status_code == 422
        assert "different from your current one" in r.json()["detail"]

    def test_the_password_policy_still_applies(self, client, people):
        _, _, ravi = people
        assert self._change(client, auth(ravi), new="abc").status_code == 422

    def test_guessing_is_limited(self, client, people):
        _, _, ravi = people
        headers = auth(ravi)
        for _ in range(5):
            assert self._change(client, headers, current="wrong-guess").status_code == 400
        r = self._change(client, headers)  # even the right one, for now
        assert r.status_code == 429
        assert "Retry-After" in r.headers

        ratelimit.reset_all()
        assert self._change(client, headers).status_code == 200

    def test_a_reset_link_signs_out_other_devices_too(self, client, db, people):
        _, _, ravi = people
        elsewhere = old_token(ravi)
        raw, _ = _issue_token(db, ravi, TokenPurpose.PASSWORD_RESET, valid_hours=2)
        db.commit()
        r = client.post("/auth/set-password", json={"token": raw, "password": "Brand-new-sky-77"})
        assert r.status_code == 200
        db.refresh(ravi)
        assert ravi.password_changed_at is not None
        assert client.get("/auth/me", headers=elsewhere).status_code == 401

    def test_without_a_change_on_record_any_valid_token_works(self, client, people):
        _, _, ravi = people
        assert client.get("/auth/me", headers=old_token(ravi, seconds_ago=3000)).status_code == 200


class TestAdminEditsEmail:
    def test_an_admin_changes_someones_email(self, client, db, people, outbox):
        _, admin, ravi = people
        r = client.patch(f"/users/{ravi.id}", headers=auth(admin), json={"email": "New@X.com"})
        assert r.status_code == 200
        assert r.json()["email"] == "new@x.com"
        [row] = audit_rows(db, ravi, AuditAction.UPDATE)
        assert row.changes["email"] == {"from": "ravi@designboxed.com", "to": "new@x.com"}
        notices = [m for m in outbox.messages if "sign-in email was changed" in m["subject"]]
        assert [m["to"] for m in notices] == ["ravi@designboxed.com"]
        assert "Changed by Priya Shah" in notices[0]["body"]

    def test_an_invite_sent_to_the_old_address_dies(self, client, db, people):
        _, admin, _ = people
        typo = User(tenant_id=TENANT, email="typo@gmial.com", full_name="New Person",
                    role=Role.ADMIN, gender=Gender.FEMALE)
        db.add(typo)
        db.commit()
        raw, _ = _issue_token(db, typo, TokenPurpose.INVITE, valid_hours=72)
        db.commit()
        r = client.patch(f"/users/{typo.id}", headers=auth(admin),
                         json={"email": "new.person@designboxed.com"})
        assert r.status_code == 200
        assert client.get(f"/auth/token/{raw}").status_code == 404
        r = client.post("/auth/set-password", json={"token": raw, "password": "Brand-new-sky-77"})
        assert r.status_code != 200
        db.refresh(typo)
        assert typo.password_hash is None

    def test_a_name_edit_leaves_links_alone(self, client, db, people):
        _, admin, ravi = people
        raw, _ = _issue_token(db, ravi, TokenPurpose.PASSWORD_RESET, valid_hours=2)
        db.commit()
        r = client.patch(f"/users/{ravi.id}", headers=auth(admin), json={"full_name": "Ravi K"})
        assert r.status_code == 200
        assert client.get(f"/auth/token/{raw}").status_code == 200

    def test_a_duplicate_is_409(self, client, people):
        boss, _, ravi = people
        r = client.patch(f"/users/{ravi.id}", headers=auth(boss),
                         json={"email": "priya@designboxed.com"})
        assert r.status_code == 409

    def test_not_your_own_from_the_team_page(self, client, people):
        _, admin, _ = people
        r = client.patch(f"/users/{admin.id}", headers=auth(admin), json={"email": "me@x.com"})
        assert r.status_code == 400
        assert "My profile" in r.json()["detail"]

    def test_a_system_admin_editing_themselves_keeps_the_old_rules(self, client, people):
        boss, _, _ = people
        r = client.patch(f"/users/{boss.id}", headers=auth(boss), json={"role": "ADMIN"})
        assert r.status_code == 400
        r = client.patch(f"/users/{boss.id}", headers=auth(boss), json={"phone": "9876543210"})
        assert r.status_code == 200


class TestAdminsOwnRecord:
    """My profile saves an admin's own department, gender and base through
    PATCH /users/{their id}: they are the people who set those for everyone."""

    @pytest.mark.parametrize("who", [0, 1])  # system admin, admin
    def test_an_admin_sets_their_own_department_gender_and_base(self, client, db, people, who):
        me = people[who]
        dept = Department(tenant_id=TENANT, name="Field Operations")
        db.add(dept)
        db.commit()

        r = client.patch(f"/users/{me.id}", headers=auth(me), json={
            "department_id": dept.id, "gender": "FEMALE", "designation": "MANAGER",
            "base_state": "Telangana", "base_location": "Hyderabad", "employee_code": "DB-001",
        })
        assert r.status_code == 200, r.text

        r = client.get("/auth/me", headers=auth(me))
        body = r.json()
        assert body["department_name"] == "Field Operations"
        assert (body["gender"], body["designation"]) == ("FEMALE", "MANAGER")
        assert (body["base_state"], body["base_location"]) == ("Telangana", "Hyderabad")
        assert body["employee_code"] == "DB-001"

        [row] = audit_rows(db, me, AuditAction.UPDATE)
        assert row.actor_user_id == me.id and "department_id" in row.changes

    def test_ground_staff_cannot_reach_their_own_record_that_way(self, client, db, people):
        _, _, ravi = people
        r = client.patch(f"/users/{ravi.id}", headers=auth(ravi), json={"gender": "FEMALE"})
        assert r.status_code == 403
        db.refresh(ravi)
        assert ravi.gender is Gender.MALE


class TestResetLink:
    def test_reinvite_says_which_kind_of_link(self, client, db, people):
        boss, _, ravi = people
        r = client.post(f"/users/{ravi.id}/reinvite", headers=auth(boss))
        assert r.json()["purpose"] == "PASSWORD_RESET"

        ravi.password_hash = None
        db.commit()
        r = client.post(f"/users/{ravi.id}/reinvite", headers=auth(boss))
        assert r.json()["purpose"] == "INVITE"

    def test_a_sign_in_right_after_a_reset_works(self, client, db, people):
        """The cutoff is in whole seconds, so a token minted in the same second
        as the reset must still count."""
        _, _, ravi = people
        ravi.password_changed_at = naive_utcnow()
        db.commit()
        r = client.post("/auth/login", json={"email": ravi.email, "password": PASSWORD})
        token = r.json()["access_token"]
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
