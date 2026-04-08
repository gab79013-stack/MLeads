"""
utils/billing.py
━━━━━━━━━━━━━━━━
Thin Stripe wrapper with graceful degradation.

Goals:
  1. Create Checkout sessions so a bot_user can subscribe to the Pro plan
  2. Verify and apply webhook events (checkout.session.completed,
     customer.subscription.deleted, invoice.payment_failed, etc.)
  3. Cleanly no-op when Stripe isn't configured (missing keys → all
     functions return None / False). The rest of the app keeps working.

Environment:
  STRIPE_API_KEY           Secret key (sk_live_... or sk_test_...)
  STRIPE_PRICE_ID          Price ID for the $99/mo plan
  STRIPE_WEBHOOK_SECRET    Used by the webhook handler
  STRIPE_SUCCESS_URL       Where Stripe redirects after a successful checkout
  STRIPE_CANCEL_URL        Where Stripe redirects on cancel
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _api_key() -> str:
    return os.getenv("STRIPE_API_KEY", "")


def _price_id() -> str:
    return os.getenv("STRIPE_PRICE_ID", "")


def is_configured() -> bool:
    return bool(_api_key() and _price_id())


def _stripe():
    """Lazy import so the module works even if stripe isn't installed."""
    if not _api_key():
        return None
    try:
        import stripe  # type: ignore
    except ImportError:
        logger.warning("[billing] stripe package not installed — billing disabled")
        return None
    stripe.api_key = _api_key()
    return stripe


# ─────────────────────────────────────────────────────
# Checkout
# ─────────────────────────────────────────────────────

def get_checkout_url(bot_user: dict) -> str | None:
    """
    Return a Stripe Checkout URL for this bot_user, creating a Session
    on demand. Returns None if Stripe isn't configured.
    """
    stripe = _stripe()
    if not stripe or not is_configured():
        logger.info("[billing] Stripe not configured — returning None checkout URL")
        return None

    success_url = os.getenv("STRIPE_SUCCESS_URL", "https://t.me/")
    cancel_url = os.getenv("STRIPE_CANCEL_URL", "https://t.me/")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": _price_id(), "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "bot_user_id": str(bot_user.get("id") or ""),
                "chat_id": str(bot_user.get("chat_id") or ""),
            },
            client_reference_id=str(bot_user.get("chat_id") or ""),
        )
        return session.url
    except Exception as e:
        logger.error(f"[billing] create checkout error: {e}")
        return None


# ─────────────────────────────────────────────────────
# Webhook verification + dispatch
# ─────────────────────────────────────────────────────

def verify_webhook(payload: bytes, signature: str) -> dict | None:
    """
    Verify a Stripe webhook signature and return the parsed event.
    Returns None on signature mismatch / missing config.
    """
    stripe = _stripe()
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    if not stripe or not secret:
        return None
    try:
        return stripe.Webhook.construct_event(payload, signature, secret)
    except Exception as e:
        logger.warning(f"[billing] webhook signature verification failed: {e}")
        return None


def handle_event(event: dict) -> bool:
    """
    Apply a Stripe event to our bot_users table. Returns True if handled.
    Import bot_users lazily so this module is cheap to import.
    """
    from utils import bot_users as bu

    event_type = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    if event_type == "checkout.session.completed":
        chat_id = data.get("client_reference_id") or (data.get("metadata") or {}).get("chat_id")
        customer = data.get("customer")
        subscription = data.get("subscription")
        if not chat_id:
            return False
        # Default to 30 days from now; webhook invoice.paid events will keep it accurate.
        until = datetime.utcnow() + timedelta(days=30)
        bu.mark_paid(chat_id, until, stripe_customer_id=customer, stripe_subscription_id=subscription)
        logger.info(f"[billing] checkout.session.completed applied to chat_id={chat_id}")
        return True

    if event_type in ("invoice.paid", "invoice.payment_succeeded"):
        customer = data.get("customer")
        period_end = data.get("lines", {}).get("data", [{}])[0].get("period", {}).get("end")
        if not customer or not period_end:
            return False
        _extend_paid_until(customer, period_end)
        return True

    if event_type in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer = data.get("customer")
        _mark_customer_expired(customer)
        return True

    return False


def _extend_paid_until(stripe_customer_id: str, period_end_epoch: int) -> None:
    from utils.web_db import get_db_connection

    new_until = datetime.utcfromtimestamp(int(period_end_epoch)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'paid',
               paid_until = ?,
               is_active = 1,
               updated_at = CURRENT_TIMESTAMP
         WHERE stripe_customer_id = ?
        """,
        (new_until, stripe_customer_id),
    )
    conn.commit()
    conn.close()
    logger.info(f"[billing] extended paid_until={new_until} for customer={stripe_customer_id}")


def _mark_customer_expired(stripe_customer_id: str) -> None:
    from utils.web_db import get_db_connection

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'expired',
               updated_at = CURRENT_TIMESTAMP
         WHERE stripe_customer_id = ?
        """,
        (stripe_customer_id,),
    )
    conn.commit()
    conn.close()
    logger.info(f"[billing] marked expired for customer={stripe_customer_id}")
