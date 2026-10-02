"""Registration, authentication, sessions and addresses."""
from __future__ import annotations

import unittest

from app import accounts, db
from app.security import clear_rate_limit
from tests.support import StoreTestCase


class RegistrationTests(StoreTestCase):
    def test_register_creates_a_customer(self):
        user = accounts.register("new@example.com", "a good long passphrase",
                                 name="New Person")
        self.assertEqual(user["email"], "new@example.com")
        self.assertEqual(user["role"], "customer")
        self.assertNotIn("a good long passphrase", user["password_hash"])

    def test_duplicate_email_is_refused_case_insensitively(self):
        accounts.register("dup@example.com", "a good long passphrase")
        with self.assertRaises(accounts.AuthError):
            accounts.register("DUP@example.com", "another long passphrase")

    def test_invalid_email_is_refused(self):
        with self.assertRaises(accounts.AuthError):
            accounts.register("not-an-email", "a good long passphrase")

    def test_weak_password_is_refused(self):
        with self.assertRaises(accounts.AuthError):
            accounts.register("weak@example.com", "short")

    def test_opting_in_subscribes_to_the_list(self):
        accounts.register("keen@example.com", "a good long passphrase",
                          marketing_opt_in=True)
        self.assertIsNotNone(
            db.one("SELECT 1 FROM newsletter WHERE email = ?", ("keen@example.com",))
        )


class AuthenticationTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.password = "a good long passphrase"
        self.user = accounts.register("shopper@example.com", self.password)
        clear_rate_limit("login:127.0.0.1")

    def test_correct_credentials_authenticate(self):
        user = accounts.authenticate("shopper@example.com", self.password,
                                     ip="127.0.0.1")
        self.assertEqual(user["id"], self.user["id"])

    def test_email_is_case_insensitive(self):
        self.assertIsNotNone(
            accounts.authenticate("SHOPPER@example.com", self.password, ip="127.0.0.1")
        )

    def test_wrong_password_is_refused(self):
        with self.assertRaises(accounts.AuthError):
            accounts.authenticate("shopper@example.com", "wrong", ip="127.0.0.1")

    def test_unknown_email_gives_the_same_message(self):
        with self.assertRaises(accounts.AuthError) as missing:
            accounts.authenticate("nobody@example.com", "whatever", ip="127.0.0.1")
        with self.assertRaises(accounts.AuthError) as wrong:
            accounts.authenticate("shopper@example.com", "wrong", ip="127.0.0.1")
        self.assertEqual(str(missing.exception), str(wrong.exception),
                         "the error must not reveal whether the account exists")

    def test_repeated_failures_lock_the_account(self):
        for _ in range(accounts.MAX_FAILED_LOGINS):
            with self.assertRaises(accounts.AuthError):
                accounts.authenticate("shopper@example.com", "wrong", ip="1.2.3.4")
        with self.assertRaises(accounts.AuthError) as caught:
            accounts.authenticate("shopper@example.com", self.password, ip="1.2.3.4")
        self.assertIn("locked", str(caught.exception).lower())

    def test_a_successful_login_clears_the_failure_counter(self):
        with self.assertRaises(accounts.AuthError):
            accounts.authenticate("shopper@example.com", "wrong", ip="127.0.0.1")
        accounts.authenticate("shopper@example.com", self.password, ip="127.0.0.1")
        self.assertEqual(
            db.scalar("SELECT failed_logins FROM users WHERE id = ?", (self.user["id"],)),
            0,
        )

    def test_login_is_rate_limited_per_ip(self):
        for _ in range(12):
            try:
                accounts.authenticate("nobody@example.com", "x", ip="9.9.9.9")
            except accounts.AuthError:
                pass
        with self.assertRaises(accounts.AuthError) as caught:
            accounts.authenticate("shopper@example.com", self.password, ip="9.9.9.9")
        self.assertIn("too many", str(caught.exception).lower())


class SessionTests(StoreTestCase):
    def test_session_round_trip_through_a_signed_cookie(self):
        session = accounts.create_session(None)
        cookie = accounts.session_cookie_value(session)
        loaded = accounts.load_session(cookie)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["id"], session["id"])

    def test_a_tampered_cookie_is_rejected(self):
        session = accounts.create_session(None)
        cookie = accounts.session_cookie_value(session)
        self.assertIsNone(accounts.load_session(cookie.replace(".", "x.", 1)))
        self.assertIsNone(accounts.load_session(session["id"]),
                          "an unsigned raw id must not be accepted")
        self.assertIsNone(accounts.load_session(None))

    def test_expired_sessions_are_not_loaded(self):
        session = accounts.create_session(None)
        with db.tx():
            db.update("sessions", "id = ?", (session["id"],),
                      expires_at="2000-01-01 00:00:00")
        self.assertIsNone(
            accounts.load_session(accounts.session_cookie_value(session))
        )

    def test_rotation_issues_a_new_id_and_kills_the_old(self):
        user = self.make_user()
        original = accounts.create_session(None)
        rotated = accounts.rotate_session(original, user["id"])
        self.assertNotEqual(rotated["id"], original["id"])
        self.assertEqual(rotated["user_id"], user["id"])
        self.assertIsNone(db.one("SELECT 1 FROM sessions WHERE id = ?",
                                 (original["id"],)))

    def test_every_session_gets_a_distinct_csrf_token(self):
        tokens = {accounts.create_session(None)["csrf_token"] for _ in range(5)}
        self.assertEqual(len(tokens), 5)

    def test_purge_expired(self):
        session = accounts.create_session(None)
        with db.tx():
            db.update("sessions", "id = ?", (session["id"],),
                      expires_at="2000-01-01 00:00:00")
        self.assertEqual(accounts.purge_expired(), 1)


class PasswordChangeTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.password = "a good long passphrase"
        self.user = accounts.register("shopper@example.com", self.password)

    def test_changing_a_password_requires_the_current_one(self):
        with self.assertRaises(accounts.AuthError):
            accounts.change_password(self.user["id"], "wrong", "a different passphrase")

    def test_a_weak_replacement_is_refused(self):
        with self.assertRaises(accounts.AuthError):
            accounts.change_password(self.user["id"], self.password, "short")

    def test_changing_a_password_revokes_every_session(self):
        accounts.create_session(self.user["id"])
        accounts.create_session(self.user["id"])
        accounts.change_password(self.user["id"], self.password, "a brand new phrase")
        self.assertEqual(
            db.scalar("SELECT count(*) FROM sessions WHERE user_id = ?",
                      (self.user["id"],)),
            0,
        )
        clear_rate_limit("login:127.0.0.1")
        self.assertIsNotNone(
            accounts.authenticate("shopper@example.com", "a brand new phrase",
                                  ip="127.0.0.1")
        )


class AddressTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = self.make_user()

    def _save(self, city: str = "Boise", **kw):
        return accounts.save_address(
            self.user["id"], name="Robert Chansa", line1="1 Training Way",
            line2="", city=city, region="ID", postal="83702", country="US",
            phone="", **kw,
        )

    def test_the_first_address_becomes_the_default(self):
        self._save()
        self.assertEqual(accounts.default_address(self.user["id"])["city"], "Boise")

    def test_saving_a_new_default_demotes_the_previous_one(self):
        self._save("Boise")
        self._save("Denver")
        self.assertEqual(accounts.default_address(self.user["id"])["city"], "Denver")
        defaults = [a for a in accounts.addresses_for(self.user["id"]) if a["is_default"]]
        self.assertEqual(len(defaults), 1)

    def test_delete_only_touches_the_owner(self):
        address_id = self._save()
        other = self.make_user("other@example.com")
        accounts.delete_address(other["id"], address_id)
        self.assertEqual(len(accounts.addresses_for(self.user["id"])), 1,
                         "another user must not be able to delete this address")
        accounts.delete_address(self.user["id"], address_id)
        self.assertEqual(accounts.addresses_for(self.user["id"]), [])

    def test_no_default_for_anonymous(self):
        self.assertIsNone(accounts.default_address(None))


if __name__ == "__main__":
    unittest.main()
