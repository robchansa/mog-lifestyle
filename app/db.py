"""SQLite access layer.

One connection per thread (the HTTP server is threaded), foreign keys on,
WAL journaling, and a `tx()` context manager that makes "reserve stock then
create the order" genuinely atomic.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .config import config

_local = threading.local()
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect() -> sqlite3.Connection:
    """Return this thread's connection, creating it on first use."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(config.database),
            timeout=15.0,
            isolation_level=None,          # explicit transactions via tx()
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 15000")
        _local.conn = conn
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Atomic unit of work.  Nested uses join the outer transaction."""
    conn = connect()
    if getattr(_local, "depth", 0):
        _local.depth += 1
        try:
            yield conn
        finally:
            _local.depth -= 1
        return

    _local.depth = 1
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    finally:
        _local.depth = 0


# ----------------------------------------------------------------- helpers

def query(sql: str, params: Sequence[Any] | dict = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def one(sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def scalar(sql: str, params: Sequence[Any] | dict = (), default: Any = None) -> Any:
    row = one(sql, params)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def execute(sql: str, params: Sequence[Any] | dict = ()) -> sqlite3.Cursor:
    return connect().execute(sql, params)


def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
    return connect().executemany(sql, seq)


def insert(table: str, **values: Any) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    return int(cur.lastrowid)


def update(table: str, where: str, params: Sequence[Any], **values: Any) -> int:
    sets = ", ".join(f"{k} = ?" for k in values)
    cur = execute(
        f"UPDATE {table} SET {sets} WHERE {where}", [*values.values(), *params]
    )
    return cur.rowcount


# --------------------------------------------------------------- lifecycle

# Columns added after the first release.  `schema.sql` stays the source of
# truth for fresh installs; this brings existing databases up to match.
ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("products", "images", "TEXT NOT NULL DEFAULT ''"),
    ("collections", "department", "TEXT NOT NULL DEFAULT 'general'"),
    ("orders", "refunded_at", "TEXT"),
    ("order_items", "department", "TEXT NOT NULL DEFAULT ''"),
    ("order_items", "on_sale", "INTEGER NOT NULL DEFAULT 0"),
)


def migrate() -> None:
    """Apply the schema.  Safe to run on every boot.

    Column migrations run on both sides of the schema script: before it, so an
    existing table gains a column that a new index in the script depends on;
    after it, so a table the script just created is brought up to date too.
    Both passes are idempotent.
    """
    conn = connect()
    added = _apply_column_migrations(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    added |= _apply_column_migrations(conn)
    if ("order_items", "department") in added:
        backfill_order_item_categories(conn)
    if not conn.execute("SELECT 1 FROM visits LIMIT 1").fetchone():
        rebuild_visits(conn)
    _rebuild_fts_if_empty(conn)


def _apply_column_migrations(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    added: set[tuple[str, str]] = set()
    for table, column, definition in ADDED_COLUMNS:
        if _ensure_column(conn, table, column, definition):
            added.add((table, column))
    return added


def _ensure_column(conn: sqlite3.Connection, table: str, column: str,
                   definition: str) -> bool:
    """Add the column if it is missing.  True when it was just added."""
    columns = list(conn.execute(f"PRAGMA table_info({table})"))
    if not columns:
        return False                # table does not exist yet
    if column in {row["name"] for row in columns}:
        return False
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    except sqlite3.OperationalError as exc:
        # Several web workers booting at once can race to add the same column;
        # whoever loses finds it already there, which is the goal.
        if "duplicate column" in str(exc).lower():
            return False
        raise
    return True


def backfill_order_item_categories(conn: sqlite3.Connection | None = None) -> int:
    """Attribute orders placed before the snapshot columns existed.

    New lines record their department and markdown at checkout.  Older lines
    are matched to the product they came from -- by variant, or by slug when the
    variant has since been deleted -- and take its current department and sale
    state, which is the best evidence left.  Lines whose product is gone stay
    blank and are reported as "Unassigned" rather than guessed.
    """
    conn = conn or connect()
    product_for_line = (
        "COALESCE("
        " (SELECT v.product_id FROM variants v WHERE v.id = order_items.variant_id),"
        " (SELECT p.id FROM products p WHERE p.slug = order_items.slug))"
    )
    cur = conn.execute(
        f"UPDATE order_items SET "
        f" department = COALESCE((SELECT c.department FROM products p "
        f"   JOIN collections c ON c.id = p.collection_id "
        f"   WHERE p.id = {product_for_line}), ''), "
        f" on_sale = COALESCE((SELECT p.compare_cents IS NOT NULL "
        f"   AND p.compare_cents > order_items.unit_cents FROM products p "
        f"   WHERE p.id = {product_for_line}), 0) "
        f"WHERE department = ''"
    )
    return cur.rowcount


def rebuild_visits(conn: sqlite3.Connection | None = None) -> int:
    """Derive per-visitor-day rows from raw page views.

    Runs once when the `visits` table first appears on a database that already
    has traffic, and after bulk imports.  Existing visits are left untouched.
    """
    conn = conn or connect()
    cur = conn.execute(
        "INSERT OR IGNORE INTO visits (visitor, started_at, views, device, referrer, "
        "  entry_path, saw_product, saw_cart, saw_checkout) "
        "SELECT pv.visitor, MIN(pv.created_at), count(*), MAX(pv.device), "
        "  COALESCE((SELECT r.referrer FROM page_views r WHERE r.visitor = pv.visitor "
        "            AND r.referrer <> '' ORDER BY r.id LIMIT 1), ''), "
        "  (SELECT e.path FROM page_views e WHERE e.visitor = pv.visitor "
        "   ORDER BY e.id LIMIT 1), "
        "  MAX(pv.kind = 'product'), MAX(pv.kind = 'cart'), MAX(pv.kind = 'checkout') "
        "FROM page_views pv GROUP BY pv.visitor"
    )
    return cur.rowcount


def _rebuild_fts_if_empty(conn: sqlite3.Connection) -> None:
    products = conn.execute("SELECT count(*) FROM products").fetchone()[0]
    if not products:
        return
    indexed = conn.execute("SELECT count(*) FROM products_fts").fetchone()[0]
    if indexed != products:
        conn.execute("INSERT INTO products_fts(products_fts) VALUES ('rebuild')")


def reset() -> None:
    """Drop every object.  Used by the test-suite and `run.py --reset`."""
    conn = connect()
    conn.executescript(
        """
        PRAGMA writable_schema = 1;
        PRAGMA foreign_keys = OFF;
        """
    )
    for kind in ("trigger", "view", "index", "table"):
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (kind,),
        ).fetchall()
        for row in rows:
            try:
                conn.execute(f'DROP {kind} IF EXISTS "{row["name"]}"')
            except sqlite3.OperationalError:
                pass  # FTS shadow tables disappear with their virtual table
    conn.execute("PRAGMA foreign_keys = ON")
