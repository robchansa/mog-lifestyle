"""Order lifecycle: creation, payment, fulfilment, cancellation, refunds."""
from __future__ import annotations

import unittest

from app import cart as cart_module, catalog, db, mailer, orders
from tests.support import StoreTestCase

SHIPPING = {
    "name": "Robert Chansa", "line1": "1 Training Way", "line2": "",
    "city": "Boise", "region": "ID", "postal": "83702", "country": "US",
    "phone": "2082066706",
}


class OrderCreationTests(StoreTestCase):
    def test_order_reserves_stock_and_snapshots_prices(self):
        product = self.make_product(price_cents=5000, stock=5)
        cart = self.make_cart((product["variant_id"], 2))
        order = orders.create_from_cart(
            cart, email="a@b.co", user_id=None, shipping=SHIPPING
        )
        self.assertEqual(order["status"], "pending")
        self.assertEqual(order["subtotal_cents"], 10000)
        variant = catalog.get_variant(product["variant_id"])
        self.assertEqual(variant["reserved"], 2)
        self.assertEqual(variant["stock"], 5, "on-hand stock waits for payment")
        items = orders.items_for(order["id"])
        self.assertEqual(items[0]["unit_cents"], 5000)

    def test_order_numbers_are_sequential_and_unique(self):
        product = self.make_product(stock=20)
        numbers = []
        for _ in range(3):
            cart = self.make_cart((product["variant_id"], 1))
            numbers.append(orders.create_from_cart(
                cart, email="a@b.co", user_id=None, shipping=SHIPPING)["number"])
        self.assertEqual(len(set(numbers)), 3)
        self.assertTrue(all(n.startswith("MOG-") for n in numbers))

    def test_empty_cart_is_refused(self):
        cart = cart_module.load(cart_module.ensure_cart(None))
        with self.assertRaises(orders.CheckoutError):
            orders.create_from_cart(cart, email="a@b.co", user_id=None,
                                    shipping=SHIPPING)

    def test_stock_sold_out_between_render_and_submit_is_refused(self):
        product = self.make_product(stock=2)
        cart = self.make_cart((product["variant_id"], 2))
        # Someone else buys the same units after this cart was rendered.
        with db.tx():
            catalog.reserve(product["variant_id"], 2)
        with self.assertRaises(orders.CheckoutError) as caught:
            orders.create_from_cart(cart, email="a@b.co", user_id=None,
                                    shipping=SHIPPING)
        self.assertIn("sold out", str(caught.exception).lower())

    def test_a_failed_line_rolls_back_every_reservation(self):
        good = self.make_product("Good", stock=10)
        scarce = self.make_product("Scarce", stock=1)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, good["variant_id"], 2)
        cart_module.add(cart_id, scarce["variant_id"], 1)
        cart = cart_module.load(cart_id)
        with db.tx():
            catalog.reserve(scarce["variant_id"], 1)     # last unit taken
        with self.assertRaises(orders.CheckoutError):
            orders.create_from_cart(cart, email="a@b.co", user_id=None,
                                    shipping=SHIPPING)
        self.assertEqual(catalog.get_variant(good["variant_id"])["reserved"], 0,
                         "the first line's reservation must roll back too")
        self.assertEqual(db.scalar("SELECT count(*) FROM orders", (), 0), 0)

    def test_shipping_fields_are_truncated_not_rejected(self):
        product = self.make_product(stock=2)
        cart = self.make_cart((product["variant_id"], 1))
        order = orders.create_from_cart(
            cart, email="a@b.co", user_id=None,
            shipping={**SHIPPING, "name": "N" * 500, "country": "usa"},
        )
        self.assertEqual(len(order["ship_name"]), 120)
        self.assertEqual(order["ship_country"], "US")


class PaymentTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.make_product(price_cents=5000, stock=5)
        cart = self.make_cart((self.product["variant_id"], 2))
        self.order = orders.create_from_cart(
            cart, email="buyer@example.com", user_id=None, shipping=SHIPPING
        )

    def test_mark_paid_commits_stock_and_queues_the_receipt(self):
        paid = orders.mark_paid(self.order, payment_intent="pi_test")
        self.assertEqual(paid["status"], "paid")
        self.assertEqual(paid["payment_intent"], "pi_test")
        self.assertIsNotNone(paid["paid_at"])
        variant = catalog.get_variant(self.product["variant_id"])
        self.assertEqual((variant["stock"], variant["reserved"]), (3, 0))
        subjects = [r["subject"] for r in db.query("SELECT subject FROM email_outbox")]
        self.assertTrue(any("confirmed" in s for s in subjects), subjects)

    def test_mark_paid_is_idempotent(self):
        orders.mark_paid(self.order, payment_intent="pi_test")
        again = orders.mark_paid(orders.get(self.order["id"]),
                                 payment_intent="pi_other")
        self.assertEqual(again["payment_intent"], "pi_test",
                         "a second confirmation must not overwrite the first")
        variant = catalog.get_variant(self.product["variant_id"])
        self.assertEqual(variant["stock"], 3, "stock must only be depleted once")

    def test_paying_twice_never_double_counts_a_discount(self):
        with db.tx():
            db.insert("discounts", code="TEN", kind="percent", value=10)
            db.update("orders", "id = ?", (self.order["id"],), discount_code="TEN")
        order = orders.get(self.order["id"])
        orders.mark_paid(order)
        orders.mark_paid(orders.get(order["id"]))
        self.assertEqual(db.scalar("SELECT uses FROM discounts WHERE code='TEN'"), 1)

    def test_cancelling_a_pending_order_releases_the_reservation(self):
        orders.cancel(self.order, reason="[test]")
        variant = catalog.get_variant(self.product["variant_id"])
        self.assertEqual((variant["stock"], variant["reserved"]), (5, 0))
        self.assertEqual(orders.get(self.order["id"])["status"], "cancelled")

    def test_cancelling_a_paid_order_restocks_the_units(self):
        orders.mark_paid(self.order)
        orders.cancel(orders.get(self.order["id"]), reason="[test]")
        variant = catalog.get_variant(self.product["variant_id"])
        self.assertEqual(variant["stock"], 5, "paid units go back on the shelf")

    def test_cancelling_twice_does_not_double_restock(self):
        orders.cancel(self.order)
        orders.cancel(orders.get(self.order["id"]))
        self.assertEqual(catalog.get_variant(self.product["variant_id"])["stock"], 5)

    def test_fulfilment_requires_payment(self):
        with self.assertRaises(orders.CheckoutError):
            orders.fulfil(self.order, carrier="USPS", tracking="1Z")

    def test_fulfilment_records_tracking_and_emails(self):
        orders.mark_paid(self.order)
        shipped = orders.fulfil(orders.get(self.order["id"]),
                                carrier="USPS", tracking="9400111899")
        self.assertEqual(shipped["status"], "fulfilled")
        self.assertEqual(shipped["tracking_number"], "9400111899")
        self.assertIsNotNone(shipped["fulfilled_at"])
        subjects = [r["subject"] for r in db.query("SELECT subject FROM email_outbox")]
        self.assertTrue(any("shipped" in s for s in subjects), subjects)

    def test_refund_restocks_and_marks_refunded(self):
        orders.mark_paid(self.order, payment_intent="pi_demo_abc")
        refunded = orders.refund(orders.get(self.order["id"]))
        self.assertEqual(refunded["status"], "refunded")
        self.assertEqual(catalog.get_variant(self.product["variant_id"])["stock"], 5)

    def test_refund_requires_a_paid_order(self):
        with self.assertRaises(orders.CheckoutError):
            orders.refund(self.order)

    def test_expire_stale_releases_abandoned_reservations(self):
        with db.tx():
            db.execute(
                "UPDATE orders SET created_at = datetime('now', '-3 hours') WHERE id = ?",
                (self.order["id"],),
            )
        released = orders.expire_stale(minutes=45)
        self.assertEqual(released, 1)
        self.assertEqual(catalog.get_variant(self.product["variant_id"])["reserved"], 0)
        self.assertEqual(orders.get(self.order["id"])["status"], "cancelled")

    def test_expire_stale_leaves_fresh_orders_alone(self):
        self.assertEqual(orders.expire_stale(minutes=45), 0)
        self.assertEqual(orders.get(self.order["id"])["status"], "pending")

    def test_low_stock_alert_is_sent_when_a_variant_runs_down(self):
        product = self.make_product("Scarce", stock=3)
        cart = self.make_cart((product["variant_id"], 3))
        order = orders.create_from_cart(cart, email="a@b.co", user_id=None,
                                        shipping=SHIPPING)
        orders.mark_paid(order)
        templates = [r["template"] for r in db.query("SELECT template FROM email_outbox")]
        self.assertIn("low_stock", templates)


class ReportingTests(StoreTestCase):
    def test_metrics_and_series(self):
        product = self.make_product(price_cents=10000, stock=20)
        for _ in range(2):
            cart = self.make_cart((product["variant_id"], 1))
            order = orders.create_from_cart(cart, email="a@b.co", user_id=None,
                                            shipping=SHIPPING)
            orders.mark_paid(order)
        metrics = orders.metrics(30)
        self.assertEqual(metrics["orders"], 2)
        self.assertEqual(metrics["revenue_cents"], 2 * (10000 + 800))
        self.assertEqual(metrics["aov_cents"], 10800)
        self.assertEqual(metrics["awaiting_fulfilment"], 2)
        series = orders.revenue_series(14)
        self.assertEqual(len(series), 14)
        self.assertEqual(sum(cents for _, cents in series), 2 * 10800)

    def test_best_sellers(self):
        a = self.make_product("Popular", stock=20)
        b = self.make_product("Quiet", stock=20)
        cart = self.make_cart((a["variant_id"], 5), (b["variant_id"], 1))
        orders.mark_paid(orders.create_from_cart(
            cart, email="a@b.co", user_id=None, shipping=SHIPPING))
        sellers = orders.best_sellers()
        self.assertEqual(sellers[0]["title"], "Popular")
        self.assertEqual(sellers[0]["units"], 5)

    def test_orders_for_user_hides_pending(self):
        user = self.make_user()
        product = self.make_product(stock=10)
        cart = self.make_cart((product["variant_id"], 1))
        orders.create_from_cart(cart, email=user["email"], user_id=user["id"],
                                shipping=SHIPPING)
        self.assertEqual(orders.for_user(user["id"]), [])
        cart = self.make_cart((product["variant_id"], 1))
        paid = orders.create_from_cart(cart, email=user["email"],
                                       user_id=user["id"], shipping=SHIPPING)
        orders.mark_paid(paid)
        self.assertEqual(len(orders.for_user(user["id"])), 1)

    def test_recent_filters_by_status_and_search(self):
        product = self.make_product(stock=10)
        cart = self.make_cart((product["variant_id"], 1))
        order = orders.create_from_cart(cart, email="findme@example.com",
                                        user_id=None, shipping=SHIPPING)
        self.assertEqual(len(orders.recent(status="pending")), 1)
        self.assertEqual(len(orders.recent(status="paid")), 0)
        self.assertEqual(len(orders.recent(search="findme")), 1)
        self.assertEqual(len(orders.recent(search="nobody")), 0)
        self.assertEqual(orders.get_by_number(order["number"])["id"], order["id"])


if __name__ == "__main__":
    unittest.main()
