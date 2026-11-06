"""Preorder launch: the countdown clock, where it leads, and the form rules.

One setting drives the homepage countdown: `config.preorder_opens_at`, a
single "YYYY-MM-DD HH:MM Area/City" value holding the date, the 24-hour time
and the IANA time zone together (see the top of `app/config.py`).

Nothing in this module writes anything.  The views decide, from
`forms_live()`, whether a valid submission is stored or only acknowledged.
"""
from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from .config import _load_zone, config
from .security import valid_email

SETTING_FORMAT = "YYYY-MM-DD HH:MM Area/City"
SETTING_EXAMPLE = "2026-11-20 10:00 America/Boise"

_SETTING_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})\s+([A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)$"
)

# Reviewers can see the countdown end without waiting for the real date:
# /?preview_countdown=15 ends it 15 seconds after the page loads, then lets
# that one browser through to the revealed site via /?preview_countdown=open.
# Ignored in production, where the configured date is the only clock.
SIMULATE_PARAM = "preview_countdown"
_MAX_SIMULATED_SECONDS = 400 * 86400


# ----------------------------------------------------------------- setting

# Safeguard: if a server has no time-zone database, a zone name like
# "America/Boise" cannot be looked up, the launch time cannot be read, and the
# site would stay hidden.  These zones are therefore also known here, with the
# US daylight-saving rule (second Sunday in March to first Sunday in November,
# changing at 2am local time).  (standard UTC offset, observes DST, labels)
_FALLBACK_ZONES: dict[str, tuple[int, bool, str, str]] = {
    "America/Boise": (-7, True, "MST", "MDT"),
    "America/Denver": (-7, True, "MST", "MDT"),
    "America/Phoenix": (-7, False, "MST", "MST"),
    "America/Chicago": (-6, True, "CST", "CDT"),
    "America/New_York": (-5, True, "EST", "EDT"),
    "America/Los_Angeles": (-8, True, "PST", "PDT"),
    "UTC": (0, False, "UTC", "UTC"),
    "Etc/UTC": (0, False, "UTC", "UTC"),
}


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7, weeks=n - 1)


def _fallback_zone(name: str, local: datetime) -> tzinfo | None:
    """A fixed-offset zone for `local` (naive wall-clock time) in `name`."""
    rule = _FALLBACK_ZONES.get(name)
    if rule is None:
        return None
    standard, observes_dst, std_label, dst_label = rule
    summer = observes_dst and (
        datetime.combine(_nth_sunday(local.year, 3, 2), time(2))
        <= local < datetime.combine(_nth_sunday(local.year, 11, 1), time(2))
    )
    return timezone(timedelta(hours=standard + (1 if summer else 0)),
                    dst_label if summer else std_label)


def zone_source(name: str) -> str:
    """How the server resolves a zone name -- shown on /healthz."""
    if _load_zone(name) is not None:
        return "time-zone database"
    if name in _FALLBACK_ZONES:
        return "built-in US rules (no time-zone database on this server)"
    return "unknown"


def parse_opening(value: str) -> datetime | None:
    """'2026-11-20 10:00 America/Boise' -> an aware datetime, else None."""
    match = _SETTING_RE.match((value or "").strip())
    if not match:
        return None
    year, month, day, hour, minute, zone_name = match.groups()
    try:
        local = datetime(int(year), int(month), int(day), int(hour), int(minute))
    except ValueError:                    # 2026-02-30, 25:00 and the like
        return None
    zone = _load_zone(zone_name) or _fallback_zone(zone_name, local)
    if zone is None:
        return None
    return local.replace(tzinfo=zone)


def opening() -> datetime | None:
    return parse_opening(config.preorder_opens_at)


def destination() -> str:
    """The preorder page to send shoppers to, or "" if none is configured.

    Only a path on this site or an absolute https:// URL is accepted, so a
    typo in the setting can never produce a broken or unsafe button.
    """
    url = (config.preorder_url or "").strip()
    if not url or any(ch.isspace() for ch in url) or "\\" in url:
        return ""
    if url.startswith("/") and not url.startswith("//"):
        return url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme == "https" and parsed.netloc and "." in parsed.netloc:
        return url
    return ""


def forms_live() -> bool:
    """True when the homepage forms store data; False for the local demo."""
    return config.preorder_forms == "live"


# ---------------------------------------------------------------- countdown

@dataclass(frozen=True)
class Countdown:
    opens_at: datetime | None       # aware, in the configured zone
    now: datetime                   # aware, UTC
    simulated: bool = False         # a reviewer's ?preview_countdown override

    @property
    def scheduled(self) -> bool:
        return self.opens_at is not None

    @property
    def remaining_seconds(self) -> int:
        """Whole seconds left, rounded up, so it reads 00:01 until it opens."""
        if self.opens_at is None:
            return 0
        delta = (self.opens_at - self.now).total_seconds()
        return max(0, math.ceil(delta))

    @property
    def is_open(self) -> bool:
        return self.opens_at is not None and self.now >= self.opens_at

    def parts(self) -> tuple[int, int, int, int]:
        """(days, hours, minutes, seconds) remaining."""
        left = self.remaining_seconds
        days, left = divmod(left, 86400)
        hours, left = divmod(left, 3600)
        minutes, seconds = divmod(left, 60)
        return days, hours, minutes, seconds

    def opens_at_utc_iso(self) -> str:
        if self.opens_at is None:
            return ""
        return self.opens_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def now_utc_iso(self) -> str:
        return self.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def zone_offset_minutes(self) -> int:
        """The store zone's UTC offset at the moment of opening."""
        if self.opens_at is None:
            return 0
        offset = self.opens_at.utcoffset() or timedelta(0)
        return int(offset.total_seconds() // 60)

    def opens_label(self) -> str:
        """'Tuesday, December 1, 2026 at 10:00 AM MST'."""
        if self.opens_at is None:
            return ""
        at = self.opens_at
        clock = at.strftime("%I:%M %p").lstrip("0")
        zone = at.tzname() or ""
        return (f"{at.strftime('%A')}, {at.strftime('%B')} {at.day}, {at.year} "
                f"at {clock} {zone}").strip()

    def short_label(self) -> str:
        """'December 1' -- for the hero."""
        if self.opens_at is None:
            return ""
        return f"{self.opens_at.strftime('%B')} {self.opens_at.day}"


def countdown(*, now: datetime | None = None, simulate: str = "") -> Countdown:
    """The countdown as of `now`, honouring a reviewer override outside production."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    opens_at = opening()
    simulate = (simulate or "").strip().lower()
    if simulate and not config.is_production:
        if simulate == "open":
            when = now - timedelta(seconds=1)
        elif simulate.isdigit() and 0 < int(simulate) <= _MAX_SIMULATED_SECONDS:
            when = now + timedelta(seconds=int(simulate))
        else:
            when = None
        if when is not None:
            zone = opens_at.tzinfo if opens_at is not None else config.zone
            return Countdown(when.astimezone(zone), now, simulated=True)
    return Countdown(opens_at, now)


def launch_status(*, now: datetime | None = None) -> dict:
    """What the server itself makes of the launch setting -- for /healthz.

    Lets anyone confirm, straight after a deploy, that the live server reads
    the opening time correctly and will open the site at the right moment.
    """
    raw = config.preorder_opens_at
    cd = countdown(now=now)
    if not cd.scheduled:
        return {"readable": False, "setting": raw, "site_open": False,
                "error": f"MOG_PREORDER_OPENS_AT must look like {SETTING_EXAMPLE}; "
                         "the site stays hidden until it is fixed"}
    zone_name = raw.split()[-1]
    return {
        "readable": True,
        "setting": raw,
        "opens_local": cd.opens_label(),
        "opens_at_utc": cd.opens_at_utc_iso(),
        "time_zone": f"{zone_name} via {zone_source(zone_name)}",
        "server_time_utc": cd.now_utc_iso(),
        "site_open": cd.is_open,
        "seconds_left": cd.remaining_seconds,
    }


# --------------------------------------------------------------- validation
# One set of rules, used by the server for every submission and mirrored by
# static/js/preorder.js so mistakes are caught before anything is sent.

EMAIL_REQUIRED = "Enter your email address."
EMAIL_INVALID = "Enter a valid email address, like name@example.com."


def email_error(email: str) -> str:
    email = (email or "").strip()
    if not email:
        return EMAIL_REQUIRED
    if not valid_email(email):
        return EMAIL_INVALID
    return ""


def notify_errors(email: str) -> dict[str, str]:
    error = email_error(email)
    return {"email": error} if error else {}
