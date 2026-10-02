# Deployment — moglifestyle.fit

The brief asked HesMartech to set up hosting, maintain the site, and manage
updates. The domain is already owned. This is that runbook.

---

## 0 · Before anything else

Change the seeded staff password. The demo account is
`info@moglifestyle.fit` / `mog-admin-2026` and is public knowledge — it is in
this repository.

```bash
python3 - <<'EOF'
import sys; sys.path.insert(0, '.')
from app import db; from app.security import hash_password
db.migrate()
with db.tx():
    db.update("users", "email = ?", ("info@moglifestyle.fit",),
              password_hash=hash_password("<a long passphrase>"))
print("staff password rotated")
EOF
```

---

## 1 · Requirements

- A Linux box with **Python 3.9 or newer**. Nothing else.
- 1 vCPU / 1 GB RAM is ample for launch.
- TLS in front (Caddy or nginx). The app speaks plain HTTP behind it.

---

## 2 · Environment

Create `/etc/mog/env` with mode `0600`:

```ini
MOG_ENV=production
MOG_HOST=127.0.0.1
MOG_PORT=8000
MOG_BASE_URL=https://moglifestyle.fit
MOG_DB=/var/lib/mog/mog.sqlite3

# Generate with: python3 -c 'import secrets; print(secrets.token_hex(32))'
MOG_SECRET_KEY=<64 hex characters>
MOG_SECURE_COOKIES=1
MOG_FORCE_HTTPS=1

STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

SMTP_HOST=smtp.postmarkapp.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
MAIL_FROM=MOG Lifestyle <info@moglifestyle.fit>

# Social profiles. Instagram is confirmed; set the others once the client has
# them, and the footer will start rendering those icons.
MOG_INSTAGRAM=mog.lifestyle
# MOG_TIKTOK=
# MOG_YOUTUBE=

MOG_SHIPPING_CENTS=800
MOG_FREE_SHIPPING_CENTS=15000
MOG_TAX_BPS=0
```

`MOG_SECRET_KEY` signs session and cart cookies. **Rotating it signs everyone
out.** The app refuses to start in production without it.

`MOG_TAX_BPS` is basis points (e.g. `725` = 7.25%). It is `0` at launch — see
*Open items* below.

---

## 3 · Install

```bash
sudo useradd --system --home /var/lib/mog --shell /usr/sbin/nologin mog
sudo mkdir -p /opt/mog /var/lib/mog
sudo rsync -a --exclude data --exclude .git ./ /opt/mog/
sudo chown -R mog:mog /opt/mog /var/lib/mog

sudo -u mog MOG_DB=/var/lib/mog/mog.sqlite3 \
     python3 /opt/mog/run.py --seed --check
```

`--check` migrates, seeds and exercises every public route without opening a
socket. It exits non-zero if anything is broken — use it as your deploy gate.

---

## 4 · Service

`/etc/systemd/system/mog.service`:

```ini
[Unit]
Description=MOG Lifestyle
After=network.target

[Service]
Type=simple
User=mog
Group=mog
WorkingDirectory=/opt/mog
EnvironmentFile=/etc/mog/env
ExecStart=/usr/bin/python3 /opt/mog/run.py
Restart=always
RestartSec=3

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/mog
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true
MemoryDenyWriteExecute=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now mog
sudo systemctl status mog
```

---

## 5 · TLS and reverse proxy

Caddy, which handles certificates automatically:

```caddyfile
moglifestyle.fit, www.moglifestyle.fit {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000 {
        header_up X-Forwarded-Proto {scheme}
        header_up X-Forwarded-For {remote_host}
    }
}
```

The app sets HSTS, CSP, `X-Frame-Options`, `X-Content-Type-Options`,
`Referrer-Policy` and `Permissions-Policy` itself — do not duplicate them.

**DNS.** `A` records for `@` and `www` pointing at the server. The client
already owns `moglifestyle.fit`.

---

## 6 · Stripe

1. Dashboard → Developers → API keys → copy the **live** secret key.
2. Developers → Webhooks → *Add endpoint*:
   - URL `https://moglifestyle.fit/webhooks/stripe`
   - Events: `checkout.session.completed`, `checkout.session.expired`,
     `checkout.session.async_payment_failed`, `charge.refunded`
3. Copy the signing secret into `STRIPE_WEBHOOK_SECRET` and restart.
4. Send a test event from the dashboard and confirm a `200` in the logs.

Until `STRIPE_SECRET_KEY` is set the store stays in **demo mode** — orders
complete without a charge. The admin overview says so on every page load, and
`/healthz` reports `"payments": "demo"`.

---

## 7 · Email

Any SMTP provider works. Then, so mail actually arrives:

- **SPF** — `v=spf1 include:<provider> ~all`
- **DKIM** — the provider's CNAME or TXT records
- **DMARC** — `v=DMARC1; p=quarantine; rua=mailto:info@moglifestyle.fit`

Verify by registering a test account and watching **Admin → Email log**. Failed
messages are kept with their error and can be retried from that page.

---

## 8 · Backups

The database is one file. Use SQLite's online backup so you never copy a
half-written page:

```bash
#!/usr/bin/env bash
# /usr/local/bin/mog-backup — run daily via cron
set -euo pipefail
stamp=$(date +%Y%m%d-%H%M)
dest=/var/backups/mog
mkdir -p "$dest"
sqlite3 /var/lib/mog/mog.sqlite3 ".backup '$dest/mog-$stamp.sqlite3'"
gzip -f "$dest/mog-$stamp.sqlite3"
find "$dest" -name 'mog-*.sqlite3.gz' -mtime +30 -delete
```

Copy off-box — the brief's "Regular backups" item is not satisfied by a second
copy on the same disk. Test a restore quarterly: decompress, point `MOG_DB` at
it, run `python3 run.py --check`.

---

## 9 · Monitoring

`GET /healthz` returns:

```json
{"status":"ok","version":"1.0.0","payments":"live","email":"smtp","products":14}
```

Point an uptime monitor at it and alert when it stops returning `200` — or when
`payments` reads `demo` in production, which means the Stripe key is missing.

Watch `journalctl -u mog -f` after each deploy.

---

## 9b · Pre-launch audit

```bash
python3 tools/audit.py https://moglifestyle.fit
```

Exits non-zero on any failure. Run it after the first deploy and as part of
every later one.

**On cookie consent.** The store sets two cookies — a signed session and a
signed cart — both strictly necessary to buy anything, which are exempt from
consent under GDPR and ePrivacy. Analytics is first-party and cookieless. So no
consent banner is required today, and `tests/test_launch.py` fails the build if
a non-essential cookie or a third-party tracker is ever added without one.

If marketing later wants Google Analytics, Meta Pixel or similar, a banner
becomes mandatory and must block those scripts until consent is given.

## 10 · Updating

```bash
sudo -u mog python3 /opt/mog/run.py --check   # gate: migrations + route health
sudo systemctl restart mog
```

Schema changes are additive and idempotent (`CREATE TABLE IF NOT EXISTS`,
`CREATE INDEX IF NOT EXISTS`), so a restart applies them. Take a backup first
anyway.

---

## Open items for the client

These came out of the brief as unanswered or as decisions that need the client's
input before launch:

1. **Budget and launch date** were left blank on the form. They drive
   prioritisation of everything below.
2. **Sales tax.** `MOG_TAX_BPS` is `0`. US sales tax is destination-based and
   varies by state; if moglifestyle has nexus anywhere, this needs either a rate
   table or Stripe Tax enabled.
3. **Shipping rates.** Flat $8, free over $150, is an assumption. Confirm against
   real carrier pricing.
4. **Returns.** The policy pages promise thirty days and prepaid US labels.
   Confirm, and decide who pays return postage.
5. **Product data and photography.** The catalogue is a plausible demo line and
   the imagery is generated. Real products, prices, stock counts and photographs
   are needed before launch.
6. **The security answers conflict.** The form checked both *SSL / HTTPS* and
   *None / Not sure*. Because the store takes card payments, we have implemented
   HTTPS, PCI scope reduction via Stripe Checkout, session hardening and audit
   logging regardless. GDPR/POPIA was left unchecked, which is consistent with a
   US-only launch — revisit before selling into the EU.
7. **Business address and legal entity** for the footer, terms and Stripe
   onboarding.
