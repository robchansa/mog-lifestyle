"""Users, sessions and authentication.

Sessions live in the database and are addressed by an HMAC-signed cookie, so a
tampered or forged cookie is rejected before it ever reaches a query.  Failed
logins both lock the account briefly and burn a per-IP rate-limit token.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from . import db
from .config import config
from .security import (
    audit, hash_password, new_token, password_problems, rate_limit,
    sign, unsign, valid_email, verify_password,
)

SESSION_COOKIE = "mog_session"
CART_COOKIE = "mog_cart"
MAX_FAILED_LOGINS = 6
LOCKOUT_MINUTES = 15


class AuthError(Exception):
    """Login or registration failed for a reason worth showing the user."""


# --------------------------------------------------------------- sessions

def _expiry(days: int | None = None) -> str:
    when = datetime.now(timezone.utc) + timedelta(days=days or config.session_days)
    return when.strftime("%Y-%m-%d %H:%M:%S")


def create_session(user_id: int | None, *, user_agent: str = "",
                   ip: str = "") -> sqlite3.Row:
    session_id = new_token(32)
    with db.tx():
        db.insert(
            "sessions", id=session_id, user_id=user_id, csrf_token=new_token(24),
            expires_at=_expiry(), user_agent=user_agent[:250], ip=ip[:64],
        )
    return db.one("SELECT * FROM sessions WHERE id = ?", (session_id,))


def load_session(cookie_value: str | None) -> sqlite3.Row | None:
    if not cookie_value:
        return None
    session_id = unsign(cookie_value)
    if not session_id:
        return None
    return db.one(
        "SELECT * FROM sessions WHERE id = ? AND expires_at > datetime('now')",
        (session_id,),
    )


def session_cookie_value(session: sqlite3.Row) -> str:
    return sign(session["id"])


def destroy_session(session_id: str) -> None:
    with db.tx():
        db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def rotate_session(session: sqlite3.Row, user_id: int) -> sqlite3.Row:
    """Issue a brand-new session id on privilege change (fixation defence)."""
    fresh = create_session(user_id, user_agent=session["user_agent"], ip=session["ip"])
    with db.tx():
        db.execute(
            "UPDATE carts SET user_id = ? WHERE id IN "
            "(SELECT id FROM carts WHERE user_id IS NULL AND id = ?)",
            (user_id, session["id"]),
        )
        db.execute("DELETE FROM sessions WHERE id = ?", (session["id"],))
    return fresh


def purge_expired() -> int:
    with db.tx():
        cur = db.execute("DELETE FROM sessions WHERE expires_at <= datetime('now')")
    return cur.rowcount


# ------------------------------------------------------------------ users

def get_user(user_id: int | None) -> sqlite3.Row | None:
    if not user_id:
        return None
    return db.one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_email(email: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM users WHERE email = ?", (email.strip(),))


def register(email: str, password: str, *, name: str = "",
             marketing_opt_in: bool = False, role: str = "customer") -> sqlite3.Row:
    email = (email or "").strip()
    if not valid_email(email):
        raise AuthError("Enter a valid email address.")
    problems = password_problems(password)
    if problems:
        raise AuthError(" ".join(problems))
    if get_user_by_email(email):
        raise AuthError("An account with that email already exists.")
    with db.tx():
        user_id = db.insert(
            "users", email=email, password_hash=hash_password(password),
            name=name.strip()[:120], role=role,
            marketing_opt_in=1 if marketing_opt_in else 0,
        )
        if marketing_opt_in:
            db.execute(
                "INSERT OR IGNORE INTO newsletter (email, source) VALUES (?, 'signup')",
                (email,),
            )
    audit("user.register", actor=email, subject=str(user_id))
    return get_user(user_id)


def authenticate(email: str, password: str, *, ip: str = "") -> sqlite3.Row:
    email = (email or "").strip()
    if not rate_limit(f"login:{ip}", limit=12, per_seconds=300):
        raise AuthError("Too many attempts. Please wait a minute and try again.")

    user = get_user_by_email(email)
    if user is None:
        # Spend comparable time so a missing account isn't distinguishable.
        verify_password(password, hash_password("decoy-value"))
        raise AuthError("Email or password is incorrect.")

    if user["locked_until"]:
        locked = db.scalar(
            "SELECT locked_until > datetime('now') FROM users WHERE id = ?",
            (user["id"],), 0,
        )
        if locked:
            raise AuthError(
                f"Account temporarily locked after repeated failures. "
                f"Try again in {LOCKOUT_MINUTES} minutes."
            )

    if not verify_password(password, user["password_hash"]):
        _record_failure(user)
        raise AuthError("Email or password is incorrect.")

    with db.tx():
        db.update("users", "id = ?", (user["id"],), failed_logins=0, locked_until=None)
    audit("user.login", actor=email, subject=str(user["id"]), ip=ip)
    return get_user(user["id"])


def _record_failure(user: sqlite3.Row) -> None:
    failures = user["failed_logins"] + 1
    values: dict[str, Any] = {"failed_logins": failures}
    if failures >= MAX_FAILED_LOGINS:
        values["locked_until"] = (
            datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_MINUTES)
        ).strftime("%Y-%m-%d %H:%M:%S")
        values["failed_logins"] = 0
        audit("user.locked", actor=user["email"], subject=str(user["id"]))
    with db.tx():
        db.update("users", "id = ?", (user["id"],), **values)


def change_password(user_id: int, current: str, replacement: str) -> None:
    user = get_user(user_id)
    if user is None or not verify_password(current, user["password_hash"]):
        raise AuthError("Your current password is incorrect.")
    problems = password_problems(replacement)
    if problems:
        raise AuthError(" ".join(problems))
    with db.tx():
        db.update("users", "id = ?", (user_id,), password_hash=hash_password(replacement))
        # Every other session for this user is invalidated.
        db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    audit("user.password_change", actor=user["email"], subject=str(user_id))


def update_profile(user_id: int, *, name: str, marketing_opt_in: bool) -> None:
    with db.tx():
        db.update(
            "users", "id = ?", (user_id,),
            name=name.strip()[:120], marketing_opt_in=1 if marketing_opt_in else 0,
        )


# -------------------------------------------------------------- addresses

def addresses_for(user_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id DESC",
        (user_id,),
    )


def default_address(user_id: int | None) -> sqlite3.Row | None:
    if not user_id:
        return None
    return db.one(
        "SELECT * FROM addresses WHERE user_id = ? "
        "ORDER BY is_default DESC, id DESC LIMIT 1",
        (user_id,),
    )


def save_address(user_id: int, *, address_id: int | None = None,
                 make_default: bool = True, **fields: Any) -> int:
    with db.tx():
        if make_default:
            db.execute("UPDATE addresses SET is_default = 0 WHERE user_id = ?", (user_id,))
        if address_id:
            db.update("addresses", "id = ? AND user_id = ?", (address_id, user_id),
                      is_default=1 if make_default else 0, **fields)
            return address_id
        return db.insert("addresses", user_id=user_id,
                         is_default=1 if make_default else 0, **fields)


def delete_address(user_id: int, address_id: int) -> None:
    with db.tx():
        db.execute("DELETE FROM addresses WHERE id = ? AND user_id = ?",
                   (address_id, user_id))
