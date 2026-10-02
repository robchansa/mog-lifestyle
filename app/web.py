"""HTTP plumbing: request parsing, responses, routing, and the server.

Built on `http.server.ThreadingHTTPServer`.  The routing table supports typed
path parameters (`/product/<slug>`, `/admin/orders/<int:order_id>`), and every
response carries a hardened set of security headers.
"""
from __future__ import annotations

import html
import json
import mimetypes
import re
import socketserver
import sys
import time
import traceback
import urllib.parse
from dataclasses import dataclass, field
from email.utils import formatdate
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Iterable

from . import db
from .config import STATIC_DIR, config

MAX_BODY_BYTES = 1 * 1024 * 1024        # 1 MiB is plenty for form posts
CONVERTERS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "str": (r"[^/]+", str),
    "int": (r"\d+", int),
    "slug": (r"[a-z0-9][a-z0-9\-]*", str),
    "path": (r".+", str),
}


# ------------------------------------------------------------------ request

@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any
    body: bytes
    cookies: SimpleCookie
    remote_addr: str
    params: dict[str, Any] = field(default_factory=dict)
    # populated by middleware
    session: Any = None
    user: Any = None
    cart_id: str | None = None
    _form: dict[str, list[str]] | None = None

    # -- accessors ------------------------------------------------------

    @property
    def form(self) -> dict[str, list[str]]:
        if self._form is None:
            ctype = self.headers.get("Content-Type", "")
            if ctype.startswith("application/x-www-form-urlencoded"):
                self._form = urllib.parse.parse_qs(
                    self.body.decode("utf-8", "replace"), keep_blank_values=True
                )
            else:
                self._form = {}
        return self._form

    def get(self, name: str, default: str = "") -> str:
        values = self.form.get(name) or self.query.get(name)
        return values[0].strip() if values else default

    def get_raw(self, name: str, default: str = "") -> str:
        values = self.form.get(name) or self.query.get(name)
        return values[0] if values else default

    def get_int(self, name: str, default: int = 0) -> int:
        try:
            return int(self.get(name, str(default)))
        except (TypeError, ValueError):
            return default

    def get_list(self, name: str) -> list[str]:
        values = self.form.get(name) or self.query.get(name) or []
        return [v.strip() for v in values if v.strip()]

    def checked(self, name: str) -> bool:
        return bool(self.form.get(name) or self.query.get(name))

    def json_body(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    @property
    def is_json(self) -> bool:
        return "application/json" in self.headers.get("Content-Type", "")

    @property
    def wants_json(self) -> bool:
        return self.is_json or "application/json" in self.headers.get("Accept", "")

    @property
    def scheme(self) -> str:
        """The scheme the *client* used.

        Behind a TLS proxy this comes from `X-Forwarded-Proto`; the proxy must
        set it (the deployment runbook does). With no header the connection
        really is plain HTTP -- assuming otherwise would silently disable the
        HTTPS redirect, which is exactly the bug this replaced.
        """
        forwarded = self.headers.get("X-Forwarded-Proto", "")
        return forwarded.split(",")[0].strip().lower() or "http"

    def url_for_self(self) -> str:
        qs = urllib.parse.urlencode(self.query, doseq=True)
        return f"{self.path}?{qs}" if qs else self.path


# ----------------------------------------------------------------- response

@dataclass
class Response:
    body: bytes | str = b""
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    cookies: list[str] = field(default_factory=list)
    content_type: str = "text/html; charset=utf-8"

    def __post_init__(self) -> None:
        if isinstance(self.body, str):
            self.body = self.body.encode("utf-8")

    def set_cookie(self, name: str, value: str, *, max_age: int | None = None,
                   http_only: bool = True, same_site: str = "Lax",
                   path: str = "/") -> "Response":
        parts = [f"{name}={urllib.parse.quote(value)}", f"Path={path}", f"SameSite={same_site}"]
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
            expires = formatdate(time.time() + max_age, usegmt=True)
            parts.append(f"Expires={expires}")
        if http_only:
            parts.append("HttpOnly")
        if config.secure_cookies:
            parts.append("Secure")
        self.cookies.append("; ".join(parts))
        return self

    def delete_cookie(self, name: str, *, path: str = "/") -> "Response":
        self.cookies.append(f"{name}=; Path={path}; Max-Age=0; HttpOnly; SameSite=Lax")
        return self


def html_response(markup: str, status: int = 200, **headers: str) -> Response:
    return Response(markup, status=status, headers=dict(headers))


def json_response(payload: Any, status: int = 200, **headers: str) -> Response:
    return Response(
        json.dumps(payload, default=str),
        status=status,
        content_type="application/json; charset=utf-8",
        headers=dict(headers),
    )


def redirect(location: str, status: int = 303, flash: str | None = None,
             tone: str = "ok") -> Response:
    response = Response(b"", status=status, headers={"Location": location})
    if flash:
        response.set_cookie(
            "flash", f"{tone}:{flash}", max_age=30, http_only=False, same_site="Lax"
        )
    return response


def text_response(text: str, status: int = 200) -> Response:
    return Response(text, status=status, content_type="text/plain; charset=utf-8")


class HttpError(Exception):
    def __init__(self, status: int, message: str = "", *, headers: dict | None = None):
        super().__init__(message or HTTPStatus(status).phrase)
        self.status = status
        self.message = message or HTTPStatus(status).phrase
        self.headers = headers or {}


# ------------------------------------------------------------------ routing

@dataclass
class Route:
    methods: frozenset[str]
    pattern: re.Pattern
    handler: Callable[..., Response]
    converters: dict[str, Callable[[str], Any]]
    name: str


class Router:
    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.middleware: list[Callable] = []

    def add(self, rule: str, handler: Callable, methods: Iterable[str] = ("GET",),
            name: str = "") -> None:
        regex, converters = _compile_rule(rule)
        upper = {m.upper() for m in methods}
        if "GET" in upper:
            upper.add("HEAD")
        self.routes.append(
            Route(frozenset(upper), regex, handler, converters, name or handler.__name__)
        )

    def route(self, rule: str, methods: Iterable[str] = ("GET",), name: str = ""):
        def decorator(fn: Callable) -> Callable:
            self.add(rule, fn, methods, name)
            return fn
        return decorator

    def get(self, rule: str, **kw):
        return self.route(rule, ("GET",), **kw)

    def post(self, rule: str, **kw):
        return self.route(rule, ("POST",), **kw)

    def use(self, fn: Callable) -> Callable:
        """Register middleware: `fn(request, next_handler) -> Response`."""
        self.middleware.append(fn)
        return fn

    def include(self, other: "Router") -> None:
        self.routes.extend(other.routes)

    # -- dispatch -------------------------------------------------------

    def match(self, method: str, path: str) -> tuple[Route, dict] | None:
        allowed: set[str] = set()
        for route in self.routes:
            m = route.pattern.match(path)
            if not m:
                continue
            if method not in route.methods:
                allowed |= route.methods
                continue
            params = {
                key: route.converters[key](value)
                for key, value in m.groupdict().items()
            }
            return route, params
        if allowed:
            raise HttpError(405, "Method not allowed",
                            headers={"Allow": ", ".join(sorted(allowed))})
        return None

    def dispatch(self, request: Request) -> Response:
        def terminal(req: Request) -> Response:
            found = self.match(req.method, req.path)
            if found is None:
                raise HttpError(404, "Not found")
            route, params = found
            req.params = params
            return route.handler(req, **params)

        handler = terminal
        for middleware in reversed(self.middleware):
            handler = _wrap(middleware, handler)
        return handler(request)


def _wrap(middleware: Callable, nxt: Callable) -> Callable:
    def wrapped(request: Request) -> Response:
        return middleware(request, nxt)
    return wrapped


def _compile_rule(rule: str) -> tuple[re.Pattern, dict[str, Callable]]:
    converters: dict[str, Callable] = {}
    pattern = ["^"]
    index = 0
    for match in re.finditer(r"<(?:(\w+):)?(\w+)>", rule):
        pattern.append(re.escape(rule[index:match.start()]))
        kind = match.group(1) or "str"
        name = match.group(2)
        if kind not in CONVERTERS:
            raise ValueError(f"unknown path converter {kind!r} in rule {rule!r}")
        regex, caster = CONVERTERS[kind]
        pattern.append(f"(?P<{name}>{regex})")
        converters[name] = caster
        index = match.end()
    pattern.append(re.escape(rule[index:]))
    pattern.append("$")
    return re.compile("".join(pattern)), converters


# ------------------------------------------------------------ static files

_STATIC_CACHE: dict[str, tuple[bytes, str, str]] = {}


def serve_static(request: Request, filename: str) -> Response:
    safe = Path(filename)
    if safe.is_absolute() or ".." in safe.parts:
        raise HttpError(404, "Not found")
    target = (STATIC_DIR / safe).resolve()
    try:
        target.relative_to(STATIC_DIR.resolve())
    except ValueError:
        raise HttpError(404, "Not found") from None
    if not target.is_file():
        raise HttpError(404, "Not found")

    key = str(target)
    cached = _STATIC_CACHE.get(key)
    stamp = f"{target.stat().st_mtime_ns:x}"
    if cached is None or cached[2] != stamp or not config.is_production:
        payload = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in {"application/javascript", "image/svg+xml"}:
            ctype = f"{ctype}; charset=utf-8"
        cached = (payload, ctype, stamp)
        _STATIC_CACHE[key] = cached

    payload, ctype, stamp = cached
    etag = f'W/"{stamp}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(b"", status=304, headers={"ETag": etag})
    cache = "public, max-age=31536000, immutable" if config.is_production else "no-cache"
    return Response(payload, content_type=ctype,
                    headers={"ETag": etag, "Cache-Control": cache})


# ----------------------------------------------------------------- handler

def build_handler(router: Router):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MOG"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # -- lifecycle --------------------------------------------------

        def do_GET(self) -> None:      self._handle("GET")
        def do_HEAD(self) -> None:     self._handle("HEAD")
        def do_POST(self) -> None:     self._handle("POST")
        def do_PUT(self) -> None:      self._handle("PUT")
        def do_DELETE(self) -> None:   self._handle("DELETE")
        def do_PATCH(self) -> None:    self._handle("PATCH")

        def log_message(self, fmt: str, *args: Any) -> None:
            if config.is_production:
                return
            sys.stderr.write(
                f"  {self.address_string()} {fmt % args}\n"
            )

        # -- plumbing ---------------------------------------------------

        def _handle(self, method: str) -> None:
            started = time.perf_counter()
            try:
                request = self._read_request(method)
            except HttpError as exc:
                self._emit(Response(exc.message, status=exc.status,
                                    content_type="text/plain; charset=utf-8"), "HEAD")
                return

            try:
                response = router.dispatch(request)
            except HttpError as exc:
                response = _error_response(request, exc.status, exc.message)
                response.headers.update(exc.headers)
            except BrokenPipeError:
                return
            except Exception:
                traceback.print_exc()
                detail = "" if config.is_production else traceback.format_exc()
                response = _error_response(request, 500, "Something went wrong.", detail)
            finally:
                db.close()

            elapsed = (time.perf_counter() - started) * 1000
            response.headers.setdefault("X-Response-Time", f"{elapsed:.1f}ms")
            self._emit(response, method)

        def _read_request(self, method: str) -> Request:
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise HttpError(413, "Request body too large")
            body = self.rfile.read(length) if length else b""
            cookies = SimpleCookie()
            cookies.load(self.headers.get("Cookie", ""))
            forwarded = self.headers.get("X-Forwarded-For", "")
            remote = forwarded.split(",")[0].strip() or self.client_address[0]
            return Request(
                method=method,
                path=urllib.parse.unquote(parsed.path) or "/",
                query=urllib.parse.parse_qs(parsed.query, keep_blank_values=True),
                headers=self.headers,
                body=body,
                cookies=cookies,
                remote_addr=remote,
            )

        def _emit(self, response: Response, method: str) -> None:
            body = b"" if method == "HEAD" else response.body
            try:
                self.send_response(response.status)
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(response.body)))
                for name, value in _security_headers().items():
                    self.send_header(name, value)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                for cookie in response.cookies:
                    self.send_header("Set-Cookie", cookie)
                self.end_headers()
                if body:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def _security_headers() -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(self)",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; "
            "form-action 'self' https://checkout.stripe.com; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "object-src 'none'"
        ),
    }
    if config.force_https:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def _error_response(request: Request, status: int, message: str,
                    detail: str = "") -> Response:
    if request.wants_json:
        return json_response({"error": message, "status": status}, status=status)
    from .ui import error_page       # local import: ui imports web helpers
    return html_response(error_page(request, status, message, detail), status=status)


# ------------------------------------------------------------------ server

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64


def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)
