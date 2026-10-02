"""A small, dependency-free Stripe client.

The brief names Stripe as the required integration.  Rather than vendor the
official SDK, this speaks the REST API directly over `urllib` -- form-encoded
requests, bearer auth, idempotency keys -- and verifies webhook signatures with
`hmac`.

When ``STRIPE_SECRET_KEY`` is unset the module runs in **demo mode**: checkout
returns an internal confirmation URL so the whole purchase flow is walkable on
a laptop with no Stripe account.  `config.payments_live` is the switch.
"""
from __future__ import annotations

import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Mapping

from .config import config
from .security import new_token

API_BASE = "https://api.stripe.com/v1"
API_VERSION = "2024-06-20"
TIMEOUT = 20
WEBHOOK_TOLERANCE_SECONDS = 300


class StripeError(Exception):
    def __init__(self, message: str, *, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = code
        self.status = status


# --------------------------------------------------------------- encoding

def encode_form(data: Mapping[str, Any], prefix: str = "") -> list[tuple[str, str]]:
    """Flatten nested dicts/lists into Stripe's bracket notation."""
    pairs: list[tuple[str, str]] = []
    for key, value in data.items():
        field = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            pairs.extend(encode_form(value, field))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    pairs.extend(encode_form(item, f"{field}[{index}]"))
                else:
                    pairs.append((f"{field}[{index}]", str(item)))
        elif isinstance(value, bool):
            pairs.append((field, "true" if value else "false"))
        else:
            pairs.append((field, str(value)))
    return pairs


def _request(method: str, path: str, data: Mapping[str, Any] | None = None,
             *, idempotency_key: str = "") -> dict:
    if not config.stripe_secret_key:
        raise StripeError("Stripe is not configured (STRIPE_SECRET_KEY is unset).")

    body = urllib.parse.urlencode(encode_form(data or {})).encode()
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body if method != "GET" else None,
        method=method,
    )
    request.add_header("Authorization", f"Bearer {config.stripe_secret_key}")
    request.add_header("Stripe-Version", API_VERSION)
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("User-Agent", "MOG-Lifestyle/1.0 (+https://moglifestyle.fit)")
    if idempotency_key:
        request.add_header("Idempotency-Key", idempotency_key)

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(raw).get("error", {})
        except ValueError:
            payload = {}
        raise StripeError(
            payload.get("message", f"Stripe returned {exc.code}."),
            code=payload.get("code", ""), status=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise StripeError(f"Could not reach Stripe: {exc.reason}") from exc


# --------------------------------------------------------------- checkout

def create_checkout_session(*, order_number: str, email: str,
                            line_items: Iterable[Mapping[str, Any]],
                            success_url: str, cancel_url: str,
                            shipping_cents: int = 0, discount_cents: int = 0,
                            metadata: Mapping[str, str] | None = None) -> dict:
    """Create a Stripe Checkout Session and return {id, url}.

    In demo mode this returns a synthetic session pointing at the local
    confirmation route so the flow stays testable end to end.
    """
    items = list(line_items)
    if not config.payments_live:
        fake_id = f"cs_demo_{new_token(12)}"
        joiner = "&" if "?" in success_url else "?"
        return {
            "id": fake_id,
            "url": f"{success_url}{joiner}demo=1&session_id={fake_id}",
            "demo": True,
        }

    payload: dict[str, Any] = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "customer_email": email,
        "client_reference_id": order_number,
        "line_items": [
            {
                "quantity": item["quantity"],
                "price_data": {
                    "currency": config.currency,
                    "unit_amount": item["unit_cents"],
                    "product_data": {
                        "name": item["name"],
                        "description": item.get("description") or None,
                    },
                },
            }
            for item in items
        ],
        "metadata": dict(metadata or {}, order_number=order_number),
        "payment_intent_data": {
            "description": f"MOG Lifestyle {order_number}",
            "metadata": {"order_number": order_number},
        },
        "automatic_tax": {"enabled": False},
        "billing_address_collection": "auto",
        "expires_at": int(time.time()) + 30 * 60,
    }
    if shipping_cents:
        payload["shipping_options"] = [{
            "shipping_rate_data": {
                "type": "fixed_amount",
                "fixed_amount": {"amount": shipping_cents, "currency": config.currency},
                "display_name": "Standard shipping",
            },
        }]
    if discount_cents:
        # Stripe Checkout needs a coupon object for order-level reductions.
        coupon = _request(
            "POST", "/coupons",
            {"amount_off": discount_cents, "currency": config.currency,
             "duration": "once", "name": "Promotion"},
            idempotency_key=f"coupon-{order_number}",
        )
        payload["discounts"] = [{"coupon": coupon["id"]}]

    return _request("POST", "/checkout/sessions", payload,
                    idempotency_key=f"checkout-{order_number}")


def retrieve_checkout_session(session_id: str) -> dict:
    if not config.payments_live or session_id.startswith("cs_demo_"):
        return {"id": session_id, "payment_status": "paid",
                "payment_intent": f"pi_demo_{session_id[-10:]}", "demo": True}
    return _request("GET", f"/checkout/sessions/{urllib.parse.quote(session_id)}")


def refund(payment_intent: str, *, amount_cents: int | None = None,
           reason: str = "requested_by_customer") -> dict:
    if not config.payments_live or payment_intent.startswith("pi_demo_"):
        return {"id": f"re_demo_{new_token(8)}", "status": "succeeded", "demo": True}
    payload: dict[str, Any] = {"payment_intent": payment_intent, "reason": reason}
    if amount_cents is not None:
        payload["amount"] = amount_cents
    return _request("POST", "/refunds", payload,
                    idempotency_key=f"refund-{payment_intent}-{amount_cents or 'full'}")


# --------------------------------------------------------------- webhooks

def parse_signature_header(header: str) -> tuple[int, list[str]]:
    timestamp = 0
    signatures: list[str] = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def verify_webhook(payload: bytes, signature_header: str, secret: str,
                   *, tolerance: int = WEBHOOK_TOLERANCE_SECONDS,
                   now: float | None = None) -> dict:
    """Validate a Stripe webhook and return its decoded event.

    Raises StripeError on a missing, malformed, stale or forged signature.
    """
    if not secret:
        raise StripeError("Webhook secret is not configured.")
    timestamp, signatures = parse_signature_header(signature_header)
    if not timestamp or not signatures:
        raise StripeError("Missing or malformed Stripe-Signature header.")

    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance:
        raise StripeError("Webhook timestamp is outside the tolerance window.")

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, "sha256").hexdigest()
    if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
        raise StripeError("Webhook signature verification failed.")

    try:
        return json.loads(payload.decode())
    except ValueError as exc:
        raise StripeError("Webhook payload was not valid JSON.") from exc
