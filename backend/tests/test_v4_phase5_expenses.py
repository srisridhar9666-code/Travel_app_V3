"""
Test suite for Sriyatra Master Specification v4.0 - Phase 5:
Post-Travel Expense Reimbursements & Finance Settlement Desk.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.core.enums import (
    ExpenseCategory,
    ExpenseStatus,
    Gender,
    PaymentMode,
    Role,
)
from app.core.security import create_access_token
from app.database import get_db
from app.main import app
from app.models.expense import ExpenseClaim, ExpenseItem
from app.models.project import Project
from app.models.user import User
from app.services import notifications

TENANT = "designboxed"


@dataclass(frozen=True)
class Ref:
    id: int
    role: Role
    email: str
    name: str = ""
    gender: Gender = Gender.MALE
    manager_id: int | None = None


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def auth_header(ref: Ref) -> dict[str, str]:
    token, _ = create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def env(db):
    project = Project(tenant_id=TENANT, name="V4 Field Deployment", code="V4-PROJ-05")
    db.add(project)
    db.commit()

    admin = User(
        tenant_id=TENANT,
        email="admin_exp@designboxed.com",
        full_name="Operations Admin",
        role=Role.ADMIN,
        gender=Gender.MALE,
        password_hash="x",
    )
    finance = User(
        tenant_id=TENANT,
        email="finance_exp@designboxed.com",
        full_name="Finance Officer",
        role=Role.FINANCE,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    manager = User(
        tenant_id=TENANT,
        email="manager_exp@designboxed.com",
        full_name="Regional Manager",
        role=Role.MANAGER,
        gender=Gender.MALE,
        password_hash="x",
    )
    db.add_all([admin, finance, manager])
    db.commit()

    staff = User(
        tenant_id=TENANT,
        email="staff_exp@designboxed.com",
        full_name="Rahul Verma",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        manager_id=manager.id,
        password_hash="x",
    )
    unrelated_user = User(
        tenant_id=TENANT,
        email="unrelated_exp@designboxed.com",
        full_name="Other Staff",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    db.add_all([staff, unrelated_user])
    db.commit()

    return {
        "project_id": project.id,
        "admin": Ref(admin.id, admin.role, admin.email, admin.full_name),
        "finance": Ref(finance.id, finance.role, finance.email, finance.full_name),
        "manager": Ref(manager.id, manager.role, manager.email, manager.full_name),
        "staff": Ref(staff.id, staff.role, staff.email, staff.full_name, manager_id=manager.id),
        "unrelated": Ref(unrelated_user.id, unrelated_user.role, unrelated_user.email, unrelated_user.full_name),
    }


def test_employee_submits_expense_claim_with_policy_caps(client, env, db):
    """Employee submits itemized claim within policy caps; code EXP-YYYY-NNNN generated."""
    today = date.today()
    payload = {
        "project_id": env["project_id"],
        "destination_city": "Nagpur",
        "travel_start_date": today.isoformat(),
        "travel_end_date": (today + timedelta(days=2)).isoformat(),
        "items": [
            {
                "expense_date": today.isoformat(),
                "category": "FOOD",
                "vendor_merchant_name": "Haldiram Restaurant",
                "bill_number": "HLD-1024",
                "claimed_amount": 350.0,
                "receipt_url": "receipts/food_350.jpg",
            },
            {
                "expense_date": today.isoformat(),
                "category": "LOCAL_TRANSIT",
                "vendor_merchant_name": "Nagpur Auto Rickshaw",
                "claimed_amount": 120.0,  # Unbilled auto <= ₹150 cap
                "justification": "Railway station to field camp office",
            },
            {
                "expense_date": (today + timedelta(days=1)).isoformat(),
                "category": "MISCELLANEOUS",
                "vendor_merchant_name": "Stationery Mart",
                "bill_number": "MART-55",
                "claimed_amount": 180.0,
                "receipt_url": "receipts/mart_180.jpg",
                "justification": "Field survey printouts and clipboards",
            },
        ],
    }

    resp = client.post("/expenses", json=payload, headers=auth_header(env["staff"]))
    assert resp.status_code == 201, resp.text
    claim = resp.json()

    assert claim["status"] == "SUBMITTED"
    assert claim["claim_code"].startswith(f"EXP-{today.year}-")
    assert claim["total_claimed_amount"] == 650.0
    assert claim["manager_id"] == env["manager"].id
    assert claim["is_food_cap_exceeded"] is False
    assert claim["food_excess_amount"] == 0.0
    assert claim["is_unbilled_transit_exceeded"] is False
    assert claim["unbilled_transit_excess_amount"] == 0.0
    assert len(claim["items"]) == 3


def test_food_and_unbilled_transit_caps_excess_detection(client, env, db):
    """Exceeding ₹400/day food or ₹150/day unbilled local transit is correctly calculated and flagged."""
    today = date.today()
    payload = {
        "project_id": env["project_id"],
        "destination_city": "Mumbai",
        "travel_start_date": today.isoformat(),
        "travel_end_date": today.isoformat(),
        "items": [
            {
                "expense_date": today.isoformat(),
                "category": "FOOD",
                "vendor_merchant_name": "Bhojanalaya",
                "claimed_amount": 550.0,  # ₹150 excess over ₹400 cap
                "receipt_url": "receipts/food.jpg",
            },
            {
                "expense_date": today.isoformat(),
                "category": "LOCAL_TRANSIT",
                "vendor_merchant_name": "Morning Auto",
                "claimed_amount": 110.0,
            },
            {
                "expense_date": today.isoformat(),
                "category": "LOCAL_TRANSIT",
                "vendor_merchant_name": "Evening Auto",
                "claimed_amount": 120.0,
            },
        ],
    }

    resp = client.post("/expenses", json=payload, headers=auth_header(env["staff"]))
    assert resp.status_code == 201, resp.text
    claim = resp.json()

    assert claim["is_food_cap_exceeded"] is True
    assert claim["food_excess_amount"] == 150.0
    assert claim["is_unbilled_transit_exceeded"] is True
    assert claim["unbilled_transit_excess_amount"] == 80.0


def test_manager_endorses_expense_claim(client, env, db):
    """Direct manager endorses employee's submitted expense claim (L1 Review)."""
    today = date.today()
    payload = {
        "project_id": env["project_id"],
        "destination_city": "Pune",
        "travel_start_date": today.isoformat(),
        "travel_end_date": today.isoformat(),
        "items": [
            {
                "expense_date": today.isoformat(),
                "category": "FOOD",
                "vendor_merchant_name": "Cafe",
                "claimed_amount": 250.0,
                "receipt_url": "receipts/cafe.jpg",
            }
        ],
    }
    create_resp = client.post("/expenses", json=payload, headers=auth_header(env["staff"]))
    claim_id = create_resp.json()["id"]

    # Unrelated user cannot endorse
    fail_resp = client.post(
        f"/expenses/{claim_id}/endorse",
        json={"endorsed": True, "notes": "I approve"},
        headers=auth_header(env["unrelated"]),
    )
    assert fail_resp.status_code == 403

    # Assigned manager endorses
    endorse_resp = client.post(
        f"/expenses/{claim_id}/endorse",
        json={"endorsed": True, "notes": "Verified food expenses incurred on project site."},
        headers=auth_header(env["manager"]),
    )
    assert endorse_resp.status_code == 200, endorse_resp.text
    claim = endorse_resp.json()
    assert claim["status"] == "MANAGER_ENDORSED"
    assert claim["manager_endorsed"] is True
    assert claim["manager_name"] == env["manager"].name


def test_finance_audit_with_itemized_deductions(client, env, db):
    """Finance desk applies item-level deductions with reasons and approves claim (L2 Review)."""
    today = date.today()
    payload = {
        "project_id": env["project_id"],
        "destination_city": "Jaipur",
        "travel_start_date": today.isoformat(),
        "travel_end_date": today.isoformat(),
        "items": [
            {
                "expense_date": today.isoformat(),
                "category": "FOOD",
                "vendor_merchant_name": "Jaipur Sweets",
                "claimed_amount": 300.0,
                "receipt_url": "receipts/food.jpg",
            },
            {
                "expense_date": today.isoformat(),
                "category": "LOCAL_TRANSIT",
                "vendor_merchant_name": "City Taxi Cab",
                "claimed_amount": 250.0,
                "receipt_url": "receipts/taxi_250.jpg",
            },
        ],
    }
    create_resp = client.post("/expenses", json=payload, headers=auth_header(env["staff"]))
    claim_id = create_resp.json()["id"]
    item_transit_id = create_resp.json()["items"][1]["id"]

    # Manager endorses first
    client.post(
        f"/expenses/{claim_id}/endorse",
        json={"endorsed": True},
        headers=auth_header(env["manager"]),
    )

    # Finance audits and applies ₹100 deduction to transit line
    audit_payload = {
        "decision": "PARTIALLY_APPROVED",
        "notes": "Audited with ₹100 deduction for exceeding unbilled auto ceiling.",
        "deductions": [
            {
                "item_id": item_transit_id,
                "deducted_amount": 100.0,
                "deduction_reason": "Exceeds ₹150 daily unbilled auto allowance cap.",
            }
        ],
    }
    audit_resp = client.post(
        f"/expenses/{claim_id}/audit",
        json=audit_payload,
        headers=auth_header(env["finance"]),
    )
    assert audit_resp.status_code == 200, audit_resp.text
    claim = audit_resp.json()

    assert claim["status"] == "PARTIALLY_APPROVED"
    assert claim["total_claimed_amount"] == 550.0
    assert claim["total_approved_amount"] == 450.0
    assert claim["total_deducted_amount"] == 100.0
    assert claim["finance_auditor_name"] == env["finance"].name

    deducted_item = next(it for it in claim["items"] if it["id"] == item_transit_id)
    assert deducted_item["deducted_amount"] == 100.0
    assert deducted_item["approved_amount"] == 150.0
    assert "Exceeds ₹150 daily" in deducted_item["deduction_reason"]


def test_finance_settles_claim_with_bank_utr(client, env, db):
    """Finance desk records bank disbursement with UTR reference, moving claim to SETTLED."""
    today = date.today()
    payload = {
        "project_id": env["project_id"],
        "destination_city": "Kolkata",
        "travel_start_date": today.isoformat(),
        "travel_end_date": today.isoformat(),
        "items": [
            {
                "expense_date": today.isoformat(),
                "category": "FOOD",
                "vendor_merchant_name": "Annapurna",
                "claimed_amount": 400.0,
                "receipt_url": "receipts/annapurna.jpg",
            }
        ],
    }
    create_resp = client.post("/expenses", json=payload, headers=auth_header(env["staff"]))
    claim_id = create_resp.json()["id"]

    # Endorse & Full Approve
    client.post(f"/expenses/{claim_id}/endorse", json={"endorsed": True}, headers=auth_header(env["manager"]))
    client.post(f"/expenses/{claim_id}/audit", json={"decision": "FINANCE_APPROVED"}, headers=auth_header(env["finance"]))

    # Ground staff cannot settle
    fail_resp = client.post(
        f"/expenses/{claim_id}/settle",
        json={"payment_mode": "NEFT", "payment_date": today.isoformat(), "bank_utr_number": "UTR12345"},
        headers=auth_header(env["staff"]),
    )
    assert fail_resp.status_code == 403

    # Finance settles
    settle_payload = {
        "payment_mode": "NEFT",
        "payment_date": today.isoformat(),
        "bank_utr_number": "HDFCN202611200009876",
    }
    settle_resp = client.post(
        f"/expenses/{claim_id}/settle",
        json=settle_payload,
        headers=auth_header(env["finance"]),
    )
    assert settle_resp.status_code == 200, settle_resp.text
    claim = settle_resp.json()
    assert claim["status"] == "SETTLED"
    assert claim["payment_mode"] == "NEFT"
    assert claim["bank_utr_number"] == "HDFCN202611200009876"
    assert claim["settled_by_name"] == env["finance"].name


def test_claims_csv_export_by_finance(client, env, db):
    """Finance desk exports CSV of reimbursement claims."""
    today = date.today()
    client.post(
        "/expenses",
        json={
            "project_id": env["project_id"],
            "destination_city": "Delhi",
            "travel_start_date": today.isoformat(),
            "travel_end_date": today.isoformat(),
            "items": [
                {
                    "expense_date": today.isoformat(),
                    "category": "FOOD",
                    "vendor_merchant_name": "Dhaba",
                    "claimed_amount": 200.0,
                    "receipt_url": "receipts/dhaba.jpg",
                }
            ],
        },
        headers=auth_header(env["staff"]),
    )

    resp = client.get("/expenses/export", headers=auth_header(env["finance"]))
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "Claim Code,Employee Name" in resp.text
    assert "EXP-" in resp.text
