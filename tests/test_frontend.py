"""Guards for front-end invariants that are easy to break silently.

These assert on the stylesheet and rendered markup rather than on a real
browser, but each one encodes a bug that actually happened.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from tests.support import StoreTestCase

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "site.css").read_text()
JS = (ROOT / "app" / "static" / "js" / "site.js").read_text()


def media_block(query: str) -> str:
    """Return the body of the first @media block matching `query`."""
    start = CSS.index(query)
    depth, index = 0, CSS.index("{", start)
    opening = index
    while True:
        char = CSS[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return CSS[opening + 1:index]
        index += 1


class MobileNavTests(unittest.TestCase):
    """The overlay nav is `position: fixed` inside the sticky header."""

    def setUp(self) -> None:
        self.block = media_block("@media (max-width: 980px)")

    def test_the_header_drops_backdrop_filter_at_mobile_width(self):
        # `backdrop-filter` makes an element the containing block for fixed
        # descendants, which traps the overlay inside the header bar.
        self.assertIn("backdrop-filter: none", self.block)

    def test_the_overlay_covers_the_viewport(self):
        self.assertIn("position: fixed; inset: 0", self.block)

    def test_the_toggle_sits_above_the_overlay(self):
        # Without this a touch user cannot close the menu: there is no Escape key.
        toggle_z = re.search(r"\.nav-toggle \{[^}]*z-index:\s*(\d+)", CSS)
        nav_z = re.search(r"\.nav \{[^}]*z-index:\s*(\d+)", self.block)
        self.assertIsNotNone(toggle_z)
        self.assertIsNotNone(nav_z)
        self.assertGreater(int(toggle_z.group(1)), int(nav_z.group(1)))

    def test_the_toggle_swaps_to_a_close_icon(self):
        self.assertIn('.nav-toggle[aria-expanded="true"] .nav-toggle__open', CSS)
        self.assertIn('.nav-toggle[aria-expanded="true"] .nav-toggle__close', CSS)

    def test_the_toggle_renders_both_icons(self):
        from app.ui import site_header
        from http.client import HTTPMessage
        from http.cookies import SimpleCookie
        from app.web import Request
        request = Request("GET", "/", {}, HTTPMessage(), b"", SimpleCookie(), "1.1.1.1")
        request.user = None
        markup = site_header(request)
        self.assertIn("nav-toggle__open", markup)
        self.assertIn("nav-toggle__close", markup)
        self.assertIn('aria-expanded="false"', markup)
        self.assertIn('aria-controls="site-nav"', markup)


class SocialProfileTests(StoreTestCase):
    def test_instagram_url_is_built_from_the_handle(self):
        from app.config import config
        self.assertEqual(config.instagram, "mog.lifestyle")
        urls = {label: url for _, url, label in config.social_links}
        self.assertEqual(urls["Instagram"],
                         "https://www.instagram.com/mog.lifestyle/")

    def test_an_at_prefixed_handle_is_normalised(self):
        from app.config import config
        original = config.instagram
        try:
            config.instagram = "@mog.lifestyle"
            urls = {label: url for _, url, label in config.social_links}
            self.assertEqual(urls["Instagram"],
                             "https://www.instagram.com/mog.lifestyle/")
        finally:
            config.instagram = original

    def test_blank_handles_are_dropped(self):
        from app.config import config
        original = config.instagram
        try:
            config.instagram = "   "
            self.assertNotIn("Instagram",
                             [label for _, _, label in config.social_links])
        finally:
            config.instagram = original

    def test_the_footer_and_structured_data_agree(self):
        """Both render from config.social_links, so they cannot drift apart."""
        from app.application import create_app
        from app import accounts, cart as cart_module
        from app.config import config
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/")
        markup = route.handler(request, **params).body.decode()
        for _, url, _ in config.social_links:
            self.assertIn(url, markup)
        self.assertIn('"sameAs"', markup)


class AccessibilityTests(unittest.TestCase):
    def test_reduced_motion_is_honoured(self):
        self.assertIn("@media (prefers-reduced-motion: reduce)", CSS)
        self.assertIn("animation-duration: .01ms !important", CSS)

    def test_focus_is_always_visible(self):
        self.assertIn(":focus-visible", CSS)
        self.assertNotIn("outline: none;\n}", CSS.replace(" ", " "))

    def test_skip_link_and_screen_reader_helpers_exist(self):
        self.assertIn(".skip-link", CSS)
        self.assertIn(".visually-hidden", CSS)

    def test_wide_content_scrolls_inside_its_own_container(self):
        self.assertIn("overflow-x: auto", CSS)


class ProgressiveEnhancementTests(StoreTestCase):
    seed_catalogue = True

    def _render(self, path: str) -> str:
        from app.application import create_app
        request = self.make_request(path=path)
        from app import accounts
        request.session = accounts.create_session(None)
        request.user = None
        from app import cart as cart_module
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", path)
        return route.handler(request, **params).body.decode()

    def test_every_mutating_form_carries_a_csrf_token(self):
        for path in ("/", "/shop", "/cart", "/contact", "/login", "/register"):
            with self.subTest(path=path):
                markup = self._render(path)
                forms = re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>',
                                   markup, re.S | re.I)
                self.assertTrue(forms, f"{path} should contain a POST form")
                for form in forms:
                    self.assertIn('name="csrf_token"', form,
                                  f"a POST form on {path} has no CSRF token")

    def test_forms_work_without_javascript(self):
        markup = self._render("/cart")
        # The async paths are enhancements layered on real form actions.
        self.assertIn('action="/newsletter"', markup)
        self.assertIn('method="post"', markup)

    def test_noscript_fallbacks_exist_for_js_only_controls(self):
        markup = self._render("/shop")
        self.assertIn("<noscript>", markup,
                      "the auto-submitting sort control needs a fallback")

    def test_js_never_assumes_fetch_exists(self):
        self.assertIn("if (!window.fetch) return;", JS)


class MarkupTests(StoreTestCase):
    seed_catalogue = True

    def test_images_declare_intrinsic_dimensions(self):
        from app.ui import product_card
        from app import catalog
        markup = product_card(catalog.featured(1)[0])
        self.assertIn('width="800"', markup)
        self.assertIn('height="1000"', markup)
        self.assertIn('loading=', markup)

    def test_product_images_have_meaningful_alt_text(self):
        from app.ui import product_card
        from app import catalog
        product = catalog.featured(1)[0]
        markup = product_card(product)
        self.assertIn(f'alt="{product["title"]}"', markup)

    def test_decorative_images_have_empty_alt(self):
        """The hero photograph is decoration; the copy beside it carries the meaning."""
        from app.application import create_app
        from app import accounts, cart as cart_module
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/")
        markup = route.handler(request, **params).body.decode()

        hero = markup[markup.index('class="hero"'):markup.index('class="marquee"')]
        images = re.findall(r"<img[^>]*>", hero)
        self.assertTrue(images)
        for tag in images:
            self.assertIn('alt=""', tag, tag)
        self.assertIn("/static/img/campaign-", hero,
                      "the hero should use the supplied campaign photography")


if __name__ == "__main__":
    unittest.main()
