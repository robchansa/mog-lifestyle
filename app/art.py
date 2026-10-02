"""Deterministic monochrome product artwork.

The brief asks for an elegant / luxury look in black and white, and the client
has no photography yet.  Rather than ship grey boxes, every product renders a
generated studio still: a soft tonal ground, a garment built from layered
silhouettes, a contact shadow and a fine grain.  Output is pure SVG -- crisp at
any size, a few KB, fully deterministic, and therefore cacheable forever.

Seeds look like ``hoodie-02``: shape name, then a variation index that shifts
tone, light direction and framing.
"""
from __future__ import annotations

import hashlib
import pathlib
from typing import Callable

VIEWBOX = "0 0 800 1000"

# Artwork URLs are served `immutable`, so the URL itself has to change whenever
# the generator does.  Hashing this module's source gives us that for free: edit
# a silhouette and every cached image is invalidated on the next deploy.
ART_VERSION = hashlib.sha1(
    pathlib.Path(__file__).read_bytes()
).hexdigest()[:8]


def media_url(seed: str) -> str:
    """Cache-busting URL for one product still."""
    return f"/media/{seed}.svg?v={ART_VERSION}"


# A part is one painted layer of the garment.
#   role "back"   -> behind the body, rendered a shade darker
#   role "body"   -> the main silhouette (carries the gradient + outline)
#   role "shade"  -> a tonal accent painted over the body
#   role "cut"    -> an opening, painted back to the ground colour
#   role "line"   -> a hairline seam / stitch detail
Part = tuple[str, str]


# --------------------------------------------------------------- silhouettes

def _tee() -> list[Part]:
    return [(
        "M 340 296 C 330 284 300 286 262 300 "
        "L 168 356 C 156 362 152 376 158 386 L 196 456 "
        "C 202 468 216 472 226 464 L 258 438 "
        "L 258 742 C 258 757 268 767 283 767 "
        "L 517 767 C 532 767 542 757 542 742 "
        "L 542 438 L 574 464 C 584 472 598 468 604 456 "
        "L 642 386 C 648 376 644 362 632 356 "
        "L 538 300 C 500 286 470 284 460 296 "
        "C 447 332 353 332 340 296 Z", "body",
    ), (
        "M 340 296 C 353 332 447 332 460 296", "line",
    )]


def _longsleeve() -> list[Part]:
    return [(
        # left sleeve: a tapering tube from the armhole down to the cuff
        "M 262 300 C 212 412 148 566 112 660 "
        "C 106 674 112 688 126 692 L 176 706 "
        "C 192 710 206 700 208 686 "
        "C 226 606 246 512 258 438 Z", "back",
    ), (
        "M 538 300 C 588 412 652 566 688 660 "
        "C 694 674 688 688 674 692 L 624 706 "
        "C 608 710 594 700 592 686 "
        "C 574 606 554 512 542 438 Z", "back",
    ), (
        "M 340 296 C 330 284 300 286 262 300 L 258 438 "
        "L 258 742 C 258 757 268 767 283 767 "
        "L 517 767 C 532 767 542 757 542 742 L 542 438 "
        "L 538 300 C 500 286 470 284 460 296 "
        "C 447 332 353 332 340 296 Z", "body",
    ), (
        "M 340 296 C 353 332 447 332 460 296", "line",
    ), (
        "M 120 672 L 202 694 M 680 672 L 598 694", "line",
    )]


def _hoodie() -> list[Part]:
    return [(
        # hood, sitting behind the shoulders
        "M 298 384 C 288 262 334 158 400 158 C 466 158 512 262 502 384 "
        "C 502 402 462 412 400 412 C 338 412 298 402 298 384 Z", "back",
    ), (
        # hood opening
        "M 340 376 C 334 278 362 200 400 200 C 438 200 466 278 460 376 "
        "C 460 392 434 400 400 400 C 366 400 340 392 340 376 Z", "shade",
    ), (
        # body with raglan sleeves
        "M 334 306 C 314 298 292 304 268 318 "
        "L 196 372 C 184 380 180 394 186 406 L 240 540 "
        "C 246 554 262 558 274 550 L 272 552 L 272 796 "
        "C 272 812 284 824 300 824 L 500 824 C 516 824 528 812 528 796 "
        "L 528 552 L 526 550 C 538 558 554 554 560 540 L 614 406 "
        "C 620 394 616 380 604 372 L 532 318 C 508 304 486 298 466 306 "
        "C 470 340 452 366 400 366 C 348 366 330 340 334 306 Z", "body",
    ), (
        # kangaroo pocket
        "M 312 656 L 488 656 C 496 656 502 662 502 670 L 502 742 "
        "C 502 750 496 756 488 756 L 312 756 C 304 756 298 750 298 742 "
        "L 298 670 C 298 662 304 656 312 656 Z", "line",
    ), (
        # drawstrings
        "M 374 388 L 370 496 M 426 388 L 430 496", "line",
    )]


def _joggers() -> list[Part]:
    return [(
        "M 292 288 L 508 288 C 520 288 528 298 526 310 "
        "L 512 430 L 496 792 C 496 804 488 812 476 812 "
        "L 432 812 C 421 812 412 804 411 793 L 400 548 "
        "L 389 793 C 388 804 379 812 368 812 L 324 812 "
        "C 312 812 304 804 304 792 L 288 430 L 274 310 "
        "C 272 298 280 288 292 288 Z", "body",
    ), (
        "M 278 342 L 522 342", "line",
    ), (
        "M 400 296 L 400 344", "line",
    ), (
        "M 306 760 L 494 760", "line",
    )]


def _shorts() -> list[Part]:
    return [(
        "M 286 292 L 514 292 C 526 292 534 302 532 314 "
        "L 520 400 L 506 612 C 505 624 497 632 485 632 "
        "L 430 632 C 419 632 410 624 409 613 L 400 486 "
        "L 391 613 C 390 624 381 632 370 632 L 315 632 "
        "C 303 632 295 624 294 612 L 280 400 L 268 314 "
        "C 266 302 274 292 286 292 Z", "body",
    ), (
        "M 272 346 L 528 346", "line",
    ), (
        "M 400 300 L 400 348", "line",
    )]


def _tank() -> list[Part]:
    return [(
        "M 338 262 C 318 270 302 286 296 308 L 268 430 L 268 748 "
        "C 268 762 278 772 292 772 L 508 772 C 522 772 532 762 532 748 "
        "L 532 430 L 504 308 C 498 286 482 270 462 262 "
        "C 452 320 438 342 400 342 C 362 342 348 320 338 262 Z", "body",
    ), (
        "M 338 262 C 348 320 362 342 400 342 C 438 342 452 320 462 262", "line",
    )]


def _cap() -> list[Part]:
    return [(
        # brim, behind the crown
        "M 252 500 C 190 508 146 532 134 560 C 124 582 142 598 176 600 "
        "L 470 600 C 528 600 566 572 566 538 L 566 500 Z", "back",
    ), (
        "M 400 318 C 294 318 224 390 220 496 C 219 514 230 526 248 526 "
        "L 552 526 C 570 526 582 514 580 496 C 576 390 506 318 400 318 Z", "body",
    ), (
        "M 400 318 L 400 526 M 312 336 C 288 396 278 460 280 526 "
        "M 488 336 C 512 396 522 460 520 526", "line",
    ), (
        "M 400 300 m -14 0 a 14 14 0 1 0 28 0 a 14 14 0 1 0 -28 0", "shade",
    )]


def _bottle() -> list[Part]:
    return [(
        "M 356 214 L 444 214 C 454 214 460 222 460 232 L 460 272 "
        "C 494 292 512 326 512 370 L 512 744 C 512 776 490 796 458 796 "
        "L 342 796 C 310 796 288 776 288 744 L 288 370 "
        "C 288 326 306 292 340 272 L 340 232 C 340 222 346 214 356 214 Z", "body",
    ), (
        "M 340 206 L 460 206 C 468 206 474 212 474 220 L 474 244 "
        "C 474 252 468 258 460 258 L 340 258 C 332 258 326 252 326 244 "
        "L 326 220 C 326 212 332 206 340 206 Z", "shade",
    ), (
        "M 292 472 L 508 472 M 292 520 L 508 520", "line",
    )]


def _duffel() -> list[Part]:
    return [(
        # carry handle, a filled strap standing above the body
        "M 342 372 C 342 302 368 274 400 274 C 432 274 458 302 458 372 "
        "L 432 372 C 432 316 420 300 400 300 C 380 300 368 316 368 372 Z", "back",
    ), (
        "M 218 358 L 582 358 C 616 358 636 382 636 414 L 636 642 "
        "C 636 678 612 700 576 700 L 224 700 C 188 700 164 678 164 642 "
        "L 164 414 C 164 382 184 358 218 358 Z", "body",
    ), (
        # webbing straps
        "M 296 358 L 340 358 L 340 700 L 296 700 Z "
        "M 460 358 L 504 358 L 504 700 L 460 700 Z", "shade",
    ), (
        # zip
        "M 180 466 L 620 466", "line",
    ), (
        "M 190 466 m -9 0 a 9 9 0 1 0 18 0 a 9 9 0 1 0 -18 0", "line",
    )]


def _beanie() -> list[Part]:
    return [(
        "M 232 566 C 232 350 306 232 400 232 C 494 232 568 350 568 566 Z", "body",
    ), (
        # folded cuff, drawn over the crown
        "M 214 548 C 200 548 190 558 190 572 L 190 684 "
        "C 190 698 200 708 214 708 L 586 708 C 600 708 610 698 610 684 "
        "L 610 572 C 610 558 600 548 586 548 Z", "body",
    ), (
        "M 254 560 L 254 698 M 312 556 L 312 698 M 370 554 L 370 698 "
        "M 430 554 L 430 698 M 488 556 L 488 698 M 546 560 L 546 698", "line",
    )]


def _skimask() -> list[Part]:
    return [(
        "M 400 192 C 286 192 222 286 222 424 L 222 608 "
        "C 222 712 280 798 400 820 C 520 798 578 712 578 608 "
        "L 578 424 C 578 286 514 192 400 192 Z", "body",
    ), (
        # eye opening
        "M 280 418 C 280 384 316 364 400 364 C 484 364 520 384 520 418 "
        "C 520 452 484 472 400 472 C 316 472 280 452 280 418 Z", "cut",
    ), (
        # mouth opening
        "M 340 600 C 340 580 366 568 400 568 C 434 568 460 580 460 600 "
        "C 460 620 434 632 400 632 C 366 632 340 620 340 600 Z", "cut",
    ), (
        "M 400 192 L 400 360 M 300 500 L 500 500", "line",
    )]


def _socks() -> list[Part]:
    return [(
        # back sock, offset right and down
        "M 398 254 L 516 254 L 516 582 C 516 618 538 642 576 652 "
        "L 662 674 C 690 681 704 706 697 732 C 690 758 665 772 637 765 "
        "L 510 732 C 438 714 392 652 392 582 L 392 254 Z", "back",
    ), (
        "M 268 228 L 386 228 L 386 556 C 386 592 408 616 446 626 "
        "L 532 648 C 560 655 574 680 567 706 C 560 732 535 746 507 739 "
        "L 380 706 C 308 688 262 626 262 556 L 262 228 Z", "body",
    ), (
        "M 262 300 L 386 300 M 392 326 L 516 326", "line",
    )]


def _croptop() -> list[Part]:
    return [(
        "M 340 296 C 330 284 300 286 262 300 "
        "L 190 350 C 178 358 174 372 180 384 L 220 470 "
        "C 226 484 242 488 254 480 L 278 462 L 278 536 "
        "C 278 552 290 564 306 564 L 494 564 C 510 564 522 552 522 536 "
        "L 522 462 L 546 480 C 558 488 574 484 580 470 L 620 384 "
        "C 626 372 622 358 610 350 L 538 300 C 500 286 470 284 460 296 "
        "C 447 332 353 332 340 296 Z", "body",
    ), (
        "M 340 296 C 353 332 447 332 460 296", "line",
    ), (
        "M 284 540 L 516 540", "line",
    )]


def _workshirt() -> list[Part]:
    return [(
        # collar, sitting behind the shoulders
        "M 326 292 C 334 268 356 256 384 256 L 416 256 "
        "C 444 256 466 268 474 292 L 400 352 Z", "back",
    ), (
        "M 330 300 C 316 290 292 294 266 308 "
        "L 184 358 C 172 366 168 380 174 392 L 232 530 "
        "C 238 544 254 548 266 540 L 288 524 L 288 744 "
        "C 288 760 300 772 316 772 L 484 772 C 500 772 512 760 512 744 "
        "L 512 524 L 534 540 C 546 548 562 544 568 530 L 626 392 "
        "C 632 380 628 366 616 358 L 534 308 C 508 294 484 290 470 300 "
        "L 400 356 Z", "body",
    ), (
        "M 400 356 L 400 772", "line",
    ), (
        # chest pockets
        "M 322 424 L 376 424 L 376 486 L 322 486 Z "
        "M 424 424 L 478 424 L 478 486 L 424 486 Z", "line",
    ), (
        "M 400 430 m -7 0 a 7 7 0 1 0 14 0 a 7 7 0 1 0 -14 0 "
        "M 400 560 m -7 0 a 7 7 0 1 0 14 0 a 7 7 0 1 0 -14 0", "line",
    )]


def _sportsbra() -> list[Part]:
    return [(
        # left shoulder strap, clearly separated from its pair
        "M 318 330 C 306 272 312 232 334 216 L 368 240 "
        "C 354 258 352 290 358 336 Z", "back",
    ), (
        "M 482 330 C 494 272 488 232 466 216 L 432 240 "
        "C 446 258 448 290 442 336 Z", "back",
    ), (
        # band with a scooped neckline
        "M 286 358 C 286 332 320 314 360 308 C 378 336 422 336 440 308 "
        "C 480 314 514 332 514 358 L 526 462 C 532 512 486 540 400 540 "
        "C 314 540 268 512 274 462 Z", "body",
    ), (
        "M 282 500 L 518 500", "line",
    ), (
        "M 360 308 C 378 336 422 336 440 308", "line",
    )]


SHAPES: dict[str, Callable[[], list[Part]]] = {
    "tee": _tee,
    "longsleeve": _longsleeve,
    "hoodie": _hoodie,
    "joggers": _joggers,
    "shorts": _shorts,
    "tank": _tank,
    "cap": _cap,
    "bottle": _bottle,
    "duffel": _duffel,
    "beanie": _beanie,
    "skimask": _skimask,
    "socks": _socks,
    "croptop": _croptop,
    "workshirt": _workshirt,
    "sportsbra": _sportsbra,
}

# Each variation nudges garment tone, ground tone and light angle, so a grid of
# products reads as one shoot rather than one repeated image.
VARIATIONS = [
    {"garment": "#17171b", "ground": "#efedea", "light": 22},
    {"garment": "#f5f3f0", "ground": "#191a1e", "light": 158},
    {"garment": "#3b3b42", "ground": "#f2f0ed", "light": 40},
    {"garment": "#dad7d2", "ground": "#2b2b31", "light": 140},
    {"garment": "#0d0d10", "ground": "#e5e2dd", "light": 0},
    {"garment": "#8e8e95", "ground": "#f6f5f2", "light": 60},
]


def parse_seed(seed: str) -> tuple[str, int]:
    """Split ``hoodie-02`` into a known shape name and a variation index."""
    shape, _, index = (seed or "tee-0").partition("-")
    if shape not in SHAPES:
        shape = "tee"
    if index.isdigit():
        variation = int(index)
    else:
        variation = int(hashlib.sha256((seed or "").encode()).hexdigest()[:8], 16)
    return shape, variation % len(VARIATIONS)


def render(seed: str, *, width: int = 800, height: int = 1000) -> str:
    """Return a standalone SVG document for this seed."""
    shape, index = parse_seed(seed)
    v = VARIATIONS[index]
    uid = hashlib.sha1((seed or "tee-0").encode()).hexdigest()[:8]
    dark_ground = _luminance(v["ground"]) < 0.5
    shade = "#ffffff" if _luminance(v["garment"]) < 0.5 else "#000000"
    contact = "#ffffff" if dark_ground else "#000000"

    parts = SHAPES[shape]()
    body_paths = [d for d, role in parts if role == "body"]

    layers: list[str] = []
    # Cast shadow, built from the body silhouettes only.
    for d in body_paths:
        layers.append(
            f'<path d="{d}" fill="{contact}" fill-opacity=".20" '
            f'transform="translate(18 22)" filter="url(#soft{uid})"/>'
        )
    for d, role in parts:
        if role == "back":
            layers.append(f'<path d="{d}" fill="url(#back{uid})"/>')
        elif role == "body":
            layers.append(f'<path d="{d}" fill="url(#body{uid})"/>')
        elif role == "shade":
            layers.append(f'<path d="{d}" fill="{shade}" fill-opacity=".10"/>')
        elif role == "cut":
            layers.append(
                f'<path d="{d}" fill="{v["ground"]}"/>'
                f'<path d="{d}" fill="none" stroke="{shade}" '
                f'stroke-opacity=".20" stroke-width="2"/>'
            )
        else:  # line
            layers.append(
                f'<path d="{d}" fill="none" stroke="{shade}" stroke-opacity=".26" '
                f'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            )
    # Outline every body silhouette last so edges stay crisp.
    for d in body_paths:
        layers.append(
            f'<path d="{d}" fill="none" stroke="{shade}" '
            f'stroke-opacity=".16" stroke-width="2"/>'
        )

    ground_hi = _mix(v["ground"], "#6a6a73" if dark_ground else "#ffffff", 0.55)
    ground_lo = _mix(v["ground"], "#000000", 0.18)
    # Layers behind the body shift *towards contrast*, not always darker, so a
    # near-black garment's sleeves stay readable instead of merging into it.
    back_hi = _mix(v["garment"], shade, 0.14)
    back_lo = _mix(v["garment"], shade, 0.34)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="{VIEWBOX}" \
width="{width}" height="{height}" role="img" \
aria-label="MOG Lifestyle {shape} product study">
  <defs>
    <radialGradient id="bgr{uid}" cx="{38 + index * 5}%" cy="26%" r="86%">
      <stop offset="0%" stop-color="{ground_hi}"/>
      <stop offset="62%" stop-color="{v['ground']}"/>
      <stop offset="100%" stop-color="{ground_lo}"/>
    </radialGradient>
    <linearGradient id="body{uid}" x1="0" y1="0" x2="1" y2="1"
        gradientTransform="rotate({v['light']} .5 .5)">
      <stop offset="0%" stop-color="{_mix(v['garment'], '#ffffff', .30)}"/>
      <stop offset="46%" stop-color="{v['garment']}"/>
      <stop offset="100%" stop-color="{_mix(v['garment'], '#000000', .34)}"/>
    </linearGradient>
    <linearGradient id="back{uid}" x1="0" y1="0" x2="1" y2="1"
        gradientTransform="rotate({v['light']} .5 .5)">
      <stop offset="0%" stop-color="{back_hi}"/>
      <stop offset="100%" stop-color="{back_lo}"/>
    </linearGradient>
    <filter id="soft{uid}" x="-25%" y="-25%" width="150%" height="150%">
      <feGaussianBlur stdDeviation="26"/>
    </filter>
    <filter id="grain{uid}" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="3"
                    seed="{index + 7}"/>
      <feColorMatrix type="saturate" values="0"/>
    </filter>
  </defs>

  <rect width="800" height="1000" fill="url(#bgr{uid})"/>
  <ellipse cx="400" cy="858" rx="188" ry="24" fill="{contact}"
           fill-opacity=".22" filter="url(#soft{uid})"/>
  {''.join(layers)}
  <rect width="800" height="1000" filter="url(#grain{uid})" opacity=".05"/>
</svg>
"""


# ------------------------------------------------------------------ colour

def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _mix(a: str, b: str, amount: float) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    r = round(ra + (rb - ra) * amount)
    g = round(ga + (gb - ga) * amount)
    bl = round(ba + (bb - ba) * amount)
    return f"#{r:02x}{g:02x}{bl:02x}"


def _luminance(value: str) -> float:
    r, g, b = _hex_to_rgb(value)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
