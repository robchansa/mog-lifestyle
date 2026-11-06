"""Pre-launch: the countdown setting, the gate that hides the site, the
countdown page, Notify Me, and the admin subscriber export.

The gate and forms are exercised over real HTTP (cookies, CSRF, spam trap).
Notify Me runs in demo mode -- proving nothing is stored -- and in live mode,
proving it reuses the store's own newsletter list.
"""
from __future__ import annotations

import csv
import io
import re
import unittest
from datetime import datetime, timezone
from unittest import mock

from app import db, preorder
from app.config import PREORDER_OPENS_AT, Config, config
from tests.support import StoreTestCase
from tests.test_integration import Client, LiveServerTestCase

UTC = timezone.utc
FUTURE = "2099-01-01 10:00 America/Boise"
PAST = "2000-01-01 00:00 UTC"


def hidden(value: str = FUTURE):
    """Run with the site still hidden behind the countdown."""
    return mock.patch.object(config, "preorder_opens_at", value)


# ----------------------------------------------------------------- setting

class SettingTests(unittest.TestCase):

    def test_one_setting_holds_date_time_and_zone(self):
        when = preorder.parse_opening("2026-12-01 10:00 America/Boise")
        self.assertEqual((when.year, when.month, when.day, when.hour, when.minute),
                         (2026, 12, 1, 10, 0))
        self.assertEqual(when.astimezone(UTC), datetime(2026, 12, 1, 17, 0, tzinfo=UTC))

    def test_a_t_separator_and_utc_are_accepted(self):
        self.assertEqual(preorder.parse_opening("2027-01-15T09:30 UTC"),
                         datetime(2027, 1, 15, 9, 30, tzinfo=UTC))

    def test_malformed_values_are_rejected_rather_than_guessed(self):
        for bad in ("", "2026-12-01", "2026-12-01 10:00", "2026-12-01 10:00 Mars/Base",
                    "2026-02-30 10:00 UTC", "2026-12-01 25:00 UTC", "12/01/2026 10am MST",
                    "2026-12-01 10:00 America/Boise; drop"):
            with self.subTest(value=bad):
                self.assertIsNone(preorder.parse_opening(bad))

    def test_the_confirmed_launch_is_20_november_10am_mountain(self):
        when = preorder.parse_opening(PREORDER_OPENS_AT)
        self.assertEqual(when.astimezone(UTC), datetime(2026, 11, 20, 17, 0, tzinfo=UTC))
        self.assertEqual(when.tzname(), "MST")

    def test_another_zone_can_be_named(self):
        with hidden("2027-03-01 09:00 America/New_York"):
            self.assertEqual(preorder.opening().tzname(), "EST")

    def test_forms_are_demo_locally_and_live_in_production(self):
        self.assertEqual(config.preorder_forms, "demo")
        self.assertFalse(preorder.forms_live())
        self.assertEqual(Config(env="production").preorder_forms, "live")
        self.assertEqual(Config(env="production", preorder_forms="demo").preorder_forms, "demo")
        self.assertEqual(Config(preorder_forms="nonsense").preorder_forms, "demo")


class DestinationTests(unittest.TestCase):

    def check(self, value: str) -> str:
        with mock.patch.object(config, "preorder_url", value):
            return preorder.destination()

    def test_no_destination_by_default(self):
        self.assertEqual(self.check(""), "")

    def test_site_paths_and_https_urls_are_accepted(self):
        self.assertEqual(self.check("/shop?collection=preorder"), "/shop?collection=preorder")
        self.assertEqual(self.check("https://moglifestyle.fit/preorder"),
                         "https://moglifestyle.fit/preorder")

    def test_unsafe_or_broken_values_are_refused(self):
        for bad in ("//evil.example", "javascript:alert(1)", "http://moglifestyle.fit",
                    "shop", "/sh op", "https://", "https://localhost", "/\\evil"):
            with self.subTest(value=bad):
                self.assertEqual(self.check(bad), "")


# ------------------------------------------------- no time-zone database

class FallbackZoneTests(unittest.TestCase):
    """A server without a time-zone database must still open on time."""

    def no_tz_database(self):
        return mock.patch("app.preorder._load_zone", return_value=None)

    def test_the_launch_still_resolves_to_the_same_moment(self):
        with self.no_tz_database():
            when = preorder.parse_opening(PREORDER_OPENS_AT)
            self.assertEqual(preorder.zone_source("America/Boise"),
                             "built-in US rules (no time-zone database on this server)")
        self.assertEqual(when.astimezone(UTC), datetime(2026, 11, 20, 17, 0, tzinfo=UTC))
        self.assertEqual(when.tzname(), "MST")

    def test_the_site_still_opens_at_ten_mountain(self):
        with self.no_tz_database(), hidden(PREORDER_OPENS_AT):
            before = preorder.countdown(now=datetime(2026, 11, 20, 16, 59, 59, tzinfo=UTC))
            after = preorder.countdown(now=datetime(2026, 11, 20, 17, 0, tzinfo=UTC))
        self.assertFalse(before.is_open)
        self.assertTrue(after.is_open)

    def test_daylight_saving_switches_on_the_right_days(self):
        cases = {                     # 2026: DST from 8 March to 1 November, at 2am
            "2026-03-08 01:30 America/Boise": "MST",
            "2026-03-08 03:00 America/Boise": "MDT",
            "2026-11-01 01:30 America/Boise": "MDT",
            "2026-11-01 03:00 America/Boise": "MST",
            "2026-07-04 10:00 America/Phoenix": "MST",
            "2026-07-04 10:00 America/New_York": "EDT",
        }
        with self.no_tz_database():
            for value, label in cases.items():
                with self.subTest(value=value):
                    self.assertEqual(preorder.parse_opening(value).tzname(), label)

    def test_built_in_rules_match_the_real_database_every_day(self):
        from datetime import date, timedelta
        from zoneinfo import ZoneInfo
        day = date(2026, 1, 1)
        while day < date(2029, 1, 1):
            for name in preorder._FALLBACK_ZONES:
                for hour in (0, 10, 23):
                    local = datetime(day.year, day.month, day.day, hour)
                    real = local.replace(tzinfo=ZoneInfo(name)).utcoffset()
                    ours = preorder._fallback_zone(name, local).utcoffset(None)
                    self.assertEqual(ours, real, f"{name} {local}")
            day += timedelta(days=1)

    def test_an_unknown_zone_is_still_refused(self):
        with self.no_tz_database():
            self.assertIsNone(preorder.parse_opening("2026-11-20 10:00 Europe/Paris"))
            self.assertEqual(preorder.zone_source("Europe/Paris"), "unknown")


# --------------------------------------------------------------- countdown

class CountdownTests(unittest.TestCase):
    OPENS = "2026-12-01 10:00 America/Boise"          # 17:00 UTC

    def at(self, now: datetime, **kw) -> preorder.Countdown:
        with hidden(self.OPENS):
            return preorder.countdown(now=now, **kw)

    def test_days_hours_minutes_seconds(self):
        cd = self.at(datetime(2026, 11, 28, 13, 58, 55, tzinfo=UTC))
        self.assertEqual(cd.parts(), (3, 3, 1, 5))
        self.assertFalse(cd.is_open)

    def test_the_last_partial_second_still_counts(self):
        cd = self.at(datetime(2026, 12, 1, 16, 59, 59, 500000, tzinfo=UTC))
        self.assertEqual(cd.parts(), (0, 0, 0, 1))
        self.assertFalse(cd.is_open)

    def test_it_opens_exactly_on_time(self):
        self.assertTrue(self.at(datetime(2026, 12, 1, 17, 0, tzinfo=UTC)).is_open)
        later = self.at(datetime(2026, 12, 9, tzinfo=UTC))
        self.assertTrue(later.is_open)
        self.assertEqual(later.parts(), (0, 0, 0, 0))

    def test_labels_name_the_store_zone(self):
        cd = self.at(datetime(2026, 11, 1, tzinfo=UTC))
        self.assertEqual(cd.opens_label(), "Tuesday, December 1, 2026 at 10:00 AM MST")
        self.assertEqual(cd.short_label(), "December 1")
        self.assertEqual(cd.opens_at_utc_iso(), "2026-12-01T17:00:00Z")
        self.assertEqual(cd.zone_offset_minutes(), -420)

    def test_daylight_saving_is_respected(self):
        with hidden("2027-07-01 10:00 America/Boise"):
            cd = preorder.countdown(now=datetime(2027, 6, 1, tzinfo=UTC))
        self.assertEqual(cd.opens_at_utc_iso(), "2027-07-01T16:00:00Z")
        self.assertIn("MDT", cd.opens_label())
        self.assertEqual(cd.zone_offset_minutes(), -360)

    def test_reviewers_can_simulate_the_end_locally(self):
        now = datetime(2026, 11, 5, 12, tzinfo=UTC)
        soon = self.at(now, simulate="15")
        self.assertTrue(soon.simulated)
        self.assertEqual(soon.remaining_seconds, 15)
        self.assertTrue(self.at(now, simulate="open").is_open)
        for junk in ("-5", "abc", "0", str(10 ** 9)):
            with self.subTest(simulate=junk):
                self.assertFalse(self.at(now, simulate=junk).simulated)

    def test_simulation_is_ignored_in_production(self):
        now = datetime(2026, 11, 5, 12, tzinfo=UTC)
        with mock.patch.object(config, "env", "production"):
            cd = self.at(now, simulate="open")
        self.assertFalse(cd.simulated)
        self.assertFalse(cd.is_open)


class LaunchStatusTests(unittest.TestCase):

    def test_the_confirmed_launch_as_the_server_reports_it(self):
        with hidden(PREORDER_OPENS_AT):
            status = preorder.launch_status(now=datetime(2026, 11, 6, 17, 0, tzinfo=UTC))
        self.assertEqual(status["opens_local"], "Friday, November 20, 2026 at 10:00 AM MST")
        self.assertEqual(status["opens_at_utc"], "2026-11-20T17:00:00Z")
        self.assertEqual(status["seconds_left"], 14 * 86400)
        self.assertFalse(status["site_open"])

    def test_the_deploy_fails_if_the_live_server_cannot_read_it(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parent.parent
                    / ".github" / "workflows" / "deploy.yml").read_text()
        self.assertIn('json.load(sys.stdin).get("launch")', workflow)
        self.assertIn('if not launch.get("readable")', workflow)


class ValidationTests(unittest.TestCase):

    def test_email(self):
        self.assertEqual(preorder.notify_errors(""), {"email": preorder.EMAIL_REQUIRED})
        self.assertEqual(preorder.notify_errors("rob@mog"), {"email": preorder.EMAIL_INVALID})
        self.assertEqual(preorder.notify_errors("rob@moglifestyle.fit"), {})


# -------------------------------------------------------- countdown page

class CountdownPageMarkupTests(StoreTestCase):

    def render(self, cd: preorder.Countdown) -> str:
        from app import accounts
        from app.views_preorder import countdown_page
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        return countdown_page(request, cd).body.decode()

    def counting(self, opens: str = PREORDER_OPENS_AT) -> preorder.Countdown:
        with hidden(opens):
            return preorder.countdown(now=datetime(2026, 11, 5, 12, tzinfo=UTC))

    def test_the_page_is_the_countdown_and_notify_me_only(self):
        html = self.render(self.counting())
        self.assertIn("Preorders Open In", html)
        for label in ("Days", "Hours", "Minutes", "Seconds"):
            self.assertIn(f'<span class="countdown__label">{label}</span>', html)
        self.assertIn('role="timer"', html)
        self.assertIn('data-opens-at="2026-11-20T17:00:00Z"', html)
        self.assertIn('data-reveal-url="/"', html)
        self.assertIn("Friday, November 20, 2026 at 10:00 AM MST", html)
        # The date is confirmed: nothing on the page calls it provisional.
        for provisional in ("Preview date", "not final", "Placeholder", "preorder__flag"):
            self.assertNotIn(provisional, html)
        self.assertIn('action="/preorder/notify"', html)
        self.assertIn(">Notify Me</button>", html)
        self.assertIn("/static/js/preorder.js", html)
        self.assertNotIn("Preview mode", html)      # no demo box on the page
        # Nothing of the store leaks onto it: no navigation, products or account form.
        for absent in ('class="site-header"', "/shop?department", 'class="card"',
                       'name="password"', 'href="/cart"'):
            self.assertNotIn(absent, html)

    def test_one_heading_labelled_fields_and_unique_ids(self):
        html = self.render(self.counting())
        self.assertEqual(len(re.findall(r"<h1\b", html)), 1)
        self.assertIn('<label for="notify-email">', html)
        ids = re.findall(r'\sid="([^"]+)"', html)
        self.assertFalse({i for i in ids if ids.count(i) > 1})

    def test_a_moved_date_is_shown_as_set(self):
        html = self.render(self.counting("2026-12-04 09:00 America/Boise"))
        self.assertIn("Friday, December 4, 2026 at 9:00 AM MST", html)

    def test_a_configured_preorder_page_is_where_the_reveal_lands(self):
        with mock.patch.object(config, "preorder_url", "/shop?collection=preorder"):
            html = self.render(self.counting())
        self.assertIn('data-reveal-url="/shop?collection=preorder"', html)
        self.assertIn(">Shop the preorder</a>", html)

    def test_an_unreadable_setting_says_so(self):
        with hidden("first week of December"):
            cd = preorder.countdown()
        html = self.render(cd)
        self.assertIn("Preorders Open Soon", html)
        self.assertNotIn("data-opens-at", html)
        self.assertIn("Setting error", html)


# ------------------------------------------------------------ the gate

class GateTests(LiveServerTestCase):

    def staff(self) -> Client:
        client = self.client()
        client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        return client

    def test_the_public_sees_only_the_countdown(self):
        with hidden():
            client = self.client()
            home = client.get("/")
            self.assertEqual(client.last_status, 200)
            self.assertIn("Preorders Open In", home)
            self.assertNotIn("Built for", home)
            self.assertEqual(client.last_headers["Cache-Control"], "no-store")
            for path in ("/shop", "/product/anything", "/about", "/cart", "/register",
                         "/account", "/contact", "/sitemap.xml"):
                with self.subTest(path=path):
                    client.get(path, follow=False)
                    self.assertEqual(client.last_status, 302)
                    self.assertEqual(client.last_headers["Location"], "/")

    def test_forms_elsewhere_are_turned_back(self):
        with hidden():
            client = self.client()
            fields = client.form_fields("/")
            client.post("/contact", {**fields, "name": "A", "email": "a@example.com",
                                     "message": "hello there friends"}, follow=False)
            self.assertEqual(client.last_status, 303)
            self.assertEqual(client.last_headers["Location"], "/")
        self.assertIsNone(db.one("SELECT 1 FROM messages WHERE email = 'a@example.com'"))

    def test_what_stays_reachable(self):
        with hidden():
            client = self.client()
            for path in ("/privacy", "/login", "/robots.txt", "/healthz",
                         "/static/css/site.css"):
                with self.subTest(path=path):
                    client.get(path, follow=False)
                    self.assertEqual(client.last_status, 200, path)
            client.get("/admin", follow=False)               # still asks staff to sign in
            self.assertIn("/login", client.last_headers["Location"])
            client.request("POST", "/webhooks/stripe", body="{}",
                           headers={"Content-Type": "application/json"}, follow=False)
            self.assertNotEqual(client.last_headers.get("Location"), "/")
            client.get("/newsletter/unsubscribe?token=bad", follow=False)
            self.assertIn("flash=", client.last_headers.get("Set-Cookie", ""))

    def test_staff_browse_the_whole_site_with_a_reminder(self):
        with hidden():
            client = self.staff()
            home = client.get("/")
            self.assertIn("Built for", home)
            self.assertIn('class="launch-bar"', home)
            self.assertIn("Staff preview", home)
            self.assertIn("Staff preview", client.get("/shop"))
            self.assertEqual(client.last_status, 200)
            self.assertIn("Preorders Open In", client.get("/?view=countdown"))

    def test_the_site_opens_by_itself_at_the_set_time(self):
        with hidden(PAST):
            client = self.client()
            home = client.get("/")
            self.assertIn("Built for", home)
            self.assertNotIn("launch-bar", home)
            client.get("/shop")
            self.assertEqual(client.last_status, 200)

    def test_an_unreadable_setting_keeps_the_site_hidden(self):
        with hidden("sometime soon"):
            client = self.client()
            self.assertIn("Preorders Open Soon", client.get("/"))
            client.get("/shop", follow=False)
            self.assertEqual(client.last_status, 302)

    def test_a_local_reviewer_can_watch_the_reveal(self):
        with hidden():
            client = self.client()
            page = client.get("/?preview_countdown=15")
            self.assertIn("Simulated for review", page)
            self.assertIn('data-reveal-url="/?preview_countdown=open"', page)
            self.assertIn("Built for", client.get("/?preview_countdown=open"))

    def test_healthz_reports_how_the_server_reads_the_launch(self):
        import json
        with hidden():
            client = self.client()
            launch = json.loads(client.get("/healthz"))["launch"]
        self.assertEqual(client.last_status, 200)            # reachable while hidden
        self.assertTrue(launch["readable"])
        self.assertFalse(launch["site_open"])
        self.assertEqual(launch["opens_at_utc"], "2099-01-01T17:00:00Z")
        self.assertIn("America/Boise via", launch["time_zone"])
        with hidden(PAST):
            self.assertTrue(json.loads(self.client().get("/healthz"))["launch"]["site_open"])
        with hidden("Nov 20th"):
            broken = json.loads(self.client().get("/healthz"))["launch"]
        self.assertFalse(broken["readable"])
        self.assertIn("must look like", broken["error"])

    def test_production_ignores_the_review_override(self):
        with hidden(), mock.patch.object(config, "env", "production"):
            client = self.client()
            self.assertIn("Preorders Open In", client.get("/?preview_countdown=open"))


# ------------------------------------------------------------ Notify Me

class NotifyTests(LiveServerTestCase):

    def count(self, sql: str) -> int:
        return db.scalar(sql, (), 0)

    def test_demo_confirms_and_stores_nothing(self):
        with hidden():
            client = self.client()
            before = self.count("SELECT count(*) FROM audit_log")
            result = client.post_json("/preorder/notify",
                                      {**client.form_fields("/"), "email": "demo@example.com"})
        self.assertTrue(result["ok"])
        self.assertIn("demo@example.com", result["message"])
        self.assertIn("Nothing was saved or sent", result["note"])
        self.assertEqual(self.count("SELECT count(*) FROM newsletter "
                                    "WHERE email = 'demo@example.com'"), 0)
        self.assertEqual(self.count("SELECT count(*) FROM audit_log"), before)

    def test_a_bad_email_is_explained(self):
        with hidden():
            client = self.client()
            result = client.post_json("/preorder/notify",
                                      {**client.form_fields("/"), "email": "demo@"})
        self.assertEqual(client.last_status, 422)
        self.assertEqual(result["errors"], {"email": preorder.EMAIL_INVALID})

    def test_without_javascript(self):
        with hidden():
            client = self.client()
            page = client.post("/preorder/notify",
                               {**client.form_fields("/"), "email": "nope"}, follow=False)
            self.assertEqual(client.last_status, 200)
            self.assertIn("Preorders Open In", page)
            self.assertIn(preorder.EMAIL_INVALID, page)
            self.assertRegex(page, r'id="notify-email"[^>]*value="nope"')
            self.assertRegex(page, r'aria-invalid="true"[^>]*autofocus')
            client.post("/preorder/notify",
                        {**client.form_fields("/"), "email": "ok@example.com"}, follow=False)
            self.assertEqual(client.last_status, 303)
            self.assertEqual(client.last_headers["Location"], "/#notify")

    def test_csrf_protected(self):
        with hidden():
            client = self.client()
            client.get("/")
            client.request("POST", "/preorder/notify", body="email=a%40example.com",
                           headers={"Accept": "application/json"}, follow=False)
        self.assertEqual(client.last_status, 403)

    def test_a_bot_gets_a_normal_answer_and_nothing_is_kept(self):
        with hidden():
            client = self.client()
            result = client.post_json("/preorder/notify", {
                **client.form_fields("/"), "email": "bot@example.com",
                "company_website": "http://spam.example"})
        self.assertTrue(result["ok"])
        self.assertEqual(self.count("SELECT count(*) FROM newsletter "
                                    "WHERE email = 'bot@example.com'"), 0)

    def test_live_mode_joins_the_existing_list(self):
        with hidden(), mock.patch.object(config, "preorder_forms", "live"):
            client = self.client()
            result = client.post_json("/preorder/notify",
                                      {**client.form_fields("/"), "email": "live@example.com"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["note"], "")
        row = db.one("SELECT source, consent_at, unsubscribed_at FROM newsletter "
                     "WHERE email = 'live@example.com'")
        self.assertEqual(row["source"], "preorder")
        self.assertIsNotNone(row["consent_at"])
        self.assertIsNone(row["unsubscribed_at"])

    def test_the_footer_list_still_works_once_open(self):
        client = self.client()
        result = client.post_json("/newsletter", {**client.form_fields("/"),
                                                  "email": "footer@example.com"})
        self.assertTrue(result["ok"])
        self.assertEqual(db.one("SELECT source FROM newsletter "
                                "WHERE email = 'footer@example.com'")["source"], "footer")


# ------------------------------------------------------- admin export

class SubscriberExportTests(LiveServerTestCase):

    def setUp(self) -> None:
        from app.accounts import subscribe_newsletter
        with db.tx():
            db.execute("DELETE FROM newsletter")
        subscribe_newsletter("first@example.com", source="preorder")
        subscribe_newsletter("second@example.com", source="footer")
        subscribe_newsletter("=HYPERLINK(1)@example.com", source="preorder")
        subscribe_newsletter("gone@example.com", source="preorder")
        with db.tx():
            db.execute("UPDATE newsletter SET unsubscribed_at = datetime('now') "
                       "WHERE email = 'gone@example.com'")

    def staff(self) -> Client:
        client = self.client()
        client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        return client

    def rows(self, body: str) -> list[list[str]]:
        return list(csv.reader(io.StringIO(body)))

    def test_only_staff_can_export(self):
        anonymous = self.client()
        anonymous.get("/admin/subscribers/export", follow=False)
        self.assertEqual(anonymous.last_status, 303)          # sent to sign in
        self.assertIn("/login", anonymous.last_headers["Location"])

        shopper = self.client()
        shopper.post("/register", {
            **shopper.form_fields("/register"), "name": "Shopper",
            "email": "customer-only@example.com", "password": "a long enough phrase"})
        body = shopper.get("/admin/subscribers/export")
        self.assertEqual(shopper.last_status, 403)
        self.assertNotIn("first@example.com", body)

    def test_staff_can_export_while_the_site_is_hidden(self):
        with hidden():
            client = self.staff()
            body = client.get("/admin/subscribers/export?source=preorder")
        self.assertEqual(client.last_status, 200)
        self.assertIn("first@example.com", body)

    def test_export_all_active_subscribers(self):
        client = self.staff()
        body = client.get("/admin/subscribers/export")
        self.assertIn("text/csv", client.last_headers["Content-Type"])
        self.assertIn('filename="mog-subscribers.csv"',
                      client.last_headers["Content-Disposition"])
        rows = self.rows(body)
        self.assertEqual(rows[0], ["email", "source", "signed_up_via",
                                   "first_joined_at", "consent_at"])
        emails = [r[0] for r in rows[1:]]
        self.assertIn("first@example.com", emails)
        self.assertIn("second@example.com", emails)
        self.assertNotIn("gone@example.com", emails)
        # A formula-looking address is defused for spreadsheets.
        self.assertIn("'=HYPERLINK(1)@example.com", emails)

    def test_export_just_the_preorder_list(self):
        client = self.staff()
        rows = self.rows(client.get("/admin/subscribers/export?source=preorder"))
        self.assertIn('filename="mog-subscribers-preorder.csv"',
                      client.last_headers["Content-Disposition"])
        self.assertEqual({r[1] for r in rows[1:]}, {"preorder"})
        self.assertEqual(len(rows) - 1, 2)
        self.assertGreaterEqual(db.scalar(
            "SELECT count(*) FROM audit_log WHERE action = 'subscribers.export' "
            "AND detail LIKE '%source=preorder%'", (), 0), 1)

    def test_customers_page_shows_the_list_and_links(self):
        body = self.staff().get("/admin/customers")
        self.assertIn("Mailing list — 3 active subscriber(s)", body)
        self.assertIn('href="/admin/subscribers/export?source=preorder"', body)
        self.assertIn("Export all subscribers (CSV)", body)
        self.assertIn("1 unsubscribed address(es) are left out", body)


if __name__ == "__main__":
    unittest.main()
