"""Runtime configuration.

Everything is environment driven with safe local defaults, so `python3 run.py`
works on a clean machine and the exact same code runs in production once the
environment is populated.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from functools import lru_cache
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=8)
def _load_zone(name: str) -> tzinfo | None:
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:                                          # noqa: BLE001
        return None              # unknown name, or no tz database installed


@dataclass
class Config:
    # -- core ------------------------------------------------------------
    env: str = field(default_factory=lambda: os.environ.get("MOG_ENV", "development"))
    host: str = field(default_factory=lambda: os.environ.get("MOG_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("MOG_PORT", 8000))
    base_url: str = field(default_factory=lambda: os.environ.get("MOG_BASE_URL", ""))
    database: Path = field(
        default_factory=lambda: Path(os.environ.get("MOG_DB", DATA_DIR / "mog.sqlite3"))
    )

    # -- security --------------------------------------------------------
    secret_key: str = field(
        default_factory=lambda: os.environ.get("MOG_SECRET_KEY", "")
    )
    session_days: int = field(default_factory=lambda: _int("MOG_SESSION_DAYS", 30))
    secure_cookies: bool = field(
        default_factory=lambda: _bool("MOG_SECURE_COOKIES", False)
    )
    force_https: bool = field(default_factory=lambda: _bool("MOG_FORCE_HTTPS", False))
    # Staff and admin sessions end this many hours after sign-in, however
    # active they are.  The console shows revenue and customer records, so a
    # forgotten tab on a shared machine should not stay signed in for a month.
    admin_session_hours: int = field(
        default_factory=lambda: _int("MOG_ADMIN_SESSION_HOURS", 12)
    )

    # -- storefront ------------------------------------------------------
    store_name: str = "MOG Lifestyle"
    store_email: str = field(
        default_factory=lambda: os.environ.get("MOG_STORE_EMAIL", "info@moglifestyle.fit")
    )
    store_phone: str = field(
        default_factory=lambda: os.environ.get("MOG_STORE_PHONE", "+1 (208) 206-6706")
    )
    currency: str = "usd"
    currency_symbol: str = "$"
    # US-only launch per the brief; flat rate with a free-shipping threshold.
    shipping_flat_cents: int = field(default_factory=lambda: _int("MOG_SHIPPING_CENTS", 800))
    free_shipping_threshold_cents: int = field(
        default_factory=lambda: _int("MOG_FREE_SHIPPING_CENTS", 15000)
    )
    tax_rate_bps: int = field(default_factory=lambda: _int("MOG_TAX_BPS", 0))
    # Reports group sales into the store's own days, not UTC ones.  The default
    # follows the store phone number's 208 area code (Idaho, Mountain Time).
    timezone: str = field(
        default_factory=lambda: os.environ.get("MOG_TIMEZONE", "America/Boise")
    )

    # -- payments --------------------------------------------------------
    stripe_secret_key: str = field(
        default_factory=lambda: os.environ.get("STRIPE_SECRET_KEY", "")
    )
    stripe_webhook_secret: str = field(
        default_factory=lambda: os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    )

    # -- email -----------------------------------------------------------
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", ""))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: os.environ.get("SMTP_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD", ""))
    smtp_starttls: bool = field(default_factory=lambda: _bool("SMTP_STARTTLS", True))
    mail_from: str = field(
        default_factory=lambda: os.environ.get("MAIL_FROM", "MOG Lifestyle <info@moglifestyle.fit>")
    )

    # -- social ----------------------------------------------------------
    # Only Instagram is confirmed by the client.  The others stay empty until
    # they are, so the storefront never links to an account that may not exist.
    instagram: str = field(
        default_factory=lambda: os.environ.get("MOG_INSTAGRAM", "mog.lifestyle")
    )
    tiktok: str = field(default_factory=lambda: os.environ.get("MOG_TIKTOK", ""))
    youtube: str = field(default_factory=lambda: os.environ.get("MOG_YOUTUBE", ""))

    def __post_init__(self) -> None:
        if not self.secret_key:
            if self.is_production:
                raise RuntimeError(
                    "MOG_SECRET_KEY must be set in production. "
                    "Generate one with: python3 -c 'import secrets;print(secrets.token_hex(32))'"
                )
            # Development: persist a key so sessions survive a restart.
            keyfile = DATA_DIR / ".secret_key"
            keyfile.parent.mkdir(parents=True, exist_ok=True)
            if not keyfile.exists():
                keyfile.write_text(secrets.token_hex(32))
                keyfile.chmod(0o600)
            self.secret_key = keyfile.read_text().strip()
        if not self.base_url:
            self.base_url = f"http://{self.host}:{self.port}"
        self.base_url = self.base_url.rstrip("/")
        self.database = Path(self.database)
        self.database.parent.mkdir(parents=True, exist_ok=True)

    # -- derived ---------------------------------------------------------

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def payments_live(self) -> bool:
        """True when real Stripe credentials are configured."""
        return bool(self.stripe_secret_key)

    @property
    def email_live(self) -> bool:
        return bool(self.smtp_host)

    @property
    def zone(self) -> tzinfo:
        """The store's timezone, falling back to UTC if it cannot be loaded."""
        return _load_zone(self.timezone) or timezone.utc

    @property
    def zone_is_valid(self) -> bool:
        return _load_zone(self.timezone) is not None

    def local_now(self) -> datetime:
        return datetime.now(timezone.utc).astimezone(self.zone)

    def url(self, path: str = "/") -> str:
        return f"{self.base_url}{path if path.startswith('/') else '/' + path}"

    @property
    def social_links(self) -> list[tuple[str, str, str]]:
        """(icon, url, label) for every social profile that is configured.

        Handles are stored bare; the platform prefix lives here so the footer
        and the structured data can never disagree about a URL.
        """
        profiles = [
            ("instagram", "Instagram",
             f"https://www.instagram.com/{self.instagram.lstrip('@')}/"),
            ("tiktok", "TikTok", f"https://www.tiktok.com/@{self.tiktok.lstrip('@')}"),
            ("youtube", "YouTube", f"https://www.youtube.com/@{self.youtube.lstrip('@')}"),
        ]
        handles = {"instagram": self.instagram, "tiktok": self.tiktok,
                   "youtube": self.youtube}
        return [
            (icon, url, label)
            for icon, label, url in profiles
            if handles[icon].strip()
        ]


config = Config()
