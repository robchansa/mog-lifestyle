# Pre-launch — countdown page, Notify Me, and the hidden site

Until preorders open, **the public sees one page**: the MOG logo over the
campaign photograph, **"Preorders Open In"** with days, hours, minutes and
seconds, and a **Notify Me** email sign-up. Every other address on the site
(shop, products, cart, account, contact…) sends visitors back to it.

When the clock reaches zero the page changes to **"Preorders Are Open"** and,
a moment later, takes the visitor into the full website. Nobody has to flip a
switch: from that second on, the server serves the normal site to everyone.

**Staff and admins** who are signed in browse the whole site as normal
throughout, with a slim bar at the top reminding them the public still sees
the countdown (and a link to view the countdown page). Sign in at `/login`.

| Piece | File |
| --- | --- |
| The setting | `app/config.py` — `PREORDER_OPENS_AT`, `preorder_opens_at`, `preorder_url`, `preorder_forms` |
| Clock and validation rules | `app/preorder.py` |
| The gate, the countdown page, Notify Me | `app/views_preorder.py` (gate registered in `app/application.py`) |
| Live countdown, reveal, in-place feedback | `app/static/js/preorder.js` |
| Styles | `app/static/css/site.css`, section "pre-launch page" |
| Tests | `tests/test_preorder.py` |

The homepage itself is unchanged; it is simply what people see once the
site opens.

---

## 1 · The opening date — one setting

Date, time and time zone live in **one value**:

```
MOG_PREORDER_OPENS_AT=2026-11-20 10:00 America/Boise
```

Format `YYYY-MM-DD HH:MM Area/City` — 24-hour clock, IANA time-zone name
(`America/Boise` is Mountain Time, `America/New_York` Eastern,
`America/Los_Angeles` Pacific, `UTC`). Daylight saving is handled.

**Confirmed launch: Friday 20 November 2026, 10:00 AM Mountain Time**
(17:00 UTC). It is the default in `app/config.py` (`PREORDER_OPENS_AT`), so
it applies even if nothing is set on the server.

To move it, add `MOG_PREORDER_OPENS_AT=...` to the server's
`~/mog-data/mog.env` (preferred — no code change) or change
`PREORDER_OPENS_AT` in `app/config.py`. To open the site early, set a time in
the past.

> **Important:** the moment this code is deployed, the live site is hidden
> until 20 November, 10:00 AM MT, and only staff who sign in can see the store.

If the value cannot be read, the site **stays hidden** and the page says
"Preorders Open Soon — date to be announced" (staff see the site and a
"Setting error" note on the countdown page), so a typo can never reveal the
store early.

The countdown runs against the **server's** clock (a visitor's phone set fast
cannot open it early) and shows visitors in another zone their local time.

### Confirming it on the live server

Nothing has to happen on launch day — no push, no restart. Every request
compares the server's clock with the opening time, so the site opens on its
own at 10:00 AM MT and stays open, whenever you next deploy.

Two safeguards make sure the live server reads that time correctly:

- **Built-in time-zone rules.** Normally the server's time-zone database turns
  `America/Boise` into "UTC−7 in November". If a server had no such database,
  the common US zones (Mountain, Pacific, Central, Eastern, Arizona, UTC) are
  also built in, with the US daylight-saving rule — so the site still opens at
  the right moment. A test checks those rules against the real database for
  every day of 2026–2028.
- **`/healthz` reports the launch.** Open `https://moglifestyle.fit/healthz`
  after deploying and look at `"launch"`:

  ```json
  "launch": {
    "readable": true,
    "opens_local": "Friday, November 20, 2026 at 10:00 AM MST",
    "opens_at_utc": "2026-11-20T17:00:00Z",
    "time_zone": "America/Boise via time-zone database",
    "site_open": false,
    "seconds_left": 1246372
  }
  ```

  The GitHub deploy does this check itself: its "Health check" step prints
  `Launch: site hidden until Friday, November 20, 2026 at 10:00 AM MST`, and
  **fails the deploy** with an error if the live server cannot read the
  opening time.

## 2 · What stays reachable while the site is hidden

The countdown page and its Notify Me form; the **privacy policy** (the
sign-up's consent notice links to it); **staff sign-in** (`/login`) and the
**admin console**; **Stripe's webhook**; **unsubscribe links**; and the
site's images, styles and scripts. Everything else redirects to the
countdown page. The page is sent with `Cache-Control: no-store`, so no browser
or proxy keeps showing it after launch.

## 3 · Where the reveal lands

At zero the page goes to the homepage, or — if a preorder page is configured —
there instead, with a **"Shop the preorder"** button:

```
MOG_PREORDER_URL=/shop?collection=preorder      # a page on this site, or
MOG_PREORDER_URL=https://moglifestyle.fit/...   # an https:// address
```

Anything that is not a site path or an `https://` URL is ignored.

## 4 · Notify Me: demo locally, live in production

`MOG_PREORDER_FORMS` = `demo` or `live`. Default: **live** when
`MOG_ENV=production`, **demo** everywhere else.

- **Demo** (the local preview): validated exactly like the real form with the
  same feedback, but **nothing is saved and nothing is sent**. The page looks
  exactly as it will live; only the confirmation message adds a "Preview
  only — nothing was saved" line.
- **Live**: `accounts.subscribe_newsletter(email, source="preorder")` — the
  same function and `newsletter` table as the footer "Join" form, with
  consent time, IP and browser recorded. Rate-limited, CSRF-protected and
  spam-trapped.

**Getting the list out:** Admin → Customers → **Mailing list** shows active
subscribers by where they signed up, with a CSV export for each source
(`/admin/subscribers/export?source=preorder`) or for everyone. Staff and
admins only; unsubscribed addresses are never included; spreadsheet formulas
are defused; every export is recorded in Admin → Activity.

**To move the list to an email service later** (Mailchimp, Klaviyo, etc.),
change only `accounts.subscribe_newsletter()` — the footer and Notify Me both
go through it.

Account creation is not on the countdown page; once the site opens, customers
use the existing `/register`.

## 5 · Reviewing locally

```bash
python3 run.py
```

- <http://127.0.0.1:8000/> — the countdown page, as the public sees it.
- <http://127.0.0.1:8000/?preview_countdown=15> — the countdown ends 15
  seconds after loading, switches to "Preorders Are Open", then opens the site
  (for that one page view).
- Sign in at <http://127.0.0.1:8000/login> with the development admin from
  the README to browse the full site with the staff bar.

`preview_countdown` is ignored in production; there the configured date is
the only clock.

## 6 · Before launch

1. The launch time is confirmed (20 November, 10:00 AM MT). Deploying hides
   the live store until then — make sure that is what you want before
   publishing (see §1).
2. After deploying, check the GitHub Actions "Health check" step (or
   `/healthz`) shows the right opening time (see §1).
3. If there will be a dedicated preorder page, build it and set
   `MOG_PREORDER_URL`.
4. Configure `SMTP_HOST` etc. (`docs/DEPLOYMENT.md`) so store email is
   actually delivered — without it, mail is only logged.
5. On launch day, export the Notify Me list from Admin → Customers → Mailing
   list and send the "preorders are open" email from your email service. The
   site collects the addresses; it does not send that announcement itself.
6. Have the Notify Me consent wording (`ui.collection_notice("preorder")`) and
   the privacy policy reviewed together.
