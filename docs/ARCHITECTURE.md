# Architecture

## Shape of the thing

```
                    ┌──────────────────────────────────────────┐
  browser  ─────────▶  ThreadingHTTPServer  (app/web.py)       │
                    │    ↓                                      │
                    │  middleware  (app/application.py)         │
                    │    · https redirect                       │
                    │    · session      → request.user          │
                    │    · cart         → request.cart_id       │
                    │    · csrf         (unsafe methods only)   │
                    │    ↓                                      │
                    │  router → views_shop / _account           │
                    │           / _checkout / _admin            │
                    │    ↓                                      │
                    │  domain: catalog · cart · orders          │
                    │          accounts · discounts · mailer    │
                    │    ↓                                      │
                    │  app/db.py  →  SQLite (WAL)               │
                    └──────────────────────────────────────────┘
                             │                    │
                    Stripe REST (urllib)     SMTP (smtplib)
```

No framework, no ORM, no template engine, no bundler. Every dependency is in the
standard library.

---

## Request lifecycle

1. `BaseHTTPRequestHandler` parses the socket and builds a `Request`
   (method, path, query, headers, body, cookies, client IP — honouring
   `X-Forwarded-For` behind a proxy).
2. Middleware runs outside-in, each one wrapping the next:
   - **https_redirect** — 301 to HTTPS when `MOG_FORCE_HTTPS` is on.
   - **session_layer** — reads the signed session cookie, loads the row, attaches
     `request.user`. Issues a fresh anonymous session when there isn't one.
   - **cart_layer** — resolves the signed cart cookie to a cart id, claiming the
     cart for the user if they are signed in.
   - **csrf_layer** — for POST/PUT/PATCH/DELETE, compares the submitted token
     against the session's with `hmac.compare_digest`, and independently checks
     the `Origin`/`Referer` host. `/webhooks/stripe` is exempt because it
     authenticates by Stripe signature instead.
3. The router matches the path against compiled patterns with typed converters
   (`<int:order_id>`, `<slug:slug>`, `<path:filename>`) and calls the view.
4. The view returns a `Response`; the handler adds security headers and writes it.
5. `db.close()` releases the thread's connection.

Errors raised as `HttpError(status, message)` render a branded page, or JSON when
the client asked for it.

---

## Data model

`app/schema.sql` is the single source of truth, applied idempotently on boot.

**Catalogue** — `collections` → `products` → `variants`. A variant carries the
SKU, size, colour, optional price override, `stock` and `reserved`.
`products_fts` is an FTS5 index kept in sync by triggers.

**Identity** — `users`, `sessions` (server-side, addressed by a signed cookie),
`addresses`.

**Commerce** — `carts` → `cart_items`; `orders` → `order_items`; `discounts`.

**Operations** — `email_outbox`, `audit_log`, `webhook_events` (idempotency),
`newsletter`, `messages`, `rate_limits`.

Two conventions run throughout: **money is integer cents**, and **order items
snapshot their product data** (title, SKU, variant label, unit price) so a
historical order never changes when the catalogue does.

---

## The inventory state machine

This is the part worth reading twice.

```
      on hand: stock            sellable: stock − reserved
  ┌─────────────────────────────────────────────────────────┐
  │                                                         │
  │   add to cart      no effect on stock                    │
  │        │                                                 │
  │        ▼                                                 │
  │   create order ──── reserve(n) ──── reserved += n        │
  │        │                                                 │
  │        ├── paid ─── commit(n) ───── stock −= n           │
  │        │                            reserved −= n        │
  │        │                                                 │
  │        ├── cancelled ── release(n) ─ reserved −= n       │
  │        │                                                 │
  │        └── expired ──── release(n) ─ reserved −= n       │
  │              (sweeper, after 45 minutes)                 │
  │                                                          │
  │   refund ─────────── restock(n) ─── stock += n           │
  └─────────────────────────────────────────────────────────┘
```

A cart holds nothing. Reservation happens only at order creation, inside one
`BEGIN IMMEDIATE` transaction, using a conditional update:

```sql
UPDATE variants SET reserved = reserved + :n
 WHERE id = :id AND stock - reserved >= :n
```

Zero rows changed means someone else got there first, so `OutOfStock` is raised
and the whole transaction — including every earlier line's reservation — rolls
back. Two shoppers cannot both buy the last unit.

Abandoned checkouts would otherwise hold stock forever, so a daemon thread
(`application.start_maintenance`) cancels pending orders older than 45 minutes,
releases their reservations, retries queued email and prunes expired sessions.

---

## Payments

`app/stripe_api.py` speaks the Stripe REST API over `urllib`: form-encoded
bodies with bracket notation for nested structures, bearer auth, an
`Idempotency-Key` on every mutating call.

**Outbound.** `create_checkout_session` builds a Checkout Session from the
order's line items and returns a URL to redirect to. Card details are entered on
Stripe's own page and never reach this server — which is what makes PCI scope
small enough to ignore.

**Inbound.** Two independent paths confirm payment:

- the customer returning to `/checkout/complete`, which retrieves the session, and
- `POST /webhooks/stripe`, which is authoritative.

Both call `orders.mark_paid`, which is idempotent by construction:

```sql
UPDATE orders SET status = 'paid', ... WHERE id = :id AND status = 'pending'
```

If that changes no rows, the work has already been done and the function returns
early. Whichever path arrives first wins; the other is a no-op. Stock is never
depleted twice, a receipt is never sent twice, a discount is never counted twice.

Webhook authenticity is checked by `verify_webhook`: parse `Stripe-Signature`,
reject a timestamp outside ±300s, recompute `HMAC-SHA256(secret, "t.payload")`
and compare in constant time. Event ids are stored in `webhook_events`, so a
replayed delivery is recognised and dropped.

**Demo mode.** With no API key, `create_checkout_session` returns a synthetic
session pointing at the local confirmation route. Every other step — order
creation, inventory, email, admin — runs exactly as it does in production.

---

## Presentation

There is no template engine. `app/ui.py` exposes composable functions
(`layout`, `product_card`, `field`, `table`, `admin_layout`) that return HTML
strings, with `E()` escaping every interpolated value. Python's f-strings do the
composing; the type checker and the test suite do the rest.

`app/art.py` generates product imagery as layered SVG: nine garment silhouettes
× six tonal variations, each rendered with a soft ground, a cast shadow and a
film grain. Output is deterministic, so images cache forever — and because they
are served `immutable`, their URLs carry `ART_VERSION`, a hash of the module's
own source, so editing a silhouette invalidates every cached image.

The stylesheet is one file of custom properties. The palette inverts under
`prefers-color-scheme: dark`, the type scale is fluid `clamp()`, and every hex
value is neutral — enforced by a test, not a convention.

Client-side JavaScript is strictly an enhancement. Add-to-bag, cart quantities,
filters, sorting and the newsletter all work without it.

---

## Security posture

| Concern | Measure |
| --- | --- |
| Password storage | PBKDF2-HMAC-SHA256, 240k rounds, 16-byte salt, constant-time verify |
| Session theft | Server-side sessions keyed by an HMAC-signed cookie; `HttpOnly`, `SameSite=Lax`, `Secure` in production |
| Session fixation | A new session id is issued on every sign-in and registration |
| CSRF | Per-session token on every unsafe request, plus an `Origin`/`Referer` host check |
| Brute force | Per-IP token bucket on login; account lockout after 6 failures |
| Enumeration | Identical error text for unknown email and wrong password, with a decoy hash to match timing |
| SQL injection | Parameterised queries throughout; FTS input is sanitised and falls back to `LIKE` |
| XSS | `E()` escapes at every interpolation; CSP forbids inline and third-party script |
| Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` |
| Path traversal | Static paths are resolved and confirmed to sit under the static root |
| Privilege escalation | `require_staff` on every admin route, checked against the DB role |
| Payment data | Never touches this server — Stripe Checkout holds it |
| Forged webhooks | HMAC signature, timestamp tolerance, replay table |
| Transport | HSTS and an HTTPS redirect when `MOG_FORCE_HTTPS` is on |
| Auditability | Every privileged action is written to `audit_log` |

---

## Where it would change under load

SQLite with WAL handles this store comfortably — a single writer, many readers,
and writes measured in orders per minute. The seams for growth are deliberate:

- **`app/db.py`** is the only module that knows the database is SQLite. Swapping
  in PostgreSQL means reimplementing `connect`, `tx` and the handful of helpers.
- **`start_maintenance`** is an in-process thread. At scale it becomes a cron
  job calling the same functions.
- **`mailer.flush`** already works as a queue drain; point it at a worker.
- **Static and generated media** are pure functions of their inputs, so they sit
  behind a CDN without any change.
