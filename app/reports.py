"""Sales, customer and traffic reporting for the admin Analytics page.

Every figure on that page comes from here: revenue, orders, average order
value, best sellers, the Men / Women / Everyone / Sale split, visitors, page
views, conversion and customer growth -- over any date range, grouped by day,
week, month or year, and compared with the period before.

Two decisions shape the module.

**Store-local time.**  Timestamps are stored in UTC, but an owner's "Monday"
is the store's Monday.  Reports group by the calendar of `MOG_TIMEZONE`, and
stay correct across daylight-saving changes: the range is split into spans of
constant UTC offset, and SQL shifts each row by the offset of the span it
falls in.  Everything -- distinct-visitor counts included -- is aggregated by
SQLite rather than by pulling rows into Python.

**Sales as they happened.**  An order line records its department and whether
it sold at a markdown at checkout (see `order_items`), so moving a product or
ending a sale never rewrites last month's report.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Mapping

from . import db
from .config import config

PAID = ("paid", "fulfilled")
PAID_SQL = "('paid', 'fulfilled')"
# When money changed hands.  `paid_at` is set by the payment webhook; orders
# created before that column was populated fall back to their creation time.
PAID_AT = "COALESCE(o.paid_at, o.created_at)"

GRAINS = ("day", "week", "month", "year")
GRAIN_LABELS = {"day": "Daily", "week": "Weekly", "month": "Monthly", "year": "Yearly"}

PRESETS = {
    "7d": "7 days",
    "30d": "30 days",
    "90d": "90 days",
    "12m": "12 months",
    "ytd": "Year to date",
    "all": "All time",
}
DEFAULT_PRESET = "30d"

MAX_BUCKETS = 400              # more bars than this stops being a chart
MAX_SPAN_DAYS = 366 * 25
EARLIEST = date(2000, 1, 1)

DEPARTMENTS = (("men", "Men"), ("women", "Women"), ("general", "Everyone"))
CATEGORY_LABELS = {"men": "Men", "women": "Women", "general": "Everyone",
                   "sale": "Sale", "": "Unassigned"}
CATEGORY_FILTERS = ("men", "women", "general", "sale")

FUNNEL_STEPS = (("product", "Viewed a product"), ("cart", "Opened the bag"),
                ("checkout", "Started checkout"))

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ================================================================ calendar

def bucket_start(day: date, grain: str) -> date:
    """The first day of the bucket containing `day`.  Weeks start on Monday."""
    if grain == "week":
        return day - timedelta(days=day.weekday())
    if grain == "month":
        return day.replace(day=1)
    if grain == "year":
        return day.replace(month=1, day=1)
    return day


def next_bucket(start: date, grain: str) -> date:
    if grain == "week":
        return start + timedelta(days=7)
    if grain == "month":
        return (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    if grain == "year":
        return start.replace(year=start.year + 1, month=1, day=1)
    return start + timedelta(days=1)


def count_buckets(start: date, end: date, grain: str) -> int:
    if grain == "week":
        return (bucket_start(end, "week") - bucket_start(start, "week")).days // 7 + 1
    if grain == "month":
        return (end.year - start.year) * 12 + end.month - start.month + 1
    if grain == "year":
        return end.year - start.year + 1
    return (end - start).days + 1


def auto_grain(days: int) -> str:
    if days <= 92:
        return "day"
    if days <= 200:
        return "week"
    if days <= 1500:
        return "month"
    return "year"


def shift_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:                       # 29 February in a common year
        return day.replace(year=day.year + years, day=28)


def format_day(day: date) -> str:
    return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"


def format_range(start: date, end: date) -> str:
    """`7–13 Sep 2026`, `28 Sep – 4 Oct 2026`, `29 Dec 2025 – 4 Jan 2026`."""
    if start == end:
        return format_day(start)
    if start.year != end.year:
        return f"{format_day(start)} – {format_day(end)}"
    if start.month != end.month:
        return (f"{start.day} {_MONTHS[start.month - 1]} – "
                f"{end.day} {_MONTHS[end.month - 1]} {end.year}")
    return f"{start.day}–{end.day} {_MONTHS[end.month - 1]} {end.year}"


@dataclass(frozen=True)
class Bucket:
    """One bar on a chart: a day, week, month or year, clipped to the period."""

    key: str          # ISO date of the bucket's natural start; matches SQL
    start: date       # first day of the bucket inside the period
    end: date         # last day of the bucket inside the period (inclusive)
    grain: str
    show_year: bool = False

    @property
    def label(self) -> str:
        """Short axis label."""
        natural = date.fromisoformat(self.key)
        if self.grain == "year":
            return str(natural.year)
        if self.grain == "month":
            month = _MONTHS[natural.month - 1]
            return f"{month} {natural.year}" if self.show_year else month
        shown = self.start
        text = f"{shown.day} {_MONTHS[shown.month - 1]}"
        return f"{text} {shown.year}" if self.show_year else text

    @property
    def long_label(self) -> str:
        """Unambiguous label for tooltips, tables and exports."""
        natural = date.fromisoformat(self.key)
        if self.grain == "day":
            return f"{_WEEKDAYS[self.start.weekday()]} {format_day(self.start)}"
        if self.grain == "month" and self.start == natural and \
                self.end == next_bucket(natural, "month") - timedelta(days=1):
            return f"{_MONTH_NAMES[natural.month - 1]} {natural.year}"
        if self.grain == "year" and self.start == natural and \
                self.end == date(natural.year, 12, 31):
            return str(natural.year)
        return format_range(self.start, self.end)


# ================================================================== period

@dataclass(frozen=True)
class Period:
    """An inclusive range of store-local days, and how to group them."""

    start: date
    end: date
    grain: str
    preset: str = ""                       # "" for a custom range
    notices: tuple[str, ...] = ()

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        return format_range(self.start, self.end)

    @property
    def bucket_count(self) -> int:
        return count_buckets(self.start, self.end, self.grain)

    def allows(self, grain: str) -> bool:
        return count_buckets(self.start, self.end, grain) <= MAX_BUCKETS

    def previous(self) -> Period | None:
        """The period to compare against, or None for "all time".

        Year-to-date and twelve-month views compare with the same dates a year
        earlier -- the like-for-like answer, and the only fair one when the
        current month is still in progress.  Everything else compares with the
        equally long stretch immediately before.
        """
        if self.preset == "all":
            return None
        if self.preset in ("ytd", "12m"):
            return Period(shift_years(self.start, -1), shift_years(self.end, -1),
                          self.grain)
        span = timedelta(days=self.days)
        return Period(self.start - span, self.start - timedelta(days=1), self.grain)

    def utc_range(self, zone: tzinfo | None = None) -> tuple[datetime, datetime]:
        """[start, end) as UTC instants: local midnight to local midnight."""
        zone = zone or config.zone
        lo = datetime(self.start.year, self.start.month, self.start.day, tzinfo=zone)
        after = self.end + timedelta(days=1)
        hi = datetime(after.year, after.month, after.day, tzinfo=zone)
        return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)

    def buckets(self) -> list[Bucket]:
        result: list[Bucket] = []
        cursor = bucket_start(self.start, self.grain)
        previous_year = None
        while cursor <= self.end:
            following = next_bucket(cursor, self.grain)
            start = max(cursor, self.start)
            end = min(following - timedelta(days=1), self.end)
            show_year = (
                self.grain in ("day", "week", "month")
                and (previous_year is None and self._spans_years()
                     or previous_year is not None and start.year != previous_year)
            )
            result.append(Bucket(cursor.isoformat(), start, end, self.grain, show_year))
            previous_year = start.year
            cursor = following
        return result

    def _spans_years(self) -> bool:
        return self.start.year != self.end.year


def first_activity_day() -> date | None:
    """The store-local day of the earliest order, account or page view."""
    earliest = db.scalar(
        "SELECT MIN(t) FROM ("
        " SELECT MIN(created_at) AS t FROM orders"
        " UNION ALL SELECT MIN(created_at) FROM page_views"
        " UNION ALL SELECT MIN(created_at) FROM users WHERE role = 'customer')"
    )
    if not earliest:
        return None
    moment = datetime.strptime(earliest[:19], "%Y-%m-%d %H:%M:%S")
    return moment.replace(tzinfo=timezone.utc).astimezone(config.zone).date()


def resolve(params: Mapping[str, str], *, today: date | None = None,
            first_day: date | None = None) -> Period:
    """Turn query parameters into a valid Period.

    Never raises: anything unreadable falls back to a sensible default and
    leaves a notice explaining what happened, so a mistyped URL produces a
    useful page instead of an error.
    """
    today = today or config.local_now().date()
    notices: list[str] = []
    preset = (params.get("range") or "").strip()
    raw_from = (params.get("from") or "").strip()
    raw_to = (params.get("to") or "").strip()

    start = end = None
    if raw_from or raw_to:
        start, end = _parse_day(raw_from), _parse_day(raw_to)
        if (raw_from and start is None) or (raw_to and end is None):
            notices.append("One of those dates couldn't be read, so this shows "
                           f"the last {PRESETS[DEFAULT_PRESET]}.")
            start = end = None
            preset = DEFAULT_PRESET
        else:
            end = end or today
            start = start or end - timedelta(days=29)
            preset = ""

    if start is None or end is None:
        if preset not in PRESETS:
            preset = DEFAULT_PRESET
        start, end = _preset_range(preset, today, first_day)

    if start > end:
        start, end = end, start
        notices.append("The start date was after the end date, so they've been swapped.")
    if end > today:
        end = today
        start = min(start, today)
        notices.append("Reports stop at today.")
    if start < EARLIEST:
        start = EARLIEST
    if (end - start).days + 1 > MAX_SPAN_DAYS:
        start = end - timedelta(days=MAX_SPAN_DAYS - 1)
        notices.append("Ranges are limited to 25 years.")

    grain = (params.get("grain") or "").strip()
    days = (end - start).days + 1
    if grain not in GRAINS:
        grain = auto_grain(days)
    elif count_buckets(start, end, grain) > MAX_BUCKETS:
        bars = count_buckets(start, end, grain)
        coarser = next(g for g in GRAINS[GRAINS.index(grain) + 1:]
                       if count_buckets(start, end, g) <= MAX_BUCKETS)
        notices.append(
            f"{GRAIN_LABELS[grain]} would draw {bars:,} bars, so this is grouped "
            f"{GRAIN_LABELS[coarser].lower()}."
        )
        grain = coarser
    return Period(start, end, grain, preset, tuple(notices))


def _parse_day(raw: str) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _preset_range(preset: str, today: date, first_day: date | None) -> tuple[date, date]:
    if preset == "7d":
        return today - timedelta(days=6), today
    if preset == "90d":
        return today - timedelta(days=89), today
    if preset == "12m":
        month = today.month - 11
        year = today.year
        if month < 1:
            month += 12
            year -= 1
        return date(year, month, 1), today
    if preset == "ytd":
        return date(today.year, 1, 1), today
    if preset == "all":
        first = first_day if first_day is not None else first_activity_day()
        if first is None or first > today:
            return today - timedelta(days=29), today
        return first, today
    return today - timedelta(days=29), today


# ======================================================= local-time in SQL

def _offset_minutes(moment: datetime, zone: tzinfo) -> int:
    offset = moment.astimezone(zone).utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


def offset_spans(lo: datetime, hi: datetime,
                 zone: tzinfo | None = None) -> list[tuple[datetime, int]]:
    """Split [lo, hi) into spans with one UTC offset each.

    Returns `[(span_start_utc, offset_minutes), ...]`.  Walks a day at a time
    and, where the offset changes, finds the exact second it does -- a
    handful of probes per daylight-saving change, so even a 25-year range is
    cheap.
    """
    zone = zone or config.zone
    spans = [(lo, _offset_minutes(lo, zone))]
    cursor = lo
    one_day, one_second = timedelta(days=1), timedelta(seconds=1)
    while cursor < hi:
        step = min(cursor + one_day, hi)
        probe = step if step < hi else hi - one_second
        current = spans[-1][1]
        if probe > cursor and _offset_minutes(probe, zone) != current:
            low, high = int(cursor.timestamp()), int(probe.timestamp())
            while high - low > 1:
                middle = (low + high) // 2
                moment = datetime.fromtimestamp(middle, timezone.utc)
                if _offset_minutes(moment, zone) == current:
                    low = middle
                else:
                    high = middle
            changed = datetime.fromtimestamp(high, timezone.utc)
            spans.append((changed, _offset_minutes(changed, zone)))
            cursor = changed
            continue
        cursor = step
    return spans


def sql_timestamp(moment: datetime) -> str:
    """The format SQLite's `datetime('now')` writes, for direct comparison."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def local_sql(column: str, spans: list[tuple[datetime, int]]) -> tuple[str, list[str]]:
    """SQL that converts a UTC timestamp column to store-local time."""
    def shifted(minutes: int) -> str:
        return f"datetime({column}, '{int(minutes):+d} minutes')"

    if len(spans) == 1:
        return shifted(spans[0][1]), []
    parts, params = ["CASE"], []
    for (_, minutes), (boundary, _) in zip(spans, spans[1:]):
        parts.append(f"WHEN {column} < ? THEN {shifted(minutes)}")
        params.append(sql_timestamp(boundary))
    parts.append(f"ELSE {shifted(spans[-1][1])} END")
    return " ".join(parts), params


def bucket_sql(local: str, grain: str) -> str:
    """SQL producing the same key as `bucket_start(...).isoformat()`."""
    if grain == "week":
        return f"date({local}, '-6 days', 'weekday 1')"
    if grain == "month":
        return f"strftime('%Y-%m-01', {local})"
    if grain == "year":
        return f"strftime('%Y-01-01', {local})"
    return f"date({local})"


class Window:
    """A period's UTC bounds plus the SQL needed to bucket rows inside it."""

    def __init__(self, period: Period):
        self.period = period
        lo, hi = period.utc_range()
        self.lo, self.hi = sql_timestamp(lo), sql_timestamp(hi)
        self.spans = offset_spans(lo, hi)

    def bucket(self, column: str) -> tuple[str, list[str]]:
        local, params = local_sql(column, self.spans)
        return bucket_sql(local, self.period.grain), params


def to_local(timestamp: str | None) -> datetime | None:
    """A stored UTC timestamp as an aware datetime in the store's zone."""
    if not timestamp:
        return None
    try:
        moment = datetime.strptime(timestamp[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return moment.replace(tzinfo=timezone.utc).astimezone(config.zone)


# ================================================================= metrics

@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    kind: str          # money | count | percent
    help: str


METRICS: tuple[Metric, ...] = (
    Metric("revenue", "Revenue", "money",
           "Collected on paid and shipped orders, including shipping and tax. "
           "Refunded orders are excluded."),
    Metric("orders", "Orders", "count", "Paid and shipped orders."),
    Metric("aov", "Average order", "money", "Revenue divided by orders."),
    Metric("units", "Units sold", "count", "Items on paid and shipped orders."),
    Metric("visitors", "Visitors", "count",
           "Distinct visitors per day, counted without cookies."),
    Metric("views", "Page views", "count", "Storefront pages viewed by people, not bots."),
    Metric("conversion", "Conversion", "percent", "Orders as a share of visitors."),
    Metric("customers", "New customers", "count", "Customer accounts created."),
)
METRIC_KEYS = tuple(m.key for m in METRICS)
METRICS_BY_KEY = {m.key: m for m in METRICS}


@dataclass
class Totals:
    revenue_cents: int = 0
    orders: int = 0
    units: int = 0
    gross_cents: int = 0
    discount_cents: int = 0
    shipping_cents: int = 0
    tax_cents: int = 0
    refunds: int = 0
    refund_cents: int = 0
    visitors: int = 0
    views: int = 0
    new_customers: int = 0

    @property
    def aov_cents(self) -> int:
        return self.revenue_cents // self.orders if self.orders else 0

    @property
    def net_sales_cents(self) -> int:
        return self.gross_cents - self.discount_cents

    @property
    def conversion_pct(self) -> float:
        return round(self.orders * 100 / self.visitors, 2) if self.visitors else 0.0

    @property
    def units_per_order(self) -> float:
        return round(self.units / self.orders, 1) if self.orders else 0.0

    def value(self, key: str) -> float:
        return {
            "revenue": self.revenue_cents, "orders": self.orders,
            "aov": self.aov_cents, "units": self.units,
            "visitors": self.visitors, "views": self.views,
            "conversion": self.conversion_pct, "customers": self.new_customers,
        }[key]


def change(current: float, previous: float | None, kind: str) -> dict[str, str]:
    """How a figure moved: direction plus a short human description."""
    if previous is None:
        return {"direction": "none", "text": ""}
    if kind == "percent":
        diff = round(current - previous, 2)
        if abs(diff) < 0.005:
            return {"direction": "flat", "text": "No change"}
        return {"direction": "up" if diff > 0 else "down",
                "text": f"{abs(diff):.2f} pts"}
    if previous == 0:
        if current == 0:
            return {"direction": "flat", "text": "No change"}
        return {"direction": "new", "text": "New"}
    pct = (current - previous) * 100 / previous
    if abs(pct) < 0.05:
        return {"direction": "flat", "text": "No change"}
    text = f"{abs(pct):,.0f}%" if abs(pct) >= 100 else f"{abs(pct):.1f}%"
    return {"direction": "up" if pct > 0 else "down", "text": text}


# ================================================================= queries

def _paid_window(w: Window) -> tuple[str, list[str]]:
    return (f"o.status IN {PAID_SQL} AND {PAID_AT} >= ? AND {PAID_AT} < ?",
            [w.lo, w.hi])


def sales_by_bucket(w: Window) -> dict[str, dict[str, int]]:
    bucket, bparams = w.bucket(PAID_AT)
    where, wparams = _paid_window(w)
    rows = db.query(
        f"SELECT {bucket} AS bucket, count(*) AS orders, "
        f"  COALESCE(SUM(o.total_cents), 0) AS revenue, "
        f"  COALESCE(SUM((SELECT SUM(oi.quantity) FROM order_items oi "
        f"                WHERE oi.order_id = o.id)), 0) AS units "
        f"FROM orders o WHERE {where} GROUP BY bucket",
        [*bparams, *wparams],
    )
    return {r["bucket"]: {"orders": r["orders"], "revenue": r["revenue"],
                          "units": r["units"]} for r in rows}


def traffic_by_bucket(w: Window) -> dict[str, dict[str, int]]:
    # A visit never crosses the store's midnight (the visitor salt rotates
    # then), so bucketing a visit by its start puts every view in its own day.
    bucket, bparams = w.bucket("started_at")
    rows = db.query(
        f"SELECT {bucket} AS bucket, COALESCE(SUM(views), 0) AS views, "
        f"  count(*) AS visitors "
        f"FROM visits WHERE started_at >= ? AND started_at < ? GROUP BY bucket",
        [*bparams, w.lo, w.hi],
    )
    return {r["bucket"]: {"views": r["views"], "visitors": r["visitors"]} for r in rows}


def customers_by_bucket(w: Window) -> dict[str, int]:
    bucket, bparams = w.bucket("created_at")
    rows = db.query(
        f"SELECT {bucket} AS bucket, count(*) AS n FROM users "
        f"WHERE role = 'customer' AND created_at >= ? AND created_at < ? "
        f"GROUP BY bucket",
        [*bparams, w.lo, w.hi],
    )
    return {r["bucket"]: r["n"] for r in rows}


def categories_by_bucket(w: Window) -> dict[str, dict[str, int]]:
    """Product sales (cents) per bucket for each department, plus `sale`."""
    bucket, bparams = w.bucket(PAID_AT)
    where, wparams = _paid_window(w)
    rows = db.query(
        f"SELECT {bucket} AS bucket, oi.department AS department, "
        f"  SUM(oi.quantity * oi.unit_cents) AS cents, "
        f"  SUM(CASE WHEN oi.on_sale THEN oi.quantity * oi.unit_cents ELSE 0 END) AS sale "
        f"FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        f"WHERE {where} GROUP BY bucket, oi.department",
        [*bparams, *wparams],
    )
    result: dict[str, dict[str, int]] = {}
    for r in rows:
        slot = result.setdefault(r["bucket"], {})
        slot[r["department"]] = slot.get(r["department"], 0) + r["cents"]
        slot["sale"] = slot.get("sale", 0) + r["sale"]
    return result


def totals(w: Window) -> Totals:
    where, wparams = _paid_window(w)
    sales = db.one(
        f"SELECT count(*) AS orders, COALESCE(SUM(o.total_cents), 0) AS revenue, "
        f"  COALESCE(SUM(o.subtotal_cents), 0) AS gross, "
        f"  COALESCE(SUM(o.discount_cents), 0) AS discount, "
        f"  COALESCE(SUM(o.shipping_cents), 0) AS shipping, "
        f"  COALESCE(SUM(o.tax_cents), 0) AS tax, "
        f"  COALESCE(SUM((SELECT SUM(oi.quantity) FROM order_items oi "
        f"                WHERE oi.order_id = o.id)), 0) AS units "
        f"FROM orders o WHERE {where}", wparams,
    )
    refunded_at = "COALESCE(o.refunded_at, o.paid_at, o.created_at)"
    refunds = db.one(
        f"SELECT count(*) AS n, COALESCE(SUM(o.total_cents), 0) AS cents "
        f"FROM orders o WHERE o.status = 'refunded' "
        f"AND {refunded_at} >= ? AND {refunded_at} < ?", [w.lo, w.hi],
    )
    traffic = db.one(
        "SELECT COALESCE(SUM(views), 0) AS views, count(*) AS visitors "
        "FROM visits WHERE started_at >= ? AND started_at < ?", [w.lo, w.hi],
    )
    new_customers = db.scalar(
        "SELECT count(*) FROM users WHERE role = 'customer' "
        "AND created_at >= ? AND created_at < ?", [w.lo, w.hi], 0,
    )
    return Totals(
        revenue_cents=sales["revenue"], orders=sales["orders"], units=sales["units"],
        gross_cents=sales["gross"], discount_cents=sales["discount"],
        shipping_cents=sales["shipping"], tax_cents=sales["tax"],
        refunds=refunds["n"], refund_cents=refunds["cents"],
        visitors=traffic["visitors"], views=traffic["views"],
        new_customers=int(new_customers),
    )


@dataclass
class CategoryRow:
    key: str
    label: str
    cents: int = 0
    units: int = 0
    orders: int = 0
    share_pct: float = 0.0
    previous_cents: int | None = None

    @property
    def change(self) -> dict[str, str]:
        return change(self.cents, self.previous_cents, "money")


def categories(w: Window, previous: Window | None = None) -> list[CategoryRow]:
    """Men, Women and Everyone -- which partition product sales -- then Sale,
    which cuts across them.  "Unassigned" appears only if it has sales."""
    def collect(window: Window) -> dict[str, dict[str, int]]:
        where, wparams = _paid_window(window)
        found: dict[str, dict[str, int]] = {}
        for r in db.query(
            f"SELECT oi.department AS key, SUM(oi.quantity) AS units, "
            f"  SUM(oi.quantity * oi.unit_cents) AS cents, "
            f"  count(DISTINCT oi.order_id) AS orders "
            f"FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            f"WHERE {where} GROUP BY oi.department", wparams,
        ):
            found[r["key"]] = dict(r)
        sale = db.one(
            f"SELECT COALESCE(SUM(oi.quantity), 0) AS units, "
            f"  COALESCE(SUM(oi.quantity * oi.unit_cents), 0) AS cents, "
            f"  count(DISTINCT oi.order_id) AS orders "
            f"FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            f"WHERE {where} AND oi.on_sale = 1", wparams,
        )
        found["sale"] = dict(sale)
        return found

    current = collect(w)
    before = collect(previous) if previous is not None else None
    product_sales = sum(v["cents"] for k, v in current.items() if k != "sale")

    keys = [key for key, _ in DEPARTMENTS]
    if current.get("", {}).get("cents"):
        keys.append("")
    keys.append("sale")
    rows = []
    for key in keys:
        data = current.get(key, {})
        cents = int(data.get("cents") or 0)
        rows.append(CategoryRow(
            key=key or "unassigned", label=CATEGORY_LABELS[key], cents=cents,
            units=int(data.get("units") or 0), orders=int(data.get("orders") or 0),
            share_pct=round(cents * 100 / product_sales, 1) if product_sales else 0.0,
            previous_cents=(int((before.get(key) or {}).get("cents") or 0)
                            if before is not None else None),
        ))
    return rows


def best_sellers(w: Window, *, category: str = "", limit: int = 10) -> list[dict[str, Any]]:
    where, params = _paid_window(w)
    if category == "sale":
        where += " AND oi.on_sale = 1"
    elif category in ("men", "women", "general"):
        where += " AND oi.department = ?"
        params = [*params, category]
    rows = db.query(
        f"SELECT oi.slug AS slug, "
        f"  COALESCE(MAX(p.title), MAX(oi.title)) AS title, "
        f"  MAX(p.id) AS product_id, MAX(p.images) AS images, "
        f"  MAX(p.art_seed) AS art_seed, MAX(oi.art_seed) AS line_seed, "
        f"  MAX(oi.department) AS department, MAX(oi.on_sale) AS on_sale, "
        f"  SUM(oi.quantity) AS units, SUM(oi.quantity * oi.unit_cents) AS cents, "
        f"  count(DISTINCT oi.order_id) AS orders "
        f"FROM order_items oi JOIN orders o ON o.id = oi.order_id "
        f"LEFT JOIN products p ON p.slug = oi.slug "
        f"WHERE {where} GROUP BY oi.slug "
        f"ORDER BY units DESC, cents DESC, title LIMIT ?",
        [*params, int(limit)],
    )
    total = sum(r["cents"] for r in rows) or 0
    everything = db.scalar(
        f"SELECT COALESCE(SUM(oi.quantity * oi.unit_cents), 0) "
        f"FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE {where}",
        params, 0,
    ) or total
    return [
        {**dict(r), "share_pct": round(r["cents"] * 100 / everything, 1) if everything else 0.0}
        for r in rows
    ]


def funnel(w: Window, orders: int) -> list[tuple[str, int]]:
    row = db.one(
        "SELECT count(*) AS visitors, COALESCE(SUM(saw_product), 0) AS product, "
        "  COALESCE(SUM(saw_cart), 0) AS cart, "
        "  COALESCE(SUM(saw_checkout), 0) AS checkout "
        "FROM visits WHERE started_at >= ? AND started_at < ?", [w.lo, w.hi],
    )
    steps = [("Visited", int(row["visitors"]))]
    steps += [(label, int(row[kind])) for kind, label in FUNNEL_STEPS]
    steps.append(("Ordered", orders))
    return steps


def buyers(w: Window) -> dict[str, int]:
    """Distinct purchasers in the period.

    `returning` had bought before the period began; `first_time` had not;
    `repeat` bought more than once inside it.  A refunded order still made
    someone a customer, so it counts towards "bought before".
    """
    where, wparams = _paid_window(w)
    row = db.one(
        f"WITH firsts AS ("
        f"  SELECT lower(email) AS who, MIN(COALESCE(paid_at, created_at)) AS first_paid "
        f"  FROM orders WHERE status IN ('paid', 'fulfilled', 'refunded') "
        f"  GROUP BY lower(email)), "
        f"period AS ("
        f"  SELECT lower(o.email) AS who, count(*) AS n FROM orders o "
        f"  WHERE {where} GROUP BY lower(o.email)) "
        f"SELECT count(*) AS buyers, "
        f"  COALESCE(SUM(f.first_paid < ?), 0) AS earlier, "
        f"  COALESCE(SUM(p.n > 1), 0) AS repeat_buyers "
        f"FROM period p JOIN firsts f ON f.who = p.who",
        [*wparams, w.lo],
    )
    count, earlier = int(row["buyers"]), int(row["earlier"])
    return {"buyers": count, "returning": earlier, "first_time": count - earlier,
            "repeat": int(row["repeat_buyers"])}


def customer_base(w: Window) -> dict[str, int]:
    at_start = db.scalar(
        "SELECT count(*) FROM users WHERE role = 'customer' AND created_at < ?",
        [w.lo], 0)
    at_end = db.scalar(
        "SELECT count(*) FROM users WHERE role = 'customer' AND created_at < ?",
        [w.hi], 0)
    return {"at_start": int(at_start), "at_end": int(at_end)}


def top_paths(w: Window, limit: int = 10) -> list[Any]:
    return db.query(
        "SELECT path, count(*) AS views, count(DISTINCT visitor) AS visitors "
        "FROM page_views WHERE created_at >= ? AND created_at < ? "
        "GROUP BY path ORDER BY views DESC, path LIMIT ?", [w.lo, w.hi, limit])


def top_referrers(w: Window, limit: int = 10) -> list[Any]:
    """External sites that brought visitors in, by visitors."""
    return db.query(
        "SELECT referrer, count(*) AS visitors, COALESCE(SUM(views), 0) AS views "
        "FROM visits WHERE referrer <> '' AND started_at >= ? AND started_at < ? "
        "GROUP BY referrer ORDER BY visitors DESC, referrer LIMIT ?",
        [w.lo, w.hi, limit])


def devices(w: Window) -> list[Any]:
    return db.query(
        "SELECT device, count(*) AS visitors FROM visits "
        "WHERE started_at >= ? AND started_at < ? GROUP BY device "
        "ORDER BY visitors DESC, device", [w.lo, w.hi])


def recent_orders(w: Window, limit: int = 8) -> list[Any]:
    return db.query(
        "SELECT o.*, (SELECT COALESCE(SUM(quantity), 0) FROM order_items oi "
        "             WHERE oi.order_id = o.id) AS item_count "
        "FROM orders o WHERE o.created_at >= ? AND o.created_at < ? "
        "ORDER BY o.created_at DESC, o.id DESC LIMIT ?", [w.lo, w.hi, limit])


def activity(w: Window, limit: int = 14) -> list[Any]:
    """The sales story of the period, newest first."""
    return db.query(
        "SELECT * FROM ("
        " SELECT paid_at AS at, 'paid' AS kind, number AS subject, "
        "   total_cents AS cents, ship_name AS detail, id AS ref "
        "   FROM orders WHERE paid_at >= ? AND paid_at < ?"
        " UNION ALL SELECT fulfilled_at, 'shipped', number, total_cents, "
        "   tracking_carrier, id FROM orders "
        "   WHERE fulfilled_at >= ? AND fulfilled_at < ?"
        " UNION ALL SELECT refunded_at, 'refunded', number, total_cents, "
        "   ship_name, id FROM orders WHERE refunded_at >= ? AND refunded_at < ?"
        " UNION ALL SELECT cancelled_at, "
        "   CASE WHEN notes LIKE '%[auto-expired]%' THEN 'expired' ELSE 'cancelled' END, "
        "   number, total_cents, ship_name, id FROM orders "
        "   WHERE cancelled_at >= ? AND cancelled_at < ?"
        " UNION ALL SELECT created_at, 'customer', email, 0, name, id FROM users "
        "   WHERE role = 'customer' AND created_at >= ? AND created_at < ?"
        " UNION ALL SELECT created_at, 'subscriber', email, 0, source, id "
        "   FROM newsletter WHERE created_at >= ? AND created_at < ?"
        ") ORDER BY at DESC, ref DESC LIMIT ?",
        [w.lo, w.hi] * 6 + [limit],
    )


# ================================================================== report

@dataclass
class Report:
    period: Period
    previous: Period | None
    buckets: list[Bucket]
    series: dict[str, list[float]]
    previous_series: dict[str, list[float]]
    previous_buckets: list[Bucket]
    totals: Totals
    previous_totals: Totals | None
    category_rows: list[CategoryRow]
    category_series: dict[str, list[int]]
    best_sellers: list[dict[str, Any]]
    category_filter: str
    funnel: list[tuple[str, int]]
    buyers: dict[str, int]
    customer_base: dict[str, int]
    top_paths: list[Any]
    top_referrers: list[Any]
    devices: list[Any]
    recent_orders: list[Any]
    activity: list[Any]
    extra: dict[str, Any] = field(default_factory=dict)

    def value(self, key: str) -> float:
        return self.totals.value(key)

    def previous_value(self, key: str) -> float | None:
        return self.previous_totals.value(key) if self.previous_totals else None

    def change(self, key: str) -> dict[str, str]:
        return change(self.value(key), self.previous_value(key),
                      METRICS_BY_KEY[key].kind)


def metric_series(period: Period, window: Window) -> tuple[list[Bucket], dict[str, list[float]]]:
    buckets = period.buckets()
    sales = sales_by_bucket(window)
    traffic = traffic_by_bucket(window)
    signups = customers_by_bucket(window)
    series: dict[str, list[float]] = {key: [] for key in METRIC_KEYS}
    for bucket in buckets:
        s = sales.get(bucket.key, {})
        t = traffic.get(bucket.key, {})
        orders, revenue = s.get("orders", 0), s.get("revenue", 0)
        visitors = t.get("visitors", 0)
        series["revenue"].append(revenue)
        series["orders"].append(orders)
        series["aov"].append(revenue // orders if orders else 0)
        series["units"].append(s.get("units", 0))
        series["visitors"].append(visitors)
        series["views"].append(t.get("views", 0))
        series["conversion"].append(round(orders * 100 / visitors, 2) if visitors else 0.0)
        series["customers"].append(signups.get(bucket.key, 0))
    return buckets, series


def build(period: Period, *, category: str = "") -> Report:
    window = Window(period)
    buckets, series = metric_series(period, window)

    previous = period.previous()
    previous_window = Window(previous) if previous is not None else None
    previous_buckets: list[Bucket] = []
    previous_series: dict[str, list[float]] = {}
    previous_totals = None
    if previous is not None and previous_window is not None:
        previous_buckets, previous_series = metric_series(previous, previous_window)
        previous_totals = totals(previous_window)

    mix = categories_by_bucket(window)
    category_series = {
        key: [mix.get(b.key, {}).get(key, 0) for b in buckets]
        for key in ("men", "women", "general", "", "sale")
    }

    current_totals = totals(window)
    category = category if category in CATEGORY_FILTERS else ""
    return Report(
        period=period, previous=previous, buckets=buckets, series=series,
        previous_series=previous_series, previous_buckets=previous_buckets,
        totals=current_totals, previous_totals=previous_totals,
        category_rows=categories(window, previous_window),
        category_series=category_series,
        best_sellers=best_sellers(window, category=category),
        category_filter=category,
        funnel=funnel(window, current_totals.orders),
        buyers=buyers(window), customer_base=customer_base(window),
        top_paths=top_paths(window), top_referrers=top_referrers(window),
        devices=devices(window), recent_orders=recent_orders(window),
        activity=activity(window),
    )


def export_rows(report: Report) -> list[list[Any]]:
    """One row per bucket: everything on the page that has a time axis."""
    header = ["period_start", "period_end", "label", "revenue", "orders",
              "average_order", "units", "visitors", "page_views",
              "conversion_pct", "new_customers", "men_sales", "women_sales",
              "everyone_sales", "unassigned_sales", "sale_sales"]
    rows: list[list[Any]] = [header]
    s, c = report.series, report.category_series
    for i, bucket in enumerate(report.buckets):
        rows.append([
            bucket.start.isoformat(), bucket.end.isoformat(), bucket.long_label,
            f"{s['revenue'][i] / 100:.2f}", int(s["orders"][i]),
            f"{s['aov'][i] / 100:.2f}", int(s["units"][i]),
            int(s["visitors"][i]), int(s["views"][i]),
            f"{s['conversion'][i]:.2f}", int(s["customers"][i]),
            f"{c['men'][i] / 100:.2f}", f"{c['women'][i] / 100:.2f}",
            f"{c['general'][i] / 100:.2f}", f"{c[''][i] / 100:.2f}",
            f"{c['sale'][i] / 100:.2f}",
        ])
    return rows
