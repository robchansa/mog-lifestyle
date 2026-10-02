"""Test package.

Importing this package configures an isolated environment *before* anything
from `app` is imported, so the suite never touches the development database
or a real Stripe/SMTP account.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="mog-tests-"))

os.environ.setdefault("MOG_ENV", "test")
os.environ.setdefault("MOG_DB", str(_TMP / "test.sqlite3"))
os.environ.setdefault("MOG_SECRET_KEY", "test-secret-key-not-for-production")
# Same KDF, far fewer rounds: keeps the suite fast without changing behaviour.
os.environ.setdefault("MOG_PBKDF2_ROUNDS", "1000")
# The honeypot still applies; only the "too fast to be human" clock is relaxed,
# so the suite does not have to sleep two seconds before every form post.
os.environ.setdefault("MOG_MIN_FORM_SECONDS", "0")
os.environ.setdefault("MOG_BASE_URL", "http://testserver")
os.environ.setdefault("MOG_SHIPPING_CENTS", "800")
os.environ.setdefault("MOG_FREE_SHIPPING_CENTS", "15000")
os.environ.setdefault("MOG_TAX_BPS", "0")
# Explicitly empty: payments run in demo mode, email in console mode.
os.environ.setdefault("STRIPE_SECRET_KEY", "")
os.environ.setdefault("SMTP_HOST", "")

TMP_DIR = _TMP
