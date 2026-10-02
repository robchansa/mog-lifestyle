# Project proposal — moglifestyle

**Prepared for** Robert Chansa, moglifestyle
**Prepared by** HesMartech · info@hesmartech.com
**Source** Client Project Intake Form, signed *Rob Chansa*
**Status** Built and delivered

The declaration on the intake form authorised HesMartech to use the answers to
prepare a proposal. This is that document — written after the build, so it
describes what was actually delivered rather than what was estimated.

---

## 1 · What you asked for

> *"Sells fitness products and clothing to a community of high achievers and
> health / fitness lovers."*

A brand-new e-commerce store, aimed at the US general public aged roughly 16–85
with intermediate technical confidence, whose main goal is **to sell products
online** and whose measure of success is **"number of visits and sales generated
from those visits."**

You asked us to handle the whole operation: build it, host it, maintain it, and
manage content updates. You own `moglifestyle.fit` already and need hosting set
up. You have no brand identity yet and asked for one — elegant and luxury, black
and white, with blancc.com as a reference. Stripe is the required payment
integration. You asked us to choose the technology.

---

## 2 · What has been built

A complete, working store. Not a prototype.

**Storefront** — home, shop with search/filter/sort, product pages with size
selection and live stock, bag, checkout, order confirmation, about, contact and
four policy pages. Responsive across desktop and mobile web, which is exactly
the two platforms you checked.

**Commerce** — guest and signed-in carts, promotion codes (percent, fixed
amount, free shipping), flat-rate shipping with a free threshold, Stripe
Checkout, and order confirmation driven by signed webhooks.

**Accounts** — registration, sign-in, order history with tracking, saved
addresses, password changes.

**Operations console** — a nine-section admin covering revenue and order
metrics, fulfilment with tracking, product and variant editing, live inventory,
customers, promotion codes, customer messages, an email log with retry, and a
full audit trail. Because you chose *"No — HesMartech can manage updates"*, this
is the surface we use on your behalf rather than a CMS we hand you. It is
ready for you to take over whenever you want it.

**Brand identity** — wordmark, monogram, favicon and social card; a monochrome
palette; a type system; and voice guidance. Documented in `docs/BRAND.md`.

**Product imagery** — you have no photography yet, so the store generates its
own: nine garment silhouettes in six tonal treatments, rendered as monochrome
studio stills. It looks like a designed shoot rather than grey boxes, and it is
replaced by dropping in real photographs when you have them.

---

## 3 · The technology we chose

You asked us to suggest one. We chose **Python with SQLite and no third-party
dependencies**.

That is an unusual answer, so here is the reasoning:

- **It runs anywhere** with Python installed — no build step, no package
  manager, no container required. Hosting cost is one small server.
- **Nothing to patch.** A store with no dependency tree has no dependency
  vulnerabilities, and no upgrade treadmill.
- **It is fast enough by a wide margin.** SQLite comfortably handles a US
  apparel store doing hundreds of orders a day.
- **It stays portable.** One module knows the database is SQLite; moving to
  PostgreSQL later is a contained change, not a rewrite.

The trade-off is that it runs on one box. When moglifestyle outgrows that, the
growth path is documented in `docs/ARCHITECTURE.md`.

---

## 4 · How we protected the payment path

You checked both *SSL / HTTPS* and *None / Not sure* on security. Since the
store takes card payments, we treated the stronger answer as the requirement:

- Card details are entered on Stripe's own checkout and never touch your server,
  which keeps your PCI obligations minimal.
- Passwords are stored with PBKDF2-HMAC-SHA256 at 240,000 rounds.
- Sessions are server-side and addressed by a signed cookie; a new session is
  issued on every sign-in.
- Every form is CSRF-protected; login is rate-limited and accounts lock after
  repeated failures.
- HTTPS is enforced with HSTS, and every privileged action is written to an
  audit log.

Inventory deserves its own note. Stock is reserved the moment an order is
created, inside a single database transaction, so **two customers cannot buy the
same last unit**. Abandoned checkouts release their hold automatically after 45
minutes. Your admin stock counts are true.

---

## 5 · Quality

493 automated tests cover the domain logic, the HTTP layer and complete customer
journeys against a real server — including a full guest purchase, CSRF
rejection, and admin access control.

One suite is unusual and worth pointing out: `tests/test_brief.py` reads your
signed PDF directly and asserts the code still satisfies it. If someone later
removes inventory management or introduces a colour to the palette, the build
fails and names the clause it broke. **Your requirements are executable.**

---

## 6 · Assumptions we made

Some questions were left blank. Rather than stop, we chose sensible defaults and
are flagging them here so you can correct any of them:

| Question | Left blank | What we assumed |
| --- | --- | --- |
| Budget range | — | Built to a launch-ready standard, no ongoing licence costs |
| Launch date | — | Ready now; go live when catalogue and payments are set |
| Users at launch | — | Sized for hundreds of orders a day |
| Languages | — | English only, consistent with US-only and no multi-language box |
| Industry | — | Fitness apparel and training accessories |
| Priority ranking | — | Weighted quality and reliability over speed of delivery |

Shipping is flat $8 with free delivery over $150; sales tax is set to zero.
Both are configuration values, not code. Confirm them and we will adjust.

---

## 7 · What is needed from you to launch

1. **Products** — names, descriptions, prices, sizes, stock counts.
2. **Photography** — or approval to launch on the generated imagery.
3. **Stripe account** — live keys, and business details for onboarding.
4. **Email sending domain** — so receipts arrive rather than land in spam.
5. **Legal details** — business address and entity name for the footer and terms.
6. **Decisions** — confirm shipping rates, returns policy, and whether sales tax
   applies anywhere you have nexus.

With those in hand, going live is a day's work: point DNS at the server, set the
environment, restart. The runbook is `docs/DEPLOYMENT.md`.

---

## 8 · What we would do next

Roughly in the order we would recommend, once the store is live and selling:

1. **Analytics against your own success metric** — you said success is visits
   and the sales they generate. A privacy-respecting funnel view (sessions →
   product views → add to bag → paid) belongs in the admin overview.
2. **Abandoned-cart email** — the outbox and order expiry machinery already
   exist; this is a scheduled query on top of them.
3. **Customer reviews** — social proof sells apparel.
4. **Restock notifications** — you sell out of sizes; capture that demand.
5. **Wholesale or bundle pricing**, if that becomes a channel.
6. **International shipping** — deliberately out of scope for a US-only launch,
   and the point at which GDPR obligations begin.

None of these are needed to start selling. The store is complete as specified.
