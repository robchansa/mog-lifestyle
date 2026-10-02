"""Password hashing, signed values, CSRF and rate limiting.

All primitives come from the standard library: PBKDF2-HMAC-SHA256 for
passwords, HMAC-SHA256 for cookie signatures, and constant-time comparison
everywhere a secret is checked.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time
import unicodedata
from typing import Any

from . import db
from .config import config

# OWASP's 2023 floor for PBKDF2-HMAC-SHA256 is 600k; 240k is a deliberate
# balance for a single-box deployment.  Overridable so the test-suite can run
# the same code path cheaply -- never lower it in production.
PBKDF2_ROUNDS = max(1000, int(os.environ.get("MOG_PBKDF2_ROUNDS", 240_000)))
SALT_BYTES = 16


# ------------------------------------------------------------- passwords

def hash_password(password: str, *, rounds: int = PBKDF2_ROUNDS) -> str:
    """Return `pbkdf2_sha256$rounds$salt$hash` (all base64, no padding)."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _unb64(salt), int(rounds)
        )
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    return hmac.compare_digest(expected, _unb64(digest))


def password_problems(password: str) -> list[str]:
    """Human-readable reasons a password is unacceptable (empty list == fine)."""
    problems = []
    if len(password) < 10:
        problems.append("Use at least 10 characters.")
    if password.lower() in _COMMON_PASSWORDS:
        problems.append("That password is too common.")
    if password and password.strip() == "":
        problems.append("Password cannot be only whitespace.")
    return problems


_COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein123", "iloveyou1", "admin12345",
    "welcome123", "changeme1", "moglifestyle",
}


# ------------------------------------------------------- signing / tokens

def sign(value: str) -> str:
    """Attach an HMAC tag: `value.tag`."""
    tag = hmac.new(config.secret_key.encode(), value.encode(), hashlib.sha256).digest()
    return f"{value}.{_b64(tag)}"


def unsign(signed: str) -> str | None:
    """Return the value if the tag verifies, else None."""
    if not signed or "." not in signed:
        return None
    value, _, tag = signed.rpartition(".")
    expected = hmac.new(
        config.secret_key.encode(), value.encode(), hashlib.sha256
    ).digest()
    try:
        provided = _unb64(tag)
    except (ValueError, base64.binascii.Error):
        return None
    return value if hmac.compare_digest(expected, provided) else None


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_hmac_sha256(secret: str, payload: bytes, expected_hex: str) -> bool:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected_hex)


# ---------------------------------------------------------- rate limiting

def rate_limit(bucket: str, *, limit: int, per_seconds: float) -> bool:
    """Token bucket backed by SQLite.  Returns True when the call is allowed."""
    now = time.time()
    rate = limit / per_seconds
    with db.tx() as conn:
        row = conn.execute(
            "SELECT tokens, updated_at FROM rate_limits WHERE bucket = ?", (bucket,)
        ).fetchone()
        if row is None:
            tokens = float(limit)
            last = now
        else:
            tokens, last = float(row["tokens"]), float(row["updated_at"])
            tokens = min(float(limit), tokens + (now - last) * rate)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        conn.execute(
            "INSERT INTO rate_limits (bucket, tokens, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(bucket) DO UPDATE SET tokens = excluded.tokens, "
            "updated_at = excluded.updated_at",
            (bucket, tokens, now),
        )
    return allowed


def clear_rate_limit(bucket: str) -> None:
    db.execute("DELETE FROM rate_limits WHERE bucket = ?", (bucket,))


# ------------------------------------------------------------- spam traps

HONEYPOT_FIELD = "company_website"
TIMESTAMP_FIELD = "form_started"
# How fast a submission has to arrive before we treat it as automated.
# Configurable so the test-suite can exercise the honeypot arm without
# sleeping; never set this to 0 in production.
MIN_SECONDS_TO_FILL = float(os.environ.get("MOG_MIN_FORM_SECONDS", 2.0))
MAX_FORM_AGE_SECONDS = 60 * 60 * 6


def form_timestamp() -> str:
    """A signed render time, so the check cannot be forged or replayed."""
    return sign(str(int(time.time())))


def spam_signals(honeypot: str, timestamp: str, *, now: float | None = None) -> str:
    """Return a reason the submission looks automated, or "" if it looks human.

    Two signals, both invisible to a real visitor and free of third parties:
    a field no human can see (bots fill every input), and the time between
    the form rendering and arriving back (bots post instantly).
    """
    if honeypot.strip():
        return "honeypot"

    raw = unsign(timestamp or "")
    if raw is None or not raw.isdigit():
        return "missing-timestamp"

    elapsed = (time.time() if now is None else now) - int(raw)
    if elapsed < MIN_SECONDS_TO_FILL:
        return "too-fast"
    if elapsed > MAX_FORM_AGE_SECONDS:
        return "stale-form"
    return ""


# -------------------------------------------------------------- utilities

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def valid_email(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and len(value) <= 254 and bool(EMAIL_RE.match(value))


def slugify(value: str, *, fallback: str = "item") -> str:
    normalised = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalised.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or fallback


def audit(action: str, *, actor: str = "system", subject: str = "",
          detail: str = "", ip: str = "") -> None:
    db.insert(
        "audit_log", actor=actor, action=action,
        subject=str(subject), detail=detail, ip=ip,
    )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
