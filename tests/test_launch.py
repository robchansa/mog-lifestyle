"""Launch-readiness: spam protection, analytics, metadata and accessibility.

These encode the pre-launch checklist as tests so the site cannot quietly
regress out of compliance between now and go-live.
"""
from __future__ import annotations

import time
import unittest

from app import analytics, db, security
from app.security import HONEYPOT_FIELD, TIMESTAMP_FIELD, spam_signals
from tests.support import StoreTestCase


class SpamProtectionTests(StoreTestCase):
    def test_a_filled_honeypot_is_rejected(self):
        self.assertEqual(
            spam_signals("http://buy-watches.example", security.form_timestamp()),
            "honeypot")

    def test_an_instant_submission_is_rejected(self):
        original = security.MIN_SECONDS_TO_FILL
        security.MIN_SECONDS_TO_FILL = 2.0
        try:
            self.assertEqual(spam_signals("", security.form_timestamp()), "too-fast")
        finally:
            security.MIN_SECONDS_TO_FILL = original

    def test_a_human_pace_is_accepted(self):
        stamp = security.form_timestamp()
        self.assertEqual(spam_signals("", stamp, now=time.time() + 5), "")

    def test_a_forged_timestamp_is_rejected(self):
        self.assertEqual(
            spam_signals("", "1700000000.not-a-real-signature", now=time.time() + 5),
            "missing-timestamp")

    def test_a_missing_timestamp_is_rejected(self):
        self.assertEqual(spam_signals("", "", now=time.time() + 5),
                         "missing-timestamp")

    def test_a_stale_form_is_rejected(self):
        stamp = security.form_timestamp()
        self.assertEqual(
            spam_signals("", stamp, now=time.time() + security.MAX_FORM_AGE_SECONDS + 10),
            "stale-form")

    def test_the_trap_is_hidden_from_sight_and_from_screen_readers(self):
        from app.ui import spam_trap
        markup = spam_trap()
        self.assertIn('aria-hidden="true"', markup)
        self.assertIn('tabindex="-1"', markup)
        self.assertIn('autocomplete="off"', markup)
        self.assertIn(f'name="{HONEYPOT_FIELD}"', markup)
        self.assertIn(f'name="{TIMESTAMP_FIELD}"', markup)
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent
               / "app" / "static" / "css" / "site.css").read_text()
        self.assertIn(".spam-trap {", css)
        self.assertIn("left: -9999px !important", css)

    def test_every_public_form_carries_the_trap(self):
        from app.application import create_app
        from app import accounts, cart as cart_module
        app = create_app()
        for path in ("/contact", "/register", "/"):
            with self.subTest(path=path):
                request = self.make_request(path=path)
                request.session = accounts.create_session(None)
                request.user = None
                request.cart_id = cart_module.ensure_cart(None)
                request.cart_count = 0
                route, params = app.match("GET", path)
                markup = route.handler(request, **params).body.decode()
                self.assertIn(f'name="{HONEYPOT_FIELD}"', markup)
                self.assertIn(f'name="{TIMESTAMP_FIELD}"', markup)


class HttpsTests(StoreTestCase):
    """The redirect only matters in production, so it is tested explicitly."""

    def setUp(self) -> None:
        super().setUp()
        from app.config import config
        self._original = config.force_https
        config.force_https = True
        self.addCleanup(setattr, config, "force_https", self._original)

    def _request(self, proto: str | None = None):
        request = self.make_request(path="/")
        request.headers["Host"] = "moglifestyle.fit"
        if proto is not None:
            request.headers["X-Forwarded-Proto"] = proto
        return request

    def test_plain_http_is_redirected(self):
        """With no proxy header the connection really is HTTP."""
        from app.application import create_app
        response = create_app().dispatch(self._request())
        self.assertEqual(response.status, 301)
        self.assertTrue(response.headers["Location"].startswith("https://"))

    def test_a_proxy_reporting_http_is_redirected(self):
        from app.application import create_app
        response = create_app().dispatch(self._request("http"))
        self.assertEqual(response.status, 301)

    def test_a_proxy_reporting_https_is_served(self):
        from app.application import create_app
        self.assertEqual(create_app().dispatch(self._request("https")).status, 200)

    def test_only_the_first_value_of_a_forwarded_chain_is_trusted(self):
        request = self._request("https, http")
        self.assertEqual(request.scheme, "https")
        request = self._request("http, https")
        self.assertEqual(request.scheme, "http")

    def test_hsts_is_sent_only_when_https_is_forced(self):
        from app import web
        from app.config import config
        self.assertIn("Strict-Transport-Security", web._security_headers())
        config.force_https = False
        self.assertNotIn("Strict-Transport-Security", web._security_headers())

    def test_cookies_are_marked_secure_in_production(self):
        from app.config import config
        from app.web import Response
        original = config.secure_cookies
        config.secure_cookies = True
        try:
            response = Response().set_cookie("x", "y", max_age=60)
            self.assertIn("Secure", response.cookies[0])
            self.assertIn("HttpOnly", response.cookies[0])
            self.assertIn("SameSite=Lax", response.cookies[0])
        finally:
            config.secure_cookies = original


class AnalyticsTests(StoreTestCase):
    def test_a_page_view_is_recorded(self):
        analytics.record("/shop", ip="203.0.113.1", user_agent="Mozilla/5.0")
        row = db.one("SELECT * FROM page_views")
        self.assertEqual(row["path"], "/shop")
        self.assertEqual(row["kind"], "shop")

    def test_crawlers_are_not_counted(self):
        for agent in ("Googlebot/2.1", "curl/8.1", "python-urllib/3.9",
                      "HeadlessChrome/120"):
            analytics.record("/", ip="203.0.113.2", user_agent=agent)
        self.assertEqual(db.scalar("SELECT count(*) FROM page_views", (), 0), 0)

    def test_static_and_admin_paths_are_not_counted(self):
        for path in ("/static/css/site.css", "/media/tee-0.svg", "/admin/orders",
                     "/healthz", "/webhooks/stripe"):
            analytics.record(path, ip="203.0.113.3", user_agent="Mozilla/5.0")
        self.assertEqual(db.scalar("SELECT count(*) FROM page_views", (), 0), 0)

    def test_no_raw_address_is_stored(self):
        analytics.record("/", ip="198.51.100.77", user_agent="Mozilla/5.0")
        row = db.one("SELECT * FROM page_views")
        self.assertNotIn("198.51.100.77", " ".join(str(v) for v in tuple(row)))
        self.assertEqual(len(row["visitor"]), 20)

    def test_the_same_visitor_hashes_consistently_within_a_day(self):
        first = analytics.visitor_hash("203.0.113.9", "Mozilla/5.0")
        second = analytics.visitor_hash("203.0.113.9", "Mozilla/5.0")
        other = analytics.visitor_hash("203.0.113.10", "Mozilla/5.0")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_the_salt_rotates_so_visitors_cannot_be_followed(self):
        before = analytics.visitor_hash("203.0.113.9", "Mozilla/5.0")
        analytics._salt_day = None          # simulate the next day
        after = analytics.visitor_hash("203.0.113.9", "Mozilla/5.0")
        self.assertNotEqual(before, after,
                            "a visitor must not be linkable across days")

    def test_only_a_referrer_host_is_kept_not_the_full_url(self):
        analytics.record("/", ip="203.0.113.4", user_agent="Mozilla/5.0",
                         referrer="https://www.google.com/search?q=private+query")
        self.assertEqual(db.scalar("SELECT referrer FROM page_views"),
                         "www.google.com")

    def test_device_classification(self):
        self.assertEqual(analytics.device_class("iPhone Mobile"), "mobile")
        self.assertEqual(analytics.device_class("iPad"), "tablet")
        self.assertEqual(analytics.device_class("Macintosh Safari"), "desktop")

    def test_the_funnel_reports_the_metric_the_brief_asked_for(self):
        from app import reports
        for index in range(5):
            ip = f"203.0.113.{100 + index}"
            analytics.record("/", ip=ip, user_agent="Mozilla/5.0")
            if index < 3:
                analytics.record("/product/x", ip=ip, user_agent="Mozilla/5.0")
            if index < 2:
                analytics.record("/cart", ip=ip, user_agent="Mozilla/5.0")
        window = reports.Window(reports.resolve({"range": "7d"}))
        labels = dict(reports.funnel(window, orders=1))
        self.assertEqual(labels["Visited"], 5)
        self.assertEqual(labels["Viewed a product"], 3)
        self.assertEqual(labels["Opened the bag"], 2)
        self.assertEqual(labels["Ordered"], 1)

    def test_each_visitor_day_is_one_visit_row(self):
        for path in ("/", "/shop", "/product/x", "/cart"):
            analytics.record(path, ip="203.0.113.50", user_agent="Mozilla/5.0",
                             referrer="https://l.instagram.com/?u=x")
        visit = db.one("SELECT * FROM visits")
        self.assertEqual(db.scalar("SELECT count(*) FROM visits"), 1)
        self.assertEqual(visit["views"], 4)
        self.assertEqual(visit["referrer"], "l.instagram.com")
        self.assertEqual(visit["entry_path"], "/")
        self.assertEqual((visit["saw_product"], visit["saw_cart"], visit["saw_checkout"]),
                         (1, 1, 0))

    def test_clicks_within_the_store_are_not_counted_as_referrals(self):
        analytics.record("/shop", ip="203.0.113.51", user_agent="Mozilla/5.0",
                         referrer="https://moglifestyle.fit/", host="moglifestyle.fit")
        self.assertEqual(db.scalar("SELECT referrer FROM page_views"), "")
        self.assertEqual(db.scalar("SELECT referrer FROM visits"), "")

    def test_visits_are_rebuilt_from_page_views_for_older_databases(self):
        for path in ("/", "/product/x"):
            analytics.record(path, ip="203.0.113.52", user_agent="Mozilla/5.0")
        with db.tx():
            db.execute("DELETE FROM visits")
        self.assertEqual(db.rebuild_visits(), 1)
        visit = db.one("SELECT * FROM visits")
        self.assertEqual((visit["views"], visit["saw_product"]), (2, 1))

    def test_old_rows_are_pruned(self):
        analytics.record("/", ip="203.0.113.5", user_agent="Mozilla/5.0")
        with db.tx():
            db.execute("UPDATE page_views SET created_at = datetime('now', '-500 days')")
        self.assertEqual(analytics.prune(400), 1)
        self.assertEqual(db.scalar("SELECT count(*) FROM page_views", (), 0), 0)
        # The visit row goes with it once it ages out too.
        with db.tx():
            db.execute("UPDATE visits SET started_at = datetime('now', '-500 days')")
        analytics.prune(400)
        self.assertEqual(db.scalar("SELECT count(*) FROM visits", (), 0), 0)

    def test_recording_never_raises(self):
        analytics.record("/", ip="", user_agent="")           # no UA at all
        analytics.record("x" * 5000, ip="203.0.113.6", user_agent="Mozilla/5.0")


class CookiePolicyTests(StoreTestCase):
    """A consent banner is only required once a non-essential cookie exists.

    The store sets two signed cookies -- a session and a cart -- both strictly
    necessary to buy anything, which are exempt from consent under GDPR/ePrivacy.
    These tests fail the moment that stops being true.
    """

    @staticmethod
    def _cookie_names() -> set[str]:
        """Every literal passed as the first argument to a set_cookie call."""
        import ast
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "app"
        names: set[str] = set()
        for source in root.rglob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "attr", getattr(func, "id", ""))
                if name not in ("set_cookie", "delete_cookie") or not node.args:
                    continue
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    names.add(str(first.value))
                elif isinstance(first, ast.Attribute):
                    names.add(first.attr)
                elif isinstance(first, ast.Name):
                    names.add(first.id)
        return names

    def test_the_site_sets_only_strictly_necessary_cookies(self):
        names = self._cookie_names()
        self.assertTrue(names, "the AST scan should find some set_cookie calls")
        allowed = {"SESSION_COOKIE", "CART_COOKIE", "flash", "name"}
        unexpected = names - allowed
        self.assertFalse(
            unexpected,
            f"{unexpected} is not strictly necessary and would require consent")

    def test_the_two_real_cookies_are_the_session_and_the_cart(self):
        from app.accounts import CART_COOKIE, SESSION_COOKIE
        self.assertEqual(SESSION_COOKIE, "mog_session")
        self.assertEqual(CART_COOKIE, "mog_cart")

    def test_analytics_sets_no_cookie_and_reads_none(self):
        """Measured server-side, so there is nothing to consent to."""
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent
                  / "app" / "analytics.py")
        tree = ast.parse(source.read_text())
        calls = {
            getattr(node.func, "attr", getattr(node.func, "id", ""))
            for node in ast.walk(tree) if isinstance(node, ast.Call)
        }
        self.assertNotIn("set_cookie", calls)
        self.assertNotIn("delete_cookie", calls)
        # And nothing client-side either.
        js = (Path(__file__).resolve().parent.parent
              / "app" / "static" / "js" / "site.js").read_text()
        tracker_cookies = [line for line in js.splitlines()
                           if "document.cookie" in line and "flash" not in line]
        self.assertEqual(tracker_cookies, [],
                         "the only cookie JavaScript touches is the flash message")

    def test_no_third_party_script_or_pixel(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "app"
        offenders = []
        for source in list(root.rglob("*.py")) + list(root.rglob("*.js")):
            text = source.read_text()
            for tracker in ("googletagmanager", "google-analytics", "gtag(",
                            "connect.facebook", "hotjar", "segment.com"):
                if tracker in text:
                    offenders.append(f"{source.name}: {tracker}")
        self.assertEqual(offenders, [],
                         "a third-party tracker would require a consent banner")

    def test_the_privacy_page_describes_the_cookies_in_use(self):
        from app.views_shop import _POLICY_PAGES
        text = " ".join(body for _, body in _POLICY_PAGES["privacy"][2]).lower()
        self.assertIn("cookie", text)
        self.assertIn("session", text)


if __name__ == "__main__":
    unittest.main()
