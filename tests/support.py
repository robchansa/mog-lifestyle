"""Shared fixtures: a clean database per test case, plus factory helpers."""
from __future__ import annotations

import sys
import unittest
from http.cookies import SimpleCookie
from http.client import HTTPMessage
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import accounts, cart as cart_module, catalog, db, seed  # noqa: E402
from app.security import hash_password                            # noqa: E402
from app.web import Request                                       # noqa: E402


class StoreTestCase(unittest.TestCase):
    """Every test starts from an empty, freshly migrated database."""

    seed_catalogue = False

    def setUp(self) -> None:
        db.reset()
        db.migrate()
        if self.seed_catalogue:
            seed.run(quiet=True)
        self.addCleanup(db.close)

    # -- factories ------------------------------------------------------

    def make_product(self, title: str = "Test Tee", *, price_cents: int = 5000,
                     stock: int = 5, sizes: tuple[str, ...] = ("M",),
                     status: str = "active", **extra: Any) -> dict:
        with db.tx():
            product_id = db.insert(
                "products", slug=catalog.unique_slug(title), title=title,
                subtitle=extra.pop("subtitle", "Subtitle"),
                description=extra.pop("description", "A description."),
                details=extra.pop("details", "One\nTwo"),
                price_cents=price_cents, status=status,
                art_seed=extra.pop("art_seed", "tee-0"), **extra,
            )
            variant_ids = []
            for index, size in enumerate(sizes):
                variant_ids.append(db.insert(
                    "variants", product_id=product_id,
                    sku=f"SKU-{product_id}-{size}", size=size, color="Black",
                    stock=stock, position=index,
                ))
        return {"id": product_id, "variants": variant_ids,
                "variant_id": variant_ids[0]}

    def make_user(self, email: str = "shopper@example.com",
                  password: str = "correct horse battery",
                  role: str = "customer") -> Any:
        with db.tx():
            user_id = db.insert(
                "users", email=email, password_hash=hash_password(password),
                name="Test Shopper", role=role,
            )
        return db.one("SELECT * FROM users WHERE id = ?", (user_id,))

    def make_cart(self, *items: tuple[int, int]) -> cart_module.Cart:
        cart_id = cart_module.ensure_cart(None)
        for variant_id, quantity in items:
            cart_module.add(cart_id, variant_id, quantity)
        return cart_module.load(cart_id)

    def make_request(self, method: str = "GET", path: str = "/", *,
                     form: dict[str, str] | None = None,
                     query: dict[str, list[str]] | None = None) -> Request:
        headers = HTTPMessage()
        body = b""
        if form is not None:
            import urllib.parse
            body = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return Request(
            method=method, path=path, query=query or {}, headers=headers,
            body=body, cookies=SimpleCookie(), remote_addr="127.0.0.1",
        )
