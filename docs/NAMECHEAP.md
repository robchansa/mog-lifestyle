# Namecheap hosting — first upload, and automatic deploys from GitHub

This is the runbook for hosting moglifestyle.fit on **Namecheap shared hosting
(cPanel)**. For a VPS, use [DEPLOYMENT.md](DEPLOYMENT.md) instead.

How it fits together:

```
~/moglifestyle/        the app folder — replaced by every deploy
    passenger_wsgi.py  cPanel's Python app runs this
    app/  run.py  …
~/mog-data/            yours alone — no deploy ever touches it
    mog.env            settings and secrets (chmod 600)
    mog.sqlite3        the database: orders, customers, stock
    backups/           nightly copies
```

Nothing secret is in the code or in git. Replace `USER` below with your cPanel
username.

---

## Part 1 · First upload (about 20 minutes)

### 1. Private settings

1. cPanel → **File Manager** → your home folder → **+ Folder** → `mog-data`.
2. Inside it, **+ File** → `mog.env`. Paste the contents of
   [`mog.env.example`](mog.env.example) and fill it in. At minimum:
   - `MOG_SECRET_KEY` — 64 random hex characters. Any computer with Python:
     `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - `MOG_DB=/home/USER/mog-data/mog.sqlite3`
3. Right-click `mog.env` → **Change Permissions** → `600` (owner read/write
   only).

### 2. Create the Python app

cPanel → **Setup Python App** → **Create Application**:

| Field | Value |
|---|---|
| Python version | the newest offered (3.11 or later; **3.9 is the minimum**) |
| Application root | `moglifestyle` |
| Application URL | `moglifestyle.fit` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Click **Create**. At the top of the page cPanel now shows a command like
`source /home/USER/virtualenv/moglifestyle/3.11/bin/activate && cd /home/USER/moglifestyle`
— keep it, you need it below. There is nothing to `pip install`: the store
uses only Python's standard library.

### 3. Upload the code

1. **File Manager** → open `moglifestyle`.
2. **Upload** the zip (`mog-lifestyle-<version>-<commit>.zip`, built with
   `python3 tools/package.py`, or downloaded from the latest GitHub Actions
   run).
3. Select it → **Extract** into `/home/USER/moglifestyle` → allow it to
   overwrite cPanel's placeholder `passenger_wsgi.py`. Delete the zip.

### 4. Set up the shop

cPanel → **Terminal**, paste the command from step 2, then:

```bash
python run.py --seed                                   # catalogue + promo codes
python run.py --create-admin info@moglifestyle.fit     # you'll be asked for a password
```

Production refuses the development password from the README, and the seed
never creates an admin with it.

### 5. HTTPS, restart, check

1. cPanel → **SSL/TLS Status** → make sure `moglifestyle.fit` and
   `www.moglifestyle.fit` have a certificate (run **AutoSSL** if not).
2. **Setup Python App** → **Restart**.
3. Open `https://moglifestyle.fit/healthz` — you should see `"status": "ok"`.
4. Sign in at `https://moglifestyle.fit/admin`.

Then, in the Stripe dashboard, add the webhook endpoint
`https://moglifestyle.fit/webhooks/stripe` with the events
`checkout.session.completed`, `checkout.session.expired`,
`checkout.session.async_payment_failed` and `charge.refunded`, put its signing
secret in `STRIPE_WEBHOOK_SECRET`, and restart.

### 6. Nightly backups

cPanel → **Cron Jobs** → once per day:

```
/home/USER/virtualenv/moglifestyle/3.11/bin/python /home/USER/moglifestyle/run.py --backup /home/USER/mog-data/backups
```

It uses SQLite's online backup, so it is safe while customers are checking
out, and keeps the newest 14. Download one now and then — a backup on the same
server is not a backup.

---

## Part 2 · Automatic deploys from GitHub

After this, every `git push` to `main` runs the test suite and, if it passes,
puts the new code live. A push that breaks a test never deploys.

### 1. Turn on SSH

cPanel → **SSH Access** (on some plans **Manage Shell**) → make sure SSH is
enabled. Namecheap shared hosting uses **port 21098**. Your server's hostname
is in cPanel's **General Information** panel (it looks like
`server123.web-hosting.com`).

### 2. Make a deploy key

On your Mac:

```bash
ssh-keygen -t ed25519 -N "" -C "github-deploy" -f ~/.ssh/mog_deploy
```

cPanel → **SSH Access** → **Manage SSH Keys** → **Import Key** → name it
`github-deploy`, paste the contents of `~/.ssh/mog_deploy.pub` → **Import** →
back in the list click **Manage** → **Authorize**.

Check it works from your Mac:

```bash
ssh -p 21098 -i ~/.ssh/mog_deploy USER@server123.web-hosting.com "ls moglifestyle"
```

### 3. Give GitHub the details

GitHub → the `mog-lifestyle` repository → **Settings** → **Secrets and
variables** → **Actions** → **New repository secret**, once for each:

| Secret | Value |
|---|---|
| `NAMECHEAP_SSH_HOST` | `server123.web-hosting.com` (yours) |
| `NAMECHEAP_SSH_PORT` | `21098` |
| `NAMECHEAP_SSH_USER` | your cPanel username |
| `NAMECHEAP_SSH_KEY` | the whole of `~/.ssh/mog_deploy` (the file **without** `.pub`) |
| `NAMECHEAP_APP_PATH` | `/home/USER/moglifestyle` |
| `NAMECHEAP_PYTHON` | `/home/USER/virtualenv/moglifestyle/3.11/bin/python` |
| `SITE_URL` | `https://moglifestyle.fit` |

`pbcopy < ~/.ssh/mog_deploy` copies the private key to the clipboard.

### 4. Push

```bash
git push
```

Watch it under the repository's **Actions** tab:

1. **Tests** — the full suite. The upload zip is attached to the run as a
   download.
2. **Deploy to Namecheap** — checks the target really is the app folder,
   uploads the code, migrates the database, restarts the app.
3. **Health check** — waits until `https://moglifestyle.fit/healthz` reports the
   commit you just pushed. Green means it is live.

### What a deploy never touches

`~/mog-data` (settings, database, backups), the app's `tmp/` and log files,
and anything else outside the code folders. Code folders are mirrored exactly,
so a file deleted in git is deleted on the server.

### Rolling back

Revert the bad commit and push (`git revert <sha> && git push`), or open an
earlier successful run under **Actions** and choose **Re-run all jobs**.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Incomplete response received from application" | Read `~/moglifestyle/stderr.log`. Almost always `MOG_SECRET_KEY must be set in production` — `mog.env` is missing, misnamed, or not in `~/mog-data`. |
| Site works but every page says demo mode | `STRIPE_SECRET_KEY` is empty in `mog.env`. Restart after editing. |
| Changes to `mog.env` don't apply | Settings are read at start-up: **Setup Python App → Restart**. |
| Deploy job skipped with a notice | One of the secrets above is missing or misspelled. |
| "has no passenger_wsgi.py" | `NAMECHEAP_APP_PATH` is wrong, or the Python app (Part 1, step 2) doesn't exist yet. |
| `Permission denied (publickey)` | The key wasn't **Authorized** in cPanel, or the private key secret is incomplete — it must include the `BEGIN` and `END` lines. |
| `rsync: command not found` | Rare on Namecheap. Ask support to enable it, or keep uploading the zip by hand (Part 1, step 3). |
| Health check fails after a green deploy | The app didn't restart: **Setup Python App → Restart**, then open `/healthz`. |
| Times in reports are off by hours | Set `MOG_TIMEZONE` to the store's timezone, e.g. `America/Boise`. |

`python run.py --migrate`, `--seed`, `--create-admin` and `--backup` all read the
same `mog.env`, so the terminal and the website always use the same database.
