"""
Password hashing, token signing, and the password policy.

Two details worth knowing:

* bcrypt silently truncates at 72 bytes. A user whose passphrase is longer would
  get a weaker password than they typed, and two different long passphrases
  sharing a 72-byte prefix would both open the account. So the password is
  SHA-256'd and base64'd first, which folds any length into a fixed 44 bytes.
  This is the well-trodden `bcrypt_sha256` construction, not an invention - but
  it does mean hashes here are not interchangeable with plain-bcrypt hashes.

* The policy follows NIST SP 800-63B: length and a breach/common check, with no
  composition rules and no forced rotation. Rules like "one uppercase, one
  symbol" measurably push people toward `Password1!` and are not required.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from app.config import get_settings

settings = get_settings()

ALGORITHM = "HS256"
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256  # guards against megabyte-payload hashing DoS

#: Not a breach corpus - just the handful that show up in practice on internal
#: tools, plus the values this project itself ships as defaults.
_COMMON_PASSWORDS = {
    "password", "password1", "password123", "passw0rd", "p@ssw0rd",
    "12345678", "123456789", "1234567890", "qwertyuiop", "qwerty123",
    "iloveyou", "admin123", "administrator", "letmein123", "welcome123",
    "changeme", "changeme123", "travelops", "designboxed", "abcd1234",
}


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def _prehash(password: str) -> bytes:
    """Fold an arbitrary-length password into 44 bytes bcrypt can take whole."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-time check that tolerates a missing or malformed stored hash.

    An invited user has no hash yet. We still run a real bcrypt comparison
    against a dummy so that "no password set" and "wrong password" take the same
    time and cannot be told apart by an attacker enumerating accounts.
    """
    if not password_hash:
        bcrypt.checkpw(_prehash(password), _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = bcrypt.hashpw(_prehash("timing-equaliser"), bcrypt.gensalt(rounds=12))


class PasswordPolicyError(ValueError):
    """Raised with a message meant to be shown to the person typing."""


def validate_password(password: str, *, email: str | None = None, name: str | None = None) -> None:
    """Raise PasswordPolicyError if the password is unacceptable."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
        )

    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        raise PasswordPolicyError("That password is too common. Choose something less guessable.")

    if len(set(password)) < 5:
        raise PasswordPolicyError("Password is too repetitive. Use a greater variety of characters.")

    # Their own name or email local-part is the first thing anyone tries.
    if email:
        local = email.split("@", 1)[0].lower()
        if len(local) >= 4 and local in lowered:
            raise PasswordPolicyError("Password must not contain your email address.")
    if name:
        for part in name.lower().split():
            if len(part) >= 4 and part in lowered:
                raise PasswordPolicyError("Password must not contain your name.")


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------

def create_access_token(*, user_id: int, role: str, tenant_id: str) -> tuple[str, datetime]:
    """Sign an access token. Returns the token and its absolute expiry."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "tenant": tenant_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": secrets.token_urlsafe(8),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), expires_at


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Return the payload, or None for anything expired, tampered or malformed."""
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Single-use tokens (invite / reset)
# ---------------------------------------------------------------------------

def generate_url_token() -> tuple[str, str]:
    """Return (raw_token, token_hash).

    The raw value goes into the emailed link and is never stored; only the hash
    is persisted, so a database leak cannot be replayed into account takeover.
    """
    raw = secrets.token_urlsafe(32)
    return raw, hash_url_token(raw)


def hash_url_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

