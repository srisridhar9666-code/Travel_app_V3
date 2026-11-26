"""
Password hashing, the password policy, and access tokens.
"""
import time

import pytest

from app.core.security import (
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    create_access_token,
    decode_access_token,
    generate_url_token,
    hash_password,
    hash_url_token,
    validate_password,
    verify_password,
)


class TestPasswordHashing:
    def test_round_trip(self):
        h = hash_password("Monsoon-Ledger-42")
        assert verify_password("Monsoon-Ledger-42", h)
        assert not verify_password("Monsoon-Ledger-43", h)

    def test_salted(self):
        """Two users with the same password must not share a hash."""
        assert hash_password("Monsoon-Ledger-42") != hash_password("Monsoon-Ledger-42")

    def test_long_passwords_are_not_truncated(self):
        """The reason for the SHA-256 pre-hash.

        Plain bcrypt stops at 72 bytes, so two passphrases sharing a 72-byte
        prefix would open the same account. They must not.
        """
        base = "x" * 72
        stored = hash_password(base + "first-ending")
        assert verify_password(base + "first-ending", stored)
        assert not verify_password(base + "second-ending", stored)

    def test_missing_hash_is_rejected_not_crashed(self):
        """An invited user has no password yet."""
        assert verify_password("anything", None) is False
        assert verify_password("anything", "") is False

    def test_malformed_hash_is_rejected_not_crashed(self):
        assert verify_password("anything", "not-a-bcrypt-hash") is False


class TestPasswordPolicy:
    def test_accepts_a_reasonable_passphrase(self):
        validate_password("Corridor-Anchor-58", email="ravi@designboxed.com", name="Ravi Kumar")

    def test_rejects_short(self):
        with pytest.raises(PasswordPolicyError, match="at least"):
            validate_password("a" * (MIN_PASSWORD_LENGTH - 1))

    def test_rejects_absurdly_long(self):
        """Guards against someone hashing a megabyte to burn CPU."""
        with pytest.raises(PasswordPolicyError, match="at most"):
            validate_password("a" * 500)

    def test_accepts_simple_and_name_email_passwords(self):
        validate_password("password123")
        validate_password("ababababababab")
        validate_password("ravikumar-travel-9", email="ravikumar@designboxed.com")
        validate_password("kumar-is-my-name-1", name="Ravi Kumar")
        validate_password("Corridor-Anchor-58", name="Al Bo")


class TestAccessTokens:
    def test_round_trip(self):
        token, expires_at = create_access_token(user_id=7, role="ADMIN", tenant_id="designboxed")
        payload = decode_access_token(token)
        assert payload is not None
        assert payload["sub"] == "7"
        assert payload["role"] == "ADMIN"
        assert payload["tenant"] == "designboxed"
        assert expires_at.timestamp() == pytest.approx(payload["exp"], abs=1)

    def test_tampered_token_is_rejected(self):
        token, _ = create_access_token(user_id=7, role="GROUND_STAFF", tenant_id="designboxed")
        header, body, signature = token.split(".")
        forged, _ = create_access_token(user_id=7, role="SYSTEM_ADMIN", tenant_id="designboxed")
        # Swap in the elevated-role body, keep the original signature.
        assert decode_access_token(f"{header}.{forged.split('.')[1]}.{signature}") is None

    def test_garbage_is_rejected(self):
        assert decode_access_token("not-a-token") is None
        assert decode_access_token("") is None

    def test_expired_token_is_rejected(self, monkeypatch):
        import app.core.security as sec

        monkeypatch.setattr(sec.settings, "access_token_expire_minutes", -1)
        token, _ = create_access_token(user_id=1, role="ADMIN", tenant_id="designboxed")
        time.sleep(0.01)
        assert decode_access_token(token) is None

    def test_tokens_are_unique_per_issue(self):
        """The jti keeps two tokens minted in the same second distinguishable."""
        a, _ = create_access_token(user_id=1, role="ADMIN", tenant_id="designboxed")
        b, _ = create_access_token(user_id=1, role="ADMIN", tenant_id="designboxed")
        assert a != b


class TestUrlTokens:
    def test_raw_is_not_recoverable_from_hash(self):
        raw, hashed = generate_url_token()
        assert raw not in hashed
        assert len(hashed) == 64

    def test_hash_is_deterministic(self):
        raw, hashed = generate_url_token()
        assert hash_url_token(raw) == hashed

    def test_tokens_are_unique(self):
        assert len({generate_url_token()[0] for _ in range(50)}) == 50
