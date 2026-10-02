"""Traceability: the running store must still satisfy the signed intake form.

`tools/extract_intake.py` reads the client's answers straight out of `mog.pdf`,
so these tests fail if the code drifts away from what was actually agreed --
or if someone edits the requirements doc without re-running the extractor.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from app import catalog, db, seed
from app.config import config
from tests.support import StoreTestCase

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "mog.pdf"


def load_brief() -> dict:
    import sys
    sys.path.insert(0, str(ROOT))
    from tools.extract_intake import IntakeForm
    return IntakeForm(PDF).to_dict()


class ExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not PDF.exists():
            raise unittest.SkipTest("mog.pdf is not present")
        cls.brief = load_brief()
        cls.fields = {
            field["id"]: field
            for section in cls.brief["sections"] for field in section["fields"]
        }

    def test_the_form_shape_is_what_we_parsed(self):
        self.assertEqual(len(self.fields), 104)
        self.assertEqual(len(self.brief["sections"]), 13)

    def test_client_identity(self):
        self.assertEqual(self.fields["s2_full_name"]["value"], "Robert Chansa")
        self.assertEqual(self.fields["s2_company"]["value"], "moglifestyle")
        self.assertEqual(self.fields["s2_email"]["value"], "info@moglifestyle.fit")
        self.assertEqual(self.fields["sig_client"]["value"], "Rob Chansa")

    def test_the_committed_json_matches_the_pdf(self):
        stored = json.loads((ROOT / "docs" / "intake.json").read_text())
        self.assertEqual(stored, self.brief,
                         "docs/intake.json is stale -- re-run tools/extract_intake.py")

    def test_labels_were_joined_to_their_fields(self):
        self.assertEqual(self.fields["s2_email"]["label"], "Email Address")
        self.assertEqual(self.fields["s6_feat_0"]["label"], "User registration / login")
        self.assertTrue(self.fields["s1_manage"]["label"].startswith("Who will manage"))


class RequirementsTests(StoreTestCase):
    """Each checked box in the brief maps to something that actually exists."""

    seed_catalogue = True

    @classmethod
    def setUpClass(cls) -> None:
        if not PDF.exists():
            raise unittest.SkipTest("mog.pdf is not present")
        brief = load_brief()
        cls.fields = {
            field["id"]: field
            for section in brief["sections"] for field in section["fields"]
        }

    def selected(self, field_id: str) -> str:
        return self.fields[field_id]["selected"]

    def checked(self, field_id: str) -> bool:
        return self.fields[field_id]["checked"]

    def routes(self) -> set[str]:
        from app.application import create_app
        return {route.pattern.pattern for route in create_app().routes}

    def has_route(self, needle: str) -> bool:
        return any(needle in pattern for pattern in self.routes())

    # -- section 3: project type ---------------------------------------

    def test_ecommerce_store_was_requested_and_exists(self):
        self.assertTrue(self.checked("s3_type_1"), "E-Commerce / Online Store")
        self.assertTrue(self.has_route("/cart"))
        self.assertTrue(self.has_route("/checkout"))
        self.assertTrue(self.has_route("/product/"))

    # -- section 6: features -------------------------------------------

    def test_user_registration_and_login(self):
        self.assertTrue(self.checked("s6_feat_0"))
        self.assertTrue(self.has_route("/register"))
        self.assertTrue(self.has_route("/login"))
        self.assertTrue(self.has_route("/account"))

    def test_admin_dashboard(self):
        self.assertTrue(self.checked("s6_feat_1"))
        self.assertTrue(self.has_route("/admin"))

    def test_payment_and_billing(self):
        self.assertTrue(self.checked("s6_feat_2"))
        from app import stripe_api
        self.assertTrue(hasattr(stripe_api, "create_checkout_session"))
        self.assertTrue(self.has_route("/webhooks/stripe"))

    def test_shopping_cart(self):
        self.assertTrue(self.checked("s6_feat_3"))
        from app import cart as cart_module
        self.assertTrue(hasattr(cart_module, "add"))

    def test_search_and_filter(self):
        self.assertTrue(self.checked("s6_feat_5"))
        rows, _ = catalog.search(catalog.Filters(q="oversized"))
        self.assertTrue(rows, "search must return results")
        rows, _ = catalog.search(catalog.Filters(collection="womens-crop-tops"))
        self.assertTrue(rows, "category filtering must work")
        rows, _ = catalog.search(catalog.Filters(department="men"))
        self.assertTrue(rows, "department filtering must work")

    def test_email_notifications(self):
        self.assertTrue(self.checked("s6_feat_6"))
        from app import mailer
        for template in ("order_confirmation", "shipping_notice", "welcome",
                         "low_stock_alert"):
            self.assertTrue(hasattr(mailer, template), template)

    def test_social_media_integration(self):
        self.assertTrue(self.checked("s6_feat_13"))
        from app.ui import site_footer
        markup = site_footer(self.make_request())
        self.assertIn("instagram.com/mog.lifestyle", markup,
                      "the client's confirmed Instagram profile must be linked")
        self.assertIn('rel="me noopener"', markup)

    def test_only_confirmed_social_profiles_are_linked(self):
        """Never ship a link to an account the client has not confirmed."""
        from app.ui import site_footer
        markup = site_footer(self.make_request())
        for unconfirmed in ("tiktok.com", "youtube.com"):
            if not getattr(config, unconfirmed.split(".")[0]):
                self.assertNotIn(unconfirmed, markup, unconfirmed)

    def test_inventory_management(self):
        self.assertTrue(self.checked("s6_feat_16"))
        self.assertTrue(self.has_route("/admin/inventory"))
        for name in ("reserve", "release", "commit_reservation", "restock",
                     "low_stock"):
            self.assertTrue(hasattr(catalog, name), name)

    def test_unchecked_features_were_not_built_in(self):
        """The brief did not ask for SMS or multi-language; we did not add them."""
        self.assertFalse(self.checked("s6_feat_7"), "SMS notifications")
        self.assertFalse(self.checked("s6_feat_11"), "Multi-language support")
        self.assertFalse(self.has_route("/sms"))

    def test_stripe_is_the_named_integration(self):
        self.assertIn("stripe", self.fields["s6_integr_1"]["value"].lower())
        from app import stripe_api
        self.assertIn("api.stripe.com", stripe_api.API_BASE)

    # -- section 7: design ---------------------------------------------

    def test_black_and_white_palette(self):
        self.assertEqual(self.fields["s7_colors"]["value"], "black & white")
        css = (ROOT / "app" / "static" / "css" / "site.css").read_text()
        import re
        colours = set(re.findall(r"#[0-9a-fA-F]{6}", css))
        for colour in colours:
            r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
            spread = max(r, g, b) - min(r, g, b)
            self.assertLessEqual(
                spread, 12,
                f"{colour} is not neutral; the brief specifies black & white",
            )

    def test_platforms_are_desktop_and_mobile_web_only(self):
        self.assertTrue(self.checked("s7_plat_0"), "Desktop (Web)")
        self.assertTrue(self.checked("s7_plat_1"), "Mobile (Web)")
        self.assertFalse(self.checked("s7_plat_2"), "iOS App was not requested")
        self.assertFalse(self.checked("s7_plat_3"), "Android App was not requested")
        css = (ROOT / "app" / "static" / "css" / "site.css").read_text()
        self.assertIn("@media (max-width: 980px)", css,
                      "a responsive breakpoint is required for mobile web")

    def test_branding_was_requested_and_delivered(self):
        self.assertEqual(self.selected("s7_brand"), "No — need branding too")
        brand = ROOT / "app" / "static" / "brand"
        for asset in ("wordmark.png", "monogram.png", "favicon.png", "og.jpg"):
            self.assertTrue((brand / asset).is_file(), asset)
        self.assertTrue((ROOT / "docs" / "BRAND.md").is_file())

    def test_the_clients_own_logotype_is_the_one_in_use(self):
        """Branding started generated; the client supplied the real mark."""
        css = (ROOT / "app" / "static" / "css" / "site.css").read_text()
        self.assertIn('mask: url("/static/brand/wordmark.png")', css)
        self.assertIn("background: currentColor", css,
                      "one asset must serve both light and dark")

    # -- section 8: content --------------------------------------------

    def test_every_requested_page_exists(self):
        pages = self.fields["s8_pages"]["value"].lower()
        self.assertEqual(pages, "home, shop, contact, cart")
        self.assertTrue(self.has_route("^/$"))
        self.assertTrue(self.has_route("/shop"))
        self.assertTrue(self.has_route("/contact"))
        self.assertTrue(self.has_route("/cart"))

    def test_no_customer_facing_cms_was_built(self):
        """The client chose 'No — HesMartech can manage updates'."""
        self.assertTrue(self.selected("s8_cms").startswith("No"))
        self.assertFalse(self.has_route("/cms"))
        self.assertTrue(self.has_route("/admin/products"),
                        "updates happen through the staff console instead")

    # -- section 1 & 9: operations -------------------------------------

    def test_domain_matches_the_clients_email(self):
        self.assertEqual(self.selected("s1_domain"), "Yes, I already bought it")
        self.assertIn("moglifestyle.fit", config.store_email)

    def test_https_is_supported(self):
        self.assertTrue(self.checked("s9_sec_1"), "SSL / HTTPS")
        self.assertTrue(hasattr(config, "force_https"))
        from app import web
        config.force_https = True
        try:
            self.assertIn("Strict-Transport-Security", web._security_headers())
        finally:
            config.force_https = False

    # -- section 5: audience -------------------------------------------

    def test_us_only_shipping_matches_the_brief(self):
        self.assertEqual(self.fields["s5_geo"]["value"], "usa")
        from app.views_account import US_STATES
        self.assertEqual(len(US_STATES), 51, "50 states plus DC")

    def test_accessibility_for_a_16_to_85_audience(self):
        self.assertEqual(self.fields["s5_age"]["value"], "16-85")
        css = (ROOT / "app" / "static" / "css" / "site.css").read_text()
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn(":focus-visible", css)
        self.assertIn(".visually-hidden", css)
        self.assertIn(".skip-link", css)


if __name__ == "__main__":
    unittest.main()
