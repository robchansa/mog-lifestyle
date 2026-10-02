"""First-party, cookieless traffic measurement.

The brief names the success metric directly: *"number of visits and sale
generated from those visits."*  This measures exactly that, without a cookie,
without JavaScript and without a third party -- which is also what keeps the
store out of consent-banner territory.

A visitor is identified by `sha256(daily_salt + ip + user_agent)`.  The salt is
random per process-day and never written down, so the hash cannot be reversed,
cannot be joined to anything else, and stops being meaningful at midnight.

Each view is written twice: the raw row in `page_views` (for top pages) and a
running per-visitor-day summary in `visits` (for everything else).  Reporting
lives in `reports`.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date

from . import db
from .config import config

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
    """A fresh random salt each day, held only in memory.

    "Day" is the store's day (`MOG_TIMEZONE`), so a visitor counted once in a
    daily report is counted once -- not split across a UTC midnight that falls
    in the middle of the store's evening.
    """
    global _salt_day, _salt
    today = config.local_now().date()
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


def record(path: str, *, ip: str, user_agent: str, referrer: str = "",
           host: str = "") -> None:
    """Log one page view.  Never raises -- analytics must not break a page.

    `host` is the site's own Host header: a referrer on the same host is just
    someone clicking around the store, not a traffic source, so it is dropped.
    """
    if not should_record(path, user_agent):
        return
    source = ""
    if referrer:
        import urllib.parse
        source = urllib.parse.urlsplit(referrer).netloc.lower()[:120]
        if host and source == host.strip().lower():
            source = ""
    kind = classify(path)
    try:
        visitor = visitor_hash(ip, user_agent)
        device = device_class(user_agent)
        with db.tx():
            db.insert("page_views", path=path[:200], kind=kind, visitor=visitor,
                      referrer=source, device=device)
            db.execute(
                "INSERT INTO visits (visitor, views, device, referrer, entry_path, "
                "  saw_product, saw_cart, saw_checkout) "
                "VALUES (?, 1, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(visitor) DO UPDATE SET views = visits.views + 1, "
                "  referrer = CASE WHEN visits.referrer = '' THEN excluded.referrer "
                "                  ELSE visits.referrer END, "
                "  saw_product = MAX(visits.saw_product, excluded.saw_product), "
                "  saw_cart = MAX(visits.saw_cart, excluded.saw_cart), "
                "  saw_checkout = MAX(visits.saw_checkout, excluded.saw_checkout)",
                (visitor, device, source, path[:200], int(kind == "product"),
                 int(kind == "cart"), int(kind == "checkout")),
            )
    except Exception:                                          # noqa: BLE001
        pass


def prune(keep_days: int = 400) -> int:
    """Traffic data is not kept forever.  Returns page views deleted."""
    window = f"-{int(keep_days)} days"
    with db.tx():
        cur = db.execute(
            "DELETE FROM page_views WHERE created_at < datetime('now', ?)", (window,))
        db.execute("DELETE FROM visits WHERE started_at < datetime('now', ?)", (window,))
    return cur.rowcount
