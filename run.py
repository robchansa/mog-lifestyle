#!/usr/bin/env python3
"""MOG Lifestyle — start the store.

    python3 run.py                  # serve on http://127.0.0.1:8000
    python3 run.py --seed           # create the demo catalogue first
    python3 run.py --reset --seed   # wipe and rebuild the database
    python3 run.py --port 9000
    python3 run.py --check          # migrate, seed, self-test, exit
    python3 run.py --demo-history   # add ~13 months of sample sales & traffic
    python3 run.py --demo-history clear
    python3 run.py --migrate        # bring the database schema up to date, exit
    python3 run.py --create-admin you@example.com   # add or reset an admin
    python3 run.py --backup ~/mog-data/backups      # consistent, compressed copy

Settings are read from the environment, and from the private env file
~/mog-data/mog.env when it exists (see docs/NAMECHEAP.md).

No third-party packages are required -- Python 3.9+ and nothing else.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.envfile import load_env_file                        # noqa: E402

load_env_file()            # before anything reads configuration

from app import __version__, db, seed                        # noqa: E402
from app.application import create_app, start_maintenance    # noqa: E402
from app.config import config                                # noqa: E402
from app.web import Server, build_handler                    # noqa: E402

BANNER = r"""
    __  __  ___   ____
   |  \/  |/ _ \ / ___|      MOG LIFESTYLE
   | |\/| | | | | |  _       commerce platform v{version}
   | |  | | |_| | |_| |
   |_|  |_|\___/ \____|
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--seed", action="store_true",
                        help="populate the demo catalogue")
    parser.add_argument("--reset", action="store_true",
                        help="DROP every table before starting")
    parser.add_argument("--check", action="store_true",
                        help="migrate, seed and self-test, then exit")
    parser.add_argument("--no-maintenance", action="store_true",
                        help="disable the background reservation sweeper")
    parser.add_argument("--migrate", action="store_true",
                        help="apply schema migrations, then exit (used by deploys)")
    parser.add_argument("--create-admin", metavar="EMAIL",
                        help="create an administrator, or reset one's password and "
                             "unlock it; the password comes from MOG_ADMIN_PASSWORD "
                             "or a prompt")
    parser.add_argument("--backup", metavar="DIR",
                        help="write a consistent, gzipped copy of the database to DIR "
                             "and keep the newest 14; safe while the site is running")
    parser.add_argument("--demo-history", nargs="?", const="generate",
                        choices=("generate", "clear"),
                        help="write (or clear) sample sales and traffic so the "
                             "Analytics page has something to show; never in production")
    args = parser.parse_args(argv)

    config.host, config.port = args.host, args.port
    if not config.base_url or config.base_url.startswith("http://127.0.0.1"):
        config.base_url = f"http://{args.host}:{args.port}"

    if args.reset:
        confirm = config.is_production
        if confirm:
            print("Refusing to --reset a production database.", file=sys.stderr)
            return 2
        db.reset()
        print("  database reset")

    db.migrate()
    if args.migrate:
        print(f"  database up to date: {config.database}")
        return 0

    if args.create_admin:
        return _create_admin(args.create_admin)

    if args.backup:
        return _backup(args.backup)

    if args.seed or args.reset:
        try:
            seed.run()
        except seed.SeedError as exc:
            print(f"  {exc}", file=sys.stderr)
            return 2

    if args.demo_history:
        from app import demo
        if args.demo_history == "clear":
            removed = demo.clear()
            print("  demo history cleared: " + ", ".join(
                f"{count:,} {name.replace('_', ' ')}" for name, count in removed.items()))
            return 0
        try:
            made = demo.generate()
        except demo.DemoRefused as exc:
            print(f"  {exc}", file=sys.stderr)
            return 2
        print("  demo history: " + ", ".join(
            f"{count:,} {name.replace('_', ' ')}" for name, count in made.items())
            + "  (remove with --demo-history clear)")
        return 0

    app = create_app()

    if args.check:
        return _self_check(app)

    if not args.no_maintenance:
        start_maintenance()

    print(BANNER.format(version=__version__))
    print(f"   listening   {config.base_url}")
    print(f"   admin       {config.base_url}/admin")
    print(f"   payments    {'Stripe (live keys)' if config.payments_live else 'demo mode — no keys configured'}")
    print(f"   email       {'SMTP ' + config.smtp_host if config.email_live else 'console mode'}")
    print(f"   database    {config.database}")
    print("\n   Ctrl-C to stop\n")

    server = Server((args.host, args.port), build_handler(app))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n   stopping…")
    finally:
        server.server_close()
        db.close()
    return 0


def _backup(directory: str, keep: int = 14) -> int:
    """SQLite's online backup: a consistent copy even mid-checkout."""
    import gzip
    import os
    import shutil
    import sqlite3
    import time

    target_dir = Path(directory).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)                 # backups hold customer records
    raw = target_dir / f"mog-{time.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    copy = sqlite3.connect(str(raw))
    try:
        db.connect().backup(copy)
    finally:
        copy.close()
    packed = raw.with_name(raw.name + ".gz")
    with raw.open("rb") as source, gzip.open(packed, "wb") as sink:
        shutil.copyfileobj(source, sink)
    raw.unlink()
    os.chmod(packed, 0o600)
    for old in sorted(target_dir.glob("mog-*.sqlite3.gz"))[:-keep]:
        old.unlink()
    print(f"  backup written: {packed} ({packed.stat().st_size / 1024:.0f} KB)")
    return 0


def _create_admin(email: str) -> int:
    """Create an administrator, or reset an existing account to admin."""
    import getpass
    import os
    from app.security import audit, hash_password, password_problems, valid_email

    email = email.strip()
    if not valid_email(email):
        print("  That isn't a valid email address.", file=sys.stderr)
        return 2
    password = os.environ.get("MOG_ADMIN_PASSWORD", "")
    if not password:
        if not sys.stdin.isatty():
            print("  Set MOG_ADMIN_PASSWORD, or run this in a terminal to be prompted.",
                  file=sys.stderr)
            return 2
        password = getpass.getpass("  New password: ")
        if getpass.getpass("  Repeat it: ") != password:
            print("  The passwords didn't match.", file=sys.stderr)
            return 2
    problems = password_problems(password)
    if password == seed.DEV_ADMIN_PASSWORD:
        problems.append("That is the public development password.")
    if problems:
        print("  " + " ".join(problems), file=sys.stderr)
        return 2

    with db.tx():
        existing = db.one("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            db.update("users", "id = ?", (existing["id"],), role="admin",
                      password_hash=hash_password(password), failed_logins=0,
                      locked_until=None)
            # Any session signed in with the old password ends now.
            db.execute("DELETE FROM sessions WHERE user_id = ?", (existing["id"],))
            action = "updated"
        else:
            db.insert("users", email=email, password_hash=hash_password(password),
                      name="Administrator", role="admin")
            action = "created"
    audit(f"admin.{action}", actor="cli", subject=email)
    print(f"  admin {action}: {email}")
    return 0


def _self_check(app) -> int:
    """Exercise the route table without opening a socket."""
    from app.web import Request
    from http.client import HTTPMessage
    from http.cookies import SimpleCookie

    checks = ["/", "/shop", "/cart", "/contact", "/about", "/healthz",
              "/robots.txt", "/sitemap.xml", "/login", "/register"]
    row = db.one("SELECT slug FROM products WHERE status = 'active' LIMIT 1")
    if row:
        checks.append(f"/product/{row['slug']}")

    failures = 0
    for path in checks:
        request = Request(
            method="GET", path=path, query={}, headers=HTTPMessage(), body=b"",
            cookies=SimpleCookie(), remote_addr="127.0.0.1",
        )
        try:
            response = app.dispatch(request)
            ok = response.status < 400
        except Exception as exc:                               # noqa: BLE001
            ok, response = False, None
            print(f"   FAIL {path}: {exc}")
        if response is not None:
            print(f"   {'ok  ' if ok else 'FAIL'} {response.status} {path} "
                  f"({len(response.body)} bytes)")
        failures += 0 if ok else 1
        db.close()

    print(f"\n   {len(checks) - failures}/{len(checks)} routes healthy")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
