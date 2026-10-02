"""Demo catalogue, staff account and sample promotions.

The structure here follows the client's category list exactly:

    Everyone  oversized tees (black & white), beanies, ski masks, socks
    Men       oversized tees, regular-fit tees, work shirts
    Women     oversized tees, crop tops, training shorts, sports bras & sets

Products carrying real photography use it; the rest fall back to the generated
studio stills in `app/art.py`.  Replace any of it through the admin console --
nothing here is load-bearing.
"""
from __future__ import annotations

import os

from . import db
from .config import config
from .security import audit, hash_password, password_problems, slugify

# Development only.  It is printed in the README, so production refuses it:
# there the first admin comes from MOG_ADMIN_PASSWORD or `run.py --create-admin`.
DEV_ADMIN_EMAIL = "info@moglifestyle.fit"
DEV_ADMIN_PASSWORD = "mog-admin-2026"


class SeedError(RuntimeError):
    """The seed was asked to do something unsafe."""


def admin_credentials() -> tuple[str, str]:
    """The first admin's email and password, or ("", "") to skip creating one."""
    email = os.environ.get("MOG_ADMIN_EMAIL", "").strip() or DEV_ADMIN_EMAIL
    password = os.environ.get("MOG_ADMIN_PASSWORD", "")
    if not config.is_production:
        return email, password or DEV_ADMIN_PASSWORD
    if not password:
        return "", ""
    if password == DEV_ADMIN_PASSWORD:
        raise SeedError("MOG_ADMIN_PASSWORD is the public development password. "
                        "Choose a new one.")
    problems = password_problems(password)
    if problems:
        raise SeedError("MOG_ADMIN_PASSWORD: " + " ".join(problems))
    return email, password

# slug, title, department, position
CATEGORIES = [
    ("oversized-tees", "Oversized T-Shirts", "general", 1),
    ("beanies", "Beanies", "general", 2),
    ("ski-masks", "Ski Masks", "general", 3),
    ("socks", "Socks", "general", 4),

    ("mens-oversized-tees", "Oversized T-Shirts", "men", 1),
    ("mens-regular-tees", "Regular-Fit T-Shirts", "men", 2),
    ("mens-work-shirts", "Work Shirts", "men", 3),

    ("womens-oversized-tees", "Oversized T-Shirts", "women", 1),
    ("womens-crop-tops", "Crop Tops", "women", 2),
    ("womens-training-shorts", "Training Shorts", "women", 3),
    ("womens-training-sets", "Sports Bras & Training Sets", "women", 4),
]

SIZES = ["S", "M", "L", "XL", "XXL"]
WOMENS_SIZES = ["XS", "S", "M", "L", "XL"]
ONE_SIZE = ["OS"]

# title, subtitle, category, price, compare, art seed, featured,
# description, details, sizes, colour, stock plan, images
PRODUCTS = [
    # ---------------------------------------------------------- everyone
    (
        "MOG Oversized Tee", "320gsm cotton · oversized", "oversized-tees",
        6800, None, "tee-0", True,
        "The one the whole line is built around. 320gsm combed cotton, cut "
        "oversized with a dropped shoulder, the monogram raised on the chest "
        "and the logotype across the back.",
        "320gsm combed ring-spun cotton\nOversized fit, dropped shoulder\n"
        "Raised monogram at the chest\nLogotype print across the back\n"
        "Ribbed collar, twin-needle hems\nGarment dyed and pre-shrunk",
        SIZES, "Washed Black", [6, 18, 24, 16, 8],
        "/static/img/mog-tee-front.jpg\n/static/img/mog-tee-back.jpg",
    ),
    (
        "It's a Lifestyle Tee", "320gsm cotton · oversized", "oversized-tees",
        7200, None, "tee-2", True,
        "The same oversized body, with the monogram and the line that started "
        "it printed small at the chest. Back logotype unchanged.",
        "320gsm combed ring-spun cotton\nOversized fit, dropped shoulder\n"
        "Chest monogram with “it’s a lifestyle”\n"
        "Logotype print across the back\nGarment dyed and pre-shrunk",
        SIZES, "Washed Black", [4, 14, 19, 12, 5],
        "/static/img/lifestyle-tee-front.jpg\n/static/img/lifestyle-tee-back.jpg",
    ),
    (
        "MOG Oversized Tee — Bone", "320gsm cotton · oversized", "oversized-tees",
        6800, None, "tee-1", False,
        "The Standard in bone. Same 320gsm body, same oversized cut, with the "
        "logotype reversed out in black.",
        "320gsm combed ring-spun cotton\nOversized fit, dropped shoulder\n"
        "Raised monogram at the chest\nGarment dyed and pre-shrunk",
        SIZES, "Bone", [5, 13, 16, 10, 4], "",
    ),
    (
        "Ribbed Beanie", "Merino blend · cuffed", "beanies",
        3800, None, "beanie-0", True,
        "A tight-knit merino blend with a deep fold. Holds its shape through a "
        "winter of early starts, and does not itch.",
        "70% merino, 30% acrylic\nDeep folded cuff\nFine 2x2 rib\n"
        "Woven monogram label\nOne size, fits 54–61cm",
        ONE_SIZE, "Black", [38], "",
    ),
    (
        "Ribbed Beanie — Bone", "Merino blend · cuffed", "beanies",
        3800, None, "beanie-1", False,
        "The Ribbed Beanie in bone.",
        "70% merino, 30% acrylic\nDeep folded cuff\nFine 2x2 rib\n"
        "One size, fits 54–61cm",
        ONE_SIZE, "Bone", [14], "",
    ),
    (
        "Thermal Ski Mask", "Fleece-lined · three-panel", "ski-masks",
        4600, 5800, "skimask-0", True,
        "A fleece-lined balaclava cut close enough to wear under a hood. "
        "Three panels, flat seams, and an opening that does not shift when you "
        "turn your head.",
        "Fleece-lined poly-elastane\nThree-panel construction\n"
        "Flatlock seams throughout\nBreathable mouth panel\nOne size",
        ONE_SIZE, "Black", [22], "",
    ),
    (
        "Thermal Ski Mask — Bone", "Fleece-lined · three-panel", "ski-masks",
        4600, None, "skimask-1", False,
        "The Thermal Ski Mask in bone.",
        "Fleece-lined poly-elastane\nThree-panel construction\n"
        "Flatlock seams throughout\nOne size",
        ONE_SIZE, "Bone", [9], "",
    ),
    (
        "Crew Socks — Three Pack", "Combed cotton · cushioned", "socks",
        2800, None, "socks-0", True,
        "Three pairs of cushioned crew socks with an arch band that actually "
        "holds. Logotype at the cuff, nothing else.",
        "Three pairs per pack\nCombed cotton with cushioned sole\n"
        "Compression arch band\nReinforced heel and toe\n"
        "Sizes: S/M (UK 4–7), L/XL (UK 8–12)",
        ["S/M", "L/XL"], "Black", [26, 24], "",
    ),
    (
        "Crew Socks — Bone Three Pack", "Combed cotton · cushioned", "socks",
        2800, None, "socks-1", False,
        "The Crew Sock three pack in bone.",
        "Three pairs per pack\nCombed cotton with cushioned sole\n"
        "Compression arch band\nReinforced heel and toe",
        ["S/M", "L/XL"], "Bone", [12, 10], "",
    ),

    # --------------------------------------------------------------- men
    (
        "Atlas Oversized Tee", "340gsm cotton · boxed", "mens-oversized-tees",
        7400, None, "tee-4", True,
        "A heavier oversized body cut for width through the chest and "
        "shoulders, with a hem that sits where you want it.",
        "340gsm combed ring-spun cotton\nBoxed oversized fit\n"
        "Dropped shoulder, wide sleeve\nRibbed collar\nPre-shrunk",
        SIZES, "Black", [5, 16, 21, 14, 7], "",
    ),
    (
        "Atlas Oversized Tee — Bone", "340gsm cotton · boxed", "mens-oversized-tees",
        7400, 8800, "tee-3", False,
        "The Atlas in bone, on the last of the first run.",
        "340gsm combed ring-spun cotton\nBoxed oversized fit\n"
        "Dropped shoulder, wide sleeve\nPre-shrunk",
        SIZES, "Bone", [2, 6, 8, 4, 2], "",
    ),
    (
        "Standard Regular Tee", "240gsm cotton · regular fit", "mens-regular-tees",
        5400, None, "tee-5", True,
        "A straight regular-fit tee at a weight you can train in. Set-in "
        "sleeve, true-to-size body, no drape.",
        "240gsm combed ring-spun cotton\nRegular fit, set-in sleeve\n"
        "True to size\nRibbed collar, twin-needle hems\nPre-shrunk",
        SIZES, "Black", [8, 22, 26, 18, 9], "",
    ),
    (
        "Standard Regular Tee — Bone", "240gsm cotton · regular fit",
        "mens-regular-tees",
        5400, None, "tee-1", False,
        "The Standard Regular in bone.",
        "240gsm combed ring-spun cotton\nRegular fit, set-in sleeve\n"
        "True to size\nPre-shrunk",
        SIZES, "Bone", [6, 15, 18, 11, 5], "",
    ),
    (
        "Utility Work Shirt", "Washed twill · boxy", "mens-work-shirts",
        11800, None, "workshirt-0", True,
        "A boxy work shirt in washed cotton twill. Two chest pockets, a "
        "straight hem, and enough room to wear over a tee.",
        "280gsm washed cotton twill\nBoxy fit, straight hem\n"
        "Two chest pockets with flaps\nCorozo buttons\n"
        "Reinforced yoke\nWears open or closed",
        SIZES, "Black", [3, 9, 12, 8, 4], "",
    ),
    (
        "Utility Work Shirt — Bone", "Washed twill · boxy", "mens-work-shirts",
        11800, 13600, "workshirt-1", False,
        "The Utility Work Shirt in bone, marked down on the last of the run.",
        "280gsm washed cotton twill\nBoxy fit, straight hem\n"
        "Two chest pockets with flaps\nCorozo buttons",
        SIZES, "Bone", [1, 4, 5, 3, 1], "",
    ),

    # ------------------------------------------------------------- women
    (
        "Oversized Tee", "320gsm cotton · oversized", "womens-oversized-tees",
        6800, None, "tee-2", True,
        "The oversized body cut for a women's fit through the shoulder, with "
        "a shorter sleeve and a hem that sits at the hip.",
        "320gsm combed ring-spun cotton\nOversized fit, shorter sleeve\n"
        "Hip-length hem\nRibbed collar\nGarment dyed and pre-shrunk",
        WOMENS_SIZES, "Washed Black", [7, 19, 22, 13, 6], "",
    ),
    (
        "Oversized Tee — Bone", "320gsm cotton · oversized",
        "womens-oversized-tees",
        6800, None, "tee-3", False,
        "The women's Oversized Tee in bone.",
        "320gsm combed ring-spun cotton\nOversized fit, shorter sleeve\n"
        "Hip-length hem\nGarment dyed and pre-shrunk",
        WOMENS_SIZES, "Bone", [4, 12, 14, 8, 3], "",
    ),
    (
        "Cropped Tee", "260gsm cotton · boxy crop", "womens-crop-tops",
        5600, None, "croptop-0", True,
        "A boxy crop with a raw-edge hem that sits just above the waistband. "
        "Heavy enough to hold its shape through a session.",
        "260gsm combed cotton\nBoxy crop, raw-edge hem\n"
        "Dropped shoulder\nRibbed collar\nPre-shrunk",
        WOMENS_SIZES, "Black", [6, 16, 18, 10, 4], "",
    ),
    (
        "Cropped Tee — Bone", "260gsm cotton · boxy crop", "womens-crop-tops",
        5600, 6800, "croptop-1", False,
        "The Cropped Tee in bone, reduced on the last of the run.",
        "260gsm combed cotton\nBoxy crop, raw-edge hem\n"
        "Dropped shoulder\nPre-shrunk",
        WOMENS_SIZES, "Bone", [2, 5, 7, 3, 1], "",
    ),
    (
        "Training Short 4\"", "Four-way stretch · lined", "womens-training-shorts",
        6400, None, "shorts-0", True,
        "A four-inch training short with a bonded high waistband that does not "
        "roll, and a liner that stays where it should.",
        "4\" inseam with inner liner\nFour-way stretch shell\n"
        "Bonded high waistband\nZip back pocket\nSquat-proof weave",
        WOMENS_SIZES, "Black", [5, 15, 17, 9, 4], "",
    ),
    (
        "Training Short 6\"", "Four-way stretch · lined", "womens-training-shorts",
        6400, None, "shorts-3", False,
        "The six-inch cut, for longer sessions and more coverage.",
        "6\" inseam with inner liner\nFour-way stretch shell\n"
        "Bonded high waistband\nZip back pocket",
        WOMENS_SIZES, "Slate", [3, 10, 12, 6, 2], "",
    ),
    (
        "Form Sports Bra", "Medium support · bonded", "womens-training-sets",
        5800, None, "sportsbra-0", True,
        "Medium support, bonded edges, no clasps and no seams to rub. Cut to "
        "be worn on its own.",
        "Medium support\nBonded edges, no clasps\n"
        "Removable cups\nFour-way stretch\nSquat-proof weave",
        WOMENS_SIZES, "Black", [6, 17, 19, 11, 5], "",
    ),
    (
        "Form Sports Bra — Bone", "Medium support · bonded",
        "womens-training-sets",
        5800, 6900, "sportsbra-1", False,
        "The Form Sports Bra in bone, reduced.",
        "Medium support\nBonded edges, no clasps\n"
        "Removable cups\nFour-way stretch",
        WOMENS_SIZES, "Bone", [2, 6, 8, 4, 2], "",
    ),
    (
        "Form Training Tank", "Ribbed · cropped", "womens-training-sets",
        5200, None, "tank-2", False,
        "A ribbed training tank cut to sit over the Form bra, cropped to the "
        "same line.",
        "Ribbed cotton-modal\nCropped to the waistband\n"
        "Dropped armhole\nWears over the Form bra",
        WOMENS_SIZES, "Charcoal", [4, 12, 14, 7, 3], "",
    ),
    (
        "Form Training Legging", "Four-way stretch · high waist",
        "womens-training-sets",
        8800, None, "joggers-0", True,
        "A high-waisted legging in the same four-way stretch as the shorts, "
        "with a hidden waistband pocket.",
        "Four-way stretch poly-elastane\nHigh bonded waistband\n"
        "Hidden waistband pocket\nSquat-proof weave\nFlatlock seams",
        WOMENS_SIZES, "Black", [4, 13, 15, 8, 3], "",
    ),
]

DISCOUNTS = [
    ("WELCOME10", "percent", 10, 0, None),
    ("FREESHIP", "free_shipping", 0, 5000, 200),
    ("MOG25", "fixed", 2500, 12000, 50),
]


def run(*, staff_email: str | None = None, staff_password: str | None = None,
        quiet: bool = False) -> dict:
    """Populate an empty database.  Idempotent: existing rows are left alone."""
    if staff_email is None or staff_password is None:
        env_email, env_password = admin_credentials()
        staff_email = staff_email if staff_email is not None else env_email
        staff_password = staff_password if staff_password is not None else env_password
    created = {"categories": 0, "products": 0, "variants": 0,
               "discounts": 0, "staff": 0}

    with db.tx():
        for slug, title, department, position in CATEGORIES:
            if db.one("SELECT id FROM collections WHERE slug = ?", (slug,)):
                continue
            db.insert("collections", slug=slug, title=title,
                      department=department, position=position)
            created["categories"] += 1

        category_ids = {
            r["slug"]: r["id"] for r in db.query("SELECT id, slug FROM collections")
        }

        for position, entry in enumerate(PRODUCTS):
            (title, subtitle, category, price, compare, seed, is_featured,
             description, details, sizes, color, stock_plan, images) = entry
            slug = _unique_slug(title, category)
            if db.one("SELECT id FROM products WHERE slug = ?", (slug,)):
                continue
            product_id = db.insert(
                "products", slug=slug, title=title, subtitle=subtitle,
                description=description, details=details,
                collection_id=category_ids.get(category),
                price_cents=price, compare_cents=compare, art_seed=seed,
                images=images,
                featured=1 if is_featured else 0, position=position,
            )
            created["products"] += 1
            prefix = "".join(w[0] for w in title.split()[:3] if w[0].isalpha()).upper()
            for index, size in enumerate(sizes):
                stock = stock_plan[index] if index < len(stock_plan) else 5
                db.insert(
                    "variants", product_id=product_id,
                    sku=f"MOG-{prefix or 'X'}-{size.replace('/', '')}-{product_id:03d}",
                    size=size, color=color, stock=stock, position=index,
                )
                created["variants"] += 1

        for code, kind, value, min_spend, max_uses in DISCOUNTS:
            if db.one("SELECT id FROM discounts WHERE code = ?", (code,)):
                continue
            db.insert("discounts", code=code, kind=kind, value=value,
                      min_spend_cents=min_spend, max_uses=max_uses)
            created["discounts"] += 1

        if staff_email and staff_password and \
                not db.one("SELECT id FROM users WHERE email = ?", (staff_email,)):
            db.insert(
                "users", email=staff_email,
                password_hash=hash_password(staff_password),
                name="MOG Admin", role="admin",
            )
            created["staff"] += 1

    if created["products"]:
        audit("seed.run", detail=str(created))
    if not quiet and not staff_email and config.is_production \
            and not db.scalar("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"):
        print("  no admin account yet: run  python3 run.py --create-admin you@example.com")
    if not quiet:
        print(
            f"  seeded: {created['categories']} categories, "
            f"{created['products']} products, {created['variants']} variants, "
            f"{created['discounts']} discount codes, {created['staff']} staff user"
        )
    return created


def _unique_slug(title: str, category: str) -> str:
    """Titles repeat across departments, so prefix with the category."""
    base = slugify(title, fallback="product")
    prefix = category.split("-")[0]
    if prefix in ("mens", "womens") and not base.startswith(prefix):
        return f"{prefix}-{base}"
    return base
