"""Generated product artwork."""
from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app import art


class ArtTests(unittest.TestCase):
    def test_every_shape_renders_valid_svg(self):
        for shape in art.SHAPES:
            for index in range(len(art.VARIATIONS)):
                seed = f"{shape}-{index}"
                with self.subTest(seed=seed):
                    svg = art.render(seed)
                    root = ET.fromstring(svg)
                    self.assertTrue(root.tag.endswith("svg"))
                    self.assertEqual(root.get("viewBox"), art.VIEWBOX)
                    self.assertIn("aria-label", root.attrib)

    def test_rendering_is_deterministic(self):
        self.assertEqual(art.render("hoodie-2"), art.render("hoodie-2"))

    def test_variations_differ(self):
        self.assertNotEqual(art.render("tee-0"), art.render("tee-1"))

    def test_unknown_shape_falls_back_to_tee(self):
        shape, _ = art.parse_seed("spaceship-2")
        self.assertEqual(shape, "tee")

    def test_non_numeric_index_is_stable(self):
        first = art.parse_seed("tee-abc")
        second = art.parse_seed("tee-abc")
        self.assertEqual(first, second)
        self.assertLess(first[1], len(art.VARIATIONS))

    def test_empty_seed_is_safe(self):
        ET.fromstring(art.render(""))
        ET.fromstring(art.render(None or ""))

    def test_media_urls_carry_the_generator_version(self):
        """Artwork is served `immutable`, so the URL must change with the code."""
        self.assertEqual(len(art.ART_VERSION), 8)
        url = art.media_url("tee-0")
        self.assertTrue(url.startswith("/media/tee-0.svg?v="))
        self.assertTrue(url.endswith(art.ART_VERSION))

    def test_the_version_tracks_the_module_source(self):
        import hashlib
        import pathlib
        expected = hashlib.sha1(
            pathlib.Path(art.__file__).read_bytes()
        ).hexdigest()[:8]
        self.assertEqual(art.ART_VERSION, expected)

    def test_colour_helpers(self):
        self.assertEqual(art._mix("#000000", "#ffffff", 0.5), "#808080")
        self.assertEqual(art._mix("#ffffff", "#000000", 0.0), "#ffffff")
        self.assertAlmostEqual(art._luminance("#ffffff"), 1.0, places=3)
        self.assertAlmostEqual(art._luminance("#000000"), 0.0, places=3)


if __name__ == "__main__":
    unittest.main()
