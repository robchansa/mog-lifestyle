"""Stripe encoding, webhook signature verification and demo mode."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from app import stripe_api
from app.config import config

SECRET = "whsec_test_secret"


def sign(payload: bytes, secret: str = SECRET, timestamp: int | None = None) -> str:
    timestamp = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


class EncodingTests(unittest.TestCase):
    def test_nested_structures_use_bracket_notation(self):
        pairs = dict(stripe_api.encode_form({
            "mode": "payment",
            "metadata": {"order_number": "MOG-4001"},
            "line_items": [{"quantity": 2, "price_data": {"unit_amount": 500}}],
            "flag": True,
        }))
        self.assertEqual(pairs["mode"], "payment")
        self.assertEqual(pairs["metadata[order_number]"], "MOG-4001")
        self.assertEqual(pairs["line_items[0][quantity]"], "2")
        self.assertEqual(pairs["line_items[0][price_data][unit_amount]"], "500")
        self.assertEqual(pairs["flag"], "true")

    def test_none_values_are_omitted(self):
        pairs = dict(stripe_api.encode_form({"a": 1, "b": None}))
        self.assertNotIn("b", pairs)

    def test_scalar_lists(self):
        pairs = dict(stripe_api.encode_form({"expand": ["a", "b"]}))
        self.assertEqual(pairs["expand[0]"], "a")
        self.assertEqual(pairs["expand[1]"], "b")


class WebhookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()

    def test_a_valid_signature_is_accepted(self):
        event = stripe_api.verify_webhook(self.payload, sign(self.payload), SECRET)
        self.assertEqual(event["id"], "evt_1")

    def test_a_forged_signature_is_rejected(self):
        header = sign(self.payload, secret="whsec_wrong_secret")
        with self.assertRaises(stripe_api.StripeError):
            stripe_api.verify_webhook(self.payload, header, SECRET)

    def test_a_modified_payload_is_rejected(self):
        header = sign(self.payload)
        with self.assertRaises(stripe_api.StripeError):
            stripe_api.verify_webhook(self.payload + b" ", header, SECRET)

    def test_a_stale_timestamp_is_rejected(self):
        header = sign(self.payload, timestamp=int(time.time()) - 4000)
        with self.assertRaises(stripe_api.StripeError) as caught:
            stripe_api.verify_webhook(self.payload, header, SECRET)
        self.assertIn("tolerance", str(caught.exception))

    def test_a_future_timestamp_is_rejected(self):
        header = sign(self.payload, timestamp=int(time.time()) + 4000)
        with self.assertRaises(stripe_api.StripeError):
            stripe_api.verify_webhook(self.payload, header, SECRET)

    def test_malformed_headers_are_rejected(self):
        for header in ("", "garbage", "t=,v1=", "v1=abc", "t=123"):
            with self.subTest(header=header):
                with self.assertRaises(stripe_api.StripeError):
                    stripe_api.verify_webhook(self.payload, header, SECRET)

    def test_multiple_signatures_pass_if_any_matches(self):
        timestamp = int(time.time())
        good = sign(self.payload, timestamp=timestamp).split("v1=")[1]
        header = f"t={timestamp},v1={'0' * 64},v1={good}"
        event = stripe_api.verify_webhook(self.payload, header, SECRET)
        self.assertEqual(event["type"], "checkout.session.completed")

    def test_an_unconfigured_secret_refuses_everything(self):
        with self.assertRaises(stripe_api.StripeError):
            stripe_api.verify_webhook(self.payload, sign(self.payload), "")

    def test_non_json_payload_is_rejected_after_signature_passes(self):
        payload = b"not json"
        with self.assertRaises(stripe_api.StripeError) as caught:
            stripe_api.verify_webhook(payload, sign(payload), SECRET)
        self.assertIn("JSON", str(caught.exception))

    def test_parse_signature_header(self):
        self.assertEqual(
            stripe_api.parse_signature_header("t=1,v1=a,v1=b,v0=c"), (1, ["a", "b"])
        )


class DemoModeTests(unittest.TestCase):
    def test_checkout_session_is_synthetic_without_keys(self):
        self.assertFalse(config.payments_live)
        session = stripe_api.create_checkout_session(
            order_number="MOG-4001", email="a@b.co",
            line_items=[{"quantity": 1, "unit_cents": 5000, "name": "Tee"}],
            success_url="http://testserver/checkout/complete?order=MOG-4001",
            cancel_url="http://testserver/checkout/cancelled",
        )
        self.assertTrue(session["demo"])
        self.assertTrue(session["id"].startswith("cs_demo_"))
        self.assertIn("demo=1", session["url"])

    def test_retrieve_reports_paid_in_demo_mode(self):
        session = stripe_api.retrieve_checkout_session("cs_demo_abc")
        self.assertEqual(session["payment_status"], "paid")

    def test_refund_is_synthetic_in_demo_mode(self):
        self.assertEqual(stripe_api.refund("pi_demo_x")["status"], "succeeded")

    def test_live_calls_refuse_without_a_key(self):
        with self.assertRaises(stripe_api.StripeError):
            stripe_api._request("GET", "/charges")


if __name__ == "__main__":
    unittest.main()
