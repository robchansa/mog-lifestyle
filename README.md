# MOG Lifestyle — commerce platform

A complete e-commerce store for **moglifestyle**, built to the signed
*HesMartech Client Project Intake Form* in [`mog.pdf`](mog.pdf).

Storefront, checkout, Stripe payments, customer accounts, transactional email
and a full operations console — in **pure Python 3.9+ with zero third-party
packages** and no build step.

```bash
python3 run.py --seed
```

Then open <http://127.0.0.1:8000>. That is the entire setup.

---

## What was asked for, and where it lives

Every answer in the intake form is extracted from the PDF by
[`tools/extract_intake.py`](tools/extract_intake.py) into
[`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) and `docs/intake.json`, so the
brief is never transcribed by hand. `tests/test_brief.py` then asserts the code
still satisfies it — the requirements are executable.

| Requested in the brief | Delivered | Code |
| --- | --- | --- |
| E-commerce / online store | Catalogue, bag, checkout, orders | `catalog.py`, `cart.py`, `orders.py` |
| Business / corporate website | Home, about, contact, policy pages | `views_shop.py` |
| Web application | Accounts, admin console | `views_account.py`, `views_admin.py` |
| User registration / login | Sessions, PBKDF2, lockout, rate limits | `accounts.py`, `security.py` |
| Admin dashboard | Nine-section operations console | `views_admin.py` |
| Payment / billing system | Stripe Checkout + signed webhooks | `stripe_api.py` |
| Shopping cart | Guest + signed-in, integer-cent pricing | `cart.py` |
| Search & filter | SQLite FTS5, facets, sorting, paging | `catalog.py` |
| Email notifications | Durable outbox, four templates | `mailer.py` |
| Social media integration | OG/Twitter cards, footer links | `ui.py` |
| Inventory management | Reserve → commit → restock, low-stock alerts | `catalog.py`, `orders.py` |
| Stripe integration | Dependency-free REST client | `stripe_api.py` |
| "No — need branding too" | Client's logotype + campaign photography, with a generated fallback | `docs/BRAND.md`, `tools/build_assets.py`, `art.py` |
| Elegant / luxury, black & white | Monochrome editorial design system | `static/css/site.css` |
| Desktop + mobile web | Responsive, no separate app | `static/css/site.css` |
| Home, shop, contact, cart | All four, plus account and admin | `views_shop.py` |
| "No CMS — HesMartech manages updates" | Staff console instead of a customer CMS | `views_admin.py` |
| SSL / HTTPS | HSTS, secure cookies, redirect | `config.py`, `web.py` |
| "Suggest a technology" | Python stdlib + SQLite — see below | — |

Things the brief did **not** ask for — SMS, multi-language, iOS/Android apps,
appointment booking — were deliberately left out. `tests/test_brief.py` asserts
that too.

### Why this stack

The brief said *"suggest one"*. This runs on any machine with Python 3.9 and
nothing else: no npm, no package manager, no build, no container required, and
no supply chain to audit or patch. A US-only apparel store doing hundreds of
orders a day fits comfortably in SQLite with WAL. When the business outgrows one
box, `app/db.py` is the only module that has to change.

---

## Running it

```bash
python3 run.py                  # serve on http://127.0.0.1:8000
python3 run.py --seed           # create the demo catalogue first
python3 run.py --reset --seed   # wipe and rebuild the database
python3 run.py --port 9000
python3 run.py --check          # migrate, seed, self-test the routes, exit
python3 run.py --demo-history   # ~13 months of sample sales & traffic for Analytics
python3 run.py --demo-history clear
python3 run.py --migrate        # schema up to date, exit (deploys run this)
python3 run.py --create-admin you@example.com   # add or reset an administrator
python3 run.py --backup ~/mog-data/backups      # consistent gzipped DB copy
python3 tools/package.py        # the zip to upload to Namecheap
```

Development admin login: **`info@moglifestyle.fit` / `mog-admin-2026`**.
It exists only outside production: with `MOG_ENV=production` the seed refuses
it and the first admin comes from `--create-admin` (see
[`docs/NAMECHEAP.md`](docs/NAMECHEAP.md)).

### Demo mode

With no `STRIPE_SECRET_KEY` set, checkout completes without charging anything
while still creating the order, depleting inventory and sending the receipt — so
the whole flow is walkable offline. Set the key and it becomes real; nothing else
changes. Email behaves the same way: without `SMTP_HOST` messages print to the
console and are still recorded in the outbox.

---

## Tests

```bash
python3 tests/run_tests.py       # 493 tests, ~18 seconds
python3 tests/run_tests.py -v
python3 tests/run_tests.py test_orders.py
```

| Suite | Covers |
| --- | --- |
| `test_security.py` | Hashing, signing, tampering, rate limits, validation |
| `test_accounts.py` | Registration, lockout, session fixation, addresses |
| `test_catalog.py` | Search, filters, and the inventory state machine |
| `test_cart.py` | Cart mechanics, pricing, every discount kind |
| `test_orders.py` | Order lifecycle, idempotency, refunds, expiry |
| `test_stripe.py` | Form encoding, webhook signatures, demo mode |
| `test_mailer.py` | Outbox durability, template rendering, escaping |
| `test_web.py` | Routing, request parsing, path traversal, headers |
| `test_art.py` | Artwork generation and cache versioning |
| `test_assets.py` | Brand assets, image resolution, PNG pipeline, migrations |
| `test_departments.py` | Taxonomy, nav menus, scroll hero, homepage bands |
| `test_launch.py` | Spam traps, analytics privacy, HTTPS, cookie policy |
| `test_analytics.py` | Periods, store-local time and DST, every sales figure, category attribution, charts, admin-only access, export |
| `test_deploy.py` | The WSGI adapter, env file, packaging, production seeding, the GitHub workflow |
| `test_frontend.py` | Mobile nav, accessibility, progressive enhancement |
| `test_integration.py` | Full journeys over real HTTP, with cookies and CSRF |
| `test_brief.py` | The signed PDF, asserted against the running code |

The integration suite boots a real server on an ephemeral port and drives it
with a cookie-aware client: guest purchase end to end, CSRF rejection,
cross-origin rejection, admin access control, CSV export, fulfilment.

---

## Layout

```
run.py                  entry point — serve, seed, migrate, admin, backup
passenger_wsgi.py       entry point for Namecheap / cPanel (Passenger)
.github/workflows/      test every push; deploy main to Namecheap
app/
  config.py             environment-driven settings, safe local defaults
  db.py                 SQLite: per-thread connections, tx(), migrations
  schema.sql            full schema, applied idempotently
  security.py           PBKDF2, HMAC signing, CSRF, token-bucket rate limits
  web.py                request/response, router, static files, the server
  application.py        middleware stack, route table, maintenance thread
  ui.py                 the design system as composable HTML functions
  art.py                generated monochrome product artwork (SVG)
  catalog.py            products, variants, FTS search, inventory
  accounts.py           users, sessions, addresses
  cart.py               cart and all money arithmetic
  discounts.py          percent / fixed / free-shipping codes
  orders.py             order lifecycle and reporting
  stripe_api.py         dependency-free Stripe client and webhook verifier
  mailer.py             outbox-backed transactional email
  views_*.py            storefront, account, checkout, admin, analytics
  analytics.py          first-party cookieless traffic measurement
  reports.py            sales/customer/traffic reporting in store-local time
  charts.py             server-rendered SVG charts with accessible tables
  demo.py               tagged, removable sample history (never production)
  wsgi.py               WSGI adapter for Passenger and other WSGI hosts
  envfile.py            private KEY=value settings file outside the code
  seed.py               demo catalogue and staff user
  static/               css, js, brand assets, photography
tools/
  extract_intake.py     the PDF → requirements extractor
  build_assets.py       source artwork → site assets (logo masks, crops)
  pngkit.py             pure-Python PNG decode/encode/crop/alpha
  audit.py              pre-launch crawler: SEO, a11y, contrast, dead links
  package.py            reproducible deploy zip (no data, no secrets)
docs/                   requirements, brand, architecture, deployment, proposal
tests/                  493 tests
```

---

## Catalogue structure

Products sit in a **category**, and every category belongs to a **department**:

| Department | Categories |
| --- | --- |
| Men | Oversized T-Shirts · Regular-Fit T-Shirts · Work Shirts |
| Women | Oversized T-Shirts · Crop Tops · Training Shorts · Sports Bras & Training Sets |
| Everyone | Oversized T-Shirts · Beanies · Ski Masks · Socks |

The department lives on the category, not the product, so a product can never
disagree with its own aisle. The same title (*Oversized T-Shirts*) exists in all
three departments with distinct slugs, which is what makes the menu readable.

**Sale** is derived, not a category: a product is on sale when it carries a
`compare_cents` higher than its price. Nothing has to be re-filed to go on or
off sale, and `/shop?on_sale=1` and the homepage band stay in agreement by
construction.

`catalog.navigation()` builds the header menu from this, dropping any empty
category so the dropdown never advertises a dead end.

## Design decisions worth knowing

**Money is always integer cents.** No float touches a price anywhere;
`test_cart.py` asserts it.

**Stock and orders move together.** `orders.create_from_cart` reserves every
line inside one transaction, so two shoppers racing for the last unit cannot
both win — and if any line fails, every reservation in that order rolls back.
Payment converts reservations to depletions; cancellation and the background
sweeper hand them back.

**Payment confirmation is idempotent.** The return URL and the Stripe webhook
both call `mark_paid`, which is guarded by a conditional UPDATE, so stock is
never depleted twice and a discount is never counted twice.

**The webhook is CSRF-exempt by design** — its authenticity comes from the
Stripe signature, which `stripe_api.verify_webhook` checks with a constant-time
comparison and a timestamp tolerance. Replays are rejected by the
`webhook_events` table.

**Email is never silently lost.** Every message is written to `email_outbox`
before delivery is attempted; failures are recorded with the error and can be
retried from the admin console.

**Photography first, generated art as the fallback.** Products carry an
`images` list; `catalog.image_list()` returns those when present and otherwise
renders a monochrome studio still from `art.py`, so nothing downstream has to
know which it got. Generated stills are served `immutable`, so their URLs carry
a hash of `art.py` — editing a silhouette invalidates every cached image.

**The logotype is a mask, not a picture.** The client's supplied mark is stored
as an alpha PNG and painted with `currentColor`, so one asset serves light mode,
dark mode, the admin sidebar and anything laid over photography. There is no
second "white version" to keep in sync.

**Assets are built, not hand-cut.** `tools/build_assets.py` takes the raw
supplied artwork and produces every derived file — logo masks, favicons, 4:5
product crops split out of two-up flat-lays, campaign sizes and the social card.
Re-run it when new artwork arrives; nothing is edited by hand.

**One panel shows the whole catalogue.** Hovering, focusing or tapping any
department opens a single full-width panel listing *every* department and
*every* category with live counts — one gesture reveals the entire structure
rather than one slice of it. Each top-level item is still a real link, so the
nav works with no JavaScript and no pointer, and on a phone the panel is not a
dropdown at all: everything is simply listed.

**The hero is pinned and the page slides over it.** It is `position: sticky` at
full viewport height, full-bleed outside the content shell, with the header
sitting *over* it (transparent and inverted until you scroll past). A
`requestAnimationFrame`-throttled listener writes `--hero-progress`; CSS turns
that into a slow swell on the photograph and a lift-and-fade on the copy, while
`.page-body` — opaque — rises over the top. Under `prefers-reduced-motion` the
listener never attaches, the pin is dropped and nothing transforms.

**Rows are always full.** Each homepage band shows exactly four products against
a fixed four-column grid, because the auto-filled grid resolved to three columns
at common desktop widths and left a dangling card beside two empty cells in
every band.

**Analytics is first-party and cookieless.** A visitor is
`sha256(daily_salt + ip + user_agent)`, truncated — the salt is random per
process-day and never written down, so a visitor cannot be followed between
days, no raw address is stored, and no third party is involved. That is what
keeps the store out of consent-banner territory while still reporting the
metric the brief named: visits, and the sales they generate. See
**Admin → Traffic**.

**Spam protection without a captcha.** Two signals, both invisible: a field no
human can see (hidden from view, from the tab order and from the accessibility
tree), and a signed render timestamp that catches instant submissions. Blocked
attempts are logged and answered with a success message, because telling a bot
why it failed only helps it adapt.

**Progressive enhancement throughout.** Add-to-bag, cart quantities, filters and
the newsletter all work with JavaScript disabled; JS only removes page reloads.

---

## Further reading

- [`docs/REQUIREMENTS.md`](docs/REQUIREMENTS.md) — the client brief, extracted from the PDF
- [`docs/PROPOSAL.md`](docs/PROPOSAL.md) — scope, assumptions, and what comes next
- [`docs/BRAND.md`](docs/BRAND.md) — the identity built for the "need branding too" answer
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — how the pieces fit together
- [`docs/NAMECHEAP.md`](docs/NAMECHEAP.md) — Namecheap hosting and automatic deploys from GitHub
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — going live on a VPS instead

## Analytics

**Admin → Analytics** (`/admin/analytics`) is for administrators only — staff
accounts run the console but are refused, and are not shown the link.
Anonymous visitors are sent to sign in and brought back.

- **Summary cards** — revenue, orders, average order, units, visitors, page
  views, conversion, new customers — each with its change against the
  previous period and a trend line. Click one to chart it.
- **Ranges** 7/30/90 days, 12 months, year to date, all time, or any dates;
  grouped **daily, weekly, monthly or yearly**. Every view is a plain URL, so
  it can be bookmarked or shared with another admin.
- **Sales by category** — Men, Women and Everyone (which add up to all product
  sales) and Sale (which cuts across them), with department mix over time.
  Click a category to filter the best sellers.
- **Best-selling products**, **sales summary** (gross → discounts → net →
  shipping → tax → revenue, refunds shown separately), **customer growth**
  (accounts, first-time vs returning buyers, repeat rate), the **conversion
  funnel**, **visitors & page views** with top pages, sources and devices,
  **recent orders** and a **sales activity** feed.
- **Export CSV** of the current view (audited, spreadsheet-formula safe).

Numbers are reported in the store's own calendar (`MOG_TIMEZONE`, default
`America/Boise`) including across daylight-saving changes. Each order line
records its department and markdown at checkout, so editing the catalogue
never rewrites past reports. Traffic is summarised per visitor-day as it is
recorded, so a year of it reports in milliseconds. Charts are SVG rendered on
the server with a data table behind each one for screen readers; there is no
charting library and no inline script.

Try it locally with `python3 run.py --demo-history`, which writes 13 months of
realistic, clearly tagged sample trade; `--demo-history clear` removes exactly
that and nothing else.

## Hosting & deploys

Namecheap shared hosting runs the store through cPanel's **Setup Python App**
(`passenger_wsgi.py`). `python3 tools/package.py` builds the upload zip —
code only: never the database, a secret, the tests or the intake form.

Once the GitHub secrets are set, **every push to `main` deploys itself**:
GitHub Actions runs the whole test suite, uploads the code over SSH, migrates
the database, restarts the app and waits for `/healthz` to report the new
commit. A push that fails a test never reaches the site. Settings and the
database live in `~/mog-data`, which no deploy touches. Step by step:
[`docs/NAMECHEAP.md`](docs/NAMECHEAP.md).

## Pre-launch audit

```bash
python3 tools/audit.py http://127.0.0.1:8000
```

Crawls the running site and checks the measurable half of a launch checklist:
security headers, exposed dev files, credential-shaped strings in anything the
browser downloads, unique titles, descriptions, canonicals, OG/Twitter tags,
favicon and social image resolution, robots and sitemap, one `<h1>` per page and
no skipped heading levels, alt text on every image, labels on every control,
WCAG contrast in both themes, JavaScript weight, `console.log` leftovers, image
weight, every internal link, form validation, spam protection, privacy and terms
pages, cookie obligations and analytics. Exits non-zero on any failure, so it
works as a deploy gate.

## Rebuilding assets

```bash
python3 tools/build_assets.py /path/to/source/images
```

Sources are matched by content, not filename: a light-background sheet with two
content bands is the logo, a light-background sheet with two garments is a
flat-lay (split at the density valley between them), and anything else is
campaign photography bucketed by aspect ratio.
