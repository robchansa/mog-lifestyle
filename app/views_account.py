"""Authentication and the customer account area."""
from __future__ import annotations

from typing import Any

from . import accounts, art, mailer, orders
from .config import config
from .security import (
    HONEYPOT_FIELD, TIMESTAMP_FIELD, audit, spam_signals, valid_email,
)
from .ui import (
    E, badge, button, csrf_input, empty_state, field, layout, money,
    page_header, section_label, spam_trap, table,
)
from .web import HttpError, Request, Response, Router, html_response, redirect

router = Router()

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
]


def require_user(request: Request):
    if request.user is None:
        raise HttpError(403, "Please sign in to continue.")
    return request.user


def _safe_next(target: str) -> str:
    """Only ever redirect to a path on this site."""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/account"


# ------------------------------------------------------------------ auth

@router.get("/login")
def login_form(request: Request, error: str = "", email: str = "") -> Response:
    if request.user and not error:
        return redirect("/account")
    next_url = _safe_next(request.get("next", "/account"))
    content = f"""
<div class="shell">
  <div class="auth">
    {section_label("Welcome back")}
    <h1 class="display display--s">Sign in</h1>
    <form method="post" action="/login" class="stack" style="margin-top:2rem">
      {csrf_input(request)}
      <input type="hidden" name="next" value="{E(next_url)}">
      {f'<div class="form-error">{E(error)}</div>' if error else ""}
      {field("email", "Email", type_="email", required=True,
             autocomplete="username", value=email)}
      {field("password", "Password", type_="password", required=True,
             autocomplete="current-password")}
      {button("Sign in", type_="submit", variant="solid", full=True)}
    </form>
    <p class="auth__switch">
      New here? <a href="/register">Create an account</a> — it takes a minute and
      keeps every order in one place.
    </p>
  </div>
</div>
"""
    return html_response(layout(request, content, title="Sign in"))


@router.post("/login")
def login_submit(request: Request) -> Response:
    email = request.get("email")
    password = request.get_raw("password")
    next_url = _safe_next(request.get("next", "/account"))
    try:
        user = accounts.authenticate(email, password, ip=request.remote_addr)
    except accounts.AuthError as exc:
        return login_form(request, error=str(exc), email=email)

    session = accounts.rotate_session(request.session, user["id"])
    response = redirect(next_url, flash=f"Signed in as {user['email']}.")
    response.set_cookie(
        accounts.SESSION_COOKIE, accounts.session_cookie_value(session),
        max_age=config.session_days * 86400,
    )
    return response


@router.get("/register")
def register_form(request: Request, error: str = "",
                  values: dict[str, Any] | None = None) -> Response:
    if request.user and not error:
        return redirect("/account")
    values = values or {}
    content = f"""
<div class="shell">
  <div class="auth">
    {section_label("Join")}
    <h1 class="display display--s">Create account</h1>
    <form method="post" action="/register" class="stack" style="margin-top:2rem">
      {csrf_input(request)}
      {spam_trap()}
      {f'<div class="form-error">{E(error)}</div>' if error else ""}
      {field("name", "Name", autocomplete="name", value=values.get("name", ""))}
      {field("email", "Email", type_="email", required=True,
             autocomplete="email", value=values.get("email", ""))}
      {field("password", "Password", type_="password", required=True,
             autocomplete="new-password",
             hint="At least 10 characters. A short phrase beats a short password.")}
      <label class="cluster" style="font-size:.8rem;color:var(--muted);margin-bottom:1rem">
        <input type="checkbox" name="marketing_opt_in" value="1"
               {'checked' if values.get('marketing_opt_in') else ''}
               style="width:auto;margin:0">
        <span>Email me about drops and restocks. Unsubscribe any time.</span>
      </label>
      {button("Create account", type_="submit", variant="solid", full=True)}
    </form>
    <p class="auth__switch">
      Already have an account? <a href="/login">Sign in</a>.
    </p>
  </div>
</div>
"""
    return html_response(layout(request, content, title="Create account"))


@router.post("/register")
def register_submit(request: Request) -> Response:
    email = request.get("email")
    name = request.get("name")
    opt_in = request.checked("marketing_opt_in")

    signal = spam_signals(request.get(HONEYPOT_FIELD),
                          request.get_raw(TIMESTAMP_FIELD))
    if signal:
        audit("spam.blocked", actor=email, subject="register",
              detail=signal, ip=request.remote_addr)
        return register_form(request, error="Something went wrong. Please try again.",
                             values={"email": email, "name": name,
                                     "marketing_opt_in": opt_in})
    try:
        user = accounts.register(
            email, request.get_raw("password"), name=name, marketing_opt_in=opt_in
        )
    except accounts.AuthError as exc:
        return register_form(request, error=str(exc),
                             values={"email": email, "name": name,
                                     "marketing_opt_in": opt_in})
    try:
        mailer.welcome(user["email"], user["name"])
    except Exception:                                          # noqa: BLE001
        pass
    session = accounts.rotate_session(request.session, user["id"])
    response = redirect("/account", flash="Account created. Welcome to MOG.")
    response.set_cookie(
        accounts.SESSION_COOKIE, accounts.session_cookie_value(session),
        max_age=config.session_days * 86400,
    )
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    if request.session:
        accounts.destroy_session(request.session["id"])
    response = redirect("/", flash="Signed out.")
    response.delete_cookie(accounts.SESSION_COOKIE)
    return response


# --------------------------------------------------------------- account

def _account_nav(request: Request, active: str) -> str:
    links = [
        ("/account", "Overview"),
        ("/account/orders", "Orders"),
        ("/account/addresses", "Addresses"),
        ("/account/settings", "Settings"),
    ]
    items = []
    for href, label in links:
        current = href == active
        state = 'is-active" aria-current="page' if current else ""
        items.append(f'<a href="{E(href)}" class="{state}">{E(label)}</a>')
    # Signing out must be a POST, so it cannot be triggered by a stray link.
    items.append(
        f'<form method="post" action="/logout" class="account__signout">'
        f'{csrf_input(request)}'
        f'<button class="btn btn--quiet btn--small" type="submit">Sign out</button>'
        f'</form>'
    )
    return '<nav class="account__nav" aria-label="Account">' + "".join(items) + "</nav>"


def _account_page(request: Request, title: str, active: str, body: str) -> Response:
    content = f"""
<div class="shell">
  {page_header(title, eyebrow="Account")}
  <div class="account section" style="padding-top:0">
    {_account_nav(request, active)}
    <div>{body}</div>
  </div>
</div>
"""
    return html_response(layout(request, content, title=title))


@router.get("/account")
def account_home(request: Request) -> Response:
    user = require_user(request)
    recent = orders.for_user(user["id"], limit=3)
    address = accounts.default_address(user["id"])
    order_rows = "".join(
        f"""
        <div class="summary__row">
          <span><a href="/account/orders">{E(o['number'])}</a>
            {badge(orders.STATUS_LABELS[o['status']], o['status'])}</span>
          <span>{money(o['total_cents'])}</span>
        </div>
        """
        for o in recent
    ) or '<p class="muted">No orders yet.</p>'

    body = f"""
<div class="split">
  <section>
    <h2 class="eyebrow">Recent orders</h2>
    {order_rows}
    <p style="margin-top:1.4rem">{button("All orders", href="/account/orders", variant="ghost")}</p>
  </section>
  <section>
    <h2 class="eyebrow">Default address</h2>
    {f'''<p>{E(address['name'])}<br>{E(address['line1'])}
         {(' ' + E(address['line2'])) if address['line2'] else ''}<br>
         {E(address['city'])}, {E(address['region'])} {E(address['postal'])}<br>
         {E(address['country'])}</p>''' if address
      else '<p class="muted">No address saved yet.</p>'}
    <p style="margin-top:1.4rem">
      {button("Manage addresses", href="/account/addresses", variant="ghost")}
    </p>
  </section>
</div>
"""
    return _account_page(request, f"Hello, {user['name'].split()[0] if user['name'] else 'there'}",
                         "/account", body)


@router.get("/account/orders")
def account_orders(request: Request) -> Response:
    user = require_user(request)
    rows = orders.for_user(user["id"])
    if not rows:
        body = empty_state(
            "No orders yet",
            "When you order, it will show up here with tracking.",
            button("Start shopping", href="/shop", variant="solid"),
        )
    else:
        blocks = []
        for order in rows:
            items = orders.items_for(order["id"])
            item_html = "".join(
                f"""
                <div class="line-item">
                  <span class="line-item__media">
                    <img src="{E(art.media_url(i['art_seed']))}" alt=""
                         width="800" height="1000" loading="lazy">
                  </span>
                  <span>
                    <a class="line-item__title" href="/product/{E(i['slug'])}">{E(i['title'])}</a>
                    <span class="line-item__meta">{E(i['variant_label'])} · Qty {i['quantity']}</span>
                  </span>
                  <span class="line-item__price">{money(i['unit_cents'] * i['quantity'])}</span>
                </div>
                """
                for i in items
            )
            tracking = ""
            if order["tracking_number"]:
                tracking = (f'<p class="muted">{E(order["tracking_carrier"])} · '
                            f'<strong>{E(order["tracking_number"])}</strong></p>')
            blocks.append(f"""
<section class="panel" style="margin-bottom:1.5rem">
  <div class="panel__head">
    <div>
      <strong>{E(order['number'])}</strong>
      <span class="muted"> · {E(order['created_at'][:10])}</span>
    </div>
    <div class="cluster">
      {badge(orders.STATUS_LABELS[order['status']], order['status'])}
      <strong>{money(order['total_cents'])}</strong>
    </div>
  </div>
  <div class="panel__body">
    <div class="line-items">{item_html}</div>
    {tracking}
  </div>
</section>
""")
        body = "".join(blocks)
    return _account_page(request, "Orders", "/account/orders", body)


@router.get("/account/addresses")
def account_addresses(request: Request, error: str = "") -> Response:
    user = require_user(request)
    saved = accounts.addresses_for(user["id"])
    cards = "".join(
        f"""
        <div class="panel" style="margin-bottom:1rem">
          <div class="panel__body">
            <p>{E(a['name'])}<br>{E(a['line1'])}
               {(' ' + E(a['line2'])) if a['line2'] else ''}<br>
               {E(a['city'])}, {E(a['region'])} {E(a['postal'])}<br>{E(a['country'])}</p>
            <div class="cluster" style="margin-top:1rem">
              {badge("Default", "paid") if a['is_default'] else ""}
              <form method="post" action="/account/addresses/delete"
                    data-confirm="Delete this address?">
                {csrf_input(request)}
                <input type="hidden" name="address_id" value="{a['id']}">
                <button class="btn btn--quiet btn--small" type="submit">Delete</button>
              </form>
            </div>
          </div>
        </div>
        """
        for a in saved
    ) or '<p class="muted">No addresses saved yet.</p>'

    body = f"""
<div class="split">
  <section>
    <h2 class="eyebrow">Saved</h2>
    {cards}
  </section>
  <section>
    <h2 class="eyebrow">Add an address</h2>
    <form method="post" action="/account/addresses" class="stack">
      {csrf_input(request)}
      {f'<div class="form-error">{E(error)}</div>' if error else ""}
      {address_fields(request)}
      {button("Save address", type_="submit", variant="solid")}
    </form>
  </section>
</div>
"""
    return _account_page(request, "Addresses", "/account/addresses", body)


def address_fields(request: Request, prefill: Any = None,
                   errors: dict[str, str] | None = None) -> str:
    """The shared US shipping-address block, used by account and checkout."""
    errors = errors or {}
    def value(key: str, fallback: str = "") -> str:
        if request.method == "POST":
            return request.get(key, fallback)
        if prefill is not None:
            try:
                return prefill[key] or fallback
            except (KeyError, IndexError, TypeError):
                return fallback
        return fallback

    user_name = request.user["name"] if request.user else ""
    return f"""
{field("name", "Full name", required=True, autocomplete="name",
       value=value("name", user_name), error=errors.get("name", ""))}
{field("line1", "Address", required=True, autocomplete="address-line1",
       value=value("line1"), error=errors.get("line1", ""))}
{field("line2", "Apartment, suite (optional)", autocomplete="address-line2",
       value=value("line2"))}
<div class="field-row">
  {field("city", "City", required=True, autocomplete="address-level2",
         value=value("city"), error=errors.get("city", ""))}
  {field("region", "State", required=True, autocomplete="address-level1",
         value=value("region"), error=errors.get("region", ""),
         options=[("", "Select")] + [(s, s) for s in US_STATES])}
  {field("postal", "ZIP code", required=True, autocomplete="postal-code",
         value=value("postal"), error=errors.get("postal", ""),
         inputmode="numeric")}
</div>
{field("phone", "Phone (for delivery updates)", type_="tel",
       autocomplete="tel", value=value("phone"))}
<input type="hidden" name="country" value="US">
"""


def validate_address(request: Request) -> tuple[dict[str, str], dict[str, str]]:
    values = {
        key: request.get(key) for key in
        ("name", "line1", "line2", "city", "region", "postal", "phone")
    }
    values["country"] = "US"
    errors: dict[str, str] = {}
    if not values["name"]:
        errors["name"] = "Required."
    if not values["line1"]:
        errors["line1"] = "Required."
    if not values["city"]:
        errors["city"] = "Required."
    if values["region"] not in US_STATES:
        errors["region"] = "Select a state."
    postal = values["postal"].replace(" ", "")
    if not (postal.isdigit() and len(postal) in (5, 9)):
        errors["postal"] = "Enter a 5-digit ZIP code."
    return values, errors


@router.post("/account/addresses")
def account_address_create(request: Request) -> Response:
    user = require_user(request)
    values, errors = validate_address(request)
    if errors:
        return account_addresses(request, error="Check the highlighted fields.")
    accounts.save_address(user["id"], make_default=True, **values)
    return redirect("/account/addresses", flash="Address saved.")


@router.post("/account/addresses/delete")
def account_address_delete(request: Request) -> Response:
    user = require_user(request)
    accounts.delete_address(user["id"], request.get_int("address_id"))
    return redirect("/account/addresses", flash="Address deleted.")


@router.get("/account/settings")
def account_settings(request: Request, error: str = "") -> Response:
    user = require_user(request)
    body = f"""
<div class="split">
  <section>
    <h2 class="eyebrow">Profile</h2>
    <form method="post" action="/account/settings" class="stack">
      {csrf_input(request)}
      {field("name", "Name", value=user["name"], autocomplete="name")}
      {field("email", "Email", value=user["email"], type_="email", disabled=True,
             hint="Email changes are handled by support to protect your orders.")}
      <label class="cluster" style="font-size:.8rem;color:var(--muted);margin-bottom:1rem">
        <input type="checkbox" name="marketing_opt_in" value="1"
               {'checked' if user['marketing_opt_in'] else ''} style="width:auto;margin:0">
        <span>Email me about drops and restocks.</span>
      </label>
      {button("Save changes", type_="submit", variant="solid")}
    </form>
  </section>
  <section>
    <h2 class="eyebrow">Password</h2>
    <form method="post" action="/account/password" class="stack">
      {csrf_input(request)}
      {f'<div class="form-error">{E(error)}</div>' if error else ""}
      {field("current", "Current password", type_="password", required=True,
             autocomplete="current-password")}
      {field("replacement", "New password", type_="password", required=True,
             autocomplete="new-password", hint="At least 10 characters.")}
      {button("Update password", type_="submit", variant="ghost")}
      <p class="form-note">Changing your password signs you out everywhere else.</p>
    </form>
  </section>
</div>
"""
    return _account_page(request, "Settings", "/account/settings", body)


@router.post("/account/settings")
def account_settings_save(request: Request) -> Response:
    user = require_user(request)
    accounts.update_profile(
        user["id"], name=request.get("name"),
        marketing_opt_in=request.checked("marketing_opt_in"),
    )
    return redirect("/account/settings", flash="Profile updated.")


@router.post("/account/password")
def account_password(request: Request) -> Response:
    user = require_user(request)
    try:
        accounts.change_password(
            user["id"], request.get_raw("current"), request.get_raw("replacement")
        )
    except accounts.AuthError as exc:
        return account_settings(request, error=str(exc))
    response = redirect("/login", flash="Password updated. Please sign in again.")
    response.delete_cookie(accounts.SESSION_COOKIE)
    return response
