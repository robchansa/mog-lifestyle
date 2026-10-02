"""The operations console.

The client answered "No — HesMartech can manage updates" on the CMS question,
so this is the surface the operator uses: orders, fulfilment, catalogue,
inventory, customers, promotions, messages and an audit trail.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from . import accounts, analytics, catalog, db, discounts, mailer, orders
from .config import config
from .security import audit, slugify
from .ui import (
    E, admin_layout, badge, button, csrf_input, field, money, table,
)
from .web import (
    HttpError, Request, Response, Router, html_response, redirect, Response as Resp,
)

router = Router()


def require_staff(request: Request):
    user = request.user
    if user is None or user["role"] not in ("staff", "admin"):
        raise HttpError(403, "Staff access only.")
    return user


def _actor(request: Request) -> str:
    return request.user["email"] if request.user else "anonymous"


# ---------------------------------------------------------------- overview

@router.get("/admin")
def dashboard(request: Request) -> Response:
    require_staff(request)
    stats = orders.metrics(30)
    series = orders.revenue_series(14)
    peak = max((cents for _, cents in series), default=0) or 1
    bars = "".join(
        f'<span style="height:{max(2, round(100 * cents / peak))}%" '
        f'title="{E(day)}: {money(cents)}"></span>'
        for day, cents in series
    )
    sellers = orders.best_sellers(5)
    pending_orders = orders.recent(8, status="paid")
    low = catalog.low_stock()[:8]

    stat_cards = "".join(
        f'<div class="stat"><span class="stat__label">{E(label)}</span>'
        f'<span class="stat__value">{E(value)}</span>'
        f'<span class="stat__note">{E(note)}</span></div>'
        for label, value, note in [
            ("Revenue", money(stats["revenue_cents"]), "last 30 days"),
            ("Orders", stats["orders"], "last 30 days"),
            ("Average order", money(stats["aov_cents"]), "last 30 days"),
            ("To ship", stats["awaiting_fulfilment"], "paid, not yet shipped"),
            ("Customers", stats["customers"], f"{stats['subscribers']} subscribers"),
            ("Low stock", stats["low_stock"], "variants at or below threshold"),
        ]
    )

    seller_rows = [
        [f'<a href="/product/{E(s["slug"])}">{E(s["title"])}</a>',
         f'<span class="num">{s["units"]}</span>',
         f'<span class="num">{money(s["cents"])}</span>']
        for s in sellers
    ]
    ship_rows = [
        [f'<a href="/admin/orders/{o["id"]}">{E(o["number"])}</a>',
         E(o["ship_name"]), f'<span class="num">{money(o["total_cents"])}</span>',
         E(o["created_at"][:16])]
        for o in pending_orders
    ]
    low_rows = [
        [E(v["title"]), E(v["sku"]),
         f'<span class="num">{v["available"]}</span>']
        for v in low
    ]

    alerts = []
    if stats["pending"]:
        alerts.append(
            f'{stats["pending"]} pending order(s) still holding stock — these '
            f'auto-release after {orders.RESERVATION_MINUTES} minutes.'
        )
    if stats["unread_messages"]:
        alerts.append(f'{stats["unread_messages"]} unread customer message(s).')
    if not config.payments_live:
        alerts.append("Stripe is in demo mode — set STRIPE_SECRET_KEY to take real payments.")
    if not config.email_live:
        alerts.append("Email is in console mode — set SMTP_HOST to deliver mail.")
    alert_html = "".join(f"<li>{E(a)}</li>" for a in alerts)

    content = f"""
<div class="stat-row">{stat_cards}</div>

{f'<div class="panel"><div class="panel__head"><h2>Needs attention</h2></div>'
 f'<div class="panel__body"><ul class="prose">{alert_html}</ul></div></div>' if alerts else ''}

<div class="panel">
  <div class="panel__head">
    <h2>Revenue · last 14 days</h2>
    <span class="muted">{money(sum(c for _, c in series))} total</span>
  </div>
  <div class="panel__body"><div class="sparkline">{bars}</div></div>
</div>

<div class="split">
  <div class="panel">
    <div class="panel__head"><h2>Awaiting fulfilment</h2>
      <a class="btn btn--quiet btn--small" href="/admin/orders?status=paid">All</a></div>
    {table(["Order", "Customer", "Total", "Placed"], ship_rows,
           empty="Nothing waiting to ship.")}
  </div>
  <div class="panel">
    <div class="panel__head"><h2>Best sellers</h2></div>
    {table(["Product", "Units", "Revenue"], seller_rows,
           empty="No sales yet.")}
  </div>
</div>

<div class="panel">
  <div class="panel__head"><h2>Low stock</h2>
    <a class="btn btn--quiet btn--small" href="/admin/inventory">Manage inventory</a></div>
  {table(["Product", "SKU", "Available"], low_rows, empty="Everything is well stocked.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Overview", active="/admin"))


# ------------------------------------------------------------------ orders

@router.get("/admin/orders")
def admin_orders(request: Request) -> Response:
    require_staff(request)
    status = request.get("status")
    search = request.get("q")
    rows = orders.recent(200, status=status, search=search)

    status_links = "".join(
        f'<a class="chip{" is-active" if status == value else ""}" '
        f'href="/admin/orders{"?status=" + value if value else ""}">{E(label)}</a>'
        for value, label in [("", "All"), ("pending", "Pending"), ("paid", "Paid"),
                             ("fulfilled", "Shipped"), ("cancelled", "Cancelled"),
                             ("refunded", "Refunded")]
    )
    order_rows = [
        [f'<a href="/admin/orders/{o["id"]}">{E(o["number"])}</a>',
         E(o["created_at"][:16]),
         f'{E(o["ship_name"])}<br><span class="muted">{E(o["email"])}</span>',
         badge(orders.STATUS_LABELS[o["status"]], o["status"]),
         f'<span class="num">{o["item_count"]}</span>',
         f'<span class="num">{money(o["total_cents"])}</span>']
        for o in rows
    ]
    content = f"""
<form class="filter-bar" method="get" action="/admin/orders">
  {status_links}
  <input name="q" value="{E(search)}" placeholder="Order, name or email">
  <button class="btn btn--small btn--ghost" type="submit">Search</button>
  <a class="btn btn--small btn--quiet" href="/admin/orders/export{'?status=' + E(status) if status else ''}">
    Export CSV</a>
</form>
<div class="panel">
  {table(["Order", "Placed", "Customer", "Status", "Items", "Total"], order_rows,
         empty="No orders match that filter.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Orders",
                                      active="/admin/orders"))


@router.get("/admin/orders/export")
def admin_orders_export(request: Request) -> Response:
    require_staff(request)
    rows = orders.recent(5000, status=request.get("status"))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "number", "created_at", "status", "email", "name", "city", "state",
        "postal", "items", "subtotal", "discount", "shipping", "tax", "total",
        "discount_code", "carrier", "tracking",
    ])
    for o in rows:
        writer.writerow([
            o["number"], o["created_at"], o["status"], o["email"], o["ship_name"],
            o["ship_city"], o["ship_region"], o["ship_postal"], o["item_count"],
            f"{o['subtotal_cents'] / 100:.2f}", f"{o['discount_cents'] / 100:.2f}",
            f"{o['shipping_cents'] / 100:.2f}", f"{o['tax_cents'] / 100:.2f}",
            f"{o['total_cents'] / 100:.2f}", o["discount_code"],
            o["tracking_carrier"], o["tracking_number"],
        ])
    audit("orders.export", actor=_actor(request), detail=f"{len(rows)} rows")
    return Resp(
        buffer.getvalue(), content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="mog-orders.csv"'},
    )


@router.get("/admin/orders/<int:order_id>")
def admin_order_detail(request: Request, order_id: int) -> Response:
    require_staff(request)
    order = orders.get(order_id)
    if order is None:
        raise HttpError(404, "Order not found.")
    items = orders.items_for(order_id)

    item_rows = [
        [f'<a href="/product/{E(i["slug"])}">{E(i["title"])}</a>'
         f'<br><span class="muted">{E(i["variant_label"])}</span>',
         E(i["sku"]), f'<span class="num">{i["quantity"]}</span>',
         f'<span class="num">{money(i["unit_cents"])}</span>',
         f'<span class="num">{money(i["unit_cents"] * i["quantity"])}</span>']
        for i in items
    ]

    actions = ""
    if order["status"] == "paid":
        actions = f"""
<form method="post" action="/admin/orders/{order_id}/fulfil" class="admin-form">
  {csrf_input(request)}
  {field("carrier", "Carrier", value=order["tracking_carrier"] or "USPS",
         options=[(c, c) for c in ["USPS", "UPS", "FedEx", "DHL", "Other"]])}
  {field("tracking", "Tracking number", value=order["tracking_number"])}
  <div class="field field--wide">
    {button("Mark shipped & email customer", type_="submit", variant="solid")}
  </div>
</form>
"""
    elif order["status"] == "pending":
        actions = f"""
<form method="post" action="/admin/orders/{order_id}/cancel"
      data-confirm="Cancel this order and release its stock?">
  {csrf_input(request)}
  {button("Cancel order", type_="submit", variant="ghost")}
</form>
"""

    refund_form = ""
    if order["status"] in ("paid", "fulfilled"):
        refund_form = f"""
<form method="post" action="/admin/orders/{order_id}/refund"
      data-confirm="Refund {money(order['total_cents'])} and restock these items?">
  {csrf_input(request)}
  {button("Refund order", type_="submit", variant="ghost")}
</form>
"""

    content = f"""
<div class="split">
  <div class="panel">
    <div class="panel__head">
      <h2>{E(order['number'])}</h2>
      {badge(orders.STATUS_LABELS[order['status']], order['status'])}
    </div>
    {table(["Item", "SKU", "Qty", "Unit", "Total"], item_rows)}
    <div class="panel__body">
      <div class="summary__row"><span>Subtotal</span>
        <span>{money(order['subtotal_cents'])}</span></div>
      {f'''<div class="summary__row"><span>Discount {E(order['discount_code'])}</span>
           <span>-{money(order['discount_cents'])}</span></div>''' if order['discount_cents'] else ''}
      <div class="summary__row"><span>Shipping</span>
        <span>{money(order['shipping_cents'])}</span></div>
      {f'''<div class="summary__row"><span>Tax</span>
           <span>{money(order['tax_cents'])}</span></div>''' if order['tax_cents'] else ''}
      <div class="summary__row summary__total"><span>Total</span>
        <span>{money(order['total_cents'])}</span></div>
    </div>
  </div>

  <div>
    <div class="panel" style="margin-bottom:1.5rem">
      <div class="panel__head"><h2>Customer</h2></div>
      <div class="panel__body">
        <p><strong>{E(order['ship_name'])}</strong><br>
          <a href="mailto:{E(order['email'])}">{E(order['email'])}</a><br>
          {E(order['ship_phone'] or '—')}</p>
        <p style="margin-top:1rem">{E(order['ship_line1'])}
          {(' ' + E(order['ship_line2'])) if order['ship_line2'] else ''}<br>
          {E(order['ship_city'])}, {E(order['ship_region'])} {E(order['ship_postal'])}<br>
          {E(order['ship_country'])}</p>
        {f'<p class="form-note" style="margin-top:1rem">{E(order["notes"])}</p>' if order["notes"] else ''}
      </div>
    </div>

    <div class="panel" style="margin-bottom:1.5rem">
      <div class="panel__head"><h2>Fulfilment</h2></div>
      <div class="panel__body">
        {actions or '<p class="muted">No actions available for this status.</p>'}
        {f'<p class="muted" style="margin-top:1rem">Shipped {E(order["fulfilled_at"] or "")} · {E(order["tracking_carrier"])} {E(order["tracking_number"])}</p>' if order["status"] == "fulfilled" else ''}
      </div>
    </div>

    <div class="panel">
      <div class="panel__head"><h2>Payment</h2></div>
      <div class="panel__body">
        <p class="muted">Reference<br><code>{E(order['payment_ref'] or '—')}</code></p>
        <p class="muted" style="margin-top:.6rem">Intent<br>
          <code>{E(order['payment_intent'] or '—')}</code></p>
        <div style="margin-top:1rem">{refund_form}</div>
      </div>
    </div>
  </div>
</div>
<p style="margin-top:1rem">
  <button class="btn btn--quiet btn--small" type="button" data-print>Print packing slip</button>
</p>
"""
    return html_response(admin_layout(
        request, content, title=f"Order {order['number']}", active="/admin/orders"
    ))


@router.post("/admin/orders/<int:order_id>/fulfil")
def admin_order_fulfil(request: Request, order_id: int) -> Response:
    require_staff(request)
    order = orders.get(order_id)
    if order is None:
        raise HttpError(404, "Order not found.")
    try:
        orders.fulfil(order, carrier=request.get("carrier"),
                      tracking=request.get("tracking"), actor=_actor(request))
    except orders.CheckoutError as exc:
        return redirect(f"/admin/orders/{order_id}", flash=str(exc), tone="error")
    return redirect(f"/admin/orders/{order_id}",
                    flash="Marked shipped — customer notified.")


@router.post("/admin/orders/<int:order_id>/cancel")
def admin_order_cancel(request: Request, order_id: int) -> Response:
    require_staff(request)
    order = orders.get(order_id)
    if order is None:
        raise HttpError(404, "Order not found.")
    orders.cancel(order, reason="[cancelled by staff]", actor=_actor(request))
    return redirect(f"/admin/orders/{order_id}", flash="Order cancelled, stock released.")


@router.post("/admin/orders/<int:order_id>/refund")
def admin_order_refund(request: Request, order_id: int) -> Response:
    require_staff(request)
    order = orders.get(order_id)
    if order is None:
        raise HttpError(404, "Order not found.")
    try:
        orders.refund(order, actor=_actor(request))
    except orders.CheckoutError as exc:
        return redirect(f"/admin/orders/{order_id}", flash=str(exc), tone="error")
    return redirect(f"/admin/orders/{order_id}", flash="Refunded and restocked.")


# ---------------------------------------------------------------- products

@router.get("/admin/products")
def admin_products(request: Request) -> Response:
    require_staff(request)
    filters = catalog.Filters.from_request(request)
    rows, total = catalog.search(filters, page_size=200, include_drafts=True)
    product_rows = [
        [f'<a href="/admin/products/{p["id"]}">{E(p["title"])}</a>'
         f'<br><span class="muted">{E(p["slug"])}</span>',
         E(p["collection_title"] or "—"),
         badge(p["status"].title(), "paid" if p["status"] == "active" else "muted"),
         f'<span class="num">{money(p["price_cents"])}</span>',
         f'<span class="num">{p["stock"]}</span>',
         f'<a class="btn btn--quiet btn--small" href="/product/{E(p["slug"])}">View</a>']
        for p in rows
    ]
    content = f"""
<form class="filter-bar" method="get" action="/admin/products">
  <input name="q" value="{E(filters.q)}" placeholder="Search products">
  <button class="btn btn--small btn--ghost" type="submit">Search</button>
</form>
<div class="panel">
  <div class="panel__head"><h2>{total} product(s)</h2>
    <a class="btn btn--small btn--solid" href="/admin/products/new">New product</a></div>
  {table(["Product", "Collection", "Status", "Price", "Stock", ""], product_rows,
         empty="No products yet.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Products",
                                      active="/admin/products"))


def _product_form(request: Request, product: Any = None,
                  error: str = "") -> Response:
    collections = catalog.list_collections()
    is_new = product is None
    variants = catalog.variants_for(product["id"]) if product else []
    variant_text = "\n".join(
        f"{v['sku']} | {v['size']} | {v['color']} | {v['stock']}" for v in variants
    )
    from .art import SHAPES, VARIATIONS
    seeds = [f"{shape}-{i}" for shape in SHAPES for i in range(len(VARIATIONS))]

    def value(key: str, fallback: Any = "") -> Any:
        if product is not None:
            try:
                return product[key]
            except (KeyError, IndexError):
                return fallback
        return fallback

    previews = catalog.image_list(product) if product is not None else []
    photo_preview = ""
    if previews:
        thumbs = "".join(
            f'<img src="{E(src)}" alt="" width="80" height="100" loading="lazy" '
            f'style="width:80px;height:100px;object-fit:cover;'
            f'border:1px solid var(--hairline)">'
            for src in previews
        )
        photo_preview = (
            f'<div class="field field--wide"><label>Current images</label>'
            f'<div class="cluster">{thumbs}</div></div>'
        )

    content = f"""
<form method="post" action="{'/admin/products/new' if is_new else f'/admin/products/{product["id"]}'}">
  {csrf_input(request)}
  {f'<div class="form-error">{E(error)}</div>' if error else ''}
  <div class="panel">
    <div class="panel__head"><h2>Details</h2></div>
    <div class="panel__body admin-form">
      <div class="field--wide">
        {field("title", "Title", required=True, value=value("title"))}
      </div>
      <div class="field--wide">
        {field("subtitle", "Subtitle", value=value("subtitle"),
               hint="The short line under the product name.")}
      </div>
      <div class="field--wide">
        {field("description", "Description", rows=4, value=value("description"))}
      </div>
      <div class="field--wide">
        {field("details", "Details", rows=5, value=value("details"),
               hint="One bullet per line.")}
      </div>
      <div class="field--wide">
        {field("images", "Photography", rows=3, value=value("images"),
               hint="One image path per line, e.g. /static/img/mog-tee-front.jpg "
                    "— the first is the card image. Leave blank to use the "
                    "fallback artwork. Build new files with "
                    "tools/build_assets.py.")}
      </div>
      {photo_preview}
      {field("price_cents", "Price (cents)", type_="number",
             value=str(value("price_cents", 0)), required=True, min="0")}
      {field("compare_cents", "Compare-at price (cents)", type_="number",
             value=str(value("compare_cents") or ""), min="0",
             hint="Leave blank for no sale price.")}
      {field("collection_id", "Collection", value=str(value("collection_id") or ""),
             options=[("", "None")] + [(str(c["id"]), c["title"]) for c in collections])}
      {field("status", "Status", value=value("status", "active"),
             options=[("active", "Active"), ("draft", "Draft"), ("archived", "Archived")])}
      {field("art_seed", "Fallback artwork", value=value("art_seed", "tee-0"),
             options=[(s, s) for s in seeds],
             hint="Used only when no photography is set below.")}
      {field("position", "Sort position", type_="number",
             value=str(value("position", 0)))}
      <label class="cluster" style="font-size:.8rem;color:var(--muted)">
        <input type="checkbox" name="featured" value="1"
               {'checked' if value("featured") else ''} style="width:auto;margin:0">
        <span>Feature on the homepage</span>
      </label>
    </div>
  </div>

  <div class="panel" style="margin-top:1.5rem">
    <div class="panel__head"><h2>Variants</h2></div>
    <div class="panel__body">
      {field("variants", "One per line: SKU | size | colour | stock", rows=8,
             value=variant_text,
             hint="Existing SKUs keep their reservations; removed SKUs are deleted "
                  "only when nothing is reserved against them.")}
    </div>
  </div>

  <p style="margin-top:1.5rem" class="cluster">
    {button("Save product", type_="submit", variant="solid")}
    <a class="btn btn--quiet" href="/admin/products">Cancel</a>
  </p>
</form>
"""
    return html_response(admin_layout(
        request, content,
        title="New product" if is_new else f"Edit · {product['title']}",
        active="/admin/products",
    ))


@router.get("/admin/products/new")
def admin_product_new(request: Request) -> Response:
    require_staff(request)
    return _product_form(request)


@router.get("/admin/products/<int:product_id>")
def admin_product_edit(request: Request, product_id: int) -> Response:
    require_staff(request)
    product = catalog.get_product_by_id(product_id)
    if product is None:
        raise HttpError(404, "Product not found.")
    return _product_form(request, product)


def _read_product_fields(request: Request) -> dict[str, Any]:
    compare = request.get("compare_cents")
    collection = request.get("collection_id")
    return {
        "title": request.get("title")[:160],
        "subtitle": request.get("subtitle")[:160],
        "description": request.get("description")[:4000],
        "details": request.get("details")[:2000],
        "price_cents": max(0, request.get_int("price_cents")),
        "compare_cents": int(compare) if compare.isdigit() else None,
        "collection_id": int(collection) if collection.isdigit() else None,
        "status": request.get("status", "active"),
        "art_seed": request.get("art_seed", "tee-0"),
        "images": "\n".join(
            line.strip() for line in request.get_raw("images").splitlines()
            if line.strip()
        )[:2000],
        "position": request.get_int("position"),
        "featured": 1 if request.checked("featured") else 0,
    }


def _parse_variants(raw: str) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("|")]
        parts += [""] * (4 - len(parts))
        sku, size, color, stock = parts[:4]
        if not sku:
            continue
        parsed.append({
            "sku": sku[:60], "size": size.upper()[:8], "color": color[:40],
            "stock": int(stock) if stock.isdigit() else 0,
        })
    return parsed


@router.post("/admin/products/new")
def admin_product_create(request: Request) -> Response:
    require_staff(request)
    fields = _read_product_fields(request)
    if not fields["title"]:
        return _product_form(request, None, "A title is required.")
    fields["slug"] = catalog.unique_slug(fields["title"])
    product_id = catalog.create_product(**fields)
    catalog.upsert_variants(product_id, _parse_variants(request.get_raw("variants")))
    audit("product.create", actor=_actor(request), subject=fields["slug"])
    return redirect(f"/admin/products/{product_id}", flash="Product created.")


@router.post("/admin/products/<int:product_id>")
def admin_product_update(request: Request, product_id: int) -> Response:
    require_staff(request)
    product = catalog.get_product_by_id(product_id)
    if product is None:
        raise HttpError(404, "Product not found.")
    fields = _read_product_fields(request)
    if not fields["title"]:
        return _product_form(request, product, "A title is required.")
    fields["slug"] = catalog.unique_slug(fields["title"], exclude_id=product_id)
    catalog.update_product(product_id, **fields)
    catalog.upsert_variants(product_id, _parse_variants(request.get_raw("variants")))
    audit("product.update", actor=_actor(request), subject=fields["slug"])
    return redirect(f"/admin/products/{product_id}", flash="Product saved.")


# --------------------------------------------------------------- inventory

@router.get("/admin/inventory")
def admin_inventory(request: Request) -> Response:
    require_staff(request)
    rows = db.query(
        "SELECT v.*, p.title, p.slug, p.status, "
        "       MAX(v.stock - v.reserved, 0) AS available "
        "FROM variants v JOIN products p ON p.id = v.product_id "
        "ORDER BY available ASC, p.title, v.position"
    )
    body_rows = []
    for v in rows:
        tone = "muted" if v["available"] > v["low_stock_at"] else "low"
        body_rows.append([
            f'<a href="/admin/products/{v["product_id"]}">{E(v["title"])}</a>',
            E(v["sku"]),
            E(f'{v["size"]} {v["color"]}'.strip() or "—"),
            f'<span class="num">{v["stock"]}</span>',
            f'<span class="num">{v["reserved"]}</span>',
            badge(str(v["available"]), tone),
            f"""<form method="post" action="/admin/inventory" class="cluster">
                {csrf_input(request)}
                <input type="hidden" name="variant_id" value="{v['id']}">
                <input type="number" name="stock" value="{v['stock']}" min="0"
                       style="width:5rem;padding:.35rem .5rem;border:1px solid var(--hairline)"
                       aria-label="Stock for {E(v['sku'])}">
                <button class="btn btn--quiet btn--small" type="submit">Set</button>
            </form>""",
        ])
    content = f"""
<div class="filter-bar">
  <input data-table-filter="#inventory-table" placeholder="Filter by product or SKU">
</div>
<div class="panel" id="inventory-table">
  <div class="panel__head"><h2>All variants</h2>
    <span class="muted">Available = stock − reserved</span></div>
  {table(["Product", "SKU", "Variant", "Stock", "Reserved", "Available", ""],
         body_rows, empty="No variants yet.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Inventory",
                                      active="/admin/inventory"))


@router.post("/admin/inventory")
def admin_inventory_update(request: Request) -> Response:
    require_staff(request)
    variant_id = request.get_int("variant_id")
    stock = max(0, request.get_int("stock"))
    with db.tx():
        db.update("variants", "id = ?", (variant_id,), stock=stock)
    audit("inventory.set", actor=_actor(request), subject=str(variant_id),
          detail=f"stock={stock}")
    return redirect("/admin/inventory", flash="Stock updated.")


# --------------------------------------------------------------- customers

@router.get("/admin/customers")
def admin_customers(request: Request) -> Response:
    require_staff(request)
    search = request.get("q")
    where, params = "1 = 1", []
    if search:
        where = "(u.email LIKE ? OR u.name LIKE ?)"
        params = [f"%{search}%", f"%{search}%"]
    rows = db.query(
        f"SELECT u.*, "
        f"  (SELECT count(*) FROM orders o WHERE o.user_id = u.id "
        f"   AND o.status IN ('paid','fulfilled')) AS order_count, "
        f"  (SELECT COALESCE(SUM(total_cents), 0) FROM orders o WHERE o.user_id = u.id "
        f"   AND o.status IN ('paid','fulfilled')) AS spend "
        f"FROM users u WHERE {where} ORDER BY spend DESC, u.id DESC LIMIT 300",
        params,
    )
    customer_rows = [
        [f'{E(u["name"] or "—")}<br><span class="muted">{E(u["email"])}</span>',
         badge(u["role"].title(), "paid" if u["role"] != "customer" else "muted"),
         f'<span class="num">{u["order_count"]}</span>',
         f'<span class="num">{money(u["spend"])}</span>',
         "Yes" if u["marketing_opt_in"] else "—",
         E(u["created_at"][:10])]
        for u in rows
    ]
    content = f"""
<form class="filter-bar" method="get" action="/admin/customers">
  <input name="q" value="{E(search)}" placeholder="Search name or email">
  <button class="btn btn--small btn--ghost" type="submit">Search</button>
</form>
<div class="panel">
  <div class="panel__head"><h2>{len(rows)} customer(s)</h2></div>
  {table(["Customer", "Role", "Orders", "Lifetime", "Marketing", "Joined"],
         customer_rows, empty="No customers yet.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Customers",
                                      active="/admin/customers"))


# --------------------------------------------------------------- discounts

@router.get("/admin/discounts")
def admin_discounts(request: Request, error: str = "") -> Response:
    require_staff(request)
    rows = discounts.list_all()
    discount_rows = [
        [f'<strong>{E(d["code"].upper())}</strong>',
         E(discounts.describe(d)),
         money(d["min_spend_cents"]) if d["min_spend_cents"] else "—",
         f'<span class="num">{d["uses"]}{" / " + str(d["max_uses"]) if d["max_uses"] else ""}</span>',
         badge("Active", "paid") if d["active"] else badge("Off", "muted"),
         f"""<form method="post" action="/admin/discounts/toggle">
             {csrf_input(request)}
             <input type="hidden" name="discount_id" value="{d['id']}">
             <button class="btn btn--quiet btn--small" type="submit">
               {'Disable' if d['active'] else 'Enable'}</button></form>"""]
        for d in rows
    ]
    content = f"""
<div class="split">
  <div class="panel">
    <div class="panel__head"><h2>Codes</h2></div>
    {table(["Code", "Reward", "Min spend", "Uses", "Status", ""], discount_rows,
           empty="No promotion codes yet.")}
  </div>
  <div class="panel">
    <div class="panel__head"><h2>Create a code</h2></div>
    <div class="panel__body">
      <form method="post" action="/admin/discounts">
        {csrf_input(request)}
        {f'<div class="form-error">{E(error)}</div>' if error else ''}
        {field("code", "Code", required=True, placeholder="WELCOME10")}
        {field("kind", "Reward", options=[("percent", "Percent off"),
                                          ("fixed", "Fixed amount off"),
                                          ("free_shipping", "Free shipping")])}
        {field("value", "Value", type_="number", value="10",
               hint="Percent points, or cents for a fixed amount.")}
        {field("min_spend_cents", "Minimum spend (cents)", type_="number", value="0")}
        {field("max_uses", "Maximum uses", type_="number",
               hint="Leave blank for unlimited.")}
        {button("Create code", type_="submit", variant="solid")}
      </form>
    </div>
  </div>
</div>
"""
    return html_response(admin_layout(request, content, title="Discounts",
                                      active="/admin/discounts"))


@router.post("/admin/discounts")
def admin_discount_create(request: Request) -> Response:
    require_staff(request)
    code = slugify(request.get("code"), fallback="").upper()
    if not code:
        return admin_discounts(request, "Enter a code.")
    if discounts.find(code):
        return admin_discounts(request, "That code already exists.")
    max_uses = request.get("max_uses")
    with db.tx():
        db.insert(
            "discounts", code=code, kind=request.get("kind", "percent"),
            value=max(0, request.get_int("value")),
            min_spend_cents=max(0, request.get_int("min_spend_cents")),
            max_uses=int(max_uses) if max_uses.isdigit() else None,
        )
    audit("discount.create", actor=_actor(request), subject=code)
    return redirect("/admin/discounts", flash=f"Code {code} created.")


@router.post("/admin/discounts/toggle")
def admin_discount_toggle(request: Request) -> Response:
    require_staff(request)
    with db.tx():
        db.execute("UPDATE discounts SET active = 1 - active WHERE id = ?",
                   (request.get_int("discount_id"),))
    return redirect("/admin/discounts", flash="Code updated.")


# ---------------------------------------------------------------- messages

@router.get("/admin/messages")
def admin_messages(request: Request) -> Response:
    require_staff(request)
    rows = db.query("SELECT * FROM messages ORDER BY handled, id DESC LIMIT 200")
    blocks = "".join(
        f"""
<div class="panel" style="margin-bottom:1rem">
  <div class="panel__head">
    <div><strong>{E(m['name'])}</strong>
      <span class="muted"> · <a href="mailto:{E(m['email'])}">{E(m['email'])}</a>
      · {E(m['created_at'][:16])}</span></div>
    <div class="cluster">
      {badge("Handled", "muted") if m["handled"] else badge("New", "paid")}
      <form method="post" action="/admin/messages/toggle">
        {csrf_input(request)}
        <input type="hidden" name="message_id" value="{m['id']}">
        <button class="btn btn--quiet btn--small" type="submit">
          {'Reopen' if m['handled'] else 'Mark handled'}</button>
      </form>
    </div>
  </div>
  <div class="panel__body">
    {f'<p class="eyebrow">{E(m["subject"])}</p>' if m["subject"] else ''}
    <p style="white-space:pre-wrap">{E(m['body'])}</p>
  </div>
</div>
"""
        for m in rows
    ) or '<p class="muted">No messages yet.</p>'
    return html_response(admin_layout(request, blocks, title="Messages",
                                      active="/admin/messages"))


@router.post("/admin/messages/toggle")
def admin_message_toggle(request: Request) -> Response:
    require_staff(request)
    with db.tx():
        db.execute("UPDATE messages SET handled = 1 - handled WHERE id = ?",
                   (request.get_int("message_id"),))
    return redirect("/admin/messages")


# ------------------------------------------------------------------- email

@router.get("/admin/email")
def admin_email(request: Request) -> Response:
    require_staff(request)
    rows = db.query("SELECT * FROM email_outbox ORDER BY id DESC LIMIT 200")
    email_rows = [
        [E(m["created_at"][:16]), E(m["to_address"]), E(m["subject"]),
         E(m["template"] or "—"),
         badge(m["status"].title(),
               {"sent": "paid", "queued": "pending", "failed": "cancelled"}[m["status"]]),
         E(m["error"][:60] or "")]
        for m in rows
    ]
    queued = sum(1 for m in rows if m["status"] != "sent")
    content = f"""
<div class="panel">
  <div class="panel__head">
    <h2>Outbox</h2>
    <div class="cluster">
      <span class="muted">Transport: {'SMTP ' + E(config.smtp_host) if config.email_live else 'console (development)'}</span>
      <form method="post" action="/admin/email/flush">
        {csrf_input(request)}
        <button class="btn btn--small btn--ghost" type="submit">
          Retry queued ({queued})</button>
      </form>
    </div>
  </div>
  {table(["When", "To", "Subject", "Template", "Status", "Error"], email_rows,
         empty="No email sent yet.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Email log",
                                      active="/admin/email"))


@router.post("/admin/email/flush")
def admin_email_flush(request: Request) -> Response:
    require_staff(request)
    with db.tx():
        db.execute("UPDATE email_outbox SET status = 'queued' WHERE status = 'failed'")
    sent = mailer.flush(limit=100)
    return redirect("/admin/email", flash=f"Delivered {sent} message(s).")


# ----------------------------------------------------------------- traffic

@router.get("/admin/traffic")
def admin_traffic(request: Request) -> Response:
    require_staff(request)
    days = request.get_int("days", 30) or 30
    days = days if days in (7, 30, 90) else 30
    stats = analytics.summary(days)
    series = analytics.by_day(min(days, 30))
    peak = max((views for _, views, _ in series), default=0) or 1

    bars = "".join(
        f'<span style="height:{max(2, round(100 * views / peak))}%" '
        f'title="{E(day)}: {views} views, {visitors} visitors"></span>'
        for day, views, visitors in series
    )

    stat_cards = "".join(
        f'<div class="stat"><span class="stat__label">{E(label)}</span>'
        f'<span class="stat__value">{E(value)}</span>'
        f'<span class="stat__note">{E(note)}</span></div>'
        for label, value, note in [
            ("Visits", f"{stats['visits']:,}", f"last {days} days"),
            ("Visitors", f"{stats['visitors']:,}", "unique, cookieless"),
            ("Pages / visitor", stats["pages_per_visitor"], "engagement"),
            ("Conversion", f"{stats['conversion_pct']}%", "visitors who ordered"),
            ("Revenue", money(stats["revenue_cents"]), f"last {days} days"),
            ("Revenue / visitor", money(stats["revenue_per_visitor_cents"]),
             "across all visitors"),
        ]
    )

    top = stats["funnel"][0][1] or 1
    funnel = "".join(
        f'<div class="funnel__step">'
        f'<div class="funnel__bar" style="width:{max(2, round(100 * count / top))}%"></div>'
        f'<span class="funnel__label">{E(label)}</span>'
        f'<span class="funnel__count">{count:,}'
        f'<small>{round(100 * count / top)}%</small></span>'
        f'</div>'
        for label, count in stats["funnel"]
    )

    path_rows = [
        [f'<a href="{E(r["path"])}">{E(r["path"])}</a>',
         f'<span class="num">{r["views"]:,}</span>',
         f'<span class="num">{r["visitors"]:,}</span>']
        for r in analytics.top_paths(12, days)
    ]
    referrer_rows = [
        [E(r["referrer"]), f'<span class="num">{r["views"]:,}</span>',
         f'<span class="num">{r["visitors"]:,}</span>']
        for r in analytics.top_referrers(10, days)
    ]
    device_rows = [
        [E(r["device"].title()), f'<span class="num">{r["visitors"]:,}</span>']
        for r in analytics.devices(days)
    ]

    ranges = "".join(
        f'<a class="chip{" is-active" if days == d else ""}" '
        f'href="/admin/traffic?days={d}">{d} days</a>'
        for d in (7, 30, 90)
    )

    content = f"""
<div class="filter-bar">{ranges}</div>
<div class="stat-row">{stat_cards}</div>

<div class="panel">
  <div class="panel__head">
    <h2>Visits &amp; sales</h2>
    <span class="muted">The metric named in the brief</span>
  </div>
  <div class="panel__body">
    <div class="sparkline">{bars}</div>
  </div>
</div>

<div class="panel">
  <div class="panel__head"><h2>Funnel</h2>
    <span class="muted">Distinct visitors reaching each step</span></div>
  <div class="panel__body"><div class="funnel">{funnel}</div></div>
</div>

<div class="split">
  <div class="panel">
    <div class="panel__head"><h2>Top pages</h2></div>
    {table(["Path", "Views", "Visitors"], path_rows, empty="No traffic yet.")}
  </div>
  <div class="panel">
    <div class="panel__head"><h2>Referrers</h2></div>
    {table(["Source", "Views", "Visitors"], referrer_rows,
           empty="No referrers yet — all traffic is direct.")}
  </div>
</div>

<div class="panel">
  <div class="panel__head"><h2>Devices</h2></div>
  {table(["Device", "Visitors"], device_rows, empty="No traffic yet.")}
</div>

<div class="panel">
  <div class="panel__head"><h2>How this is measured</h2></div>
  <div class="panel__body prose">
    <p>First-party and cookieless. A visitor is a hash of IP address and
       browser against a salt that changes every day and is never written
       down, so nobody can be followed between days, no raw address is kept,
       and no third party sees any of it.</p>
    <p>That is deliberate: it measures what the brief asked for without
       putting the store under a cookie-consent obligation. Known crawlers are
       excluded, and rows older than 400 days are deleted automatically.</p>
  </div>
</div>
"""
    return html_response(admin_layout(request, content, title="Traffic",
                                      active="/admin/traffic"))


# ---------------------------------------------------------------- activity

@router.get("/admin/activity")
def admin_activity(request: Request) -> Response:
    require_staff(request)
    rows = db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300")
    activity_rows = [
        [E(a["created_at"][:19]), E(a["actor"]),
         f'<code>{E(a["action"])}</code>', E(a["subject"]), E(a["detail"]), E(a["ip"])]
        for a in rows
    ]
    content = f"""
<div class="filter-bar">
  <input data-table-filter="#activity-table" placeholder="Filter activity">
</div>
<div class="panel" id="activity-table">
  <div class="panel__head"><h2>Audit trail</h2>
    <span class="muted">Newest first · last 300 events</span></div>
  {table(["When", "Actor", "Action", "Subject", "Detail", "IP"], activity_rows,
         empty="Nothing recorded yet.")}
</div>
"""
    return html_response(admin_layout(request, content, title="Activity",
                                      active="/admin/activity"))
