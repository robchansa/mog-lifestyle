"""Products, variants, search, filtering and inventory.

Search uses SQLite FTS5 with a LIKE fallback, so a missing FTS build degrades
to something still useful rather than erroring.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from . import db
from .security import slugify

PAGE_SIZE = 12
DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("men", "Men"),
    ("women", "Women"),
    ("general", "Everyone"),
)
DEPARTMENT_LABELS = dict(DEPARTMENTS)
# A product is on sale when it carries a higher compare-at price.
ON_SALE = "p.compare_cents IS NOT NULL AND p.compare_cents > p.price_cents"
SORTS = {
    "featured": "p.featured DESC, p.position ASC, p.id ASC",
    "new": "p.created_at DESC, p.id DESC",
    "price-asc": "p.price_cents ASC, p.id ASC",
    "price-desc": "p.price_cents DESC, p.id ASC",
    "name": "p.title COLLATE NOCASE ASC",
}
SORT_LABELS = [
    ("featured", "Featured"),
    ("new", "Newest"),
    ("price-asc", "Price: low to high"),
    ("price-desc", "Price: high to low"),
    ("name", "Alphabetical"),
]

# `stock` on a product row means *sellable* stock: on hand minus reserved.
_PRODUCT_COLUMNS = """
    p.*,
    c.slug       AS collection_slug,
    c.title      AS collection_title,
    COALESCE(c.department, 'general') AS department,
    COALESCE((SELECT SUM(MAX(v.stock - v.reserved, 0))
              FROM variants v WHERE v.product_id = p.id), 0) AS stock,
    (SELECT MIN(COALESCE(v.price_cents, p.price_cents))
     FROM variants v WHERE v.product_id = p.id)              AS min_price_cents
"""


@dataclass
class Filters:
    q: str = ""
    department: str = ""
    collection: str = ""
    sizes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    max_price: int = 0
    in_stock: bool = False
    on_sale: bool = False
    sort: str = "featured"
    page: int = 1

    @classmethod
    def from_request(cls, request: Any) -> "Filters":
        sort = request.get("sort", "featured")
        department = request.get("department")[:10]
        return cls(
            q=request.get("q")[:80],
            department=department if department in DEPARTMENT_LABELS else "",
            collection=request.get("collection")[:60],
            sizes=tuple(s.upper()[:6] for s in request.get_list("size")[:12]),
            colors=tuple(c[:24] for c in request.get_list("color")[:12]),
            max_price=max(0, request.get_int("max_price", 0)),
            in_stock=request.checked("in_stock"),
            on_sale=request.checked("on_sale"),
            sort=sort if sort in SORTS else "featured",
            page=max(1, request.get_int("page", 1)),
        )

    def query_string(self, **overrides: Any) -> str:
        import urllib.parse
        pairs: list[tuple[str, str]] = []
        data = {
            "q": self.q, "department": self.department,
            "collection": self.collection, "sort": self.sort,
            "max_price": self.max_price or "",
            "in_stock": "1" if self.in_stock else "",
            "on_sale": "1" if self.on_sale else "",
        }
        data.update({k: ("" if v is None else v) for k, v in overrides.items()})
        for key, value in data.items():
            if value not in ("", 0, None) and key != "page":
                pairs.append((key, str(value)))
        for size in overrides.get("sizes", self.sizes):
            pairs.append(("size", size))
        for color in overrides.get("colors", self.colors):
            pairs.append(("color", color))
        if overrides.get("page"):
            pairs.append(("page", str(overrides["page"])))
        return urllib.parse.urlencode(pairs)

    @property
    def active(self) -> bool:
        return bool(self.q or self.department or self.collection or self.sizes
                    or self.colors or self.max_price or self.in_stock
                    or self.on_sale)


# ------------------------------------------------------------------ reads

def list_collections(department: str = "") -> list[sqlite3.Row]:
    """Categories, optionally limited to one department."""
    where = "WHERE c.department = ?" if department else ""
    params = (department,) if department else ()
    return db.query(
        f"SELECT c.*, (SELECT count(*) FROM products p "
        f"             WHERE p.collection_id = c.id AND p.status = 'active') "
        f"            AS product_count "
        f"FROM collections c {where} ORDER BY c.position, c.title",
        params,
    )


def navigation() -> list[dict[str, Any]]:
    """The shop menu: each department with its categories.

    Empty categories are dropped so the dropdown never advertises a dead end.
    """
    menu = []
    for slug, label in DEPARTMENTS:
        categories = [c for c in list_collections(slug) if c["product_count"]]
        if categories:
            menu.append({"slug": slug, "label": label, "categories": categories})
    return menu


def sale_count() -> int:
    return int(db.scalar(
        f"SELECT count(*) FROM products p WHERE p.status = 'active' AND {ON_SALE}",
        (), 0,
    ))


def get_product(slug: str, *, include_drafts: bool = False) -> sqlite3.Row | None:
    status = "" if include_drafts else "AND p.status = 'active'"
    return db.one(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.slug = ? {status}",
        (slug,),
    )


def get_product_by_id(product_id: int) -> sqlite3.Row | None:
    return db.one(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id WHERE p.id = ?",
        (product_id,),
    )


def variants_for(product_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT v.*, COALESCE(v.price_cents, p.price_cents) AS effective_cents, "
        "       MAX(v.stock - v.reserved, 0) AS available "
        "FROM variants v JOIN products p ON p.id = v.product_id "
        "WHERE v.product_id = ? ORDER BY v.position, v.id",
        (product_id,),
    )


def get_variant(variant_id: int) -> sqlite3.Row | None:
    return db.one(
        "SELECT v.*, COALESCE(v.price_cents, p.price_cents) AS effective_cents, "
        "       MAX(v.stock - v.reserved, 0) AS available, "
        "       p.title, p.slug, p.art_seed, p.status "
        "FROM variants v JOIN products p ON p.id = v.product_id WHERE v.id = ?",
        (variant_id,),
    )


def facets() -> dict[str, list[str]]:
    """Distinct sizes and colours across the live catalogue, in wearable order."""
    order = ["XS", "S", "M", "L", "XL", "XXL"]
    sizes = [
        r["size"] for r in db.query(
            "SELECT DISTINCT v.size FROM variants v JOIN products p ON p.id = v.product_id "
            "WHERE p.status = 'active' AND v.size <> ''"
        )
    ]
    sizes.sort(key=lambda s: (order.index(s) if s in order else 99, s))
    colors = [
        r["color"] for r in db.query(
            "SELECT DISTINCT v.color FROM variants v JOIN products p ON p.id = v.product_id "
            "WHERE p.status = 'active' AND v.color <> '' ORDER BY v.color"
        )
    ]
    return {"sizes": sizes, "colors": colors}


def search(filters: Filters, *, page_size: int = PAGE_SIZE,
           include_drafts: bool = False) -> tuple[list[sqlite3.Row], int]:
    """Return (products, total_matches) for the given filters."""
    where = ["1 = 1"]
    params: list[Any] = []

    if not include_drafts:
        where.append("p.status = 'active'")
    if filters.department:
        where.append("COALESCE(c.department, 'general') = ?")
        params.append(filters.department)
    if filters.collection:
        where.append("c.slug = ?")
        params.append(filters.collection)
    if filters.on_sale:
        where.append(ON_SALE)
    if filters.max_price:
        where.append("p.price_cents <= ?")
        params.append(filters.max_price)
    if filters.sizes:
        marks = ", ".join("?" * len(filters.sizes))
        where.append(
            f"EXISTS (SELECT 1 FROM variants v WHERE v.product_id = p.id "
            f"AND v.size IN ({marks}))"
        )
        params.extend(filters.sizes)
    if filters.colors:
        marks = ", ".join("?" * len(filters.colors))
        where.append(
            f"EXISTS (SELECT 1 FROM variants v WHERE v.product_id = p.id "
            f"AND v.color IN ({marks}))"
        )
        params.extend(filters.colors)
    if filters.in_stock:
        where.append(
            "EXISTS (SELECT 1 FROM variants v WHERE v.product_id = p.id "
            "AND v.stock - v.reserved > 0)"
        )

    join = "LEFT JOIN collections c ON c.id = p.collection_id"
    order = SORTS[filters.sort]

    if filters.q:
        matched = _fts_ids(filters.q)
        if not matched:
            return [], 0
        marks = ", ".join("?" * len(matched))
        where.append(f"p.id IN ({marks})")
        params.extend(matched)
        if filters.sort == "featured":
            # Preserve relevance ordering from FTS.
            ranking = " ".join(
                f"WHEN {pid} THEN {i}" for i, pid in enumerate(matched)
            )
            order = f"CASE p.id {ranking} END"

    clause = " AND ".join(where)
    total = db.scalar(
        f"SELECT count(*) FROM products p {join} WHERE {clause}", params, 0
    )
    offset = (filters.page - 1) * page_size
    rows = db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p {join} "
        f"WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    )
    return rows, int(total)


def _fts_ids(term: str, limit: int = 300) -> list[int]:
    """Best-effort relevance search; falls back to LIKE when FTS can't parse."""
    cleaned = re.sub(r'[^\w\s]', " ", term).strip()
    if not cleaned:
        return []
    match = " OR ".join(f'"{word}"*' for word in cleaned.split()[:8])
    try:
        rows = db.query(
            "SELECT rowid FROM products_fts WHERE products_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (match, limit),
        )
        if rows:
            return [r["rowid"] for r in rows]
    except sqlite3.OperationalError:
        pass
    like = f"%{cleaned}%"
    return [
        r["id"] for r in db.query(
            "SELECT id FROM products WHERE title LIKE ? OR subtitle LIKE ? "
            "OR description LIKE ? LIMIT ?",
            (like, like, like, limit),
        )
    ]


def related(product: sqlite3.Row, limit: int = 4) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.status = 'active' AND p.id <> ? "
        f"ORDER BY (p.collection_id = ?) DESC, p.featured DESC, RANDOM() LIMIT ?",
        (product["id"], product["collection_id"], limit),
    )


def for_department(department: str, limit: int = 4) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.status = 'active' AND COALESCE(c.department, 'general') = ? "
        f"ORDER BY p.featured DESC, p.position, p.id LIMIT ?",
        (department, limit),
    )


def on_sale(limit: int = 4) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.status = 'active' AND {ON_SALE} "
        f"ORDER BY (p.compare_cents - p.price_cents) DESC, p.id LIMIT ?",
        (limit,),
    )


def featured(limit: int = 8) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.status = 'active' "
        f"ORDER BY p.featured DESC, p.position, p.id LIMIT ?",
        (limit,),
    )


def newest(limit: int = 4) -> list[sqlite3.Row]:
    return db.query(
        f"SELECT {_PRODUCT_COLUMNS} FROM products p "
        f"LEFT JOIN collections c ON c.id = p.collection_id "
        f"WHERE p.status = 'active' ORDER BY p.created_at DESC, p.id DESC LIMIT ?",
        (limit,),
    )


# --------------------------------------------------------------- imagery

def image_list(product: Any) -> list[str]:
    """Every image URL for a product, real photography first.

    Products carry `images` (newline-separated static paths) once real
    photography exists; until then `art_seed` drives the generated studio
    still.  Callers never need to know which they got.
    """
    from . import art
    try:
        raw = product["images"] or ""
    except (KeyError, IndexError, TypeError):
        raw = ""
    photos = [line.strip() for line in raw.splitlines() if line.strip()]
    if photos:
        return photos
    try:
        seed = product["art_seed"] or "tee-0"
    except (KeyError, IndexError, TypeError):
        seed = "tee-0"
    shape, index = art.parse_seed(seed)
    alternate = f"{shape}-{(index + 3) % len(art.VARIATIONS)}"
    return [art.media_url(seed), art.media_url(alternate)]


def primary_image(product: Any) -> str:
    return image_list(product)[0]


def has_photography(product: Any) -> bool:
    try:
        return bool((product["images"] or "").strip())
    except (KeyError, IndexError, TypeError):
        return False


# ------------------------------------------------------------- inventory

class OutOfStock(Exception):
    """Raised when a reservation cannot be satisfied."""

    def __init__(self, variant_id: int, requested: int, available: int):
        super().__init__(
            f"Only {available} left (requested {requested})" if available
            else "That size just sold out"
        )
        self.variant_id = variant_id
        self.requested = requested
        self.available = available


def reserve(variant_id: int, quantity: int) -> None:
    """Move `quantity` units from available into reserved.  Caller holds a tx."""
    changed = db.execute(
        "UPDATE variants SET reserved = reserved + ? "
        "WHERE id = ? AND stock - reserved >= ?",
        (quantity, variant_id, quantity),
    ).rowcount
    if not changed:
        available = db.scalar(
            "SELECT MAX(stock - reserved, 0) FROM variants WHERE id = ?",
            (variant_id,), 0,
        )
        raise OutOfStock(variant_id, quantity, int(available))


def release(variant_id: int, quantity: int) -> None:
    """Return reserved units to the available pool."""
    db.execute(
        "UPDATE variants SET reserved = MAX(reserved - ?, 0) WHERE id = ?",
        (quantity, variant_id),
    )


def commit_reservation(variant_id: int, quantity: int) -> None:
    """Payment captured: the units leave the building."""
    db.execute(
        "UPDATE variants SET stock = MAX(stock - ?, 0), "
        "reserved = MAX(reserved - ?, 0) WHERE id = ?",
        (quantity, quantity, variant_id),
    )


def restock(variant_id: int, quantity: int) -> None:
    db.execute("UPDATE variants SET stock = stock + ? WHERE id = ?",
               (quantity, variant_id))


def low_stock(threshold: int | None = None) -> list[sqlite3.Row]:
    return db.query(
        "SELECT v.*, p.title, p.slug, MAX(v.stock - v.reserved, 0) AS available "
        "FROM variants v JOIN products p ON p.id = v.product_id "
        "WHERE p.status = 'active' "
        "  AND v.stock - v.reserved <= COALESCE(?, v.low_stock_at) "
        "ORDER BY available ASC, p.title LIMIT 100",
        (threshold,),
    )


# ------------------------------------------------------------- mutations

def unique_slug(title: str, *, exclude_id: int | None = None) -> str:
    base = slugify(title, fallback="product")
    slug, suffix = base, 2
    while True:
        row = db.one("SELECT id FROM products WHERE slug = ?", (slug,))
        if row is None or (exclude_id is not None and row["id"] == exclude_id):
            return slug
        slug = f"{base}-{suffix}"
        suffix += 1


def create_product(**fields: Any) -> int:
    fields.setdefault("slug", unique_slug(fields.get("title", "")))
    with db.tx():
        return db.insert("products", **fields)


def update_product(product_id: int, **fields: Any) -> None:
    fields["updated_at"] = _now()
    with db.tx():
        db.update("products", "id = ?", (product_id,), **fields)


def upsert_variants(product_id: int, rows: Iterable[dict]) -> None:
    """Replace a product's variant set, preserving stock for matching SKUs."""
    with db.tx():
        existing = {r["sku"]: r for r in db.query(
            "SELECT * FROM variants WHERE product_id = ?", (product_id,)
        )}
        seen: set[str] = set()
        for position, row in enumerate(rows):
            sku = row["sku"].strip()
            if not sku:
                continue
            seen.add(sku)
            payload = {
                "size": row.get("size", ""),
                "color": row.get("color", ""),
                "price_cents": row.get("price_cents"),
                "stock": max(0, int(row.get("stock", 0))),
                "low_stock_at": max(0, int(row.get("low_stock_at", 3))),
                "position": position,
            }
            if sku in existing:
                db.update("variants", "id = ?", (existing[sku]["id"],), **payload)
            else:
                db.insert("variants", product_id=product_id, sku=sku, **payload)
        stale = [r["id"] for sku, r in existing.items()
                 if sku not in seen and r["reserved"] == 0]
        for variant_id in stale:
            db.execute("DELETE FROM variants WHERE id = ?", (variant_id,))


def _now() -> str:
    return db.scalar("SELECT datetime('now')")
