"""Pre-launch: the countdown page, and the gate that hides the site until then.

Until the opening time in `config.preorder_opens_at`, the public sees one page:
the "Preorders Open In" countdown with a Notify Me sign-up.  Every other
address sends them back to it.  At zero the page switches to "Preorders Are
Open" and takes visitors into the full website -- no one has to flip a switch.
Signed-in staff and admins browse the whole site throughout, with a slim bar
reminding them the public cannot see it yet.

Notify Me runs in two modes (config.preorder_forms):

DEMO -- the default everywhere except production
    Validated and acknowledged exactly like the real thing, but nothing is
    saved and nothing is sent; the confirmation message says so.

LIVE -- MOG_PREORDER_FORMS=live, or automatically with MOG_ENV=production
    accounts.subscribe_newsletter(source="preorder"): the same `newsletter`
    list the footer "Join" form fills, exported from Admin -> Customers.

See docs/PREORDER.md.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from . import accounts, preorder
from .config import config
from .security import HONEYPOT_FIELD, TIMESTAMP_FIELD, audit, rate_limit, spam_signals
from .ui import E, button, collection_notice, csrf_input, icon, layout, spam_trap, wordmark
from .web import Request, Response, Router, html_response, json_response, redirect

router = Router()

NOTIFY_DONE = "You’re on the list. Preorder news goes to {email}."
NOTIFY_DONE_SHORT = "You’re on the list."
DEMO_NOTE_NOTIFY = ("Preview only: on the live store this address joins the "
                    "mailing list. Nothing was saved or sent.")

# Reachable while the site is hidden: the countdown page's own needs (assets,
# Notify Me, the privacy policy its consent notice links to), staff sign-in
# and the admin console, Stripe's server-to-server webhook, and unsubscribe
# links that must always work.
OPEN_PATHS = frozenset({"/favicon.ico", "/healthz", "/robots.txt", "/privacy"})
OPEN_PREFIXES = ("/static/", "/media/", "/admin", "/login", "/logout",
                 "/webhooks/", "/preorder/", "/newsletter/unsubscribe")
STAFF_ROLES = ("staff", "admin")
STAFF_COUNTDOWN_PARAM = "view"          # /?view=countdown -- staff see the public page


# -------------------------------------------------------------------- gate

def _simulate(request: Request) -> str:
    return (request.query.get(preorder.SIMULATE_PARAM) or [""])[0]


def gate(request: Request, nxt) -> Response:
    """Middleware: the public sees only the countdown until preorders open."""
    cd = preorder.countdown(simulate=_simulate(request))
    if cd.is_open:
        return nxt(request)

    path = request.path
    user = request.user
    is_staff = bool(user and user["role"] in STAFF_ROLES)
    wants_countdown = (request.query.get(STAFF_COUNTDOWN_PARAM) or [""])[0] == "countdown"

    if path == "/" and request.method in ("GET", "HEAD") and (not is_staff or wants_countdown):
        return countdown_page(request, cd)
    if is_staff:
        request.launch_preview = cd             # ui.layout shows the staff bar
        return nxt(request)
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return nxt(request)

    response = redirect("/", status=302 if request.method in ("GET", "HEAD") else 303)
    response.headers["Cache-Control"] = "no-store"
    return response


# ------------------------------------------------------------------ markup

def _email_field(value: str, error: str, autofocus: bool) -> str:
    """Labelled email input with Notify Me beside it, wired like ui.field()."""
    fid = "notify-email"
    describedby = f' aria-describedby="{fid}-error"' if error else ""
    invalid = ' aria-invalid="true"' if error else ""
    focus = " autofocus" if autofocus else ""
    return f"""
<div class="field{' field--error' if error else ''}" data-field="email">
  <label for="{fid}">Email address <abbr title="required">*</abbr></label>
  <div class="field__row">
    <input id="{fid}" type="email" name="email" value="{E(value)}" required
           autocomplete="email" inputmode="email" spellcheck="false"
           placeholder="you@example.com"
           data-msg-required="{E(preorder.EMAIL_REQUIRED)}"
           data-msg-format="{E(preorder.EMAIL_INVALID)}"{describedby}{invalid}{focus}>
    <button class="btn btn--solid" type="submit">Notify Me</button>
  </div>
  <p class="field__error" id="{fid}-error" data-error-for="email"{'' if error else ' hidden'}>{E(error)}</p>
</div>"""


def _countdown_units(cd: preorder.Countdown) -> str:
    days, hours, minutes, seconds = cd.parts()
    units = (("days", "Days", str(days)), ("hours", "Hours", f"{hours:02d}"),
             ("minutes", "Minutes", f"{minutes:02d}"),
             ("seconds", "Seconds", f"{seconds:02d}"))
    return "".join(
        f'<div class="countdown__unit"><span class="countdown__value" '
        f'data-unit="{key}">{value}</span>'
        f'<span class="countdown__label">{label}</span></div>'
        for key, label, value in units
    )


def _reveal_url(cd: preorder.Countdown) -> str:
    """Where visitors go when the clock runs out."""
    if cd.simulated:                     # local review: let the reveal through once
        return f"/?{preorder.SIMULATE_PARAM}=open"
    return preorder.destination() or "/"


def _flag(cd: preorder.Countdown) -> tuple[str, str]:
    """A 'simulated' tag and note, only on a reviewer's local simulation."""
    if cd.simulated:
        return ('<span class="preorder__flag">Simulated for review</span>',
                '<p class="preorder__setting-note">This countdown is simulated by the '
                f'<code>?{preorder.SIMULATE_PARAM}=</code> link and only works on a '
                'local preview. <a href="/">Show the configured date</a>.</p>')
    return "", ""


def countdown_page(request: Request, cd: preorder.Countdown,
                   form_state: dict[str, Any] | None = None) -> Response:
    form_state = form_state or {}
    errors = form_state.get("errors", {})
    values = form_state.get("values", {})
    flag, flag_note = _flag(cd)

    if not cd.scheduled:
        # The setting could not be read: the site stays hidden (fail closed),
        # and the page says so plainly instead of counting to a wrong time.
        setting_error = "" if config.is_production else (
            '<p class="preorder__setting-note">Setting error: '
            '<code>MOG_PREORDER_OPENS_AT</code> must look like '
            f'<code>{E(preorder.SETTING_EXAMPLE)}</code>.</p>')
        clock = f"""
      <h1 class="display preorder__title" id="preorder-title">Preorders Open Soon</h1>
      <p class="preorder__lede">Date to be announced. Join the list and you hear first.</p>
      {setting_error}"""
        title = "Preorders open soon"
    else:
        reveal = _reveal_url(cd)
        enter = ("Shop the preorder" if preorder.destination() and not cd.simulated
                 else "Enter the site")
        heading = "Preorders Are Open" if cd.is_open else "Preorders Open In"
        clock = f"""
      <h1 class="display preorder__title" id="preorder-title" data-preorder-title>{heading}</h1>
      <div class="countdown" role="timer" aria-describedby="preorder-when"
           data-countdown-panel{' hidden' if cd.is_open else ''}>
        {_countdown_units(cd)}
      </div>
      <div class="preorder__state" data-open-panel{'' if cd.is_open else ' hidden'}>
        <p class="preorder__lede">The doors are open. Taking you in now.</p>
        <div class="preorder__cta">{button(enter, href=reveal, variant="solid")}</div>
      </div>
      <p class="preorder__when" id="preorder-when">
        <span data-when-prefix>{'Opened' if cd.is_open else 'Opens'}</span>
        <time datetime="{E(cd.opens_at_utc_iso())}">{E(cd.opens_label())}</time>
      </p>
      <p class="preorder__local" data-local-time hidden></p>
      <p class="visually-hidden" role="status" data-countdown-announce></p>
      {flag_note}"""
        title = f"Preorders open {cd.short_label()}"

    data = ""
    if cd.scheduled:
        data = (f' data-opens-at="{E(cd.opens_at_utc_iso())}"'
                f' data-server-now="{E(cd.now_utc_iso())}"'
                f' data-zone-offset="{cd.zone_offset_minutes()}"'
                f' data-reveal-url="{E(_reveal_url(cd))}"')

    form_error = errors.get("_", "")
    social = "".join(
        f'<a href="{E(url)}" rel="me noopener" target="_blank">{E(label)}</a>'
        for _, url, label in config.social_links
    )
    content = f"""
<div class="launch" data-preorder{data}>
  <div class="launch__media" aria-hidden="true">
    <picture>
      <source media="(max-width: 700px)" srcset="/static/img/campaign-tall.jpg">
      <img src="/static/img/campaign-wide.jpg" alt=""
           width="2000" height="1125" fetchpriority="high" decoding="async">
    </picture>
  </div>
  <header class="launch__top">{wordmark()}</header>

  <section class="launch__body" aria-labelledby="preorder-title">
    <div class="preorder__clock">
      <p class="eyebrow preorder__eyebrow"><span>Preorder</span>{flag}</p>
      {clock}
    </div>

    <div class="preorder__notify" id="notify">
      <h2 class="preorder__subhead">Be first in line.</h2>
      <p class="preorder__copy">One email the moment preorders open. Drops and
        restocks after that. No noise.</p>
      <form class="preorder-form" action="/preorder/notify" method="post"
            data-preorder-form="notify">
        {csrf_input(request)}
        {spam_trap("notify-")}
        <div class="form-error" role="alert" data-form-error{'' if form_error else ' hidden'}>{E(form_error)}</div>
        {_email_field(values.get("email", ""), errors.get("email", ""), bool(errors))}
      </form>
      {collection_notice("preorder")}
      <div class="form-status" data-form-status role="status" aria-live="polite"></div>
    </div>
  </section>

  <footer class="launch__foot">
    <span>© 2026 MOG Lifestyle</span>
    <nav class="launch__links" aria-label="Footer">
      <a href="/privacy">Privacy</a><a href="/?ad_settings=1">Ad measurement choices</a>{social}
    </nav>
  </footer>
</div>
"""
    response = html_response(layout(
        request, content, title=title, chrome=False, body_class="body--launch",
        canonical="/", scripts=("/static/js/preorder.js",),
        description="MOG Lifestyle preorders. Performance apparel and training "
                    "essentials, cut in black and white. Join the list to hear "
                    "the moment preorders open.",
    ))
    # Never let a browser or proxy keep serving this page after launch.
    response.headers["Cache-Control"] = "no-store"
    return response


def staff_bar(request: Request) -> str:
    """Shown on every page to signed-in staff while the public is held back."""
    cd = getattr(request, "launch_preview", None)
    if cd is None:
        return ""
    when = f"until {cd.opens_label()}" if cd.scheduled else "until the opening date is set"
    return (
        '<div class="launch-bar" role="note">'
        f'<span>{icon("user", size=14)} Staff preview — the public sees only the '
        f'countdown page {E(when)}.</span>'
        '<a href="/?view=countdown">View the countdown page</a></div>'
    )


# ------------------------------------------------------------------ routes

@router.post("/preorder/notify")
def notify_submit(request: Request) -> Response:
    live = preorder.forms_live()
    email = request.get("email")[:254]
    errors = preorder.notify_errors(email)
    if (not errors and live
            and not rate_limit(f"preorder-notify:{request.remote_addr}",
                               limit=6, per_seconds=600)):
        errors = {"_": "Too many attempts. Try again in a few minutes."}
    if errors:
        if request.wants_json:
            return json_response({"ok": False, "errors": errors,
                                  "error": errors.get("_", "")}, status=422)
        return countdown_page(request, preorder.countdown(),
                              {"errors": errors, "values": {"email": email}})

    note = "" if live else DEMO_NOTE_NOTIFY
    signal = spam_signals(request.get(HONEYPOT_FIELD), request.get_raw(TIMESTAMP_FIELD))
    new_signup = False
    if signal:
        # Answer a bot exactly as a person, so it learns nothing.
        if live:
            audit("spam.blocked", actor=email, subject="preorder-notify",
                  detail=signal, ip=request.remote_addr)
    elif live:
        new_signup = accounts.subscribe_newsletter(
            email, source="preorder", ip=request.remote_addr,
            user_agent=request.headers.get("User-Agent", ""),
        )
    if request.wants_json:
        return json_response({"ok": True, "note": note,
                              "meta_event_id": uuid4().hex if new_signup else "",
                              "message": NOTIFY_DONE.format(email=email)})
    return redirect("/#notify", flash=f"{NOTIFY_DONE_SHORT} {note}".strip())
