"""Cart, checkout, Stripe hand-off, confirmation and webhooks."""
from __future__ import annotations

import json

from . import accounts, art, cart as cart_module, catalog, db, orders, stripe_api
from .config import config
from .security import audit, valid_email
from .ui import (
    E, badge, button, csrf_input, empty_state, field, icon, layout, money,
    page_header, section_label,
)
from .views_account import address_fields, validate_address
from .web import (
    HttpError, Request, Response, Router, html_response, json_response,
    redirect, text_response,
)

router = Router()


# ------------------------------------------------------------- summary UI

def order_summary(cart: cart_module.Cart, *, request: Request,
                  editable: bool = True) -> str:
    totals = cart.totals
    rows = [("Subtotal", money(totals.subtotal_cents))]
    if totals.discount_cents:
        rows.append((f"Discount · {totals.discount_label}",
                     "-" + money(totals.discount_cents)))
    rows.append(("Shipping",
                 "Free" if not totals.shipping_cents else money(totals.shipping_cents)))
    if totals.tax_cents:
        rows.append(("Tax", money(totals.tax_cents)))

    row_html = "".join(
        f'<div class="summary__row"><span>{E(label)}</span><span>{E(value)}</span></div>'
        for label, value in rows
    )

    progress = ""
    if totals.free_shipping_remaining and config.free_shipping_threshold_cents:
        pct = min(100, round(
            100 * totals.subtotal_cents / config.free_shipping_threshold_cents
        ))
        progress = f"""
<div class="progress">
  <p class="summary__row summary__row--muted" style="padding:0">
    <span>{money(totals.free_shipping_remaining)} away from free shipping</span>
  </p>
  <div class="progress__track"><div class="progress__bar" style="width:{pct}%"></div></div>
</div>
"""

    promo = ""
    if editable:
        if cart.discount_row is not None:
            promo = f"""
<form method="post" action="/cart/discount/remove" class="cluster" style="margin:1.1rem 0">
  {csrf_input(request)}
  {badge(totals.discount_code, "paid")}
  <button class="btn btn--quiet btn--small" type="submit">Remove</button>
</form>
"""
        else:
            promo = f"""
<form method="post" action="/cart/discount" class="promo">
  {csrf_input(request)}
  <label class="visually-hidden" for="promo-code">Promotion code</label>
  <input id="promo-code" name="code" placeholder="Promo code" autocomplete="off">
  <button class="btn btn--ghost btn--small" type="submit">Apply</button>
</form>
"""

    return f"""
<aside class="summary" aria-label="Order summary">
  <h2 class="eyebrow">Summary</h2>
  {row_html}
  <div class="summary__row summary__total">
    <span>Total</span><span>{money(totals.total_cents)}</span>
  </div>
  {promo}
  {progress}
</aside>
"""


# ------------------------------------------------------------------ cart

@router.get("/cart")
def cart_page(request: Request) -> Response:
    cart = cart_module.load(request.cart_id)
    if cart.empty:
        content = f"""
<div class="shell">
  {empty_state("Your bag is empty",
               "Nothing in here yet. The collection is one click away.",
               button("Shop the collection", href="/shop", variant="solid"),
               heading="h1")}
</div>
"""
        return html_response(layout(request, content, title="Bag"))

    lines = "".join(
        f"""
<div class="line-item">
  <a class="line-item__media" href="/product/{E(line.slug)}">
    <img src="{E(art.media_url(line.art_seed))}" alt="{E(line.title)}"
         width="800" height="1000" loading="lazy">
  </a>
  <div>
    <a class="line-item__title" href="/product/{E(line.slug)}">{E(line.title)}</a>
    <p class="line-item__meta">{E(line.variant_label or 'One size')} · {E(line.sku)}</p>
    {f'<p class="line-item__meta" style="color:var(--ink)">Only {line.available} left — quantity reduced at checkout.</p>' if line.over_stock else ''}
    <form method="post" action="/cart/update" class="qty">
      {csrf_input(request)}
      <input type="hidden" name="variant_id" value="{line.variant_id}">
      <button type="button" data-qty="down" aria-label="Decrease quantity for {E(line.title)}">
        {icon("minus", size=14)}</button>
      <label class="visually-hidden" for="qty-{line.variant_id}">
        Quantity for {E(line.title)}</label>
      <input id="qty-{line.variant_id}" type="number" name="quantity"
             value="{line.quantity}" min="1" max="{max(1, line.available)}"
             inputmode="numeric" data-cart-qty>
      <button type="button" data-qty="up" aria-label="Increase quantity for {E(line.title)}">
        {icon("plus", size=14)}</button>
      <noscript><button class="btn btn--small btn--ghost" type="submit">Update</button></noscript>
    </form>
    <form method="post" action="/cart/remove" style="margin-top:.5rem">
      {csrf_input(request)}
      <input type="hidden" name="variant_id" value="{line.variant_id}">
      <button class="btn btn--quiet btn--small" type="submit">Remove</button>
    </form>
  </div>
  <div class="line-item__price">
    {money(line.total_cents)}
    {f'<p class="line-item__meta">{money(line.unit_cents)} each</p>' if line.quantity > 1 else ''}
  </div>
</div>
"""
        for line in cart.lines
    )

    content = f"""
<div class="shell">
  {page_header("Bag", eyebrow=f"{cart.count} item{'' if cart.count == 1 else 's'}")}
  <div class="cart section" style="padding-top:0">
    <div>
      <div class="line-items">{lines}</div>
      <p style="margin-top:1.5rem">
        {button("Continue shopping", href="/shop", variant="quiet")}
      </p>
    </div>
    <div>
      {order_summary(cart, request=request)}
      <p style="margin-top:1rem">
        {button("Checkout", href="/checkout", variant="solid", full=True)}
      </p>
      <div class="trust">
        <span>{icon("check", size=14)} Secure checkout by Stripe</span>
        <span>{icon("check", size=14)} 30-day returns</span>
      </div>
    </div>
  </div>
</div>
"""
    return html_response(layout(request, content, title="Bag"))


@router.post("/cart/add")
def cart_add(request: Request) -> Response:
    variant_id = request.get_int("variant_id")
    quantity = max(1, request.get_int("quantity", 1))
    if not variant_id:
        variants = catalog.variants_for(request.get_int("product_id"))
        available = next((v for v in variants if v["available"] > 0), None)
        variant_id = available["id"] if available else 0

    try:
        line = cart_module.add(request.cart_id, variant_id, quantity)
    except ValueError as exc:
        if request.wants_json:
            return json_response({"ok": False, "error": str(exc)}, status=400)
        return redirect(request.headers.get("Referer", "/shop"),
                        flash=str(exc), tone="error")

    count = cart_module.count_for(request.cart_id)
    if request.wants_json:
        return json_response({
            "ok": True, "cart_count": count,
            "message": f"{line.title} added to bag",
        })
    return redirect("/cart", flash=f"{line.title} added to bag.")


@router.post("/cart/update")
def cart_update(request: Request) -> Response:
    cart_module.set_quantity(
        request.cart_id, request.get_int("variant_id"), request.get_int("quantity", 1)
    )
    if request.wants_json:
        return json_response({"ok": True,
                              "cart_count": cart_module.count_for(request.cart_id)})
    return redirect("/cart")


@router.post("/cart/remove")
def cart_remove(request: Request) -> Response:
    cart_module.remove(request.cart_id, request.get_int("variant_id"))
    if request.wants_json:
        return json_response({"ok": True,
                              "cart_count": cart_module.count_for(request.cart_id)})
    return redirect("/cart", flash="Item removed.")


@router.post("/cart/discount")
def cart_discount(request: Request) -> Response:
    result = cart_module.apply_discount(request.cart_id, request.get("code"))
    return redirect(request.headers.get("Referer", "/cart"),
                    flash=result.message, tone="ok" if result.ok else "error")


@router.post("/cart/discount/remove")
def cart_discount_remove(request: Request) -> Response:
    cart_module.clear_discount(request.cart_id)
    return redirect(request.headers.get("Referer", "/cart"), flash="Code removed.")


# -------------------------------------------------------------- checkout

@router.get("/checkout")
def checkout_page(request: Request, errors: dict | None = None,
                  message: str = "") -> Response:
    cart = cart_module.load(request.cart_id)
    if cart.empty:
        return redirect("/cart", flash="Your bag is empty.", tone="error")

    errors = errors or {}
    prefill = accounts.default_address(request.user["id"] if request.user else None)
    email_value = request.get("email") or (request.user["email"] if request.user else "")

    mode_note = "" if config.payments_live else f"""
<div class="pay-note">
  {icon("check", size=16)}
  <span><strong>Demo mode.</strong> No Stripe keys are configured, so this
  checkout completes without charging anything — the full order, email and
  inventory flow still runs. Set <code>STRIPE_SECRET_KEY</code> to go live.</span>
</div>
"""

    sign_in_prompt = "" if request.user else f"""
<p class="form-note" style="margin-bottom:1.5rem">
  Have an account? <a href="/login?next=/checkout">Sign in</a> to use a saved
  address — or just check out as a guest below.
</p>
"""

    content = f"""
<div class="shell">
  {page_header("Checkout", eyebrow="Secure")}
  <div class="checkout section" style="padding-top:0">
    <form method="post" action="/checkout">
      {csrf_input(request)}
      {f'<div class="form-error">{E(message)}</div>' if message else ""}
      {mode_note}
      {sign_in_prompt}

      <section class="checkout__step">
        <h2>1 · Contact</h2>
        {field("email", "Email", type_="email", required=True, autocomplete="email",
               value=email_value, error=errors.get("email", ""),
               hint="Your receipt and tracking go here.")}
      </section>

      <section class="checkout__step">
        <h2>2 · Shipping address</h2>
        {address_fields(request, prefill, errors)}
        {'' if not request.user else '''
        <label class="cluster" style="font-size:.8rem;color:var(--muted)">
          <input type="checkbox" name="save_address" value="1" checked
                 style="width:auto;margin:0">
          <span>Save this address to my account</span>
        </label>'''}
      </section>

      <section class="checkout__step">
        <h2>3 · Order notes (optional)</h2>
        {field("notes", "Anything we should know?", rows=3,
               placeholder="Delivery instructions, gift note…")}
      </section>

      {button("Continue to payment", type_="submit", variant="solid", full=True)}
      <div class="trust">
        <span>{icon("check", size=14)} Card details never touch our servers</span>
        <span>{icon("check", size=14)} Stripe-secured payment</span>
        <span>{icon("check", size=14)} 30-day returns</span>
      </div>
    </form>

    <div>
      {order_summary(cart, request=request, editable=False)}
      <details style="margin-top:1rem">
        <summary class="eyebrow" style="cursor:pointer">
          {cart.count} item{'' if cart.count == 1 else 's'} in bag</summary>
        <div style="margin-top:1rem">
          {"".join(f'''
          <div class="summary__row summary__row--muted">
            <span>{E(line.title)} · {E(line.variant_label or "One size")} × {line.quantity}</span>
            <span>{money(line.total_cents)}</span>
          </div>''' for line in cart.lines)}
        </div>
      </details>
      <p style="margin-top:1rem">{button("Edit bag", href="/cart", variant="quiet")}</p>
    </div>
  </div>
</div>
"""
    return html_response(layout(request, content, title="Checkout"))


@router.post("/checkout")
def checkout_submit(request: Request) -> Response:
    cart = cart_module.load(request.cart_id)
    if cart.empty:
        return redirect("/cart", flash="Your bag is empty.", tone="error")

    email = request.get("email")
    values, errors = validate_address(request)
    if not valid_email(email):
        errors["email"] = "Enter a valid email address."
    if errors:
        return checkout_page(request, errors, "Check the highlighted fields.")

    user_id = request.user["id"] if request.user else None
    if user_id and request.checked("save_address"):
        accounts.save_address(user_id, make_default=True, **values)

    try:
        order = orders.create_from_cart(
            cart, email=email, user_id=user_id, shipping=values,
            notes=request.get("notes"),
        )
    except orders.CheckoutError as exc:
        return checkout_page(request, {}, str(exc))

    try:
        session = stripe_api.create_checkout_session(
            order_number=order["number"],
            email=email,
            line_items=orders.to_stripe_line_items(order["id"]),
            success_url=config.url(f"/checkout/complete?order={order['number']}"),
            cancel_url=config.url(f"/checkout/cancelled?order={order['number']}"),
            shipping_cents=order["shipping_cents"],
            discount_cents=order["discount_cents"],
            metadata={"cart_id": request.cart_id},
        )
    except stripe_api.StripeError as exc:
        orders.cancel(order, reason="[payment setup failed]", actor="system")
        return checkout_page(request, {},
                             f"We couldn't start the payment: {exc}")

    orders.attach_payment(order["id"], session["id"])
    return redirect(session["url"], status=303)


@router.get("/checkout/complete")
def checkout_complete(request: Request) -> Response:
    order = orders.get_by_number(request.get("order"))
    if order is None:
        raise HttpError(404, "We couldn't find that order.")

    if order["status"] == "pending":
        try:
            session = stripe_api.retrieve_checkout_session(order["payment_ref"])
        except stripe_api.StripeError:
            session = {}
        if session.get("payment_status") == "paid":
            intent = session.get("payment_intent") or ""
            if isinstance(intent, dict):
                intent = intent.get("id", "")
            order = orders.mark_paid(order, payment_intent=intent)

    if order["status"] == "pending":
        content = f"""
<div class="shell">
  {empty_state("Payment is still processing",
               f"Order {order['number']} hasn't been confirmed yet. We'll email you "
               f"the moment it clears — no need to pay again.",
               button("View orders", href="/account/orders", variant="ghost"),
               heading="h1")}
</div>
"""
        return html_response(layout(request, content, title="Processing"))

    # Payment succeeded: the cart's work is done.  The header's count was read
    # by middleware before this handler ran, so correct it for this render.
    cart_module.clear(request.cart_id)
    request.cart_count = 0
    items = orders.items_for(order["id"])
    item_html = "".join(
        f"""
<div class="summary__row">
  <span>{E(i['title'])} · {E(i['variant_label'] or 'One size')} × {i['quantity']}</span>
  <span>{money(i['unit_cents'] * i['quantity'])}</span>
</div>
"""
        for i in items
    )
    content = f"""
<div class="shell">
  <section class="section section--center">
    {section_label("Confirmed")}
    <h1 class="display display--m">Thank you.</h1>
    <p class="lede">Order <strong>{E(order['number'])}</strong> is confirmed.
      A receipt is on its way to {E(order['email'])}.</p>
  </section>
  <div class="cart" style="padding-bottom:var(--rhythm)">
    <div>
      <h2 class="eyebrow">What's coming</h2>
      <div class="line-items">
        {"".join(f'''
        <div class="line-item">
          <span class="line-item__media">
            <img src="{E(art.media_url(i['art_seed']))}" alt="" width="800" height="1000">
          </span>
          <span>
            <a class="line-item__title" href="/product/{E(i['slug'])}">{E(i['title'])}</a>
            <span class="line-item__meta">{E(i['variant_label'])} · Qty {i['quantity']}</span>
          </span>
          <span class="line-item__price">{money(i['unit_cents'] * i['quantity'])}</span>
        </div>''' for i in items)}
      </div>
      <h2 class="eyebrow" style="margin-top:2rem">Shipping to</h2>
      <p>{E(order['ship_name'])}<br>{E(order['ship_line1'])}
         {(' ' + E(order['ship_line2'])) if order['ship_line2'] else ''}<br>
         {E(order['ship_city'])}, {E(order['ship_region'])} {E(order['ship_postal'])}<br>
         {E(order['ship_country'])}</p>
    </div>
    <aside class="summary">
      <h2 class="eyebrow">Summary</h2>
      {item_html}
      <div class="summary__row summary__row--muted">
        <span>Shipping</span>
        <span>{"Free" if not order['shipping_cents'] else money(order['shipping_cents'])}</span>
      </div>
      {f'''<div class="summary__row summary__row--muted"><span>Discount</span>
           <span>-{money(order['discount_cents'])}</span></div>''' if order['discount_cents'] else ''}
      <div class="summary__row summary__total">
        <span>Paid</span><span>{money(order['total_cents'])}</span>
      </div>
      <p style="margin-top:1.4rem">
        {button("Keep shopping", href="/shop", variant="solid", full=True)}
      </p>
    </aside>
  </div>
</div>
"""
    return html_response(layout(request, content, title=f"Order {order['number']}"))


@router.get("/checkout/cancelled")
def checkout_cancelled(request: Request) -> Response:
    order = orders.get_by_number(request.get("order"))
    if order is not None and order["status"] == "pending":
        orders.cancel(order, reason="[customer cancelled at payment]",
                      actor=order["email"])
    content = f"""
<div class="shell">
  {empty_state("Payment cancelled",
               "Nothing was charged and your bag is exactly as you left it.",
               button("Back to bag", href="/cart", variant="solid"),
               heading="h1")}
</div>
"""
    return html_response(layout(request, content, title="Payment cancelled"))


# -------------------------------------------------------------- webhooks

@router.post("/webhooks/stripe")
def stripe_webhook(request: Request) -> Response:
    """Authoritative payment confirmation.

    CSRF-exempt by design: authenticity comes from the Stripe signature, which
    is far stronger than a cookie-bound token for a server-to-server call.
    """
    signature = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe_api.verify_webhook(
            request.body, signature, config.stripe_webhook_secret
        )
    except stripe_api.StripeError as exc:
        audit("webhook.rejected", actor="stripe", detail=str(exc),
              ip=request.remote_addr)
        return text_response(f"Signature verification failed: {exc}", status=400)

    event_id = event.get("id", "")
    if event_id:
        existing = db.one("SELECT id FROM webhook_events WHERE id = ?", (event_id,))
        if existing:
            return json_response({"received": True, "duplicate": True})
        with db.tx():
            db.insert("webhook_events", id=event_id,
                      kind=event.get("type", ""),
                      payload=json.dumps(event)[:20000])

    kind = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if kind == "checkout.session.completed":
        order = _order_for_session(obj)
        if order is not None and obj.get("payment_status") == "paid":
            intent = obj.get("payment_intent") or ""
            if isinstance(intent, dict):
                intent = intent.get("id", "")
            orders.mark_paid(order, payment_intent=intent)

    elif kind in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        order = _order_for_session(obj)
        if order is not None and order["status"] == "pending":
            orders.cancel(order, reason=f"[{kind}]", actor="stripe")

    elif kind == "charge.refunded":
        intent = obj.get("payment_intent", "")
        order = db.one("SELECT * FROM orders WHERE payment_intent = ?", (intent,))
        if order is not None and order["status"] in ("paid", "fulfilled"):
            with db.tx():
                db.update("orders", "id = ?", (order["id"],), status="refunded")
            audit("order.refund", actor="stripe", subject=order["number"])

    return json_response({"received": True, "type": kind})


def _order_for_session(obj: dict):
    reference = obj.get("client_reference_id") or ""
    order = orders.get_by_number(reference) if reference else None
    if order is None and obj.get("id"):
        order = orders.get_by_payment_ref(obj["id"])
    if order is None:
        number = (obj.get("metadata") or {}).get("order_number", "")
        order = orders.get_by_number(number) if number else None
    return order
