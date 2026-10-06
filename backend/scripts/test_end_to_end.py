"""
The whole system, once, on a database migrated from empty.

The eight phase smokes each prove their own slice against whatever the dev
database already contains. This proves the things none of them can:

* **that the product deploys from nothing** - every migration in order against an
  empty schema, then a bootstrap admin, then real work;
* **that one continuous journey crosses every phase boundary** - onboarding, a
  group request, a conflict, a co-stay offer, an edit, selective approval, a
  ticket read by the model, a confirmation email, a cost, and a report - with
  each step consuming what the last one produced rather than its own fixtures;
* **that the numbers agree across screens.** The queue count, the analytics
  total and the requests list are three different queries over the same rows,
  and nothing until now has checked they say the same thing;
* **that the permission matrix holds** for every role against every sensitive
  endpoint, rather than the handful each phase happened to test.

Running it:

    # 1. a clean database
    DROP DATABASE IF EXISTS travel_ops_e2e;
    CREATE DATABASE travel_ops_e2e CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;

    # 2. migrate it from zero
    cd backend
    DATABASE_URL='...travel_ops_e2e...' ./.venv/Scripts/python.exe -m alembic upgrade head

    # 3. an API pointed at it, on its own port
    DATABASE_URL='...travel_ops_e2e...' ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001

    # 4. this
    ./.venv/Scripts/python.exe scripts/test_end_to_end.py

It calls the real extraction model, so it costs a few seconds and a few tokens.
It sends no real email: every account it creates is invented, and EMAIL_ALLOWLIST
keeps those SUPPRESSED - which is itself asserted rather than assumed.
"""
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "http://127.0.0.1:8001"
c = httpx.Client(base_url=API, timeout=120)

# Every decision carries a reason now - an approval as much as a rejection. The
# journey below is about who decides what, not the wording, so a decision sent
# without one gets a plain sentence here; the checks about override reasons
# are unaffected, since those are a separate field.
_post = c.post


def _post_with_reasons(url, *args, **kwargs):
    body = kwargs.get("json")
    if "/decide" in str(url) and isinstance(body, dict):
        for decision in body.get("decisions", [body]):
            if isinstance(decision, dict) and "to_status" in decision:
                decision.setdefault("reason", "Decided on the end-to-end run")
    return _post(url, *args, **kwargs)


c.post = _post_with_reasons
failures = []
notes = []

STAMP = uuid.uuid4().hex[:6]
PASSWORD = "FieldStaff@2026"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# The trip everything in this script is about.
TRIP_DAY = date.today() + timedelta(days=21)
CITY = "Nagpur"


def check(label, condition, detail=""):
    mark = "PASS" if condition else "FAIL"
    if not condition:
        failures.append(label)
    print(f"{mark}  {label}{(' -> ' + str(detail)) if detail else ''}")


def note(text):
    notes.append(text)
    print(f"NOTE  {text}")


def section(title):
    print(f"\n{'=' * 68}\n  {title}\n{'=' * 68}")


def at(day: date, hour: int, minute: int = 0) -> str:
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute).isoformat()


def money(v) -> Decimal:
    return Decimal(str(v))


# =========================================================================
section("1. A system nobody has used yet")
# =========================================================================
r = c.get("/health")
check("the API is up and the database answers", r.json()["database"] == "ok", r.json())

r = c.get("/health/gemini")
gemini_ok = r.json().get("ok") is True
check("the extraction model is reachable", gemini_ok, r.json().get("detail", "ok"))

r = c.post("/auth/login", json={"email": "admin@designboxed.com", "password": "ChangeMe@123"})
check("the bootstrap admin can sign in on a fresh install", r.status_code == 200, r.status_code)
ADMIN = {"Authorization": f"Bearer {r.json()['access_token']}"}

r = c.get("/audit", headers=ADMIN, params={"page_size": 50})
check(
    "and its own creation is already in the ledger",
    any("Bootstrap" in e["summary"] for e in r.json()["items"]),
    [e["summary"][:50] for e in r.json()["items"]],
)

r = c.get("/requests", headers=ADMIN, params={"mine": False})
check("there are no requests yet", r.json()["total"] == 0, r.json()["total"])

r = c.get("/analytics", headers=ADMIN)
check("and the reports say zero rather than breaking", r.json()["overview"]["spent"] == "0.00", r.json()["overview"])
check("with nothing uncosted to chase", r.json()["overview"]["uncosted"] == 0)

r = c.get("/requests/queue/counts", headers=ADMIN)
check("the queue is empty", r.json()["awaiting"] == 0, r.json())

# =========================================================================
section("2. Onboarding a field team")
# =========================================================================
def onboard(name, gender, designation="EXECUTIVE", role="GROUND_STAFF"):
    address = f"{name.split()[0].lower()}.{STAMP}@designboxed.com"
    r = c.post("/users", headers=ADMIN, json={
        "email": address, "full_name": name, "role": role,
        "designation": designation, "gender": gender, "base_location": "Hyderabad",
    })
    if r.status_code != 201:
        check(f"onboard {name}", False, r.text[:200])
        sys.exit(1)
    invite = r.json()["invite_url"]
    token = invite.rsplit("=", 1)[-1]

    # The invite flow as a real joiner experiences it.
    preview = c.get(f"/auth/token/{token}")
    if preview.status_code != 200:
        check(f"{name}'s invite link previews", False, preview.text[:150])
    c.post("/auth/set-password", json={"token": token, "password": PASSWORD})
    login = c.post("/auth/login", json={"email": address, "password": PASSWORD})
    return (
        login.json()["user"]["id"],
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        address,
    )


ravi_id, RAVI, ravi_email = onboard("Ravi Kumar", "MALE", "TEAM_LEAD")
arjun_id, ARJUN, arjun_email = onboard("Arjun Nair", "MALE")
meera_id, MEERA, meera_email = onboard("Meera Iyer", "FEMALE", "MANAGER")
priya_id, PRIYA, priya_email = onboard("Priya Shah", "FEMALE", "MANAGER", role="ADMIN")

check("four people are onboarded through the invite flow", all([ravi_id, arjun_id, meera_id, priya_id]))

r = c.get("/users", headers=ADMIN)
check("the directory shows all of them plus the bootstrap admin", r.json()["total"] == 5, r.json()["total"])

# An invite is single use - the most important property of the whole flow.
r = c.post("/users", headers=ADMIN, json={
    "email": f"once.{STAMP}@designboxed.com", "full_name": "Single Use",
    "role": "GROUND_STAFF", "gender": "FEMALE",
})
once_token = r.json()["invite_url"].rsplit("=", 1)[-1]
first = c.post("/auth/set-password", json={"token": once_token, "password": PASSWORD})
second = c.post("/auth/set-password", json={"token": once_token, "password": "Different@2026"})
check("an invite link works once", first.status_code == 200, first.status_code)
# 404, not 400, and deliberately: the endpoint does not distinguish "already
# used" from "never existed", so a spent link leaks nothing about whether an
# account is real.
check("and is dead the second time", second.status_code == 404, second.status_code)
check(
    "without saying which kind of dead",
    "invalid or has expired" in second.json().get("detail", ""),
    second.json().get("detail"),
)

r = c.post("/projects", headers=ADMIN, json={
    "name": "Central India Retail Audit", "code": f"CIR-{STAMP.upper()}",
    "client_name": "Acme Retail", "state": "Maharashtra",
    "start_date": date.today().isoformat(),
    "end_date": (date.today() + timedelta(days=120)).isoformat(),
})
check("a campaign is created", r.status_code == 201, r.text[:200])
project_id = r.json()["id"]
project_code = r.json()["code"]

# =========================================================================
section("3. Ravi raises a group request, and is warned")
# =========================================================================
r = c.post("/requests", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": CITY,
    "start_at": at(TRIP_DAY, 7), "end_at": at(TRIP_DAY, 9),
    "traveller_ids": [arjun_id, meera_id],
    "notes": "Store audit, three sites",
})
check("a three-person request is raised", r.status_code == 201, r.text[:300])
flight = r.json()
flight_id = flight["id"]
check("everyone gets their own row", len(flight["travellers"]) == 3, len(flight["travellers"]))
check("it is editable while everyone is pending", flight["is_editable"] is True)
check("nothing clashes on an empty calendar", flight["conflicts"] == [], flight["conflicts"])

# The airport cab on the same day must NOT warn - the rule the SOW got wrong.
r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "LOCAL_CAB", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "origin_state": "Telangana", "pickup_city": "Hyderabad", "destination_state": "Telangana", "drop_city": "Hyderabad",
    "origin": "Banjara Hills", "destination": "RGIA Airport",
    "start_at": at(TRIP_DAY, 4), "end_at": at(TRIP_DAY, 6),
})
check("a cab to the airport for that flight does NOT warn", r.json()["conflicts"] == [], r.json()["conflicts"])

r = c.post("/requests", headers=RAVI, json={
    "request_type": "LOCAL_CAB", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign",
    "origin_state": "Telangana", "pickup_city": "Hyderabad", "destination_state": "Telangana", "drop_city": "Hyderabad",
    "origin": "Banjara Hills", "destination": "RGIA Airport",
    "start_at": at(TRIP_DAY, 4), "end_at": at(TRIP_DAY, 6),
})
cab_id = r.json()["id"]
check("so the cab is raised cleanly", r.status_code == 201 and r.json()["conflicts"] == [])

# A genuine clash: a second flight overlapping the first.
r = c.post("/requests", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "TRAIN",
    "origin": "Hyderabad", "destination": "Pune",
    "start_at": at(TRIP_DAY, 8), "end_at": at(TRIP_DAY, 20),
})
clash = r.json()
check("a genuinely overlapping journey IS flagged", len(clash["conflicts"]) == 1, clash["conflicts"])
check("but it is still accepted - a warning, not a block", r.status_code == 201, r.status_code)
check("and typed as overlapping travel", clash["conflicts"][0]["kind"] == "OVERLAPPING_TRAVEL")
clash_id = clash["id"]

# =========================================================================
section("4. Hotels, and who may share a room")
# =========================================================================
r = c.post("/requests", headers=ARJUN, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "hotel_city": CITY,
    "check_in": TRIP_DAY.isoformat(), "check_out": (TRIP_DAY + timedelta(days=3)).isoformat(),
})
check("Arjun books a hotel first", r.status_code == 201, r.text[:200])

# Ravi is the same gender: he should be offered a share.
r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "hotel_city": CITY,
    "check_in": TRIP_DAY.isoformat(), "check_out": (TRIP_DAY + timedelta(days=3)).isoformat(),
})
offers = r.json()["costay_matches"]
check("Ravi is offered a share with Arjun", len(offers) == 1 and offers[0]["full_name"] == "Arjun Nair", offers)
check("the offer names their designation", offers and offers[0]["designation"] == "EXECUTIVE")

# Meera is a different gender: she must not be.
r = c.post("/requests/check", headers=MEERA, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "hotel_city": CITY,
    "check_in": TRIP_DAY.isoformat(), "check_out": (TRIP_DAY + timedelta(days=3)).isoformat(),
})
check("Meera is offered nothing, silently", r.json()["costay_matches"] == [], r.json()["costay_matches"])

r = c.post("/requests", headers=RAVI, json={
    "request_type": "HOTEL", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "hotel_city": CITY,
    "check_in": TRIP_DAY.isoformat(), "check_out": (TRIP_DAY + timedelta(days=3)).isoformat(),
})
stay = r.json()
stay_id = stay["id"]
ravi_bed = stay["travellers"][0]["id"]

r = c.post(f"/requests/{stay_id}/room-sharing", headers=RAVI, json={
    "traveller_id": ravi_bed, "choice": "SHARE_EXISTING", "share_with_user_id": arjun_id,
})
check("Ravi asks to share", r.status_code == 200, r.text[:200])
check("but it is not confirmed by his asking", r.json()["travellers"][0]["share_confirmed"] is False)

notices = c.get("/notifications/mine", headers=ARJUN).json()
check("Arjun is told someone asked", any(n["kind"] == "COSTAY_REQUESTED" for n in notices), len(notices))

r = c.post(f"/requests/{stay_id}/travellers/{ravi_bed}/confirm-share", headers=ADMIN)
check("an admin confirms it", r.status_code == 200 and r.json()["travellers"][0]["share_confirmed"] is True)

# =========================================================================
section("5. Ravi changes his mind, and it is recorded")
# =========================================================================
r = c.put(f"/requests/{flight_id}", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": CITY,
    "start_at": at(TRIP_DAY, 11), "end_at": at(TRIP_DAY, 13),
    "traveller_ids": [arjun_id, meera_id],
    "notes": "Store audit, three sites - moved to the later flight",
})
check("the flight can be edited while everyone is pending", r.status_code == 200, r.text[:300])
check("and the edit counter moves", r.json()["edit_count"] == 1, r.json()["edit_count"])

history = c.get(f"/requests/{flight_id}/revisions", headers=RAVI).json()
check("the history has the submission and the edit", len(history) == 2, len(history))
check("newest first", history[0]["revision_number"] == 2)
check(
    "and it records what changed, before and after",
    history[0]["changes"]["start_at"]["from"].endswith("07:00:00")
    and history[0]["changes"]["start_at"]["to"].endswith("11:00:00"),
    history[0]["changes"].get("start_at"),
)

# Moving the flight later now clashes with the Pune train differently - recheck.
r = c.get(f"/requests/{flight_id}", headers=RAVI)
check("the conflict is recomputed after the edit", len(r.json()["conflicts"]) >= 1, r.json()["conflicts"])

# =========================================================================
section("6. Priya works the queue")
# =========================================================================
r = c.get("/requests/queue/counts", headers=PRIYA)
counts = r.json()
check("an ADMIN (not just SYSTEM_ADMIN) can work the queue", r.status_code == 200, r.status_code)
check("and sees everything awaiting a decision", counts["awaiting"] == 5, counts)
check("including the one with a clash", counts["with_conflicts"] >= 1, counts)
check("and the one that was edited", counts["edited"] >= 1, counts)

r = c.get(f"/requests/{flight_id}/revisions", headers=PRIYA)
check("she can read the edit history before deciding", r.status_code == 200 and len(r.json()) == 2)

by_user = {t["user_id"]: t["id"] for t in c.get(f"/requests/{flight_id}", headers=PRIYA).json()["travellers"]}

# Selective approval: two yes, one no.
# Ravi is on both the Nagpur flight and the Pune train by now, so approving
# him costs a typed reason. Prove the batch refuses *as a whole* first - a
# partially applied batch would be the worst possible outcome.
attempt = c.post(f"/requests/{flight_id}/decide", headers=PRIYA, json={"decisions": [
    {"traveller_id": by_user[ravi_id], "to_status": "APPROVED"},
    {"traveller_id": by_user[arjun_id], "to_status": "APPROVED"},
    {"traveller_id": by_user[meera_id], "to_status": "REJECTED",
     "reason": "Needed at the Pune site that week"},
]})
check("a batch containing an unjustified override is refused", attempt.status_code == 409, attempt.status_code)
untouched = c.get(f"/requests/{flight_id}", headers=PRIYA).json()
check(
    "and nothing in it was applied - not even the two that were fine",
    all(t["status"] == "PENDING" for t in untouched["travellers"]),
    [t["status"] for t in untouched["travellers"]],
)

r = c.post(f"/requests/{flight_id}/decide", headers=PRIYA, json={"decisions": [
    {"traveller_id": by_user[ravi_id], "to_status": "APPROVED",
     "conflict_override_reason": "Pune leg is being cancelled, this one stands"},
    {"traveller_id": by_user[arjun_id], "to_status": "APPROVED"},
    {"traveller_id": by_user[meera_id], "to_status": "REJECTED",
     "reason": "Needed at the Pune site that week"},
]})
check("with the reason supplied, she approves two and rejects one in one action", r.status_code == 200, r.text[:300])
decided = {t["user_id"]: t["status"] for t in r.json()["travellers"]}
check("each person gets their own answer", decided == {ravi_id: "APPROVED", arjun_id: "APPROVED", meera_id: "REJECTED"}, decided)
check("the request reads as partly approved", r.json()["status"] == "APPROVED", r.json()["status"])
check("and is now locked against edits", r.json()["is_editable"] is False)

r = c.put(f"/requests/{flight_id}", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "FLIGHT",
    "origin": "Hyderabad", "destination": "Bhopal", "start_at": at(TRIP_DAY, 11),
})
check("Ravi can no longer change it", r.status_code == 409, r.status_code)

meera_notices = c.get("/notifications/mine", headers=MEERA).json()
check("Meera is told she was rejected", any(n["kind"] == "REQUEST_REJECTED" for n in meera_notices))
check("with the reason", any("Pune site" in n["body"] for n in meera_notices))

# Approving over the known clash must cost a typed reason.
clash_tid = c.get(f"/requests/{clash_id}", headers=PRIYA).json()["travellers"][0]["id"]
r = c.post(f"/requests/{clash_id}/travellers/{clash_tid}/decide", headers=PRIYA,
           json={"to_status": "APPROVED"})
check("approving over a clash is blocked without a reason", r.status_code == 409, r.status_code)

r = c.post(f"/requests/{clash_id}/travellers/{clash_tid}/decide", headers=PRIYA,
           json={"to_status": "APPROVED", "conflict_override_reason": "Client moved the Nagpur visit"})
check("and allowed with one", r.status_code == 200, r.text[:200])

ledger = c.get("/audit", headers=ADMIN, params={"page_size": 200}).json()["items"]
override = [e for e in ledger if e["action"] == "OVERRIDE_CONFLICT"]
check("which is recorded as an override", len(override) >= 1, len(override))
check(
    "carrying the typed reason",
    any("Nagpur visit" in (e["reason"] or "") for e in override),
    [e["reason"] for e in override],
)
check(
    "and one row per override, not one per batch",
    len(override) == 2,
    [e["summary"][:60] for e in override],
)

# =========================================================================
section("7. A ticket, read and confirmed")
# =========================================================================
if not gemini_ok:
    note("skipping the extraction journey - the model is unreachable")
    ticket_confirmed = False
else:
    r = c.post(
        f"/requests/{flight_id}/tickets", headers=PRIYA,
        params={"traveller_id": by_user[ravi_id]},
        files={"file": ("flight_ticket.png", (FIXTURES / "flight_ticket.png").read_bytes(), "image/png")},
    )
    check("a ticket uploads and is read", r.status_code == 201, r.text[:300])
    ticket = r.json()
    check("the model proposed a reference", bool(ticket["booking_reference"]), ticket["booking_reference"])

    # The whole point of B3.
    still = c.get(f"/requests/{flight_id}", headers=PRIYA).json()
    ravi_row = next(t for t in still["travellers"] if t["user_id"] == ravi_id)
    check("but reading it booked nobody", ravi_row["status"] == "APPROVED", ravi_row["status"])
    check("and set no reference", ravi_row["booking_reference"] is None)

    before = len(c.get("/notifications/mine", headers=RAVI).json())
    r = c.post(f"/tickets/{ticket['id']}/confirm", headers=PRIYA, json={
        "booking_reference": ticket["booking_reference"],
        "carrier": ticket["carrier"], "service_number": ticket["service_number"],
        "cost_amount": "4850.00",
    })
    check("a human confirming it books the traveller", r.status_code == 200, r.text[:300])
    ticket_confirmed = True

    after = c.get(f"/requests/{flight_id}", headers=PRIYA).json()
    ravi_row = next(t for t in after["travellers"] if t["user_id"] == ravi_id)
    check("only now is he BOOKED", ravi_row["status"] == "BOOKED", ravi_row["status"])
    check("with the confirmed reference", ravi_row["booking_reference"] == ticket["booking_reference"])
    check("and the cost recorded in the same step", money(ravi_row["cost_amount"]) == money("4850.00"), ravi_row["cost_amount"])

    ravi_notices = c.get("/notifications/mine", headers=RAVI).json()
    check("and he is told", len(ravi_notices) > before, {"before": before, "after": len(ravi_notices)})
    check("with the reference in the message", any(ticket["booking_reference"] in n["body"] for n in ravi_notices))

    led = c.get("/notifications/ledger", headers=ADMIN, params={"page_size": 100}).json()
    mail = [n for n in led["items"] if n["to_address"] == ravi_email and n["channel"] == "EMAIL"]
    check("an email was attempted and recorded", len(mail) >= 1, len(mail))
    check(
        "and SUPPRESSED rather than sent to an invented address",
        all(n["status"] == "SUPPRESSED" for n in mail),
        [n["status"] for n in mail],
    )

# =========================================================================
section("8. Costs, and the reports that depend on them")
# =========================================================================
arjun_tid = by_user[arjun_id]
c.post(f"/requests/{flight_id}/travellers/{arjun_tid}/decide", headers=PRIYA,
       json={"to_status": "BOOKED", "booking_reference": "6E-2231"})

# The shared cab: one payment, three people... but only two are going.
cab = c.get(f"/requests/{cab_id}", headers=PRIYA).json()
cab_tid = cab["travellers"][0]["id"]
c.post(f"/requests/{cab_id}/travellers/{cab_tid}/decide", headers=PRIYA,
       json={"to_status": "APPROVED", "conflict_override_reason": "Airport run"})
c.post(f"/requests/{cab_id}/travellers/{cab_tid}/decide", headers=PRIYA,
       json={"to_status": "BOOKED", "booking_reference": "CAB-8891"})

r = c.post(f"/requests/{flight_id}/costs/preview", headers=PRIYA, json={
    "total_amount": "1000.00", "traveller_ids": [by_user[ravi_id], arjun_tid],
})
shares = [money(row["amount"]) for row in r.json()["rows"]]
check("a split preview sums to exactly the total", sum(shares) == money("1000.00"), shares)

c.post(f"/requests/{cab_id}/costs", headers=PRIYA, json={
    "amounts": [{"traveller_id": cab_tid, "amount": "1200.00", "note": "Airport transfer"}],
})
c.post(f"/requests/{flight_id}/costs", headers=PRIYA, json={
    "amounts": [{"traveller_id": arjun_tid, "amount": "4850.00"}],
})

r = c.get("/analytics/campaigns", headers=PRIYA)
mine = next(row for row in r.json() if row["code"] == project_code)
expected = money("4850.00") * 2 + money("1200.00") if ticket_confirmed else money("4850.00") + money("1200.00")
check("campaign spend is the sum of its travellers", money(mine["spent"]) == expected, {"got": mine["spent"], "want": str(expected)})
check("with nothing uncosted", mine["uncosted"] == 0, mine["uncosted"])

# Now leave one booked traveller without a fare and prove it is flagged, not zeroed.
clash_row = c.get(f"/requests/{clash_id}", headers=PRIYA).json()["travellers"][0]
c.post(f"/requests/{clash_id}/travellers/{clash_row['id']}/decide", headers=PRIYA,
       json={"to_status": "BOOKED", "booking_reference": "TR-4410"})

r = c.get("/analytics/campaigns", headers=PRIYA)
mine = next(row for row in r.json() if row["code"] == project_code)
check("an uncosted booking is flagged", mine["uncosted"] == 1, mine["uncosted"])
check("and does not make the campaign look cheaper", money(mine["spent"]) == expected, mine["spent"])

r = c.get("/analytics/uncosted", headers=PRIYA)
check("it appears on the worklist by name", any(row["request_id"] == clash_id for row in r.json()), len(r.json()))

# =========================================================================
section("9. Do the screens agree with each other?")
# =========================================================================
# Three different queries over the same rows. Nothing until now checked they
# tell the same story.
analytics = c.get("/analytics", headers=PRIYA).json()
queue = c.get("/requests/queue/counts", headers=PRIYA).json()
listing = c.get("/requests", headers=PRIYA, params={"mine": False, "page_size": 100}).json()

booked_in_list = sum(
    1 for req in listing["items"] for t in req["travellers"] if t["status"] == "BOOKED"
)
check(
    "the analytics booked count matches the request list",
    analytics["overview"]["booked_travellers"] == booked_in_list,
    {"analytics": analytics["overview"]["booked_travellers"], "listing": booked_in_list},
)

campaign_total = sum(money(row["spent"]) for row in analytics["by_campaign"])
type_total = sum(money(row["spent"]) for row in analytics["by_type"])
check(
    "spend by campaign and spend by type add to the same figure",
    campaign_total == type_total == money(analytics["overview"]["spent"]),
    {"campaign": str(campaign_total), "type": str(type_total), "overview": analytics["overview"]["spent"]},
)

uncosted_count = len(analytics["uncosted"])
check(
    "the uncosted list and the uncosted count agree",
    uncosted_count == analytics["overview"]["uncosted"],
    {"list": uncosted_count, "count": analytics["overview"]["uncosted"]},
)

awaiting_in_list = sum(1 for req in listing["items"] if req["status"] == "SUBMITTED")
check(
    "the queue's awaiting tab matches the list",
    queue["awaiting"] == awaiting_in_list,
    {"queue": queue["awaiting"], "listing": awaiting_in_list},
)

# =========================================================================
section("10. Cancelling, and what disappears")
# =========================================================================
# An approved trip may already have a ticket, so the requester asks and an
# admin (or their manager) agrees before it is cancelled.
r = c.post(f"/requests/{clash_id}/cancel", headers=RAVI, json={"reason": "Client cancelled the Pune leg"})
check(
    "the requester asks to cancel an approved trip",
    r.status_code == 200 and r.json()["cancellation_status"] == "PENDING" and r.json()["status"] != "CANCELLED",
    r.text[:200],
)
r = c.post(f"/requests/{clash_id}/cancellation/decide", headers=PRIYA,
           json={"approve": True, "comment": "Pune leg dropped"})
check("an admin agrees and the trip is cancelled", r.status_code == 200 and r.json()["status"] == "CANCELLED", r.text[:200])

after = c.get("/analytics", headers=PRIYA).json()
check(
    "a cancelled trip stops counting as spend",
    money(after["overview"]["spent"]) == expected,
    after["overview"]["spent"],
)
check(
    "and drops off the uncosted worklist",
    not any(row["request_id"] == clash_id for row in after["uncosted"]),
)

r = c.post("/requests/check", headers=RAVI, json={
    "request_type": "LONG_DISTANCE", "project_id": project_id, "travel_reason": "Field survey coverage for the campaign", "mode": "TRAIN",
    "origin": "Hyderabad", "destination": "Pune",
    "start_at": at(TRIP_DAY, 8), "end_at": at(TRIP_DAY, 20),
})
check(
    "and frees that person's calendar",
    all(x["other_request_id"] != clash_id for x in r.json()["conflicts"]),
    [x["other_request_id"] for x in r.json()["conflicts"]],
)

# =========================================================================
section("11. The permission matrix")
# =========================================================================
# Every role against every endpoint that exposes money, PII or someone else's
# business. Each phase tested a handful; this is the whole grid.
GUARDED = [
    ("GET", "/users", "admin"),
    ("GET", "/analytics", "admin"),
    ("GET", "/analytics/campaigns", "admin"),
    ("GET", "/analytics/uncosted", "admin"),
    ("GET", "/requests/queue/counts", "admin"),
    ("GET", "/tickets/pending", "admin"),
    ("GET", "/notifications/ledger", "admin"),
    ("GET", "/notifications/scheduler", "admin"),
    ("GET", "/id-proofs/retention", "admin"),
    # The activity log is open to every admin role.
    ("GET", "/audit", "admin"),
    ("GET", "/audit/verify", "admin"),
    ("GET", "/audit/grants", "admin"),
    ("GET", "/audit/summary", "admin"),
    ("GET", "/vendors", "admin"),
    ("GET", "/invoices", "admin"),
    ("GET", "/team/changes", "admin"),
]

for method, path, level in GUARDED:
    staff = c.request(method, path, headers=RAVI).status_code
    admin = c.request(method, path, headers=PRIYA).status_code
    system = c.request(method, path, headers=ADMIN).status_code
    anon = c.request(method, path).status_code

    check(f"{path} refuses ground staff", staff == 403, staff)
    check(f"{path} refuses an anonymous caller", anon == 401, anon)
    if level == "admin":
        check(f"{path} allows an admin", admin == 200, admin)
    else:
        check(f"{path} is super-admin only", admin == 403 and system == 200, {"admin": admin, "system": system})

# Ground staff must not see another person's request at all.
r = c.get(f"/requests/{stay_id}", headers=MEERA)
check("someone not on a request cannot read it", r.status_code == 404, r.status_code)

# Cost is admin-only even on your own request.
r = c.get(f"/requests/{flight_id}", headers=RAVI)
check(
    "ground staff never see a cost, even their own",
    all(t["cost_amount"] is None for t in r.json()["travellers"]),
    [t["cost_amount"] for t in r.json()["travellers"]],
)

# =========================================================================
section("12. The ledger, after all of it")
# =========================================================================
r = c.get("/audit/verify", headers=ADMIN)
check("the hash chain is intact end to end", r.json()["ok"] is True, r.json())
chain = r.json()["checked"]

r = c.get("/audit/summary", headers=ADMIN)
summary = r.json()
# The activity log leaves sign-ins, sign-outs and failed sign-ins out, so the
# summary counts everything in the chain except those - this journey signed in
# several times, so it is strictly fewer.
sign_ins = {"LOGIN", "LOGIN_FAILED", "LOGOUT"}
check("every action but a sign-in is represented", 0 < summary["total"] < chain,
      {"summary": summary["total"], "chain": chain})
check("and sign-ins are left out", not (sign_ins & set(summary["by_action"])), summary["by_action"])

expected_actions = {
    "CREATE", "SUBMIT", "UPDATE", "APPROVE", "REJECT",
    "BOOK", "CANCEL", "OVERRIDE_CONFLICT",
}
if gemini_ok:
    # Confirming the extracted ticket is what sends the booking notice.
    expected_actions |= {"UPLOAD", "EXTRACT", "NOTIFY"}
missing = expected_actions - set(summary["by_action"])
check("covering every kind of action this journey took", not missing, missing)

r = c.get(f"/audit/entity/travel_request/{flight_id}", headers=ADMIN)
story = [e["action"] for e in r.json()]
check("one request's whole story reads forwards", story[0] == "SUBMIT", story)
check("it includes the edit", "UPDATE" in story, story)
# Decisions are recorded against the traveller row, because that is the decision
# unit. A request's history that omitted who approved it would be a correct
# answer to a question nobody asked.
check("and rolls up the decisions taken on its travellers", "APPROVE" in story, story)
check("and the rejection", "REJECT" in story, story)
if gemini_ok:
    check("and the ticket that was uploaded against it", "UPLOAD" in story, story)
    check("and the extraction", "EXTRACT" in story, story)

r = c.get("/audit/grants", headers=ADMIN)
if r.json().get("append_only"):
    check("the database refuses to rewrite the ledger", True)
else:
    note(
        "the ledger is not append-only on this database (running as root) - "
        "expected in development; scripts/grant_append_only.py applies it"
    )

# ---------------------------------------------------------------------------
print("\n" + "=" * 68)
if notes:
    print("Notes:")
    for text in notes:
        print(f"  - {text}")
    print()
if failures:
    print(f"{len(failures)} check(s) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print(f"Whole-system journey passed. {chain} ledger entries, chain intact.")
