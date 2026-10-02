"""Cart mechanics and money arithmetic."""
from __future__ import annotations

import unittest

from app import cart as cart_module, catalog, db, discounts
from app.config import config
from tests.support import StoreTestCase


class CartBasicsTests(StoreTestCase):
    def test_add_creates_a_line(self):
        product = self.make_product(price_cents=5000, stock=5)
        cart = self.make_cart((product["variant_id"], 2))
        self.assertEqual(cart.count, 2)
        self.assertEqual(cart.lines[0].total_cents, 10000)

    def test_adding_the_same_variant_accumulates(self):
        product = self.make_product(stock=10)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, product["variant_id"], 2)
        cart_module.add(cart_id, product["variant_id"], 3)
        cart = cart_module.load(cart_id)
        self.assertEqual(len(cart.lines), 1)
        self.assertEqual(cart.lines[0].quantity, 5)

    def test_quantity_is_capped_by_available_stock(self):
        product = self.make_product(stock=3)
        cart = self.make_cart((product["variant_id"], 9))
        self.assertEqual(cart.lines[0].quantity, 3)

    def test_quantity_is_capped_by_the_per_line_maximum(self):
        product = self.make_product(stock=100)
        cart = self.make_cart((product["variant_id"], 99))
        self.assertEqual(cart.lines[0].quantity, cart_module.MAX_QUANTITY_PER_LINE)

    def test_adding_a_sold_out_variant_raises(self):
        product = self.make_product(stock=0)
        cart_id = cart_module.ensure_cart(None)
        with self.assertRaises(ValueError):
            cart_module.add(cart_id, product["variant_id"], 1)

    def test_adding_an_unknown_variant_raises(self):
        cart_id = cart_module.ensure_cart(None)
        with self.assertRaises(ValueError):
            cart_module.add(cart_id, 999_999, 1)

    def test_adding_a_draft_product_raises(self):
        product = self.make_product(status="draft", stock=5)
        cart_id = cart_module.ensure_cart(None)
        with self.assertRaises(ValueError):
            cart_module.add(cart_id, product["variant_id"], 1)

    def test_set_quantity_to_zero_removes_the_line(self):
        product = self.make_product(stock=5)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, product["variant_id"], 2)
        cart_module.set_quantity(cart_id, product["variant_id"], 0)
        self.assertTrue(cart_module.load(cart_id).empty)

    def test_remove_and_clear(self):
        product = self.make_product(stock=5)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, product["variant_id"], 1)
        cart_module.remove(cart_id, product["variant_id"])
        self.assertTrue(cart_module.load(cart_id).empty)
        cart_module.add(cart_id, product["variant_id"], 1)
        cart_module.clear(cart_id)
        self.assertTrue(cart_module.load(cart_id).empty)

    def test_archived_products_drop_out_of_the_cart(self):
        product = self.make_product(stock=5)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, product["variant_id"], 1)
        with db.tx():
            db.update("products", "id = ?", (product["id"],), status="archived")
        self.assertTrue(cart_module.load(cart_id).empty)

    def test_over_stock_is_reported_when_stock_drops_after_adding(self):
        product = self.make_product(stock=5)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, product["variant_id"], 4)
        with db.tx():
            db.update("variants", "id = ?", (product["variant_id"],), stock=2)
        cart = cart_module.load(cart_id)
        self.assertTrue(cart.has_stock_problem)
        self.assertTrue(cart.lines[0].over_stock)

    def test_ensure_cart_reuses_a_valid_id_and_replaces_a_stale_one(self):
        first = cart_module.ensure_cart(None)
        self.assertEqual(cart_module.ensure_cart(first), first)
        self.assertNotEqual(cart_module.ensure_cart("not-a-real-cart"), "not-a-real-cart")

    def test_empty_cart_id_is_safe(self):
        cart = cart_module.load(None)
        self.assertTrue(cart.empty)
        self.assertEqual(cart.totals.total_cents, 0)
        self.assertEqual(cart_module.count_for(None), 0)

    def test_variant_label(self):
        product = self.make_product(stock=2, sizes=("L",))
        cart = self.make_cart((product["variant_id"], 1))
        self.assertEqual(cart.lines[0].variant_label, "L / Black")


class PricingTests(StoreTestCase):
    def test_shipping_is_charged_below_the_threshold(self):
        product = self.make_product(price_cents=5000, stock=5)
        cart = self.make_cart((product["variant_id"], 1))
        self.assertEqual(cart.totals.subtotal_cents, 5000)
        self.assertEqual(cart.totals.shipping_cents, config.shipping_flat_cents)
        self.assertEqual(cart.totals.total_cents,
                         5000 + config.shipping_flat_cents)

    def test_shipping_is_free_at_the_threshold(self):
        product = self.make_product(price_cents=config.free_shipping_threshold_cents,
                                    stock=5)
        cart = self.make_cart((product["variant_id"], 1))
        self.assertEqual(cart.totals.shipping_cents, 0)
        self.assertEqual(cart.totals.free_shipping_remaining, 0)

    def test_free_shipping_progress_is_reported(self):
        product = self.make_product(price_cents=10000, stock=5)
        cart = self.make_cart((product["variant_id"], 1))
        self.assertEqual(cart.totals.free_shipping_remaining,
                         config.free_shipping_threshold_cents - 10000)

    def test_totals_are_zero_for_an_empty_cart(self):
        cart = cart_module.load(cart_module.ensure_cart(None))
        totals = cart.totals
        self.assertEqual(
            (totals.subtotal_cents, totals.shipping_cents, totals.total_cents),
            (0, 0, 0),
        )

    def test_tax_is_applied_after_discount(self):
        original = config.tax_rate_bps
        config.tax_rate_bps = 1000          # 10%
        try:
            product = self.make_product(price_cents=10000, stock=5)
            cart_id = cart_module.ensure_cart(None)
            cart_module.add(cart_id, product["variant_id"], 1)
            with db.tx():
                db.insert("discounts", code="HALF", kind="percent", value=50)
            cart_module.apply_discount(cart_id, "HALF")
            totals = cart_module.load(cart_id).totals
            self.assertEqual(totals.discount_cents, 5000)
            self.assertEqual(totals.tax_cents, 500, "tax is 10% of the reduced 5000")
        finally:
            config.tax_rate_bps = original

    def test_prices_are_never_floats(self):
        product = self.make_product(price_cents=3333, stock=3)
        cart = self.make_cart((product["variant_id"], 3))
        for value in (cart.totals.subtotal_cents, cart.totals.total_cents,
                      cart.lines[0].total_cents):
            self.assertIsInstance(value, int)


class DiscountTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.make_product(price_cents=10000, stock=10)

    def _apply(self, code: str, quantity: int = 1):
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, self.product["variant_id"], quantity)
        result = cart_module.apply_discount(cart_id, code)
        return result, cart_module.load(cart_id)

    def test_percent_discount(self):
        with db.tx():
            db.insert("discounts", code="TEN", kind="percent", value=10)
        result, cart = self._apply("TEN")
        self.assertTrue(result.ok)
        self.assertEqual(cart.totals.discount_cents, 1000)
        self.assertEqual(cart.totals.discount_code, "TEN")

    def test_fixed_discount_never_exceeds_the_subtotal(self):
        with db.tx():
            db.insert("discounts", code="BIG", kind="fixed", value=999_999)
        _, cart = self._apply("BIG")
        self.assertEqual(cart.totals.discount_cents, 10000)
        self.assertEqual(cart.totals.total_cents, config.shipping_flat_cents)

    def test_free_shipping_discount(self):
        with db.tx():
            db.insert("discounts", code="SHIP", kind="free_shipping", value=0)
        _, cart = self._apply("SHIP")
        self.assertEqual(cart.totals.shipping_cents, 0)
        self.assertEqual(cart.totals.discount_cents, 0)

    def test_unknown_code_is_rejected(self):
        result, cart = self._apply("NOPE")
        self.assertFalse(result.ok)
        self.assertEqual(cart.totals.discount_cents, 0)

    def test_inactive_code_is_rejected(self):
        with db.tx():
            db.insert("discounts", code="OFF", kind="percent", value=50, active=0)
        result, _ = self._apply("OFF")
        self.assertFalse(result.ok)

    def test_expired_code_is_rejected(self):
        with db.tx():
            db.insert("discounts", code="OLD", kind="percent", value=50,
                      ends_at="2000-01-01 00:00:00")
        result, _ = self._apply("OLD")
        self.assertFalse(result.ok)
        self.assertIn("expired", result.message.lower())

    def test_future_code_is_rejected(self):
        with db.tx():
            db.insert("discounts", code="SOON", kind="percent", value=50,
                      starts_at="2999-01-01 00:00:00")
        result, _ = self._apply("SOON")
        self.assertFalse(result.ok)

    def test_exhausted_code_is_rejected(self):
        with db.tx():
            db.insert("discounts", code="GONE", kind="percent", value=50,
                      max_uses=2, uses=2)
        result, _ = self._apply("GONE")
        self.assertFalse(result.ok)

    def test_minimum_spend_is_enforced(self):
        with db.tx():
            db.insert("discounts", code="MIN", kind="fixed", value=1000,
                      min_spend_cents=50000)
        result, _ = self._apply("MIN")
        self.assertFalse(result.ok)
        result, cart = self._apply("MIN", quantity=6)
        self.assertTrue(result.ok)

    def test_codes_are_case_insensitive(self):
        with db.tx():
            db.insert("discounts", code="Welcome10", kind="percent", value=10)
        result, _ = self._apply("wElCoMe10")
        self.assertTrue(result.ok)

    def test_clearing_a_discount(self):
        with db.tx():
            db.insert("discounts", code="TEN", kind="percent", value=10)
        cart_id = cart_module.ensure_cart(None)
        cart_module.add(cart_id, self.product["variant_id"], 1)
        cart_module.apply_discount(cart_id, "TEN")
        cart_module.clear_discount(cart_id)
        self.assertEqual(cart_module.load(cart_id).totals.discount_cents, 0)

    def test_describe(self):
        with db.tx():
            db.insert("discounts", code="P", kind="percent", value=15)
            db.insert("discounts", code="F", kind="fixed", value=2500)
            db.insert("discounts", code="S", kind="free_shipping", value=0)
        self.assertEqual(discounts.describe(discounts.find("P")), "15% off")
        self.assertEqual(discounts.describe(discounts.find("F")), "$25.00 off")
        self.assertEqual(discounts.describe(discounts.find("S")), "Free shipping")


if __name__ == "__main__":
    unittest.main()
