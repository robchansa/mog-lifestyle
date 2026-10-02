#!/usr/bin/env python3
"""MOG Lifestyle — start the store.

    python3 run.py                  # serve on http://127.0.0.1:8000
    python3 run.py --seed           # create the demo catalogue first
    python3 run.py --reset --seed   # wipe and rebuild the database
    python3 run.py --port 9000
    python3 run.py --check          # migrate, seed, self-test, exit

No third-party packages are required -- Python 3.9+ and nothing else.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
    if args.seed or args.reset:
        seed.run()

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
