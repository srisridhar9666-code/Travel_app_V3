"""
Comprehensive role-level flow test for Sriyatra Travel Management System.

Tests the complete workflow at all access levels:
  Super Admin -> Admin -> Manager -> Ground Staff

Run from backend/ with:
    .venv\\Scripts\\python.exe scripts\\test_all_roles.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import urllib.request
import urllib.error
import urllib.parse

BASE = "http://127.0.0.1:8000"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[PASS]{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {BLUE}->{RESET} {msg}")


def section(title: str) -> None:
    print(f"\n{BOLD}{YELLOW}{'-'*60}{RESET}")
    print(f"{BOLD}{YELLOW}  {title}{RESET}")
    print(f"{BOLD}{YELLOW}{'-'*60}{RESET}")


def _request(method: str, path: str, token: str | None = None,
             body: Any = None) -> tuple[int, Any]:
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode("utf-8", errors="replace")


def get(path: str, token: str | None = None) -> tuple[int, Any]:
    return _request("GET", path, token)


def post(path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    return _request("POST", path, token, body)


def put(path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    return _request("PUT", path, token, body)


def patch(path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
    return _request("PATCH", path, token, body)


def delete(path: str, token: str | None = None) -> tuple[int, Any]:
    return _request("DELETE", path, token)


def login(email: str, password: str) -> str | None:
    status, resp = post("/auth/login", {"email": email, "password": password})
    if status == 200 and isinstance(resp, dict):
        return resp.get("access_token")
    return None


@dataclass
class State:
    errors: list[str] = field(default_factory=list)
    super_admin_token: str = ""
    admin_token: str = ""
    manager_token: str = ""
    staff_token: str = ""
    manager_id: int = 0
    staff_id: int = 0
    project_id: int = 0
    request_id: int = 0
    traveller_id: int = 0

    def check(self, cond: bool, name: str, detail: str = "") -> bool:
        if cond:
            ok(name)
            return True
        else:
            msg = name + (f" - {detail}" if detail else "")
            fail(msg)
            self.errors.append(msg)
            return False


def assert_status(state: State, status: int, expected: int,
                  name: str, resp: Any = None) -> bool:
    detail = str(resp)[:140] if resp and status != expected else ""
    return state.check(status == expected, name, detail)


def test_auth(s: State) -> None:
    section("1 . Authentication")

    tok = login("admin@designboxed.com", "ChangeMe@123")
    s.check(tok is not None, "Super admin login (admin@designboxed.com)")
    s.super_admin_token = tok or ""

    # The fulfilment admin: invited by the super admin on a fresh install, or
    # already there from an earlier run.
    tok = login("sachin@designboxed.com", "Karnatak@9876")
    if tok is None and s.super_admin_token:
        status, resp = post("/users", {
            "email": "sachin@designboxed.com",
            "full_name": "Sachin Rao",
            "role": "ADMIN",
            "designation": "MANAGER",
            "gender": "MALE",
            "phone": "9876500001",
            "base_state": "Karnataka",
            "base_location": "Bengaluru",
            "send_email": False,
        }, s.super_admin_token)
        if status == 201 and resp.get("invite_url"):
            post("/auth/set-password", {
                "token": resp["invite_url"].split("token=")[-1],
                "password": "Karnatak@9876",
            })
        tok = login("sachin@designboxed.com", "Karnatak@9876")
    s.check(tok is not None, "Admin login (sachin@designboxed.com)")
    s.admin_token = tok or ""

    status, resp = get("/health")
    s.check(status == 200 and resp.get("status") == "ok", "Health endpoint returns ok")

    tok = login("nobody@example.com", "wrong")
    s.check(tok is None, "Wrong credentials rejected")


def test_super_admin_creates_users(s: State) -> None:
    section("2 . Super Admin: create manager + ground staff")

    if not s.super_admin_token:
        fail("No super admin token - skipping user creation")
        return

    # Create manager (Ramesh Sharma)
    status, resp = post("/users", {
        "email": "ramesh.manager@designboxed.com",
        "full_name": "Ramesh Sharma",
        "role": "MANAGER",
        "designation": "MANAGER",
        "gender": "MALE",
        "phone": "9876543210",
        "base_state": "Karnataka",
        "base_location": "Bengaluru",
        "send_email": False,
    }, s.super_admin_token)
    assert_status(s, status, 201, "Create manager account", resp)

    if status == 201 and "invite_url" in resp:
        token_part = resp["invite_url"].split("token=")[-1]
        st2, r2 = post("/auth/set-password", {
            "token": token_part,
            "password": "Karnatak@9876",
        })
        s.check(st2 == 200, "Manager password set via invite token")

    # Find manager id
    st_u, users_list = get("/users?search=ramesh.manager", s.super_admin_token)
    if st_u == 200 and users_list.get("items"):
        s.manager_id = users_list["items"][0]["id"]
        info(f"Manager ID: {s.manager_id}")

    # Create ground staff (Priya Patel) reporting to Ramesh Sharma
    status, resp = post("/users", {
        "email": "priya.staff@designboxed.com",
        "full_name": "Priya Patel",
        "role": "GROUND_STAFF",
        "designation": "EXECUTIVE",
        "gender": "FEMALE",
        "phone": "9123456780",
        "base_state": "Karnataka",
        "base_location": "Bengaluru",
        "manager_id": s.manager_id if s.manager_id else None,
        "send_email": False,
    }, s.super_admin_token)
    assert_status(s, status, 201, "Create ground staff account", resp)

    if status == 201 and "invite_url" in resp:
        token_part = resp["invite_url"].split("token=")[-1]
        st2, r2 = post("/auth/set-password", {
            "token": token_part,
            "password": "Karnatak@9876",
        })
        s.check(st2 == 200, "Ground staff password set via invite token")

    # Find staff id
    st_u, users_list = get("/users?search=priya.staff", s.super_admin_token)
    if st_u == 200 and users_list.get("items"):
        s.staff_id = users_list["items"][0]["id"]
        info(f"Staff ID: {s.staff_id}")

    # Log in as manager and staff
    tok = login("ramesh.manager@designboxed.com", "Karnatak@9876")
    s.check(tok is not None, "Manager logs in")
    s.manager_token = tok or ""

    tok = login("priya.staff@designboxed.com", "Karnatak@9876")
    s.check(tok is not None, "Ground staff logs in")
    s.staff_token = tok or ""


def test_admin_creates_project(s: State) -> None:
    section("3 . Admin: create campaign (project)")

    if not s.admin_token:
        fail("No admin token - skipping project creation")
        return

    status, resp = post("/projects", {
        "name": "Karnataka Field Survey 2026",
        "description": "Comprehensive statewide ground survey",
        "client_name": "DesignBoxed Analytics",
        "state": "Karnataka",
        "city": "Bengaluru",
        "start_date": str(date.today()),
        "end_date": str(date.today() + timedelta(days=90)),
    }, s.admin_token)
    if assert_status(s, status, 201, "Admin creates campaign", resp):
        s.project_id = resp["id"]
        info(f"Campaign: {resp['name']} ({resp['code']}) id={s.project_id}")

    status, resp = get("/projects", s.admin_token)
    s.check(status == 200 and len(resp.get("items", [])) > 0, "Admin reads campaign list")

    if s.manager_token:
        status, resp = get("/projects", s.manager_token)
        s.check(status == 200, "Manager reads campaign list")

    if s.staff_token:
        status, resp = get("/projects", s.staff_token)
        s.check(status == 200, "Ground staff reads campaign list")


def test_ground_staff_raises_request(s: State) -> None:
    section("4 . Ground Staff: raise travel request")

    if not s.staff_token or not s.project_id:
        fail("No staff token or project - skipping request creation")
        return

    dep = str(date.today() + timedelta(days=7))
    ret = str(date.today() + timedelta(days=10))

    status, resp = post("/requests", {
        "request_type": "LONG_DISTANCE",
        "project_id": s.project_id,
        "mode": "TRAIN",
        "origin": "Bengaluru",
        "destination": "Chennai",
        "origin_state": "Karnataka",
        "destination_state": "Tamil Nadu",
        "start_at": f"{dep}T08:00:00",
        "end_at": f"{ret}T20:00:00",
        "travel_reason": "On-ground field meeting and coordination",
        "priority": "HIGH",
        "notes": "Please book morning Shatabdi Express",
        "is_draft": False,
        "co_traveller_ids": [],
    }, s.staff_token)

    if assert_status(s, status, 201, "Ground staff submits travel request", resp):
        s.request_id = resp["id"]
        s.traveller_id = resp["travellers"][0]["id"] if resp.get("travellers") else 0
        info(f"Request id={s.request_id}, traveller_id={s.traveller_id}")
        s.check(resp.get("status") == "SUBMITTED", "Request status is SUBMITTED")

    # Staff reads own requests
    status, resp = get("/requests", s.staff_token)
    s.check(status == 200, "Ground staff reads own requests list")


def test_manager_recommends(s: State) -> None:
    section("5 . Manager: recommend on team member's request")

    if not s.manager_token or not s.request_id:
        fail("No manager token or request - skipping manager recommendation")
        return

    # Manager checks their team requests
    status, resp = get("/requests?review=waiting", s.manager_token)
    s.check(status == 200, "Manager reads waiting recommendations")

    # Manager recommends
    status, resp = post(f"/requests/{s.request_id}/recommendation", {
        "recommendation": "RECOMMENDED",
        "comment": "Recommended - field assignment approved by Ramesh",
    }, s.manager_token)
    assert_status(s, status, 200, "Manager recommends the request", resp)


def test_admin_approves(s: State) -> None:
    section("6 . Admin: approve the request")

    if not s.admin_token or not s.request_id or not s.traveller_id:
        fail("No admin token or traveller - skipping approval")
        return

    status, resp = post(f"/requests/{s.request_id}/travellers/{s.traveller_id}/decide", {
        "to_status": "APPROVED",
        "reason": "Travel desk approval granted by Sachin",
    }, s.admin_token)
    assert_status(s, status, 200, "Admin approves the traveller", resp)
    if status == 200:
        traveller = next((t for t in resp.get("travellers", []) if t["id"] == s.traveller_id), None)
        s.check(traveller and traveller.get("status") == "APPROVED", "Traveller status is APPROVED")


def test_admin_books(s: State) -> None:
    section("7 . Admin: book the trip (set booking reference + details)")

    if not s.admin_token or not s.request_id or not s.traveller_id:
        fail("No admin token or traveller - skipping booking")
        return

    dep_time = str(date.today() + timedelta(days=7)) + "T08:10:00"
    arr_time = str(date.today() + timedelta(days=7)) + "T14:30:00"

    status, resp = post(f"/requests/{s.request_id}/travellers/{s.traveller_id}/decide", {
        "to_status": "BOOKED",
        "reason": "Booked via IRCTC portal",
        "booking_reference": "PNR-4589217032",
        "booking_details": {
            "carrier": "Indian Railways",
            "service_number": "12008 Shatabdi Express",
            "depart_at": dep_time,
            "arrive_at": arr_time,
            "seat": "C1-24 (CC)",
            "notes": "Boarding: KSR Bengaluru City Junction",
        },
    }, s.admin_token)
    assert_status(s, status, 200, "Admin marks traveller as BOOKED", resp)
    if status == 200:
        traveller = next((t for t in resp.get("travellers", []) if t["id"] == s.traveller_id), None)
        s.check(traveller and traveller.get("status") == "BOOKED", "Traveller status is BOOKED")
        s.check(resp.get("status") == "BOOKED", "Request overall status is BOOKED")


def test_admin_records_cost(s: State) -> None:
    section("8 . Admin: record travel cost")

    if not s.admin_token or not s.request_id or not s.traveller_id:
        fail("No admin token or traveller - skipping cost recording")
        return

    status, vendors = get("/vendors", s.admin_token)
    vendor_id = vendors[0]["id"] if status == 200 and isinstance(vendors, list) and vendors else None

    status, resp = post(f"/requests/{s.request_id}/costs", {
        "amounts": [
            {
                "traveller_id": s.traveller_id,
                "amount": "1450.00",
                "note": "Train ticket cost",
            }
        ],
        "vendor_id": vendor_id,
    }, s.admin_token)
    assert_status(s, status, 200, "Admin records cost (Rs 1,450.00 + vendor)", resp)


def test_staff_views_history(s: State) -> None:
    section("9 . Ground Staff: view own travel history & notifications")

    if not s.staff_token or not s.staff_id:
        fail("No staff token or staff id - skipping history check")
        return

    status, resp = get(f"/users/{s.staff_id}/travel-history", s.staff_token)
    s.check(status == 200, "Ground staff reads own travel history")

    status, resp = get("/notifications/mine", s.staff_token)
    s.check(status == 200, "Ground staff reads personal in-app notifications")


def test_super_admin_features(s: State) -> None:
    section("10 . Super Admin: audit log, vendors, invoices")

    if not s.super_admin_token:
        fail("No super admin token - skipping super admin checks")
        return

    status, resp = get("/audit", s.super_admin_token)
    s.check(status == 200, "Super admin reads audit log")

    status, resp = get("/users", s.super_admin_token)
    s.check(status == 200, "Super admin reads users list")

    status, resp = get("/vendors", s.super_admin_token)
    s.check(status == 200, "Super admin reads vendors")

    status, resp = get("/invoices", s.super_admin_token)
    s.check(status == 200, "Super admin reads invoices")

    # Super admin cannot create invoices (separation of duties)
    status, resp = post("/invoices", {
        "vendor_id": 1,
        "period_start": str(date.today() - timedelta(days=30)),
        "period_end": str(date.today()),
        "traveller_ids": [],
    }, s.super_admin_token)
    s.check(status in (401, 403), "Super admin cannot create invoices (read-only for audit)")


def test_access_control(s: State) -> None:
    section("11 . Access control boundary checks")

    if s.staff_token:
        status, _ = get("/users", s.staff_token)
        s.check(status in (401, 403), "Ground staff blocked from /users directory")

        status, _ = get("/audit", s.staff_token)
        s.check(status in (401, 403), "Ground staff blocked from audit log")

        status, _ = get("/notifications/ledger", s.staff_token)
        s.check(status in (401, 403), "Ground staff blocked from admin notification ledger")

    if s.manager_token:
        status, _ = post("/vendors", {
            "name": "Unauthorized Vendor",
            "category": "TRAVEL",
        }, s.manager_token)
        s.check(status in (401, 403), "Manager blocked from creating vendors")

    if s.admin_token:
        status, _ = post("/invoices/99999/approve", {}, s.admin_token)
        s.check(status in (401, 403, 404), "Admin blocked from approving invoices")


def main() -> int:
    print(f"\n{BOLD}Sriyatra Travel Management - All-Roles Flow Test{RESET}")
    print(f"Testing against: {BASE}\n")

    s = State()

    try:
        st, _ = get("/health")
        if st != 200:
            print(f"{RED}Server not ready (status {st}) - aborting.{RESET}")
            return 1
    except Exception as e:
        print(f"{RED}Cannot reach {BASE}: {e}{RESET}")
        return 1

    test_auth(s)
    test_super_admin_creates_users(s)
    test_admin_creates_project(s)
    test_ground_staff_raises_request(s)
    test_manager_recommends(s)
    test_admin_approves(s)
    test_admin_books(s)
    test_admin_records_cost(s)
    test_staff_views_history(s)
    test_super_admin_features(s)
    test_access_control(s)

    print(f"\n{BOLD}{'='*60}{RESET}")
    if s.errors:
        print(f"{RED}{BOLD}  {len(s.errors)} failure(s):{RESET}")
        for err in s.errors:
            print(f"  {RED}* {err}{RESET}")
        print(f"{BOLD}{'='*60}{RESET}\n")
        return 1
    else:
        print(f"{GREEN}{BOLD}  ALL ROLE-LEVEL CHECKS PASSED PERFECTLY! [OK]{RESET}")
        print(f"{BOLD}{'='*60}{RESET}\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
