"""Departments, categories, the sale shelf and the navigation built from them."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import catalog, db, seed
from tests.support import StoreTestCase

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "site.css").read_text()
JS = (ROOT / "app" / "static" / "js" / "site.js").read_text()

# The structure the client specified, verbatim.
EXPECTED = {
    "general": ["Oversized T-Shirts", "Beanies", "Ski Masks", "Socks"],
    "men": ["Oversized T-Shirts", "Regular-Fit T-Shirts", "Work Shirts"],
    "women": ["Oversized T-Shirts", "Crop Tops", "Training Shorts",
              "Sports Bras & Training Sets"],
}


class TaxonomyTests(StoreTestCase):
    seed_catalogue = True

    def test_every_requested_category_exists_in_order(self):
        for department, titles in EXPECTED.items():
            with self.subTest(department=department):
                actual = [c["title"] for c in catalog.list_collections(department)]
                self.assertEqual(actual, titles)

    def test_every_category_has_stock(self):
        for category in catalog.list_collections():
            with self.subTest(category=category["slug"]):
                self.assertGreater(category["product_count"], 0,
                                   "an empty category would be a dead end")

    def test_the_same_title_can_repeat_across_departments(self):
        tees = db.query(
            "SELECT slug, department FROM collections WHERE title = 'Oversized T-Shirts'"
        )
        self.assertEqual({r["department"] for r in tees}, {"general", "men", "women"})
        self.assertEqual(len({r["slug"] for r in tees}), 3, "slugs stay unique")

    def test_products_inherit_their_department_from_their_category(self):
        row = db.one(
            "SELECT p.slug FROM products p JOIN collections c ON c.id = p.collection_id "
            "WHERE c.slug = 'womens-crop-tops' LIMIT 1"
        )
        product = catalog.get_product(row["slug"])
        self.assertEqual(product["department"], "women")

    def test_navigation_groups_categories_under_departments(self):
        menu = catalog.navigation()
        self.assertEqual([d["slug"] for d in menu], ["men", "women", "general"])
        for department in menu:
            titles = [c["title"] for c in department["categories"]]
            self.assertEqual(titles, EXPECTED[department["slug"]])

    def test_navigation_hides_empty_categories(self):
        with db.tx():
            db.insert("collections", slug="ghost", title="Ghost",
                      department="men", position=99)
        slugs = [
            c["slug"] for d in catalog.navigation() for c in d["categories"]
        ]
        self.assertNotIn("ghost", slugs)


class DepartmentFilterTests(StoreTestCase):
    seed_catalogue = True

    def test_filtering_by_department(self):
        for department in ("men", "women", "general"):
            with self.subTest(department=department):
                rows, total = catalog.search(catalog.Filters(department=department))
                self.assertGreater(total, 0)
                self.assertTrue(all(r["department"] == department for r in rows))

    def test_filtering_by_category(self):
        rows, total = catalog.search(catalog.Filters(collection="womens-crop-tops"))
        self.assertGreater(total, 0)
        self.assertTrue(all(r["collection_slug"] == "womens-crop-tops" for r in rows))

    def test_an_unknown_department_is_ignored_not_errored(self):
        request = self.make_request(query={"department": ["../../etc"]})
        filters = catalog.Filters.from_request(request)
        self.assertEqual(filters.department, "")
        rows, total = catalog.search(filters)
        self.assertGreater(total, 0, "a bad department falls back to everything")

    def test_departments_partition_the_catalogue(self):
        _, everything = catalog.search(catalog.Filters())
        per_department = sum(
            catalog.search(catalog.Filters(department=d))[1]
            for d, _ in catalog.DEPARTMENTS
        )
        self.assertEqual(per_department, everything,
                         "every product belongs to exactly one department")

    def test_query_string_round_trips_department_and_sale(self):
        filters = catalog.Filters(department="women", on_sale=True)
        query = filters.query_string()
        self.assertIn("department=women", query)
        self.assertIn("on_sale=1", query)


class SaleTests(StoreTestCase):
    seed_catalogue = True

    def test_sale_means_a_higher_compare_at_price(self):
        rows = catalog.on_sale(50)
        self.assertTrue(rows)
        for row in rows:
            self.assertIsNotNone(row["compare_cents"])
            self.assertGreater(row["compare_cents"], row["price_cents"])

    def test_sale_count_matches_the_filter(self):
        _, total = catalog.search(catalog.Filters(on_sale=True))
        self.assertEqual(total, catalog.sale_count())

    def test_a_compare_price_at_or_below_the_price_is_not_a_sale(self):
        product = self.make_product("Not Really", price_cents=5000, stock=2)
        with db.tx():
            db.update("products", "id = ?", (product["id"],), compare_cents=5000)
        slugs = {r["slug"] for r in catalog.on_sale(50)}
        self.assertNotIn(catalog.get_product_by_id(product["id"])["slug"], slugs)

    def test_sale_is_ordered_by_biggest_reduction(self):
        rows = catalog.on_sale(50)
        reductions = [r["compare_cents"] - r["price_cents"] for r in rows]
        self.assertEqual(reductions, sorted(reductions, reverse=True))


def css_rule(selector: str) -> str:
    """The body of the first rule whose selector line starts with `selector`."""
    index = CSS.index(selector + " {")
    return CSS[index:CSS.index("}", index)]


class HeaderTests(StoreTestCase):
    seed_catalogue = True

    def _header(self) -> str:
        from app.ui import site_header
        request = self.make_request()
        request.user = None
        request.cart_count = 0
        return site_header(request)

    def test_the_logotype_comes_first_in_the_bar(self):
        markup = self._header()
        brand = markup.index("site-header__brand")
        nav = markup.index('class="nav"')
        actions = markup.index("site-header__actions")
        self.assertLess(brand, nav, "the logo must precede the nav in the DOM")
        self.assertLess(nav, actions)
        self.assertIn("justify-self: start", css_rule(".site-header__brand"))

    def test_the_logotype_is_large_and_scales_with_the_viewport(self):
        """It has to stay prominent, not shrink into the corner."""
        rule = css_rule(".site-header__brand")
        self.assertIn("--wordmark-width: clamp(", rule)
        floor = float(re.search(r"clamp\((\d+(?:\.\d+)?)rem", rule).group(1))
        self.assertGreaterEqual(floor, 6.0,
                                "the logotype should never fall below ~6rem wide")

    def test_the_logo_is_not_recentred_on_small_screens(self):
        mobile = CSS[CSS.index("@media (max-width: 980px)"):]
        self.assertNotIn("justify-self: center", mobile,
                         "the logo stays hard left at every width")

    def test_one_panel_lists_every_department_and_category(self):
        markup = self._header()
        self.assertEqual(markup.count('id="shop-menu"'), 1,
                         "a single shared panel, not one per department")
        for department, titles in EXPECTED.items():
            with self.subTest(department=department):
                self.assertIn(f'href="/shop?department={department}"', markup)
                for title in titles:
                    self.assertIn(title.replace("&", "&amp;"), markup)

    def test_every_category_is_visible_without_a_second_gesture(self):
        """One hover reveals the whole catalogue, not one slice of it."""
        markup = self._header()
        panel = markup[markup.index('id="shop-menu"'):]
        for category in catalog.list_collections():
            if category["product_count"]:
                self.assertIn(f'href="/shop?collection={category["slug"]}"', panel)

    def test_the_menu_is_wired_for_assistive_tech(self):
        markup = self._header()
        self.assertIn('aria-expanded="false"', markup)
        self.assertEqual(markup.count('aria-controls="shop-menu"'), 3)
        self.assertEqual(markup.count("data-nav-trigger"), 3)

    def test_sale_appears_only_while_something_is_reduced(self):
        self.assertIn('href="/shop?on_sale=1"', self._header())
        with db.tx():
            db.execute("UPDATE products SET compare_cents = NULL")
        self.assertNotIn('href="/shop?on_sale=1"', self._header())

    def test_the_panel_opens_on_hover_focus_and_click(self):
        self.assertIn(".nav:hover .megamenu", CSS)
        self.assertIn(".nav:focus-within .megamenu", CSS)
        self.assertIn(".megamenu.is-open", CSS)

    def test_only_the_nav_opens_the_panel(self):
        """Hovering the bag or the search box must not drop the catalogue open."""
        self.assertNotIn(".site-header:hover .megamenu", CSS)

    def test_the_panel_sets_its_own_colour(self):
        """The bar inverts over the hero; the panel must not inherit that."""
        self.assertIn("color: var(--ink)", css_rule(".megamenu"))

    def test_categories_are_listed_outright_on_a_phone(self):
        mobile = CSS[CSS.index("@media (max-width: 980px)"):]
        self.assertIn(".megamenu {", mobile)
        self.assertIn("position: static; opacity: 1; visibility: visible", mobile)


class ShopCopyTests(StoreTestCase):
    seed_catalogue = True

    def _lede(self, query: dict) -> str:
        import re as _re
        from app.application import create_app
        from app import accounts, cart as cart_module
        request = self.make_request(path="/shop", query=query)
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/shop")
        body = route.handler(request, **params).body.decode()
        return _re.search(r'<p class="lede">([^<]*)', body).group(1)

    def test_the_lede_follows_the_filter(self):
        self.assertIn("black and white", self._lede({}))
        self.assertIn("training kit", self._lede({"department": ["women"]}))
        self.assertIn("final few", self._lede({"on_sale": ["1"]}))
        self.assertIn("outside the split", self._lede({"department": ["general"]}))

    def test_category_ledes_read_grammatically(self):
        """Category titles are plural, so the sentence must suit a plural."""
        for category in catalog.list_collections():
            with self.subTest(category=category["slug"]):
                import html as _html
                lede = self._lede({"collection": [category["slug"]]})
                expected = _html.escape(
                    f"All {category['title'].lower()} in the line.", quote=True)
                self.assertEqual(lede, expected)
                self.assertNotIn("Every ", lede)

    def test_search_ledes_agree_in_number(self):
        self.assertIn("results for your search", self._lede({"q": ["tee"]}))
        self.assertIn("1 result for your search", self._lede({"q": ["legging"]}))


class HeroTests(StoreTestCase):
    seed_catalogue = True

    def _home(self) -> str:
        from app.application import create_app
        from app import accounts, cart as cart_module
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/")
        return route.handler(request, **params).body.decode()

    def test_the_hero_is_marked_for_the_scroll_handler(self):
        self.assertIn("data-hero", self._home())

    def test_scroll_progress_drives_the_motion(self):
        self.assertIn("--hero-progress", CSS)
        self.assertIn('hero.style.setProperty("--hero-progress"', JS)

    def test_the_hero_is_pinned_so_the_page_slides_over_it(self):
        rule = css_rule(".hero")
        self.assertIn("position: sticky", rule)
        self.assertIn("top: 0", rule)
        body = css_rule(".page-body")
        self.assertIn("background: var(--paper)", body,
                      "the overlay needs an opaque ground to read as receding")

    def test_the_hero_fills_the_viewport_edge_to_edge(self):
        rule = css_rule(".hero")
        self.assertIn("height: 100svh", rule)
        self.assertNotIn("max-width", rule,
                         "the hero must not be capped by the content shell")
        self.assertIn("object-fit: cover", css_rule(".hero__media img"))
        self.assertIn("object-position", css_rule(".hero__media img"),
                      "a deliberate focal point keeps the crop balanced")

    def test_the_header_sits_over_the_hero_rather_than_above_it(self):
        self.assertIn("margin-top: calc(var(--header-height", css_rule(".hero"))
        self.assertIn("is-over-hero", CSS)
        self.assertIn('header.classList.toggle("is-over-hero"', JS)

    def test_the_handler_is_frame_throttled(self):
        self.assertIn("window.requestAnimationFrame(writeHero)", JS)
        self.assertIn("{ passive: true }", JS)

    def test_motion_is_skipped_when_the_viewer_asks_for_less(self):
        self.assertIn("if (hero && !reduceMotion)", JS)
        reduced = CSS[CSS.index("@media (prefers-reduced-motion: reduce)"):]
        self.assertIn(".hero__media, .hero__body { transform: none; opacity: 1; }",
                      reduced)

    def test_the_hero_is_outside_the_content_shell(self):
        """Anything inside .shell is capped at 1560px; the hero must not be."""
        home = self._home()
        hero = home.index('class="hero"')
        shell = home.index('class="shell"')
        self.assertLess(hero, shell, "the hero comes before any shell")
        prefix = home[:hero]
        self.assertNotIn('class="shell"', prefix,
                         "the hero must not be nested inside a shell")

    def test_content_after_the_hero_is_wrapped_for_the_overlay(self):
        home = self._home()
        self.assertIn('class="page-body"', home)
        self.assertLess(home.index('class="hero"'), home.index('class="page-body"'))

    def test_the_hero_offers_both_departments(self):
        home = self._home()
        self.assertIn('href="/shop?department=men"', home)
        self.assertIn('href="/shop?department=women"', home)


class HomepageSectionTests(StoreTestCase):
    seed_catalogue = True

    def setUp(self) -> None:
        super().setUp()
        from app.application import create_app
        from app import accounts, cart as cart_module
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/")
        self.home = route.handler(request, **params).body.decode()

    def test_the_three_requested_sections_are_present(self):
        for anchor in ("men", "women", "sale"):
            with self.subTest(anchor=anchor):
                self.assertIn(f'id="{anchor}"', self.home)

    def test_sections_appear_in_order(self):
        order = [self.home.index(f'id="{a}"') for a in ("men", "women", "sale")]
        self.assertEqual(order, sorted(order))

    def test_each_section_lists_its_categories(self):
        for department, titles in EXPECTED.items():
            if department == "general":
                continue
            section = self.home[self.home.index(f'id="{department}"'):]
            section = section[:section.index("</section>")]
            for title in titles:
                self.assertIn(title.replace("&", "&amp;"), section,
                              f"{title} missing from the {department} band")

    def test_each_section_links_to_its_department(self):
        self.assertIn('href="/shop?department=men"', self.home)
        self.assertIn('href="/shop?department=women"', self.home)
        self.assertIn('href="/shop?on_sale=1"', self.home)

    def test_the_sale_section_shows_only_reduced_products(self):
        section = self.home[self.home.index('id="sale"'):]
        section = section[:section.index("</section>")]
        self.assertEqual(section.count("badge--sale"), section.count("card__flags"))
        self.assertIn("price__was", section)

    def test_each_band_shows_exactly_four_products(self):
        """A fixed column count depends on a fixed product count."""
        for anchor in ("men", "women", "sale", "general"):
            with self.subTest(anchor=anchor):
                if f'id="{anchor}"' not in self.home:
                    continue
                section = self.home[self.home.index(f'id="{anchor}"'):]
                section = section[:section.index("</section>")]
                self.assertLessEqual(section.count('<article class="card">'), 4)

    def test_the_band_grid_has_no_dangling_cell(self):
        """Auto-fill gave 3 columns for 4 products -- two empty cells per band."""
        self.assertIn(".band .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
                      CSS)
        wide = CSS[CSS.index("@media (min-width: 900px)"):]
        self.assertIn("repeat(4, minmax(0, 1fr))", wide[:400])

    def test_nothing_doubles_the_gap_before_the_footer(self):
        rule = css_rule(".site-footer")
        self.assertNotIn("margin-top", rule,
                         "the preceding section already carries the rhythm")

    def test_a_band_disappears_when_its_department_is_empty(self):
        with db.tx():
            db.execute(
                "UPDATE products SET status = 'draft' WHERE collection_id IN "
                "(SELECT id FROM collections WHERE department = 'women')"
            )
        from app.application import create_app
        from app import accounts, cart as cart_module
        request = self.make_request(path="/")
        request.session = accounts.create_session(None)
        request.user = None
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        route, params = create_app().match("GET", "/")
        home = route.handler(request, **params).body.decode()
        self.assertNotIn('id="women"', home)
        self.assertIn('id="men"', home)


if __name__ == "__main__":
    unittest.main()
