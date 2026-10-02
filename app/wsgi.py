"""WSGI entry point.

The store normally runs on its own threaded server (`run.py`).  Shared hosts
such as Namecheap run Python sites through Apache + Passenger instead, which
speaks WSGI -- this adapter lets the very same router, middleware and security
headers run there unchanged (`respond` and `header_list` are shared with the
built-in server).

Two details are deliberately stricter than the built-in server:

* the scheme comes from the web server (`wsgi.url_scheme` / `HTTPS`), never
  from a client-supplied `X-Forwarded-Proto`, so the HTTPS redirect cannot be
  talked out of;
* the client address is Apache's `REMOTE_ADDR`, never `X-Forwarded-For`, so
  login rate limits cannot be dodged by inventing addresses.
"""
from __future__ import annotations

import threading
import urllib.parse
from http import HTTPStatus
from http.client import HTTPMessage
from http.cookies import CookieError, SimpleCookie
from typing import Any, Callable, Iterable

from .web import (
    MAX_BODY_BYTES, HttpError, Request, Response, Router, header_list, respond,
)

_SPOOFABLE = {"X-Forwarded-Proto", "X-Forwarded-For", "X-Real-Ip"}


def scheme_of(environ: dict[str, Any]) -> str:
    if str(environ.get("HTTPS", "")).lower() in ("on", "1", "true"):
        return "https"
    return str(environ.get("wsgi.url_scheme") or "http").lower()


def make_request(environ: dict[str, Any]) -> Request:
    """Build the app's Request from a WSGI environ."""
    method = str(environ.get("REQUEST_METHOD", "GET")).upper()
    # PATH_INFO arrives percent-decoded as latin-1 bytes; recover the UTF-8.
    raw_path = str(environ.get("PATH_INFO") or "/")
    path = raw_path.encode("latin-1", "replace").decode("utf-8", "replace") or "/"

    headers = HTTPMessage()
    for key, value in environ.items():
        if not key.startswith("HTTP_"):
            continue
        name = key[5:].replace("_", "-").title()
        if name in _SPOOFABLE:
            continue
        headers[name] = str(value)
    for key, name in (("CONTENT_TYPE", "Content-Type"), ("CONTENT_LENGTH", "Content-Length")):
        if environ.get(key):
            headers[name] = str(environ[key])
    headers["X-Forwarded-Proto"] = scheme_of(environ)

    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > MAX_BODY_BYTES:
        raise HttpError(413, "Request body too large")
    body = environ["wsgi.input"].read(length) if length > 0 else b""

    cookies = SimpleCookie()
    try:
        cookies.load(str(environ.get("HTTP_COOKIE", "")))
    except CookieError:
        cookies = SimpleCookie()

    return Request(
        method=method, path=path,
        query=urllib.parse.parse_qs(str(environ.get("QUERY_STRING", "")),
                                    keep_blank_values=True),
        headers=headers, body=body, cookies=cookies,
        remote_addr=str(environ.get("REMOTE_ADDR", "")),
    )


def create_wsgi_app(router: Router) -> Callable:
    def application(environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        try:
            request = make_request(environ)
        except HttpError as exc:
            response = Response(exc.message, status=exc.status,
                                content_type="text/plain; charset=utf-8")
        else:
            response = respond(router, request)
        phrase = HTTPStatus(response.status).phrase
        start_response(f"{response.status} {phrase}", header_list(response))
        return [b""] if method == "HEAD" else [response.body]
    return application


# -------------------------------------------------- the production instance

_lock = threading.Lock()
_instance: Callable | None = None


def _boot() -> Callable:
    """Migrate, build the app and start housekeeping -- once per process."""
    global _instance
    with _lock:
        if _instance is None:
            from . import db
            from .application import create_app, start_maintenance
            db.migrate()
            db.close()
            app = create_wsgi_app(create_app())
            start_maintenance()
            _instance = app
    return _instance


def application(environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
    """What Passenger (or any WSGI server) calls."""
    return (_instance or _boot())(environ, start_response)
