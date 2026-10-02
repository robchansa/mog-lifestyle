"""The admin Analytics page: sales, customers and traffic over any range.

Administrators only (`require_admin`): staff run the operations console but
do not see revenue analysis.  All numbers come from `reports`; this module
only lays them out.  Every control is a plain link or GET form, so the page
works without JavaScript and every view has a shareable, bookmarkable URL.
"""
from __future__ import annotations

import csv
import io
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

from . import charts, reports
from .config import config
from .reports import METRICS, METRICS_BY_KEY, Report
from .security import audit
from .ui import E, admin_layout, badge, money, table
from .views_admin import csv_safe, require_admin
from .web import Request, Response, Router, html_response

router = Router()

ARROWS = {"up": "▲", "down": "▼", "flat": "–", "new": "▲", "none": ""}


# ------------------------------------------------------------------- state

class View:
    """What the admin asked to see, and how to link to variations of it."""

    def __init__(self, request: Request):
        self.params = {key: request.get(key) for key in ("range", "from", "to", "grain")}
        self.period = reports.resolve(self.params)
        self.explicit_grain = self.params["grain"] in reports.GRAINS
        metric = request.get("metric")
        self.metric = metric if metric in reports.METRIC_KEYS else "revenue"
        category = request.get("cat")
        self.category = category if category in reports.CATEGORY_FILTERS else ""

    def query(self, *, keep_grain: bool = True, **overrides: str) -> dict[str, str]:
        period = self.period
        state: dict[str, str] = {}
        if period.preset:
            state["range"] = period.preset
        else:
            state["from"] = period.start.isoformat()
            state["to"] = period.end.isoformat()
        if keep_grain and self.explicit_grain:
            state["grain"] = period.grain
        state["metric"] = self.metric
        state["cat"] = self.category
        state.update(overrides)
        if state.get("metric") == "revenue":
            state.pop("metric")
        return {k: v for k, v in state.items() if v}

    def url(self, path: str = "/admin/analytics", anchor: str = "", *,
            keep_grain: bool = True, **overrides: str) -> str:
        query = urllib.parse.urlencode(self.query(keep_grain=keep_grain, **overrides))
        return f"{path}{'?' + query if query else ''}{'#' + anchor if anchor else ''}"

    def preset_url(self, preset: str) -> str:
        # A new range picks its own grouping unless the admin chose one that fits.
        return self.url(keep_grain=False, range=preset, **{"from": "", "to": ""})


# --------------------------------------------------------------- formatting

def fmt_value(kind: str, value: float) -> str:
    if kind == "money":
        return money(int(round(value)))
    if kind == "percent":
        return f"{value:.2f}%"
    return f"{int(round(value)):,}"


def fmt_axis(kind: str):
    if kind == "money":
        return lambda v: charts.compact_money(v, config.currency_symbol)
    if kind == "percent":
        return lambda v: f"{v:g}%"
    return charts.compact_number


def when(timestamp: str | None, *, now: datetime | None = None) -> str:
    """A stored UTC timestamp as a short store-local phrase."""
    moment = reports.to_local(timestamp)
    if moment is None:
        return "—"
    now = now or config.local_now()
    clock = moment.strftime("%H:%M")
    if moment.date() == now.date():
        return f"Today {clock}"
    if moment.date() == now.date() - timedelta(days=1):
        return f"Yesterday {clock}"
    if moment.year == now.year:
        return f"{moment.day} {moment.strftime('%b')} {clock}"
    return f"{moment.day} {moment.strftime('%b')} {moment.year}"


def iso_local(timestamp: str | None) -> str:
    moment = reports.to_local(timestamp)
    return moment.isoformat(timespec="minutes") if moment else ""


def delta_html(change: dict[str, str], *, against: str = "") -> str:
    direction = change["direction"]
    if direction == "none":
        return '<span class="delta delta--none">No earlier period to compare</span>'
    arrow = ARROWS[direction]
    spoken = {"up": "Up", "down": "Down", "flat": "", "new": ""}[direction]
    versus = f' <span class="delta__vs">vs {E(against)}</span>' if against else ""
    return (
        f'<span class="delta delta--{E(direction)}">'
        f'<span aria-hidden="true">{arrow}</span> '
        f'<span class="visually-hidden">{spoken} </span>{E(change["text"])}'
        f'{versus}</span>'
    )


def _aligned(values: list[float], length: int) -> list[float | None]:
    padded: list[float | None] = list(values[:length])
    return padded + [None] * (length - len(padded))


# ----------------------------------------------------------------- sections

def controls(view: View) -> str:
    period = view.period
    presets = "".join(
        _chip(view.preset_url(key), label, period.preset == key)
        for key, label in reports.PRESETS.items()
    )
    grains = []
    for grain in reports.GRAINS:
        label = reports.GRAIN_LABELS[grain]
        if grain == period.grain:
            grains.append(f'<a class="segmented__opt is-active" aria-current="true" '
                          f'href="{E(view.url(grain=grain))}">{E(label)}</a>')
        elif period.allows(grain):
            grains.append(f'<a class="segmented__opt" '
                          f'href="{E(view.url(grain=grain))}">{E(label)}</a>')
        else:
            bars = reports.count_buckets(period.start, period.end, grain)
            grains.append(
                f'<span class="segmented__opt" aria-disabled="true" '
                f'title="{bars:,} bars is too many to chart — pick a shorter range">'
                f'{E(label)}</span>'
            )

    hidden = "".join(
        f'<input type="hidden" name="{E(k)}" value="{E(v)}">'
        for k, v in view.query().items() if k in ("metric", "cat")
        or (k == "grain" and view.explicit_grain)
    )
    today = config.local_now().date().isoformat()
    previous = period.previous()
    compared = (f" · compared with {E(previous.label)}" if previous is not None
                else " · no comparison for all time")
    zone = (f"{E(config.timezone)} ({E(config.local_now().tzname() or '')})"
            if config.zone_is_valid else
            f"UTC — “{E(config.timezone)}” isn't a timezone this server knows; set MOG_TIMEZONE")
    notices = "".join(f'<p class="range__notice" role="status">{E(n)}</p>'
                      for n in period.notices)
    return f"""
<section class="range no-print" aria-label="Report range">
  <div class="range__row">
    <nav class="range__presets" aria-label="Quick ranges">{presets}</nav>
    <div class="segmented" role="group" aria-label="Group by">{"".join(grains)}</div>
  </div>
  <form class="range__custom" method="get" action="/admin/analytics" data-range-form>
    <label>From <input type="date" name="from" value="{period.start.isoformat()}"
      min="{reports.EARLIEST.isoformat()}" max="{today}" required></label>
    <label>To <input type="date" name="to" value="{period.end.isoformat()}"
      min="{reports.EARLIEST.isoformat()}" max="{today}" required></label>
    {hidden}
    <button class="btn btn--small btn--ghost" type="submit">Apply dates</button>
  </form>
  {notices}
</section>
<p class="range__summary">
  <strong>{E(period.label)}</strong> · {period.days:,} day{'s' if period.days != 1 else ''}
  · grouped {E(reports.GRAIN_LABELS[period.grain].lower())}{compared}
  · times in {zone}
</p>
"""


def _chip(href: str, label: str, active: bool) -> str:
    current = ' aria-current="true"' if active else ""
    return (f'<a class="chip{" is-active" if active else ""}" href="{E(href)}"{current}>'
            f'{E(label)}</a>')


def kpis(report: Report, view: View) -> str:
    cards = []
    for metric in METRICS:
        value = report.value(metric.key)
        previous = report.previous_value(metric.key)
        against = fmt_value(metric.kind, previous) if previous is not None else ""
        active = metric.key == view.metric
        current = ' aria-current="true"' if active else ""
        cards.append(f"""
<li><a class="kpi{' is-active' if active else ''}" href="{E(view.url(anchor='trend', metric=metric.key))}"{current}
   title="{E(metric.help)}">
  <span class="kpi__label">{E(metric.label)}</span>
  <span class="kpi__value">{E(fmt_value(metric.kind, value))}</span>
  {delta_html(report.change(metric.key), against=against)}
  {charts.sparkline(report.series[metric.key])}
</a></li>""")
    return f'<ul class="kpis" aria-label="Summary for this range">{"".join(cards)}</ul>'


def trend(report: Report, view: View) -> str:
    metric = METRICS_BY_KEY[view.metric]
    count = len(report.buckets)
    current = report.series[metric.key]
    bars = [charts.Series("This period", current, "ink")]
    lines = []
    if report.previous is not None:
        lines.append(charts.Series(
            "Previous period", _aligned(report.previous_series[metric.key], count),
            dashed=True,
        ))
    total_text = fmt_value(metric.kind, report.value(metric.key))
    noun = "total" if metric.kind != "percent" and metric.key != "aov" else "overall"
    peak_index = max(range(count), key=lambda i: current[i]) if count else 0
    summary = (
        f"{metric.label} by {report.period.grain} for {report.period.label}: "
        f"{total_text} {noun}"
        + (f", highest on {report.buckets[peak_index].long_label} at "
           f"{fmt_value(metric.kind, current[peak_index])}." if count and current[peak_index] else ".")
    )
    chart = charts.Chart(
        title=f"{metric.label} by {report.period.grain}",
        labels=[b.label for b in report.buckets],
        long_labels=[b.long_label for b in report.buckets],
        bars=bars, lines=lines,
        fmt=lambda v: fmt_value(metric.kind, v), fmt_axis=fmt_axis(metric.kind),
        integer=metric.kind == "count", summary=summary, height="17rem",
    )
    tabs = "".join(
        _tab(view.url(anchor="trend", metric=m.key), m.label, m.key == metric.key)
        for m in METRICS
    )
    return f"""
<section class="panel" id="trend" aria-labelledby="trend-title">
  <div class="panel__head">
    <h2 id="trend-title">{E(metric.label)} <span class="muted">by {E(report.period.grain)}</span></h2>
    <span class="panel__figure">{E(total_text)} <small>{E(noun)}</small></span>
  </div>
  <nav class="tabs no-print" aria-label="Chart metric">{tabs}</nav>
  <div class="panel__body">
    <p class="panel__note">{E(metric.help)}</p>
    {charts.render(chart)}
  </div>
</section>
"""


def _tab(href: str, label: str, active: bool) -> str:
    current = ' aria-current="true"' if active else ""
    return (f'<a class="tab{" is-active" if active else ""}" href="{E(href)}"{current}>'
            f'{E(label)}</a>')


def category_panel(report: Report, view: View) -> str:
    rows = []
    for row in report.category_rows:
        key = row.key if row.key != "unassigned" else ""
        is_sale = row.key == "sale"
        active = bool(key) and view.category == key
        name = E(row.label)
        if key:
            href = view.url(anchor="best-sellers", cat="" if active else key)
            current = ' aria-current="true"' if active else ""
            name = (f'<a href="{E(href)}" class="category__link"{current}>'
                    f'{E(row.label)}</a>')
        rows.append(f"""
<li class="category{' category--sale' if is_sale else ''}{' is-active' if active else ''}">
  <div class="category__head">
    <span class="category__name">{name}</span>
    <span class="category__value">{E(money(row.cents))}</span>
  </div>
  {charts.share_bar(row.share_pct, tone='sale' if is_sale else 'ink')}
  <div class="category__meta">
    <span>{row.share_pct:g}% of product sales</span>
    <span>{row.units:,} unit{'s' if row.units != 1 else ''}</span>
    <span>{row.orders:,} order{'s' if row.orders != 1 else ''}</span>
    {delta_html(row.change) if report.previous is not None else ''}
  </div>
</li>""")

    series_defs = [("men", "Men", "ink"), ("women", "Women", "mid"),
                   ("general", "Everyone", "light")]
    if any(report.category_series[""]):
        series_defs.append(("", "Unassigned", "faint"))
    mix = charts.Chart(
        title=f"Product sales by department, by {report.period.grain}",
        labels=[b.label for b in report.buckets],
        long_labels=[b.long_label for b in report.buckets],
        bars=[charts.Series(label, report.category_series[key], tone)
              for key, label, tone in series_defs],
        lines=[charts.Series("Sale", report.category_series["sale"], dashed=True)],
        fmt=lambda v: money(int(v)),
        fmt_axis=fmt_axis("money"), show_total=True, height="13rem",
        summary="Stacked bars of product sales for Men, Women and Everyone in each "
                "period, with sale-priced sales drawn as a dashed line.",
    )
    return f"""
<section class="panel dash__main" id="categories" aria-labelledby="categories-title">
  <div class="panel__head">
    <h2 id="categories-title">Sales by category</h2>
    <span class="muted">Product sales, before discounts</span>
  </div>
  <div class="panel__body category-grid">
    <ul class="categories">{"".join(rows)}</ul>
    <div>
      <h3 class="panel__subhead">Department mix over time</h3>
      {charts.render(mix)}
    </div>
  </div>
  <p class="panel__foot">Men, Women and Everyone add up to all product sales.
    Sale cuts across them: a marked-down men's tee counts in Men <em>and</em> Sale.
    Select a category to filter best sellers.</p>
</section>
"""


def best_sellers_panel(report: Report, view: View) -> str:
    rows = []
    for rank, item in enumerate(report.best_sellers, start=1):
        title = E(item["title"])
        if item["product_id"]:
            title = f'<a href="/admin/products/{int(item["product_id"])}">{title}</a>'
        tags = [badge(reports.CATEGORY_LABELS.get(item["department"] or "", "Unassigned"),
                      "neutral")]
        if item["on_sale"]:
            tags.append(badge("Sale", "sale"))
        rows.append([
            f'<span class="rank">{rank}</span>',
            f'<span class="product-cell">{title}<span class="cluster">{"".join(tags)}</span></span>',
            f'{item["units"]:,}',
            f'{item["orders"]:,}',
            E(money(item["cents"])),
            f'<span class="share-cell">{charts.share_bar(item["share_pct"])}'
            f'<span class="num">{item["share_pct"]:g}%</span></span>',
        ])
    filter_note = ""
    if report.category_filter:
        label = reports.CATEGORY_LABELS[report.category_filter]
        filter_note = (
            f'<span class="cluster">{badge(label, "paid")}'
            f'<a class="btn btn--quiet btn--small" '
            f'href="{E(view.url(anchor="best-sellers", cat=""))}">Show all</a></span>'
        )
    empty = ("No sales in this category for this range." if report.category_filter
             else "No sales in this range yet.")
    return f"""
<section class="panel dash__main" id="best-sellers" aria-labelledby="best-title">
  <div class="panel__head">
    <h2 id="best-title">Best-selling products</h2>
    {filter_note or '<span class="muted">By units sold</span>'}
  </div>
  {table(["#", "Product", "Units", "Orders", "Sales", "Share"], rows, empty=empty,
         numeric=(2, 3, 4))}
</section>
"""


def sales_summary(report: Report) -> str:
    t, p = report.totals, report.previous_totals
    lines = [
        ("Gross sales", money(t.gross_cents), "Product prices × quantities", ""),
        ("Discounts", money(-t.discount_cents) if t.discount_cents else money(0), "", ""),
        ("Net sales", money(t.net_sales_cents), "", "summary-list__row--sub"),
        ("Shipping", money(t.shipping_cents), "", ""),
        ("Tax", money(t.tax_cents), "", ""),
        ("Revenue", money(t.revenue_cents), "", "summary-list__row--total"),
    ]
    body = "".join(
        f'<div class="summary-list__row {cls}"><dt>{E(label)}'
        f'{f"<small>{E(note)}</small>" if note else ""}</dt><dd>{E(value)}</dd></div>'
        for label, value, note, cls in lines
    )
    refunds = (
        f'<p class="panel__note">{t.refunds:,} order{"s" if t.refunds != 1 else ""} '
        f'refunded in this range ({E(money(t.refund_cents))}) — not counted above.</p>'
        if t.refunds else
        '<p class="panel__note">No refunds in this range.</p>'
    )
    stats = [
        ("Orders", f"{t.orders:,}", report.change("orders")),
        ("Average order", money(t.aov_cents), report.change("aov")),
        ("Units sold", f"{t.units:,}", report.change("units")),
        ("Units per order", f"{t.units_per_order:g}",
         reports.change(t.units_per_order, p.units_per_order if p else None, "count")),
    ]
    stat_html = "".join(
        f'<div class="mini-stat"><span class="mini-stat__label">{E(label)}</span>'
        f'<span class="mini-stat__value">{E(value)}</span>{delta_html(change)}</div>'
        for label, value, change in stats
    )
    return f"""
<section class="panel dash__side" aria-labelledby="summary-title">
  <div class="panel__head"><h2 id="summary-title">Sales summary</h2></div>
  <div class="panel__body">
    <dl class="summary-list">{body}</dl>
    {refunds}
    <div class="mini-stats">{stat_html}</div>
  </div>
</section>
"""


def customers_panel(report: Report, view: View) -> str:
    base, buyers = report.customer_base, report.buyers
    t = report.totals
    growth = (round(t.new_customers * 100 / base["at_start"], 1)
              if base["at_start"] else None)
    repeat = (round(buyers["repeat"] * 100 / buyers["buyers"], 1)
              if buyers["buyers"] else 0.0)
    stats = [
        ("Customers", f"{base['at_end']:,}", "accounts at the end of the range"),
        ("New accounts", f"{t.new_customers:,}",
         f"{growth:+g}% on the start of the range" if growth is not None
         else "no accounts before this range"),
        ("Buyers", f"{buyers['buyers']:,}",
         f"{buyers['first_time']:,} first-time · {buyers['returning']:,} returning"),
        ("Repeat rate", f"{repeat:g}%",
         f"{buyers['repeat']:,} bought more than once"),
    ]
    stat_html = "".join(
        f'<div class="mini-stat"><span class="mini-stat__label">{E(label)}</span>'
        f'<span class="mini-stat__value">{E(value)}</span>'
        f'<span class="mini-stat__note">{E(note)}</span></div>'
        for label, value, note in stats
    )
    count = len(report.buckets)
    lines = []
    if report.previous is not None:
        lines.append(charts.Series(
            "Previous period", _aligned(report.previous_series["customers"], count),
            dashed=True))
    chart = charts.Chart(
        title=f"New customer accounts by {report.period.grain}",
        labels=[b.label for b in report.buckets],
        long_labels=[b.long_label for b in report.buckets],
        bars=[charts.Series("New accounts", report.series["customers"], "ink")],
        lines=lines, fmt=lambda v: f"{int(v):,}", fmt_axis=charts.compact_number,
        integer=True, height="10rem",
        summary=f"{t.new_customers:,} new customer accounts in {report.period.label}.",
    )
    return f"""
<section class="panel dash__half" id="customers" aria-labelledby="customers-title">
  <div class="panel__head">
    <h2 id="customers-title">Customer growth</h2>
    <a class="btn btn--quiet btn--small" href="/admin/customers">All customers</a>
  </div>
  <div class="panel__body">
    <div class="mini-stats">{stat_html}</div>
    {charts.render(chart)}
  </div>
</section>
"""


def funnel_panel(report: Report) -> str:
    steps = report.funnel
    top = steps[0][1] or 1
    items = []
    for index, (label, count) in enumerate(steps):
        of_visitors = round(count * 100 / top, 1) if steps[0][1] else 0
        step_note = ""
        if index:
            before = steps[index - 1][1]
            rate = round(count * 100 / before, 1) if before else 0
            step_note = f'<small>{rate:g}% of previous step</small>'
        items.append(
            f'<li class="funnel__step">'
            f'<div class="funnel__bar" style="width:{max(1.5, min(100, of_visitors)):g}%"></div>'
            f'<span class="funnel__label">{E(label)}{step_note}</span>'
            f'<span class="funnel__count">{count:,}<small>{of_visitors:g}%</small></span>'
            f'</li>'
        )
    t = report.totals
    return f"""
<section class="panel dash__half" id="conversion" aria-labelledby="conversion-title">
  <div class="panel__head">
    <h2 id="conversion-title">Conversion</h2>
    <span class="panel__figure">{t.conversion_pct:.2f}% <small>of visitors ordered</small></span>
  </div>
  <div class="panel__body">
    <ol class="funnel">{"".join(items)}</ol>
    <p class="panel__note">Distinct visitors reaching each step. Orders are paid
      orders in the range, so the last step can include visitors who browsed on
      an earlier day.</p>
  </div>
</section>
"""


def traffic_panel(report: Report) -> str:
    t = report.totals
    chart = charts.Chart(
        title=f"Page views and visitors by {report.period.grain}",
        labels=[b.label for b in report.buckets],
        long_labels=[b.long_label for b in report.buckets],
        bars=[charts.Series("Page views", report.series["views"], "light")],
        lines=[charts.Series("Visitors", report.series["visitors"])],
        fmt=lambda v: f"{int(v):,}", fmt_axis=charts.compact_number,
        integer=True, height="13rem",
        summary=f"{t.views:,} page views from {t.visitors:,} visitors in "
                f"{report.period.label}.",
    )
    pages = [
        [f'<a href="{E(r["path"])}">{E(r["path"])}</a>',
         f'{r["views"]:,}', f'{r["visitors"]:,}']
        for r in report.top_paths
    ]
    referrers = [
        [E(r["referrer"]), f'{r["visitors"]:,}', f'{r["views"]:,}']
        for r in report.top_referrers
    ]
    device_total = sum(r["visitors"] for r in report.devices) or 1
    device_rows = "".join(
        f'<li class="device"><span>{E(r["device"].title())}</span>'
        f'{charts.share_bar(r["visitors"] * 100 / device_total)}'
        f'<span class="num">{r["visitors"]:,} · {round(r["visitors"] * 100 / device_total):g}%</span></li>'
        for r in report.devices
    ) or '<li class="muted">No traffic in this range.</li>'
    per_visitor = round(t.views / t.visitors, 1) if t.visitors else 0
    return f"""
<section class="panel dash__wide" id="traffic" aria-labelledby="traffic-title">
  <div class="panel__head">
    <h2 id="traffic-title">Visitors &amp; page views</h2>
    <span class="panel__figure">{t.visitors:,} <small>visitors</small> ·
      {t.views:,} <small>views</small> · {per_visitor:g} <small>pages each</small></span>
  </div>
  <div class="panel__body">
    {charts.render(chart)}
  </div>
  <div class="traffic-tables">
    <div>
      <h3 class="panel__subhead">Top pages</h3>
      {table(["Page", "Views", "Visitors"], pages, empty="No traffic in this range.",
             numeric=(1, 2))}
    </div>
    <div>
      <h3 class="panel__subhead">Where visitors came from</h3>
      {table(["Source", "Visitors", "Views"], referrers,
             empty="No referrers — all traffic in this range was direct.",
             numeric=(1, 2))}
    </div>
    <div>
      <h3 class="panel__subhead">Devices</h3>
      <ul class="devices">{device_rows}</ul>
    </div>
  </div>
</section>
"""


def recent_orders_panel(report: Report) -> str:
    from . import orders
    rows = [
        [f'<a href="/admin/orders/{o["id"]}">{E(o["number"])}</a>',
         f'<time datetime="{E(iso_local(o["created_at"]))}">{E(when(o["created_at"]))}</time>',
         f'<span class="cell-wrap">{E(o["ship_name"] or "—")}<br>'
         f'<span class="muted">{E(o["email"])}</span></span>',
         f'{o["item_count"]}',
         badge(orders.STATUS_LABELS.get(o["status"], o["status"]), o["status"]),
         E(money(o["total_cents"]))]
        for o in report.recent_orders
    ]
    return f"""
<section class="panel dash__wide" id="recent-orders" aria-labelledby="orders-title">
  <div class="panel__head">
    <h2 id="orders-title">Recent orders</h2>
    <a class="btn btn--quiet btn--small" href="/admin/orders">All orders</a>
  </div>
  {table(["Order", "Placed", "Customer", "Items", "Status", "Total"], rows,
         empty="No orders were placed in this range.", numeric=(3, 5))}
</section>
"""


_ACTIVITY = {
    "paid": "Order paid",
    "shipped": "Shipped",
    "refunded": "Refunded",
    "cancelled": "Cancelled",
    "expired": "Checkout abandoned",
    "customer": "New customer",
    "subscriber": "Newsletter sign-up",
}


def activity_panel(report: Report) -> str:
    items = []
    for event in report.activity:
        kind = event["kind"]
        subject = E(event["subject"])
        if kind in ("paid", "shipped", "refunded", "cancelled", "expired"):
            subject = f'<a href="/admin/orders/{int(event["ref"])}">{subject}</a>'
            detail_bits = [money(event["cents"])]
            if kind == "shipped" and event["detail"]:
                detail_bits.append(f"via {event['detail']}")
            elif kind == "expired":
                detail_bits.append("stock released")
            elif event["detail"]:
                detail_bits.append(event["detail"])
        elif kind == "customer":
            query = urllib.parse.urlencode({"q": event["subject"]})
            subject = f'<a href="/admin/customers?{E(query)}">{subject}</a>'
            detail_bits = [event["detail"]] if event["detail"] else []
        else:
            detail_bits = [f"from {event['detail']}"] if event["detail"] else []
        detail = " · ".join(E(bit) for bit in detail_bits if bit)
        detail_html = f' <span class="muted">· {detail}</span>' if detail else ""
        items.append(
            f'<li class="activity__item activity__item--{E(kind)}">'
            f'<span class="activity__dot" aria-hidden="true"></span>'
            f'<span class="activity__text"><strong>{E(_ACTIVITY.get(kind, kind))}</strong> '
            f'{subject}{detail_html}</span>'
            f'<time class="activity__time" datetime="{E(iso_local(event["at"]))}">'
            f'{E(when(event["at"]))}</time></li>'
        )
    body = (f'<ol class="activity">{"".join(items)}</ol>' if items else
            '<p class="muted table-empty">Nothing happened in this range.</p>')
    return f"""
<section class="panel dash__side" id="activity" aria-labelledby="activity-title">
  <div class="panel__head">
    <h2 id="activity-title">Sales activity</h2>
    <span class="muted">Newest first</span>
  </div>
  <div class="panel__body">{body}</div>
</section>
"""


def definitions() -> str:
    terms = [(m.label, m.help) for m in METRICS] + [
        ("Sales by category",
         "Product sales (price × quantity, before order discounts) by the department "
         "the item belonged to when it sold. Sale means it sold below its "
         "compare-at price. Both are recorded at checkout, so later catalogue "
         "changes never rewrite past reports."),
        ("Previous period",
         "The same number of days immediately before. Year to date and 12 months "
         "compare with the same dates a year earlier."),
        ("Time zone",
         f"Days, weeks (Monday to Sunday), months and years follow "
         f"{config.timezone}, including daylight-saving changes."),
        ("Privacy",
         "Visitors are counted without cookies: a daily-rotating hash of address and "
         "browser that is never stored in a reversible form, and resets each day. "
         "Known bots are excluded and traffic older than 400 days is deleted."),
    ]
    items = "".join(f"<div><dt>{E(term)}</dt><dd>{E(text)}</dd></div>" for term, text in terms)
    return f"""
<details class="panel definitions dash__wide">
  <summary class="panel__head"><h2>How these numbers are measured</h2>
    <span class="muted">Definitions</span></summary>
  <div class="panel__body"><dl class="definitions__list">{items}</dl></div>
</details>
"""


# ------------------------------------------------------------------- routes

@router.get("/admin/analytics")
def analytics_page(request: Request) -> Response:
    require_admin(request)
    view = View(request)
    report = reports.build(view.period, category=view.category)

    content = f"""
{controls(view)}
{kpis(report, view)}
{trend(report, view)}
<div class="dash">
  {category_panel(report, view)}
  {sales_summary(report)}
  {best_sellers_panel(report, view)}
  {activity_panel(report)}
  {customers_panel(report, view)}
  {funnel_panel(report)}
  {traffic_panel(report)}
  {recent_orders_panel(report)}
  {definitions()}
</div>
"""
    actions = (
        f'<a class="btn btn--small btn--ghost" href="{E(view.url("/admin/analytics/export"))}">'
        f'Export CSV</a>'
        f'<button class="btn btn--small btn--quiet" type="button" data-print>Print</button>'
    )
    return html_response(admin_layout(request, content, title="Analytics",
                                      active="/admin/analytics", actions=actions))


@router.get("/admin/analytics/export")
def analytics_export(request: Request) -> Response:
    user = require_admin(request)
    view = View(request)
    report = reports.build(view.period, category=view.category)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for row in reports.export_rows(report):
        writer.writerow([csv_safe(cell) for cell in row])
    period = view.period
    audit("analytics.export", actor=user["email"],
          subject=f"{period.start.isoformat()}..{period.end.isoformat()}",
          detail=f"{len(report.buckets)} {period.grain} rows", ip=request.remote_addr)
    filename = f"mog-analytics-{period.start.isoformat()}-to-{period.end.isoformat()}.csv"
    return Response(
        buffer.getvalue(), content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
