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

## Part 2 · Automatic deploys from GitHub over SSH

After this, every `git push` to `main` runs the test suite and, if it passes,
puts the new code live. A push that breaks a test never deploys.

### 1. Enable SSH and create a deployment key

cPanel → **SSH Access** (or **Manage Shell Access**) → enable SSH for the
hosting account. Create an ed25519 key pair locally:

```bash
ssh-keygen -t ed25519 -C "github-actions-moglifestyle" -f namecheap_mog_deploy
```

Import `namecheap_mog_deploy.pub` in cPanel → **SSH Access** → **Manage SSH
Keys**, then authorize it. Keep the private key for the GitHub secret only.

The SSH account must be able to access `/home/USER/moglifestyle`:

The deploy script updates only code directories and release files. A cPanel
SSH key has the permissions of the hosting account, including access to
`~/mog-data`; protect the GitHub repository and its deployment secrets.
The workflow uses SSH with an ed25519 key and a pinned host key.

### 2. Give GitHub the details

GitHub → the `mog-lifestyle` repository → **Settings** → **Secrets and
variables** → **Actions** → **New repository secret**, once for each:

| Secret | Value |
|---|---|
| `NAMECHEAP_SSH_HOST` | server hostname from cPanel, e.g. `server123.web-hosting.com` |
| `NAMECHEAP_SSH_USER` | SSH username |
| `NAMECHEAP_SSH_PORT` | `21098` for this hosting account |
| `NAMECHEAP_SSH_KEY` | contents of the private `namecheap_mog_deploy` key |
| `NAMECHEAP_APP_PATH` | `/home/hesron/moglifestyle` |
| `NAMECHEAP_PYTHON` | `/home/hesron/virtualenv/moglifestyle/3.11/bin/python` |
| `NAMECHEAP_SSH_KNOWN_HOSTS` | trusted host key entry for `[premium339.web-hosting.com]:21098` from the Mac's `~/.ssh/known_hosts` |
| `SITE_URL` | `https://moglifestyle.fit` |

Set all eight secrets above. The host-key secret pins the server identity;
use the entry already accepted during the successful manual SSH connection.

### 3. Push

```bash
git push
```

Watch it under the repository's **Actions** tab:

1. **Tests** — the full suite. The upload zip is attached to the run as a
   download.
2. **Deploy to Namecheap** — connects over SSH, mirrors the code with rsync and
   restarts Passenger.
   The WSGI boot path applies idempotent database migrations before serving.
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
| Remote app folder is not found | Set `NAMECHEAP_APP_PATH` to `/home/hesron/moglifestyle`. |
| `Permission denied (publickey)` | Confirm the public key is imported and authorized in cPanel, and that `NAMECHEAP_SSH_USER` is correct. |
| Host key verification failed | Verify the server fingerprint and update `NAMECHEAP_SSH_KNOWN_HOSTS`. |
| Health check fails after a green deploy | The app didn't restart: **Setup Python App → Restart**, then open `/healthz`. |
| Times in reports are off by hours | Set `MOG_TIMEZONE` to the store's timezone, e.g. `America/Boise`. |

`python run.py --migrate`, `--seed`, `--create-admin` and `--backup` all read the
same `mog.env`, so the terminal and the website always use the same database.
