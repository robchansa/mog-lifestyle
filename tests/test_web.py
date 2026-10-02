"""Routing, request parsing, responses and static-file safety."""
from __future__ import annotations

import unittest

from app import web
from app.web import HttpError, Request, Response, Router
from tests.support import StoreTestCase


class RouteCompilationTests(unittest.TestCase):
    def test_typed_parameters(self):
        pattern, converters = web._compile_rule("/admin/orders/<int:order_id>")
        match = pattern.match("/admin/orders/42")
        self.assertIsNotNone(match)
        self.assertEqual(converters["order_id"](match.group("order_id")), 42)
        self.assertIsNone(pattern.match("/admin/orders/abc"))

    def test_slug_converter_rejects_spaces_and_capitals(self):
        pattern, _ = web._compile_rule("/product/<slug:slug>")
        self.assertIsNotNone(pattern.match("/product/atlas-hoodie"))
        self.assertIsNone(pattern.match("/product/Atlas Hoodie"))
        self.assertIsNone(pattern.match("/product/a/b"))

    def test_str_converter_does_not_cross_a_slash(self):
        pattern, _ = web._compile_rule("/media/<str:name>.svg")
        self.assertIsNotNone(pattern.match("/media/tee-0.svg"))
        self.assertIsNone(pattern.match("/media/a/b.svg"))

    def test_path_converter_crosses_slashes(self):
        pattern, _ = web._compile_rule("/static/<path:filename>")
        self.assertIsNotNone(pattern.match("/static/css/site.css"))

    def test_unknown_converter_raises(self):
        with self.assertRaises(ValueError):
            web._compile_rule("/<uuid:id>")

    def test_literal_segments_are_escaped(self):
        pattern, _ = web._compile_rule("/a.b")
        self.assertIsNotNone(pattern.match("/a.b"))
        self.assertIsNone(pattern.match("/axb"))


class RouterTests(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.router = Router()

        @self.router.get("/hello/<str:name>")
        def hello(request, name):
            return Response(f"hi {name}")

        @self.router.post("/submit")
        def submit(request):
            return Response("posted")

    def test_dispatch_passes_path_parameters(self):
        response = self.router.dispatch(self.make_request(path="/hello/world"))
        self.assertEqual(response.body, b"hi world")

    def test_get_also_allows_head(self):
        response = self.router.dispatch(self.make_request("HEAD", "/hello/x"))
        self.assertEqual(response.status, 200)

    def test_unknown_path_is_404(self):
        with self.assertRaises(HttpError) as caught:
            self.router.dispatch(self.make_request(path="/nope"))
        self.assertEqual(caught.exception.status, 404)

    def test_wrong_method_is_405_with_allow(self):
        with self.assertRaises(HttpError) as caught:
            self.router.dispatch(self.make_request("POST", "/hello/x"))
        self.assertEqual(caught.exception.status, 405)
        self.assertIn("GET", caught.exception.headers["Allow"])

    def test_middleware_wraps_in_order(self):
        calls = []

        @self.router.use
        def outer(request, nxt):
            calls.append("outer-in")
            response = nxt(request)
            calls.append("outer-out")
            return response

        @self.router.use
        def inner(request, nxt):
            calls.append("inner")
            return nxt(request)

        self.router.dispatch(self.make_request(path="/hello/x"))
        self.assertEqual(calls, ["outer-in", "inner", "outer-out"])


class RequestTests(StoreTestCase):
    def test_form_parsing_and_accessors(self):
        request = self.make_request("POST", "/", form={
            "name": "  Robert  ", "quantity": "3", "empty": "",
        })
        self.assertEqual(request.get("name"), "Robert")
        self.assertEqual(request.get_raw("name"), "  Robert  ")
        self.assertEqual(request.get_int("quantity"), 3)
        self.assertEqual(request.get_int("missing", 7), 7)
        self.assertEqual(request.get_int("name", 1), 1, "non-numeric falls back")
        self.assertTrue(request.checked("empty"), "a present key counts as checked")
        self.assertFalse(request.checked("absent"))

    def test_query_parsing(self):
        request = self.make_request(query={"size": ["M", "L"], "q": ["tee"]})
        self.assertEqual(request.get_list("size"), ["M", "L"])
        self.assertEqual(request.get("q"), "tee")

    def test_json_body(self):
        request = self.make_request("POST", "/")
        request.body = b'{"a": 1}'
        self.assertEqual(request.json_body(), {"a": 1})
        request.body = b"not json"
        self.assertIsNone(request.json_body())

    def test_non_form_content_type_yields_empty_form(self):
        request = self.make_request("POST", "/")
        request.headers["Content-Type"] = "application/json"
        request.body = b'{"a":1}'
        self.assertEqual(request.form, {})

    def test_url_for_self_round_trips_the_query(self):
        request = self.make_request(path="/shop", query={"q": ["tee"], "page": ["2"]})
        self.assertIn("q=tee", request.url_for_self())
        self.assertIn("page=2", request.url_for_self())


class ResponseTests(unittest.TestCase):
    def test_string_bodies_are_encoded(self):
        self.assertEqual(Response("héllo").body, "héllo".encode())

    def test_cookies(self):
        response = Response().set_cookie("a", "b c", max_age=60)
        self.assertIn("a=b%20c", response.cookies[0])
        self.assertIn("Max-Age=60", response.cookies[0])
        self.assertIn("HttpOnly", response.cookies[0])
        self.assertIn("SameSite=Lax", response.cookies[0])

    def test_delete_cookie(self):
        response = Response().delete_cookie("a")
        self.assertIn("Max-Age=0", response.cookies[0])

    def test_redirect_carries_a_flash(self):
        response = web.redirect("/x", flash="done", tone="error")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/x")
        self.assertIn("error%3Adone", response.cookies[0])

    def test_json_response(self):
        response = web.json_response({"ok": True}, status=201)
        self.assertEqual(response.status, 201)
        self.assertIn("application/json", response.content_type)
        self.assertEqual(response.body, b'{"ok": true}')


class StaticFileTests(StoreTestCase):
    def test_serves_a_real_file_with_an_etag(self):
        response = web.serve_static(self.make_request(), "css/site.css")
        self.assertEqual(response.status, 200)
        self.assertIn("text/css", response.content_type)
        self.assertIn("ETag", response.headers)

    def test_conditional_request_returns_304(self):
        request = self.make_request()
        first = web.serve_static(request, "css/site.css")
        request.headers["If-None-Match"] = first.headers["ETag"]
        self.assertEqual(web.serve_static(request, "css/site.css").status, 304)

    def test_path_traversal_is_refused(self):
        for attempt in ("../config.py", "../../etc/passwd", "css/../../db.py",
                        "/etc/passwd"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(HttpError) as caught:
                    web.serve_static(self.make_request(), attempt)
                self.assertEqual(caught.exception.status, 404)

    def test_missing_file_is_404(self):
        with self.assertRaises(HttpError):
            web.serve_static(self.make_request(), "css/nope.css")


class SecurityHeaderTests(unittest.TestCase):
    def test_baseline_headers(self):
        headers = web._security_headers()
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertIn("object-src 'none'", headers["Content-Security-Policy"])

    def test_hsts_only_when_https_is_forced(self):
        from app.config import config
        original = config.force_https
        try:
            config.force_https = False
            self.assertNotIn("Strict-Transport-Security", web._security_headers())
            config.force_https = True
            self.assertIn("Strict-Transport-Security", web._security_headers())
        finally:
            config.force_https = original


class EscapingTests(unittest.TestCase):
    def test_escape_neutralises_markup(self):
        self.assertEqual(
            web.escape('<script>"x"</script>'),
            "&lt;script&gt;&quot;x&quot;&lt;/script&gt;",
        )


if __name__ == "__main__":
    unittest.main()
