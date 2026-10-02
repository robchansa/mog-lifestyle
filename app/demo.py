"""Synthetic trading history, so the Analytics page can be shown before launch.

    python3 run.py --demo-history          # ~13 months of visits, orders, sign-ups
    python3 run.py --demo-history clear    # remove every demo row again

Nothing here can be mistaken for, or mixed up with, real trade:

* it refuses to run when `MOG_ENV=production`;
* every row is tagged -- orders carry `[demo]` in their notes, people use the
  reserved `demo.example.com` domain (RFC 2606: mail to it cannot be
  delivered), visitor ids start with `demo` (real ones are hex, so never do),
  and sign-ups have source `demo`;
* demo accounts have no usable password, so nobody can sign in as one;
* `clear()` removes exactly those rows and nothing else.

The shape is deliberately realistic -- a store growing over the year, busier
at weekends and evenings, a Black Friday spike, roughly 2–3% conversion, most
traffic on phones and from Instagram -- so the charts look like the ones the
owner will actually read.
"""
from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from . import db
from .config import config

DEMO_DOMAIN = "demo.example.com"
DEMO_NOTE = "[demo]"
DEMO_VISITOR = "demo"
DEMO_SOURCE = "demo"
DEFAULT_DAYS = 395            # inside the 400-day traffic retention window
UNUSABLE_PASSWORD = "!demo-account-cannot-sign-in"

FIRST = ["Ava", "Jordan", "Mia", "Marcus", "Sofia", "Tyler", "Chloe", "Andre",
         "Grace", "Elijah", "Nora", "Isaiah", "Layla", "Caleb", "Zoe", "Darius",
         "Hannah", "Mateo", "Leah", "Jalen", "Ruby", "Owen", "Imani", "Lucas"]
LAST = ["Johnson", "Rivera", "Chen", "Williams", "Okafor", "Nguyen", "Brooks",
        "Martinez", "Patel", "Hughes", "Kim", "Foster", "Bennett", "Reyes",
        "Coleman", "Price", "Ward", "Sanders", "Diaz", "Morgan"]
CITIES = [("Boise", "ID", "83702"), ("Meridian", "ID", "83642"),
          ("Salt Lake City", "UT", "84101"), ("Denver", "CO", "80202"),
          ("Phoenix", "AZ", "85004"), ("Los Angeles", "CA", "90012"),
          ("Seattle", "WA", "98101"), ("Portland", "OR", "97205"),
          ("Austin", "TX", "78701"), ("Chicago", "IL", "60601"),
          ("Atlanta", "GA", "30303"), ("New York", "NY", "10001"),
          ("Miami", "FL", "33130"), ("Las Vegas", "NV", "89101")]
REFERRERS = [("", 44), ("l.instagram.com", 18), ("www.instagram.com", 9),
             ("www.google.com", 14), ("www.tiktok.com", 6), ("m.facebook.com", 4),
             ("www.pinterest.com", 3), ("www.bing.com", 2)]
DEVICES = [("mobile", 63), ("desktop", 30), ("tablet", 7)]
BROWSE_PAGES = ["/", "/", "/", "/shop", "/shop", "/shop?department=men",
                "/shop?department=women", "/shop?department=general",
                "/shop?on_sale=1", "/about", "/faq", "/shipping", "/contact"]
# Evening-heavy shopping, by store-local hour.
HOUR_WEIGHTS = [1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 7, 8, 7, 6, 6, 7, 8, 10, 12,
                13, 12, 8, 4]


class DemoRefused(RuntimeError):
    """Demo history was asked for somewhere it must not go."""


def present() -> bool:
    return bool(db.scalar(
        "SELECT 1 FROM orders WHERE notes LIKE ? LIMIT 1", (f"%{DEMO_NOTE}%",)
    ) or db.scalar(
        "SELECT 1 FROM page_views WHERE visitor LIKE ? LIMIT 1", (f"{DEMO_VISITOR}%",)
    ))


def clear() -> dict[str, int]:
    """Delete every demo row, and only demo rows."""
    with db.tx():
        removed = {
            "orders": db.execute(
                "DELETE FROM orders WHERE notes LIKE ?", (f"%{DEMO_NOTE}%",)).rowcount,
            "customers": db.execute(
                "DELETE FROM users WHERE role = 'customer' AND email LIKE ?",
                (f"%@{DEMO_DOMAIN}",)).rowcount,
            "subscribers": db.execute(
                "DELETE FROM newsletter WHERE email LIKE ?",
                (f"%@{DEMO_DOMAIN}",)).rowcount,
            "page_views": db.execute(
                "DELETE FROM page_views WHERE visitor LIKE ?",
                (f"{DEMO_VISITOR}%",)).rowcount,
        }
        db.execute("DELETE FROM visits WHERE visitor LIKE ?", (f"{DEMO_VISITOR}%",))
    return removed


def _season(day: date) -> float:
    factor = {5: 1.25, 6: 1.2, 0: 0.9}.get(day.weekday(), 1.0)
    factor *= {1: 0.8, 2: 0.85, 6: 1.05, 7: 1.08, 11: 1.15, 12: 1.4}.get(day.month, 1.0)
    # Black Friday through Cyber Monday: the fourth Thursday of November + 4 days.
    november = date(day.year, 11, 1)
    thanksgiving = november + timedelta(days=(3 - november.weekday()) % 7 + 21)
    if thanksgiving + timedelta(days=1) <= day <= thanksgiving + timedelta(days=4):
        factor *= 2.6
    return factor


def _weighted(rng: random.Random, pairs: list[tuple[Any, int]]) -> Any:
    values, weights = zip(*pairs)
    return rng.choices(values, weights=weights)[0]


def _utc(local_day: date, rng: random.Random, after: datetime | None = None,
         now: datetime | None = None) -> datetime:
    """A plausible moment on `local_day` -- never later than `now`."""
    if after is not None:
        moment = after + timedelta(seconds=rng.randint(20, 900))
    else:
        hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
        moment = datetime.combine(
            local_day, time(hour, rng.randint(0, 59), rng.randint(0, 59)),
            tzinfo=config.zone).astimezone(timezone.utc)
    if now is not None and moment > now:
        moment = now - timedelta(seconds=rng.randint(60, 3 * 3600))
    return moment


def _ts(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def generate(*, days: int = DEFAULT_DAYS, seed: int = 2026,
             today: date | None = None, base_visitors: int = 70) -> dict[str, int]:
    """Write `days` of history ending today.  Replaces earlier demo history."""
    if config.is_production:
        raise DemoRefused("Refusing to write demo history into a production database.")
    products = db.query(
        "SELECT p.id, p.slug, p.title, p.art_seed, p.price_cents, p.compare_cents, "
        "  COALESCE(c.department, '') AS department, "
        "  (SELECT v.id FROM variants v WHERE v.product_id = p.id "
        "   ORDER BY v.position LIMIT 1) AS variant_id, "
        "  (SELECT v.sku FROM variants v WHERE v.product_id = p.id "
        "   ORDER BY v.position LIMIT 1) AS sku, "
        "  (SELECT v.size FROM variants v WHERE v.product_id = p.id "
        "   ORDER BY v.position LIMIT 1) AS size, "
        "  (SELECT v.color FROM variants v WHERE v.product_id = p.id "
        "   ORDER BY v.position LIMIT 1) AS color "
        "FROM products p LEFT JOIN collections c ON c.id = p.collection_id "
        "WHERE p.status = 'active' ORDER BY p.id"
    )
    products = [p for p in products if p["variant_id"]]
    if not products:
        raise DemoRefused("Seed the catalogue first: python3 run.py --seed")

    clear()
    rng = random.Random(seed)
    if today is None:
        today = config.local_now().date()
        now = datetime.now(timezone.utc)
    else:
        now = datetime.combine(today, time(23, 59, 59), tzinfo=config.zone) \
            .astimezone(timezone.utc)
    first_day = today - timedelta(days=days - 1)

    # A few products carry most of the volume, as in any real store.
    ranked = products[:]
    rng.shuffle(ranked)
    popularity = [(p, max(1, int(100 / (rank + 1) ** 0.8))) for rank, p in enumerate(ranked)]

    views: list[tuple] = []
    customers: list[str] = []
    buyers: list[tuple[str, str, int | None]] = []     # (email, name, user_id)
    counts = {"orders": 0, "customers": 0, "subscribers": 0, "page_views": 0}
    visitor_seq = 0

    with db.tx():
        for offset in range(days):
            day = first_day + timedelta(days=offset)
            growth = 0.35 + 0.65 * (offset / max(1, days - 1))
            expected = base_visitors * growth * _season(day)
            visitors = max(3, int(rng.gauss(expected, expected * 0.12)))

            for _ in range(visitors):
                visitor_seq += 1
                visitor = f"{DEMO_VISITOR}{visitor_seq:016x}"
                device = _weighted(rng, DEVICES)
                referrer = _weighted(rng, REFERRERS)
                moment = _utc(day, rng, now=now)
                pages = [rng.choice(BROWSE_PAGES)]
                pages += [rng.choice(BROWSE_PAGES) for _ in range(rng.randint(0, 2))]
                product = None
                if rng.random() < 0.56:
                    product = _weighted(rng, popularity)
                    pages.append(f"/product/{product['slug']}")
                    if rng.random() < 0.35:
                        pages.append(f"/product/{_weighted(rng, popularity)['slug']}")
                reached_cart = product is not None and rng.random() < 0.2
                reached_checkout = reached_cart and rng.random() < 0.5
                ordered = reached_checkout and rng.random() < 0.52
                if reached_cart:
                    pages.append("/cart")
                if reached_checkout:
                    pages.append("/checkout")

                for index, path in enumerate(pages):
                    if index:
                        moment = _utc(day, rng, after=moment, now=now)
                    kind = ("product" if path.startswith("/product/") else
                            "cart" if path == "/cart" else
                            "checkout" if path.startswith("/checkout") else
                            "shop" if path.startswith("/shop") else "page")
                    views.append((path, kind, visitor, referrer if index == 0 else "",
                                  device, _ts(moment)))

                if rng.random() < 0.006:
                    customers.append(_ts(moment))
                if rng.random() < 0.004:
                    counts["subscribers"] += _subscribe(rng, visitor_seq, moment)

                if reached_checkout and not ordered and rng.random() < 0.3:
                    _order(rng, popularity, product, moment, today, now, buyers,
                           status="expired")
                elif ordered:
                    counts["orders"] += 1
                    if _order(rng, popularity, product, moment, today, now, buyers):
                        counts["customers"] += 1

        db.executemany(
            "INSERT INTO page_views (path, kind, visitor, referrer, device, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", views)
        counts["page_views"] = len(views)
        db.rebuild_visits()
        for index, created in enumerate(customers):
            name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            db.insert("users", email=f"member{index + 1}@{DEMO_DOMAIN}",
                      password_hash=UNUSABLE_PASSWORD, name=name, role="customer",
                      created_at=created)
            counts["customers"] += 1
    return counts


def _subscribe(rng: random.Random, seq: int, moment: datetime) -> int:
    cur = db.execute(
        "INSERT OR IGNORE INTO newsletter (email, source, created_at) VALUES (?, ?, ?)",
        (f"list{seq}@{DEMO_DOMAIN}", DEMO_SOURCE, _ts(moment)))
    return cur.rowcount


def _order(rng: random.Random, popularity: list, first_product: Any,
           moment: datetime, today: date, now: datetime, buyers: list,
           status: str = "paid") -> int | None:
    """Insert one order.  Returns the id of an account created for the buyer."""
    new_account = None
    if buyers and status != "expired" and rng.random() < 0.24:
        email, name, user_id = rng.choice(buyers)                # a returning buyer
    else:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        email = f"{name.lower().replace(' ', '.')}.{len(buyers) + 1}@{DEMO_DOMAIN}"
        user_id = None
        if status != "expired":
            if rng.random() < 0.38:
                user_id = new_account = db.insert(
                    "users", email=email, password_hash=UNUSABLE_PASSWORD, name=name,
                    role="customer", created_at=_ts(moment - timedelta(minutes=3)))
            buyers.append((email, name, user_id))

    lines = [first_product]
    while rng.random() < 0.32 and len(lines) < 4:
        lines.append(_weighted(rng, popularity))
    items = []
    for product in lines:
        quantity = 2 if rng.random() < 0.12 else 1
        items.append((product, quantity))
    subtotal = sum(p["price_cents"] * q for p, q in items)
    discount = subtotal // 10 if rng.random() < 0.12 else 0
    shipping = 0 if subtotal - discount >= config.free_shipping_threshold_cents \
        else config.shipping_flat_cents
    tax = (subtotal - discount) * config.tax_rate_bps // 10000
    city, region, postal = rng.choice(CITIES)
    paid = min(moment + timedelta(seconds=rng.randint(40, 240)), now)
    local_paid = paid.astimezone(config.zone).date()
    age = (today - local_paid).days

    fields: dict[str, Any] = {
        "number": f"DEMO-{rng.getrandbits(64):016x}", "user_id": user_id,
        "email": email,
        "subtotal_cents": subtotal, "discount_cents": discount,
        "shipping_cents": shipping, "tax_cents": tax,
        "total_cents": subtotal - discount + shipping + tax,
        "discount_code": "WELCOME10" if discount else "",
        "ship_name": name, "ship_line1": f"{rng.randint(100, 9999)} Main St",
        "ship_city": city, "ship_region": region, "ship_postal": postal,
        "notes": DEMO_NOTE, "created_at": _ts(moment),
    }
    if status == "expired":
        fields.update(status="cancelled", notes=f"[auto-expired] {DEMO_NOTE}",
                      cancelled_at=_ts(min(moment + timedelta(minutes=45), now)))
    else:
        fields.update(status="paid", paid_at=_ts(paid),
                      payment_ref=f"cs_demo_{rng.getrandbits(48):012x}")
        if age >= 2:
            shipped = min(paid + timedelta(days=rng.randint(1, 2), hours=rng.randint(1, 6)),
                          now - timedelta(minutes=rng.randint(5, 90)))
            fields.update(status="fulfilled", fulfilled_at=_ts(shipped),
                          tracking_carrier=rng.choice(["USPS", "USPS", "UPS"]),
                          tracking_number=f"9400{rng.getrandbits(60):018d}"[:22])
            if age >= 12 and rng.random() < 0.025:
                fields.update(status="refunded",
                              refunded_at=_ts(shipped + timedelta(days=rng.randint(4, 9))))
    order_id = db.insert("orders", **fields)
    db.execute("UPDATE orders SET number = ? WHERE id = ?",
               (f"MOG-{4000 + order_id}", order_id))
    for product, quantity in items:
        on_sale = product["compare_cents"] is not None and \
            product["compare_cents"] > product["price_cents"]
        label = " / ".join(x for x in (product["size"], product["color"]) if x)
        db.insert(
            "order_items", order_id=order_id, variant_id=product["variant_id"],
            sku=product["sku"], title=product["title"], variant_label=label,
            slug=product["slug"], art_seed=product["art_seed"],
            unit_cents=product["price_cents"], quantity=quantity,
            department=product["department"], on_sale=1 if on_sale else 0,
        )
    return new_account
