"""
The report endpoints over HTTP: the dashboard, the travel log and cost analytics
share one set of filters, and each reads them the same way.

What matters here is the wiring the service tests cannot see: that a missing
status really means every status, that the cost page takes the shared filters
(and quietly ignores the two dropdowns it used to have), that reversed dates
are swapped rather than failing, and that only admins get in.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core import ratelimit
from app.core.enums import ProjectStatus, RequestType, Role, TravelMode, TravellerStatus
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User

TENANT = "designboxed"


@pytest.fixture
def client(db):
    def same_session():
        yield db

    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        # No lifespan: it would start the scheduler and probe the database.
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def people(db):
    admin = User(tenant_id=TENANT, email="priya@designboxed.com", full_name="Priya Shah",
                 role=Role.ADMIN, password_hash="x")
    ravi = User(tenant_id=TENANT, email="ravi@designboxed.com", full_name="Ravi Kumar",
                role=Role.GROUND_STAFF, password_hash="x")
    db.add_all([admin, ravi])
    db.commit()
    return admin, ravi


def auth(user):
    token, _ = create_access_token(user_id=user.id, role=str(user.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def august(db, people):
    """Three August movements for Ravi: booked to Pune, rejected to Chennai,
    and a booked hotel in Chennai."""
    _, ravi = people
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MS-1",
                      status=ProjectStatus.ACTIVE)
    db.add(project)
    db.flush()

    def add(kind, status, cost=None, **fields):
        row = TravelRequest(tenant_id=TENANT, request_type=kind, project_id=project.id,
                            requester_id=ravi.id, travel_reason="Store audit", **fields)
        db.add(row)
        db.flush()
        db.add(RequestTraveller(request_id=row.id, user_id=ravi.id, status=status,
                                cost_amount=cost))

    add(RequestType.LONG_DISTANCE, TravellerStatus.BOOKED, Decimal("4000"),
        mode=TravelMode.FLIGHT, origin="Hyderabad", origin_state="Telangana",
        destination="Pune", destination_state="Maharashtra", start_at=datetime(2026, 8, 5, 9))
    add(RequestType.LONG_DISTANCE, TravellerStatus.REJECTED,
        mode=TravelMode.FLIGHT, origin="Hyderabad", origin_state="Telangana",
        destination="Chennai", destination_state="Tamil Nadu", start_at=datetime(2026, 8, 6, 9))
    add(RequestType.HOTEL, TravellerStatus.BOOKED, Decimal("900"),
        hotel_city="Chennai", hotel_state="Tamil Nadu",
        check_in=date(2026, 8, 7), check_out=date(2026, 8, 9))
    db.commit()
    return project


WINDOW = {"since": "2026-08-01", "until": "2026-08-31"}


class TestTravelLogs:
    def test_no_status_lists_every_status(self, client, people, august):
        admin, _ = people
        body = client.get("/travel-logs", params=WINDOW, headers=auth(admin)).json()
        assert body["total"] == 3
        assert body["summary"]["movements"] == 2      # the rejected flight went nowhere
        assert body["summary"]["spent"] == "4900.00"

    def test_travelled_statuses_can_be_asked_for(self, client, people, august):
        admin, _ = people
        params = {**WINDOW, "status": ["PENDING", "APPROVED", "BOOKED"]}
        body = client.get("/travel-logs", params=params, headers=auth(admin)).json()
        assert body["total"] == 2
        assert {e["priority"] for e in body["entries"]} == {"MEDIUM"}

    def test_state_and_city_are_the_destination(self, client, people, august):
        admin, _ = people
        headers = auth(admin)
        by_state = client.get("/travel-logs", params={**WINDOW, "state": "Telangana"}, headers=headers)
        assert by_state.json()["total"] == 0
        by_city = client.get("/travel-logs", params={**WINDOW, "city": "Chennai"}, headers=headers)
        assert by_city.json()["total"] == 2


class TestDashboard:
    def test_filters_and_awaiting_come_back(self, client, people, august):
        admin, _ = people
        response = client.get("/analytics/insights", params={**WINDOW, "state": "Tamil Nadu"},
                              headers=auth(admin))
        assert response.status_code == 200
        body = response.json()
        assert body["top_states"] == [{"label": "Tamil Nadu", "count": 1}]
        assert set(body["awaiting"]) == {"requests", "people", "partly_approved", "with_conflicts"}
        assert body["kpis"]["unstated"] == 0

    def test_filter_options_offer_destination_cities(self, client, people, august):
        admin, _ = people
        body = client.get("/analytics/filter-options", headers=auth(admin)).json()
        assert body["states"] == ["Maharashtra", "Tamil Nadu"]
        assert {"state": "Tamil Nadu", "city": "Chennai"} in body["cities"]
        assert {p["full_name"]: p["status"] for p in body["people"]}["Ravi Kumar"] == "ACTIVE"


class TestCostAnalytics:
    def test_the_bundle_follows_the_filters(self, client, people, august):
        admin, _ = people
        params = {**WINDOW, "project_id": august.id, "state": "Tamil Nadu", "city": "Chennai"}
        response = client.get("/analytics", params=params, headers=auth(admin))
        assert response.status_code == 200
        body = response.json()
        assert body["overview"]["spent"] == "900.00"
        assert body["grain"] == "day"
        assert (body["since"], body["until"]) == ("2026-08-01", "2026-08-31")
        assert len(body["trend"]) == 31
        assert [p["full_name"] for p in body["by_person"]] == ["Ravi Kumar"]
        assert [s["label"] for s in body["by_state"]] == ["Tamil Nadu"]
        assert [c["label"] for c in body["by_city"]] == ["Chennai"]
        assert "by_month" not in body
        assert body["deployed_people"] == 0   # August is in the past

    def test_the_old_dropdown_params_are_ignored(self, client, people, august):
        admin, _ = people
        response = client.get("/analytics", params={"days": 30, "months": 6}, headers=auth(admin))
        assert response.status_code == 200
        assert response.json()["overview"]["spent"] == "4900.00"

    def test_reversed_dates_are_swapped(self, client, people, august):
        admin, _ = people
        params = {"since": "2026-08-31", "until": "2026-08-01"}
        response = client.get("/analytics", params=params, headers=auth(admin))
        assert response.status_code == 200
        assert response.json()["since"] == "2026-08-01"
        assert response.json()["overview"]["spent"] == "4900.00"

    @pytest.mark.parametrize("path", ["/analytics", "/analytics/insights", "/travel-logs"])
    def test_ground_staff_cannot_read_reports(self, client, people, path):
        _, ravi = people
        assert client.get(path, headers=auth(ravi)).status_code == 403
