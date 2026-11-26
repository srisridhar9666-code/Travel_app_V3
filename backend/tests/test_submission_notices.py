"""
Admins hear about a request the moment it is raised.

Before this, a submission told no one: the admin found it by opening the queue,
and "emails are not going" was partly "no email was ever meant to go". The email
is written QUEUED and sent after the response, so the person pressing Submit is
not kept waiting on the mail server.
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import (
    NotificationCategory,
    NotificationChannel,
    NotificationStatus,
    RequestPriority,
    RequestType,
    Role,
    TravelMode,
    TravellerStatus,
)
from app.models.base import naive_utcnow
from app.models.project import Project
from app.models.request import Notification, RequestTraveller, TravelRequest
from app.models.user import User
from app.services import notifications
from app.services import requests as svc

TENANT = "designboxed"


@pytest.fixture
def world(db):
    project = Project(tenant_id=TENANT, name="Monsoon Survey", code="MON-1")

    def person(name, email, role=Role.GROUND_STAFF, active=True):
        user = User(tenant_id=TENANT, email=email, full_name=name, role=role,
                    password_hash="x", is_active=active)
        db.add(user)
        return user

    people = dict(
        priya=person("Priya Shah", "priya@designboxed.com", Role.ADMIN),
        arjun=person("Arjun Rao", "arjun@designboxed.com", Role.SYSTEM_ADMIN),
        gone=person("Old Admin", "old@designboxed.com", Role.ADMIN, active=False),
        ravi=person("Ravi Kumar", "ravi@designboxed.com"),
        meena=person("Meena Iyer", "meena@designboxed.com"),
    )
    db.add(project)
    db.commit()
    return project, people


def raise_flight(db, project, requester, travellers=None, priority=RequestPriority.MEDIUM):
    row = TravelRequest(
        tenant_id=TENANT, request_type=RequestType.LONG_DISTANCE, project_id=project.id,
        requester_id=requester.id, mode=TravelMode.FLIGHT,
        origin="Hyderabad", origin_state="Telangana",
        destination="Pune", destination_state="Maharashtra",
        start_at=datetime(2026, 10, 5, 6, 0), travel_reason="Store audit",
        priority=priority,
    )
    row.travellers = [
        RequestTraveller(user_id=p.id, status=TravellerStatus.PENDING)
        for p in (travellers or [requester])
    ]
    db.add(row)
    db.flush()
    queued = svc.record_submission(db, request=row, actor=requester, tenant_id=TENANT)
    db.commit()
    return row, queued


def notices(db, *, channel):
    return db.execute(
        select(Notification).where(
            Notification.kind == "REQUEST_SUBMITTED", Notification.channel == channel
        )
    ).scalars().all()


def deliver(db, queued):
    """Run the after-response send against the test's own transaction."""
    notifications.deliver_queued(
        queued,
        session_factory=lambda: Session(bind=db.get_bind(), join_transaction_mode="create_savepoint"),
    )
    db.expire_all()


class TestWhoIsTold:
    def test_every_active_admin_gets_a_notice_and_a_queued_email(self, db, world):
        project, p = world
        _, queued = raise_flight(db, project, p["ravi"])

        told = {n.user_id for n in notices(db, channel=NotificationChannel.IN_APP)}
        assert told == {p["priya"].id, p["arjun"].id}

        mails = notices(db, channel=NotificationChannel.EMAIL)
        assert {m.status for m in mails} == {NotificationStatus.QUEUED}
        assert sorted(queued) == sorted(m.id for m in mails)

    def test_an_admin_is_not_told_about_their_own_request(self, db, world):
        project, p = world
        raise_flight(db, project, p["priya"])
        told = {n.user_id for n in notices(db, channel=NotificationChannel.IN_APP)}
        assert told == {p["arjun"].id}

    def test_ground_staff_are_not_told(self, db, world):
        project, p = world
        raise_flight(db, project, p["ravi"], travellers=[p["ravi"], p["meena"]])
        told = {n.user_id for n in notices(db, channel=NotificationChannel.IN_APP)}
        assert p["ravi"].id not in told and p["meena"].id not in told

    def test_the_email_says_what_who_and_where_to_decide(self, db, world):
        project, p = world
        raise_flight(db, project, p["ravi"], travellers=[p["ravi"], p["meena"]])
        mail = notices(db, channel=NotificationChannel.EMAIL)[0]

        assert mail.subject == "New travel request: Flight: Hyderabad to Pune, 05 Oct 2026, 6:00 AM"
        assert "Travellers: Ravi Kumar, Meena Iyer" in mail.body
        assert "Campaign: MON-1 - Monsoon Survey" in mail.body
        assert "Reason: Store audit" in mail.body
        assert "Priority: Medium" in mail.body
        assert mail.body.rstrip().endswith("/approvals")

    def test_a_high_priority_request_stands_out_in_the_inbox(self, db, world):
        project, p = world
        raise_flight(db, project, p["ravi"], priority=RequestPriority.HIGH)
        mail = notices(db, channel=NotificationChannel.EMAIL)[0]
        notice = notices(db, channel=NotificationChannel.IN_APP)[0]

        assert mail.subject == (
            "High priority - New travel request: Flight: Hyderabad to Pune, 05 Oct 2026, 6:00 AM"
        )
        assert "Priority: High" in mail.body
        assert notice.title == "High-priority request from Ravi Kumar"

    def test_a_low_priority_request_keeps_the_plain_subject(self, db, world):
        project, p = world
        raise_flight(db, project, p["ravi"], priority=RequestPriority.LOW)
        mail = notices(db, channel=NotificationChannel.EMAIL)[0]
        assert mail.subject.startswith("New travel request:")
        assert "Priority: Low" in mail.body
        assert notices(db, channel=NotificationChannel.IN_APP)[0].title == (
            "New request from Ravi Kumar"
        )


class TestDelivery:
    def test_queued_emails_are_sent_after_the_response(self, db, world, outbox):
        project, p = world
        _, queued = raise_flight(db, project, p["ravi"])
        assert outbox.messages == []

        deliver(db, queued)

        assert {m.status for m in notices(db, channel=NotificationChannel.EMAIL)} == {
            NotificationStatus.SENT
        }
        assert len(outbox.messages) == 1
        assert sorted(a.strip() for a in outbox.messages[0]["to"].split(",")) == [
            "arjun@designboxed.com", "priya@designboxed.com",
        ]

    def test_a_row_already_handled_is_not_sent_twice(self, db, world, outbox):
        project, p = world
        _, queued = raise_flight(db, project, p["ravi"])
        deliver(db, queued)
        deliver(db, queued)
        assert len(outbox.messages) == 1

    def test_nothing_queued_is_a_no_op(self):
        notifications.deliver_queued([])

    def test_the_retry_job_sends_an_email_stranded_by_a_restart(self, db, world, outbox):
        project, p = world
        raise_flight(db, project, p["ravi"])
        raise_flight(db, project, p["meena"])
        stale, fresh = notices(db, channel=NotificationChannel.EMAIL)
        stale.created_at = naive_utcnow() - timedelta(minutes=10)
        db.commit()

        result = notifications.retry_failed(db, TENANT)

        assert result["attempted"] == 1
        assert stale.status is NotificationStatus.SENT
        assert fresh.status is NotificationStatus.QUEUED   # its own send may still be coming


class TestSwitchingItOff:
    def test_an_admin_can_turn_the_email_off_and_keep_the_notice(self, db, world, outbox):
        project, p = world
        notifications.set_preference(
            db, user=p["priya"], category=NotificationCategory.NEW_REQUESTS, enabled=False
        )
        db.commit()

        raise_flight(db, project, p["ravi"])

        assert p["priya"].id in {n.user_id for n in notices(db, channel=NotificationChannel.IN_APP)}
        assert {m.to_address for m in notices(db, channel=NotificationChannel.EMAIL)} == {
            "arjun@designboxed.com"
        }

    def test_only_admins_are_offered_the_switch(self, db, world):
        _, p = world
        assert "NEW_REQUESTS" in notifications.preferences_for(db, p["priya"])
        assert "NEW_REQUESTS" not in notifications.preferences_for(db, p["ravi"])
