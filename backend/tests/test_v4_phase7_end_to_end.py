"""
Phase 7 End-to-End Comprehensive Operational Scenario Test
Master Specification V4.0 - Sriyatra Travel Management System

Validates the full enterprise lifecycle across all 5 roles:
1. Ground Staff raises Hotel & Local Cab requests under Campaign.
2. Manager L1 Review: Evaluates/sets Single/Double room occupancy entitlement & approves.
3. System Gate: Proves Admin approval is strictly blocked until Manager approval is recorded.
4. Admin L2 Approval & Vendor Dispatch: Transitions to READY_FOR_VENDOR.
5. Vendor Fulfillment: Submits 2-5 hotel proposals; employee selects; cab driver/vehicle assigned.
6. Co-Stay Affinity Engine: Strictly validates same-gender pairing constraint (HTTP 422 on mismatch).
7. In-Transit Telemetry: Morning/evening cab odometer (80 km contract package) & Hotel check-in/folio upload.
8. Expense Claim & Finance Desk: Itemized claim, ₹400 food cap check, Manager endorsement, Finance line deductions, and Bank UTR settlement.
9. Executive Governance: Super Admin == System Admin (Rank 5) & Activity Audit Ledger integrity.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import security
from app.core.enums import (
    AuditAction,
    CabType,
    ExpenseCategory,
    ExpenseStatus,
    HotelOccupancy,
    HotelProposalStatus,
    PaymentMode,
    ProjectStatus,
    RequestPriority,
    RequestStatus,
    RequestType,
    Role,
    TravellerStatus,
    UserStatus,
    StayVerificationStatus,
)
from app.core import ratelimit
from app.database import get_db
from app.main import app
from app.models.audit import AuditLog
from app.models.expense import ExpenseClaim, ExpenseItem
from app.models.hotel_proposal import HotelProposal
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.telemetry import CabTelemetry, HotelStayVerification
from app.models.user import User
from app.models.vendor import Vendor, VendorKind
from app.services import notifications, policy

TENANT = "designboxed"


@dataclass(frozen=True)
class Ref:
    id: int
    role: Role
    email: str
    name: str = ""
    gender: str = "MALE"


@pytest.fixture
def client(db, monkeypatch):
    def same_session():
        yield db

    monkeypatch.setattr(notifications, "deliver_queued", lambda ids: None)
    app.dependency_overrides[get_db] = same_session
    ratelimit.reset_all()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.reset_all()


@pytest.fixture
def e2e_env(db: Session):
    """Seed users, vendor, and campaign for the complete operational lifecycle."""
    # 1. Vendor Organization
    vendor = Vendor(
        tenant_id=TENANT,
        name="Apex Travel & Fleet Solutions",
        kind=VendorKind.CAB,
        contact_name="Vikram Sethi",
        phone="9876543210",
        email="e2e.dispatch@apextravel.in",
        gstin="07AAAAA0000A1Z5",
        is_active=True,
    )
    db.add(vendor)
    db.flush()

    # 2. Users across the 5 roles
    manager = User(
        tenant_id=TENANT,
        email="e2e.manager.rajesh@designboxed.com",
        full_name="Rajesh Sharma",
        role=Role.MANAGER,
        gender="MALE",
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    admin = User(
        tenant_id=TENANT,
        email="e2e.admin.priya@designboxed.com",
        full_name="Priya Patel",
        role=Role.ADMIN,
        gender="FEMALE",
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    super_admin = User(
        tenant_id=TENANT,
        email="e2e.super.anand@designboxed.com",
        full_name="Anand Varma",
        role=Role.SUPER_ADMIN,
        gender="MALE",
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    system_admin = User(
        tenant_id=TENANT,
        email="e2e.sys.admin@designboxed.com",
        full_name="System Administrator",
        role=Role.SYSTEM_ADMIN,
        gender="MALE",
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    finance = User(
        tenant_id=TENANT,
        email="e2e.finance.desk@designboxed.com",
        full_name="Sunil Mehta",
        role=Role.FINANCE,
        gender="MALE",
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    vendor_user = User(
        tenant_id=TENANT,
        email="e2e.ops@apextravel.in",
        full_name="Apex Dispatch Operator",
        role=Role.VENDOR,
        gender="MALE",
        vendor_id=vendor.id,
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([manager, admin, super_admin, system_admin, finance, vendor_user])
    db.flush()

    # Ground staff reporting to Rajesh Sharma
    emp_amit = User(
        tenant_id=TENANT,
        email="e2e.amit.kumar@designboxed.com",
        full_name="Amit Kumar",
        role=Role.GROUND_STAFF,
        gender="MALE",
        manager_id=manager.id,
        hotel_occupancy_entitlement=HotelOccupancy.SINGLE,
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    emp_deepak = User(
        tenant_id=TENANT,
        email="e2e.deepak.verma@designboxed.com",
        full_name="Deepak Verma",
        role=Role.GROUND_STAFF,
        gender="MALE",
        manager_id=manager.id,
        hotel_occupancy_entitlement=HotelOccupancy.DOUBLE,
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    emp_kavita = User(
        tenant_id=TENANT,
        email="e2e.kavita.singh@designboxed.com",
        full_name="Kavita Singh",
        role=Role.GROUND_STAFF,
        gender="FEMALE",
        manager_id=manager.id,
        hotel_occupancy_entitlement=HotelOccupancy.DOUBLE,
        password_hash=security.hash_password("Pass@123"),
        status=UserStatus.ACTIVE,
        is_active=True,
    )
    db.add_all([emp_amit, emp_deepak, emp_kavita])
    db.flush()

    # Operational Campaign
    campaign = Project(
        tenant_id=TENANT,
        code="CMP-2026-0099",
        name="Telangana Assembly Elections Field Logistics",
        status=ProjectStatus.ACTIVE,
    )
    db.add(campaign)
    db.commit()

    return {
        "vendor_id": vendor.id,
        "campaign_id": campaign.id,
        "manager": Ref(manager.id, manager.role, manager.email, manager.full_name, manager.gender),
        "admin": Ref(admin.id, admin.role, admin.email, admin.full_name, admin.gender),
        "super_admin": Ref(super_admin.id, super_admin.role, super_admin.email, super_admin.full_name, super_admin.gender),
        "system_admin": Ref(system_admin.id, system_admin.role, system_admin.email, system_admin.full_name, system_admin.gender),
        "finance": Ref(finance.id, finance.role, finance.email, finance.full_name, finance.gender),
        "vendor_user": Ref(vendor_user.id, vendor_user.role, vendor_user.email, vendor_user.full_name, vendor_user.gender),
        "emp_amit": Ref(emp_amit.id, emp_amit.role, emp_amit.email, emp_amit.full_name, emp_amit.gender),
        "emp_deepak": Ref(emp_deepak.id, emp_deepak.role, emp_deepak.email, emp_deepak.full_name, emp_deepak.gender),
        "emp_kavita": Ref(emp_kavita.id, emp_kavita.role, emp_kavita.email, emp_kavita.full_name, emp_kavita.gender),
    }


def auth_header(ref: Ref) -> dict[str, str]:
    token, _ = security.create_access_token(user_id=ref.id, role=str(ref.role), tenant_id=TENANT)
    return {"Authorization": f"Bearer {token}"}


def test_full_lifecycle_operational_scenario(client: TestClient, db: Session, e2e_env: dict):
    """
    Executes the entire corporate workflow:
    1. Employee profile manager occupancy evaluation
    2. Hotel request creation & 2-tier approval barrier
    3. Vendor hotel proposals & employee selection
    4. Cab request creation, approval, and dispatch
    5. In-transit telemetry (odometer & check-in/out)
    6. Expense claim with food cap audit & UTR settlement
    7. Super Admin & Activity Ledger verification
    """
    env = e2e_env
    manager_h = auth_header(env["manager"])
    admin_h = auth_header(env["admin"])
    super_admin_h = auth_header(env["super_admin"])
    finance_h = auth_header(env["finance"])
    vendor_h = auth_header(env["vendor_user"])
    amit_h = auth_header(env["emp_amit"])

    # -------------------------------------------------------------------------
    # Step 1: Manager Occupancy Evaluation on Team Member Profile
    # -------------------------------------------------------------------------
    # Manager evaluates Amit and changes his entitlement from SINGLE to DOUBLE
    resp = client.put(
        f"/team/members/{env['emp_amit'].id}/occupancy",
        json={"hotel_occupancy_entitlement": "DOUBLE"},
        headers=manager_h,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["hotel_occupancy_entitlement"] == "DOUBLE"

    # Reset to SINGLE as required for the scenario
    resp = client.put(
        f"/team/members/{env['emp_amit'].id}/occupancy",
        json={"hotel_occupancy_entitlement": "SINGLE"},
        headers=manager_h,
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["hotel_occupancy_entitlement"] == "SINGLE"

    # -------------------------------------------------------------------------
    # Step 2: Employee Amit raises a Hotel Request in Hyderabad (Category A)
    # -------------------------------------------------------------------------
    t_start = date.today() + timedelta(days=2)
    t_end = t_start + timedelta(days=2)

    hotel_payload = {
        "request_type": "HOTEL",
        "project_id": env["campaign_id"],
        "priority": "HIGH",
        "travel_reason": "Constituency campaign coordination and strategy meeting",
        "hotel_state": "Telangana",
        "hotel_city": "Hyderabad",
        "check_in": str(t_start),
        "check_out": str(t_end),
        "traveller_ids": [env["emp_amit"].id],
        "is_draft": False,
    }
    resp = client.post("/requests", json=hotel_payload, headers=amit_h)
    assert resp.status_code == status.HTTP_201_CREATED
    hotel_req = resp.json()
    hotel_req_id = hotel_req["id"]
    traveller_id = hotel_req["travellers"][0]["id"]

    # -------------------------------------------------------------------------
    # Step 3: Enforce Two-Tier Approval Guard
    # Admin MUST be blocked from approving until Manager L1 approval is recorded
    # -------------------------------------------------------------------------
    admin_block_resp = client.post(
        f"/requests/{hotel_req_id}/travellers/{traveller_id}/decide",
        json={
            "to_status": "APPROVED",
            "reason": "Direct admin approval bypass attempt",
        },
        headers=admin_h,
    )
    assert admin_block_resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "Manager approval (L1) is required before Admin can approve" in admin_block_resp.json()["detail"]

    # Manager records L1 Recommendation (Approve with Single Occupancy confirmation)
    manager_review_resp = client.post(
        f"/requests/{hotel_req_id}/recommendation",
        json={
            "recommendation": "RECOMMENDED",
            "comment": "Verified operational necessity in Hyderabad. Single room approved.",
            "hotel_occupancy": "SINGLE",
        },
        headers=manager_h,
    )
    assert manager_review_resp.status_code == status.HTTP_200_OK
    assert manager_review_resp.json()["travellers"][0]["manager_recommendation"] == "RECOMMENDED"

    # Now Admin L2 Approval succeeds and dispatches to Vendor
    admin_approve_resp = client.post(
        f"/requests/{hotel_req_id}/travellers/{traveller_id}/decide",
        json={
            "to_status": "APPROVED",
            "reason": "Within Category A limits and manager endorsed",
            "assigned_vendor_id": env["vendor_id"],
        },
        headers=admin_h,
    )
    assert admin_approve_resp.status_code == status.HTTP_200_OK
    updated_req = admin_approve_resp.json()
    assert updated_req["status"] == "READY_FOR_VENDOR"
    assert updated_req["assigned_vendor_id"] == env["vendor_id"]

    # -------------------------------------------------------------------------
    # Step 4: Contracted Vendor submits 2 Curated Hotel Proposals
    # -------------------------------------------------------------------------
    proposals_payload = {
        "options": [
            {
                "hotel_name": "Lemon Tree Premier Hitec City",
                "address": "Plot No 2, Survey No 64, HITEC City, Madhapur, Hyderabad",
                "landmark": "Near Inorbit Mall",
                "google_maps_url": "https://maps.google.com/?q=Lemon+Tree+Premier+Hitec+City",
                "room_category": "Standard Single",
                "nightly_rate": 1450.00,  # Below Cat A Single cap of ₹1,500
                "amenities": ["Wi-Fi", "Breakfast", "AC"],
            },
            {
                "hotel_name": "Radisson Hyderabad Hitec City",
                "address": "Gachibowli Miyapur Road, Hyderabad",
                "landmark": "Near DLF Cyber City",
                "google_maps_url": "https://maps.google.com/?q=Radisson+Hitec+City",
                "room_category": "Deluxe Single",
                "nightly_rate": 1500.00,  # Exactly at Cat A Single cap of ₹1,500
                "amenities": ["Wi-Fi", "Breakfast", "Gym", "AC"],
            },
        ]
    }
    vendor_prop_resp = client.post(
        f"/requests/{hotel_req_id}/hotel-proposals",
        json=proposals_payload,
        headers=vendor_h,
    )
    assert vendor_prop_resp.status_code == status.HTTP_201_CREATED
    proposals = vendor_prop_resp.json()
    assert len(proposals) == 2
    assert all(p["is_policy_compliant"] for p in proposals)
    chosen_proposal_id = proposals[0]["id"]

    # Employee selects preferred proposal (Option 1: Lemon Tree Premier)
    select_resp = client.post(
        f"/requests/{hotel_req_id}/hotel-proposals/{chosen_proposal_id}/select",
        json={"notes": "Closer to campaign field office"},
        headers=amit_h,
    )
    assert select_resp.status_code == status.HTTP_200_OK
    assert select_resp.json()["status"] == HotelProposalStatus.SELECTED.value

    # -------------------------------------------------------------------------
    # Step 5: Strict Safety Constraint - Cross-Gender Co-Stay Block
    # Attempting to pair male Deepak with female Kavita must be strictly rejected
    # -------------------------------------------------------------------------
    from app.services import costay
    user_deepak = db.get(User, env["emp_deepak"].id)
    user_kavita = db.get(User, env["emp_kavita"].id)
    user_admin = db.get(User, env["admin"].id)
    # Create travellers in db for pairing test
    t_male = RequestTraveller(request_id=hotel_req_id, user=user_deepak, status=TravellerStatus.APPROVED)
    t_female = RequestTraveller(request_id=hotel_req_id, user=user_kavita, status=TravellerStatus.APPROVED)
    db.add_all([t_male, t_female])
    db.flush()

    with pytest.raises(Exception) as excinfo:
        costay.pair(t_male, t_female, confirmed_by=user_admin)
    assert "Shared rooms are strictly for colleagues of the same gender" in str(excinfo.value)

    # -------------------------------------------------------------------------
    # Step 6: Local Cab Request with Telemetry & 80 KM Contract Package Audit
    # -------------------------------------------------------------------------
    cab_payload = {
        "request_type": "LOCAL_CAB",
        "project_id": env["campaign_id"],
        "priority": "HIGH",
        "travel_reason": "Full day field deployment across assembly constituencies",
        "origin": "Hitec City Campaign Office",
        "destination": "Secunderabad Constituency Field Area",
        "origin_state": "Telangana",
        "pickup_city": "Hyderabad",
        "destination_state": "Telangana",
        "drop_city": "Hyderabad",
        "start_at": f"{t_start}T08:00:00",
        "end_at": f"{t_start}T20:00:00",
        "cab_trip": "LOCAL",
        "cab_distance_km": 40,
        "cab_type": "SEDAN",
        "traveller_ids": [env["emp_amit"].id],
        "is_draft": False,
    }
    cab_resp = client.post("/requests", json=cab_payload, headers=amit_h)
    assert cab_resp.status_code == status.HTTP_201_CREATED
    cab_req_id = cab_resp.json()["id"]
    cab_traveller_id = cab_resp.json()["travellers"][0]["id"]

    # Manager approves cab request
    client.post(
        f"/requests/{cab_req_id}/recommendation",
        json={"recommendation": "RECOMMENDED", "comment": "Full day cab required for field duty"},
        headers=manager_h,
    )
    # Admin approves and assigns vendor
    client.post(
        f"/requests/{cab_req_id}/travellers/{cab_traveller_id}/decide",
        json={
            "to_status": "APPROVED",
            "reason": "Field cab approved",
            "assigned_vendor_id": env["vendor_id"],
        },
        headers=admin_h,
    )

    # Vendor assigns driver & vehicle details
    dispatch_cab_resp = client.put(
        f"/requests/{cab_req_id}/cab-booking",
        json={
            "booked_cab_type": "SEDAN",
            "vehicle_number": "TS 09 EA 4455",
            "driver_name": "Ramesh Goud",
            "driver_phone": "9848012345",
        },
        headers=vendor_h,
    )
    assert dispatch_cab_resp.status_code == status.HTTP_200_OK

    # In-Transit Cab Telemetry: Morning start odometer
    start_cab_resp = client.post(
        f"/requests/{cab_req_id}/cab-telemetry/start",
        json={
            "start_odometer_km": 12450.0,
            "start_odometer_photo_url": "telemetry/odometer_start_12450.jpg",
            "start_gps_lat": 17.385044,
            "start_gps_lng": 78.486671,
            "driver_name": "Ramesh Goud",
            "driver_phone": "9848012345",
            "vehicle_number": "TS 09 EA 4455",
            "vehicle_model": "Maruti Dzire",
        },
        headers=amit_h,
    )
    assert start_cab_resp.status_code == status.HTTP_201_CREATED
    telemetry_id = start_cab_resp.json()["id"]

    # Evening end odometer: 12,545 km (Total 95 km; Contract package is 80 km; Excess is 15 km)
    end_cab_resp = client.post(
        f"/requests/{cab_req_id}/cab-telemetry/{telemetry_id}/end",
        json={
            "end_odometer_km": 12545.0,
            "end_odometer_photo_url": "telemetry/odometer_end_12545.jpg",
            "end_gps_lat": 17.385050,
            "end_gps_lng": 78.486680,
        },
        headers=amit_h,
    )
    assert end_cab_resp.status_code == status.HTTP_200_OK
    cab_data = end_cab_resp.json()
    assert cab_data["total_km"] == 95.0
    assert cab_data["local_excess_km"] == 15.0  # 95 - 80 = 15
    assert cab_data["is_local_exceeded"] is True

    # Finance/Admin audits cab telemetry
    verify_cab_resp = client.post(
        f"/requests/{cab_req_id}/cab-telemetry/{telemetry_id}/verify",
        json={
            "notes": "15 km excess approved for urgent constituency coordination",
        },
        headers=finance_h,
    )
    assert verify_cab_resp.status_code == status.HTTP_200_OK
    assert verify_cab_resp.json()["status"] == "VERIFIED"
    assert verify_cab_resp.json()["is_verified"] is True

    # -------------------------------------------------------------------------
    # Step 7: Hotel Stay Telemetry: Check-In & Check-Out Folio Upload
    # -------------------------------------------------------------------------
    check_in_resp = client.post(
        f"/requests/{hotel_req_id}/hotel-verification/check-in",
        json={
            "room_number": "Room 408",
            "check_in_lat": 17.4399,
            "check_in_lng": 78.3800,
        },
        headers=amit_h,
    )
    assert check_in_resp.status_code == status.HTTP_201_CREATED
    hotel_verif_id = check_in_resp.json()["id"]

    check_out_resp = client.post(
        f"/requests/{hotel_req_id}/hotel-verification/{hotel_verif_id}/check-out",
        json={
            "folio_bill_url": "telemetry/lemon_tree_invoice_408.pdf",
            "check_out_lat": 17.4399,
            "check_out_lng": 78.3800,
        },
        headers=amit_h,
    )
    assert check_out_resp.status_code == status.HTTP_200_OK
    assert check_out_resp.json()["reimbursement_prompted"] is True

    # -------------------------------------------------------------------------
    # Step 8: Post-Travel Expense Reimbursement & Central Finance Settlement
    # -------------------------------------------------------------------------
    # Employee Amit submits an itemized claim:
    # Day 1 Food: ₹380 (Within ₹400 policy)
    # Day 2 Food: ₹450 (Exceeds ₹400 policy by ₹50)
    # Day 1 Local Transit: ₹140 (Unbilled auto, within ₹150 cap)
    # Total claimed: 380 + 450 + 140 = ₹970
    claim_payload = {
        "project_id": env["campaign_id"],
        "request_id": hotel_req_id,
        "destination_city": "Hyderabad",
        "travel_start_date": str(t_start),
        "travel_end_date": str(t_end),
        "items": [
            {
                "expense_date": str(t_start),
                "category": "FOOD",
                "claimed_amount": 380.0,
                "vendor_merchant_name": "Ulavacharu Restaurant",
                "bill_number": "UC-8812",
                "receipt_url": "receipts/food_day1.jpg",
                "description": "Lunch and dinner on duty",
            },
            {
                "expense_date": str(t_start + timedelta(days=1)),
                "category": "FOOD",
                "claimed_amount": 450.0,
                "vendor_merchant_name": "Paradise Biryani Hitec",
                "bill_number": "PB-9912",
                "receipt_url": "receipts/food_day2.jpg",
                "description": "Dinner with campaign staff",
            },
            {
                "expense_date": str(t_start),
                "category": "LOCAL_TRANSIT",
                "claimed_amount": 140.0,
                "vendor_merchant_name": "Auto Rickshaw",
                "description": "Metro station to hotel auto",
            },
        ],
    }
    submit_claim_resp = client.post("/expenses", json=claim_payload, headers=amit_h)
    assert submit_claim_resp.status_code == status.HTTP_201_CREATED
    claim = submit_claim_resp.json()
    claim_id = claim["id"]
    assert claim["total_claimed_amount"] == 970.0
    assert claim["food_excess_amount"] == 50.0  # 450 - 400 = 50 excess on Day 2
    item2_id = claim["items"][1]["id"]

    # Manager L1 Endorsement
    endorse_resp = client.post(
        f"/expenses/{claim_id}/endorse",
        json={"endorsed": True, "notes": "Verified official travel duties in Hyderabad"},
        headers=manager_h,
    )
    assert endorse_resp.status_code == status.HTTP_200_OK
    assert endorse_resp.json()["status"] == ExpenseStatus.MANAGER_ENDORSED.value

    # Finance L2 Audit: Deducts the ₹50 food excess on Day 2
    # Approved amount: ₹970 - ₹50 = ₹920
    audit_resp = client.post(
        f"/expenses/{claim_id}/audit",
        json={
            "decision": "PARTIALLY_APPROVED",
            "notes": "Approved with policy deduction of ₹50 exceeding daily ₹400 food ceiling",
            "deductions": [
                {
                    "item_id": item2_id,
                    "deducted_amount": 50.0,
                    "deduction_reason": "Policy ceiling cap of ₹400 per day on food allowance",
                }
            ],
        },
        headers=finance_h,
    )
    assert audit_resp.status_code == status.HTTP_200_OK
    audited_claim = audit_resp.json()
    assert audited_claim["status"] == ExpenseStatus.PARTIALLY_APPROVED.value
    assert audited_claim["total_approved_amount"] == 920.0
    assert audited_claim["total_deducted_amount"] == 50.0

    # Finance Bank Payout Settlement: Record NEFT & Bank UTR
    settle_resp = client.post(
        f"/expenses/{claim_id}/settle",
        json={
            "payment_mode": "NEFT",
            "bank_utr_number": "HDFC0001234567890",
            "payment_date": str(date.today()),
        },
        headers=finance_h,
    )
    assert settle_resp.status_code == status.HTTP_200_OK
    settled = settle_resp.json()
    assert settled["status"] == ExpenseStatus.SETTLED.value
    assert settled["bank_utr_number"] == "HDFC0001234567890"
    assert settled["bank_utr_number"] == "HDFC0001234567890"

    # Verify CSV accounting reconciliation export
    export_resp = client.get("/expenses/export", headers=finance_h)
    assert export_resp.status_code == status.HTTP_200_OK
    assert "text/csv" in export_resp.headers["content-type"]
    assert "HDFC0001234567890" in export_resp.text
    assert claim["claim_code"] in export_resp.text

    # -------------------------------------------------------------------------
    # Step 9: Super Admin & System Admin Equivalence & Tamper-Evident Activity Ledger
    # -------------------------------------------------------------------------
    from app.core.enums import ROLE_RANK
    assert ROLE_RANK[Role.SUPER_ADMIN] == 5
    assert ROLE_RANK[Role.SYSTEM_ADMIN] == 5
    assert ROLE_RANK[Role.ADMIN] == 4
    assert ROLE_RANK[Role.FINANCE] == 3
    assert ROLE_RANK[Role.MANAGER] == 2
    assert ROLE_RANK[Role.VENDOR] == 1
    assert ROLE_RANK[Role.GROUND_STAFF] == 0

    # Verify audit logs captured key actions in the ledger
    audit_entries = (
        db.execute(
            select(AuditLog)
            .where(AuditLog.tenant_id == TENANT)
            .order_by(AuditLog.id.desc())
        )
        .scalars()
        .all()
    )
    actions = {e.action for e in audit_entries}
    assert AuditAction.APPROVE in actions
    assert AuditAction.SUBMIT_EXPENSE in actions
    assert AuditAction.ENDORSE_EXPENSE in actions
    assert AuditAction.AUDIT_EXPENSE in actions
    assert AuditAction.SETTLE_EXPENSE in actions
    assert AuditAction.CAPTURE_ODOMETER in actions
    assert AuditAction.CHECK_IN in actions
    assert AuditAction.CHECK_OUT in actions
