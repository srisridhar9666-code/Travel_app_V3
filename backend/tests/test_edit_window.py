"""
The edit window and its revision trail (addendum A1).

`is_editable()` itself is covered in `test_status.py`. What is tested here is the
machinery built on top of it: that the lock is actually enforced before anything
is mutated, that every amendment leaves exactly one append-only revision row with
a usable diff, and that a room share confirmed against one set of dates does not
survive being moved to another.
"""
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.enums import (
    Gender,
    RequestPriority,
    RequestType,
    RequestStatus,
    Role,
    RoomSharingChoice,
    TravellerStatus,
)
from app.models.project import Project
from app.models.request import RequestTraveller, TravelRequest
from app.models.user import User
from app.schemas.request import RequestCreate
from app.services import audit
from app.services import requests as svc

TENANT = "designboxed"


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")
    ravi = User(
        tenant_id=TENANT,
        email="ravi@designboxed.com",
        full_name="Ravi Kumar",
        role=Role.GROUND_STAFF,
        gender=Gender.MALE,
        password_hash="x",
    )
    meera = User(
        tenant_id=TENANT,
        email="meera@designboxed.com",
        full_name="Meera Iyer",
        role=Role.GROUND_STAFF,
        gender=Gender.FEMALE,
        password_hash="x",
    )
    db.add_all([project, ravi, meera])
    db.commit()
    return project, ravi, meera


def make_request(db, project, users, *, draft=False, **overrides):
    fields = dict(
        request_type=RequestType.HOTEL,
        hotel_city="Mumbai",
        check_in=date(2026, 10, 3),
        check_out=date(2026, 10, 6),
    )
    fields.update(overrides)
    row = TravelRequest(
        tenant_id=TENANT,
        project_id=project.id,
        requester_id=users[0].id,
        is_draft=draft,
        **fields,
    )
    row.travellers = [
        RequestTraveller(user_id=u.id, status=TravellerStatus.PENDING) for u in users
    ]
    db.add(row)
    db.commit()
    return row


def amend(db, row, editor, **changes):
    """One edit, exactly as the router does it: snapshot, mutate, diff, record."""
    before = svc.snapshot(row)
    for key, value in changes.items():
        setattr(row, key, value)
    after = svc.snapshot(row)
    diff = audit.diff(before, after)
    if diff:
        svc.clear_stale_shares(row, diff)
        svc.write_revision(
            db,
            request=row,
            editor=editor,
            summary=f"Edited {svc.describe_changes(diff)}",
            changes=diff,
        )
    db.commit()
    return diff


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------


def test_a_fresh_group_request_is_editable(db, world):
    project, ravi, meera = world
    row = make_request(db, project, [ravi, meera])
    assert svc.request_is_editable(row) is True
    svc.assert_editable(row)   # does not raise


@pytest.mark.parametrize(
    "decision", [TravellerStatus.APPROVED, TravellerStatus.REJECTED, TravellerStatus.BOOKED]
)
def test_one_decision_on_one_traveller_locks_the_whole_request(db, world, decision):
    """The core of A1. A group request is not half-editable."""
    project, ravi, meera = world
    row = make_request(db, project, [ravi, meera])

    row.travellers[1].status = decision
    db.commit()

    assert svc.request_is_editable(row) is False
    with pytest.raises(HTTPException) as caught:
        svc.assert_editable(row)
    assert caught.value.status_code == 409
    assert "locked" in caught.value.detail


def test_a_cancelled_request_is_locked_with_its_own_message(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    row.is_cancelled = True
    db.commit()

    with pytest.raises(HTTPException) as caught:
        svc.assert_editable(row)
    assert "cancelled" in caught.value.detail


def test_a_co_traveller_dropping_out_does_not_lock_the_request(db, world):
    """A cancellation is not an admin decision. The rest of the group can still
    amend their own trip."""
    project, ravi, meera = world
    row = make_request(db, project, [ravi, meera])
    row.travellers[1].status = TravellerStatus.CANCELLED
    db.commit()
    assert svc.request_is_editable(row) is True


def test_a_draft_stays_editable_whatever_its_travellers_say(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi], draft=True)
    assert svc.request_is_editable(row) is True


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------


def test_submission_opens_the_trail_at_revision_one(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi], draft=True)
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    history = svc.revisions_of(db, row.id)
    assert [r.revision_number for r in history] == [1]
    assert history[0].summary == "Raised"
    assert history[0].changes is None
    assert svc.edit_count(db, row.id) == 0


def test_each_amendment_appends_one_numbered_revision(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    amend(db, row, ravi, check_out=date(2026, 10, 8))
    amend(db, row, ravi, hotel_city="Pune")

    history = svc.revisions_of(db, row.id)
    assert [r.revision_number for r in history] == [3, 2, 1]   # newest first
    assert svc.edit_count(db, row.id) == 2
    assert history[0].editor_name == "Ravi Kumar"


def test_a_revision_carries_a_field_level_before_and_after(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    amend(db, row, ravi, hotel_city="Pune", check_out=date(2026, 10, 9))

    latest = svc.revisions_of(db, row.id)[0]
    assert latest.changes["hotel_city"] == {"from": "Mumbai", "to": "Pune"}
    assert latest.changes["check_out"] == {"from": "2026-10-06", "to": "2026-10-09"}
    assert "check-out" in latest.summary and "city" in latest.summary


def test_dates_in_a_diff_survive_serialisation(db, world):
    """A raw date in a JSON column raises at INSERT and rolls back the edit it
    describes - the bug that cost Phase 2 a user update. Phase 3 is all dates,
    so the commit below is the assertion as much as the types are."""
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    amend(db, row, ravi, check_in=date(2026, 11, 1), check_out=date(2026, 11, 4))

    db.expire_all()
    stored = svc.revisions_of(db, row.id)[0].changes
    assert stored["check_in"] == {"from": "2026-10-03", "to": "2026-11-01"}
    assert all(isinstance(v, str) for side in stored.values() for v in side.values())


def test_an_edit_that_changes_nothing_writes_no_revision(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    assert amend(db, row, ravi, hotel_city="Mumbai") == {}
    assert svc.edit_count(db, row.id) == 0


def test_adding_and_dropping_a_co_traveller_shows_up_in_the_diff(db, world):
    project, ravi, meera = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    before = svc.snapshot(row)
    svc.sync_travellers(db, request=row, people=[ravi, meera])
    after = svc.snapshot(row)
    diff = audit.diff(before, after)

    assert diff["travellers"]["from"] == [ravi.id]
    assert diff["travellers"]["to"] == sorted([ravi.id, meera.id])

    svc.write_revision(db, request=row, editor=ravi, summary="Edited travellers", changes=diff)
    db.commit()
    assert svc.edit_count(db, row.id) == 1


def test_sync_travellers_reports_who_joined_and_who_left(db, world):
    project, ravi, meera = world
    row = make_request(db, project, [ravi, meera])

    added, dropped = svc.sync_travellers(db, request=row, people=[ravi])
    assert added == []
    assert dropped == ["Meera Iyer"]
    assert [t.user_id for t in row.travellers] == [ravi.id]


def test_describe_changes_summarises_a_wide_edit():
    wide = {k: {} for k in ("origin", "destination", "start_at", "end_at", "notes")}
    assert svc.describe_changes(wide).endswith("and 2 more")
    assert svc.describe_changes({"start_at": {}}) == "departure"


# ---------------------------------------------------------------------------
# Shares do not outlive the dates they were agreed against
# ---------------------------------------------------------------------------


def test_moving_the_stay_drops_a_confirmed_room_share(db, world):
    project, ravi, meera = world
    row = make_request(db, project, [ravi])
    traveller = row.travellers[0]
    traveller.room_sharing = RoomSharingChoice.SHARE_EXISTING
    traveller.share_with_user_id = meera.id
    traveller.share_confirmed_by_id = meera.id
    traveller.share_confirmed_at = datetime(2026, 9, 30, 12, 0)
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    amend(db, row, ravi, check_in=date(2026, 11, 1), check_out=date(2026, 11, 4))

    assert traveller.room_sharing is RoomSharingChoice.NOT_OFFERED
    assert traveller.share_with_user_id is None
    assert traveller.share_confirmed_at is None


def test_editing_an_unrelated_field_leaves_the_share_alone(db, world):
    project, ravi, meera = world
    row = make_request(db, project, [ravi])
    traveller = row.travellers[0]
    traveller.room_sharing = RoomSharingChoice.SHARE_EXISTING
    traveller.share_with_user_id = meera.id
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    amend(db, row, ravi, notes="Arriving late")

    assert traveller.room_sharing is RoomSharingChoice.SHARE_EXISTING
    assert traveller.share_with_user_id == meera.id


# ---------------------------------------------------------------------------
# Derived status, end to end through the ORM
# ---------------------------------------------------------------------------


def test_status_follows_the_traveller_rows(db, world):
    project, ravi, meera = world
    row = make_request(
        db, project, [ravi, meera], check_in=date(2026, 11, 1), check_out=date(2026, 11, 4)
    )

    def status():
        return svc.to_read(db, row, tenant_id=TENANT).status

    assert status() is RequestStatus.SUBMITTED
    row.travellers[0].status = TravellerStatus.APPROVED
    db.commit()
    assert status() is RequestStatus.PARTIALLY_APPROVED
    row.travellers[1].status = TravellerStatus.APPROVED
    db.commit()
    assert status() is RequestStatus.APPROVED


def test_to_read_reports_the_lock_and_the_edit_count(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()
    amend(db, row, ravi, notes="Bringing a projector")

    read = svc.to_read(db, row, tenant_id=TENANT)
    assert read.is_editable is True
    assert read.edit_count == 1

    row.travellers[0].status = TravellerStatus.BOOKED
    db.commit()
    assert svc.to_read(db, row, tenant_id=TENANT).is_editable is False


# ---------------------------------------------------------------------------
# Priority is an editable field like any other
# ---------------------------------------------------------------------------


def test_a_request_saved_without_a_priority_is_medium(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    db.refresh(row)
    assert row.priority is RequestPriority.MEDIUM
    assert svc.to_read(db, row, tenant_id=TENANT).priority is RequestPriority.MEDIUM


def test_changing_priority_writes_a_revision(db, world):
    project, ravi, _ = world
    row = make_request(db, project, [ravi])
    svc.record_submission(db, request=row, actor=ravi, tenant_id=TENANT)
    db.commit()

    diff = amend(db, row, ravi, priority=RequestPriority.HIGH)

    assert diff == {"priority": {"from": RequestPriority.MEDIUM, "to": RequestPriority.HIGH}}
    latest = svc.revisions_of(db, row.id)[0]
    assert latest.summary == "Edited priority"
    assert latest.changes == {"priority": {"from": "MEDIUM", "to": "HIGH"}}
    assert svc.describe_changes({"priority": {}}) == "priority"


def test_the_body_defaults_priority_and_refuses_an_unknown_one():
    base = dict(
        request_type="HOTEL", project_id=1, hotel_city="Mumbai",
        check_in="2026-10-03", travel_reason="Store audit",
    )
    assert RequestCreate(**base).priority is RequestPriority.MEDIUM
    with pytest.raises(ValidationError):
        RequestCreate(**base, priority="URGENT")
