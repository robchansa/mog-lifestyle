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
)


def migrate() -> None:
    """Apply the schema.  Safe to run on every boot.

    Column migrations run on both sides of the schema script: before it, so an
    existing table gains a column that a new index in the script depends on;
    after it, so a table the script just created is brought up to date too.
    Both passes are idempotent.
    """
    conn = connect()
    _apply_column_migrations(conn)
    conn.executescript(SCHEMA_PATH.read_text())
    _apply_column_migrations(conn)
    _rebuild_fts_if_empty(conn)


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, column, definition in ADDED_COLUMNS:
        _ensure_column(conn, table, column, definition)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str,
                   definition: str) -> None:
    columns = list(conn.execute(f"PRAGMA table_info({table})"))
    if not columns:
        return                      # table does not exist yet
    if column not in {row["name"] for row in columns}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
