"""The shopping cart and its pricing.

A cart is keyed by an opaque id stored in a signed cookie, so guests can shop
without an account; on sign-in the cart is claimed by the user.  All money is
integer cents -- no floats touch a price anywhere in this codebase.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from . import catalog, db, discounts
from .config import config
from .security import new_token

MAX_QUANTITY_PER_LINE = 10


@dataclass
class Line:
    variant_id: int
    quantity: int
    sku: str
    title: str
    slug: str
    art_seed: str
    size: str
    color: str
    unit_cents: int
    available: int

    @property
    def total_cents(self) -> int:
        return self.unit_cents * self.quantity

    @property
    def variant_label(self) -> str:
        parts = [p for p in (self.size, self.color) if p]
        return " / ".join(parts)

    @property
    def over_stock(self) -> bool:
        return self.quantity > self.available


@dataclass
class Totals:
    subtotal_cents: int = 0
    discount_cents: int = 0
    shipping_cents: int = 0
    tax_cents: int = 0
    total_cents: int = 0
    discount_code: str = ""
    discount_label: str = ""
    free_shipping_remaining: int = 0


@dataclass
class Cart:
    id: str
    lines: list[Line] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)
    discount_row: sqlite3.Row | None = None

    @property
    def count(self) -> int:
        return sum(line.quantity for line in self.lines)

    @property
    def empty(self) -> bool:
        return not self.lines

    @property
    def has_stock_problem(self) -> bool:
        return any(line.over_stock for line in self.lines)


# ------------------------------------------------------------- lifecycle

def ensure_cart(cart_id: str | None, user_id: int | None = None) -> str:
    """Return a valid cart id, creating one if the cookie is missing or stale."""
    if cart_id:
        row = db.one("SELECT id FROM carts WHERE id = ?", (cart_id,))
        if row:
            if user_id:
                db.execute(
                    "UPDATE carts SET user_id = ? WHERE id = ? AND user_id IS NULL",
                    (user_id, cart_id),
                )
            return cart_id
    new_id = new_token(18)
    with db.tx():
        db.insert("carts", id=new_id, user_id=user_id)
    return new_id


def load(cart_id: str | None) -> Cart:
    if not cart_id:
        return Cart(id="", totals=compute(Cart(id=""), None))
    rows = db.query(
        "SELECT ci.variant_id, ci.quantity, v.sku, v.size, v.color, "
        "       COALESCE(v.price_cents, p.price_cents) AS unit_cents, "
        "       MAX(v.stock - v.reserved, 0) AS available, "
        "       p.title, p.slug, p.art_seed "
        "FROM cart_items ci "
        "JOIN variants v ON v.id = ci.variant_id "
        "JOIN products p ON p.id = v.product_id "
        "WHERE ci.cart_id = ? AND p.status = 'active' "
        "ORDER BY ci.added_at, ci.id",
        (cart_id,),
    )
    lines = [
        Line(
            variant_id=r["variant_id"], quantity=r["quantity"], sku=r["sku"],
            title=r["title"], slug=r["slug"], art_seed=r["art_seed"],
            size=r["size"], color=r["color"], unit_cents=r["unit_cents"],
            available=r["available"],
        )
        for r in rows
    ]
    cart_row = db.one("SELECT * FROM carts WHERE id = ?", (cart_id,))
    discount_row = None
    if cart_row and cart_row["discount_id"]:
        discount_row = db.one(
            "SELECT * FROM discounts WHERE id = ?", (cart_row["discount_id"],)
        )
    cart = Cart(id=cart_id, lines=lines, discount_row=discount_row)
    cart.totals = compute(cart, discount_row)
    return cart


def count_for(cart_id: str | None) -> int:
    if not cart_id:
        return 0
    return int(db.scalar(
        "SELECT COALESCE(SUM(quantity), 0) FROM cart_items WHERE cart_id = ?",
        (cart_id,), 0,
    ))


# -------------------------------------------------------------- mutation

def add(cart_id: str, variant_id: int, quantity: int = 1) -> Line:
    variant = catalog.get_variant(variant_id)
    if variant is None or variant["status"] != "active":
        raise ValueError("That product is no longer available.")
    quantity = max(1, min(MAX_QUANTITY_PER_LINE, quantity))

    with db.tx():
        existing = db.one(
            "SELECT quantity FROM cart_items WHERE cart_id = ? AND variant_id = ?",
            (cart_id, variant_id),
        )
        wanted = quantity + (existing["quantity"] if existing else 0)
        wanted = min(wanted, MAX_QUANTITY_PER_LINE)
        if variant["available"] < 1:
            raise ValueError("That size is sold out.")
        if wanted > variant["available"]:
            wanted = variant["available"]
        if existing:
            db.update("cart_items", "cart_id = ? AND variant_id = ?",
                      (cart_id, variant_id), quantity=wanted)
        else:
            db.insert("cart_items", cart_id=cart_id, variant_id=variant_id,
                      quantity=wanted)
        db.execute("UPDATE carts SET updated_at = datetime('now') WHERE id = ?",
                   (cart_id,))

    return Line(
        variant_id=variant_id, quantity=wanted, sku=variant["sku"],
        title=variant["title"], slug=variant["slug"], art_seed=variant["art_seed"],
        size=variant["size"], color=variant["color"],
        unit_cents=variant["effective_cents"], available=variant["available"],
    )


def set_quantity(cart_id: str, variant_id: int, quantity: int) -> None:
    if quantity <= 0:
        remove(cart_id, variant_id)
        return
    variant = catalog.get_variant(variant_id)
    if variant is None:
        remove(cart_id, variant_id)
        return
    quantity = max(1, min(MAX_QUANTITY_PER_LINE, quantity, variant["available"] or 1))
    with db.tx():
        db.update("cart_items", "cart_id = ? AND variant_id = ?",
                  (cart_id, variant_id), quantity=quantity)
        db.execute("UPDATE carts SET updated_at = datetime('now') WHERE id = ?",
                   (cart_id,))


def remove(cart_id: str, variant_id: int) -> None:
    with db.tx():
        db.execute("DELETE FROM cart_items WHERE cart_id = ? AND variant_id = ?",
                   (cart_id, variant_id))


def clear(cart_id: str) -> None:
    with db.tx():
        db.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
        db.update("carts", "id = ?", (cart_id,), discount_id=None)


def apply_discount(cart_id: str, code: str) -> discounts.DiscountResult:
    cart = load(cart_id)
    result = discounts.validate(code, cart.totals.subtotal_cents)
    if result.ok and result.row is not None:
        with db.tx():
            db.update("carts", "id = ?", (cart_id,), discount_id=result.row["id"])
    return result


def clear_discount(cart_id: str) -> None:
    with db.tx():
        db.update("carts", "id = ?", (cart_id,), discount_id=None)


# --------------------------------------------------------------- pricing

def compute(cart: Cart, discount_row: sqlite3.Row | None) -> Totals:
    """Derive every money figure from the lines.  Pure -- no I/O, no floats."""
    subtotal = sum(line.total_cents for line in cart.lines)
    totals = Totals(subtotal_cents=subtotal)
    if not cart.lines:
        return totals

    shipping = config.shipping_flat_cents
    threshold = config.free_shipping_threshold_cents
    if threshold and subtotal >= threshold:
        shipping = 0
    else:
        totals.free_shipping_remaining = max(0, threshold - subtotal)

    discount_cents, shipping = discounts.amount_off(discount_row, subtotal, shipping)
    if discount_row is not None:
        totals.discount_code = discount_row["code"].upper()
        totals.discount_label = discounts.describe(discount_row)

    taxable = max(0, subtotal - discount_cents)
    tax = taxable * config.tax_rate_bps // 10_000

    totals.discount_cents = discount_cents
    totals.shipping_cents = shipping
    totals.tax_cents = tax
    totals.total_cents = max(0, taxable + shipping + tax)
    return totals
