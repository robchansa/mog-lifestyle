"""Promotion codes: percent off, fixed amount off, or free shipping."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import db


@dataclass
class DiscountResult:
    ok: bool
    message: str = ""
    row: sqlite3.Row | None = None


def find(code: str) -> sqlite3.Row | None:
    code = (code or "").strip()
    if not code:
        return None
    return db.one("SELECT * FROM discounts WHERE code = ?", (code,))


def validate(code: str, subtotal_cents: int) -> DiscountResult:
    row = find(code)
    if row is None:
        return DiscountResult(False, "That code isn't recognised.")
    if not row["active"]:
        return DiscountResult(False, "That code is no longer active.")
    expired = db.scalar(
        "SELECT (starts_at IS NOT NULL AND starts_at > datetime('now')) "
        "    OR (ends_at   IS NOT NULL AND ends_at   < datetime('now')) "
        "FROM discounts WHERE id = ?",
        (row["id"],), 0,
    )
    if expired:
        return DiscountResult(False, "That code has expired.")
    if row["max_uses"] is not None and row["uses"] >= row["max_uses"]:
        return DiscountResult(False, "That code has reached its limit.")
    if subtotal_cents < row["min_spend_cents"]:
        from .ui import money
        return DiscountResult(
            False, f"Spend {money(row['min_spend_cents'])} to use this code."
        )
    return DiscountResult(True, f"Code {row['code'].upper()} applied.", row)


def amount_off(row: sqlite3.Row | None, subtotal_cents: int,
               shipping_cents: int) -> tuple[int, int]:
    """Return (discount_cents, shipping_cents) after applying the promotion."""
    if row is None:
        return 0, shipping_cents
    if row["kind"] == "percent":
        return min(subtotal_cents, subtotal_cents * row["value"] // 100), shipping_cents
    if row["kind"] == "fixed":
        return min(subtotal_cents, row["value"]), shipping_cents
    if row["kind"] == "free_shipping":
        return 0, 0
    return 0, shipping_cents


def describe(row: sqlite3.Row) -> str:
    if row["kind"] == "percent":
        return f"{row['value']}% off"
    if row["kind"] == "fixed":
        from .ui import money
        return f"{money(row['value'])} off"
    return "Free shipping"


def record_use(discount_id: int | None) -> None:
    if discount_id:
        db.execute("UPDATE discounts SET uses = uses + 1 WHERE id = ?", (discount_id,))


def list_all() -> list[sqlite3.Row]:
    return db.query("SELECT * FROM discounts ORDER BY active DESC, id DESC")
