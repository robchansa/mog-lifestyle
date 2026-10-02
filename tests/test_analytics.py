"""The admin Analytics page: periods, store-local time, sales maths, category
attribution, charts, access control and export.

Numbers on a dashboard are only useful if they are right, so most of these
tests build a small, exactly known history and check every figure against it.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app import charts, db, demo, reports
from app.config import config
from app.reports import Period, bucket_start, count_buckets, offset_spans, resolve
from tests.support import StoreTestCase
from tests.test_integration import LiveServerTestCase

TODAY = date(2026, 10, 1)


def utc(text: str) -> str:
    """A UTC timestamp in the format SQLite's datetime('now') writes."""
    return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")


class StoreTime:
    """Pin the store's timezone for one test."""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.original = config.timezone
        config.timezone = self.name

    def __exit__(self, *exc):
        config.timezone = self.original


class HistoryMixin:
    """Factories that write orders and traffic at exact moments."""

    def add_collection(self, slug: str, department: str) -> int:
        with db.tx():
            return db.insert("collections", slug=slug, title=slug.title(),
                             department=department)

    def add_order(self, *, paid_at: str, lines: list[tuple], status: str = "paid",
                  email: str = "buyer@example.com", shipping: int = 0,
                  discount: int = 0, refunded_at: str | None = None) -> int:
        subtotal = sum(unit * qty for _, unit, qty, *_ in lines)
        with db.tx():
            order_id = db.insert(
                "orders", number=f"T-{len(email)}-{paid_at}-{subtotal}-{status}",
                email=email, status=status, subtotal_cents=subtotal,
                discount_cents=discount, shipping_cents=shipping,
                total_cents=subtotal - discount + shipping,
                created_at=paid_at, paid_at=paid_at, refunded_at=refunded_at,
            )
            for slug, unit, qty, department, on_sale in lines:
                db.insert("order_items", order_id=order_id, sku=f"SKU-{slug}",
                          title=slug.replace("-", " ").title(), slug=slug,
                          unit_cents=unit, quantity=qty,
                          department=department, on_sale=on_sale)
        return order_id

    def add_visit(self, visitor: str, started_at: str, *, views: int = 1,
                  product: bool = False, cart: bool = False, checkout: bool = False,
                  device: str = "mobile", referrer: str = "") -> None:
        with db.tx():
            db.insert("visits", visitor=visitor, started_at=started_at, views=views,
                      device=device, referrer=referrer, entry_path="/",
                      saw_product=int(product), saw_cart=int(cart),
                      saw_checkout=int(checkout))
            for index in range(views):
                db.insert("page_views", path="/" if index == 0 else f"/p{index}",
                          kind="page", visitor=visitor, device=device,
                          referrer=referrer if index == 0 else "",
                          created_at=started_at)

    def add_customer(self, email: str, created_at: str, role: str = "customer") -> None:
        with db.tx():
            db.insert("users", email=email, password_hash="!", role=role,
                      created_at=created_at)


# ================================================================ periods

class PeriodTests(StoreTestCase):
    def test_default_is_the_last_thirty_days_by_day(self):
        period = resolve({}, today=TODAY)
        self.assertEqual((period.start, period.end), (date(2026, 9, 2), TODAY))
        self.assertEqual((period.days, period.grain, period.preset), (30, "day", "30d"))

    def test_presets(self):
        cases = {
            "7d": (date(2026, 9, 25), "day"),
            "90d": (date(2026, 7, 4), "day"),
            "12m": (date(2025, 11, 1), "month"),
            "ytd": (date(2026, 1, 1), "month"),
        }
        for preset, (start, grain) in cases.items():
            with self.subTest(preset=preset):
                period = resolve({"range": preset}, today=TODAY)
                self.assertEqual((period.start, period.end, period.grain),
                                 (start, TODAY, grain))

    def test_all_time_starts_at_the_first_activity(self):
        period = resolve({"range": "all"}, today=TODAY, first_day=date(2024, 3, 9))
        self.assertEqual(period.start, date(2024, 3, 9))
        self.assertEqual(period.grain, "month")
        self.assertIsNone(period.previous(), "all time has nothing to compare with")

    def test_all_time_with_no_history_falls_back_to_thirty_days(self):
        period = resolve({"range": "all"}, today=TODAY)
        self.assertEqual(period.days, 30)

    def test_a_custom_range(self):
        period = resolve({"from": "2026-03-01", "to": "2026-03-31"}, today=TODAY)
        self.assertEqual((period.start, period.end, period.preset), (
            date(2026, 3, 1), date(2026, 3, 31), ""))

    def test_reversed_dates_are_swapped_with_a_notice(self):
        period = resolve({"from": "2026-03-31", "to": "2026-03-01"}, today=TODAY)
        self.assertEqual((period.start, period.end), (date(2026, 3, 1), date(2026, 3, 31)))
        self.assertTrue(any("swapped" in n for n in period.notices))

    def test_future_dates_stop_at_today(self):
        period = resolve({"from": "2026-09-20", "to": "2027-01-01"}, today=TODAY)
        self.assertEqual(period.end, TODAY)
        self.assertIn("Reports stop at today.", period.notices)

    def test_unreadable_input_never_raises(self):
        for params in ({"from": "2026-13-45"}, {"to": "yesterday"},
                       {"range": "forever"}, {"grain": "hour"},
                       {"from": "'; DROP TABLE orders; --"}):
            with self.subTest(params=params):
                period = resolve(params, today=TODAY)
                self.assertEqual(period.end, TODAY)
                self.assertIn(period.grain, reports.GRAINS)
        self.assertTrue(db.scalar("SELECT count(*) FROM sqlite_master WHERE name = 'orders'"))

    def test_a_grain_with_too_many_bars_is_coarsened(self):
        period = resolve({"from": "2020-01-01", "to": "2026-01-01", "grain": "day"},
                         today=TODAY)
        self.assertEqual(period.grain, "week")
        self.assertTrue(any("2,193 bars" in n for n in period.notices), period.notices)
        self.assertFalse(period.allows("day"))

    def test_an_explicit_grain_is_honoured_when_it_fits(self):
        period = resolve({"range": "90d", "grain": "week"}, today=TODAY)
        self.assertEqual(period.grain, "week")

    def test_previous_period_is_the_same_length_immediately_before(self):
        previous = resolve({"range": "7d"}, today=TODAY).previous()
        self.assertEqual((previous.start, previous.end),
                         (date(2026, 9, 18), date(2026, 9, 24)))

    def test_year_to_date_compares_with_the_same_dates_last_year(self):
        previous = resolve({"range": "ytd"}, today=TODAY).previous()
        self.assertEqual((previous.start, previous.end),
                         (date(2025, 1, 1), date(2025, 10, 1)))

    def test_leap_day_comparison(self):
        period = Period(date(2028, 1, 1), date(2028, 2, 29), "week", "ytd")
        self.assertEqual(period.previous().end, date(2027, 2, 28))


class BucketTests(StoreTestCase):
    def test_weeks_start_on_monday(self):
        self.assertEqual(bucket_start(date(2026, 10, 1), "week"), date(2026, 9, 28))
        self.assertEqual(bucket_start(date(2026, 9, 28), "week"), date(2026, 9, 28))
        self.assertEqual(bucket_start(date(2026, 10, 4), "week"), date(2026, 9, 28))

    def test_partial_buckets_are_clipped_to_the_period(self):
        buckets = Period(date(2026, 9, 2), date(2026, 10, 1), "week").buckets()
        self.assertEqual(buckets[0].key, "2026-08-31")
        self.assertEqual((buckets[0].start, buckets[0].end),
                         (date(2026, 9, 2), date(2026, 9, 6)))
        self.assertEqual(buckets[-1].end, date(2026, 10, 1))
        self.assertEqual(len(buckets), count_buckets(date(2026, 9, 2), TODAY, "week"))

    def test_labels(self):
        months = Period(date(2025, 11, 1), date(2026, 2, 10), "month").buckets()
        self.assertEqual([b.label for b in months], ["Nov 2025", "Dec", "Jan 2026", "Feb"])
        self.assertEqual(months[0].long_label, "November 2025")
        self.assertEqual(months[-1].long_label, "1–10 Feb 2026")
        days = Period(date(2026, 9, 7), date(2026, 9, 8), "day").buckets()
        self.assertEqual(days[0].long_label, "Mon 7 Sep 2026")
        week = Period(date(2026, 9, 28), date(2026, 10, 4), "week").buckets()[0]
        self.assertEqual(week.long_label, "28 Sep – 4 Oct 2026")
        years = Period(date(2024, 1, 1), date(2026, 10, 1), "year").buckets()
        self.assertEqual([b.label for b in years], ["2024", "2025", "2026"])
        self.assertEqual(years[-1].long_label, "1 Jan – 1 Oct 2026")

    def test_sql_buckets_match_python_buckets_for_every_grain(self):
        """The SQL expression and the Python calendar must agree, or bars land
        in the wrong slot."""
        days = [date(2025, 12, 29) + timedelta(days=n) for n in range(0, 500, 3)]
        for grain in reports.GRAINS:
            for day in days:
                with self.subTest(grain=grain, day=day):
                    sql = reports.bucket_sql("?", grain)
                    got = db.scalar(f"SELECT {sql}", (f"{day.isoformat()} 13:00:00",))
                    self.assertEqual(got, bucket_start(day, grain).isoformat())


# ============================================================ store time

class StoreTimeTests(HistoryMixin, StoreTestCase):
    def test_offset_spans_find_daylight_saving_changes_to_the_second(self):
        zone = ZoneInfo("America/Boise")
        lo = datetime(2026, 1, 1, 7, tzinfo=timezone.utc)
        hi = datetime(2027, 1, 1, 7, tzinfo=timezone.utc)
        spans = offset_spans(lo, hi, zone)
        self.assertEqual([minutes for _, minutes in spans], [-420, -360, -420])
        self.assertEqual(spans[1][0], datetime(2026, 3, 8, 9, tzinfo=timezone.utc))
        self.assertEqual(spans[2][0], datetime(2026, 11, 1, 8, tzinfo=timezone.utc))

    def test_a_zone_without_daylight_saving_is_one_span(self):
        spans = offset_spans(datetime(2026, 1, 1, tzinfo=timezone.utc),
                             datetime(2027, 1, 1, tzinfo=timezone.utc), timezone.utc)
        self.assertEqual(spans, [(datetime(2026, 1, 1, tzinfo=timezone.utc), 0)])

    def test_late_evening_sales_count_on_the_stores_day_not_utcs(self):
        with StoreTime("America/Boise"):
            # 9pm on 7 Sep in Boise is 03:00 UTC on 8 Sep.
            self.add_order(paid_at=utc("2026-09-08T03:00:00"),
                           lines=[("tee", 5000, 1, "men", 0)])
            period = Period(date(2026, 9, 7), date(2026, 9, 8), "day")
            report = reports.build(period)
        self.assertEqual(report.series["revenue"], [5000, 0])

    def test_bucketing_is_right_on_both_sides_of_a_clock_change(self):
        with StoreTime("America/Boise"):
            # Clocks go forward at 02:00 MST on 8 Mar 2026 (09:00 UTC).
            self.add_order(paid_at=utc("2026-03-08T06:30:00"),     # 23:30 MST, 7 Mar
                           lines=[("a", 100, 1, "men", 0)])
            self.add_order(paid_at=utc("2026-03-09T05:30:00"),     # 23:30 MDT, 8 Mar
                           lines=[("b", 200, 1, "men", 0)])
            self.add_order(paid_at=utc("2026-03-09T06:30:00"),     # 00:30 MDT, 9 Mar
                           lines=[("c", 400, 1, "men", 0)])
            report = reports.build(Period(date(2026, 3, 7), date(2026, 3, 9), "day"))
        self.assertEqual(report.series["revenue"], [100, 200, 400])

    def test_range_bounds_follow_local_midnight(self):
        with StoreTime("America/Boise"):
            lo, hi = Period(date(2026, 9, 1), date(2026, 9, 30), "day").utc_range()
        self.assertEqual(lo, datetime(2026, 9, 1, 6, tzinfo=timezone.utc))
        self.assertEqual(hi, datetime(2026, 10, 1, 6, tzinfo=timezone.utc))

    def test_an_unknown_timezone_falls_back_to_utc(self):
        with StoreTime("Mars/Olympus_Mons"):
            self.assertFalse(config.zone_is_valid)
            self.assertEqual(config.zone, timezone.utc)
            report = reports.build(resolve({}, today=TODAY))
        self.assertEqual(len(report.buckets), 30)


# ================================================================ figures

class SalesFigureTests(HistoryMixin, StoreTestCase):
    """A small, exactly known month of trade."""

    def setUp(self):
        super().setUp()
        self.ctx = StoreTime("UTC")
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__)
        self.period = Period(date(2026, 9, 1), date(2026, 9, 30), "day")
        # Three paid orders, one refunded, one cancelled, one pending.
        self.add_order(paid_at="2026-09-02 10:00:00", email="ann@example.com",
                       shipping=800, discount=500,
                       lines=[("men-tee", 5000, 2, "men", 0),
                              ("beanie", 3000, 1, "general", 1)])
        self.add_order(paid_at="2026-09-02 18:00:00", email="ben@example.com",
                       lines=[("crop-top", 4000, 1, "women", 0)], status="fulfilled")
        self.add_order(paid_at="2026-09-15 12:00:00", email="ANN@example.com",
                       lines=[("men-tee", 5000, 1, "men", 0)])
        self.add_order(paid_at="2026-09-05 12:00:00", email="cat@example.com",
                       lines=[("beanie", 3000, 1, "general", 1)], status="refunded",
                       refunded_at="2026-09-20 09:00:00")
        self.add_order(paid_at="2026-09-06 12:00:00", email="dan@example.com",
                       lines=[("crop-top", 4000, 3, "women", 0)], status="cancelled")
        self.add_order(paid_at="2026-09-07 12:00:00", email="eve@example.com",
                       lines=[("crop-top", 4000, 3, "women", 0)], status="pending")
        # An order outside the range must not leak in.
        self.add_order(paid_at="2026-08-31 23:59:59", email="old@example.com",
                       lines=[("men-tee", 5000, 1, "men", 0)])
        self.report = reports.build(self.period)

    def test_revenue_orders_and_average_only_count_paid_and_shipped(self):
        totals = self.report.totals
        self.assertEqual(totals.orders, 3)
        # 13,000 - 500 + 800, then 4,000, then 5,000.
        self.assertEqual(totals.revenue_cents, 13300 + 4000 + 5000)
        self.assertEqual(totals.aov_cents, (13300 + 4000 + 5000) // 3)
        self.assertEqual(totals.units, 5)
        self.assertEqual(totals.gross_cents, 22000)
        self.assertEqual(totals.discount_cents, 500)
        self.assertEqual(totals.net_sales_cents, 21500)
        self.assertEqual(totals.shipping_cents, 800)

    def test_refunds_are_reported_separately_by_refund_date(self):
        self.assertEqual((self.report.totals.refunds, self.report.totals.refund_cents),
                         (1, 3000))

    def test_the_daily_series_adds_up_to_the_totals(self):
        series = self.report.series
        self.assertEqual(sum(series["revenue"]), self.report.totals.revenue_cents)
        self.assertEqual(sum(series["orders"]), 3)
        self.assertEqual(series["orders"][1], 2)          # 2 September
        self.assertEqual(series["aov"][1], (13300 + 4000) // 2)

    def test_men_women_and_everyone_partition_product_sales(self):
        rows = {row.key: row for row in self.report.category_rows}
        self.assertEqual([row.key for row in self.report.category_rows],
                         ["men", "women", "general", "sale"])
        self.assertEqual(rows["men"].cents, 15000)
        self.assertEqual(rows["men"].units, 3)
        self.assertEqual(rows["men"].orders, 2)
        self.assertEqual(rows["women"].cents, 4000)
        self.assertEqual(rows["general"].cents, 3000)
        departments = rows["men"].cents + rows["women"].cents + rows["general"].cents
        self.assertEqual(departments, self.report.totals.gross_cents)
        self.assertAlmostEqual(rows["men"].share_pct + rows["women"].share_pct
                               + rows["general"].share_pct, 100, delta=0.2)

    def test_sale_cuts_across_departments(self):
        sale = self.report.category_rows[-1]
        self.assertEqual((sale.label, sale.cents, sale.units, sale.orders),
                         ("Sale", 3000, 1, 1))

    def test_unassigned_appears_only_when_it_has_sales(self):
        self.assertNotIn("unassigned", [r.key for r in self.report.category_rows])
        self.add_order(paid_at="2026-09-20 12:00:00",
                       lines=[("mystery", 1000, 1, "", 0)])
        rows = reports.build(self.period).category_rows
        self.assertIn("Unassigned", [r.label for r in rows])

    def test_category_series_per_bucket(self):
        self.assertEqual(self.report.category_series["men"][1], 10000)
        self.assertEqual(self.report.category_series["sale"][1], 3000)
        self.assertEqual(sum(self.report.category_series["women"]), 4000)

    def test_best_sellers_rank_by_units_with_shares(self):
        best = self.report.best_sellers
        self.assertEqual([b["slug"] for b in best], ["men-tee", "crop-top", "beanie"])
        self.assertEqual((best[0]["units"], best[0]["orders"], best[0]["cents"]),
                         (3, 2, 15000))
        self.assertAlmostEqual(sum(b["share_pct"] for b in best), 100, delta=0.2)

    def test_best_sellers_can_be_filtered_by_category(self):
        women = reports.build(self.period, category="women").best_sellers
        self.assertEqual([b["slug"] for b in women], ["crop-top"])
        self.assertEqual(women[0]["share_pct"], 100.0)
        sale = reports.build(self.period, category="sale").best_sellers
        self.assertEqual([b["slug"] for b in sale], ["beanie"])
        anything = reports.build(self.period, category="<script>")
        self.assertEqual(anything.category_filter, "")

    def test_buyers_are_matched_case_insensitively(self):
        # ann@ and ANN@ are one person who bought twice in the range.
        self.assertEqual(self.report.buyers,
                         {"buyers": 2, "returning": 0, "first_time": 2, "repeat": 1})

    def test_a_buyer_from_before_the_range_is_returning(self):
        later = reports.build(Period(date(2026, 9, 10), date(2026, 9, 30), "day"))
        self.assertEqual(later.buyers["returning"], 1)       # Ann, first in Sep 2

    def test_recent_orders_and_activity_stay_inside_the_range(self):
        numbers = [o["email"] for o in self.report.recent_orders]
        self.assertNotIn("old@example.com", numbers)
        kinds = {event["kind"] for event in self.report.activity}
        self.assertTrue({"paid", "refunded"} <= kinds, kinds)

    def test_the_comparison_period_is_computed(self):
        previous = self.report.previous_totals
        self.assertEqual(previous.orders, 1)           # the 31 August order
        self.assertEqual(self.report.change("orders"),
                         {"direction": "up", "text": "200%"})


class TrafficFigureTests(HistoryMixin, StoreTestCase):
    def setUp(self):
        super().setUp()
        self.ctx = StoreTime("UTC")
        self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__)
        self.add_visit("v1", "2026-09-01 10:00:00", views=3, product=True, cart=True,
                       referrer="l.instagram.com")
        self.add_visit("v2", "2026-09-01 11:00:00", views=1, device="desktop")
        self.add_visit("v3", "2026-09-02 09:00:00", views=5, product=True, cart=True,
                       checkout=True, referrer="www.google.com")
        self.add_visit("v4", "2026-09-03 09:00:00", views=2, product=True,
                       referrer="l.instagram.com")
        self.add_order(paid_at="2026-09-02 09:10:00", lines=[("x", 1000, 1, "men", 0)])
        self.add_customer("new@example.com", "2026-09-02 08:00:00")
        self.add_customer("staff@example.com", "2026-09-02 08:00:00", role="staff")
        self.report = reports.build(Period(date(2026, 9, 1), date(2026, 9, 3), "day"))

    def test_visitors_page_views_and_conversion(self):
        totals = self.report.totals
        self.assertEqual((totals.visitors, totals.views), (4, 11))
        self.assertEqual(totals.conversion_pct, 25.0)
        self.assertEqual(self.report.series["visitors"], [2, 1, 1])
        self.assertEqual(self.report.series["views"], [4, 5, 2])
        self.assertEqual(self.report.series["conversion"], [0, 100.0, 0])

    def test_the_funnel(self):
        self.assertEqual(self.report.funnel, [
            ("Visited", 4), ("Viewed a product", 3), ("Opened the bag", 2),
            ("Started checkout", 1), ("Ordered", 1)])

    def test_sources_devices_and_pages(self):
        referrers = [(r["referrer"], r["visitors"], r["views"])
                     for r in self.report.top_referrers]
        self.assertEqual(referrers, [("l.instagram.com", 2, 5), ("www.google.com", 1, 5)])
        self.assertEqual([(d["device"], d["visitors"]) for d in self.report.devices],
                         [("mobile", 3), ("desktop", 1)])
        self.assertEqual(self.report.top_paths[0]["path"], "/")

    def test_new_customers_exclude_staff_accounts(self):
        self.assertEqual(self.report.totals.new_customers, 1)
        self.assertEqual(self.report.customer_base, {"at_start": 0, "at_end": 1})

    def test_an_empty_store_reports_zeros_not_errors(self):
        db.reset()
        db.migrate()
        report = reports.build(resolve({}, today=TODAY))
        self.assertEqual(report.totals.revenue_cents, 0)
        self.assertEqual(report.totals.conversion_pct, 0.0)
        self.assertEqual(report.best_sellers, [])
        self.assertTrue(all(v == 0 for v in report.series["aov"]))


class ChangeTests(StoreTestCase):
    def test_percentage_changes(self):
        self.assertEqual(reports.change(150, 100, "money"),
                         {"direction": "up", "text": "50.0%"})
        self.assertEqual(reports.change(50, 100, "count"),
                         {"direction": "down", "text": "50.0%"})
        self.assertEqual(reports.change(100, 100, "count")["direction"], "flat")
        self.assertEqual(reports.change(5, 0, "count"), {"direction": "new", "text": "New"})
        self.assertEqual(reports.change(0, 0, "count")["text"], "No change")
        self.assertEqual(reports.change(5, None, "count")["direction"], "none")

    def test_rates_change_in_points(self):
        self.assertEqual(reports.change(3.1, 2.6, "percent"),
                         {"direction": "up", "text": "0.50 pts"})


# ================================================================ snapshot

class SnapshotTests(StoreTestCase):
    def test_checkout_records_department_and_markdown(self):
        from app import orders
        men = db.insert("collections", slug="mens-tees", title="Tees", department="men")
        product = self.make_product("Sale Tee", price_cents=4000, collection_id=men,
                                    compare_cents=6000)
        cart = self.make_cart((product["variant_id"], 1))
        order = orders.create_from_cart(
            cart, email="a@example.com", user_id=None,
            shipping={"name": "A", "line1": "1 St", "city": "Boise",
                      "region": "ID", "postal": "83702"})
        line = db.one("SELECT * FROM order_items WHERE order_id = ?", (order["id"],))
        self.assertEqual((line["department"], line["on_sale"]), ("men", 1))

        # Moving the product and ending the sale must not rewrite history.
        women = db.insert("collections", slug="w", title="W", department="women")
        with db.tx():
            db.update("products", "id = ?", (product["id"],), collection_id=women,
                      compare_cents=None)
        line = db.one("SELECT * FROM order_items WHERE order_id = ?", (order["id"],))
        self.assertEqual((line["department"], line["on_sale"]), ("men", 1))

    def test_full_price_lines_are_not_sale(self):
        from app import orders
        product = self.make_product("Plain Tee", price_cents=4000)
        order = orders.create_from_cart(
            self.make_cart((product["variant_id"], 1)), email="b@example.com",
            user_id=None, shipping={"name": "B", "line1": "1 St", "city": "Boise",
                                    "region": "ID", "postal": "83702"})
        line = db.one("SELECT * FROM order_items WHERE order_id = ?", (order["id"],))
        self.assertEqual((line["department"], line["on_sale"]), ("", 0))

    def test_older_order_lines_are_backfilled_from_the_catalogue(self):
        women = db.insert("collections", slug="w", title="W", department="women")
        product = self.make_product("Old Crop", price_cents=3000, collection_id=women,
                                    compare_cents=4500)
        with db.tx():
            order_id = db.insert("orders", number="OLD-1", email="c@example.com",
                                 status="paid")
            db.insert("order_items", order_id=order_id, variant_id=None,
                      sku="X", title="Old Crop", slug=db.scalar(
                          "SELECT slug FROM products WHERE id = ?", (product["id"],)),
                      unit_cents=3000, quantity=1)
            db.insert("order_items", order_id=order_id, variant_id=None, sku="Y",
                      title="Gone", slug="deleted-product", unit_cents=100, quantity=1)
        db.backfill_order_item_categories()
        rows = db.query("SELECT title, department, on_sale FROM order_items ORDER BY id")
        self.assertEqual([tuple(r) for r in rows],
                         [("Old Crop", "women", 1), ("Gone", "", 0)])

    def test_refunds_are_timestamped(self):
        from app import orders
        product = self.make_product("Refund Tee")
        order = orders.create_from_cart(
            self.make_cart((product["variant_id"], 1)), email="d@example.com",
            user_id=None, shipping={"name": "D", "line1": "1 St", "city": "Boise",
                                    "region": "ID", "postal": "83702"})
        order = orders.mark_paid(order, send_email=False)
        refunded = orders.refund(order)
        self.assertEqual(refunded["status"], "refunded")
        self.assertTrue(refunded["refunded_at"])


# ================================================================== charts

class ChartTests(StoreTestCase):
    def chart(self, **overrides):
        options = dict(
            title="Revenue by day", labels=["1 Sep", "2 Sep", "3 Sep"],
            long_labels=["Tue 1 Sep 2026", "Wed 2 Sep 2026", "Thu 3 Sep 2026"],
            bars=[charts.Series("This period", [100, 0, 250])],
            lines=[charts.Series("Previous period", [50, 75, None], dashed=True)],
            fmt=lambda v: f"${v / 100:.2f}", fmt_axis=charts.compact_number,
        )
        options.update(overrides)
        return charts.render(charts.Chart(**options))

    def test_bars_lines_ticks_and_tooltips(self):
        html = self.chart()
        self.assertEqual(html.count('class="chart__bar '), 2, "zero values draw no bar")
        self.assertIn('class="chart__line chart__line--dashed"', html)
        self.assertEqual(html.count('class="chart__hit"'), 3)
        self.assertIn("Wed 2 Sep 2026", html)
        self.assertIn("$2.50", html)

    def test_every_chart_carries_an_accessible_table(self):
        html = self.chart()
        self.assertIn('<details class="chart__data">', html)
        self.assertIn('<th scope="row">Thu 3 Sep 2026</th>', html)
        self.assertIn('aria-hidden="true"', html)          # the marks themselves
        self.assertIn('class="visually-hidden"', html)

    def test_missing_comparison_values_show_a_dash(self):
        self.assertIn("<td class=\"num\">—</td>", self.chart())

    def test_all_zero_and_empty_series_render(self):
        flat = self.chart(bars=[charts.Series("x", [0, 0, 0])], lines=[])
        self.assertIn("chart__svg", flat)
        empty = charts.render(charts.Chart(title="t", labels=[], long_labels=[],
                                           bars=[charts.Series("x", [])]))
        self.assertIn("No data", empty)

    def test_stacked_bars_add_a_total(self):
        html = self.chart(bars=[charts.Series("Men", [1, 2, 3], "ink"),
                                charts.Series("Women", [4, 5, 6], "mid")],
                          lines=[], show_total=True, fmt=str)
        self.assertIn("chart__tip-row--total", html)
        self.assertIn('<td class="num">9</td>', html)       # 3 + 6

    def test_labels_are_escaped(self):
        html = self.chart(labels=["<b>x</b>", "b", "c"],
                          long_labels=["<script>alert(1)</script>", "b", "c"])
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<b>x</b>", html)

    def test_scales_are_round_numbers(self):
        self.assertEqual(charts.nice_scale(37, integer=True), (40.0, [10.0, 20.0, 30.0, 40.0]))
        self.assertEqual(charts.nice_scale(3, integer=True)[0], 4.0)
        self.assertEqual(charts.nice_scale(123456)[0], 160000)
        self.assertEqual(charts.nice_scale(0)[0], 1.0)

    def test_compact_formats(self):
        self.assertEqual(charts.compact_money(123456), "$1.2k")
        self.assertEqual(charts.compact_money(95000), "$950")
        self.assertEqual(charts.compact_money(150), "$1.50")
        self.assertEqual(charts.compact_number(2500000), "2.5M")

    def test_a_sparkline_needs_two_points(self):
        self.assertEqual(charts.sparkline([5]), "")
        self.assertIn("<polyline", charts.sparkline([1, 3, 2]))


# ==================================================================== demo

class DemoHistoryTests(StoreTestCase):
    seed_catalogue = True

    def test_generates_tagged_history_and_clears_only_that(self):
        with db.tx():
            db.insert("orders", number="REAL-1", email="real@example.com", status="paid")
        made = demo.generate(days=21, today=TODAY, base_visitors=40)
        self.assertGreater(made["orders"], 0)
        self.assertGreater(made["page_views"], 0)
        self.assertTrue(demo.present())
        self.assertEqual(db.scalar("SELECT count(*) FROM visits WHERE visitor NOT LIKE 'demo%'"), 0)
        untagged = db.scalar(
            "SELECT count(*) FROM orders WHERE notes NOT LIKE '%[demo]%' AND number <> 'REAL-1'")
        self.assertEqual(untagged, 0)
        demo_emails = db.scalar(
            "SELECT count(*) FROM users WHERE role = 'customer' "
            "AND email NOT LIKE '%@demo.example.com'")
        self.assertEqual(demo_emails, 0)

        demo.clear()
        self.assertFalse(demo.present())
        self.assertEqual(db.scalar("SELECT count(*) FROM orders"), 1, "real order kept")
        self.assertEqual(db.scalar("SELECT count(*) FROM page_views"), 0)
        self.assertEqual(db.scalar("SELECT count(*) FROM visits"), 0)

    def test_demo_accounts_cannot_sign_in(self):
        from app import accounts
        demo.generate(days=60, today=TODAY, base_visitors=40)
        email = db.scalar("SELECT email FROM users WHERE email LIKE '%@demo.example.com' LIMIT 1")
        self.assertIsNotNone(email)
        with self.assertRaises(accounts.AuthError):
            accounts.authenticate(email, demo.UNUSABLE_PASSWORD, ip="203.0.113.1")

    def test_nothing_is_dated_in_the_future(self):
        demo.generate(days=14, base_visitors=40)
        for table, column in (("orders", "created_at"), ("orders", "paid_at"),
                              ("orders", "fulfilled_at"), ("page_views", "created_at"),
                              ("users", "created_at")):
            with self.subTest(table=table, column=column):
                self.assertEqual(db.scalar(
                    f"SELECT count(*) FROM {table} WHERE {column} > datetime('now')"), 0)

    def test_refused_in_production(self):
        original = config.env
        config.env = "production"
        try:
            with self.assertRaises(demo.DemoRefused):
                demo.generate(days=7)
        finally:
            config.env = original


# ===================================================================== web

class AnalyticsPageTests(LiveServerTestCase):
    def sign_in(self, email: str, password: str):
        client = self.client()
        client.post("/login", {"csrf_token": client.csrf("/login"),
                               "email": email, "password": password})
        return client

    def admin(self):
        return self.sign_in("info@moglifestyle.fit", "mog-admin-2026")

    def test_anonymous_visitors_are_sent_to_sign_in_and_back(self):
        client = self.client()
        client.get("/admin/analytics?range=12m", follow=False)
        self.assertEqual(client.last_status, 303)
        self.assertEqual(client.last_headers["Location"],
                         "/login?next=%2Fadmin%2Fanalytics%3Frange%3D12m")
        client.get("/admin/analytics/export", follow=False)
        self.assertEqual(client.last_status, 303)

    def test_customers_and_staff_are_refused(self):
        from app import accounts
        accounts.register("shopper@example.com", "a good long passphrase")
        accounts.register("packer@example.com", "a good long passphrase", role="staff")
        for email in ("shopper@example.com", "packer@example.com"):
            client = self.sign_in(email, "a good long passphrase")
            for path in ("/admin/analytics", "/admin/analytics/export"):
                with self.subTest(email=email, path=path):
                    body = client.get(path, follow=False)
                    self.assertEqual(client.last_status, 403)
                    self.assertNotIn("Revenue", body)

    def test_staff_do_not_see_the_analytics_link(self):
        from app import accounts
        accounts.register("picker@example.com", "a good long passphrase", role="staff")
        body = self.sign_in("picker@example.com", "a good long passphrase").get("/admin")
        self.assertEqual(body.count('href="/admin/analytics"'), 0)
        self.assertIn('href="/admin/orders"', body)

    def test_admins_see_every_section(self):
        body = self.admin().get("/admin/analytics")
        for marker in ("Revenue", "Orders", "Average order", "Visitors", "Page views",
                       "Conversion", "New customers", "Sales by category",
                       "Best-selling products", "Sales summary", "Customer growth",
                       "Recent orders", "Sales activity", "Visitors &amp; page views",
                       "Men", "Women", "Everyone", "Sale", "Daily", "Weekly",
                       "Monthly", "Yearly", "View as table"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)
        self.assertIn('aria-current="page"', body)

    def test_admin_pages_are_never_cached_or_indexed(self):
        client = self.admin()
        for path in ("/admin/analytics", "/admin", "/account"):
            with self.subTest(path=path):
                client.get(path)
                self.assertIn("no-store", client.last_headers["Cache-Control"])
                self.assertIn("noindex", client.last_headers["X-Robots-Tag"])
        client.get("/")
        self.assertNotIn("no-store", client.last_headers.get("Cache-Control", ""))

    def test_every_range_and_grouping_renders(self):
        client = self.admin()
        for query in ("range=7d", "range=90d&grain=week", "range=12m",
                      "range=ytd&grain=month", "range=all&grain=year",
                      "from=2026-01-01&to=2026-03-31&grain=week",
                      "metric=conversion", "metric=customers&cat=women",
                      "cat=sale#best-sellers"):
            with self.subTest(query=query):
                client.get(f"/admin/analytics?{query}")
                self.assertEqual(client.last_status, 200)

    def test_hostile_parameters_are_neutralised(self):
        client = self.admin()
        body = client.get("/admin/analytics?metric=%3Cscript%3Ealert(1)%3C/script%3E"
                          "&cat=%22%3E%3Cimg&from=not-a-date&grain=%27")
        self.assertEqual(client.last_status, 200)
        self.assertNotIn("<script>alert(1)", body)
        self.assertNotIn('"><img', body)
        self.assertIn("couldn&#x27;t be read", body)

    def test_a_metric_tab_selects_the_chart(self):
        body = self.admin().get("/admin/analytics?metric=orders")
        self.assertRegex(body, r'<h2 id="trend-title">Orders')
        self.assertRegex(body, r'class="tab is-active"[^>]*aria-current="true">Orders<')

    def test_export_is_csv_audited_and_formula_safe(self):
        client = self.admin()
        body = client.get("/admin/analytics/export?range=7d")
        self.assertIn("text/csv", client.last_headers["Content-Type"])
        self.assertRegex(client.last_headers["Content-Disposition"],
                         r'attachment; filename="mog-analytics-\d{4}-\d\d-\d\d-to-\d{4}-\d\d-\d\d\.csv"')
        rows = list(csv.reader(io.StringIO(body)))
        self.assertEqual(rows[0][:5], ["period_start", "period_end", "label",
                                       "revenue", "orders"])
        self.assertEqual(len(rows), 8)                      # header + 7 days
        self.assertTrue(db.scalar(
            "SELECT count(*) FROM audit_log WHERE action = 'analytics.export'"))

    def test_old_traffic_links_land_on_analytics(self):
        client = self.admin()
        client.get("/admin/traffic?days=90", follow=False)
        self.assertEqual(client.last_status, 301)
        self.assertEqual(client.last_headers["Location"], "/admin/analytics?range=90d#traffic")


class SessionAgeTests(LiveServerTestCase):
    def test_console_sessions_end_after_the_configured_hours(self):
        client = self.client()
        client.post("/login", {"csrf_token": client.csrf("/login"),
                               "email": "info@moglifestyle.fit",
                               "password": "mog-admin-2026"})
        client.get("/admin/analytics")
        self.assertEqual(client.last_status, 200)
        with db.tx():
            db.execute("UPDATE sessions SET created_at = datetime('now', '-13 hours') "
                       "WHERE user_id = (SELECT id FROM users WHERE role = 'admin')")
        client.get("/admin/analytics", follow=False)
        self.assertEqual(client.last_status, 303)
        self.assertTrue(client.last_headers["Location"].startswith("/login?next="))
        self.assertIn("flash", client.cookies)
        self.assertEqual(db.scalar(
            "SELECT count(*) FROM sessions WHERE user_id = "
            "(SELECT id FROM users WHERE role = 'admin')"), 0, "session destroyed")
        self.assertTrue(db.scalar(
            "SELECT count(*) FROM audit_log WHERE action = 'auth.session_expired'"))


class CsvSafetyTests(StoreTestCase):
    def test_formulas_are_defused_but_numbers_survive(self):
        from app.views_admin import csv_safe
        self.assertEqual(csv_safe('=HYPERLINK("http://evil","x")'),
                         '\'=HYPERLINK("http://evil","x")')
        for risky in ("+1+1", "@SUM(A1)", "-2+3", "\tx", "\rx"):
            with self.subTest(value=risky):
                self.assertTrue(csv_safe(risky).startswith("'"))
        for fine in ("-5.00", "12.50", "Jane", "", 7):
            with self.subTest(value=fine):
                self.assertEqual(csv_safe(fine), fine)
