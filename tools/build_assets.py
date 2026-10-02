#!/usr/bin/env python3
"""Turn the client's supplied brand and campaign images into site assets.

Run it whenever new source artwork arrives:

    python3 tools/build_assets.py /path/to/source/images

Sources are matched by content, not filename:

  * the logo sheet  -> alpha masks for the wordmark and monogram, plus favicons
  * the flat-lays   -> front/back product shots, cropped 4:5 around the garment
  * the campaign    -> hero, portrait and social crops

`sips` (bundled with macOS) does the resizing and JPEG encoding; `pngkit` does
the cropping and alpha extraction.  Everything is written under app/static/.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pngkit import Image, alpha_bounds, decode, encode, to_alpha_mask  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
BRAND = STATIC / "brand"
IMG = STATIC / "img"
INK = (17, 17, 16)
PAPER = (246, 244, 239)


# ------------------------------------------------------------------- sips

def sips(*args: str) -> None:
    result = subprocess.run(["sips", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"sips failed: {result.stderr.strip()}")


def to_png(source: Path, destination: Path) -> Path:
    sips("-s", "format", "png", str(source), "--out", str(destination))
    return destination


def to_jpeg(source: Path, destination: Path, *, width: int, quality: int = 80) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sips("-s", "format", "jpeg", "-s", "formatOptions", str(quality),
         "--resampleWidth", str(width), str(source), "--out", str(destination))
    return destination


def to_png_sized(source: Path, destination: Path, *, width: int) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    sips("-s", "format", "png", "--resampleWidth", str(width),
         str(source), "--out", str(destination))
    return destination


# ---------------------------------------------------------------- geometry

def content_bands(mask: Image, *, axis: str, threshold: int = 30) -> list[tuple[int, int]]:
    """Runs of rows (axis='y') or columns (axis='x') containing artwork."""
    if axis == "y":
        filled = [
            any(mask.pixels[(y * mask.width + x) * 4 + 3] >= threshold
                for x in range(mask.width))
            for y in range(mask.height)
        ]
    else:
        filled = [
            any(mask.pixels[(y * mask.width + x) * 4 + 3] >= threshold
                for y in range(mask.height))
            for x in range(mask.width)
        ]
    bands, start = [], None
    for index, value in enumerate(filled):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start > 8:            # ignore speckle
                bands.append((start, index))
            start = None
    if start is not None:
        bands.append((start, len(filled)))
    return bands


def frame_to_ratio(box: tuple[int, int, int, int], *, ratio: float,
                   bounds: tuple[int, int], pad: float = 0.12,
                   x_range: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """Grow a bounding box to `ratio` (width/height) with breathing room.

    `x_range` pins the crop inside one half of a two-up layout, so a wide
    garment never pulls its neighbour's sleeve into frame.
    """
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    cx, cy = left + width / 2, top + height / 2
    width *= 1 + pad * 2
    height *= 1 + pad * 2
    if width / height > ratio:
        height = width / ratio
    else:
        width = height * ratio

    max_w, max_h = bounds
    x_low, x_high = x_range if x_range else (0, max_w)
    width = min(width, x_high - x_low)
    height = min(height, max_h)
    if width / height > ratio:
        width = height * ratio
    else:
        height = width / ratio

    cx = min(max(cx, x_low + width / 2), x_high - width / 2)
    cy = min(max(cy, height / 2), max_h - height / 2)
    return (round(cx - width / 2), round(cy - height / 2),
            round(cx + width / 2), round(cy + height / 2))


def column_density(mask: Image, threshold: int = 120) -> list[int]:
    return [
        sum(1 for y in range(mask.height)
            if mask.pixels[(y * mask.width + x) * 4 + 3] > threshold)
        for x in range(mask.width)
    ]


def split_point(mask: Image) -> int:
    """Where to cut a two-up layout in half.

    The two garments in a flat-lay usually touch, so there is no empty column
    to find -- but there is always a density valley between them.
    """
    density = column_density(mask)
    low, high = int(mask.width * 0.35), int(mask.width * 0.65)
    return min(range(low, high), key=lambda x: density[x])


def is_light_background(image: Image) -> bool:
    corners = [image.at(2, 2), image.at(image.width - 3, 2),
               image.at(2, image.height - 3)]
    average = sum(sum(c[:3]) / 3 for c in corners) / len(corners)
    return average > 200


def flatten(mask: Image, *, ink: tuple[int, int, int],
            paper: tuple[int, int, int] | None) -> Image:
    """Composite an alpha mask onto a solid colour (or keep it transparent)."""
    out = bytearray(mask.width * mask.height * 4)
    for index in range(mask.width * mask.height):
        alpha = mask.pixels[index * 4 + 3] / 255
        target = index * 4
        if paper is None:
            out[target:target + 3] = bytes(ink)
            out[target + 3] = mask.pixels[index * 4 + 3]
        else:
            for channel in range(3):
                out[target + channel] = round(
                    paper[channel] + (ink[channel] - paper[channel]) * alpha
                )
            out[target + 3] = 255
    return Image(mask.width, mask.height, 4, out)


def pad_square(image: Image, *, fill: tuple[int, int, int], margin: float = 0.16) -> Image:
    side = round(max(image.width, image.height) * (1 + margin * 2))
    out = bytearray()
    for _ in range(side * side):
        out += bytes(fill) + b"\xff"
    left = (side - image.width) // 2
    top = (side - image.height) // 2
    for y in range(image.height):
        src = y * image.width * 4
        dst = ((top + y) * side + left) * 4
        out[dst:dst + image.width * 4] = image.pixels[src:src + image.width * 4]
    return Image(side, side, 4, out)


# ------------------------------------------------------------------ builds

def build_logo(source: Path, work: Path) -> None:
    mask = to_alpha_mask(decode(to_png(source, work / "logo.png").read_bytes()))
    bands = content_bands(mask, axis="y")
    if len(bands) < 2:
        raise SystemExit("expected a wordmark and a monogram in the logo sheet")

    BRAND.mkdir(parents=True, exist_ok=True)
    for name, (top, bottom) in zip(("wordmark", "monogram"), bands[:2]):
        band = mask.crop(0, top, mask.width, bottom)
        left, _, right, _ = alpha_bounds(band)
        tight = band.crop(left, 0, right, band.height)
        (work / f"{name}.png").write_bytes(encode(tight))
        # The mask keeps its alpha so CSS can paint it in the theme colour.
        to_png_sized(work / f"{name}.png", BRAND / f"{name}.png",
                     width=min(tight.width, 900 if name == "wordmark" else 320))
        print(f"  brand/{name}.png  {tight.width}x{tight.height}")

    # Favicon: the monogram in paper on an ink field.
    monogram = decode((work / "monogram.png").read_bytes())
    icon = pad_square(flatten(monogram, ink=PAPER, paper=INK), fill=INK, margin=0.34)
    (work / "favicon.png").write_bytes(encode(icon))
    to_png_sized(work / "favicon.png", BRAND / "favicon.png", width=180)
    print(f"  brand/favicon.png {icon.width}x{icon.height} -> 180px")


def build_flatlay(source: Path, work: Path, slug: str) -> list[str]:
    """Split a two-up flat-lay into front and back, each cropped 4:5."""
    png = to_png(source, work / f"{slug}.png")
    image = decode(png.read_bytes())
    mask = to_alpha_mask(image)
    middle = split_point(mask)

    halves = {"front": (0, middle), "back": (middle, image.width)}
    written: list[str] = []
    for label, (left, right) in halves.items():
        strip = mask.crop(left, 0, right, mask.height)
        sub_left, top, sub_right, bottom = alpha_bounds(strip, threshold=120)
        box = frame_to_ratio(
            (left + sub_left, top, left + sub_right, bottom),
            ratio=0.8, bounds=(image.width, image.height), pad=0.10,
            x_range=(left, right),
        )
        crop = image.crop(*box)
        (work / f"{slug}-{label}.png").write_bytes(encode(crop))
        out = IMG / f"{slug}-{label}.jpg"
        to_jpeg(work / f"{slug}-{label}.png", out, width=1200, quality=82)
        written.append(f"/static/img/{out.name}")
        print(f"  img/{out.name}  crop={box}  {out.stat().st_size // 1024}KB")
    return written


def build_campaign(sources: dict[str, Path], work: Path) -> None:
    plan = [
        ("wide", "campaign-wide.jpg", 2000, 82),
        ("portrait", "campaign-portrait.jpg", 1200, 82),
        ("tall", "campaign-tall.jpg", 1080, 82),
    ]
    for key, name, width, quality in plan:
        if key not in sources:
            continue
        out = to_jpeg(sources[key], IMG / name, width=width, quality=quality)
        print(f"  img/{name}  {out.stat().st_size // 1024}KB")

    # Social card: 1200x630 from the widest campaign frame.
    if "wide" in sources:
        png = to_png(sources["wide"], work / "og-src.png")
        image = decode(png.read_bytes())
        ratio = 1200 / 630
        height = min(image.height, round(image.width / ratio))
        width = round(height * ratio)
        left = (image.width - width) // 2
        top = round((image.height - height) * 0.28)
        (work / "og.png").write_bytes(
            encode(image.crop(left, top, left + width, top + height))
        )
        out = to_jpeg(work / "og.png", BRAND / "og.jpg", width=1200, quality=84)
        print(f"  brand/og.jpg  {out.stat().st_size // 1024}KB")


# -------------------------------------------------------------- dispatch

def classify(paths: list[Path], work: Path) -> dict:
    """Sort the source images by what they actually contain, not by filename."""
    found: dict = {"flatlays": [], "campaign": {}}
    for path in sorted(paths):
        png = to_png(path, work / f"probe-{path.stem}.png")
        image = decode(png.read_bytes())
        ratio = image.width / image.height

        if is_light_background(image):
            # Studio artwork: either the logo sheet or a garment flat-lay.
            mask = to_alpha_mask(image)
            ink = sum(1 for i in range(mask.width * mask.height)
                      if mask.pixels[i * 4 + 3] > 128)
            coverage = ink / (mask.width * mask.height)
            bands = content_bands(mask, axis="y")
            if coverage < 0.25 and len(bands) >= 2 and "logo" not in found:
                found["logo"] = path
            else:
                found["flatlays"].append(path)
            continue

        # Photography: bucket by shape.
        if ratio > 1.4:
            found["campaign"]["wide"] = path
        elif ratio < 0.62:
            found["campaign"]["tall"] = path
        else:
            found["campaign"]["portrait"] = path
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help="directory of source images")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args(argv)

    paths = [p for p in sorted(args.source.iterdir())
             if p.suffix.lower() in {".webp", ".jpg", ".jpeg", ".png", ".heic"}]
    if not paths:
        raise SystemExit(f"no images found in {args.source}")

    work = Path(tempfile.mkdtemp(prefix="mog-assets-"))
    try:
        IMG.mkdir(parents=True, exist_ok=True)
        found = classify(paths, work)

        if "logo" in found:
            print("logo sheet:", found["logo"].name)
            build_logo(found["logo"], work)

        slugs = ["mog-tee", "lifestyle-tee"]
        for index, flatlay in enumerate(found["flatlays"][:2]):
            slug = slugs[index] if index < len(slugs) else f"product-{index}"
            print(f"flat-lay: {flatlay.name} -> {slug}")
            build_flatlay(flatlay, work, slug)

        if found["campaign"]:
            print("campaign:", {k: v.name for k, v in found["campaign"].items()})
            build_campaign(found["campaign"], work)
    finally:
        if args.keep_work:
            print(f"work kept in {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
