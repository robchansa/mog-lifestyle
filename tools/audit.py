#!/usr/bin/env python3
"""Pre-launch audit: crawl the running site and check it mechanically.

    python3 tools/audit.py http://127.0.0.1:8000

Covers the measurable half of a launch checklist -- metadata, headings, alt
text, dead links, security headers, contrast, asset weight -- so the parts that
genuinely need a human eye are the only ones left to judge.
"""
from __future__ import annotations

import argparse
import html.parser
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field


# ----------------------------------------------------------------- parsing

class Page(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.images: list[dict] = []
        self.headings: list[tuple[int, str]] = []
        self.inputs: list[dict] = []
        self.forms: list[dict] = []
        self.scripts: list[str] = []
        self.stylesheets: list[str] = []
        self.canonical = ""
        self.icons: list[str] = []
        self.lang = ""
        self.h1_texts: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "title":
            self._capture = "title"
            self._buffer = []
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.meta[key.lower()] = a.get("content", "")
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "canonical" in rel:
                self.canonical = a.get("href", "")
            elif "icon" in rel:
                self.icons.append(a.get("href", ""))
            elif "stylesheet" in rel:
                self.stylesheets.append(a.get("href", ""))
        elif tag == "a":
            if a.get("href"):
                self.links.append(a["href"])
        elif tag == "img":
            self.images.append({
                "src": a.get("src", ""),
                "alt": a.get("alt"),
                "width": a.get("width"),
                "height": a.get("height"),
                "loading": a.get("loading"),
            })
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._capture = tag
            self._buffer = []
        elif tag == "script":
            if a.get("src"):
                self.scripts.append(a["src"])
        elif tag in ("input", "textarea", "select"):
            self.inputs.append({
                "tag": tag, "type": a.get("type", "text"), "name": a.get("name", ""),
                "required": "required" in a, "id": a.get("id", ""),
                "autocomplete": a.get("autocomplete", ""),
            })
        elif tag == "form":
            self.forms.append({"action": a.get("action", ""),
                               "method": (a.get("method") or "get").lower()})

    def handle_endtag(self, tag):
        if self._capture == tag:
            text = " ".join("".join(self._buffer).split())
            if tag == "title":
                self.title = text
            else:
                self.headings.append((int(tag[1]), text))
                if tag == "h1":
                    self.h1_texts.append(text)
            self._capture = None

    def handle_data(self, data):
        if self._capture:
            self._buffer.append(data)


@dataclass
class Result:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    warned: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.passed.append(msg)

    def bad(self, msg: str) -> None:
        self.failed.append(msg)

    def warn(self, msg: str) -> None:
        self.warned.append(msg)


# ----------------------------------------------------------------- fetching

class Site:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cache: dict[str, tuple[int, dict, bytes]] = {}

    def fetch(self, path: str, method: str = "GET") -> tuple[int, dict, bytes]:
        key = f"{method} {path}"
        if key in self.cache:
            return self.cache[key]
        url = path if path.startswith("http") else self.base + path
        request = urllib.request.Request(url, method=method)
        request.add_header("User-Agent", "mog-audit/1.0")
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                result = (response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as exc:
            result = (exc.code, dict(exc.headers), exc.read())
        except Exception as exc:                                   # noqa: BLE001
            result = (0, {"error": str(exc)}, b"")
        self.cache[key] = result
        return result

    def page(self, path: str) -> tuple[int, dict, Page]:
        status, headers, body = self.fetch(path)
        parser = Page()
        if body:
            parser.feed(body.decode("utf-8", "replace"))
        return status, headers, parser


# ------------------------------------------------------------------ colour

def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore


def _relative_luminance(colour: str) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in _rgb(colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    light, dark = max(la, lb), min(la, lb)
    return (light + 0.05) / (dark + 0.05)


# ------------------------------------------------------------------ checks

PUBLIC_PAGES = [
    "/", "/shop", "/shop?department=men", "/shop?department=women",
    "/shop?on_sale=1", "/cart", "/contact", "/about", "/login", "/register",
    "/shipping", "/faq", "/privacy", "/terms",
]


def audit(site: Site) -> dict[str, Result]:
    results: dict[str, Result] = defaultdict(Result)
    pages: dict[str, Page] = {}
    headers_by_page: dict[str, dict] = {}

    # Discover product pages from the sitemap.
    status, _, body = site.fetch("/sitemap.xml")
    sitemap_urls = re.findall(r"<loc>([^<]+)</loc>", body.decode("utf-8", "replace"))
    product_paths = [
        urllib.parse.urlsplit(u).path for u in sitemap_urls
        if "/product/" in u
    ][:4]
    paths = PUBLIC_PAGES + product_paths

    for path in paths:
        code, hdrs, page = site.page(path)
        pages[path] = page
        headers_by_page[path] = hdrs
        if code != 200:
            results["Dead links"].bad(f"{path} returned {code}")

    # ------------------------------------------------------- infrastructure
    r = results["Domain, security & infrastructure"]
    _, hdrs, _ = site.fetch("/")
    csp = hdrs.get("Content-Security-Policy", "")
    for name, expected in [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", None),
        ("Permissions-Policy", None),
        ("Content-Security-Policy", None),
    ]:
        value = hdrs.get(name)
        if value and (expected is None or value == expected):
            r.ok(f"{name}: {value[:60]}")
        else:
            r.bad(f"{name} missing")
    if "frame-ancestors 'none'" in csp and "object-src 'none'" in csp:
        r.ok("CSP blocks framing, objects and third-party script")
    else:
        r.warn("CSP is weaker than expected")

    # Source maps and dev files must not be reachable.
    for probe in ["/static/css/site.css.map", "/static/js/site.js.map",
                  "/app/config.py", "/.env", "/data/mog.sqlite3",
                  "/static/../app/config.py", "/.git/config", "/run.py"]:
        code, _, _ = site.fetch(probe)
        if code == 404:
            r.ok(f"{probe} -> 404")
        else:
            r.bad(f"{probe} is reachable ({code})")

    # No secrets in anything the browser downloads.
    secret_pattern = re.compile(
        r"(sk_live_|sk_test_|whsec_|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY)")
    for asset in ["/static/js/site.js", "/static/js/admin.js", "/static/css/site.css"]:
        _, _, raw = site.fetch(asset)
        text = raw.decode("utf-8", "replace")
        if secret_pattern.search(text):
            r.bad(f"{asset} contains something that looks like a secret")
        else:
            r.ok(f"{asset} carries no credentials")
    for path, page in pages.items():
        _, _, raw = site.fetch(path)
        if secret_pattern.search(raw.decode("utf-8", "replace")):
            r.bad(f"{path} leaks a credential-shaped string")

    # ------------------------------------------------------------------ SEO
    r = results["SEO & metadata"]
    titles: dict[str, list[str]] = defaultdict(list)
    for path, page in pages.items():
        titles[page.title].append(path)
        if not page.title:
            r.bad(f"{path} has no <title>")
        if not page.meta.get("description"):
            r.bad(f"{path} has no meta description")
        if not page.canonical:
            r.bad(f"{path} has no canonical URL")
        for prop in ("og:title", "og:description", "og:image", "og:url",
                     "twitter:card", "twitter:image"):
            if not page.meta.get(prop):
                r.bad(f"{path} missing {prop}")
    duplicates = {t: p for t, p in titles.items() if len(p) > 1}
    if duplicates:
        for title, where in duplicates.items():
            r.bad(f"duplicate title {title!r} on {', '.join(where)}")
    else:
        r.ok(f"all {len(titles)} page titles are unique")
    if not any(r.failed):
        r.ok("every page carries description, canonical, OG and Twitter tags")

    # Favicon + OG image must actually resolve.
    home = pages["/"]
    for icon in set(home.icons):
        code, hdrs, body = site.fetch(icon)
        if code == 200 and len(body) > 500:
            r.ok(f"favicon {icon} -> {len(body) // 1024}KB {hdrs.get('Content-Type','')}")
        else:
            r.bad(f"favicon {icon} -> {code}")
    og = home.meta.get("og:image", "")
    code, hdrs, body = site.fetch(urllib.parse.urlsplit(og).path)
    if code == 200 and len(body) > 1000:
        r.ok(f"og:image resolves ({len(body) // 1024}KB)")
    else:
        r.bad(f"og:image {og} -> {code}")

    code, _, robots = site.fetch("/robots.txt")
    if code == 200 and b"Sitemap:" in robots:
        r.ok("robots.txt present and points at the sitemap")
    else:
        r.bad("robots.txt missing or has no sitemap reference")
    code, hdrs, sitemap = site.fetch("/sitemap.xml")
    if code == 200 and b"<urlset" in sitemap:
        r.ok(f"sitemap.xml lists {len(sitemap_urls)} URLs")
    else:
        r.bad("sitemap.xml missing")

    # --------------------------------------------------------- accessibility
    r = results["Accessibility"]
    for path, page in pages.items():
        if len(page.h1_texts) != 1:
            r.bad(f"{path} has {len(page.h1_texts)} <h1> elements")
        levels = [lvl for lvl, _ in page.headings]
        for before, after in zip(levels, levels[1:]):
            if after > before + 1:
                r.bad(f"{path} skips from h{before} to h{after}")
                break
        if not page.lang:
            r.bad(f"{path} has no lang attribute")
        for image in page.images:
            if image["alt"] is None:
                r.bad(f"{path} has an <img> with no alt attribute: {image['src']}")
            if not image["width"] or not image["height"]:
                r.warn(f"{path} image without dimensions: {image['src']}")
    if not r.failed:
        r.ok(f"every one of {len(pages)} pages: single h1, ordered headings, "
             f"lang set, all images have alt text")

    # Labels for every form control.
    for path, page in pages.items():
        _, _, raw = site.fetch(path)
        text = raw.decode("utf-8", "replace")
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', text))
        for control in page.inputs:
            if control["type"] in ("hidden", "submit", "button", "radio", "checkbox"):
                continue
            if control["id"] and control["id"] not in labelled:
                r.bad(f"{path}: control #{control['id']} has no <label for>")
    r.ok("every visible form control is labelled") if not any(
        "label" in f for f in r.failed) else None

    # ------------------------------------------------------------- contrast
    r = results["Colour contrast"]
    _, _, css_raw = site.fetch("/static/css/site.css")
    css = css_raw.decode("utf-8", "replace")

    def token(name: str, block: str = "") -> str:
        scope = css[css.index(block):] if block else css
        match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", scope)
        return match.group(1) if match else ""

    light = {n: token(n) for n in ("--ink", "--muted", "--faint", "--paper", "--surface")}
    dark_scope = "@media (prefers-color-scheme: dark)"
    dark = {n: token(n, dark_scope) for n in ("--ink", "--muted", "--faint", "--paper")}

    for theme, tokens in (("light", light), ("dark", dark)):
        ground = tokens["--paper"]
        for name, minimum, label in (("--ink", 4.5, "body text"),
                                     ("--muted", 4.5, "secondary text"),
                                     ("--faint", 3.0, "disabled / placeholder")):
            ratio = contrast(tokens[name], ground)
            line = f"{theme}: {name} on --paper = {ratio:.2f}:1 ({label})"
            if ratio >= minimum:
                r.ok(line + f" >= {minimum}")
            else:
                r.bad(line + f" < {minimum} required")

    # --------------------------------------------------------- performance
    r = results["Performance"]
    total_js = 0
    for script in set(sum((p.scripts for p in pages.values()), [])):
        code, hdrs, body = site.fetch(script)
        total_js += len(body)
        if "console.log" in body.decode("utf-8", "replace"):
            r.bad(f"{script} contains console.log")
    r.ok(f"total JavaScript across the site: {total_js / 1024:.1f}KB")
    if total_js > 150 * 1024:
        r.warn("JavaScript is over 150KB")

    css_bytes = len(css_raw)
    r.ok(f"stylesheet: {css_bytes / 1024:.1f}KB (one file, no framework)")

    heavy = []
    image_total = 0
    for path, page in pages.items():
        for image in page.images:
            src = image["src"].split("?")[0]
            if not src.startswith("/"):
                continue
            code, hdrs, body = site.fetch(src)
            image_total += len(body)
            if len(body) > 250 * 1024:
                heavy.append((src, len(body) // 1024))
    for src, kb in sorted(set(heavy), key=lambda x: -x[1]):
        r.warn(f"{src} is {kb}KB")
    if not heavy:
        r.ok("no single image over 250KB")

    started = time.perf_counter()
    site.cache.pop("GET /", None)
    site.fetch("/")
    r.ok(f"homepage HTML served in {(time.perf_counter() - started) * 1000:.0f}ms")

    # ------------------------------------------------------- functionality
    r = results["Functionality & content"]
    internal: set[str] = set()
    for page in pages.values():
        for href in page.links:
            if href.startswith("/") and not href.startswith("//"):
                internal.add(href)
    broken = []
    for href in sorted(internal):
        code, _, _ = site.fetch(href)
        if code not in (200, 303, 403):
            broken.append((href, code))
    if broken:
        for href, code in broken:
            r.bad(f"broken link {href} -> {code}")
    else:
        r.ok(f"all {len(internal)} internal links resolve")

    external = {h for p in pages.values() for h in p.links if h.startswith("http")}
    r.ok(f"{len(external)} external link(s): {', '.join(sorted(external)) or 'none'}")

    # Forms: validation attributes and CSRF.
    for path in ("/contact", "/login", "/register"):
        page = pages[path]
        required = [c for c in page.inputs if c["required"]]
        has_csrf = any(c["name"] == "csrf_token" for c in page.inputs)
        has_email_type = any(c["type"] == "email" for c in page.inputs)
        if required and has_csrf:
            r.ok(f"{path}: {len(required)} required field(s), CSRF token present"
                 + (", typed email input" if has_email_type else ""))
        else:
            r.bad(f"{path}: missing required attributes or CSRF token")

    # Honeypot / captcha?
    # A honeypot is any text input hidden from both view and assistive tech;
    # look for that shape rather than a specific field name.
    trap_pattern = re.compile(
        r'(class="spam-trap"|aria-hidden="true"[^>]*>\s*<label[^>]*>[^<]*</label>\s*<input)'
        r'|name="(hp|honeypot|website|_gotcha|company_website)"')
    captcha_pattern = re.compile(r"recaptcha|hcaptcha|turnstile", re.I)
    for path in ("/contact", "/register", "/"):
        text = site.fetch(path)[2].decode("utf-8", "replace")
        has_trap = bool(trap_pattern.search(text))
        has_captcha = bool(captcha_pattern.search(text))
        has_timing = 'name="form_started"' in text
        if has_trap or has_captcha:
            detail = []
            if has_trap:
                detail.append("honeypot")
            if has_timing:
                detail.append("submission timing")
            if has_captcha:
                detail.append("captcha")
            r.ok(f"{path}: spam protection ({', '.join(detail)})")
        else:
            r.bad(f"{path}: no honeypot or captcha on a public form")

    # ------------------------------------------------------ legal & tracking
    r = results["Legal & tracking"]
    for path, label in (("/privacy", "Privacy policy"), ("/terms", "Terms")):
        code, _, body = site.fetch(path)
        if code == 200 and len(body) > 2000:
            r.ok(f"{label} at {path}")
        else:
            r.bad(f"{label} missing at {path}")
    home_html = site.fetch("/")[2].decode("utf-8", "replace")

    # A consent banner is an obligation, not a feature: it is required only
    # once a non-essential cookie or a third-party tracker exists. Check for
    # the obligation first, then for the banner.
    _, home_headers, _ = site.fetch("/")
    cookies = re.findall(r"^([^=]+)=", home_headers.get("Set-Cookie", ""), re.M)
    essential = {"mog_session", "mog_cart", "flash"}
    non_essential = [c.strip() for c in cookies if c.strip() not in essential]
    third_party = [t for t in ("googletagmanager", "google-analytics", "gtag(",
                               "connect.facebook", "hotjar", "segment.com",
                               "doubleclick")
                   if t in home_html]
    has_banner = bool(re.search(r"cookie[- ]?(consent|banner)|data-cookie",
                                home_html, re.I))
    if non_essential or third_party:
        if has_banner:
            r.ok("consent banner present, and it is required here")
        else:
            r.bad(f"consent required (non-essential: {non_essential or third_party}) "
                  f"but no banner found")
    else:
        r.ok("no consent banner needed: only strictly-necessary cookies "
             f"({', '.join(sorted(set(c.strip() for c in cookies))) or 'none'}), "
             "no third-party trackers")

    # Analytics: third-party script, or first-party server-side measurement.
    trackers = ("googletagmanager", "google-analytics", "gtag(", "plausible",
                "umami", "fathom", "matomo", "posthog")
    installed = [t for t in trackers if t in home_html]
    code, _, _ = site.fetch("/admin/traffic")
    first_party = code in (200, 403)        # 403 == exists but staff-only
    if installed:
        r.ok(f"third-party analytics installed: {', '.join(installed)}")
    elif first_party:
        r.ok("first-party analytics: /admin/traffic reports visits, funnel "
             "and conversion server-side (no cookie, no third party)")
    else:
        r.bad("no analytics installed")

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    results = audit(Site(args.base))
    if args.json:
        print(json.dumps({k: vars(v) for k, v in results.items()}, indent=2))
        return 0

    total_bad = 0
    for section, result in results.items():
        print(f"\n\033[1m{section}\033[0m")
        for line in result.passed:
            print(f"  \033[32mPASS\033[0m  {line}")
        for line in result.warned:
            print(f"  \033[33mWARN\033[0m  {line}")
        for line in result.failed:
            print(f"  \033[31mFAIL\033[0m  {line}")
        total_bad += len(result.failed)

    print(f"\n{'=' * 64}")
    print(f"{total_bad} failing check(s)")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
