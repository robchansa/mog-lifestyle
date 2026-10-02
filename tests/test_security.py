"""Password hashing, signing, rate limiting and input validation."""
from __future__ import annotations

import unittest

from app import security
from tests.support import StoreTestCase


class PasswordTests(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        first = security.hash_password("a strong passphrase", rounds=1000)
        second = security.hash_password("a strong passphrase", rounds=1000)
        self.assertNotEqual(first, second, "each hash must use a fresh salt")
        self.assertTrue(security.verify_password("a strong passphrase", first))
        self.assertTrue(security.verify_password("a strong passphrase", second))

    def test_wrong_password_rejected(self):
        encoded = security.hash_password("the right one", rounds=1000)
        self.assertFalse(security.verify_password("the wrong one", encoded))
        self.assertFalse(security.verify_password("", encoded))

    def test_malformed_hash_is_rejected_not_raised(self):
        for bad in ("", "nonsense", "md5$1$a$b", "pbkdf2_sha256$x$y", "a$b$c$d"):
            with self.subTest(bad=bad):
                self.assertFalse(security.verify_password("anything", bad))

    def test_password_policy(self):
        self.assertEqual(security.password_problems("a long enough one"), [])
        self.assertTrue(security.password_problems("short"))
        self.assertTrue(security.password_problems("password123"))


class SigningTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(security.unsign(security.sign("hello")), "hello")

    def test_values_containing_dots_survive(self):
        value = "a.b.c.d"
        self.assertEqual(security.unsign(security.sign(value)), value)

    def test_tampering_is_detected(self):
        signed = security.sign("session-id")
        value, _, tag = signed.rpartition(".")
        self.assertIsNone(security.unsign(f"{value}x.{tag}"))
        self.assertIsNone(security.unsign(f"{value}.{tag[:-2]}xx"))
        self.assertIsNone(security.unsign("no-signature"))
        self.assertIsNone(security.unsign(""))

    def test_forged_signature_with_bad_base64(self):
        self.assertIsNone(security.unsign("value.!!!not-base64!!!"))


class ValidationTests(unittest.TestCase):
    def test_email_validation(self):
        for good in ("a@b.co", "first.last+tag@sub.example.com"):
            self.assertTrue(security.valid_email(good), good)
        for bad in ("", "a@b", "no-at.example.com", "a@@b.co", "a b@c.co",
                    "x" * 250 + "@example.com"):
            self.assertFalse(security.valid_email(bad), bad)

    def test_slugify(self):
        self.assertEqual(security.slugify("Élan Vital Tee!!"), "elan-vital-tee")
        self.assertEqual(security.slugify("  multiple   spaces "), "multiple-spaces")
        self.assertEqual(security.slugify("///"), "item")
        self.assertEqual(security.slugify(""), "item")

    def test_constant_time_equals(self):
        self.assertTrue(security.constant_time_equals("abc", "abc"))
        self.assertFalse(security.constant_time_equals("abc", "abd"))
        self.assertFalse(security.constant_time_equals("abc", "ab"))


class RateLimitTests(StoreTestCase):
    def test_bucket_allows_then_blocks(self):
        for attempt in range(3):
            self.assertTrue(
                security.rate_limit("test", limit=3, per_seconds=3600),
                f"attempt {attempt} should be allowed",
            )
        self.assertFalse(security.rate_limit("test", limit=3, per_seconds=3600))

    def test_buckets_are_independent(self):
        self.assertTrue(security.rate_limit("a", limit=1, per_seconds=3600))
        self.assertFalse(security.rate_limit("a", limit=1, per_seconds=3600))
        self.assertTrue(security.rate_limit("b", limit=1, per_seconds=3600))

    def test_clearing_restores_the_bucket(self):
        self.assertTrue(security.rate_limit("c", limit=1, per_seconds=3600))
        self.assertFalse(security.rate_limit("c", limit=1, per_seconds=3600))
        security.clear_rate_limit("c")
        self.assertTrue(security.rate_limit("c", limit=1, per_seconds=3600))


class HmacTests(unittest.TestCase):
    def test_verify_hmac_sha256(self):
        import hashlib
        import hmac
        payload = b"payload"
        expected = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
        self.assertTrue(security.verify_hmac_sha256("secret", payload, expected))
        self.assertFalse(security.verify_hmac_sha256("secret", payload, "0" * 64))
        self.assertFalse(security.verify_hmac_sha256("other", payload, expected))


if __name__ == "__main__":
    unittest.main()
