"""Transactional email.

Every message is written to the `email_outbox` table first and then handed to
a transport, so the admin can always see exactly what the store sent -- and
nothing is silently lost when SMTP is down.  With no SMTP host configured the
console transport prints messages, which keeps local development honest.
"""
from __future__ import annotations

import html
import smtplib
import sys
import threading
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Mapping

from . import db
from .config import config

_send_lock = threading.Lock()


# --------------------------------------------------------------- queueing

def queue(to_address: str, subject: str, body_text: str, *,
          body_html: str = "", template: str = "") -> int:
    with db.tx():
        return db.insert(
            "email_outbox", to_address=to_address.strip(), subject=subject,
            body_text=body_text, body_html=body_html, template=template,
        )


def send(to_address: str, subject: str, body_text: str, *,
         body_html: str = "", template: str = "") -> int:
    """Queue then immediately attempt delivery.  Never raises."""
    message_id = queue(to_address, subject, body_text,
                       body_html=body_html, template=template)
    flush(message_id)
    return message_id


def flush(message_id: int | None = None, limit: int = 25) -> int:
    """Attempt delivery of queued mail.  Returns the number sent."""
    if message_id is not None:
        rows = db.query("SELECT * FROM email_outbox WHERE id = ?", (message_id,))
    else:
        rows = db.query(
            "SELECT * FROM email_outbox WHERE status = 'queued' "
            "ORDER BY id LIMIT ?", (limit,)
        )
    sent = 0
    for row in rows:
        try:
            _deliver(row)
        except Exception as exc:                      # noqa: BLE001
            with db.tx():
                db.update("email_outbox", "id = ?", (row["id"],),
                          status="failed", error=str(exc)[:500])
            continue
        with db.tx():
            db.update("email_outbox", "id = ?", (row["id"],),
                      status="sent", error="", sent_at=_now())
        sent += 1
    return sent


def _deliver(row: Any) -> None:
    message = EmailMessage()
    name, address = parseaddr(config.mail_from)
    message["From"] = formataddr((name or "MOG Lifestyle", address))
    message["To"] = row["to_address"]
    message["Subject"] = row["subject"]
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(row["body_text"])
    if row["body_html"]:
        message.add_alternative(row["body_html"], subtype="html")

    if not config.email_live:
        with _send_lock:
            sys.stderr.write(
                f"\n─── email ─────────────────────────────────────────────\n"
                f"To:      {row['to_address']}\n"
                f"Subject: {row['subject']}\n\n{row['body_text']}\n"
                f"───────────────────────────────────────────────────────\n\n"
            )
        return

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=20) as smtp:
        smtp.ehlo()
        if config.smtp_starttls:
            smtp.starttls()
            smtp.ehlo()
        if config.smtp_user:
            smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(message)


def _now() -> str:
    return db.scalar("SELECT datetime('now')")


# -------------------------------------------------------------- templates

_WRAPPER = """<!doctype html>
<html><body style="margin:0;background:#faf9f7;padding:32px 16px;
 font-family:-apple-system,Helvetica,Arial,sans-serif;color:#0b0b0c">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e2dfd9">
<tr><td style="padding:32px 32px 24px;border-bottom:1px solid #e2dfd9;text-align:center">
  <div style="font-family:Georgia,serif;font-size:26px;letter-spacing:8px">MOG</div>
  <div style="font-size:9px;letter-spacing:6px;color:#6c6c74;margin-top:6px">LIFESTYLE</div>
</td></tr>
<tr><td style="padding:32px">{body}</td></tr>
<tr><td style="padding:20px 32px;border-top:1px solid #e2dfd9;
 font-size:12px;color:#6c6c74;text-align:center">
  MOG Lifestyle · <a href="mailto:{email}" style="color:#6c6c74">{email}</a><br>
  <a href="{base}" style="color:#6c6c74">moglifestyle.fit</a>
</td></tr></table></body></html>"""


def render_html(body: str) -> str:
    return _WRAPPER.format(body=body, email=config.store_email, base=config.base_url)


def _rows_html(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f'<tr><td style="padding:6px 0;color:#6c6c74">{html.escape(label)}</td>'
        f'<td style="padding:6px 0;text-align:right">{html.escape(value)}</td></tr>'
        for label, value in rows
    )


def order_confirmation(order: Mapping[str, Any], items: list[Any],
                       *, to_address: str) -> int:
    from .ui import money
    number = order["number"]
    lines_text = "\n".join(
        f"  {item['quantity']} × {item['title']}"
        f"{' (' + item['variant_label'] + ')' if item['variant_label'] else ''}"
        f" — {money(item['unit_cents'] * item['quantity'])}"
        for item in items
    )
    totals = [
        ("Subtotal", money(order["subtotal_cents"])),
        *([("Discount", "-" + money(order["discount_cents"]))]
          if order["discount_cents"] else []),
        ("Shipping", money(order["shipping_cents"]) if order["shipping_cents"] else "Free"),
        *([("Tax", money(order["tax_cents"]))] if order["tax_cents"] else []),
        ("Total", money(order["total_cents"])),
    ]
    text = f"""Thank you — your order is confirmed.

Order {number}

{lines_text}

{chr(10).join(f'{label}: {value}' for label, value in totals)}

Shipping to
  {order['ship_name']}
  {order['ship_line1']}{(' ' + order['ship_line2']) if order['ship_line2'] else ''}
  {order['ship_city']}, {order['ship_region']} {order['ship_postal']}
  {order['ship_country']}

Track your order: {config.url('/account/orders')}

Questions? Reply to this email or write to {config.store_email}.

MOG Lifestyle
"""
    items_html = "".join(
        f'<tr><td style="padding:10px 0;border-bottom:1px solid #f0eeea">'
        f'<strong>{html.escape(item["title"])}</strong><br>'
        f'<span style="color:#6c6c74;font-size:13px">'
        f'{html.escape(item["variant_label"])} · Qty {item["quantity"]}</span></td>'
        f'<td style="padding:10px 0;border-bottom:1px solid #f0eeea;text-align:right">'
        f'{money(item["unit_cents"] * item["quantity"])}</td></tr>'
        for item in items
    )
    body = f"""
<h1 style="font-family:Georgia,serif;font-weight:400;font-size:24px;margin:0 0 8px">
  Thank you.</h1>
<p style="color:#6c6c74;margin:0 0 24px">
  Order <strong style="color:#0b0b0c">{html.escape(number)}</strong> is confirmed.
  We'll email tracking as soon as it ships.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="font-size:14px">{items_html}</table>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="font-size:14px;margin-top:16px">{_rows_html(totals)}</table>
<p style="margin:28px 0 8px;font-size:12px;letter-spacing:.14em;
  text-transform:uppercase;color:#6c6c74">Shipping to</p>
<p style="margin:0;font-size:14px;line-height:1.6">
  {html.escape(order['ship_name'])}<br>
  {html.escape(order['ship_line1'])}
  {(' ' + html.escape(order['ship_line2'])) if order['ship_line2'] else ''}<br>
  {html.escape(order['ship_city'])}, {html.escape(order['ship_region'])}
  {html.escape(order['ship_postal'])}<br>{html.escape(order['ship_country'])}</p>
<p style="margin-top:32px">
  <a href="{config.url('/account/orders')}"
     style="display:inline-block;background:#0b0b0c;color:#faf9f7;padding:14px 28px;
     text-decoration:none;font-size:12px;letter-spacing:.18em;text-transform:uppercase">
    View order</a></p>
"""
    return send(to_address, f"Order {number} confirmed", text,
                body_html=render_html(body), template="order_confirmation")


def shipping_notice(order: Mapping[str, Any], *, to_address: str) -> int:
    number = order["number"]
    carrier = order["tracking_carrier"] or "Carrier"
    tracking = order["tracking_number"]
    tracking_text = f"\n{carrier} tracking: {tracking}\n" if tracking else "\n"
    text = (f"Your MOG Lifestyle order {number} has shipped."
            f"{tracking_text}\n"
            f"Order details: {config.url('/account/orders')}\n\nMOG Lifestyle\n")
    body = f"""
<h1 style="font-family:Georgia,serif;font-weight:400;font-size:24px;margin:0 0 8px">
  It's on the way.</h1>
<p style="color:#6c6c74;margin:0 0 20px">
  Order <strong style="color:#0b0b0c">{html.escape(number)}</strong> shipped today.</p>
{f'<p style="font-size:14px">{html.escape(carrier)} tracking<br>'
 f'<strong style="font-size:16px;letter-spacing:.04em">{html.escape(tracking)}</strong></p>'
 if tracking else ''}
<p style="margin-top:28px"><a href="{config.url('/account/orders')}"
  style="display:inline-block;background:#0b0b0c;color:#faf9f7;padding:14px 28px;
  text-decoration:none;font-size:12px;letter-spacing:.18em;text-transform:uppercase">
  Track order</a></p>
"""
    return send(to_address, f"Order {number} has shipped", text,
                body_html=render_html(body), template="shipping_notice")


def low_stock_alert(variants: list[Any], *, to_address: str) -> int:
    lines = "\n".join(
        f"  {v['title']} — {v['sku']} — {v['available']} left" for v in variants
    )
    text = f"Low stock at MOG Lifestyle:\n\n{lines}\n\n{config.url('/admin/inventory')}\n"
    rows = "".join(
        f'<tr><td style="padding:6px 0">{html.escape(v["title"])} '
        f'<span style="color:#6c6c74">{html.escape(v["sku"])}</span></td>'
        f'<td style="padding:6px 0;text-align:right"><strong>{v["available"]}</strong></td></tr>'
        for v in variants
    )
    body = (f'<h1 style="font-family:Georgia,serif;font-weight:400;font-size:22px;'
            f'margin:0 0 16px">Low stock</h1>'
            f'<table role="presentation" width="100%" style="font-size:14px">{rows}</table>')
    return send(to_address, "Low stock alert", text,
                body_html=render_html(body), template="low_stock")


def welcome(email: str, name: str = "") -> int:
    greeting = f"Welcome, {name.split()[0]}." if name.strip() else "Welcome."
    text = (f"{greeting}\n\nYour MOG Lifestyle account is ready.\n\n"
            f"Shop: {config.url('/shop')}\n\nMOG Lifestyle\n")
    body = f"""
<h1 style="font-family:Georgia,serif;font-weight:400;font-size:24px;margin:0 0 8px">
  {html.escape(greeting)}</h1>
<p style="color:#6c6c74">Your account is ready. Orders, addresses and tracking
  now live in one place.</p>
<p style="margin-top:28px"><a href="{config.url('/shop')}"
  style="display:inline-block;background:#0b0b0c;color:#faf9f7;padding:14px 28px;
  text-decoration:none;font-size:12px;letter-spacing:.18em;text-transform:uppercase">
  Start shopping</a></p>
"""
    return send(email, "Welcome to MOG Lifestyle", text,
                body_html=render_html(body), template="welcome")


def contact_receipt(name: str, email: str, subject: str, message: str) -> None:
    """Acknowledge the sender and notify the store."""
    send(email, "We received your message",
         f"Hi {name},\n\nThanks for writing — we'll reply within one business day.\n\n"
         f"Your message:\n{message}\n\nMOG Lifestyle\n",
         body_html=render_html(
             f'<h1 style="font-family:Georgia,serif;font-weight:400;font-size:22px;'
             f'margin:0 0 12px">Message received</h1>'
             f'<p style="color:#6c6c74">Thanks for writing, '
             f'{html.escape(name)}. We reply within one business day.</p>'
             f'<blockquote style="margin:20px 0 0;padding-left:16px;'
             f'border-left:2px solid #e2dfd9;color:#6c6c74;font-size:14px">'
             f'{html.escape(message)}</blockquote>'),
         template="contact_receipt")
    send(config.store_email, f"[Contact] {subject or 'New message'}",
         f"From: {name} <{email}>\nSubject: {subject}\n\n{message}\n",
         template="contact_internal")
