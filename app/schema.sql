-- MOG Lifestyle schema.  Applied idempotently by app.db.migrate().
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- catalog
-- A "collection" is a product category.  It belongs to a department, so the
-- same label (Oversized T-Shirts) can exist under Men, Women and Everyone
-- without colliding.
CREATE TABLE IF NOT EXISTS collections (
    id          INTEGER PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    subtitle    TEXT NOT NULL DEFAULT '',
    department  TEXT NOT NULL DEFAULT 'general'
                CHECK (department IN ('general', 'men', 'women')),
    position    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_collections_department ON collections(department);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY,
    slug           TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    subtitle       TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    details        TEXT NOT NULL DEFAULT '',      -- newline separated bullets
    collection_id  INTEGER REFERENCES collections(id) ON DELETE SET NULL,
    price_cents    INTEGER NOT NULL CHECK (price_cents >= 0),
    compare_cents  INTEGER,                        -- struck-through "was" price
    art_seed       TEXT NOT NULL DEFAULT '',       -- fallback generated artwork
    images         TEXT NOT NULL DEFAULT '',       -- newline-separated asset paths
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'draft', 'archived')),
    featured       INTEGER NOT NULL DEFAULT 0,
    position       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
CREATE INDEX IF NOT EXISTS idx_products_collection ON products(collection_id);

CREATE TABLE IF NOT EXISTS variants (
    id            INTEGER PRIMARY KEY,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sku           TEXT NOT NULL UNIQUE,
    size          TEXT NOT NULL DEFAULT '',
    color         TEXT NOT NULL DEFAULT '',
    price_cents   INTEGER,                         -- NULL -> inherit product
    stock         INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    reserved      INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    low_stock_at  INTEGER NOT NULL DEFAULT 3,
    position      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_variants_product ON variants(product_id);

-- Full-text search over the catalogue, kept in sync by triggers.
CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    title, subtitle, description, details,
    content='products', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS products_fts_ai AFTER INSERT ON products BEGIN
    INSERT INTO products_fts(rowid, title, subtitle, description, details)
    VALUES (new.id, new.title, new.subtitle, new.description, new.details);
END;
CREATE TRIGGER IF NOT EXISTS products_fts_ad AFTER DELETE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, title, subtitle, description, details)
    VALUES ('delete', old.id, old.title, old.subtitle, old.description, old.details);
END;
CREATE TRIGGER IF NOT EXISTS products_fts_au AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, title, subtitle, description, details)
    VALUES ('delete', old.id, old.title, old.subtitle, old.description, old.details);
    INSERT INTO products_fts(rowid, title, subtitle, description, details)
    VALUES (new.id, new.title, new.subtitle, new.description, new.details);
END;

-- --------------------------------------------------------------- accounts
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash     TEXT NOT NULL,
    name              TEXT NOT NULL DEFAULT '',
    role              TEXT NOT NULL DEFAULT 'customer'
                      CHECK (role IN ('customer', 'staff', 'admin')),
    marketing_opt_in  INTEGER NOT NULL DEFAULT 0,
    failed_logins     INTEGER NOT NULL DEFAULT 0,
    locked_until      TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    csrf_token  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT NOT NULL,
    user_agent  TEXT NOT NULL DEFAULT '',
    ip          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS addresses (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    line1      TEXT NOT NULL,
    line2      TEXT NOT NULL DEFAULT '',
    city       TEXT NOT NULL,
    region     TEXT NOT NULL,
    postal     TEXT NOT NULL,
    country    TEXT NOT NULL DEFAULT 'US',
    phone      TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0
);

-- ------------------------------------------------------------------ cart
CREATE TABLE IF NOT EXISTS carts (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    discount_id INTEGER REFERENCES discounts(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER PRIMARY KEY,
    cart_id     TEXT NOT NULL REFERENCES carts(id) ON DELETE CASCADE,
    variant_id  INTEGER NOT NULL REFERENCES variants(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cart_id, variant_id)
);

-- -------------------------------------------------------------- discounts
CREATE TABLE IF NOT EXISTS discounts (
    id             INTEGER PRIMARY KEY,
    code           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    kind           TEXT NOT NULL CHECK (kind IN ('percent', 'fixed', 'free_shipping')),
    value          INTEGER NOT NULL DEFAULT 0,     -- percent points, or cents
    min_spend_cents INTEGER NOT NULL DEFAULT 0,
    starts_at      TEXT,
    ends_at        TEXT,
    max_uses       INTEGER,
    uses           INTEGER NOT NULL DEFAULT 0,
    active         INTEGER NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------- orders
CREATE TABLE IF NOT EXISTS orders (
    id                  INTEGER PRIMARY KEY,
    number              TEXT NOT NULL UNIQUE,
    user_id             INTEGER REFERENCES users(id) ON DELETE SET NULL,
    email               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'paid', 'fulfilled',
                                          'cancelled', 'refunded')),
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    shipping_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    discount_code       TEXT NOT NULL DEFAULT '',
    ship_name           TEXT NOT NULL DEFAULT '',
    ship_line1          TEXT NOT NULL DEFAULT '',
    ship_line2          TEXT NOT NULL DEFAULT '',
    ship_city           TEXT NOT NULL DEFAULT '',
    ship_region         TEXT NOT NULL DEFAULT '',
    ship_postal         TEXT NOT NULL DEFAULT '',
    ship_country        TEXT NOT NULL DEFAULT 'US',
    ship_phone          TEXT NOT NULL DEFAULT '',
    payment_ref         TEXT NOT NULL DEFAULT '',   -- Stripe session id
    payment_intent      TEXT NOT NULL DEFAULT '',
    tracking_carrier    TEXT NOT NULL DEFAULT '',
    tracking_number     TEXT NOT NULL DEFAULT '',
    notes               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    paid_at             TEXT,
    fulfilled_at        TEXT,
    cancelled_at        TEXT,
    refunded_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_paid ON orders(paid_at);
CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS order_items (
    id            INTEGER PRIMARY KEY,
    order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    variant_id    INTEGER REFERENCES variants(id) ON DELETE SET NULL,
    sku           TEXT NOT NULL,
    title         TEXT NOT NULL,
    variant_label TEXT NOT NULL DEFAULT '',
    slug          TEXT NOT NULL DEFAULT '',
    art_seed      TEXT NOT NULL DEFAULT '',
    unit_cents    INTEGER NOT NULL,
    quantity      INTEGER NOT NULL CHECK (quantity > 0),
    -- Snapshotted at checkout so reports describe the sale as it happened:
    -- moving a product to another department, or ending its markdown, must
    -- not rewrite last month's numbers.
    department    TEXT NOT NULL DEFAULT '',        -- men | women | general
    on_sale       INTEGER NOT NULL DEFAULT 0       -- sold below compare-at price
);
CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_users_created ON users(created_at);

-- ---------------------------------------------------- operational tables
CREATE TABLE IF NOT EXISTS email_outbox (
    id            INTEGER PRIMARY KEY,
    to_address    TEXT NOT NULL,
    subject       TEXT NOT NULL,
    body_text     TEXT NOT NULL,
    body_html     TEXT NOT NULL DEFAULT '',
    template      TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'sent', 'failed')),
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    sent_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON email_outbox(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id         INTEGER PRIMARY KEY,
    actor      TEXT NOT NULL DEFAULT 'system',
    action     TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    detail     TEXT NOT NULL DEFAULT '',
    ip         TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

CREATE TABLE IF NOT EXISTS webhook_events (
    id          TEXT PRIMARY KEY,           -- Stripe event id; enforces idempotency
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    received_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS newsletter (
    id         INTEGER PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    source     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    subject    TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    handled    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- First-party, cookieless analytics.  `visitor` is a hash of IP + user agent
-- against a salt that rotates daily and is never stored, so a visitor cannot be
-- followed across days and no raw address is retained.  This is what keeps the
-- site out of consent-banner territory: no cookie, no third party, no identity.
CREATE TABLE IF NOT EXISTS page_views (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'page',
    visitor    TEXT NOT NULL,
    referrer   TEXT NOT NULL DEFAULT '',
    device     TEXT NOT NULL DEFAULT 'desktop',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_views_created ON page_views(created_at);
CREATE INDEX IF NOT EXISTS idx_views_kind ON page_views(kind);
CREATE INDEX IF NOT EXISTS idx_views_visitor ON page_views(visitor);
-- Top pages reads only these columns, so the index alone answers it.
CREATE INDEX IF NOT EXISTS idx_views_paths ON page_views(created_at, path, visitor);

-- One row per visitor per day, kept current as each page view is recorded.
-- The daily salt means a visitor hash never spans two days, so visitors over
-- any range is simply a count of rows here -- no DISTINCT over raw page views,
-- which keeps a year of traffic reportable in milliseconds.
CREATE TABLE IF NOT EXISTS visits (
    visitor      TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    views        INTEGER NOT NULL DEFAULT 0,
    device       TEXT NOT NULL DEFAULT 'desktop',
    referrer     TEXT NOT NULL DEFAULT '',     -- first external host, if any
    entry_path   TEXT NOT NULL DEFAULT '',
    saw_product  INTEGER NOT NULL DEFAULT 0,
    saw_cart     INTEGER NOT NULL DEFAULT 0,
    saw_checkout INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visits_started ON visits(started_at);

CREATE TABLE IF NOT EXISTS rate_limits (
    bucket     TEXT PRIMARY KEY,
    tokens     REAL NOT NULL,
    updated_at REAL NOT NULL
);
