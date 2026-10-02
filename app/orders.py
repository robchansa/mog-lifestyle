"""Orders: creation, payment reconciliation, fulfilment and refunds.

The invariant that matters: **stock and orders move together or not at all.**
`create_from_cart` reserves every line inside one transaction, so two shoppers
racing for the last unit cannot both win.  Payment then converts reservations
into real depletions; cancellation and expiry hand them back.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import cart as cart_module
from . import catalog, db, discounts, mailer
from .config import config
from .security import audit

RESERVATION_MINUTES = 45
STATUS_LABELS = {
    "pending": "Awaiting payment",
    "paid": "Paid",
    "fulfilled": "Shipped",
    "cancelled": "Cancelled",
    "refunded": "Refunded",
}


class CheckoutError(Exception):
    """Checkout could not proceed; the message is safe to show the customer."""


# ----------------------------------------------------------------- numbers

def next_number() -> str:
    """Sequential, human-quotable order number: MOG-4021."""
    last = db.scalar("SELECT MAX(id) FROM orders", (), 0) or 0
    return f"MOG-{4000 + int(last) + 1}"


# ---------------------------------------------------------------- creation

def create_from_cart(cart: cart_module.Cart, *, email: str, user_id: int | None,
                     shipping: Mapping[str, str], notes: str = "") -> sqlite3.Row:
    """Turn a cart into a pending order, reserving stock atomically."""
    if cart.empty:
        raise CheckoutError("Your bag is empty.")

    with db.tx():
        # Re-read availability inside the transaction; the cart snapshot is stale
        # the moment it is rendered.
        for line in cart.lines:
            try:
                catalog.reserve(line.variant_id, line.quantity)
            except catalog.OutOfStock as exc:
                raise CheckoutError(
                    f"{line.title} ({line.variant_label or 'one size'}): {exc}"
                ) from exc

        totals = cart.totals
        number = next_number()
        order_id = db.insert(
            "orders",
            number=number, user_id=user_id, email=email.strip(),
            status="pending",
            subtotal_cents=totals.subtotal_cents,
            discount_cents=totals.discount_cents,
            shipping_cents=totals.shipping_cents,
            tax_cents=totals.tax_cents,
            total_cents=totals.total_cents,
            discount_code=totals.discount_code,
            ship_name=shipping.get("name", "")[:120],
            ship_line1=shipping.get("line1", "")[:160],
            ship_line2=shipping.get("line2", "")[:160],
            ship_city=shipping.get("city", "")[:80],
            ship_region=shipping.get("region", "")[:80],
            ship_postal=shipping.get("postal", "")[:24],
            ship_country=shipping.get("country", "US")[:2].upper(),
            ship_phone=shipping.get("phone", "")[:40],
            notes=notes[:500],
        )
        for line in cart.lines:
            db.insert(
                "order_items", order_id=order_id, variant_id=line.variant_id,
                sku=line.sku, title=line.title, variant_label=line.variant_label,
                slug=line.slug, art_seed=line.art_seed,
                unit_cents=line.unit_cents, quantity=line.quantity,
                department=line.department, on_sale=1 if line.on_sale else 0,
            )

    audit("order.create", actor=email, subject=number,
          detail=f"{cart.count} items, {totals.total_cents}c")
    return get(order_id)


# ------------------------------------------------------------------- reads

def get(order_id: int) -> sqlite3.Row | None:
    return db.one("SELECT * FROM orders WHERE id = ?", (order_id,))


def get_by_number(number: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM orders WHERE number = ?", ((number or "").strip(),))


def get_by_payment_ref(reference: str) -> sqlite3.Row | None:
    return db.one("SELECT * FROM orders WHERE payment_ref = ?", (reference,))


def items_for(order_id: int) -> list[sqlite3.Row]:
    return db.query("SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
                    (order_id,))


def for_user(user_id: int, limit: int = 50) -> list[sqlite3.Row]:
    return db.query(
        "SELECT o.*, (SELECT COALESCE(SUM(quantity), 0) FROM order_items oi "
        "             WHERE oi.order_id = o.id) AS item_count "
        "FROM orders o WHERE o.user_id = ? AND o.status <> 'pending' "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT ?",
        (user_id, limit),
    )


def recent(limit: int = 25, *, status: str = "", search: str = "") -> list[sqlite3.Row]:
    where, params = ["1 = 1"], []
    if status in STATUS_LABELS:
        where.append("o.status = ?")
        params.append(status)
    if search:
        where.append("(o.number LIKE ? OR o.email LIKE ? OR o.ship_name LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    return db.query(
        f"SELECT o.*, (SELECT COALESCE(SUM(quantity), 0) FROM order_items oi "
        f"             WHERE oi.order_id = o.id) AS item_count "
        f"FROM orders o WHERE {' AND '.join(where)} "
        f"ORDER BY o.created_at DESC, o.id DESC LIMIT ?",
        [*params, limit],
    )


# ---------------------------------------------------------- state changes

def attach_payment(order_id: int, session_id: str) -> None:
    with db.tx():
        db.update("orders", "id = ?", (order_id,), payment_ref=session_id)


def mark_paid(order: sqlite3.Row, *, payment_intent: str = "",
              send_email: bool = True) -> sqlite3.Row:
    """Idempotent: safe to call from both the return URL and the webhook."""
    if order["status"] != "pending":
        return get(order["id"])

    with db.tx():
        changed = db.execute(
            "UPDATE orders SET status = 'paid', paid_at = datetime('now'), "
            "payment_intent = ? WHERE id = ? AND status = 'pending'",
            (payment_intent, order["id"]),
        ).rowcount
        if not changed:
            return get(order["id"])
        for item in items_for(order["id"]):
            if item["variant_id"]:
                catalog.commit_reservation(item["variant_id"], item["quantity"])
        if order["discount_code"]:
            row = discounts.find(order["discount_code"])
            discounts.record_use(row["id"] if row else None)

    fresh = get(order["id"])
    audit("order.paid", actor=order["email"], subject=order["number"],
          detail=f"{order['total_cents']}c")
    if send_email:
        try:
            mailer.order_confirmation(fresh, items_for(order["id"]),
                                      to_address=order["email"])
        except Exception:                                    # noqa: BLE001
            pass                       # queued row survives; admin can retry
        _maybe_low_stock_alert(order["id"])
    return fresh


def _maybe_low_stock_alert(order_id: int) -> None:
    variant_ids = [i["variant_id"] for i in items_for(order_id) if i["variant_id"]]
    if not variant_ids:
        return
    marks = ", ".join("?" * len(variant_ids))
    rows = db.query(
        f"SELECT v.sku, p.title, MAX(v.stock - v.reserved, 0) AS available "
        f"FROM variants v JOIN products p ON p.id = v.product_id "
        f"WHERE v.id IN ({marks}) AND v.stock - v.reserved <= v.low_stock_at",
        variant_ids,
    )
    if rows:
        try:
            mailer.low_stock_alert(rows, to_address=config.store_email)
        except Exception:                                    # noqa: BLE001
            pass


def cancel(order: sqlite3.Row, *, reason: str = "", actor: str = "system") -> sqlite3.Row:
    """Cancel a pending order and return its reserved stock."""
    if order["status"] not in ("pending", "paid"):
        return get(order["id"])
    with db.tx():
        changed = db.execute(
            "UPDATE orders SET status = 'cancelled', cancelled_at = datetime('now'), "
            "notes = TRIM(notes || ' ' || ?) WHERE id = ? AND status IN ('pending','paid')",
            (reason, order["id"]),
        ).rowcount
        if not changed:
            return get(order["id"])
        for item in items_for(order["id"]):
            if not item["variant_id"]:
                continue
            if order["status"] == "pending":
                catalog.release(item["variant_id"], item["quantity"])
            else:
                catalog.restock(item["variant_id"], item["quantity"])
    audit("order.cancel", actor=actor, subject=order["number"], detail=reason)
    return get(order["id"])


def fulfil(order: sqlite3.Row, *, carrier: str = "", tracking: str = "",
           actor: str = "system", send_email: bool = True) -> sqlite3.Row:
    if order["status"] not in ("paid", "fulfilled"):
        raise CheckoutError("Only paid orders can be marked shipped.")
    with db.tx():
        db.update(
            "orders", "id = ?", (order["id"],),
            status="fulfilled", fulfilled_at=_now(),
            tracking_carrier=carrier[:60], tracking_number=tracking[:80],
        )
    fresh = get(order["id"])
    audit("order.fulfil", actor=actor, subject=order["number"],
          detail=f"{carrier} {tracking}".strip())
    if send_email:
        try:
            mailer.shipping_notice(fresh, to_address=order["email"])
        except Exception:                                    # noqa: BLE001
            pass
    return fresh


def refund(order: sqlite3.Row, *, actor: str = "system",
           restock_items: bool = True) -> sqlite3.Row:
    from . import stripe_api
    if order["status"] not in ("paid", "fulfilled"):
        raise CheckoutError("Only paid orders can be refunded.")
    if order["payment_intent"]:
        try:
            stripe_api.refund(order["payment_intent"])
        except stripe_api.StripeError as exc:
            raise CheckoutError(f"Stripe refused the refund: {exc}") from exc
    with db.tx():
        db.update("orders", "id = ?", (order["id"],), status="refunded",
                  refunded_at=_now())
        if restock_items:
            for item in items_for(order["id"]):
                if item["variant_id"]:
                    catalog.restock(item["variant_id"], item["quantity"])
    audit("order.refund", actor=actor, subject=order["number"],
          detail=f"{order['total_cents']}c")
    return get(order["id"])


def expire_stale(minutes: int = RESERVATION_MINUTES) -> int:
    """Release stock held by abandoned pending orders.  Returns count released."""
    rows = db.query(
        "SELECT * FROM orders WHERE status = 'pending' "
        "AND created_at < datetime('now', ?)",
        (f"-{int(minutes)} minutes",),
    )
    for order in rows:
        cancel(order, reason="[auto-expired]", actor="system")
    return len(rows)


# ------------------------------------------------------------- line items

def to_stripe_line_items(order_id: int) -> list[dict[str, Any]]:
    return [
        {
            "quantity": item["quantity"],
            "unit_cents": item["unit_cents"],
            "name": item["title"],
            "description": item["variant_label"] or None,
        }
        for item in items_for(order_id)
    ]


def status_badge_tone(status: str) -> str:
    return status if status in STATUS_LABELS else "neutral"


def _now() -> str:
    return db.scalar("SELECT datetime('now')")


# ------------------------------------------------------------- reporting

def metrics(days: int = 30) -> dict[str, Any]:
    window = f"-{int(days)} days"
    paid_states = ("paid", "fulfilled")
    marks = ", ".join("?" * len(paid_states))
    revenue = db.scalar(
        f"SELECT COALESCE(SUM(total_cents), 0) FROM orders "
        f"WHERE status IN ({marks}) AND created_at >= datetime('now', ?)",
        [*paid_states, window], 0,
    )
    order_count = db.scalar(
        f"SELECT count(*) FROM orders WHERE status IN ({marks}) "
        f"AND created_at >= datetime('now', ?)",
        [*paid_states, window], 0,
    )
    return {
        "revenue_cents": int(revenue),
        "orders": int(order_count),
        "aov_cents": int(revenue // order_count) if order_count else 0,
        "pending": int(db.scalar(
            "SELECT count(*) FROM orders WHERE status = 'pending'", (), 0)),
        "awaiting_fulfilment": int(db.scalar(
            "SELECT count(*) FROM orders WHERE status = 'paid'", (), 0)),
        "customers": int(db.scalar(
            "SELECT count(*) FROM users WHERE role = 'customer'", (), 0)),
        "subscribers": int(db.scalar("SELECT count(*) FROM newsletter", (), 0)),
        "unread_messages": int(db.scalar(
            "SELECT count(*) FROM messages WHERE handled = 0", (), 0)),
        "low_stock": len(catalog.low_stock()),
    }


def revenue_series(days: int = 14) -> list[tuple[str, int]]:
    rows = db.query(
        "SELECT date(created_at) AS day, COALESCE(SUM(total_cents), 0) AS cents "
        "FROM orders WHERE status IN ('paid','fulfilled') "
        "AND created_at >= datetime('now', ?) GROUP BY day ORDER BY day",
        (f"-{int(days)} days",),
    )
    by_day = {r["day"]: int(r["cents"]) for r in rows}
    today = datetime.now(timezone.utc).date()
    series: list[tuple[str, int]] = []
    for offset in range(days - 1, -1, -1):
        day = today.fromordinal(today.toordinal() - offset).isoformat()
        series.append((day, by_day.get(day, 0)))
    return series


def best_sellers(limit: int = 5) -> list[sqlite3.Row]:
    return db.query(
        "SELECT oi.title, oi.slug, SUM(oi.quantity) AS units, "
        "       SUM(oi.quantity * oi.unit_cents) AS cents "
        "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        "WHERE o.status IN ('paid','fulfilled') "
        "GROUP BY oi.title, oi.slug ORDER BY units DESC LIMIT ?",
        (limit,),
    )
