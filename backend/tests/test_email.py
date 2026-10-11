"""
The guards between this code and a real inbox.

These are worth testing precisely because the failure is invisible in
development and expensive in production: the smoke scripts invent addresses at
`@designboxed.com`, which is a real domain, and mail to a hundred of them would
bounce off one Gmail account and take its sending reputation with it.

The distinction the ledger has to keep is SUPPRESSED versus FAILED. Suppressed
means nobody tried, on purpose. Failed means we tried and were refused. Treating
them the same would make "did this person get told?" unanswerable.
"""
import smtplib

import pytest

from app.config import Settings
from app.core.enums import NotificationChannel, NotificationStatus, Role
from app.models.request import Notification
from app.models.user import User
from app.services import email, notifications

TENANT = "designboxed"


@pytest.fixture
def person(db):
    user = User(
        tenant_id=TENANT,
        email="ravi@designboxed.com",
        full_name="Ravi Kumar",
        role=Role.GROUND_STAFF,
        password_hash="x",
    )
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def real_transport(monkeypatch):
    """Step out of the session-wide outbox to test the guards themselves.

    Nothing here reaches a socket: every case is stopped by a guard before
    smtplib is touched, which is the point being tested.
    """
    monkeypatch.setattr(email, "_outbox", None)
    return monkeypatch


def configure(monkeypatch, **overrides):
    """Swap in a settings object for the module under test.

    `Settings()` still reads the developer's .env for anything not named here,
    so every field these tests depend on is passed explicitly - otherwise a real
    EMAIL_ALLOWLIST leaks in and the case under test is not the case running.
    """
    defaults = {
        "email_enabled": False,
        "email_allowlist": "",
        "smtp_username": "",
        "smtp_app_password": "",
        "email_from": "",
    }
    settings = Settings(**{**defaults, **overrides})
    monkeypatch.setattr(email, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# The guards
# ---------------------------------------------------------------------------


def test_delivery_is_off_by_default(real_transport, monkeypatch):
    """A fresh checkout, a test run and CI must be incapable of sending."""
    configure(monkeypatch, email_enabled=False)
    result = email.send("someone@example.com", "Subject", "Body")
    assert not result.ok
    assert result.suppressed
    assert "switched off" in result.detail


def test_an_address_outside_the_allowlist_is_suppressed(real_transport, monkeypatch):
    configure(
        monkeypatch,
        email_enabled=True,
        smtp_username="ops@example.com",
        smtp_app_password="secret",
        email_from="ops@example.com",
        email_allowlist="me@example.com",
    )
    result = email.send("p5.ravi.a1b2c3@designboxed.com", "Subject", "Body")
    assert not result.ok
    assert result.suppressed
    assert "EMAIL_ALLOWLIST" in result.detail


def test_the_allowlist_ignores_case_and_padding(real_transport, monkeypatch):
    settings = configure(
        monkeypatch,
        email_enabled=True,
        email_allowlist="  Me@Example.com , other@example.com ",
    )
    assert settings.allowed_email_recipients == {"me@example.com", "other@example.com"}
    assert email._may_send_to("ME@EXAMPLE.COM") is True
    assert email._may_send_to("stranger@example.com") is False


def test_an_empty_allowlist_means_no_restriction(real_transport, monkeypatch):
    """Which is what production wants - it is the enabled switch that gates it."""
    configure(monkeypatch, email_enabled=True, email_allowlist="")
    assert email._may_send_to("anyone@example.com") is True


def test_enabled_but_unconfigured_is_a_failure_not_a_suppression(
    real_transport, monkeypatch
):
    """Someone turned delivery on and forgot the credentials. That is a fault to
    surface, not a decision to respect."""
    configure(monkeypatch, email_enabled=True, smtp_username="", smtp_app_password="")
    result = email.send("me@example.com", "Subject", "Body")
    assert not result.ok
    assert not result.suppressed
    assert "not configured" in result.detail


def test_a_missing_address_never_reaches_the_transport(real_transport, monkeypatch):
    configure(monkeypatch, email_enabled=True, email_allowlist="")
    for address in ("", "not-an-address"):
        result = email.send(address, "Subject", "Body")
        assert not result.ok
        assert "no usable address" in result.detail


def test_the_outbox_captures_instead_of_sending():
    with email.use_outbox() as outbox:
        assert email.send("anyone@example.com", "Hello", "Body").ok
        assert len(outbox.messages) == 1
        assert outbox.messages[0]["to"] == "anyone@example.com"


def test_the_built_message_carries_a_named_sender(monkeypatch):
    configure(
        monkeypatch,
        email_from="ops@example.com",
        email_from_name="Travel Ops",
    )
    message = email.build("ravi@example.com", "Booked", "Body")
    assert message["To"] == "ravi@example.com"
    assert message["Subject"] == "Booked"
    assert message["From"] == "Travel Ops <ops@example.com>"


# ---------------------------------------------------------------------------
# What the ledger records
# ---------------------------------------------------------------------------


def test_a_sent_message_is_recorded_as_sent(db, person, outbox):
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.SENT
    assert mail.attempts == 1
    assert mail.sent_at is not None
    assert mail.last_error is None
    assert len(outbox.messages) == 1


def test_an_in_app_row_is_always_written_even_without_email(db, person):
    rows = notifications.notify(
        db,
        tenant_id=TENANT,
        user=person,
        kind="TEST",
        title="Hello",
        body="Body",
        send_email=False,
    )
    db.commit()

    assert len(rows) == 1
    assert rows[0].channel is NotificationChannel.IN_APP
    assert rows[0].status is NotificationStatus.SENT


def test_a_suppressed_message_is_distinguished_from_a_failure(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, suppressed=True, detail="off")
    )
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.SUPPRESSED
    assert mail.sent_at is None


def test_a_refused_message_is_recorded_as_failed_with_the_reason(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, detail="SMTPAuthenticationError: nope")
    )
    rows = notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    mail = next(r for r in rows if r.channel is NotificationChannel.EMAIL)
    assert mail.status is NotificationStatus.FAILED
    assert "SMTPAuthenticationError" in mail.last_error


def test_a_failed_message_never_takes_down_the_caller(db, person, monkeypatch):
    """The booking happened whether or not the email did."""
    def explode(*a, **k):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=False, detail="refused"))
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()   # would raise if the failure had propagated
    assert db.query(Notification).count() == 2


def test_retry_picks_up_failures_and_leaves_suppressions_alone(db, person, monkeypatch):
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, detail="temporary refusal")
    )
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Failed one", body="Body"
    )
    monkeypatch.setattr(
        email, "send", lambda *a, **k: email.Sent(ok=False, suppressed=True, detail="off")
    )
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Suppressed one", body="Body"
    )
    db.commit()

    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=True))
    result = notifications.retry_failed(db, TENANT)

    assert result == {"attempted": 1, "sent": 1, "still_failing": 0}
    suppressed = db.query(Notification).filter_by(title="Suppressed one").all()
    assert any(n.status is NotificationStatus.SUPPRESSED for n in suppressed)


def test_retry_gives_up_after_the_attempt_cap(db, person, monkeypatch):
    """An address that has refused three times is not going to start working,
    and retrying forever just hammers the SMTP server."""
    monkeypatch.setattr(email, "send", lambda *a, **k: email.Sent(ok=False, detail="refused"))
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    for _ in range(5):
        notifications.retry_failed(db, TENANT)

    mail = db.query(Notification).filter_by(channel=NotificationChannel.EMAIL).one()
    assert mail.attempts == notifications.MAX_ATTEMPTS


def test_the_ledger_summarises_by_status(db, person, outbox):
    notifications.notify(
        db, tenant_id=TENANT, user=person, kind="TEST", title="Hello", body="Body"
    )
    db.commit()

    summary = notifications.ledger_summary(db, TENANT)
    assert summary["total"] == 2
    assert summary["emails"] == 1
    assert summary["by_status"]["SENT"] == 2


def test_a_user_with_no_address_gets_the_in_app_row_only(db):
    nameless = User(
        tenant_id=TENANT, email="", full_name="No Address", role=Role.GROUND_STAFF,
        password_hash="x",
    )
    db.add(nameless)
    db.commit()

    rows = notifications.notify(
        db, tenant_id=TENANT, user=nameless, kind="TEST", title="Hello", body="Body"
    )
    db.commit()
    assert [r.channel for r in rows] == [NotificationChannel.IN_APP]


# ---------------------------------------------------------------------------
# Account links, and saying why mail is not going
# ---------------------------------------------------------------------------


def test_an_invite_link_is_emailed_to_the_person(outbox):
    sent = email.send_account_link(
        "ravi@example.com", "Ravi Kumar", "https://app/set-password?token=abc",
        purpose="invite", valid_hours=72,
    )
    assert sent.ok
    message = outbox.messages[-1]
    assert message["to"] == "ravi@example.com"
    assert "invited" in message["subject"]
    assert "https://app/set-password?token=abc" in message["body"]
    assert message["body"].startswith("Hi Ravi,")


def test_a_reset_link_says_it_is_a_reset(outbox):
    email.send_account_link("ravi@example.com", "Ravi", "https://x", purpose="reset", valid_hours=2)
    assert "Reset" in outbox.messages[-1]["subject"]
    assert "2 hours" in outbox.messages[-1]["body"]


def test_the_problem_names_the_switch_when_mail_is_off(monkeypatch):
    configure(monkeypatch, email_enabled=False)
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: True})())
    assert "EMAIL_ENABLED" in email.configuration_problem()


def test_the_problem_names_every_missing_setting(monkeypatch):
    configure(monkeypatch, email_enabled=True, smtp_username="me@gmail.com")
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: True})())
    problem = email.configuration_problem()
    assert "SMTP_APP_PASSWORD" in problem and "EMAIL_FROM" in problem
    assert "SMTP_USERNAME" not in problem


def test_a_missing_env_file_is_named_when_settings_are_missing(monkeypatch):
    configure(monkeypatch, email_enabled=True)
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: False})())
    assert "there is no" in email.configuration_problem()


def test_settings_from_the_environment_need_no_file(monkeypatch):
    """Docker and Cloud Run pass settings as variables, with no .env in the
    image. That used to read as "no .env" and skip bulk-import invites."""
    configure(
        monkeypatch, email_enabled=True, smtp_username="me@gmail.com",
        smtp_app_password="abcdefghijklmnop", email_from="me@gmail.com",
    )
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: False})())
    assert email.configuration_problem() is None


def test_no_problem_when_configured(monkeypatch):
    configure(
        monkeypatch, email_enabled=True, smtp_username="me@gmail.com",
        smtp_app_password="abcd efgh ijkl mnop", email_from="me@gmail.com",
    )
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: True})())
    assert email.configuration_problem() is None


def test_an_app_password_pasted_with_spaces_signs_in_without_them():
    settings = Settings(_env_file=None, smtp_app_password="abcd efgh ijkl mnop")
    assert settings.smtp_app_password == "abcdefghijklmnop"


# ---------------------------------------------------------------------------
# Talking to the server: 465 vs 587, and saying what it said
# ---------------------------------------------------------------------------


class FakeSMTP:
    """Records the conversation; never opens a socket."""

    made: list["FakeSMTP"] = []
    refuse_login = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.calls = host, port, []
        self.ssl_from_start = False
        FakeSMTP.made.append(self)

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")
        if FakeSMTP.refuse_login:
            raise smtplib.SMTPAuthenticationError(
                535, b"5.7.8 Username and Password not accepted. For more information, go to"
            )

    def send_message(self, message):
        self.calls.append(("send", message["To"]))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSMTPSSL(FakeSMTP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ssl_from_start = True


@pytest.fixture
def wire(real_transport, monkeypatch):
    FakeSMTP.made = []
    FakeSMTP.refuse_login = False
    monkeypatch.setattr(email.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def gmail(monkeypatch, **overrides):
    return configure(monkeypatch, **{
        "email_enabled": True, "smtp_username": "me@gmail.com",
        "smtp_app_password": "abcdefghijklmnop", "email_from": "me@gmail.com",
        **overrides,
    })


def test_port_587_upgrades_with_starttls(wire, monkeypatch):
    gmail(monkeypatch, smtp_port=587)
    assert email.send("ravi@example.com", "s", "b").ok
    conn = wire.made[-1]
    assert not conn.ssl_from_start
    assert conn.calls[:3] == ["ehlo", "starttls", "ehlo"]


def test_port_465_is_tls_from_the_first_byte(wire, monkeypatch):
    """STARTTLS on 465 does not fail - it hangs until the timeout."""
    gmail(monkeypatch, smtp_port=465)
    assert email.send("ravi@example.com", "s", "b").ok
    conn = wire.made[-1]
    assert conn.ssl_from_start
    assert "starttls" not in conn.calls


def test_a_refused_login_says_what_gmail_said(wire, monkeypatch, caplog):
    gmail(monkeypatch)
    wire.refuse_login = True
    with caplog.at_level("WARNING"):
        sent = email.send("ravi@example.com", "s", "b")
    assert not sent.ok
    assert "535" in sent.detail and "Username and Password not accepted" in sent.detail
    # The log carries the reason too, not just "SMTPAuthenticationError".
    assert "Username and Password not accepted" in caplog.text
    assert "App Password" in caplog.text


def test_hints_name_the_fix():
    settings = Settings(_env_file=None, smtp_host="smtp.gmail.com", smtp_port=587)
    assert "App Password" in email.hint_for("SMTPAuthenticationError: 535 5.7.8 nope", settings)
    assert "465" in email.hint_for("TimeoutError: timed out", settings)
    assert email.hint_for("something new", settings) is None


def test_a_refused_sender_is_not_mistaken_for_a_dead_server():
    """The class name SMTPSenderRefused contains "refused", which once matched
    the nothing-is-listening hint first."""
    settings = Settings(_env_file=None, smtp_host="smtp.gmail.com", smtp_port=587)
    sender = email.hint_for("SMTPSenderRefused: 553 5.7.1 Sender address rejected", settings)
    assert "EMAIL_FROM" in sender
    recipient = email.hint_for("SMTPRecipientsRefused: {'x@y': (550, b'5.1.1 no such user')}", settings)
    assert "recipient" in recipient
    refused = email.hint_for("ConnectionRefusedError: [Errno 111] Connection refused", settings)
    assert "SMTP_PORT" in refused


def test_a_failed_starttls_closes_the_connection(wire, monkeypatch):
    gmail(monkeypatch, smtp_port=587)
    closed = []

    def no_tls(self, context=None):
        raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")

    monkeypatch.setattr(FakeSMTP, "starttls", no_tls)
    monkeypatch.setattr(FakeSMTP, "close", lambda self: closed.append(self), raising=False)
    assert not email.send("ravi@example.com", "s", "b").ok
    assert closed == [wire.made[-1]]


# ---------------------------------------------------------------------------
# The .env itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "encode"),
    [
        ("utf-8", lambda text: text.encode("utf-8")),
        ("utf-8 with BOM", lambda text: b"\xef\xbb\xbf" + text.encode("utf-8")),
        ("utf-16", lambda text: text.encode("utf-16")),
    ],
)
def test_a_windows_saved_env_file_still_turns_email_on(tmp_path, monkeypatch, label, encode):
    """Notepad and PowerShell add a byte-order mark or write UTF-16. With the
    BOM read as text, the first key became "\\ufeffEMAIL_ENABLED" and email
    stayed off while the file plainly said true."""
    from app.config import env_file_encoding

    # A real environment variable outranks the file; CI sets EMAIL_ENABLED.
    monkeypatch.delenv("EMAIL_ENABLED", raising=False)
    monkeypatch.delenv("SMTP_PORT", raising=False)
    path = tmp_path / ".env"
    path.write_bytes(encode("EMAIL_ENABLED=true\r\nSMTP_PORT=465\r\n"))
    settings = Settings(_env_file=path, _env_file_encoding=env_file_encoding(path))
    assert settings.email_enabled is True, label
    assert settings.smtp_port == 465


def test_a_misspelt_key_is_reported_with_a_guess(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("EMAIL_ENABLED=true\nSMTP_PASSWORD=abcd\nSMTP_USERNAME=me@gmail.com\n")
    monkeypatch.setattr(email, "ENV_FILE", path)
    report = email.env_file_report()
    assert report["unknown_keys"] == {"SMTP_PASSWORD": "SMTP_APP_PASSWORD"}
    assert "SMTP_USERNAME" in report["keys"]


def test_a_value_on_its_own_line_is_never_reported_as_a_key(tmp_path, monkeypatch):
    """A password pasted on the line after `SMTP_APP_PASSWORD=` parses as a key
    with no value. Key names are shown to admins and logged; that line is not."""
    path = tmp_path / ".env"
    path.write_text("EMAIL_ENABLED=true\nSMTP_APP_PASSWORD=\nwxyzwxyzwxyzwxyz\n")
    monkeypatch.setattr(email, "ENV_FILE", path)
    report = email.env_file_report()
    assert "wxyzwxyzwxyzwxyz" not in report["keys"]
    assert "wxyzwxyzwxyzwxyz" not in report["unknown_keys"]


def test_the_password_is_described_never_shown(monkeypatch):
    gmail(monkeypatch, smtp_app_password="abcdefghijklmnop")
    shown = email.effective_settings()
    assert shown["password"] == "set (16 characters)"
    assert "abcdefghijklmnop" not in repr(shown)


def test_a_gmail_password_of_the_wrong_length_is_flagged(monkeypatch):
    gmail(monkeypatch, smtp_app_password="my-normal-password")
    assert email.effective_settings()["password_looks_wrong"] is True


# ---------------------------------------------------------------------------
# Send a test
# ---------------------------------------------------------------------------


def test_a_test_with_mail_off_stops_at_config(real_transport, monkeypatch):
    configure(monkeypatch, email_enabled=False)
    result = email.send_test("me@example.com")
    assert result["ok"] is False and result["stage"] == "config"
    assert "EMAIL_ENABLED" in result["error"]
    assert "restart" in result["hint"]


def test_a_test_reports_the_stage_it_failed_at(wire, monkeypatch):
    gmail(monkeypatch)
    wire.refuse_login = True
    result = email.send_test("me@example.com")
    assert result["stage"] == "login"
    assert "535" in result["error"]
    assert "App Password" in result["hint"]


def test_a_test_ignores_the_allowlist_but_says_so(wire, monkeypatch):
    gmail(monkeypatch, email_allowlist="someone@else.com")
    result = email.send_test("me@example.com")
    assert result["ok"] is True and result["stage"] == "done"
    assert result["allowlisted"] is False
    assert wire.made[-1].calls[-1] == ("send", "me@example.com")


def test_toggle_email_service(monkeypatch):
    monkeypatch.setattr(email, "ENV_FILE", type("P", (), {"exists": lambda self: False})())
    shown = email.set_email_enabled(True)
    assert shown["enabled"] is True
    shown2 = email.set_email_enabled(False)
    assert shown2["enabled"] is False

