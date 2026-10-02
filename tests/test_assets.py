"""Brand assets, product imagery resolution, and the PNG pipeline."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from app import catalog, db
from tests.support import StoreTestCase

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "app" / "static" / "brand"
IMG = ROOT / "app" / "static" / "img"
sys.path.insert(0, str(ROOT / "tools"))


class SuppliedAssetTests(unittest.TestCase):
    def test_brand_assets_exist(self):
        for name in ("wordmark.png", "monogram.png", "favicon.png", "og.jpg"):
            with self.subTest(name=name):
                path = BRAND / name
                self.assertTrue(path.is_file(), name)
                self.assertGreater(path.stat().st_size, 1000, name)

    def test_campaign_and_product_photography_exist(self):
        for name in ("campaign-wide.jpg", "campaign-portrait.jpg",
                     "campaign-tall.jpg", "mog-tee-front.jpg",
                     "mog-tee-back.jpg", "lifestyle-tee-front.jpg",
                     "lifestyle-tee-back.jpg"):
            with self.subTest(name=name):
                self.assertTrue((IMG / name).is_file(), name)

    def test_the_logotype_is_a_transparent_mask(self):
        """It is painted with currentColor, so it must carry real alpha."""
        from pngkit import decode
        image = decode((BRAND / "wordmark.png").read_bytes())
        self.assertEqual(image.channels, 4, "wordmark must be RGBA")
        alphas = {image.pixels[i * 4 + 3] for i in range(image.width * image.height)}
        self.assertIn(0, alphas, "the mask needs fully transparent pixels")
        self.assertTrue(any(a > 240 for a in alphas), "…and fully opaque ones")

    def test_the_logotype_keeps_its_aspect_ratio(self):
        """The stylesheet hard-codes 1087/304; the asset must agree."""
        from pngkit import decode
        image = decode((BRAND / "wordmark.png").read_bytes())
        css = (ROOT / "app" / "static" / "css" / "site.css").read_text()
        self.assertIn("aspect-ratio: 1087 / 304", css)
        self.assertAlmostEqual(image.width / image.height, 1087 / 304, places=1)

    def test_product_photography_is_four_by_five(self):
        from pngkit import decode
        import subprocess
        for name in ("mog-tee-front.jpg", "lifestyle-tee-back.jpg"):
            out = subprocess.run(
                ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(IMG / name)],
                capture_output=True, text=True,
            ).stdout
            width = int(out.split("pixelWidth:")[1].split()[0])
            height = int(out.split("pixelHeight:")[1].split()[0])
            with self.subTest(name=name):
                self.assertAlmostEqual(width / height, 0.8, places=2)


class PngKitTests(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        from pngkit import Image, decode, encode
        pixels = bytearray()
        for y in range(4):
            for x in range(6):
                pixels += bytes((x * 40, y * 60, 128, 255))
        original = Image(6, 4, 4, pixels)
        restored = decode(encode(original))
        self.assertEqual((restored.width, restored.height, restored.channels),
                         (6, 4, 4))
        self.assertEqual(restored.pixels, original.pixels)

    def test_crop(self):
        from pngkit import Image
        pixels = bytearray()
        for y in range(4):
            for x in range(4):
                pixels += bytes((x, y, 0))
        cropped = Image(4, 4, 3, pixels).crop(1, 1, 3, 3)
        self.assertEqual((cropped.width, cropped.height), (2, 2))
        self.assertEqual(cropped.at(0, 0), (1, 1, 0))
        self.assertEqual(cropped.at(1, 1), (2, 2, 0))

    def test_crop_clamps_to_bounds(self):
        from pngkit import Image
        image = Image(4, 4, 3, bytearray(4 * 4 * 3))
        cropped = image.crop(-5, -5, 99, 99)
        self.assertEqual((cropped.width, cropped.height), (4, 4))

    def test_alpha_mask_from_dark_on_light(self):
        from pngkit import Image, to_alpha_mask
        # Two pixels: paper, then ink.
        pixels = bytearray((241, 237, 231, 20, 20, 18))
        mask = to_alpha_mask(Image(2, 1, 3, pixels), background=(241, 237, 231))
        self.assertLess(mask.pixels[3], 8, "paper should be transparent")
        self.assertGreater(mask.pixels[7], 245, "ink should be opaque")

    def test_frame_to_ratio_stays_inside_its_half(self):
        from build_assets import frame_to_ratio
        left, top, right, bottom = frame_to_ratio(
            (60, 130, 740, 880), ratio=0.8, bounds=(1536, 1024),
            pad=0.10, x_range=(0, 765),
        )
        self.assertGreaterEqual(left, 0)
        self.assertLessEqual(right, 765, "must not reach into the next garment")
        self.assertAlmostEqual((right - left) / (bottom - top), 0.8, places=2)


class ImageResolutionTests(StoreTestCase):
    def test_photography_wins_when_present(self):
        product = self.make_product("Photographed", stock=3)
        with db.tx():
            db.update("products", "id = ?", (product["id"],),
                      images="/static/img/mog-tee-front.jpg\n/static/img/mog-tee-back.jpg")
        row = catalog.get_product_by_id(product["id"])
        self.assertEqual(catalog.image_list(row),
                         ["/static/img/mog-tee-front.jpg",
                          "/static/img/mog-tee-back.jpg"])
        self.assertTrue(catalog.has_photography(row))
        self.assertEqual(catalog.primary_image(row), "/static/img/mog-tee-front.jpg")

    def test_generated_art_is_the_fallback(self):
        product = self.make_product("Unphotographed", stock=3, art_seed="hoodie-2")
        row = catalog.get_product_by_id(product["id"])
        images = catalog.image_list(row)
        self.assertEqual(len(images), 2, "a front and an alternate view")
        self.assertTrue(all(i.startswith("/media/hoodie-") for i in images), images)
        self.assertNotEqual(images[0], images[1], "the two views must differ")
        self.assertFalse(catalog.has_photography(row))

    def test_blank_and_whitespace_images_fall_back(self):
        product = self.make_product("Blank", stock=1)
        for value in ("", "   \n  \n"):
            with db.tx():
                db.update("products", "id = ?", (product["id"],), images=value)
            row = catalog.get_product_by_id(product["id"])
            self.assertTrue(catalog.image_list(row)[0].startswith("/media/"))

    def test_resolution_survives_a_row_without_the_column(self):
        """Older callers may pass a row that never selected `images`."""
        row = db.one("SELECT 1 AS x")
        self.assertTrue(catalog.image_list(row)[0].startswith("/media/tee-"))


class AdminPhotographyTests(StoreTestCase):
    """The client chose 'HesMartech manages updates' — so this is the surface."""

    seed_catalogue = True

    def _staff_request(self, **form):
        from app import accounts, cart as cart_module
        request = self.make_request("POST", "/", form=form)
        request.user = self.make_user("staff@example.com", role="admin")
        request.session = accounts.create_session(request.user["id"])
        request.cart_id = cart_module.ensure_cart(None)
        request.cart_count = 0
        return request

    def test_the_form_offers_a_photography_field(self):
        from app import views_admin
        product = db.one("SELECT * FROM products WHERE slug = 'mog-oversized-tee'")
        request = self._staff_request()
        request.method = "GET"
        markup = views_admin._product_form(request, product).body.decode()
        self.assertIn('name="images"', markup)
        self.assertIn("Fallback artwork", markup)
        self.assertIn("/static/img/mog-tee-front.jpg", markup,
                      "existing photography should preview in the form")

    def test_saving_normalises_the_image_list(self):
        from app import views_admin
        product = db.one(
            "SELECT * FROM products WHERE slug = 'mens-utility-work-shirt'")
        request = self._staff_request(
            title="Utility Work Shirt", price_cents="11800", status="active",
            art_seed="workshirt-0", position="0",
            images="  /static/img/a.jpg  \n\n   \n/static/img/b.jpg\n",
            variants="", csrf_token=request_token(),
        )
        views_admin.admin_product_update(request, product["id"])
        saved = db.scalar("SELECT images FROM products WHERE id = ?", (product["id"],))
        self.assertEqual(saved, "/static/img/a.jpg\n/static/img/b.jpg",
                         "blank lines and padding must be stripped")

    def test_clearing_photography_falls_back_to_generated_art(self):
        from app import views_admin
        product = db.one("SELECT * FROM products WHERE slug = 'mog-oversized-tee'")
        request = self._staff_request(
            title="MOG Oversized Tee", price_cents="6800", status="active",
            art_seed="tee-0", position="0", images="", variants="",
            csrf_token=request_token(),
        )
        views_admin.admin_product_update(request, product["id"])
        row = catalog.get_product_by_id(product["id"])
        self.assertFalse(catalog.has_photography(row))
        self.assertTrue(catalog.image_list(row)[0].startswith("/media/tee-"))


def request_token() -> str:
    return "unused-csrf-is-checked-by-middleware"


class MigrationTests(StoreTestCase):
    def test_images_column_is_added_to_an_older_database(self):
        with db.tx():
            db.execute("ALTER TABLE products DROP COLUMN images")
        columns = {r["name"] for r in db.query("PRAGMA table_info(products)")}
        self.assertNotIn("images", columns)
        db.migrate()
        columns = {r["name"] for r in db.query("PRAGMA table_info(products)")}
        self.assertIn("images", columns, "migrate() must add missing columns")

    def test_migrate_is_idempotent(self):
        db.migrate()
        db.migrate()
        self.assertIn("images",
                      {r["name"] for r in db.query("PRAGMA table_info(products)")})


if __name__ == "__main__":
    unittest.main()
