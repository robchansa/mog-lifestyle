"""Server-rendered charts for the admin console.

No charting library and no client-side rendering: a chart is an SVG for the
marks plus ordinary HTML for every piece of text.  That split is what makes
them responsive -- the SVG stretches to any width (`preserveAspectRatio=none`,
with non-scaling strokes) while axis labels stay crisp, readable text that can
be hidden selectively on narrow screens.  It also keeps the strict
Content-Security-Policy intact: there is no inline script to allow.

Hover and tap detail comes from CSS (`:hover`, plus a class admin.js toggles on
tap).  Every chart also ships its numbers as a real table behind a disclosure,
which is what a screen reader or a keyboard user reads instead of the marks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

from .web import escape as E

Formatter = Callable[[float], str]

TONES = ("ink", "mid", "light", "faint")


@dataclass
class Series:
    name: str
    values: Sequence[float | None]
    tone: str = "ink"            # ink | mid | light | faint  (bars)
    dashed: bool = False         # lines only


@dataclass
class Chart:
    title: str                   # accessible name, also the table caption
    labels: Sequence[str]        # short axis label per bucket
    long_labels: Sequence[str]   # unambiguous label per bucket
    bars: Sequence[Series]       # several series are stacked
    lines: Sequence[Series] = field(default_factory=list)
    fmt: Formatter = str         # full value, for tooltips and the table
    fmt_axis: Formatter = str    # compact value, for the y axis
    integer: bool = False        # counts: keep gridlines on whole numbers
    summary: str = ""            # one sentence for assistive technology
    height: str = "15rem"
    show_total: bool = False     # stacked bars: add a Total row to tips/table
    legend: bool = True


# ----------------------------------------------------------------- scaling

def nice_scale(peak: float, *, integer: bool = False, ticks: int = 4) -> tuple[float, list[float]]:
    """A round top value and evenly spaced gridlines that reach it."""
    if peak <= 0:
        step = 1.0 if integer else 0.25
        return step * ticks, [step * i for i in range(1, ticks + 1)]
    raw = peak / ticks
    exponent = 10 ** math.floor(math.log10(raw))
    candidates = ((1, 2, 3, 4, 5, 6, 8, 10) if integer and exponent < 10
                  else (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10))
    step = next(m * exponent for m in candidates if m * exponent >= raw - 1e-9)
    if integer:
        step = max(1.0, float(math.ceil(step)))
    return round(step * ticks, 9), [round(step * i, 9) for i in range(1, ticks + 1)]


def compact_number(value: float) -> str:
    value = float(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(value) >= limit:
            scaled = value / limit
            text = f"{scaled:.1f}".rstrip("0").rstrip(".") if scaled < 100 else f"{scaled:.0f}"
            return f"{text}{suffix}"
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def compact_money(cents: float, symbol: str = "$") -> str:
    dollars = cents / 100
    if abs(dollars) < 1000:
        if dollars == int(dollars):
            return f"{symbol}{int(dollars):,}"
        return f"{symbol}{dollars:,.2f}"
    return f"{symbol}{compact_number(dollars)}"


# --------------------------------------------------------------- rendering

_UNIT = 10.0          # viewBox units per bucket


def _bar_inset(count: int) -> float:
    """Gap on each side of a bar, as a share of its slot."""
    if count <= 16:
        return 0.2
    if count <= 45:
        return 0.15
    if count <= 120:
        return 0.1
    return 0.04


def _fmt_num(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def render(chart: Chart) -> str:
    count = len(chart.labels)
    if count == 0:
        return '<p class="muted">No data for this range.</p>'

    stacked = [
        sum((s.values[i] or 0) for s in chart.bars) for i in range(count)
    ]
    line_values = [v for s in chart.lines for v in s.values if v is not None]
    top, ticks = nice_scale(max(stacked + line_values + [0]), integer=chart.integer)

    width = count * _UNIT
    inset = _bar_inset(count) * _UNIT

    def y(value: float) -> float:
        return 100 - (value / top * 100 if top else 0)

    # -- marks ----------------------------------------------------------
    grid = "".join(
        f'<line class="chart__gridline" x1="0" x2="{_fmt_num(width)}" '
        f'y1="{_fmt_num(y(t))}" y2="{_fmt_num(y(t))}"/>'
        for t in ticks
    )
    bars: list[str] = []
    for i in range(count):
        base = 0.0
        x = i * _UNIT + inset
        w = _UNIT - 2 * inset
        for series in chart.bars:
            value = series.values[i] or 0
            if value <= 0:
                continue
            height = value / top * 100
            if height < 0.6:
                height = 0.6               # a sliver is still visible
            top_y = 100 - (base / top * 100) - height
            bars.append(
                f'<rect class="chart__bar chart__bar--{E(series.tone)}" '
                f'x="{_fmt_num(x)}" y="{_fmt_num(top_y)}" '
                f'width="{_fmt_num(w)}" height="{_fmt_num(height)}"/>'
            )
            base += value
    lines: list[str] = []
    for series in chart.lines:
        points = [
            f"{_fmt_num(i * _UNIT + _UNIT / 2)},{_fmt_num(y(v))}"
            for i, v in enumerate(series.values[:count]) if v is not None
        ]
        if len(points) > 1:
            dashed = " chart__line--dashed" if series.dashed else ""
            lines.append(
                f'<polyline class="chart__line{dashed}" points="{" ".join(points)}"/>'
            )

    svg = (
        f'<svg class="chart__svg" viewBox="0 0 {_fmt_num(width)} 100" '
        f'preserveAspectRatio="none" aria-hidden="true" focusable="false">'
        f'{grid}{"".join(bars)}{"".join(lines)}'
        f'<line class="chart__baseline" x1="0" x2="{_fmt_num(width)}" y1="100" y2="100"/>'
        f'</svg>'
    )

    # -- text -----------------------------------------------------------
    y_ticks = '<span class="chart__ytick" style="bottom:0%">' \
              f'{E(chart.fmt_axis(0))}</span>' + "".join(
        f'<span class="chart__ytick" style="bottom:{_fmt_num(t / top * 100)}%">'
        f'{E(chart.fmt_axis(t))}</span>'
        for t in ticks
    )

    step = max(1, math.ceil(count / 7))
    x_ticks = []
    for i in range(0, count, step):
        left = (i + 0.5) / count * 100
        classes = ["chart__xtick"]
        if (i // step) % 2:
            classes.append("chart__xtick--minor")
        if left < 7:
            classes.append("chart__xtick--start")
        elif left > 93:
            classes.append("chart__xtick--end")
        x_ticks.append(
            f'<span class="{" ".join(classes)}" style="left:{_fmt_num(left)}%">'
            f'{E(chart.labels[i])}</span>'
        )

    all_series = [(s, False) for s in chart.bars] + [(s, True) for s in chart.lines]
    hits = []
    for i in range(count):
        rows = "".join(
            f'<span class="chart__tip-row"><i class="swatch {_swatch_class(s, line)}"></i>'
            f'{E(s.name)}<b>{E(_value_or_dash(s.values, i, chart.fmt))}</b></span>'
            for s, line in all_series
        )
        if chart.show_total and len(chart.bars) > 1:
            rows += (f'<span class="chart__tip-row chart__tip-row--total">Total'
                     f'<b>{E(chart.fmt(stacked[i]))}</b></span>')
        centre = (i + 0.5) / count
        align = " chart__tip--start" if centre < 0.25 else (
            " chart__tip--end" if centre > 0.75 else "")
        hits.append(
            f'<span class="chart__hit" style="left:{_fmt_num(i / count * 100)}%;'
            f'width:{_fmt_num(100 / count)}%">'
            f'<span class="chart__tip{align}"><strong>{E(chart.long_labels[i])}</strong>'
            f'{rows}</span></span>'
        )

    legend = ""
    if chart.legend and len(all_series) > 1:
        legend = '<ul class="chart__legend">' + "".join(
            f'<li><i class="swatch {_swatch_class(s, line)}"></i>{E(s.name)}</li>'
            for s, line in all_series
        ) + "</ul>"

    return f"""
<figure class="chart" style="--chart-h:{E(chart.height)}">
  {legend}
  <div class="chart__frame" aria-hidden="true">
    <div class="chart__y">{y_ticks}</div>
    <div class="chart__plot">{svg}<div class="chart__hits" data-chart-hits>{"".join(hits)}</div></div>
    <div class="chart__x">{"".join(x_ticks)}</div>
  </div>
  <figcaption class="visually-hidden">{E(chart.summary or chart.title)}</figcaption>
</figure>
{data_table(chart, stacked)}
"""


def _value_or_dash(values: Sequence[float | None], i: int, fmt: Formatter) -> str:
    if i >= len(values) or values[i] is None:
        return "—"
    return fmt(values[i])


def _swatch_class(series: Series, is_line: bool) -> str:
    if is_line:
        return "swatch--dashed" if series.dashed else "swatch--line"
    return f"swatch--{series.tone}"


def data_table(chart: Chart, stacked: Sequence[float]) -> str:
    all_series = list(chart.bars) + list(chart.lines)
    total = chart.show_total and len(chart.bars) > 1
    head = "".join(f'<th scope="col" class="num">{E(s.name)}</th>' for s in all_series)
    if total:
        head += '<th scope="col" class="num">Total</th>'
    body = "".join(
        "<tr>"
        f'<th scope="row">{E(chart.long_labels[i])}</th>'
        + "".join(
            f'<td class="num">{E(_value_or_dash(s.values, i, chart.fmt))}</td>'
            for s in all_series
        )
        + (f'<td class="num">{E(chart.fmt(stacked[i]))}</td>' if total else "")
        + "</tr>"
        for i in range(len(chart.labels))
    )
    return f"""
<details class="chart__data">
  <summary>View as table</summary>
  <div class="table-wrap">
    <table class="table">
      <caption class="visually-hidden">{E(chart.title)}</caption>
      <thead><tr><th scope="col">Period</th>{head}</tr></thead>
      <tbody>{body}</tbody>
    </table>
  </div>
</details>
"""


def sparkline(values: Sequence[float]) -> str:
    """A decorative trend line for a summary card; the card states the value."""
    if len(values) < 2:
        return ""
    peak = max(max(values), 0) or 1
    count = len(values) - 1
    points = [
        (i / count * 100, 28 - (max(v, 0) / peak * 26)) for i, v in enumerate(values)
    ]
    line = " ".join(f"{_fmt_num(x)},{_fmt_num(y)}" for x, y in points)
    area = f"M0,30 L{line.replace(' ', ' L')} L100,30 Z"
    return (
        '<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" '
        'aria-hidden="true" focusable="false">'
        f'<path class="spark__area" d="{area}"/>'
        f'<polyline class="spark__line" points="{line}"/></svg>'
    )


def share_bar(pct: float, *, tone: str = "ink") -> str:
    width = max(0.0, min(100.0, pct))
    return (f'<span class="share" aria-hidden="true"><span class="share__fill '
            f'share__fill--{E(tone)}" style="width:{_fmt_num(width)}%"></span></span>')
