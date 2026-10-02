"""End-to-end HTTP tests against a real server on a real socket.

These exercise the whole stack the way a browser does: cookies, redirects,
CSRF tokens, sessions and the complete purchase journey.
"""
from __future__ import annotations

import http.client
import json
import re
import threading
import unittest
import urllib.parse
from http.cookies import SimpleCookie

from app import db, seed
from app.application import create_app
from app.config import config
from app.web import Server, build_handler


class Client:
    """A cookie-aware HTTP client, small enough to read in one sitting."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.cookies: dict[str, str] = {}
        self.last_status = 0
        self.last_headers: dict[str, str] = {}
        self._last_html = ""

    # -- transport ------------------------------------------------------

    def request(self, method: str, path: str, *, body: str = "",
                headers: dict | None = None, follow: bool = True,
                depth: int = 0) -> str:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=15)
        sent = dict(headers or {})
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if body:
            sent.setdefault("Content-Type", "application/x-www-form-urlencoded")
        try:
            connection.request(method, path, body=body or None, headers=sent)
            response = connection.getresponse()
            payload = response.read().decode("utf-8", "replace")
            self.last_status = response.status
            self.last_headers = dict(response.getheaders())
            for header, value in response.getheaders():
                if header.lower() == "set-cookie":
                    jar = SimpleCookie()
                    jar.load(value)
                    for name, morsel in jar.items():
                        if morsel["max-age"] == "0":
                            self.cookies.pop(name, None)
                        else:
                            # Store the value exactly as sent -- a browser keeps
                            # the percent-encoded form and echoes it verbatim.
                            self.cookies[name] = morsel.coded_value
            location = self.last_headers.get("Location", "")
            if follow and 300 <= response.status < 400 and location and depth < 5:
                if location.startswith("http"):
                    location = urllib.parse.urlsplit(location).path or "/"
                    query = urllib.parse.urlsplit(
                        self.last_headers["Location"]).query
                    if query:
                        location += "?" + query
                return self.request("GET", location, follow=True, depth=depth + 1)
            return payload
        finally:
            connection.close()

    # -- helpers --------------------------------------------------------

    def get(self, path: str, **kw) -> str:
        return self.request("GET", path, **kw)

    def post(self, path: str, data: dict, **kw) -> str:
        return self.request("POST", path, body=urllib.parse.urlencode(data), **kw)

    def post_json(self, path: str, data: dict) -> dict:
        raw = self.request(
            "POST", path, body=urllib.parse.urlencode(data),
            headers={"Accept": "application/json"}, follow=False,
        )
        return json.loads(raw)

    def csrf(self, path: str = "/cart") -> str:
        html = self.get(path)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not match:
            raise AssertionError(f"no CSRF token on {path}")
        self._last_html = html
        return match.group(1)

    def form_fields(self, path: str) -> dict[str, str]:
        """CSRF token plus the spam-trap fields, exactly as a browser sends them."""
        token = self.csrf(path)
        stamp = re.search(r'name="form_started" value="([^"]+)"', self._last_html)
        fields = {"csrf_token": token}
        if stamp:
            fields["form_started"] = stamp.group(1)
            fields["company_website"] = ""          # honeypot, left empty
        return fields


class LiveServerTestCase(unittest.TestCase):
    """Boots the app once for the whole class on an ephemeral port."""

    @classmethod
    def setUpClass(cls) -> None:
        db.reset()
        db.migrate()
        seed.run(quiet=True)
        cls.server = Server(("127.0.0.1", 0), build_handler(create_app()))
        cls.host, cls.port = cls.server.server_address[0], cls.server.server_address[1]
        config.base_url = f"http://{cls.host}:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        db.close()

    def client(self) -> Client:
        return Client(self.host, self.port)


class PublicPageTests(LiveServerTestCase):
    def test_core_pages_render(self):
        client = self.client()
        for path in ("/", "/shop", "/cart", "/contact", "/about", "/login",
                     "/register", "/shipping", "/faq", "/privacy", "/terms"):
            with self.subTest(path=path):
                body = client.get(path)
                self.assertEqual(client.last_status, 200)
                self.assertIn("MOG", body)

    def test_security_headers_are_present(self):
        client = self.client()
        client.get("/")
        self.assertEqual(client.last_headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(client.last_headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", client.last_headers["Content-Security-Policy"])

    def test_health_and_robots_and_sitemap(self):
        client = self.client()
        health = json.loads(client.get("/healthz"))
        self.assertEqual(health["status"], "ok")
        self.assertIn("Disallow: /admin", client.get("/robots.txt"))
        self.assertIn("<urlset", client.get("/sitemap.xml"))

    def test_product_page_and_structured_data(self):
        client = self.client()
        body = client.get("/product/mens-utility-work-shirt")
        self.assertEqual(client.last_status, 200)
        self.assertIn("Utility Work Shirt", body)
        self.assertIn('"@type": "Product"', body)
        self.assertIn('name="variant_id"', body)

    def test_unknown_product_is_404(self):
        client = self.client()
        client.get("/product/does-not-exist", follow=False)
        self.assertEqual(client.last_status, 404)

    def test_generated_media_is_cacheable_svg(self):
        client = self.client()
        body = client.get("/media/tee-0.svg")
        self.assertIn("<svg", body)
        self.assertIn("image/svg+xml", client.last_headers["Content-Type"])
        self.assertIn("immutable", client.last_headers["Cache-Control"])

    def test_the_homepage_uses_the_supplied_brand_assets(self):
        client = self.client()
        body = client.get("/")
        self.assertIn("/static/img/campaign-wide.jpg", body, "hero photography")
        self.assertIn("/static/brand/favicon.png", body)
        self.assertIn("/static/brand/og.jpg", body)
        self.assertIn('aria-label="MOG"', body, "the logotype needs a text label")
        # The logotype is a CSS mask, so it is referenced from the stylesheet.
        css = client.get("/static/css/site.css")
        self.assertIn("/static/brand/wordmark.png", css)
        self.assertIn("background: currentColor", css)

    def test_photographed_products_serve_real_images(self):
        client = self.client()
        body = client.get("/product/mog-oversized-tee")
        gallery = body.split('class="pdp__gallery"')[1].split("</div>")[0]
        self.assertIn("/static/img/mog-tee-front.jpg", gallery)
        self.assertIn("/static/img/mog-tee-back.jpg", gallery)
        self.assertNotIn("/media/", gallery,
                         "a photographed product must not fall back to generated art")
        self.assertIn("back view", gallery, "each view needs distinct alt text")
        for path in ("/static/img/mog-tee-front.jpg", "/static/brand/wordmark.png"):
            client.get(path)
            self.assertEqual(client.last_status, 200, path)

    def test_unphotographed_products_still_render(self):
        client = self.client()
        body = client.get("/product/mens-utility-work-shirt")
        self.assertIn("/media/workshirt-", body, "generated art is the fallback")
        self.assertEqual(client.last_status, 200)

    def test_every_navigation_link_resolves(self):
        """Crawl the header and footer: no link may 404."""
        import re as _re
        client = self.client()
        home = client.get("/")
        chrome = home[:home.index("<main")] + home[home.index("<footer"):]
        hrefs = sorted(set(_re.findall(r'href="(/[^"#]*)"', chrome)))
        self.assertGreater(len(hrefs), 12, "the menu should expose many links")
        for href in hrefs:
            with self.subTest(href=href):
                client.get(href, follow=False)
                self.assertIn(client.last_status, (200, 303, 403),
                              f"{href} returned {client.last_status}")

    def test_department_links_show_only_that_department(self):
        client = self.client()
        men = client.get("/shop?department=men")
        self.assertIn("Work Shirts", men)
        self.assertNotIn("Crop Tops", men.split("<main")[1])
        women = client.get("/shop?department=women")
        self.assertIn("Crop Tops", women)
        self.assertNotIn("Work Shirts", women.split("<main")[1])

    def test_category_links_land_on_that_category(self):
        client = self.client()
        body = client.get("/shop?collection=womens-crop-tops")
        self.assertEqual(client.last_status, 200)
        self.assertIn("Cropped Tee", body)
        self.assertIn("Crop Tops", body)

    def test_the_sale_link_shows_only_reduced_products(self):
        client = self.client()
        body = client.get("/shop?on_sale=1")
        self.assertEqual(client.last_status, 200)
        results = body.split('class="grid')[1]
        self.assertEqual(results.count('class="card"'), results.count("price__was"),
                         "every result on the sale page must be reduced")

    def test_homepage_anchors_exist_for_the_three_sections(self):
        client = self.client()
        home = client.get("/")
        for anchor in ("men", "women", "sale"):
            self.assertIn(f'id="{anchor}"', home)

    def test_media_urls_in_markup_are_version_stamped(self):
        from app.art import ART_VERSION
        client = self.client()
        body = client.get("/shop")
        self.assertIn(f".svg?v={ART_VERSION}", body,
                      "products without photography still carry a versioned URL")
        # The route still serves the image when the query string is present.
        client.get(f"/media/tee-0.svg?v={ART_VERSION}")
        self.assertEqual(client.last_status, 200)

    def test_search_and_filters(self):
        client = self.client()
        self.assertIn("Utility Work Shirt", client.get("/shop?q=twill"))
        self.assertIn("No matches", client.get("/shop?q=snowboard"))
        self.assertEqual(client.last_status, 200)
        client.get("/shop?department=women&size=M&sort=price-asc&in_stock=1")
        self.assertEqual(client.last_status, 200)

    def test_search_input_is_escaped(self):
        client = self.client()
        body = client.get("/shop?q=" + urllib.parse.quote('<script>alert(1)</script>'))
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)


class CsrfTests(LiveServerTestCase):
    def test_a_post_without_a_token_is_refused(self):
        client = self.client()
        client.get("/")
        client.post("/cart/add", {"variant_id": "1", "quantity": "1"}, follow=False)
        self.assertEqual(client.last_status, 403)

    def test_a_post_with_a_wrong_token_is_refused(self):
        client = self.client()
        client.get("/")
        client.post("/cart/add",
                    {"variant_id": "1", "quantity": "1", "csrf_token": "forged"},
                    follow=False)
        self.assertEqual(client.last_status, 403)

    def test_a_cross_origin_post_is_refused(self):
        client = self.client()
        token = client.csrf("/cart")
        client.request(
            "POST", "/cart/add",
            body=urllib.parse.urlencode(
                {"variant_id": "1", "quantity": "1", "csrf_token": token}),
            headers={"Origin": "https://evil.example.com"}, follow=False,
        )
        self.assertEqual(client.last_status, 403)

    def test_the_webhook_endpoint_is_exempt_but_signature_checked(self):
        client = self.client()
        client.request("POST", "/webhooks/stripe", body='{"id":"evt_x"}',
                       headers={"Content-Type": "application/json"}, follow=False)
        self.assertEqual(client.last_status, 400,
                         "no CSRF challenge, but the signature must fail")


class GuestPurchaseTests(LiveServerTestCase):
    def test_a_guest_can_complete_a_purchase(self):
        client = self.client()

        # 1. Find a product and its first in-stock variant.
        page = client.get("/product/mog-oversized-tee")
        variant_id = re.search(r'name="variant_id" value="(\d+)"[^>]*\n?[^>]*checked',
                               page) or re.search(r'name="variant_id" value="(\d+)"', page)
        variant_id = variant_id.group(1)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

        # 2. Add to bag over the JSON API, as the front end does.
        result = client.post_json("/cart/add", {
            "variant_id": variant_id, "quantity": "2", "csrf_token": token,
        })
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cart_count"], 2)

        # 3. The bag shows the line and a subtotal.
        bag = client.get("/cart")
        self.assertIn("MOG Oversized Tee", bag)
        self.assertIn("$136.00", bag)

        # 4. Apply a promotion code.
        client.post("/cart/discount", {"code": "WELCOME10", "csrf_token": client.csrf()})
        bag = client.get("/cart")
        self.assertIn("WELCOME10", bag)
        self.assertIn("-$13.60", bag)

        # 5. Check out.
        checkout = client.get("/checkout")
        self.assertIn("Demo mode", checkout)
        token = re.search(r'name="csrf_token" value="([^"]+)"', checkout).group(1)
        confirmation = client.post("/checkout", {
            "csrf_token": token, "email": "guest@example.com",
            "name": "Robert Chansa", "line1": "1 Training Way", "line2": "",
            "city": "Boise", "region": "ID", "postal": "83702",
            "phone": "2082066706", "notes": "Leave at the door",
        })

        # 6. Demo payment returns to the confirmation page.
        self.assertIn("Thank you", confirmation)
        self.assertIn("MOG-", confirmation)
        number = re.search(r"(MOG-\d+)", confirmation).group(1)

        order = db.one("SELECT * FROM orders WHERE number = ?", (number,))
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["email"], "guest@example.com")
        self.assertEqual(order["discount_code"], "WELCOME10")
        self.assertEqual(order["ship_city"], "Boise")
        self.assertEqual(order["notes"], "Leave at the door")

        # 7. The confirmation page itself shows an empty bag in the header --
        #    middleware counted before the handler cleared it.
        self.assertIn('data-cart-count>0<', confirmation.replace("\n", ""))

        # 8. The bag is empty again and stock was depleted.
        self.assertIn("Your bag is empty", client.get("/cart"))
        item = db.one("SELECT * FROM order_items WHERE order_id = ?", (order["id"],))
        variant = db.one("SELECT * FROM variants WHERE id = ?", (item["variant_id"],))
        self.assertEqual(variant["reserved"], 0)

        # 9. A receipt was queued.
        email = db.one(
            "SELECT * FROM email_outbox WHERE to_address = ? ORDER BY id DESC",
            ("guest@example.com",),
        )
        self.assertIn(number, email["subject"])

    def test_checkout_validates_the_address(self):
        client = self.client()
        page = client.get("/product/ribbed-beanie")
        variant_id = re.search(r'name="variant_id" value="(\d+)"', page).group(1)
        client.post_json("/cart/add", {
            "variant_id": variant_id, "quantity": "1",
            "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page).group(1),
        })
        body = client.post("/checkout", {
            "csrf_token": client.csrf("/checkout"), "email": "not-an-email",
            "name": "", "line1": "", "city": "", "region": "ZZ", "postal": "abc",
        })
        self.assertIn("Check the highlighted fields", body)
        self.assertIn("Enter a valid email address", body)
        self.assertIn("Enter a 5-digit ZIP code", body)
        self.assertEqual(db.scalar(
            "SELECT count(*) FROM orders WHERE email = 'not-an-email'", (), 0), 0)

    def test_checkout_with_an_empty_bag_redirects(self):
        client = self.client()
        client.get("/")
        body = client.get("/checkout")
        self.assertIn("Your bag is empty", body)

    def test_cancelling_payment_releases_stock(self):
        client = self.client()
        page = client.get("/product/crew-socks-three-pack")
        variant_id = re.search(r'name="variant_id" value="(\d+)"', page).group(1)
        before = db.scalar("SELECT stock - reserved FROM variants WHERE id = ?",
                           (variant_id,))
        client.post_json("/cart/add", {
            "variant_id": variant_id, "quantity": "1",
            "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page).group(1),
        })
        client.request("POST", "/checkout", body=urllib.parse.urlencode({
            "csrf_token": client.csrf("/checkout"), "email": "cancel@example.com",
            "name": "A B", "line1": "1 Way", "city": "Boise", "region": "ID",
            "postal": "83702",
        }), follow=False)
        order = db.one("SELECT * FROM orders WHERE email = 'cancel@example.com'")
        self.assertEqual(order["status"], "pending")
        client.get(f"/checkout/cancelled?order={order['number']}")
        self.assertEqual(
            db.scalar("SELECT status FROM orders WHERE id = ?", (order["id"],)),
            "cancelled",
        )
        self.assertEqual(
            db.scalar("SELECT stock - reserved FROM variants WHERE id = ?",
                      (variant_id,)),
            before,
        )


class AccountJourneyTests(LiveServerTestCase):
    def test_register_sign_out_sign_in_and_see_orders(self):
        client = self.client()
        body = client.post("/register", {
            **client.form_fields("/register"), "email": "journey@example.com",
            "password": "a good long passphrase", "name": "Journey Tester",
            "marketing_opt_in": "1",
        })
        self.assertIn("Hello, Journey", body)

        client.post("/logout", {"csrf_token": client.csrf("/account")})
        self.assertIn("Sign in", client.get("/login"))

        body = client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "journey@example.com", "password": "a good long passphrase",
        })
        self.assertIn("Hello, Journey", body)
        self.assertIn("No orders yet", client.get("/account/orders"))

    def test_a_customer_can_sign_out_from_the_account_area(self):
        client = self.client()
        client.post("/register", {
            **client.form_fields("/register"), "email": "leaver@example.com",
            "password": "a good long passphrase", "name": "Leaver",
        })
        account = client.get("/account")
        self.assertIn('action="/logout"', account,
                      "a signed-in customer needs a way to sign out")
        client.post("/logout", {"csrf_token": client.csrf("/account")})
        client.get("/account", follow=False)
        self.assertEqual(client.last_status, 403)

    def test_logout_rejects_a_get(self):
        client = self.client()
        client.get("/logout", follow=False)
        self.assertEqual(client.last_status, 405,
                         "sign-out must not be triggerable by a link")

    def test_account_pages_require_sign_in(self):
        client = self.client()
        for path in ("/account", "/account/orders", "/account/addresses",
                     "/account/settings"):
            with self.subTest(path=path):
                client.get(path, follow=False)
                self.assertEqual(client.last_status, 403)

    def test_bad_credentials_are_rejected(self):
        client = self.client()
        body = client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "nobody@example.com", "password": "wrong password here",
        })
        self.assertIn("incorrect", body)

    def test_a_signed_in_shopper_keeps_their_bag(self):
        client = self.client()
        page = client.get("/product/womens-form-training-legging")
        variant_id = re.search(r'name="variant_id" value="(\d+)"', page).group(1)
        client.post_json("/cart/add", {
            "variant_id": variant_id, "quantity": "1",
            "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page).group(1),
        })
        client.post("/register", {
            **client.form_fields("/register"), "email": "keeper@example.com",
            "password": "a good long passphrase", "name": "Keeper",
        })
        self.assertIn("Form Training Legging", client.get("/cart"))


class AdminAccessTests(LiveServerTestCase):
    def test_admin_is_refused_to_anonymous_and_customers(self):
        client = self.client()
        client.get("/admin", follow=False)
        self.assertEqual(client.last_status, 403)

        client.post("/register", {
            **client.form_fields("/register"), "email": "nosy@example.com",
            "password": "a good long passphrase",
        })
        for path in ("/admin", "/admin/orders", "/admin/products",
                     "/admin/customers", "/admin/inventory"):
            with self.subTest(path=path):
                client.get(path, follow=False)
                self.assertEqual(client.last_status, 403)

    def test_staff_can_run_the_console(self):
        client = self.client()
        client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        for path, marker in [
            ("/admin", "Revenue"),
            ("/admin/orders", "Orders"),
            ("/admin/products", "product(s)"),
            ("/admin/inventory", "Available"),
            ("/admin/customers", "customer(s)"),
            ("/admin/discounts", "WELCOME10"),
            ("/admin/email", "Outbox"),
            ("/admin/activity", "Audit trail"),
            ("/admin/messages", "Messages"),
        ]:
            with self.subTest(path=path):
                body = client.get(path)
                self.assertEqual(client.last_status, 200)
                self.assertIn(marker, body)

    def test_staff_can_edit_inventory_and_fulfil_an_order(self):
        client = self.client()
        client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        variant = db.one("SELECT * FROM variants ORDER BY id LIMIT 1")
        client.post("/admin/inventory", {
            "csrf_token": client.csrf("/admin/inventory"),
            "variant_id": str(variant["id"]), "stock": "77",
        })
        self.assertEqual(
            db.scalar("SELECT stock FROM variants WHERE id = ?", (variant["id"],)), 77
        )

        # Create a paid order directly, then ship it through the console.
        from app import cart as cart_module, orders
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, variant["id"], 1)
        order = orders.create_from_cart(
            cart_module.load(cart_id), email="ship@example.com", user_id=None,
            shipping={"name": "A B", "line1": "1 Way", "city": "Boise",
                      "region": "ID", "postal": "83702", "country": "US"},
        )
        orders.mark_paid(order, send_email=False)
        client.post(f"/admin/orders/{order['id']}/fulfil", {
            "csrf_token": client.csrf(f"/admin/orders/{order['id']}"),
            "carrier": "USPS", "tracking": "9400111899223",
        })
        shipped = db.one("SELECT * FROM orders WHERE id = ?", (order["id"],))
        self.assertEqual(shipped["status"], "fulfilled")
        self.assertEqual(shipped["tracking_number"], "9400111899223")

    def test_csv_export(self):
        client = self.client()
        client.post("/login", {
            "csrf_token": client.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        body = client.get("/admin/orders/export")
        self.assertIn("text/csv", client.last_headers["Content-Type"])
        self.assertIn("attachment", client.last_headers["Content-Disposition"])
        self.assertTrue(body.startswith("number,created_at,status"))


class ContactAndNewsletterTests(LiveServerTestCase):
    def test_contact_form_stores_and_acknowledges(self):
        client = self.client()
        body = client.post("/contact", {
            **client.form_fields("/contact"), "name": "Robert",
            "email": "robert@example.com", "subject": "Sizing",
            "message": "Do the hoodies run large? I usually wear a medium.",
        })
        self.assertIn("Message sent", body)
        row = db.one("SELECT * FROM messages WHERE email = 'robert@example.com'")
        self.assertEqual(row["subject"], "Sizing")

    def test_contact_form_validates(self):
        client = self.client()
        body = client.post("/contact", {
            **client.form_fields("/contact"), "name": "",
            "email": "bad", "message": "hi",
        })
        self.assertIn("Tell us who you are", body)
        self.assertIn("Enter a valid email address", body)

    def test_newsletter_signup_is_idempotent(self):
        client = self.client()
        newsletter_fields = client.form_fields("/")
        first = client.post_json("/newsletter",
                                 {"email": "list@example.com", **newsletter_fields})
        self.assertTrue(first["ok"])
        client.post_json("/newsletter",
                         {"email": "list@example.com", **newsletter_fields})
        self.assertEqual(db.scalar(
            "SELECT count(*) FROM newsletter WHERE email = 'list@example.com'", (), 0), 1)

    def test_newsletter_rejects_a_bad_address(self):
        client = self.client()
        result = client.post_json(
            "/newsletter", {"email": "nope", **client.form_fields("/")})
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
