"""Wires middleware, routes and background maintenance into one app."""
from __future__ import annotations

import threading
import time
import urllib.parse

from . import accounts, analytics, cart as cart_module, db, orders
from .config import config
from .security import constant_time_equals
from .web import (
    HttpError, Request, Response, Router, json_response, redirect, serve_static,
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CSRF_EXEMPT = frozenset({"/webhooks/stripe"})


def create_app() -> Router:
    router = Router()

    # ---------------------------------------------------------- middleware

    @router.use
    def https_redirect(request: Request, nxt):
        if config.force_https and request.scheme != "https":
            host = request.headers.get("Host", "")
            if host:
                return redirect(f"https://{host}{request.url_for_self()}", status=301)
        return nxt(request)

    @router.use
    def session_layer(request: Request, nxt):
        cookie = request.cookies.get(accounts.SESSION_COOKIE)
        session = accounts.load_session(cookie.value if cookie else None)
        issued = False
        if session is None:
            session = accounts.create_session(
                None,
                user_agent=request.headers.get("User-Agent", ""),
                ip=request.remote_addr,
            )
            issued = True
        request.session = session
        request.user = accounts.get_user(session["user_id"])

        response = nxt(request)

        if issued:
            response.set_cookie(
                accounts.SESSION_COOKIE,
                accounts.session_cookie_value(session),
                max_age=config.session_days * 86400,
            )
        return response

    @router.use
    def cart_layer(request: Request, nxt):
        cookie = request.cookies.get(accounts.CART_COOKIE)
        from .security import sign, unsign
        existing = unsign(cookie.value) if cookie else None
        user_id = request.user["id"] if request.user else None
        cart_id = cart_module.ensure_cart(existing, user_id)
        request.cart_id = cart_id
        request.cart_count = cart_module.count_for(cart_id)

        response = nxt(request)

        if cart_id != existing:
            response.set_cookie(
                accounts.CART_COOKIE, sign(cart_id), max_age=60 * 86400
            )
        return response

    @router.use
    def analytics_layer(request: Request, nxt):
        response = nxt(request)
        if (request.method == "GET"
                and response.status == 200
                and response.content_type.startswith("text/html")):
            analytics.record(
                request.url_for_self(),
                ip=request.remote_addr,
                user_agent=request.headers.get("User-Agent", ""),
                referrer=request.headers.get("Referer", ""),
            )
        return response

    @router.use
    def csrf_layer(request: Request, nxt):
        if request.method in SAFE_METHODS or request.path in CSRF_EXEMPT:
            return nxt(request)

        # Defence in depth: the token is authoritative, origin is a second lock.
        origin = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            host = request.headers.get("Host", "")
            if parsed.netloc and host and parsed.netloc != host:
                raise HttpError(403, "Cross-origin request rejected.")

        supplied = request.get("csrf_token")
        expected = request.session["csrf_token"] if request.session else ""
        if not supplied or not expected or not constant_time_equals(supplied, expected):
            if request.wants_json:
                return json_response(
                    {"ok": False, "error": "Your session expired. Please reload."},
                    status=403,
                )
            raise HttpError(403, "Your session expired. Please reload the page and try again.")
        return nxt(request)

    # -------------------------------------------------------------- routes
    from . import views_account, views_admin, views_checkout, views_shop

    router.include(views_shop.router)
    router.include(views_account.router)
    router.include(views_checkout.router)
    router.include(views_admin.router)

    @router.get("/static/<path:filename>")
    def static_files(request: Request, filename: str) -> Response:
        return serve_static(request, filename)

    @router.get("/healthz")
    def healthz(request: Request) -> Response:
        return json_response({
            "status": "ok",
            "version": __import__("app").__version__,
            "payments": "live" if config.payments_live else "demo",
            "email": "smtp" if config.email_live else "console",
            "products": db.scalar("SELECT count(*) FROM products", (), 0),
        })

    @router.get("/robots.txt")
    def robots(request: Request) -> Response:
        return Response(
            f"User-agent: *\nDisallow: /admin\nDisallow: /account\n"
            f"Disallow: /checkout\nDisallow: /cart\n"
            f"Sitemap: {config.url('/sitemap.xml')}\n",
            content_type="text/plain; charset=utf-8",
        )

    @router.get("/sitemap.xml")
    def sitemap(request: Request) -> Response:
        from .web import escape
        urls = ["/", "/shop", "/about", "/contact", "/shipping", "/faq",
                "/privacy", "/terms"]
        urls += [f"/product/{r['slug']}" for r in db.query(
            "SELECT slug FROM products WHERE status = 'active'")]
        urls += [f"/shop?collection={r['slug']}" for r in db.query(
            "SELECT slug FROM collections")]
        entries = "".join(
            f"<url><loc>{escape(config.url(u))}</loc></url>" for u in urls
        )
        return Response(
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{entries}</urlset>",
            content_type="application/xml; charset=utf-8",
        )

    return router


# ------------------------------------------------------------ maintenance

def start_maintenance(interval_seconds: int = 300) -> threading.Thread:
    """Release abandoned reservations, retry queued email, prune sessions."""
    def loop() -> None:
        while True:
            time.sleep(interval_seconds)
            try:
                released = orders.expire_stale()
                accounts.purge_expired()
                analytics.prune()
                from . import mailer
                mailer.flush()
                if released:
                    print(f"  maintenance: released {released} stale order(s)")
            except Exception as exc:                          # noqa: BLE001
                print(f"  maintenance error: {exc}")
            finally:
                db.close()

    thread = threading.Thread(target=loop, name="mog-maintenance", daemon=True)
    thread.start()
    return thread
