"""Product pricing through the real admin, storefront and checkout routes."""
from app import catalog, db, orders, cart as cart_module
from tests.test_integration import LiveServerTestCase
from tests.test_orders import SHIPPING


class ProductPricingTests(LiveServerTestCase):
    def setUp(self):
        self.product_id = catalog.create_product(
            title=self._testMethodName, price_cents=5000, status="active",
            description="Keep this description", images="/static/img/mog-tee-front.jpg",
        )
        catalog.upsert_variants(self.product_id, [
            {"sku": f"PRICE-{self.product_id}", "size": "M", "color": "Black", "stock": 10},
        ])
        self.product = catalog.get_product_by_id(self.product_id)
        self.variant = catalog.variants_for(self.product_id)[0]
        self.path = f"/admin/products/{self.product_id}/price"
        self.admin = self.client()
        self.admin.post("/login", {
            "csrf_token": self.admin.csrf("/login"),
            "email": "info@moglifestyle.fit", "password": "mog-admin-2026",
        })
        self.assertEqual(self.admin.last_status, 200)

    def save_price(self, price, compare=""):
        return self.admin.post(self.path, {
            "csrf_token": self.admin.csrf("/admin/products"),
            "price": price, "compare_price": compare,
        })

    def test_products_page_has_discoverable_price_and_detail_controls(self):
        page = self.admin.get("/admin/products")
        self.assertIn(f'action="{self.path}"', page)
        self.assertIn('name="price" type="number"', page)
        self.assertIn('value="50.00"', page)
        self.assertIn("Save price", page)
        self.assertIn(f'href="/admin/products/{self.product_id}">Edit details', page)

    def test_save_updates_storefront_cart_checkout_and_new_orders(self):
        shopper = self.client()
        shopper.post("/cart/add", {
            "csrf_token": shopper.csrf("/cart"),
            "variant_id": str(self.variant["id"]), "quantity": "1",
        })
        page = self.save_price("29.99", "45.00")
        self.assertIn("Price saved", page)
        saved = catalog.get_product_by_id(self.product_id)
        self.assertEqual((saved["price_cents"], saved["compare_cents"]), (2999, 4500))
        self.assertIn("$29.99", shopper.get(f"/product/{saved['slug']}"))
        self.assertIn("$29.99", shopper.get(f"/shop?q={self._testMethodName}"))
        self.assertIn("$29.99", shopper.get("/cart"))
        self.assertIn("$29.99", shopper.get("/checkout"))
        cart = cart_module.ensure_cart(None)
        cart_module.add(cart, self.variant["id"], 1)
        order = orders.create_from_cart(cart_module.load(cart), email="price@example.com",
                                        user_id=None, shipping=SHIPPING)
        self.assertEqual(orders.items_for(order["id"])[0]["unit_cents"], 2999)
        self.assertEqual(saved["description"], self.product["description"])
        self.assertEqual(saved["images"], self.product["images"])
        self.assertEqual(catalog.get_variant(self.variant["id"])["stock"], 10)
        self.assertEqual(db.scalar("SELECT count(*) FROM audit_log WHERE action = 'product.price' AND subject = ?",
                                   (saved["slug"],)), 1)

    def test_invalid_prices_never_change_the_product(self):
        for value in ("", "-1", "abc", "NaN", "Infinity", "1e3", "29.999", "1000000", "9" * 100):
            with self.subTest(value=value):
                page = self.save_price(value)
                self.assertIn("Price must be a dollar amount", page)
                self.assertEqual(catalog.get_product_by_id(self.product_id)["price_cents"], 5000)

    def test_invalid_compare_prices_reject_the_whole_update(self):
        for compare in ("-1", "nope", "10.001", "20", "19.99"):
            with self.subTest(compare=compare):
                self.save_price("20", compare)
                saved = catalog.get_product_by_id(self.product_id)
                self.assertEqual(saved["price_cents"], 5000)
                self.assertIsNone(saved["compare_cents"])

    def test_zero_price_and_removing_a_sale(self):
        self.save_price("0.01", "10")
        self.save_price("0", "")
        saved = catalog.get_product_by_id(self.product_id)
        self.assertEqual(saved["price_cents"], 0)
        self.assertIsNone(saved["compare_cents"])

    def test_customer_cannot_change_prices_and_csrf_is_required(self):
        customer = self.client()
        customer.post("/register", {
            **customer.form_fields("/register"), "email": "price-customer@example.com",
            "password": "a good long passphrase",
        })
        customer.post(self.path, {"csrf_token": customer.csrf("/cart"), "price": "1"}, follow=False)
        self.assertEqual(customer.last_status, 403)
        self.admin.post(self.path, {"price": "1"}, follow=False)
        self.assertEqual(self.admin.last_status, 403)
        self.assertEqual(catalog.get_product_by_id(self.product_id)["price_cents"], 5000)

    def test_unknown_product_returns_404(self):
        self.admin.post("/admin/products/999999/price", {
            "csrf_token": self.admin.csrf("/admin/products"), "price": "1",
        }, follow=False)
        self.assertEqual(self.admin.last_status, 404)

    def test_existing_orders_keep_their_original_prices(self):
        cart = cart_module.ensure_cart(None)
        cart_module.add(cart, self.variant["id"], 1)
        order = orders.create_from_cart(cart_module.load(cart), email="old@example.com",
                                        user_id=None, shipping=SHIPPING)
        self.save_price("25.50")
        self.assertEqual(orders.items_for(order["id"])[0]["unit_cents"], 5000)
        self.assertEqual(orders.get(order["id"])["subtotal_cents"], 5000)

    def test_full_editor_updates_details_and_prices_in_dollars(self):
        path = f"/admin/products/{self.product_id}"
        result = self.admin.post(path, {
            "csrf_token": self.admin.csrf(path), "title": "Updated product title",
            "description": "Updated product description", "price": "19.95",
            "compare_price": "25", "status": "active",
            "variants": f"{self.variant['sku']} | M | Black | 10",
        })
        self.assertIn("Product saved", result)
        saved = catalog.get_product_by_id(self.product_id)
        self.assertEqual(saved["title"], "Updated product title")
        self.assertEqual(saved["description"], "Updated product description")
        self.assertEqual(saved["price_cents"], 1995)
        self.assertEqual(saved["compare_cents"], 2500)
        self.assertEqual(catalog.variants_for(self.product_id)[0]["id"], self.variant["id"])

    def test_full_editor_preserves_submitted_details_when_price_is_invalid(self):
        path = f"/admin/products/{self.product_id}"
        result = self.admin.post(path, {
            "csrf_token": self.admin.csrf(path), "title": "Unsaved title",
            "price": "bad", "variants": "SAVED-SKU | L | Blue | 3",
        })
        self.assertIn("Price must be a dollar amount", result)
        self.assertIn('value="Unsaved title"', result)
        self.assertIn("SAVED-SKU | L | Blue | 3", result)
        self.assertEqual(catalog.get_product_by_id(self.product_id)["price_cents"], 5000)
