"""The design system, expressed as composable HTML functions.

Art direction follows the signed brief: *Elegant / Luxury*, *black & white*,
reference blancc.com.  That means an editorial layout -- high-contrast serif
display type, letter-spaced uppercase micro-labels, hairline rules instead of
shadows, and a lot of air.  Everything is monochrome; the only colour in the
system is the customer's own artwork.
"""
from __future__ import annotations

import html
import json
from typing import Any, Iterable, Mapping, Sequence

from .catalog import image_list
from .config import config
from .web import Request, static_url

__all__ = [
    "E", "attrs", "money", "layout", "page_header", "button", "field",
    "product_card", "product_grid", "empty_state", "pagination", "badge",
    "admin_layout", "table", "error_page", "section_label", "icon",
]


# ------------------------------------------------------------------ basics

def E(value: Any) -> str:
    """Escape a value for HTML text or attribute context."""
    return html.escape("" if value is None else str(value), quote=True)


def attrs(**kwargs: Any) -> str:
    """Render keyword arguments as HTML attributes.

    ``class_`` / ``for_`` map to ``class`` / ``for``; underscores become
    hyphens; ``True`` renders a bare attribute and ``None``/``False`` is
    dropped entirely.
    """
    parts: list[str] = []
    for key, value in kwargs.items():
        if value is None or value is False:
            continue
        name = key.rstrip("_").replace("_", "-")
        if value is True:
            parts.append(name)
        else:
            parts.append(f'{name}="{E(value)}"')
    return (" " + " ".join(parts)) if parts else ""


def money(cents: int | None, *, symbol: bool = True) -> str:
    if cents is None:
        return "—"
    sign = "-" if cents < 0 else ""
    whole, remainder = divmod(abs(int(cents)), 100)
    prefix = config.currency_symbol if symbol else ""
    return f"{sign}{prefix}{whole:,}.{remainder:02d}"


def section_label(text: str, tag: str = "p") -> str:
    return f'<{tag} class="eyebrow">{E(text)}</{tag}>'


def badge(text: str, tone: str = "neutral") -> str:
    return f'<span class="badge badge--{E(tone)}">{E(text)}</span>'


# -------------------------------------------------------------------- icons

_ICONS: dict[str, str] = {
    "search": '<path d="M11 4a7 7 0 1 0 4.2 12.6l3.6 3.6 1.4-1.4-3.6-3.6A7 7 0 0 0 11 4Zm0 2a5 5 0 1 1 0 10 5 5 0 0 1 0-10Z"/>',
    "bag": '<path d="M7 7V6a5 5 0 0 1 10 0v1h3l1 14H3L4 7h3Zm2 0h6V6a3 3 0 1 0-6 0v1Z" fill-rule="evenodd"/>',
    "user": '<path d="M12 12a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0 2c-4.4 0-8 2.4-8 5.4V21h16v-1.6c0-3-3.6-5.4-8-5.4Z"/>',
    "close": '<path d="m5.3 4 14.7 14.7-1.3 1.3L4 5.3 5.3 4Zm14.7 1.3L5.3 20 4 18.7 18.7 4 20 5.3Z"/>',
    "menu": '<path d="M3 6h18v2H3V6Zm0 5h18v2H3v-2Zm0 5h18v2H3v-2Z"/>',
    "arrow": '<path d="m13.2 5 6.5 6.3c.4.4.4 1 0 1.4L13.2 19l-1.4-1.4 4.6-4.6H4v-2h12.4l-4.6-4.6L13.2 5Z"/>',
    "check": '<path d="M9.6 16.2 5.4 12l-1.4 1.4 5.6 5.6L20.4 8.2 19 6.8 9.6 16.2Z"/>',
    "minus": '<path d="M5 11h14v2H5z"/>',
    "plus": '<path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6V5Z"/>',
    "instagram": '<path d="M12 2.2c3.2 0 3.6 0 4.9.1 3.3.1 4.8 1.7 4.9 4.9.1 1.3.1 1.6.1 4.8s0 3.6-.1 4.9c-.1 3.2-1.6 4.8-4.9 4.9-1.3.1-1.7.1-4.9.1s-3.6 0-4.9-.1c-3.3-.2-4.8-1.7-4.9-4.9-.1-1.3-.1-1.7-.1-4.9s0-3.5.1-4.8C2.3 4 3.8 2.4 7.1 2.3c1.3-.1 1.7-.1 4.9-.1Zm0 4.9a4.9 4.9 0 1 0 0 9.8 4.9 4.9 0 0 0 0-9.8Zm0 8a3.2 3.2 0 1 1 0-6.3 3.2 3.2 0 0 1 0 6.3Zm5.1-9.3a1.1 1.1 0 1 0 0 2.3 1.1 1.1 0 0 0 0-2.3Z"/>',
    "tiktok": '<path d="M16.5 2h-3v14a2.6 2.6 0 1 1-2.6-2.6c.3 0 .5 0 .8.1v-3a5.6 5.6 0 1 0 4.8 5.5V8.9a7 7 0 0 0 4 1.3V7.3a4 4 0 0 1-4-4Z"/>',
    "youtube": '<path d="M22 12s0-3.3-.4-4.9a2.6 2.6 0 0 0-1.8-1.8C18.2 4.9 12 4.9 12 4.9s-6.2 0-7.8.4a2.6 2.6 0 0 0-1.8 1.8C2 8.7 2 12 2 12s0 3.3.4 4.9a2.6 2.6 0 0 0 1.8 1.8c1.6.4 7.8.4 7.8.4s6.2 0 7.8-.4a2.6 2.6 0 0 0 1.8-1.8c.4-1.6.4-4.9.4-4.9ZM10 15.2V8.8l5.2 3.2-5.2 3.2Z"/>',
}


def icon(name: str, *, size: int = 20, cls: str = "") -> str:
    body = _ICONS.get(name, "")
    classes = f"icon {cls}".strip()
    return (
        f'<svg class="{E(classes)}" width="{size}" height="{size}" viewBox="0 0 24 24" '
        f'fill="currentColor" aria-hidden="true" focusable="false">{body}</svg>'
    )


# ------------------------------------------------------------------- brand

def wordmark(*, tagline: bool = True, size: str = "") -> str:
    """The supplied MOG logotype.

    Rendered as a CSS mask filled with `currentColor` rather than a flat
    image, so one asset serves light mode, dark mode and the inverted header
    without ever shipping a mismatched black box.
    """
    line = (
        '<span class="wordmark__tag">it\u2019s a lifestyle</span>' if tagline else ""
    )
    return (
        f'<span class="wordmark {E(size)}">'
        f'<span class="wordmark__mark" role="img" aria-label="MOG"></span>'
        f'{line}</span>'
    )


# ------------------------------------------------------------------ chrome

def csrf_input(request: Request) -> str:
    token = request.session["csrf_token"] if request.session else ""
    return f'<input type="hidden" name="csrf_token" value="{E(token)}">'


def shop_menu(active: str = "") -> str:
    """Department links plus one shared panel listing the whole catalogue.

    Hovering (or focusing, or tapping) any department opens a single
    full-width panel showing *every* department and *every* category at once,
    so one gesture reveals the entire structure rather than one slice of it.
    Each top-level item is still a real link, so the nav works with no
    JavaScript and no pointer.
    """
    from . import catalog

    menu = catalog.navigation()
    triggers = "".join(
        f'<a class="nav__link{" is-active" if active == d["slug"] else ""}"'
        f' href="/shop?department={E(d["slug"])}" data-nav-trigger'
        f' aria-expanded="false" aria-controls="shop-menu"'
        f'{attrs(aria_current="page" if active == d["slug"] else None)}>'
        f'{E(d["label"])}<span class="nav__chevron" aria-hidden="true"></span></a>'
        for d in menu
    )

    if catalog.sale_count():
        triggers += (
            f'<a class="nav__link nav__link--sale'
            f'{" is-active" if active == "sale" else ""}" href="/shop?on_sale=1"'
            f'{attrs(aria_current="page" if active == "sale" else None)}>Sale</a>'
        )
    triggers += "".join(
        f'<a class="nav__link{" is-active" if active == slug else ""}"'
        f' href="{href}">{E(label)}</a>'
        for slug, href, label in (("about", "/about", "About"),
                                  ("contact", "/contact", "Contact"))
    )

    columns = "".join(
        f"""
    <div class="megamenu__col">
      <a class="megamenu__heading" href="/shop?department={E(d['slug'])}">
        {E(d['label'])}</a>
      <div class="megamenu__links">{''.join(
          f'<a class="megamenu__link" href="/shop?collection={E(c["slug"])}">'
          f'<span>{E(c["title"])}</span>'
          f'<span class="megamenu__count">{c["product_count"]}</span></a>'
          for c in d['categories'])}</div>
    </div>"""
        for d in menu
    )
    sale_column = ""
    if catalog.sale_count():
        sale_column = f"""
    <div class="megamenu__col megamenu__col--sale">
      <a class="megamenu__heading" href="/shop?on_sale=1">Sale</a>
      <p class="megamenu__note">{catalog.sale_count()} pieces reduced \u2014 last
        of the run, end of a colourway, or simply the final few.</p>
      <a class="megamenu__link" href="/shop?on_sale=1"><span>Shop all sale</span>
        <span class="megamenu__count">{catalog.sale_count()}</span></a>
    </div>"""

    return f"""
<div class="nav__row">{triggers}</div>
<div class="megamenu" id="shop-menu" data-megamenu>
  <div class="megamenu__inner">{columns}{sale_column}</div>
</div>
"""


def site_header(request: Request, active: str = "") -> str:
    cart_count = getattr(request, "cart_count", 0)
    user = request.user
    account_href = "/account" if user else "/login"
    account_label = "Account" if user else "Sign in"
    admin_link = ""
    if user and user["role"] in ("staff", "admin"):
        admin_link = ('<a class="nav__link nav__link--quiet" href="/admin">'
                      'Admin</a>')
    return f"""
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header" data-header>
  <div class="site-header__inner">
    <a class="site-header__brand" href="/" aria-label="MOG Lifestyle — home">
      {wordmark()}
    </a>
    <nav class="nav" id="site-nav" aria-label="Primary">
      {shop_menu(active)}
      {f'<div class="nav__row nav__row--extra">{admin_link}</div>' if admin_link else ''}
    </nav>
    <div class="site-header__actions">
      <form class="search search--header" action="/shop" method="get" role="search">
        <label class="visually-hidden" for="q">Search products</label>
        {icon("search", size=17)}
        <input id="q" type="search" name="q" placeholder="Search"
               autocomplete="off" value="{E(request.get('q'))}">
      </form>
      <a class="icon-btn" href="{account_href}" aria-label="{E(account_label)}">
        {icon("user")}
      </a>
      <a class="icon-btn cart-link" href="/cart" data-cart-link
         aria-label="Cart, {cart_count} item{'' if cart_count == 1 else 's'}">
        {icon("bag")}
        <span class="cart-link__count{' is-empty' if not cart_count else ''}"
              data-cart-count>{cart_count}</span>
      </a>
      <button class="icon-btn nav-toggle" type="button" data-nav-toggle
              aria-expanded="false" aria-controls="site-nav" aria-label="Open menu">
        <span class="nav-toggle__open">{icon("menu")}</span>
        <span class="nav-toggle__close">{icon("close", size=18)}</span>
      </button>
    </div>
  </div>
</header>
"""


def site_footer(request: Request) -> str:
    year = 2026
    social_html = "".join(
        f'<a class="icon-btn" href="{E(url)}" rel="me noopener" target="_blank" '
        f'aria-label="{E(label)}">{icon(name)}</a>'
        for name, url, label in config.social_links
    )
    csrf = csrf_input(request)
    trap = spam_trap()
    return f"""
<footer class="site-footer">
  <div class="site-footer__top">
    <div class="site-footer__signup">
      {section_label("The List")}
      <h2 class="display display--s">First access, no noise.</h2>
      <p class="muted">Drops, restocks and training notes. Roughly monthly.</p>
      <form class="signup" action="/newsletter" method="post" data-async-form>
        {csrf}
        {trap}
        <label class="visually-hidden" for="newsletter-email">Email address</label>
        <input id="newsletter-email" type="email" name="email" required
               placeholder="you@example.com" autocomplete="email">
        <button class="btn btn--solid" type="submit">Join</button>
      </form>
      <p class="signup__status" data-form-status role="status" aria-live="polite"></p>
    </div>
    <nav class="site-footer__nav" aria-label="Footer">
      <div>
        <h3 class="eyebrow">Shop</h3>
        <a href="/shop">All products</a>
        <a href="/shop?department=men">Men</a>
        <a href="/shop?department=women">Women</a>
        <a href="/shop?department=general">Everyone</a>
        <a href="/shop?on_sale=1">Sale</a>
        <a href="/shop?sort=new">New arrivals</a>
      </div>
      <div>
        <h3 class="eyebrow">Support</h3>
        <a href="/contact">Contact</a>
        <a href="/shipping">Shipping &amp; returns</a>
        <a href="/account/orders">Order status</a>
        <a href="/faq">FAQ</a>
      </div>
      <div>
        <h3 class="eyebrow">Company</h3>
        <a href="/about">About</a>
        <a href="/privacy">Privacy</a>
        <a href="/terms">Terms</a>
      </div>
    </nav>
  </div>
  <div class="site-footer__bottom">
    <span>© {year} MOG Lifestyle. All rights reserved.</span>
    <div class="site-footer__social">{social_html}</div>
    <span class="site-footer__contact">
      <a href="mailto:{E(config.store_email)}">{E(config.store_email)}</a>
    </span>
  </div>
</footer>
"""


# ------------------------------------------------------------------ layout

def layout(request: Request, content: str, *, title: str = "",
           description: str = "", active: str = "", body_class: str = "",
           og_image: str = "", scripts: Sequence[str] = (),
           canonical: str = "", json_ld: Mapping[str, Any] | None = None,
           chrome: bool = True) -> str:
    """Wrap page content in the full document shell."""
    full_title = f"{title} — MOG Lifestyle" if title else "MOG Lifestyle — Performance apparel for high achievers"
    description = description or (
        "Fitness apparel and training essentials for people who take the work "
        "seriously. Designed in black and white. Shipped from the USA."
    )
    canonical_url = config.url(canonical or request.path)
    image = og_image or config.url("/static/brand/og.jpg")
    flash_markup = _flash(request)
    structured = (
        f'<script type="application/ld+json">{json.dumps(json_ld)}</script>'
        if json_ld else ""
    )
    extra_scripts = "".join(
        f'<script src="{E(static_url(src))}" defer></script>' for src in scripts
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{E(full_title)}</title>
<meta name="description" content="{E(description)}">
<link rel="canonical" href="{E(canonical_url)}">
<meta name="theme-color" content="#0b0b0c" media="(prefers-color-scheme: dark)">
<meta name="theme-color" content="#faf9f7" media="(prefers-color-scheme: light)">
<meta property="og:site_name" content="MOG Lifestyle">
<meta property="og:type" content="website">
<meta property="og:title" content="{E(full_title)}">
<meta property="og:description" content="{E(description)}">
<meta property="og:url" content="{E(canonical_url)}">
<meta property="og:image" content="{E(image)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{E(full_title)}">
<meta name="twitter:description" content="{E(description)}">
<meta name="twitter:image" content="{E(image)}">
<link rel="icon" href="{E(static_url("/static/brand/favicon.png"))}" type="image/png">
<link rel="apple-touch-icon" href="{E(static_url("/static/brand/favicon.png"))}">
<link rel="stylesheet" href="{E(static_url("/static/css/site.css"))}">
{structured}
</head>
<body class="{E(body_class)}">
{site_header(request, active) if chrome else ""}
{flash_markup}
<main id="main" tabindex="-1">{content}</main>
{site_footer(request) if chrome else ""}
<script src="{E(static_url("/static/js/site.js"))}" defer></script>
{extra_scripts}
</body>
</html>
"""


def _flash(request: Request) -> str:
    raw = request.cookies.get("flash")
    if not raw:
        return ""
    import urllib.parse
    tone, _, message = urllib.parse.unquote(raw.value).partition(":")
    if not message:
        tone, message = "ok", tone
    tone = tone if tone in {"ok", "error", "info"} else "ok"
    return (
        f'<div class="flash flash--{tone}" role="status" data-flash>'
        f'<span>{E(message)}</span>'
        f'<button class="icon-btn" type="button" data-flash-close '
        f'aria-label="Dismiss">{icon("close", size=14)}</button></div>'
    )


def page_header(title: str, *, eyebrow: str = "", lede: str = "",
                aside: str = "") -> str:
    return f"""
<header class="page-header">
  <div>
    {section_label(eyebrow) if eyebrow else ""}
    <h1 class="display display--m">{E(title)}</h1>
    {f'<p class="lede">{E(lede)}</p>' if lede else ""}
  </div>
  {f'<div class="page-header__aside">{aside}</div>' if aside else ""}
</header>
"""


# -------------------------------------------------------------- components

def button(label: str, *, href: str = "", variant: str = "solid",
           type_: str = "button", full: bool = False, **kw: Any) -> str:
    classes = f"btn btn--{variant}{' btn--full' if full else ''}"
    if href:
        return f'<a class="{classes}" href="{E(href)}"{attrs(**kw)}>{E(label)}</a>'
    return f'<button class="{classes}" type="{E(type_)}"{attrs(**kw)}>{E(label)}</button>'


def field(name: str, label: str, *, value: str = "", type_: str = "text",
          required: bool = False, error: str = "", hint: str = "",
          autocomplete: str = "", placeholder: str = "", rows: int = 0,
          options: Sequence[tuple[str, str]] | None = None,
          **kw: Any) -> str:
    """One labelled form control, with hint and error wiring for screen readers."""
    field_id = f"f-{name.replace('_', '-').replace('[', '').replace(']', '')}"
    described: list[str] = []
    if hint:
        described.append(f"{field_id}-hint")
    if error:
        described.append(f"{field_id}-error")
    shared = attrs(
        id=field_id, name=name, required=required or None,
        autocomplete=autocomplete or None, placeholder=placeholder or None,
        aria_invalid="true" if error else None,
        aria_describedby=" ".join(described) or None, **kw,
    )
    if options is not None:
        choices = "".join(
            f'<option value="{E(v)}"{" selected" if str(v) == str(value) else ""}>'
            f"{E(text)}</option>"
            for v, text in options
        )
        control = f"<select{shared}>{choices}</select>"
    elif rows:
        control = f'<textarea rows="{rows}"{shared}>{E(value)}</textarea>'
    else:
        control = f'<input type="{E(type_)}" value="{E(value)}"{shared}>'
    return f"""
<div class="field{' field--error' if error else ''}">
  <label for="{field_id}">{E(label)}{' <abbr title="required">*</abbr>' if required else ''}</label>
  {control}
  {f'<p class="field__hint" id="{field_id}-hint">{E(hint)}</p>' if hint else ''}
  {f'<p class="field__error" id="{field_id}-error">{E(error)}</p>' if error else ''}
</div>
"""


def spam_trap() -> str:
    """A field no human can see, plus a signed render time.

    Hidden from assistive technology and keyboard order as well as from view,
    so it traps bots without ever reaching a real visitor.
    """
    from .security import HONEYPOT_FIELD, TIMESTAMP_FIELD, form_timestamp
    return (
        f'<div class="spam-trap" aria-hidden="true">'
        f'<label for="f-{HONEYPOT_FIELD}">Leave this field empty</label>'
        f'<input id="f-{HONEYPOT_FIELD}" type="text" name="{HONEYPOT_FIELD}"'
        f' tabindex="-1" autocomplete="off" value="">'
        f'</div>'
        f'<input type="hidden" name="{TIMESTAMP_FIELD}" value="{E(form_timestamp())}">'
    )


def product_card(product: Mapping[str, Any], *, eager: bool = False,
                 heading: str = "h3") -> str:
    price = money(product["price_cents"])
    compare = product["compare_cents"] if "compare_cents" in product.keys() else None
    was = f'<s class="price__was">{money(compare)}</s>' if compare else ""
    flags = []
    if compare:
        flags.append(badge("Sale", "sale"))
    if product["stock"] == 0:
        flags.append(badge("Sold out", "muted"))
    elif product["stock"] <= 6:
        flags.append(badge(f"Only {product['stock']} left", "low"))
    return f"""
<article class="card">
  <a class="card__link" href="/product/{E(product['slug'])}">
    <span class="card__media">
      <img src="{E(image_list(product)[0])}" alt="{E(product['title'])}"
           width="800" height="1000"
           loading="{'eager' if eager else 'lazy'}" decoding="async">
    </span>
    <span class="card__body">
      <{heading} class="card__title">{E(product['title'])}</{heading}>
      <span class="card__meta">{E(product['subtitle'])}</span>
      <span class="price">{was}<span>{price}</span></span>
    </span>
  </a>
  {f'<div class="card__flags">{"".join(flags)}</div>' if flags else ""}
</article>
"""


def product_grid(products: Iterable[Mapping[str, Any]], *,
                 columns: str = "", eager_first: int = 4) -> str:
    cards = [
        product_card(p, eager=(i < eager_first))
        for i, p in enumerate(products)
    ]
    if not cards:
        return ""
    return f'<div class="grid {E(columns)}">{"".join(cards)}</div>'


def empty_state(title: str, message: str = "", action: str = "", *,
                heading: str = "h2") -> str:
    """An empty state.

    Pass heading="h1" when the empty state *is* the page -- otherwise the
    document is left with no level-one heading at all.
    """
    return f"""
<div class="empty">
  <{heading} class="display display--xs">{E(title)}</{heading}>
  {f'<p class="muted">{E(message)}</p>' if message else ''}
  {action}
</div>
"""


def pagination(page: int, pages: int, base: str) -> str:
    if pages <= 1:
        return ""
    def link(target: int, label: str, rel: str = "") -> str:
        joiner = "&" if "?" in base else "?"
        return (
            f'<a class="page-link" href="{E(base)}{joiner}page={target}"'
            f'{attrs(rel=rel or None)}>{E(label)}</a>'
        )
    prev_link = link(page - 1, "Previous", "prev") if page > 1 else \
        '<span class="page-link is-disabled">Previous</span>'
    next_link = link(page + 1, "Next", "next") if page < pages else \
        '<span class="page-link is-disabled">Next</span>'
    return f"""
<nav class="pagination" aria-label="Pagination">
  {prev_link}
  <span class="pagination__status">Page {page} of {pages}</span>
  {next_link}
</nav>
"""


def table(headers: Sequence[str], rows: Iterable[Sequence[str]], *,
          empty: str = "Nothing here yet.", caption: str = "",
          numeric: Iterable[int] = ()) -> str:
    """A data table.  Columns listed in `numeric` are right-aligned, header
    included, so figures line up by place value."""
    numeric = set(numeric)

    def cls(index: int) -> str:
        return ' class="num"' if index in numeric else ""

    body = "".join(
        "<tr>" + "".join(f"<td{cls(i)}>{cell}</td>" for i, cell in enumerate(row)) + "</tr>"
        for row in rows
    )
    if not body:
        return f'<p class="muted table-empty">{E(empty)}</p>'
    head = "".join(f"<th scope='col'{cls(i)}>{E(h)}</th>" for i, h in enumerate(headers))
    cap = f"<caption>{E(caption)}</caption>" if caption else ""
    return f"""
<div class="table-wrap">
  <table class="table">{cap}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>
</div>
"""


# ------------------------------------------------------------------- admin

# (href, label, roles that see it).  The route guards are what enforce access;
# this only keeps staff from being shown a link that would refuse them.
ADMIN_NAV = [
    ("/admin", "Overview", ("staff", "admin")),
    ("/admin/analytics", "Analytics", ("admin",)),
    ("/admin/orders", "Orders", ("staff", "admin")),
    ("/admin/products", "Products", ("staff", "admin")),
    ("/admin/inventory", "Inventory", ("staff", "admin")),
    ("/admin/customers", "Customers", ("staff", "admin")),
    ("/admin/discounts", "Discounts", ("staff", "admin")),
    ("/admin/messages", "Messages", ("staff", "admin")),
    ("/admin/email", "Email log", ("staff", "admin")),
    ("/admin/activity", "Activity", ("staff", "admin")),
]


def admin_layout(request: Request, content: str, *, title: str,
                 active: str = "", actions: str = "") -> str:
    role = request.user["role"] if request.user else ""
    links = "".join(
        f'<a href="{E(href)}" class="admin-nav__link'
        f'{" is-active" if href == active else ""}"'
        f'{attrs(aria_current="page" if href == active else None)}>{E(label)}</a>'
        for href, label, roles in ADMIN_NAV
        if role in roles
    )
    user = request.user
    body = f"""
<div class="admin">
  <aside class="admin__side">
    <a class="admin__brand" href="/">{wordmark(tagline=False)}<span class="admin__brand-tag">Admin</span></a>
    <nav class="admin-nav" aria-label="Admin">{links}</nav>
    <form class="admin__signout" method="post" action="/logout">
      {csrf_input(request)}
      <button class="btn btn--ghost btn--full" type="submit">Sign out</button>
    </form>
  </aside>
  <div class="admin__main">
    <header class="admin__header">
      <div>
        <p class="eyebrow">Signed in as {E(user['email']) if user else ''}</p>
        <h1 class="admin__title">{E(title)}</h1>
      </div>
      <div class="admin__actions">{actions}</div>
    </header>
    <div class="admin__content">{content}</div>
  </div>
</div>
"""
    return layout(
        request, body, title=f"{title} · Admin", body_class="body--admin",
        chrome=False, scripts=("/static/js/admin.js",),
    )


# ------------------------------------------------------------------ errors

_ERROR_COPY = {
    404: ("Not found", "That page has moved on. The collection hasn't."),
    403: ("Not permitted", "You don't have access to this page."),
    405: ("Not allowed", "That action isn't available here."),
    413: ("Too large", "That request was bigger than we accept."),
    429: ("Slow down", "Too many attempts. Give it a minute and try again."),
    500: ("Something broke", "We've logged it. Try again in a moment."),
}


def error_page(request: Request, status: int, message: str = "",
               detail: str = "") -> str:
    title, blurb = _ERROR_COPY.get(status, ("Error", message or "Something went wrong."))
    trace = (
        f'<pre class="trace">{E(detail)}</pre>' if detail and not config.is_production else ""
    )
    content = f"""
<section class="section section--center">
  <p class="eyebrow">{status}</p>
  <h1 class="display display--l">{E(title)}</h1>
  <p class="lede">{E(message if status not in _ERROR_COPY else blurb)}</p>
  <p class="cluster">
    {button("Back to shop", href="/shop", variant="solid")}
    {button("Home", href="/", variant="ghost")}
  </p>
  {trace}
</section>
"""
    try:
        return layout(request, content, title=title, canonical="/")
    except Exception:       # a failure inside chrome must not mask the error
        return f"<!doctype html><title>{status}</title><h1>{E(title)}</h1><p>{E(blurb)}</p>"
