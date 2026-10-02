"""Transactional email: outbox durability and template rendering."""
from __future__ import annotations

import unittest

from app import cart as cart_module, db, mailer, orders
from tests.support import StoreTestCase

SHIPPING = {
    "name": "Robert Chansa", "line1": "1 Training Way", "city": "Boise",
    "region": "ID", "postal": "83702", "country": "US",
}


class OutboxTests(StoreTestCase):
    def test_queue_then_flush_marks_sent(self):
        message_id = mailer.queue("a@b.co", "Subject", "Body")
        row = db.one("SELECT * FROM email_outbox WHERE id = ?", (message_id,))
        self.assertEqual(row["status"], "queued")
        self.assertEqual(mailer.flush(message_id), 1)
        row = db.one("SELECT * FROM email_outbox WHERE id = ?", (message_id,))
        self.assertEqual(row["status"], "sent")
        self.assertIsNotNone(row["sent_at"])

    def test_send_queues_and_delivers_in_one_step(self):
        mailer.send("a@b.co", "Hello", "Body text")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertEqual(row["status"], "sent")

    def test_a_transport_failure_is_recorded_not_raised(self):
        original = mailer._deliver

        def explode(row):
            raise RuntimeError("smtp is down")

        mailer._deliver = explode
        try:
            mailer.send("a@b.co", "Hello", "Body")
        finally:
            mailer._deliver = original
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertEqual(row["status"], "failed")
        self.assertIn("smtp is down", row["error"])

    def test_a_failed_message_can_be_retried(self):
        original = mailer._deliver
        mailer._deliver = lambda row: (_ for _ in ()).throw(RuntimeError("down"))
        try:
            mailer.send("a@b.co", "Hello", "Body")
        finally:
            mailer._deliver = original
        with db.tx():
            db.execute("UPDATE email_outbox SET status = 'queued' WHERE status = 'failed'")
        self.assertEqual(mailer.flush(), 1)
        self.assertEqual(
            db.scalar("SELECT status FROM email_outbox ORDER BY id DESC"), "sent"
        )

    def test_flush_respects_its_limit(self):
        for index in range(5):
            mailer.queue("a@b.co", f"Subject {index}", "Body")
        self.assertEqual(mailer.flush(limit=2), 2)
        self.assertEqual(
            db.scalar("SELECT count(*) FROM email_outbox WHERE status = 'queued'"), 3
        )


class TemplateTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.make_product(price_cents=6800, stock=5, sizes=("L",))
        cart = self.make_cart((self.product["variant_id"], 2))
        self.order = orders.create_from_cart(
            cart, email="buyer@example.com", user_id=None, shipping=SHIPPING
        )

    def test_order_confirmation_contains_the_essentials(self):
        mailer.order_confirmation(
            self.order, orders.items_for(self.order["id"]),
            to_address="buyer@example.com",
        )
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertEqual(row["template"], "order_confirmation")
        self.assertIn(self.order["number"], row["subject"])
        for expected in (self.order["number"], "Test Tee", "$136.00",
                         "Robert Chansa", "Boise"):
            self.assertIn(expected, row["body_text"], expected)
            self.assertIn(expected, row["body_html"], expected)

    def test_html_escaping_in_templates(self):
        with db.tx():
            db.update("orders", "id = ?", (self.order["id"],),
                      ship_name="<script>alert(1)</script>")
        order = orders.get(self.order["id"])
        mailer.order_confirmation(order, orders.items_for(order["id"]),
                                  to_address="buyer@example.com")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertNotIn("<script>alert(1)</script>", row["body_html"])
        self.assertIn("&lt;script&gt;", row["body_html"])

    def test_shipping_notice(self):
        with db.tx():
            db.update("orders", "id = ?", (self.order["id"],),
                      tracking_carrier="USPS", tracking_number="9400111899")
        mailer.shipping_notice(orders.get(self.order["id"]),
                               to_address="buyer@example.com")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertIn("9400111899", row["body_text"])
        self.assertIn("USPS", row["body_html"])

    def test_shipping_notice_without_tracking(self):
        mailer.shipping_notice(self.order, to_address="buyer@example.com")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertIn("has shipped", row["subject"])

    def test_welcome_uses_a_first_name_when_present(self):
        mailer.welcome("new@example.com", "Robert Chansa")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertIn("Welcome, Robert.", row["body_text"])
        mailer.welcome("anon@example.com", "")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertIn("Welcome.", row["body_text"])

    def test_contact_receipt_writes_two_messages(self):
        before = db.scalar("SELECT count(*) FROM email_outbox", (), 0)
        mailer.contact_receipt("Robert", "robert@example.com", "Sizing", "Do they run large?")
        after = db.scalar("SELECT count(*) FROM email_outbox", (), 0)
        self.assertEqual(after - before, 2, "one to the customer, one to the store")

    def test_low_stock_alert(self):
        from app import catalog
        rows = catalog.low_stock(threshold=99)
        mailer.low_stock_alert(rows, to_address="ops@example.com")
        row = db.one("SELECT * FROM email_outbox ORDER BY id DESC")
        self.assertEqual(row["template"], "low_stock")
        self.assertIn("Test Tee", row["body_text"])


if __name__ == "__main__":
    unittest.main()
