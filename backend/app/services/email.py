"""
Sending mail, and deciding when not to.

Three guards stand between this module and a real inbox, because the cost of
getting it wrong is not a failed test - it is a hundred invented addresses at a
real domain bouncing off one Gmail account and taking its sending reputation
with them.

1. **`EMAIL_ENABLED` is off by default.** A fresh checkout, a test run and CI
   cannot send. Turning it on is a deliberate act in `.env`.
2. **`EMAIL_ALLOWLIST` confines development.** When it is set, only those
   addresses actually leave the building; everything else is recorded as
   SUPPRESSED. The smoke scripts create accounts like
   `p5.ravi.a1b2c3@designboxed.com`, and that is a real domain.
3. **An outbox replaces the wire in tests.** `use_outbox()` swaps the transport
   for a list, so the delivery path itself is exercised without a socket.

Nothing here raises on a delivery failure. A refused message is a recorded fact
on the notification row, not an exception that rolls back the approval that
caused it - the booking happened whether or not the email did.
"""
from __future__ import annotations

import difflib
import logging
import os
import re
import smtplib
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage

from dotenv import dotenv_values

from app.config import ENV_FILE, Settings, env_file_encoding, get_settings

logger = logging.getLogger(__name__)

#: Seconds to wait on the mail server. Long enough for a slow office link, short
#: enough that a blocked port fails while someone is still looking at the page.
SMTP_TIMEOUT = 15

#: When this process read its settings. A .env changed after this needs a
#: restart before it means anything - the commonest "I fixed it and nothing
#: changed".
_STARTED_AT = time.time()

#: What a setting's name looks like in .env.
ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Sent:
    """What happened to one message."""

    ok: bool
    suppressed: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class Attachment:
    """A file sent with a message - a traveller's ticket."""

    name: str
    content_type: str
    data: bytes


@dataclass
class Outbox:
    """Captured messages, for tests and for the dev console."""

    messages: list[dict] = field(default_factory=list)

    def clear(self) -> None:
        self.messages.clear()


#: When set, `send` writes here instead of opening a socket.
_outbox: Outbox | None = None


@contextmanager
def use_outbox():
    """Redirect every send into a list for the duration of the block."""
    global _outbox
    previous = _outbox
    _outbox = Outbox()
    try:
        yield _outbox
    finally:
        _outbox = previous


def _may_send_to(address: str) -> bool:
    """Whether this address is allowed a real message right now."""
    allowed = get_settings().allowed_email_recipients
    if not allowed:
        return True   # unrestricted, which is what production wants
    return address.strip().lower() in allowed


def build(
    to_address: str,
    subject: str,
    body: str,
    cc: list[str] | None = None,
    attachments: list[Attachment] | None = None,
) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.email_from_name} <{settings.email_from}>"
    message["To"] = to_address
    if cc:
        # send_message() delivers to every address in To and Cc, so the header
        # is the whole of what copying someone takes.
        message["Cc"] = ", ".join(cc)
    message.set_content(body)
    for item in attachments or []:
        maintype, _, subtype = (item.content_type or "application/octet-stream").partition("/")
        message.add_attachment(
            item.data,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=item.name,
        )
    return message


def recipients(to_address: str) -> list[str]:
    """The To line as a list: usually one address, several when one message
    went to everyone booked together. Real addresses only, each once."""
    kept: list[str] = []
    for address in (to_address or "").split(","):
        address = address.strip()
        if "@" in address and address.lower() not in {a.lower() for a in kept}:
            kept.append(address)
    return kept


def _usable_copies(to: list[str], cc: list[str] | None) -> list[str]:
    """The Cc list worth sending: real addresses, each once, never anyone
    already on the To line."""
    seen = {address.lower() for address in to}
    kept: list[str] = []
    for address in cc or []:
        address = (address or "").strip()
        if "@" not in address or address.lower() in seen:
            continue
        seen.add(address.lower())
        kept.append(address)
    return kept


def send(
    to_address: str,
    subject: str,
    body: str,
    *,
    cc: list[str] | None = None,
    attachments: list[Attachment] | None = None,
) -> Sent:
    """Deliver one message, or record precisely why it was not delivered.

    `cc` copies others in - a traveller's manager on a decision. The message
    belongs to its main recipient, so whether it goes at all is decided by
    them; each copy then passes the same allowlist on its own, and one that
    may not be mailed here is simply left off.

    `to_address` may name several people, comma separated - everyone booked
    together. The first is the main recipient; the rest are treated like the
    copies: each passes the allowlist on its own.
    """
    settings = get_settings()

    to = recipients(to_address)
    if not to:
        return Sent(ok=False, detail="no usable address on this account")
    to_address = to[0]

    copies = _usable_copies(to, cc)

    if _outbox is not None:
        _outbox.messages.append(
            {"to": ", ".join(to), "cc": copies, "subject": subject, "body": body,
             "attachments": [item.name for item in attachments or []]}
        )
        return Sent(ok=True)

    if not settings.email_enabled:
        logger.info("Mail to %s not sent: EMAIL_ENABLED is off", to_address)
        return Sent(
            ok=False,
            suppressed=True,
            detail="email delivery is switched off (EMAIL_ENABLED is not true in backend/.env)",
        )

    if not _may_send_to(to_address):
        # Not an error. Someone chose to confine this environment.
        logger.info("Mail to %s not sent: outside EMAIL_ALLOWLIST", to_address)
        return Sent(
            ok=False,
            suppressed=True,
            detail="address is outside EMAIL_ALLOWLIST for this environment",
        )

    held_back = [address for address in [*to[1:], *copies] if not _may_send_to(address)]
    if held_back:
        logger.info("Copy to %s not sent: outside EMAIL_ALLOWLIST", ", ".join(held_back))
        copies = [address for address in copies if address not in held_back]
    to = [to_address, *(address for address in to[1:] if address not in held_back)]

    missing = missing_settings()
    if missing:
        detail = f"SMTP is enabled but not configured: {', '.join(missing)} is not set"
        logger.warning("Mail to %s not sent: %s", to_address, detail)
        return Sent(ok=False, detail=detail)

    try:
        with _connect(settings) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_app_password)
            smtp.send_message(
                build(", ".join(to), subject, body, cc=copies, attachments=attachments)
            )
        logger.info(
            "Mail sent to %s%s: %s",
            ", ".join(to), f" (cc {', '.join(copies)})" if copies else "", subject,
        )
        return Sent(ok=True)
    except Exception as exc:
        # Deliberately broad: DNS, TLS, auth and refusal are all the same
        # outcome here, and none of them may take down the caller.
        # The message body is never logged - it carries a PNR and a full name.
        detail = error_text(exc)
        hint = hint_for(detail, settings)
        logger.warning(
            "Mail to %s failed: %s%s", to_address, detail, f" - {hint}" if hint else ""
        )
        return Sent(ok=False, detail=detail[:400])


def _connect(settings: Settings) -> smtplib.SMTP:
    """An open, encrypted connection, ready for login.

    Port 465 speaks TLS from the first byte and needs SMTP_SSL; 587 (and 25)
    start in plain text and upgrade with STARTTLS. Sending STARTTLS to 465
    does not fail - it waits for a reply that never comes, then times out.
    """
    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT, context=context
        )
    else:
        smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=SMTP_TIMEOUT)
    try:
        smtp.ehlo()
        if settings.smtp_port != 465:
            smtp.starttls(context=context)
            smtp.ehlo()
    except BaseException:
        smtp.close()   # not handed to a `with` yet, so nothing else would
        raise
    return smtp


def error_text(exc: BaseException) -> str:
    """The server's own words. `str()` of an SMTP error is a tuple repr with the
    reply as bytes; the code and the text are what a person can search for."""
    if isinstance(exc, smtplib.SMTPResponseException):
        reply = exc.smtp_error
        if isinstance(reply, bytes):
            reply = reply.decode(errors="replace")
        return f"{type(exc).__name__}: {exc.smtp_code} {' '.join(str(reply).split())}"
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def hint_for(error: str, settings: Settings) -> str | None:
    """What to do about an error, in the terms of this app's settings."""
    lowered = error.lower()
    gmail = "gmail" in settings.smtp_host.lower()
    if "535" in error or "534" in error or "username and password not accepted" in lowered:
        if gmail:
            return (
                "Gmail refused SMTP_USERNAME / SMTP_APP_PASSWORD. Use a 16-letter App "
                "Password made on that same Google account (it needs 2-Step "
                "Verification), not the account's normal password."
            )
        return "The mail server refused SMTP_USERNAME / SMTP_APP_PASSWORD."
    if "5.4.5" in error or "daily user sending limit" in lowered:
        return "Gmail's daily sending limit was reached. It resets within 24 hours."
    # Before the connection checks below: "SMTPSenderRefused" contains "refused".
    if "recipientsrefused" in lowered or "5.1.1" in error:
        return "The mail server refused the recipient address. Check it is spelled correctly."
    if "senderrefused" in lowered or "sender address rejected" in lowered or "553" in error:
        return "The server rejected the sender. EMAIL_FROM should be the SMTP_USERNAME address."
    if "timed out" in lowered or "timeout" in lowered or "serverdisconnected" in lowered:
        return (
            f"Could not talk to {settings.smtp_host}:{settings.smtp_port}. An office "
            "network, ISP or antivirus may block outgoing mail ports - try SMTP_PORT=465 "
            "(or 587 if 465 is the one set), or another network."
        )
    if "gaierror" in lowered or "name or service not known" in lowered or "getaddrinfo" in lowered:
        return f"{settings.smtp_host} could not be found. Check SMTP_HOST and the internet connection."
    if "connectionrefused" in lowered or "connection refused" in lowered:
        return f"Nothing is accepting mail at {settings.smtp_host}:{settings.smtp_port}. Check SMTP_HOST and SMTP_PORT."
    if "certificate" in lowered or "ssl" in lowered:
        return (
            "The secure connection failed. Antivirus or a company proxy that inspects "
            "TLS can cause this; so can SMTP_PORT=465 on a server that expects 587."
        )
    return None


def missing_settings() -> list[str]:
    """The SMTP settings a send needs that are still blank."""
    settings = get_settings()
    return [
        name
        for name, value in (
            ("SMTP_USERNAME", settings.smtp_username),
            ("SMTP_APP_PASSWORD", settings.smtp_app_password),
            ("EMAIL_FROM", settings.email_from),
        )
        if not value.strip()
    ]


def configuration_problem() -> str | None:
    """Why mail would not leave this server right now, in words a person can act on.

    None when it is configured to send. Says nothing about whether the
    credentials are right - `check()` proves that against the server.
    """
    settings = get_settings()
    # Settings can arrive as real environment variables (Docker, Cloud Run)
    # with no file at all, so a missing file is only the problem when the
    # settings it would have held are missing too.
    no_file = (
        f" - and there is no {ENV_FILE}: the API reads backend/.env and no other file"
        if not ENV_FILE.exists()
        else ""
    )
    if not settings.email_enabled:
        return f"EMAIL_ENABLED is not true in backend/.env{no_file}"
    missing = missing_settings()
    if missing:
        return f"{', '.join(missing)} is not set in backend/.env{no_file}"
    return None


def log_configuration() -> None:
    """Say once, at startup, whether this server will send mail.

    "The emails are not arriving" otherwise has to be debugged from the
    outside; this puts the answer in the first screen of the server log.
    """
    settings = get_settings()
    report = env_file_report()
    for key, guess in report["unknown_keys"].items():
        logger.warning(
            "backend/.env has %s, which this app does not read%s.",
            key, f" - did you mean {guess}?" if guess else "",
        )
    if report["encoding"] == "utf-16":
        logger.warning("backend/.env is saved as UTF-16; it was read, but save it as UTF-8.")

    problem = configuration_problem()
    if problem:
        logger.warning(
            "Email will NOT be sent: %s. Admins: Notifications > Delivery ledger > "
            "Email delivery shows the settings in use and can send a test.",
            problem,
        )
        return
    allowlist = settings.allowed_email_recipients
    logger.info(
        "Email delivery on: %s:%s (%s) as %s",
        settings.smtp_host,
        settings.smtp_port,
        "SSL" if settings.smtp_port == 465 else "STARTTLS",
        settings.email_from,
    )
    if allowlist:
        logger.warning(
            "EMAIL_ALLOWLIST is set, so mail goes ONLY to: %s. Everyone else's is "
            "recorded as Not sent. Empty it to email everyone.",
            ", ".join(sorted(allowlist)),
        )


def send_account_link(
    to_address: str, full_name: str, url: str, *, purpose: str, valid_hours: int
) -> Sent:
    """Email an invite or password-reset link to the person it is for."""
    settings = get_settings()
    first_name = (full_name or "").split()[0] if (full_name or "").strip() else "there"
    if purpose == "invite":
        subject = f"You're invited to {settings.app_name}"
        lead = (
            "An administrator has created an account for you. "
            "Choose a password to sign in:"
        )
    else:
        subject = f"Reset your {settings.app_name} password"
        lead = "Someone asked to reset the password on your account. Choose a new one here:"

    body = (
        f"Hi {first_name},\n\n"
        f"{lead}\n\n{url}\n\n"
        f"The link works once and expires in {valid_hours} hours. "
        "If you were not expecting this, you can ignore it.\n\n"
        f"- {settings.email_from_name}"
    )
    return send(to_address, subject, body)


def send_email_changed_notice(
    old_address: str, full_name: str, new_address: str, *, by: str | None = None
) -> Sent:
    """Tell the old address that the sign-in email moved.

    Sent to the old address on purpose: if someone else made the change, the
    real owner is the one who needs to hear about it, and the new address may
    be theirs. Best-effort, after the response - the change has already
    happened and a mail failure must not undo it.
    """
    settings = get_settings()
    first_name = (full_name or "").split()[0] if (full_name or "").strip() else "there"
    changed_by = f"Changed by {by}.\n\n" if by else ""
    body = (
        f"Hi {first_name},\n\n"
        f"Your {settings.app_name} sign-in email is now {new_address}. "
        "Use that address to sign in and to reset your password from now on.\n\n"
        f"{changed_by}"
        "If you did not expect this, contact your administrator.\n\n"
        f"- {settings.email_from_name}"
    )
    return send(old_address, f"Your {settings.app_name} sign-in email was changed", body)


def send_password_changed_notice(address: str, full_name: str) -> Sent:
    """Tell someone their password was just changed, so a change they did not
    make does not go unnoticed. Best-effort, after the response."""
    settings = get_settings()
    first_name = (full_name or "").split()[0] if (full_name or "").strip() else "there"
    body = (
        f"Hi {first_name},\n\n"
        f"The password on your {settings.app_name} account was just changed, and "
        "every other device was signed out.\n\n"
        "If this was not you, contact your administrator straight away.\n\n"
        f"- {settings.email_from_name}"
    )
    return send(address, f"Your {settings.app_name} password was changed", body)


def check() -> dict:
    """Prove the SMTP credentials work, without sending anything.

    Used by /health/email. Connects, negotiates TLS and authenticates, then hangs
    up - so a broken app password is visible on the dashboard rather than at the
    moment someone is waiting for a booking confirmation.
    """
    settings = get_settings()
    problem = configuration_problem()
    if problem:
        return {"ok": False, "detail": problem}

    try:
        with _connect(settings) as smtp:
            smtp.login(settings.smtp_username, settings.smtp_app_password)
        return {
            "ok": True,
            "host": settings.smtp_host,
            "from": settings.email_from,
            "restricted_to": sorted(settings.allowed_email_recipients) or None,
        }
    except Exception as exc:
        return {"ok": False, "detail": error_text(exc)[:300]}


# ---------------------------------------------------------------------------
# Diagnostics: what an admin needs to fix delivery without reading server logs
# ---------------------------------------------------------------------------


def env_file_report() -> dict:
    """Which .env this process reads, and what is wrong with it, if anything.

    Key names only - never values.
    """
    exists = ENV_FILE.exists()
    report: dict = {
        "path": str(ENV_FILE),
        "exists": exists,
        "encoding": None,
        "modified_at": None,
        "keys": [],
        "unknown_keys": {},
    }
    if not exists:
        return report

    encoding = env_file_encoding(ENV_FILE)
    try:
        raw = ENV_FILE.read_bytes()
        mtime = ENV_FILE.stat().st_mtime
    except OSError:
        return report
    report["encoding"] = (
        "utf-16" if encoding == "utf-16"
        else "utf-8 with BOM" if raw.startswith(b"\xef\xbb\xbf")
        else "utf-8"
    )
    report["modified_at"] = datetime.fromtimestamp(mtime, tz=timezone.utc)
    try:
        # A line with no "=" parses as a key with no value - and is as likely to
        # be a pasted password as a name, so it is never reported.
        keys = [
            k for k, v in dotenv_values(ENV_FILE, encoding=encoding).items()
            if k and v is not None and ENV_KEY.fullmatch(k)
        ]
    except Exception:   # an unreadable file is reported, not raised
        keys = []
    report["keys"] = keys

    known = {name.upper() for name in Settings.model_fields}
    for key in keys:
        if key.upper() not in known:
            guess = difflib.get_close_matches(key.upper(), sorted(known), n=1, cutoff=0.6)
            report["unknown_keys"][key] = guess[0] if guess else None
    return report


def effective_settings() -> dict:
    """The email settings this process is actually using. The password is
    described, never shown."""
    settings = get_settings()
    password = settings.smtp_app_password
    env = env_file_report()
    modified = env["modified_at"]
    started = datetime.fromtimestamp(_STARTED_AT, tz=timezone.utc)
    overridden = sorted(
        name for name in (
            "EMAIL_ENABLED", "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME",
            "SMTP_APP_PASSWORD", "EMAIL_FROM", "EMAIL_ALLOWLIST",
        )
        if name in os.environ
    )
    return {
        "enabled": settings.email_enabled,
        "host": settings.smtp_host,
        "port": settings.smtp_port,
        "security": "SSL" if settings.smtp_port == 465 else "STARTTLS",
        "username": settings.smtp_username or None,
        "password": f"set ({len(password)} characters)" if password else "not set",
        "password_looks_wrong": bool(
            password and "gmail" in settings.smtp_host.lower() and len(password) != 16
        ),
        "from_address": settings.email_from or None,
        "from_name": settings.email_from_name,
        "allowlist": sorted(settings.allowed_email_recipients),
        "links_point_to": settings.frontend_base_url,
        "env_file": env,
        "from_environment": overridden,
        "started_at": started,
        "restart_needed": bool(modified and modified > started),
    }


def send_test(to_address: str) -> dict:
    """Send one real message and report exactly how far it got.

    Ignores EMAIL_ALLOWLIST - an admin typed this address on purpose - but
    says whether ordinary notices to it would be held back by it.
    """
    settings = get_settings()
    result: dict = {
        "ok": False,
        "to": to_address,
        "stage": "config",
        "error": None,
        "hint": None,
        "allowlisted": _may_send_to(to_address),
    }

    problem = configuration_problem()
    if problem:
        result["error"] = problem
        result["hint"] = (
            "Edit backend/.env, save it, and restart the API - settings are read "
            "once, when it starts."
        )
        return result

    subject = f"Test email from {settings.app_name}"
    body = (
        "This is a test sent from Notifications > Delivery ledger.\n\n"
        "If you are reading it, email delivery works: approvals, bookings and "
        "invites will reach people at their account addresses.\n\n"
        f"- {settings.email_from_name}"
    )

    if _outbox is not None:   # tests
        _outbox.messages.append({"to": to_address, "subject": subject, "body": body})
        result.update(ok=True, stage="done")
        return result

    try:
        result["stage"] = "connect"
        with _connect(settings) as smtp:
            result["stage"] = "login"
            smtp.login(settings.smtp_username, settings.smtp_app_password)
            result["stage"] = "send"
            smtp.send_message(build(to_address, subject, body))
        result.update(ok=True, stage="done")
        logger.info("Test mail sent to %s", to_address)
    except Exception as exc:   # every failure is a result to show, not a 500
        result["error"] = error_text(exc)[:500]
        result["hint"] = hint_for(result["error"], settings)
        logger.warning("Test mail to %s failed at %s: %s", to_address, result["stage"], result["error"])
    return result
