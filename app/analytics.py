"""First-party, cookieless traffic measurement.

The brief names the success metric directly: *"number of visits and sale
generated from those visits."*  This measures exactly that, without a cookie,
without JavaScript and without a third party -- which is also what keeps the
store out of consent-banner territory.

A visitor is identified by `sha256(daily_salt + ip + user_agent)`.  The salt is
random per process-day and never written down, so the hash cannot be reversed,
cannot be joined to anything else, and stops being meaningful at midnight.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from datetime import date
from typing import Any

from . import db

BOT_PATTERN = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|headless|"
    r"lighthouse|monitor|curl|wget|python-urllib|axios|postman",
    re.I,
)
MOBILE_PATTERN = re.compile(r"android|iphone|ipod|windows phone|mobile", re.I)
TABLET_PATTERN = re.compile(r"ipad|tablet", re.I)
SKIP_PREFIXES = ("/static/", "/media/", "/admin", "/healthz", "/robots.txt",
                 "/sitemap.xml", "/webhooks/")

_salt_day: date | None = None
_salt = ""


def _daily_salt() -> str:
    """A fresh random salt each day, held only in memory."""
    global _salt_day, _salt
    today = date.today()
    if _salt_day != today or not _salt:
        _salt_day, _salt = today, secrets.token_hex(16)
    return _salt


def visitor_hash(ip: str, user_agent: str) -> str:
    digest = hashlib.sha256(
        f"{_daily_salt()}|{ip}|{user_agent}".encode()
    ).hexdigest()
    return digest[:20]


def classify(path: str) -> str:
    if path.startswith("/product/"):
        return "product"
    if path == "/cart":
        return "cart"
    if path.startswith("/checkout"):
        return "checkout"
    if path == "/shop" or path.startswith("/shop?"):
        return "shop"
    return "page"


def device_class(user_agent: str) -> str:
    if TABLET_PATTERN.search(user_agent):
        return "tablet"
    if MOBILE_PATTERN.search(user_agent):
        return "mobile"
    return "desktop"


def should_record(path: str, user_agent: str) -> bool:
    if any(path.startswith(prefix) for prefix in SKIP_PREFIXES):
        return False
    return not BOT_PATTERN.search(user_agent or "")


def record(path: str, *, ip: str, user_agent: str, referrer: str = "") -> None:
    """Log one page view.  Never raises -- analytics must not break a page."""
    if not should_record(path, user_agent):
        return
    host = ""
    if referrer:
        import urllib.parse
        parsed = urllib.parse.urlsplit(referrer)
        host = parsed.netloc[:120]
    try:
        with db.tx():
            db.insert(
                "page_views", path=path[:200], kind=classify(path),
                visitor=visitor_hash(ip, user_agent), referrer=host,
                device=device_class(user_agent),
            )
    except Exception:                                          # noqa: BLE001
        pass


# ------------------------------------------------------------------ reports

def summary(days: int = 30) -> dict[str, Any]:
    window = f"-{int(days)} days"
    visits = int(db.scalar(
        "SELECT count(*) FROM page_views WHERE created_at >= datetime('now', ?)",
        (window,), 0))
    visitors = int(db.scalar(
        "SELECT count(DISTINCT visitor) FROM page_views "
        "WHERE created_at >= datetime('now', ?)", (window,), 0))

    # The funnel the brief asks about, by distinct visitor at each step.
    def reach(kind: str) -> int:
        return int(db.scalar(
            "SELECT count(DISTINCT visitor) FROM page_views "
            "WHERE kind = ? AND created_at >= datetime('now', ?)",
            (kind, window), 0))

    orders = int(db.scalar(
        "SELECT count(*) FROM orders WHERE status IN ('paid','fulfilled') "
        "AND created_at >= datetime('now', ?)", (window,), 0))
    revenue = int(db.scalar(
        "SELECT COALESCE(SUM(total_cents), 0) FROM orders "
        "WHERE status IN ('paid','fulfilled') AND created_at >= datetime('now', ?)",
        (window,), 0))

    return {
        "visits": visits,
        "visitors": visitors,
        "pages_per_visitor": round(visits / visitors, 1) if visitors else 0.0,
        "funnel": [
            ("Visited", visitors),
            ("Viewed a product", reach("product")),
            ("Opened the bag", reach("cart")),
            ("Started checkout", reach("checkout")),
            ("Ordered", orders),
        ],
        "conversion_pct": round(orders * 100 / visitors, 2) if visitors else 0.0,
        "revenue_cents": revenue,
        "revenue_per_visitor_cents": revenue // visitors if visitors else 0,
    }


def by_day(days: int = 14) -> list[tuple[str, int, int]]:
    rows = db.query(
        "SELECT date(created_at) AS day, count(*) AS views, "
        "       count(DISTINCT visitor) AS visitors "
        "FROM page_views WHERE created_at >= datetime('now', ?) "
        "GROUP BY day ORDER BY day", (f"-{int(days)} days",))
    return [(r["day"], r["views"], r["visitors"]) for r in rows]


def top_paths(limit: int = 10, days: int = 30) -> list[sqlite3.Row]:
    return db.query(
        "SELECT path, count(*) AS views, count(DISTINCT visitor) AS visitors "
        "FROM page_views WHERE created_at >= datetime('now', ?) "
        "GROUP BY path ORDER BY views DESC LIMIT ?",
        (f"-{int(days)} days", limit))


def top_referrers(limit: int = 10, days: int = 30) -> list[sqlite3.Row]:
    return db.query(
        "SELECT referrer, count(*) AS views, count(DISTINCT visitor) AS visitors "
        "FROM page_views WHERE referrer <> '' "
        "AND created_at >= datetime('now', ?) "
        "GROUP BY referrer ORDER BY views DESC LIMIT ?",
        (f"-{int(days)} days", limit))


def devices(days: int = 30) -> list[sqlite3.Row]:
    return db.query(
        "SELECT device, count(DISTINCT visitor) AS visitors FROM page_views "
        "WHERE created_at >= datetime('now', ?) GROUP BY device "
        "ORDER BY visitors DESC", (f"-{int(days)} days",))


def prune(keep_days: int = 400) -> int:
    """Traffic data is not kept forever."""
    with db.tx():
        cur = db.execute(
            "DELETE FROM page_views WHERE created_at < datetime('now', ?)",
            (f"-{int(keep_days)} days",))
    return cur.rowcount
