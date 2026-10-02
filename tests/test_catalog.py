"""Catalogue search, filtering and the inventory state machine."""
from __future__ import annotations

import unittest

from app import catalog, db
from tests.support import StoreTestCase


class InventoryTests(StoreTestCase):
    def test_reserve_moves_stock_out_of_available(self):
        product = self.make_product(stock=10)
        variant_id = product["variant_id"]
        with db.tx():
            catalog.reserve(variant_id, 3)
        row = catalog.get_variant(variant_id)
        self.assertEqual(row["stock"], 10, "on-hand stock is untouched by a reservation")
        self.assertEqual(row["reserved"], 3)
        self.assertEqual(row["available"], 7)

    def test_cannot_reserve_more_than_available(self):
        product = self.make_product(stock=2)
        with self.assertRaises(catalog.OutOfStock) as caught:
            with db.tx():
                catalog.reserve(product["variant_id"], 3)
        self.assertEqual(caught.exception.available, 2)
        self.assertEqual(caught.exception.requested, 3)
        self.assertEqual(catalog.get_variant(product["variant_id"])["reserved"], 0)

    def test_two_reservations_cannot_oversell_the_last_unit(self):
        product = self.make_product(stock=1)
        variant_id = product["variant_id"]
        with db.tx():
            catalog.reserve(variant_id, 1)
        with self.assertRaises(catalog.OutOfStock):
            with db.tx():
                catalog.reserve(variant_id, 1)
        self.assertEqual(catalog.get_variant(variant_id)["reserved"], 1)

    def test_release_returns_stock(self):
        product = self.make_product(stock=5)
        variant_id = product["variant_id"]
        with db.tx():
            catalog.reserve(variant_id, 2)
            catalog.release(variant_id, 2)
        row = catalog.get_variant(variant_id)
        self.assertEqual((row["stock"], row["reserved"], row["available"]), (5, 0, 5))

    def test_release_never_goes_negative(self):
        product = self.make_product(stock=5)
        with db.tx():
            catalog.release(product["variant_id"], 99)
        self.assertEqual(catalog.get_variant(product["variant_id"])["reserved"], 0)

    def test_commit_depletes_on_hand_stock(self):
        product = self.make_product(stock=5)
        variant_id = product["variant_id"]
        with db.tx():
            catalog.reserve(variant_id, 2)
            catalog.commit_reservation(variant_id, 2)
        row = catalog.get_variant(variant_id)
        self.assertEqual((row["stock"], row["reserved"], row["available"]), (3, 0, 3))

    def test_restock_adds_units(self):
        product = self.make_product(stock=1)
        with db.tx():
            catalog.restock(product["variant_id"], 4)
        self.assertEqual(catalog.get_variant(product["variant_id"])["stock"], 5)

    def test_low_stock_report(self):
        self.make_product("Plenty", stock=50)
        self.make_product("Scarce", stock=1)
        titles = {row["title"] for row in catalog.low_stock()}
        self.assertIn("Scarce", titles)
        self.assertNotIn("Plenty", titles)


class SearchTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        with db.tx():
            self.apparel = db.insert("collections", slug="apparel", title="Apparel")
            self.training = db.insert("collections", slug="training", title="Training")
        self.make_product("Atlas Hoodie", price_cents=14800, stock=5,
                          sizes=("S", "M", "L"), collection_id=self.apparel,
                          description="A heavyweight loopback hoodie.")
        self.make_product("Range Short", price_cents=7400, stock=4,
                          sizes=("M", "L"), collection_id=self.training,
                          description="Lined training shorts with stretch.")
        self.make_product("Sold Out Tee", price_cents=6800, stock=0,
                          sizes=("M",), collection_id=self.apparel,
                          description="A plain cotton tee.")
        self.make_product("Hidden Draft", price_cents=1000, stock=9,
                          status="draft", collection_id=self.apparel)

    def _search(self, **kwargs):
        return catalog.search(catalog.Filters(**kwargs))

    def test_drafts_are_hidden_from_the_storefront(self):
        rows, total = self._search()
        self.assertEqual(total, 3)
        self.assertNotIn("Hidden Draft", {r["title"] for r in rows})

    def test_drafts_visible_to_admin(self):
        rows, total = catalog.search(catalog.Filters(), include_drafts=True)
        self.assertEqual(total, 4)

    def test_full_text_search_matches_description(self):
        rows, total = self._search(q="loopback")
        self.assertEqual([r["title"] for r in rows], ["Atlas Hoodie"])

    def test_search_matches_a_prefix(self):
        rows, _ = self._search(q="hood")
        self.assertIn("Atlas Hoodie", {r["title"] for r in rows})

    def test_search_with_no_match_returns_nothing(self):
        rows, total = self._search(q="snowboard")
        self.assertEqual((rows, total), ([], 0))

    def test_search_tolerates_punctuation(self):
        rows, total = self._search(q='"; DROP TABLE products; --')
        self.assertEqual(total, 0)
        self.assertIsNotNone(db.one("SELECT 1 FROM products LIMIT 1"),
                             "the products table must still exist")

    def test_collection_filter(self):
        rows, total = self._search(collection="training")
        self.assertEqual([r["title"] for r in rows], ["Range Short"])

    def test_size_filter(self):
        rows, _ = self._search(sizes=("S",))
        self.assertEqual({r["title"] for r in rows}, {"Atlas Hoodie"})

    def test_in_stock_filter_excludes_sold_out(self):
        rows, _ = self._search(in_stock=True)
        self.assertNotIn("Sold Out Tee", {r["title"] for r in rows})

    def test_max_price_filter(self):
        rows, _ = self._search(max_price=8000)
        self.assertEqual({r["title"] for r in rows}, {"Range Short", "Sold Out Tee"})

    def test_sorting(self):
        rows, _ = self._search(sort="price-asc")
        prices = [r["price_cents"] for r in rows]
        self.assertEqual(prices, sorted(prices))
        rows, _ = self._search(sort="price-desc")
        prices = [r["price_cents"] for r in rows]
        self.assertEqual(prices, sorted(prices, reverse=True))

    def test_stock_column_is_sellable_not_on_hand(self):
        product = self.make_product("Reserved Item", stock=5, sizes=("M",))
        with db.tx():
            catalog.reserve(product["variant_id"], 2)
        row = catalog.get_product_by_id(product["id"])
        self.assertEqual(row["stock"], 3)

    def test_pagination(self):
        rows, total = catalog.search(catalog.Filters(page=1), page_size=2)
        self.assertEqual((len(rows), total), (2, 3))
        rows, _ = catalog.search(catalog.Filters(page=2), page_size=2)
        self.assertEqual(len(rows), 1)

    def test_facets_are_ordered_by_size(self):
        facets = catalog.facets()
        self.assertEqual(facets["sizes"], ["S", "M", "L"])


class FiltersTests(StoreTestCase):
    def test_from_request_clamps_and_whitelists(self):
        request = self.make_request(query={
            "q": ["x" * 200], "sort": ["; DROP TABLE"], "page": ["-4"],
            "size": ["m", "l"], "max_price": ["-10"],
        })
        filters = catalog.Filters.from_request(request)
        self.assertEqual(len(filters.q), 80)
        self.assertEqual(filters.sort, "featured", "unknown sorts fall back")
        self.assertEqual(filters.page, 1, "page is never below 1")
        self.assertEqual(filters.sizes, ("M", "L"))
        self.assertEqual(filters.max_price, 0)

    def test_query_string_round_trip(self):
        filters = catalog.Filters(q="tee", collection="apparel",
                                  sizes=("M",), sort="new")
        query = filters.query_string()
        self.assertIn("q=tee", query)
        self.assertIn("collection=apparel", query)
        self.assertIn("size=M", query)
        self.assertIn("sort=new", query)


class SlugTests(StoreTestCase):
    def test_slugs_are_unique(self):
        first = self.make_product("Atlas Hoodie")
        second = self.make_product("Atlas Hoodie")
        slugs = {
            catalog.get_product_by_id(first["id"])["slug"],
            catalog.get_product_by_id(second["id"])["slug"],
        }
        self.assertEqual(slugs, {"atlas-hoodie", "atlas-hoodie-2"})

    def test_a_product_keeps_its_own_slug_when_renamed_to_itself(self):
        product = self.make_product("Atlas Hoodie")
        self.assertEqual(
            catalog.unique_slug("Atlas Hoodie", exclude_id=product["id"]),
            "atlas-hoodie",
        )


class VariantSyncTests(StoreTestCase):
    def test_upsert_preserves_stock_for_existing_skus(self):
        product = self.make_product("Tee", stock=7, sizes=("M",))
        sku = catalog.get_variant(product["variant_id"])["sku"]
        catalog.upsert_variants(product["id"], [
            {"sku": sku, "size": "M", "color": "Black", "stock": 7},
            {"sku": "NEW-L", "size": "L", "color": "Black", "stock": 4},
        ])
        variants = catalog.variants_for(product["id"])
        self.assertEqual({v["sku"] for v in variants}, {sku, "NEW-L"})
        self.assertEqual({v["size"]: v["stock"] for v in variants}["L"], 4)

    def test_removed_skus_are_deleted_when_nothing_is_reserved(self):
        product = self.make_product("Tee", stock=3, sizes=("M", "L"))
        keep = catalog.get_variant(product["variants"][0])["sku"]
        catalog.upsert_variants(product["id"],
                                [{"sku": keep, "size": "M", "stock": 3}])
        self.assertEqual(len(catalog.variants_for(product["id"])), 1)

    def test_reserved_variants_are_never_deleted(self):
        product = self.make_product("Tee", stock=3, sizes=("M", "L"))
        with db.tx():
            catalog.reserve(product["variants"][1], 1)
        keep = catalog.get_variant(product["variants"][0])["sku"]
        catalog.upsert_variants(product["id"],
                                [{"sku": keep, "size": "M", "stock": 3}])
        self.assertEqual(len(catalog.variants_for(product["id"])), 2,
                         "a variant with live reservations must survive")


if __name__ == "__main__":
    unittest.main()
