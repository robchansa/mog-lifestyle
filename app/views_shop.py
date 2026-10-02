"""Storefront: home, shop, product detail, content pages, media, contact."""
from __future__ import annotations

import sqlite3

from . import art, catalog, db, mailer
from .config import config
from .security import (
    HONEYPOT_FIELD, TIMESTAMP_FIELD, audit, rate_limit, spam_signals,
    valid_email,
)
from .ui import (
    E, badge, button, csrf_input, empty_state, field, icon, layout, money,
    page_header, pagination, product_grid, section_label, spam_trap,
)
from .web import HttpError, Request, Response, Router, html_response, json_response, redirect

router = Router()


# --------------------------------------------------------------- home

@router.get("/")
def home(request: Request) -> Response:
    marquee_items = [
        "Free US shipping over " + money(config.free_shipping_threshold_cents),
        "Built for the work", "Monochrome by design",
        "Ships in 1\u20132 business days", "Thirty-day returns",
    ]
    marquee = "".join(f"<span>{E(t)}</span>" for t in marquee_items * 2)

    bands = "".join(
        _department_band(slug, label)
        for slug, label in catalog.DEPARTMENTS
        if slug != "general"
    )
    everyone = _department_band(
        "general", "Everyone",
        lede="Black and white essentials that sit outside the split \u2014 "
             "worn by anyone, sized for everyone.",
    )
    sale = _sale_band()

    content = f"""
<section class="hero" data-hero>
  <div class="hero__media">
    <picture>
      <source media="(max-width: 700px)" srcset="/static/img/campaign-tall.jpg">
      <img src="/static/img/campaign-wide.jpg" alt=""
           width="2000" height="1125" fetchpriority="high" decoding="async">
    </picture>
  </div>
  <div class="hero__inner">
    <div class="hero__body">
      {section_label("Autumn 2026")}
      <h1 class="display hero__title">Built for<br>the work.</h1>
      <p class="hero__lede">Performance apparel and training essentials, cut in
        black and white for people who show up every day.</p>
      <p class="cluster">
        {button("Shop men", href="/shop?department=men", variant="solid")}
        {button("Shop women", href="/shop?department=women", variant="ghost")}
      </p>
    </div>
  </div>
  <div class="hero__scroll-cue" aria-hidden="true">Scroll<span></span></div>
</section>

<div class="page-body">
<div class="marquee" aria-hidden="true"><div class="marquee__track">{marquee}</div></div>

<div class="shell">
  {bands}
  {sale}
  {everyone}
</div>

<section class="shell feature-row section--rule">
  <div class="feature reveal">
    {icon("check", size=22)}
    <h3>Ships in 1\u20132 days</h3>
    <p>Orders placed before 2pm MT leave the same day. Free over
       {money(config.free_shipping_threshold_cents)}.</p>
  </div>
  <div class="feature reveal">
    {icon("arrow", size=22)}
    <h3>Thirty-day returns</h3>
    <p>Unworn, tags on, no questions. Return labels are prepaid inside the US.</p>
  </div>
  <div class="feature reveal">
    {icon("bag", size=22)}
    <h3>Made to be worn out</h3>
    <p>Heavyweight cotton and four-way stretch, tested through real training
       blocks before anything ships.</p>
  </div>
</section>
</div>
"""
    return html_response(layout(
        request, content, active="",
        description="MOG Lifestyle \u2014 performance apparel and training "
                    "essentials for high achievers. Men's, women's and "
                    "everyday monochrome, shipped from the USA.",
        json_ld={
            "@context": "https://schema.org",
            "@type": "Store",
            "name": "MOG Lifestyle",
            "url": config.base_url,
            "email": config.store_email,
            "telephone": config.store_phone,
            "image": config.url("/static/brand/og.jpg"),
            "logo": config.url("/static/brand/wordmark.png"),
            "sameAs": [url for _, url, _ in config.social_links],
        },
    ))


_BAND_COPY = {
    "men": "Oversized and regular-fit cotton, and the work shirt that goes "
           "over both.",
    "women": "Oversized cotton, cropped cuts, and training kit built to be "
             "trained in.",
}


def _category_chips(categories, *, department: str) -> str:
    chips = "".join(
        f'<a class="chip" href="/shop?collection={E(c["slug"])}">'
        f'{E(c["title"])}</a>'
        for c in categories
    )
    return f'<div class="band__categories">{chips}</div>' if chips else ""


def _department_band(slug: str, label: str, *, lede: str = "") -> str:
    products = catalog.for_department(slug, 4)
    if not products:
        return ""
    categories = [c for c in catalog.list_collections(slug) if c["product_count"]]
    return f"""
<section class="band reveal" id="{E(slug)}">
  <div class="band__head">
    <div>
      {section_label("Shop")}
      <h2 class="display band__title">{E(label)}</h2>
      <p class="lede">{E(lede or _BAND_COPY.get(slug, ""))}</p>
      {_category_chips(categories, department=slug)}
    </div>
    <div>
      {button(f"All {label.lower()}", href=f"/shop?department={slug}", variant="quiet")}
    </div>
  </div>
  {product_grid(products, eager_first=0)}
</section>
"""


def _sale_band() -> str:
    products = catalog.on_sale(4)
    if not products:
        return ""
    return f"""
<section class="band band--sale reveal" id="sale">
  <div class="band__head">
    <div>
      {section_label("Marked down")}
      <h2 class="display band__title">Sale</h2>
      <p class="lede">Last of the run, end of a colourway, or simply the final
         few. When they are gone they are gone.</p>
    </div>
    <div>{button("Shop all sale", href="/shop?on_sale=1", variant="solid")}</div>
  </div>
  {product_grid(products, eager_first=0)}
</section>
"""


# --------------------------------------------------------------- shop

@router.get("/shop")
def shop(request: Request) -> Response:
    filters = catalog.Filters.from_request(request)
    products, total = catalog.search(filters)
    pages = max(1, -(-total // catalog.PAGE_SIZE))
    collections = catalog.list_collections(filters.department)
    available = catalog.facets()

    def chip(label: str, href: str, active: bool) -> str:
        state = ' is-active" aria-current="true' if active else ""
        return f'<a class="chip{state}" href="{E(href)}">{E(label)}</a>'

    department_chips = chip(
        "Everything", f"/shop?{filters.query_string(department='', collection='')}",
        not filters.department and not filters.collection,
    ) + "".join(
        chip(label,
             f"/shop?{filters.query_string(department=slug, collection='')}",
             filters.department == slug)
        for slug, label in catalog.DEPARTMENTS
    )
    collection_chips = "".join(
        chip(f"{c['title']} ({c['product_count']})",
             f"/shop?{filters.query_string(collection=c['slug'])}",
             filters.collection == c["slug"])
        for c in collections if c["product_count"]
    )
    sale_chip = chip(
        f"On sale ({catalog.sale_count()})",
        f"/shop?{filters.query_string(on_sale='' if filters.on_sale else '1')}",
        filters.on_sale,
    )
    size_chips = "".join(
        chip(size,
             f"/shop?{filters.query_string(sizes=_toggle(filters.sizes, size))}",
             size in filters.sizes)
        for size in available["sizes"]
    )
    color_chips = "".join(
        chip(color,
             f"/shop?{filters.query_string(colors=_toggle(filters.colors, color))}",
             color in filters.colors)
        for color in available["colors"]
    )
    stock_chip = chip(
        "In stock only",
        f"/shop?{filters.query_string(in_stock='' if filters.in_stock else '1')}",
        filters.in_stock,
    )
    reset = (
        f'<p><a class="btn btn--quiet" href="/shop">Clear all filters</a></p>'
        if filters.active else ""
    )

    sort_options = "".join(
        f'<option value="{E(value)}"{" selected" if filters.sort == value else ""}>'
        f"{E(label)}</option>"
        for value, label in catalog.SORT_LABELS
    )
    hidden = "".join(
        f'<input type="hidden" name="{E(k)}" value="{E(v)}">'
        for k, v in _hidden_pairs(filters)
    )

    if products:
        results = product_grid(products)
    elif filters.q:
        results = empty_state(
            "No matches",
            f"Nothing matched “{filters.q}”. Try a broader term or clear your filters.",
            button("Clear filters", href="/shop", variant="ghost"),
        )
    else:
        results = empty_state(
            "Nothing here yet",
            "No products match these filters.",
            button("Clear filters", href="/shop", variant="ghost"),
        )

    base = f"/shop?{filters.query_string()}" if filters.query_string() else "/shop"
    heading, eyebrow = "Shop", "Collection"
    if filters.q:
        heading, eyebrow = f"“{filters.q}”", "Search"
    elif filters.on_sale:
        heading, eyebrow = "Sale", "Marked down"
    elif filters.collection:
        match = db.one(
            "SELECT title, department FROM collections WHERE slug = ?",
            (filters.collection,),
        )
        if match:
            heading = match["title"]
            eyebrow = catalog.DEPARTMENT_LABELS.get(match["department"], "Collection")
    elif filters.department:
        heading = catalog.DEPARTMENT_LABELS[filters.department]
        eyebrow = "Department"

    lede = "Everything in the line, in black and white."
    if filters.on_sale:
        lede = "Last of the run, end of a colourway, or simply the final few."
    elif filters.collection:
        # Category titles are plural, so "All <title> in the line" always reads.
        lede = f"All {heading.lower()} in the line."
    elif filters.department == "general":
        lede = "Pieces that sit outside the split \u2014 worn by anyone."
    elif filters.department:
        lede = _BAND_COPY.get(filters.department, lede)
    elif filters.q:
        lede = f"{total} result{'' if total == 1 else 's'} for your search."

    content = f"""
<div class="shell">
  {page_header(heading, eyebrow=eyebrow, lede=lede)}
  <div class="shop">
    <div>
      <button class="chip filters-toggle" type="button" data-filters-toggle
              aria-expanded="false">Filters</button>
      <aside class="filters" data-filters aria-label="Product filters">
        <div class="filters__group">
          <h2>Department</h2>
          <div class="filters__list">{department_chips}</div>
        </div>
        {f'<div class="filters__group"><h2>Category</h2><div class="filters__list">{collection_chips}</div></div>' if collection_chips else ''}
        {f'<div class="filters__group"><h2>Size</h2><div class="filters__list">{size_chips}</div></div>' if size_chips else ''}
        {f'<div class="filters__group"><h2>Colour</h2><div class="filters__list">{color_chips}</div></div>' if color_chips else ''}
        <div class="filters__group">
          <h2>Availability</h2>
          <div class="filters__list">{stock_chip}{sale_chip}</div>
        </div>
        {reset}
      </aside>
    </div>
    <div>
      <div class="shop__toolbar">
        <p class="shop__count">{total} product{"" if total == 1 else "s"}</p>
        <form class="shop__sort" action="/shop" method="get">
          {hidden}
          <label for="sort" class="visually-hidden">Sort by</label>
          <select id="sort" name="sort" data-autosubmit>{sort_options}</select>
          <noscript><button class="btn btn--small btn--ghost" type="submit">Sort</button></noscript>
        </form>
      </div>
      {results}
      {pagination(filters.page, pages, base)}
    </div>
  </div>
</div>
"""
    active = "sale" if filters.on_sale else (filters.department or "")
    return html_response(layout(
        request, content, title=heading, active=active,
        description=f"Shop {heading} — MOG Lifestyle performance apparel.",
    ))


def _toggle(values: tuple[str, ...], value: str) -> list[str]:
    return [v for v in values if v != value] if value in values else [*values, value]


def _hidden_pairs(filters: catalog.Filters) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if filters.q:
        pairs.append(("q", filters.q))
    if filters.department:
        pairs.append(("department", filters.department))
    if filters.collection:
        pairs.append(("collection", filters.collection))
    if filters.in_stock:
        pairs.append(("in_stock", "1"))
    if filters.on_sale:
        pairs.append(("on_sale", "1"))
    pairs.extend(("size", s) for s in filters.sizes)
    pairs.extend(("color", c) for c in filters.colors)
    return pairs


# ------------------------------------------------------------ product

@router.get("/product/<slug:slug>")
def product_detail(request: Request, slug: str) -> Response:
    product = catalog.get_product(slug)
    if product is None:
        raise HttpError(404, "That product isn't available.")
    variants = catalog.variants_for(product["id"])
    in_stock = any(v["available"] > 0 for v in variants)
    first_available = next((v for v in variants if v["available"] > 0), None)

    swatches = "".join(
        f"""
        <label class="swatch">
          <input type="radio" name="variant_id" value="{v['id']}"
                 data-price="{money(v['effective_cents'])}"
                 data-stock="{v['available']}"
                 {'checked' if first_available and v['id'] == first_available['id'] else ''}
                 {'disabled' if v['available'] <= 0 else ''}>
          <span>{E(v['size'] or v['color'] or v['sku'])}</span>
        </label>
        """
        for v in variants
    )
    details = "".join(
        f"<li>{E(line)}</li>"
        for line in (product["details"] or "").splitlines() if line.strip()
    )
    compare = product["compare_cents"]
    price_block = (
        f'<s class="price__was">{money(compare)}</s>' if compare else ""
    ) + f'<span data-variant-price>{money(product["price_cents"])}</span>'

    images = catalog.image_list(product)
    view_names = ["front", "back", "detail"]
    gallery = "".join(
        f'''<figure class="pdp__figure">
        <img src="{E(src)}" alt="{E(product['title'])}{'' if not index else ' — ' + view_names[min(index, 2)] + ' view'}"
             width="800" height="1000"
             {'fetchpriority="high"' if not index else 'loading="lazy"'} decoding="async">
      </figure>'''
        for index, src in enumerate(images)
    )

    related = catalog.related(product, 4)
    flags = []
    if compare:
        flags.append(badge("Sale", "sale"))
    if not in_stock:
        flags.append(badge("Sold out", "muted"))

    content = f"""
<div class="shell">
  <nav class="eyebrow" aria-label="Breadcrumb" style="padding-top:2rem">
    <a href="/shop">Shop</a> / {E(product['collection_title'] or 'All')} / {E(product['title'])}
  </nav>
  <div class="pdp">
    <div class="pdp__gallery">{gallery}</div>
    <div class="pdp__info">
      <div class="cluster">{"".join(flags)}</div>
      {section_label(product['collection_title'] or 'MOG Lifestyle')}
      <h1 class="display pdp__title">{E(product['title'])}</h1>
      <p class="muted">{E(product['subtitle'])}</p>
      <p class="pdp__price">{price_block}</p>
      <p class="pdp__desc">{E(product['description'])}</p>

      <form method="post" action="/cart/add" data-add-to-cart data-variant-form>
        {csrf_input(request)}
        <fieldset style="border:0;padding:0;margin:0">
          <legend class="eyebrow" style="margin-bottom:0">Size</legend>
          <div class="swatches">{swatches or '<p class="muted">One size</p>'}</div>
        </fieldset>
        <div class="cluster">
          <div class="qty" style="margin-top:0">
            <button type="button" data-qty="down" aria-label="Decrease quantity">
              {icon("minus", size=14)}</button>
            <label class="visually-hidden" for="quantity">Quantity</label>
            <input id="quantity" type="number" name="quantity" value="1" min="1"
                   max="{first_available['available'] if first_available else 1}"
                   inputmode="numeric">
            <button type="button" data-qty="up" aria-label="Increase quantity">
              {icon("plus", size=14)}</button>
          </div>
          <button class="btn btn--solid" type="submit" style="flex:1"
                  {'disabled' if not in_stock else ''}>
            {'Sold out' if not in_stock else 'Add to bag'}
          </button>
        </div>
        <p class="stock-note" data-variant-note role="status" aria-live="polite"></p>
      </form>

      <div class="accordion">
        <details open>
          <summary>Details</summary>
          <div class="accordion__body">
            <ul class="prose">{details or '<li>Considered construction, built to last.</li>'}</ul>
          </div>
        </details>
        <details>
          <summary>Shipping &amp; returns</summary>
          <div class="accordion__body">
            <p>Ships in 1–2 business days from the USA. Free over
               {money(config.free_shipping_threshold_cents)}; otherwise
               {money(config.shipping_flat_cents)} flat. Thirty-day returns on
               unworn items with tags attached.</p>
          </div>
        </details>
        <details>
          <summary>Care</summary>
          <div class="accordion__body">
            <p>Machine wash cold, inside out, with like colours. Tumble dry low or
               hang. Do not bleach. Do not iron prints.</p>
          </div>
        </details>
      </div>
    </div>
  </div>

  {f'<section class="section section--rule"><h2 class="display display--xs" style="margin-bottom:2rem">You may also like</h2>{product_grid(related)}</section>' if related else ''}
</div>
"""
    return html_response(layout(
        request, content, title=product["title"], active="shop",
        description=product["subtitle"] or product["description"][:155],
        og_image=config.url(catalog.primary_image(product)),
        canonical=f"/product/{product['slug']}",
        json_ld={
            "@context": "https://schema.org",
            "@type": "Product",
            "name": product["title"],
            "description": product["description"],
            "image": config.url(catalog.primary_image(product)),
            "brand": {"@type": "Brand", "name": "MOG Lifestyle"},
            "offers": {
                "@type": "Offer",
                "price": f"{product['price_cents'] / 100:.2f}",
                "priceCurrency": config.currency.upper(),
                "availability": "https://schema.org/InStock" if in_stock
                                else "https://schema.org/OutOfStock",
                "url": config.url(f"/product/{product['slug']}"),
            },
        },
    ))


# ---------------------------------------------------------------- media

@router.get("/media/<str:name>.svg")
def media(request: Request, name: str) -> Response:
    """Generated product artwork.  Deterministic, so it caches forever."""
    svg = art.render(name)
    return _svg_response(request, svg, f"{name}:{art.ART_VERSION}")


def _svg_response(request: Request, svg: str, cache_key: str) -> Response:
    import hashlib
    etag = 'W/"%s"' % hashlib.sha1(cache_key.encode()).hexdigest()[:12]
    if request.headers.get("If-None-Match") == etag:
        return Response(b"", status=304, headers={"ETag": etag})
    return Response(
        svg, content_type="image/svg+xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=31536000, immutable", "ETag": etag},
    )


# --------------------------------------------------------- content pages

@router.get("/about")
def about(request: Request) -> Response:
    content = f"""
<div class="shell">
  {page_header("Built for the work", eyebrow="About",
               lede="MOG Lifestyle makes apparel for people who treat training "
                    "as a standard, not a hobby.")}
  <div class="editorial section">
    <div class="editorial__media">
      <img src="/static/img/campaign-tall.jpg" alt="" width="1080" height="1920" loading="lazy">
    </div>
    <div class="prose">
      <p>We started with one problem: training gear that either falls apart or
         announces itself. We wanted neither. So we cut everything in black and
         white, kept the branding to a whisper, and spent the budget on fabric.</p>
      <h2>What we make</h2>
      <p>Heavyweight jersey that holds its shape. Four-way stretch that moves in
         every direction you do. Hardware that survives the gym bag. Every piece
         is tested through a full training block before it goes on sale.</p>
      <h2>Who it's for</h2>
      <p>High achievers and people who train like it matters — whether that is a
         5am lift, a long run, or the walk between the two. Our customers are
         across the United States and range from sixteen to eighty-five.</p>
      <h2>How we work</h2>
      <p>Small drops. Honest stock counts. If it says three left, there are three
         left. We would rather sell out than oversell.</p>
    </div>
  </div>
</div>
"""
    return html_response(layout(request, content, title="About", active="about"))


_POLICY_PAGES = {
    "shipping": ("Shipping & returns", "Policies", [
        ("Shipping", "Orders placed before 2pm Mountain Time ship the same "
                     "business day. Standard delivery runs 2–5 business days "
                     "inside the continental United States."),
        ("Rates", "Flat $8 anywhere in the US. Free on orders over $150."),
        ("Returns", "Thirty days from delivery on unworn items with tags "
                    "attached. Start a return from your account, or email us "
                    "and we will send a prepaid label."),
        ("Exchanges", "Exchange for a different size at no cost, once per "
                      "order, while stock lasts."),
        ("Damaged or wrong items", "Email us within seven days with a photo and "
                                   "we will replace it immediately."),
    ]),
    "faq": ("Frequently asked", "Support", [
        ("How do your sizes run?", "True to size with a relaxed shoulder. If you "
                                   "are between sizes and want a closer cut, size down."),
        ("When do you restock?", "Core pieces restock monthly. Join the list to "
                                 "hear first — restocks are announced there before anywhere else."),
        ("Do you ship internationally?", "Not yet. We ship across the United "
                                         "States today; international is next."),
        ("How do I track my order?", "Tracking is emailed when your order ships, "
                                     "and every order lives in your account."),
        ("What payment methods do you take?", "All major cards, Apple Pay and "
                                              "Google Pay, processed securely by Stripe. "
                                              "We never see or store your card details."),
    ]),
    "privacy": ("Privacy", "Legal", [
        ("What we collect", "Your name, email, shipping address and order "
                            "history. That is all we need to get a package to you."),
        ("Payments", "Card details are entered on Stripe's own checkout and "
                     "never touch our servers. We store only a payment reference."),
        ("Email", "Transactional email is always sent. Marketing email is sent "
                  "only if you opted in, and every message can unsubscribe you."),
        ("Cookies", "Two: one signed session cookie and one signed cart cookie. "
                    "No third-party tracking, no advertising pixels."),
        ("Your rights", f"Email {config.store_email} to see, correct or delete "
                        f"the data we hold about you. We respond within thirty days."),
    ]),
    "terms": ("Terms", "Legal", [
        ("Orders", "An order is an offer to buy. We accept it when we take "
                   "payment and email your confirmation."),
        ("Pricing", "Prices are in US dollars and include no sales tax unless "
                    "shown at checkout. We correct pricing errors before charging you."),
        ("Stock", "Stock counts are live. If an item sells out between your "
                  "adding it and paying, we cancel that line and refund it in full."),
        ("Liability", "Our liability for any order is limited to the amount you "
                      "paid for it."),
        ("Contact", f"{config.store_email} · {config.store_phone}"),
    ]),
}


def _render_policy(request: Request, page: str) -> Response:
    title, eyebrow, sections = _POLICY_PAGES[page]
    body = "".join(
        f"<h2>{E(heading)}</h2><p>{E(text)}</p>" for heading, text in sections
    )
    content = f"""
<div class="shell">
  {page_header(title, eyebrow=eyebrow)}
  <div class="prose section" style="padding-top:0">{body}</div>
</div>
"""
    return html_response(layout(request, content, title=title))


def _policy_view(page: str):
    """Bind one policy page to its own handler; no greedy catch-all route."""
    def view(request: Request) -> Response:
        return _render_policy(request, page)
    view.__name__ = f"policy_{page}"
    return view


for _page in _POLICY_PAGES:
    router.add(f"/{_page}", _policy_view(_page), ("GET",))


# -------------------------------------------------------------- contact

@router.get("/contact")
def contact_form(request: Request, errors: dict | None = None,
                 values: dict | None = None) -> Response:
    errors = errors or {}
    values = values or {}
    user = request.user
    content = f"""
<div class="shell">
  {page_header("Contact", eyebrow="Say hello",
               lede="Questions about sizing, an order, or a wholesale enquiry — "
                    "we reply within one business day.")}
  <div class="split section" style="padding-top:0">
    <form method="post" action="/contact" class="stack">
      {csrf_input(request)}
      {spam_trap()}
      {f'<div class="form-error">{E(errors["_"])}</div>' if errors.get("_") else ""}
      <div class="field-row">
        {field("name", "Your name", required=True, autocomplete="name",
               value=values.get("name", (user["name"] if user else "")),
               error=errors.get("name", ""))}
        {field("email", "Email", type_="email", required=True, autocomplete="email",
               value=values.get("email", (user["email"] if user else "")),
               error=errors.get("email", ""))}
      </div>
      {field("subject", "Subject", value=values.get("subject", ""),
             error=errors.get("subject", ""))}
      {field("message", "Message", rows=7, required=True,
             value=values.get("message", ""), error=errors.get("message", ""))}
      {button("Send message", type_="submit", variant="solid")}
    </form>
    <aside class="prose">
      <h2>Direct</h2>
      <p><a href="mailto:{E(config.store_email)}">{E(config.store_email)}</a><br>
         {E(config.store_phone)}</p>
      <h2>Order support</h2>
      <p>Have your order number ready — it looks like MOG-4021 and is in your
         confirmation email. You can also see every order in
         <a href="/account/orders">your account</a>.</p>
      <h2>Hours</h2>
      <p>Monday to Friday, 9am–5pm Mountain Time.</p>
    </aside>
  </div>
</div>
"""
    return html_response(layout(request, content, title="Contact", active="contact"))


@router.post("/contact")
def contact_submit(request: Request) -> Response:
    name = request.get("name")[:120]
    email = request.get("email")[:254]
    subject = request.get("subject")[:160]
    message = request.get("message")[:4000]

    errors: dict[str, str] = {}
    if not name:
        errors["name"] = "Tell us who you are."
    if not valid_email(email):
        errors["email"] = "Enter a valid email address."
    if len(message.strip()) < 10:
        errors["message"] = "A little more detail will help us help you."
    if not rate_limit(f"contact:{request.remote_addr}", limit=5, per_seconds=600):
        errors["_"] = "Too many messages from this connection. Try again shortly."

    signal = spam_signals(request.get(HONEYPOT_FIELD),
                          request.get_raw(TIMESTAMP_FIELD))
    if signal:
        # Accept it silently: telling a bot why it failed only helps it adapt,
        # and a real visitor who somehow trips this still sees success.
        audit("spam.blocked", actor=email or "anonymous", subject="contact",
              detail=signal, ip=request.remote_addr)
        return redirect("/contact", flash="Message sent — we'll be in touch shortly.")

    if errors:
        return contact_form(request, errors,
                            {"name": name, "email": email, "subject": subject,
                             "message": message})

    from . import db as database
    with database.tx():
        database.insert("messages", name=name, email=email, subject=subject,
                        body=message)
    try:
        mailer.contact_receipt(name, email, subject, message)
    except Exception:                                          # noqa: BLE001
        pass
    return redirect("/contact", flash="Message sent — we'll be in touch shortly.")


# ----------------------------------------------------------- newsletter

@router.post("/newsletter")
def newsletter(request: Request) -> Response:
    email = request.get("email")[:254]
    if not valid_email(email):
        message, ok = "Enter a valid email address.", False
    elif not rate_limit(f"newsletter:{request.remote_addr}", limit=6, per_seconds=600):
        message, ok = "Too many attempts. Try again shortly.", False
    elif spam_signals(request.get(HONEYPOT_FIELD), request.get_raw(TIMESTAMP_FIELD)):
        audit("spam.blocked", actor=email, subject="newsletter",
              detail="trap", ip=request.remote_addr)
        message, ok = "You're on the list.", True        # silent to the bot
    else:
        from . import db as database
        with database.tx():
            database.execute(
                "INSERT OR IGNORE INTO newsletter (email, source) VALUES (?, 'footer')",
                (email,),
            )
        message, ok = "You're on the list.", True

    if request.wants_json:
        return json_response({"ok": ok, "message": message})
    return redirect(request.headers.get("Referer", "/"), flash=message,
                    tone="ok" if ok else "error")
