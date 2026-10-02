"""Hosting: the WSGI adapter, the private env file, production seeding, the
command-line tasks, the upload zip and the GitHub deploy workflow.

The most important test here builds the real zip, unpacks it somewhere else and
boots it the way Passenger will -- so what ships is what was tested.
"""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from app import db, envfile, seed
from app.application import create_app
from app.config import config
from app.wsgi import create_wsgi_app, make_request
from tests import TMP_DIR
from tests.support import StoreTestCase

ROOT = Path(__file__).resolve().parent.parent


def call(app, path: str = "/", *, method: str = "GET", body: bytes = b"",
         **environ) -> tuple[str, dict, list[tuple[str, str]], bytes]:
    """Drive a WSGI app once; return status, headers, raw header list, body."""
    env: dict = {}
    setup_testing_defaults(env)
    query = ""
    if "?" in path:
        path, query = path.split("?", 1)
    env.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING=query,
               REMOTE_ADDR="198.51.100.20", HTTP_HOST="moglifestyle.fit")
    env["wsgi.input"] = io.BytesIO(body)
    if body:
        env["CONTENT_LENGTH"] = str(len(body))
        env["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
    env.update(environ)
    captured: dict = {}

    def start_response(status, headers):
        captured["status"], captured["headers"] = status, headers

    payload = b"".join(app(env, start_response))
    return (captured["status"], dict(captured["headers"]), captured["headers"], payload)


def subprocess_env(**overrides: str) -> dict:
    env = dict(os.environ)
    env.update(MOG_ENV="test", MOG_ENV_FILE=str(TMP_DIR / "no-such-env-file"),
               MOG_SECRET_KEY="x" * 64, MOG_PBKDF2_ROUNDS="1000")
    env.update(overrides)
    return env


# ================================================================ env file

class EnvFileTests(StoreTestCase):
    def test_parsing(self):
        parsed = envfile.parse(
            "# comment\n\nMOG_ENV=production\nexport MOG_PORT = 9000\n"
            "SMTP_PASSWORD=p#ss=word\nQUOTED=\"with spaces\"\nSINGLE='x'\n"
            "not a line\n1BAD=no\nEMPTY=\n"
        )
        self.assertEqual(parsed, {
            "MOG_ENV": "production", "MOG_PORT": "9000",
            "SMTP_PASSWORD": "p#ss=word", "QUOTED": "with spaces",
            "SINGLE": "x", "EMPTY": "",
        })

    def test_loading_keeps_variables_already_set(self):
        path = TMP_DIR / "settings.env"
        path.write_text("MOG_ENV=production\nMOG_TIMEZONE=UTC\n")
        environ = {"MOG_ENV": "staging"}
        self.assertEqual(envfile.load_env_file(path, environ=environ), path)
        self.assertEqual(environ, {"MOG_ENV": "staging", "MOG_TIMEZONE": "UTC"})

    def test_the_path_can_come_from_the_environment(self):
        path = TMP_DIR / "elsewhere.env"
        path.write_text("A=1\n")
        environ = {"MOG_ENV_FILE": str(path)}
        envfile.load_env_file(environ=environ)
        self.assertEqual(environ["A"], "1")

    def test_a_missing_file_is_not_an_error(self):
        self.assertIsNone(envfile.load_env_file(TMP_DIR / "absent.env", environ={}))


# ==================================================================== WSGI

class WsgiTests(StoreTestCase):
    seed_catalogue = True

    def setUp(self):
        super().setUp()
        self.app = create_wsgi_app(create_app())

    def test_a_page_with_every_security_header(self):
        status, headers, _, body = call(self.app, "/")
        self.assertEqual(status, "200 OK")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertIn(b"MOG", body)
        for name in ("Content-Security-Policy", "X-Frame-Options",
                     "X-Content-Type-Options", "Referrer-Policy", "X-Response-Time"):
            self.assertIn(name, headers)

    def test_head_has_headers_but_no_body(self):
        status, headers, _, body = call(self.app, "/", method="HEAD")
        self.assertEqual(status, "200 OK")
        self.assertEqual(body, b"")
        self.assertGreater(int(headers["Content-Length"]), 0)

    def test_errors_and_redirects(self):
        self.assertEqual(call(self.app, "/no-such-page")[0], "404 Not Found")
        status, headers, _, _ = call(self.app, "/admin/analytics")
        self.assertEqual(status, "303 See Other")
        self.assertEqual(headers["Location"], "/login?next=%2Fadmin%2Fanalytics")

    def test_session_and_cart_cookies_are_all_sent(self):
        _, _, raw, _ = call(self.app, "/")
        names = [value.split("=")[0] for name, value in raw if name == "Set-Cookie"]
        self.assertIn("mog_session", names)
        self.assertIn("mog_cart", names)

    def test_the_scheme_comes_from_the_server_not_the_client(self):
        original = config.force_https
        config.force_https = True
        try:
            # Plain HTTP that *claims* to be HTTPS is still redirected.
            status, headers, _, _ = call(self.app, "/shop",
                                         HTTP_X_FORWARDED_PROTO="https")
            self.assertEqual(status, "301 Moved Permanently")
            self.assertEqual(headers["Location"], "https://moglifestyle.fit/shop")
            # Apache terminating TLS says so through the environ.
            self.assertEqual(call(self.app, "/shop", HTTPS="on")[0], "200 OK")
            self.assertEqual(
                call(self.app, "/shop", **{"wsgi.url_scheme": "https"})[0], "200 OK")
        finally:
            config.force_https = original

    def test_the_client_address_cannot_be_forged(self):
        env: dict = {}
        setup_testing_defaults(env)
        env.update(REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR="1.2.3.4",
                   HTTP_X_REAL_IP="5.6.7.8")
        request = make_request(env)
        self.assertEqual(request.remote_addr, "198.51.100.7")
        self.assertIsNone(request.headers.get("X-Forwarded-For"))

    def test_utf8_paths_and_query_strings(self):
        env: dict = {}
        setup_testing_defaults(env)
        env.update(PATH_INFO="/search/café".encode("utf-8").decode("latin-1"),
                   QUERY_STRING="q=t%C3%A9e&q=two&empty=")
        request = make_request(env)
        self.assertEqual(request.path, "/search/café")
        self.assertEqual(request.query["q"], ["tée", "two"])
        self.assertEqual(request.query["empty"], [""])

    def test_oversized_bodies_are_refused(self):
        status, *_ = call(self.app, "/contact", method="POST", body=b"x",
                          CONTENT_LENGTH=str(5 * 1024 * 1024))
        self.assertTrue(status.startswith("413 "), status)

    def test_a_post_without_a_token_is_refused(self):
        status, *_ = call(self.app, "/contact", method="POST",
                          body=b"name=a&email=a%40example.com&message=hello")
        self.assertEqual(status, "403 Forbidden")

    def test_a_malformed_cookie_header_does_not_break_the_page(self):
        status, *_ = call(self.app, "/", HTTP_COOKIE='mog_session="unterminated; ;;=')
        self.assertEqual(status, "200 OK")


# ============================================================== seeding

class ProductionSeedTests(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.original_env = config.env
        self.saved = {k: os.environ.pop(k, None)
                      for k in ("MOG_ADMIN_EMAIL", "MOG_ADMIN_PASSWORD")}
        config.env = "production"

    def tearDown(self):
        config.env = self.original_env
        for key, value in self.saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        super().tearDown()

    def admins(self) -> int:
        return db.scalar("SELECT count(*) FROM users WHERE role = 'admin'", (), 0)

    def test_production_never_creates_the_public_development_admin(self):
        seed.run(quiet=True)
        self.assertEqual(self.admins(), 0)
        self.assertGreater(db.scalar("SELECT count(*) FROM products"), 0,
                           "the catalogue is still seeded")

    def test_the_development_password_is_refused_outright(self):
        os.environ["MOG_ADMIN_PASSWORD"] = seed.DEV_ADMIN_PASSWORD
        with self.assertRaises(seed.SeedError):
            seed.run(quiet=True)

    def test_a_weak_password_is_refused(self):
        os.environ["MOG_ADMIN_PASSWORD"] = "short"
        with self.assertRaises(seed.SeedError):
            seed.run(quiet=True)

    def test_a_strong_password_creates_the_admin(self):
        os.environ["MOG_ADMIN_EMAIL"] = "owner@moglifestyle.fit"
        os.environ["MOG_ADMIN_PASSWORD"] = "a long and private passphrase"
        seed.run(quiet=True)
        self.assertEqual(db.scalar("SELECT email FROM users WHERE role = 'admin'"),
                         "owner@moglifestyle.fit")


class MigrationRaceTests(StoreTestCase):
    def test_losing_a_race_to_add_a_column_is_harmless(self):
        class Racing:
            def execute(self, sql):
                if sql.startswith("PRAGMA"):
                    return [{"name": "id"}]
                raise sqlite3.OperationalError("duplicate column name: images")
        self.assertFalse(db._ensure_column(Racing(), "products", "images", "TEXT"))

    def test_other_migration_errors_still_surface(self):
        class Broken:
            def execute(self, sql):
                if sql.startswith("PRAGMA"):
                    return [{"name": "id"}]
                raise sqlite3.OperationalError("disk I/O error")
        with self.assertRaises(sqlite3.OperationalError):
            db._ensure_column(Broken(), "products", "images", "TEXT")


# ========================================================= command line

class CommandLineTests(StoreTestCase):
    def run_cli(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        database = TMP_DIR / "cli.sqlite3"
        return subprocess.run(
            [sys.executable, str(ROOT / "run.py"), *args], cwd=ROOT,
            env=subprocess_env(MOG_DB=str(database), **env),
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )

    def test_migrate(self):
        result = self.run_cli("--migrate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("database up to date", result.stdout)

    def test_create_admin_then_reset_it(self):
        made = self.run_cli("--create-admin", "boss@example.com",
                            MOG_ADMIN_PASSWORD="first long passphrase")
        self.assertEqual(made.returncode, 0, made.stderr)
        self.assertIn("admin created", made.stdout)
        reset = self.run_cli("--create-admin", "boss@example.com",
                             MOG_ADMIN_PASSWORD="second long passphrase")
        self.assertIn("admin updated", reset.stdout)
        conn = sqlite3.connect(TMP_DIR / "cli.sqlite3")
        role = conn.execute("SELECT role FROM users WHERE email = 'boss@example.com'")
        self.assertEqual(role.fetchone()[0], "admin")

    def test_create_admin_refuses_weak_or_public_passwords(self):
        for password in ("short", seed.DEV_ADMIN_PASSWORD):
            with self.subTest(password=password):
                result = self.run_cli("--create-admin", "weak@example.com",
                                      MOG_ADMIN_PASSWORD=password)
                self.assertEqual(result.returncode, 2)

    def test_create_admin_without_a_password_or_terminal_explains_itself(self):
        result = self.run_cli("--create-admin", "nobody@example.com")
        self.assertEqual(result.returncode, 2)
        self.assertIn("MOG_ADMIN_PASSWORD", result.stderr)

    def test_backup_writes_a_restorable_compressed_copy(self):
        import gzip
        target = TMP_DIR / "backups"
        result = self.run_cli("--backup", str(target))
        self.assertEqual(result.returncode, 0, result.stderr)
        archives = list(target.glob("mog-*.sqlite3.gz"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(oct(archives[0].stat().st_mode & 0o777), "0o600")
        restored = TMP_DIR / "restored.sqlite3"
        restored.write_bytes(gzip.decompress(archives[0].read_bytes()))
        check = sqlite3.connect(restored).execute("PRAGMA integrity_check").fetchone()
        self.assertEqual(check[0], "ok")

    def test_settings_come_from_the_env_file(self):
        settings = TMP_DIR / "cli.env"
        database = TMP_DIR / "from-env-file.sqlite3"
        settings.write_text(f"MOG_DB={database}\n")
        env = subprocess_env(MOG_ENV_FILE=str(settings))
        env.pop("MOG_DB", None)
        result = subprocess.run([sys.executable, str(ROOT / "run.py"), "--migrate"],
                                cwd=ROOT, env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(database.exists())


# ============================================================ packaging

class PackageTests(StoreTestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "tools"))
        import package
        cls.package = package
        cls.out = Path(tempfile.mkdtemp(prefix="mog-dist-"))
        cls.stage = cls.out / "site"
        cls.archive = package.build(cls.out, cls.stage)
        with zipfile.ZipFile(cls.archive) as zf:
            cls.names = zf.namelist()
            cls.contents = {name: zf.read(name) for name in cls.names}

    def test_it_contains_what_the_server_runs(self):
        for name in ("passenger_wsgi.py", "run.py", "app/wsgi.py", "app/schema.sql",
                     "app/static/css/site.css", "app/static/brand/wordmark.png",
                     "docs/NAMECHEAP.md", "docs/mog.env.example", "BUILD.json"):
            self.assertIn(name, self.names)
        self.assertNotIn("moglifestyle/passenger_wsgi.py", self.names,
                         "files sit at the top of the zip, ready to extract in place")

    def test_it_never_contains_data_secrets_or_private_material(self):
        forbidden = re.compile(
            r"(^|/)(data|tests|\.git|\.github|__pycache__|dist)/|\.DS_Store$|"
            r"\.sqlite3|\.secret_key$|^mog\.pdf$|intake\.json$|REQUIREMENTS\.md$|"
            r"^MOG files")
        offenders = [name for name in self.names if forbidden.search(name)]
        self.assertEqual(offenders, [])
        secret = re.compile(rb"sk_live_[0-9a-zA-Z]{8}|whsec_[0-9a-zA-Z]{8}|"
                            rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")
        leaking = [n for n, data in self.contents.items() if secret.search(data)]
        self.assertEqual(leaking, [])

    def test_the_build_is_stamped(self):
        info = json.loads(self.contents["BUILD.json"])
        self.assertEqual(info["version"], self.package.version())
        self.assertIn("commit", info)

    def test_builds_are_reproducible(self):
        again = self.package.build(self.out / "again")
        self.assertEqual(again.read_bytes(), self.archive.read_bytes())

    def test_the_staged_tree_matches_the_zip(self):
        staged = sorted(p.relative_to(self.stage).as_posix()
                        for p in self.stage.rglob("*") if p.is_file())
        self.assertEqual(staged, sorted(self.names))

    def test_the_shipped_zip_boots_under_wsgi_in_production_mode(self):
        """Unpack the real artifact elsewhere and serve it as Passenger would."""
        site = Path(tempfile.mkdtemp(prefix="mog-site-"))
        with zipfile.ZipFile(self.archive) as zf:
            zf.extractall(site)
        private = Path(tempfile.mkdtemp(prefix="mog-private-"))
        settings = private / "mog.env"
        settings.write_text(
            "MOG_ENV=production\n"
            f"MOG_SECRET_KEY={'ab' * 32}\n"
            f"MOG_DB={private / 'mog.sqlite3'}\n"
            "MOG_BASE_URL=https://moglifestyle.fit\n"
            "MOG_FORCE_HTTPS=1\nMOG_SECURE_COOKIES=1\n")
        probe = (
            "import io, json, sys\n"
            "from wsgiref.util import setup_testing_defaults\n"
            "sys.path.insert(0, '.')\n"
            "import passenger_wsgi\n"
            "def get(path, https):\n"
            "    env = {}; setup_testing_defaults(env)\n"
            "    env.update(PATH_INFO=path, HTTP_HOST='moglifestyle.fit',\n"
            "               REMOTE_ADDR='198.51.100.9', HTTPS='on' if https else 'off')\n"
            "    env['wsgi.input'] = io.BytesIO(b'')\n"
            "    seen = {}\n"
            "    body = b''.join(passenger_wsgi.application(env,\n"
            "        lambda s, h: seen.update(status=s, headers=dict(h))))\n"
            "    return seen['status'], seen['headers'], body\n"
            "status, headers, body = get('/healthz', True)\n"
            "redirect = get('/shop', False)\n"
            "print(json.dumps({'status': status, 'health': json.loads(body),\n"
            "                  'hsts': headers.get('Strict-Transport-Security', ''),\n"
            "                  'redirect': [redirect[0], redirect[1].get('Location')]}))\n"
        )
        env = subprocess_env(MOG_ENV_FILE=str(settings))
        for key in ("MOG_ENV", "MOG_SECRET_KEY", "MOG_DB", "MOG_BASE_URL"):
            env.pop(key, None)
        env["PYTHONPATH"] = ""
        result = subprocess.run([sys.executable, "-c", probe], cwd=site, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["status"], "200 OK")
        self.assertEqual(report["health"]["status"], "ok")
        from app.application import _short_build
        build = _short_build(json.loads(self.contents["BUILD.json"])["commit"])
        self.assertEqual(report["health"]["build"], build)
        self.assertIn("max-age", report["hsts"])
        self.assertEqual(report["redirect"],
                         ["301 Moved Permanently", "https://moglifestyle.fit/shop"])
        self.assertTrue((private / "mog.sqlite3").exists(),
                        "the database lives where the private settings say")
        self.assertFalse((site / "data").exists(),
                         "nothing is written into the code folder")

    def test_production_without_a_secret_key_refuses_to_start(self):
        site = Path(tempfile.mkdtemp(prefix="mog-site-"))
        with zipfile.ZipFile(self.archive) as zf:
            zf.extractall(site)
        env = subprocess_env(MOG_ENV="production", MOG_DB=str(site / "x.sqlite3"))
        env.pop("MOG_SECRET_KEY")
        result = subprocess.run([sys.executable, "-c", "import passenger_wsgi"],
                                cwd=site, env=env, capture_output=True, text=True,
                                timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MOG_SECRET_KEY must be set in production", result.stderr)


class HealthBuildTests(StoreTestCase):
    def test_healthz_reports_the_deployed_commit(self):
        from app import application
        original = application._BUILD
        application._BUILD = {"commit": "0123456789abcdef0123"}
        try:
            status, _, _, body = call(create_wsgi_app(create_app()), "/healthz")
        finally:
            application._BUILD = original
        self.assertEqual(status, "200 OK")
        self.assertEqual(json.loads(body)["build"], "0123456789ab")


# ============================================================== workflow

class WorkflowTests(StoreTestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()

    def test_tests_gate_the_deploy(self):
        self.assertIn("python tests/run_tests.py", self.text)
        self.assertRegex(self.text, r"deploy:\n(?:.*\n)*?\s+needs: \[test, settings\]")
        self.assertIn("github.ref == 'refs/heads/main'", self.text)
        self.assertIn("github.event_name != 'pull_request'", self.text)

    def test_it_uses_the_folder_limited_ftps_deployer(self):
        self.assertIn("python tools/deploy_ftps.py dist/site", self.text)
        for name in ("NAMECHEAP_FTP_HOST", "NAMECHEAP_FTP_USER",
                     "NAMECHEAP_FTP_PASSWORD"):
            self.assertIn(name, self.text)
        self.assertNotIn("NAMECHEAP_SSH_KEY", self.text)

    def test_it_restarts_and_then_checks_health(self):
        deployer = (ROOT / "tools" / "deploy_ftps.py").read_text()
        self.assertIn('ftp.prot_p()', deployer)
        self.assertIn('STOR tmp/restart.txt', deployer)
        self.assertIn('"passenger_wsgi.py", "BUILD.json"', deployer)
        self.assertIn('\\"build\\": \\"$want\\"', self.text)

    def test_secrets_are_not_echoed(self):
        self.assertNotRegex(self.text, r"echo [^\n]*FTP_PASSWORD")
        self.assertIn("permissions:\n  contents: read", self.text)


# ======================================================= cache busting

class AssetVersioningTests(StoreTestCase):
    """With deploys on every push, a changed stylesheet must reach returning
    visitors at once -- while unchanged files stay cached for a year."""

    seed_catalogue = True

    def setUp(self):
        super().setUp()
        self.app = create_wsgi_app(create_app())

    def test_pages_link_assets_by_content_hash(self):
        _, _, _, body = call(self.app, "/")
        html = body.decode()
        for asset in ("/static/css/site.css", "/static/js/site.js",
                      "/static/brand/favicon.png"):
            self.assertRegex(html, re.escape(asset) + r"\?v=[0-9a-f]{12}")

    def test_the_version_follows_the_file_contents(self):
        from app import web
        asset = Path(web.STATIC_DIR) / "css" / "probe-test.css"
        asset.write_text("a{}")
        try:
            first = web.static_url("/static/css/probe-test.css")
            asset.write_text("b{}")
            os.utime(asset, ns=(asset.stat().st_atime_ns, asset.stat().st_mtime_ns + 10**9))
            second = web.static_url("/static/css/probe-test.css")
        finally:
            asset.unlink()
        self.assertNotEqual(first, second)
        self.assertEqual(web.static_url("/static/css/missing.css"), "/static/css/missing.css")

    def test_production_caching_depends_on_the_version(self):
        original = config.env
        config.env = "production"
        try:
            _, versioned, _, _ = call(self.app, "/static/css/site.css", QUERY_STRING="v=abc")
            _, plain, _, _ = call(self.app, "/static/css/site.css")
        finally:
            config.env = original
        self.assertIn("immutable", versioned["Cache-Control"])
        self.assertEqual(plain["Cache-Control"], "public, max-age=3600")

    def test_favicon_ico_is_answered(self):
        status, headers, _, body = call(self.app, "/favicon.ico")
        self.assertEqual(status, "200 OK")
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))


class UncommittedBuildTests(StoreTestCase):
    def test_a_build_with_local_changes_says_so(self):
        from app import application
        original = application._BUILD
        application._BUILD = {"commit": "0123456789abcdef0123-dirty"}
        try:
            _, _, _, body = call(create_wsgi_app(create_app()), "/healthz")
        finally:
            application._BUILD = original
        self.assertEqual(json.loads(body)["build"], "0123456789ab-dirty")
