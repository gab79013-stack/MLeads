"""
workers/telegram_bot.py
━━━━━━━━━━━━━━━━━━━━━━━
Interactive Telegram bot worker.

Runs in a background thread alongside the web app and long-polls
Telegram for private-chat messages from users. Implements:

  1. An onboarding flow
       /start  →  "which lead services do you want?" (inline buttons)
               →  "what is your city?" (text message)
               →  confirmation + trial activated
  2. Channel subscription tracking
       chat_member updates on the configured TELEGRAM_CHANNEL_ID start
       a 7-day free trial automatically.
  3. A small command set for active users
       /status  → trial/paid state
       /services, /city → re-run that onboarding step
       /upgrade → returns the Stripe checkout link (when configured)
  4. Persists the last processed update_id in `bot_state` so we never
     replay old updates across restarts.

The worker is optional. If TELEGRAM_BOT_TOKEN is missing or
BOT_WORKER_ENABLED=false the start_bot_worker() call is a no-op.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime

from utils import telegram as tg
from utils import bot_users as bu

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────

BOT_WORKER_ENABLED = os.getenv("BOT_WORKER_ENABLED", "true").lower() not in ("false", "0", "no")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")  # e.g. "-1001234567890"
POLL_TIMEOUT = int(os.getenv("BOT_POLL_TIMEOUT", "25"))


# ─────────────────────────────────────────────────────
# Keyboards
# ─────────────────────────────────────────────────────

def _services_keyboard(selected: list[str]) -> dict:
    """Build the inline keyboard for picking services. Selected ones get a ✅."""
    rows = []
    for key, label in bu.AVAILABLE_SERVICES:
        prefix = "✅ " if key in selected else ""
        rows.append([{
            "text": f"{prefix}{label}",
            "callback_data": f"svc:{key}",
        }])
    rows.append([{"text": "➡️ Done", "callback_data": "svc:done"}])
    return {"inline_keyboard": rows}


def _upgrade_keyboard(url: str | None) -> dict | None:
    if not url:
        return None
    return {"inline_keyboard": [[{"text": "💳 Upgrade to Pro ($99/mo)", "url": url}]]}


# ─────────────────────────────────────────────────────
# Message templates
# ─────────────────────────────────────────────────────

WELCOME_TEXT = (
    "👋 *Welcome to MLeads!*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "I hunt construction leads all over the Bay Area and deliver them to you "
    "in real time.\n\n"
    "Let's get you set up in 30 seconds.\n\n"
    "*Step 1 of 2 — Which services are you interested in?*\n"
    "Tap all that apply, then press *Done*."
)

CITY_PROMPT_TEXT = (
    "📍 *Step 2 of 2 — Your city*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Send me the name of your city (e.g. `San Francisco`, `Oakland`, `San Jose`).\n\n"
    "I'll find every lead that matches your services within *35 miles* of it."
)

ONBOARDING_COMPLETE_TEMPLATE = (
    "🎉 *You're all set, {name}!*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📌 *Services:* {services}\n"
    "📍 *City:* {city} (35 mi radius)\n"
    "✅ *Trial:* 7 days free — expires *{trial_ends}*\n\n"
    "New leads will arrive here automatically. You can always change your "
    "preferences with /services, /city or check /status."
)

STATUS_TEMPLATE = (
    "📊 *Your MLeads status*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "• Plan: *{plan}*\n"
    "• {expiry_line}\n"
    "• Services: {services}\n"
    "• City: {city}\n"
    "• Leads delivered: *{leads}*\n\n"
    "Commands: /services /city /upgrade /status"
)

TRIAL_EXPIRED_TEXT = (
    "⏰ *Your 7-day trial has ended*\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "To keep receiving leads, upgrade to the Pro plan — only *$99/month*.\n"
    "Tap the button below to subscribe."
)


# ─────────────────────────────────────────────────────
# Command / state handlers
# ─────────────────────────────────────────────────────

def _send(chat_id, text, reply_markup=None):
    ok = tg.send_message_to(chat_id, text, reply_markup=reply_markup)
    return ok


def _send_welcome(user: dict):
    chat_id = user["chat_id"]
    bu.set_state(chat_id, bu.STATE_AWAITING_SERVICES)
    _send(chat_id, WELCOME_TEXT, reply_markup=_services_keyboard(user.get("services") or []))


def _send_city_prompt(chat_id):
    bu.set_state(chat_id, bu.STATE_AWAITING_CITY)
    _send(chat_id, CITY_PROMPT_TEXT)


def _send_onboarding_complete(user: dict):
    services = user.get("services") or []
    services_labels = [label for key, label in bu.AVAILABLE_SERVICES if key in services] or ["—"]
    trial_end = user.get("trial_ends_at") or "—"
    text = ONBOARDING_COMPLETE_TEMPLATE.format(
        name=user.get("first_name") or user.get("username") or "there",
        services=", ".join(services_labels),
        city=user.get("city") or "—",
        trial_ends=trial_end,
    )
    _send(user["chat_id"], text)


def _send_status(user: dict):
    bu.update_subscription_status(user)
    user = bu.get_by_chat_id(user["chat_id"])  # reload
    status = user.get("subscription_status") or "none"

    if status == "paid":
        plan = "Pro ($99/mo)"
        expiry_line = f"Paid until: *{user.get('paid_until') or '—'}*"
    elif status == "trial":
        plan = "Free trial"
        expiry_line = f"Trial ends: *{user.get('trial_ends_at') or '—'}*"
    elif status == "expired":
        plan = "Expired"
        expiry_line = "Subscription ended — /upgrade to continue"
    else:
        plan = "Not started"
        expiry_line = "Run /start to begin your free trial"

    services = user.get("services") or []
    services_labels = [label for key, label in bu.AVAILABLE_SERVICES if key in services] or ["—"]

    text = STATUS_TEMPLATE.format(
        plan=plan,
        expiry_line=expiry_line,
        services=", ".join(services_labels),
        city=user.get("city") or "—",
        leads=user.get("leads_sent_count") or 0,
    )
    _send(user["chat_id"], text)


def _send_upgrade(user: dict):
    try:
        from utils import billing
        url = billing.get_checkout_url(user)
    except Exception as e:
        logger.warning(f"[bot] billing unavailable: {e}")
        url = None

    if url:
        _send(user["chat_id"], TRIAL_EXPIRED_TEXT, reply_markup=_upgrade_keyboard(url))
    else:
        _send(
            user["chat_id"],
            "💳 *Upgrade to Pro*\n━━━━━━━━━━━━━━━━━━━━\n"
            "Billing is not configured yet. Please contact support to activate "
            "your $99/mo subscription.",
        )


# ─────────────────────────────────────────────────────
# Update dispatch
# ─────────────────────────────────────────────────────

def _handle_message(message: dict) -> None:
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    text = (message.get("text") or "").strip()

    user = bu.upsert_from_telegram(sender, chat)
    chat_id = user["chat_id"]
    bu.log_message(user["id"], chat_id, "in", text, message_type="text")

    # ── Command dispatch ────────────────────────────────
    lower = text.lower()
    if lower.startswith("/start"):
        _send_welcome(user)
        return
    if lower.startswith("/services"):
        bu.set_state(chat_id, bu.STATE_AWAITING_SERVICES)
        _send(
            chat_id,
            "Which services? Tap all that apply, then press *Done*.",
            reply_markup=_services_keyboard(user.get("services") or []),
        )
        return
    if lower.startswith("/city"):
        _send_city_prompt(chat_id)
        return
    if lower.startswith("/status"):
        _send_status(user)
        return
    if lower.startswith("/upgrade"):
        _send_upgrade(user)
        return
    if lower.startswith("/help"):
        _send(
            chat_id,
            "*MLeads commands*\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "/start — onboarding\n"
            "/services — change services\n"
            "/city — change city / radius\n"
            "/status — subscription status\n"
            "/upgrade — get Pro ($99/mo)",
        )
        return

    # ── State machine ───────────────────────────────────
    state = user.get("state") or bu.STATE_NEW
    if state == bu.STATE_NEW:
        _send_welcome(user)
        return

    if state == bu.STATE_AWAITING_CITY and text:
        city = text.strip()
        bu.set_city(chat_id, city)
        # Complete onboarding: start trial if not already
        updated = bu.get_by_chat_id(chat_id)
        if not updated.get("trial_ends_at") and updated.get("subscription_status") != "paid":
            updated = bu.start_trial(chat_id)
        if not updated.get("services"):
            # They skipped services — push them back
            _send(
                chat_id,
                "Almost there — pick at least one service:",
                reply_markup=_services_keyboard([]),
            )
            bu.set_state(chat_id, bu.STATE_AWAITING_SERVICES)
            return
        bu.set_state(chat_id, bu.STATE_ACTIVE)
        updated = bu.get_by_chat_id(chat_id)
        _send_onboarding_complete(updated)
        return

    if state == bu.STATE_AWAITING_SERVICES:
        _send(
            chat_id,
            "Use the buttons above to choose services, or /start to restart.",
        )
        return

    # Fallback for active users chatting freely
    _send(
        chat_id,
        "I only understand commands right now. Try /status or /help.",
    )


def _handle_callback_query(callback: dict) -> None:
    cb_id = callback.get("id")
    message = callback.get("message") or {}
    sender = callback.get("from") or {}
    chat = message.get("chat") or {}
    data = callback.get("data") or ""

    user = bu.upsert_from_telegram(sender, chat)
    chat_id = user["chat_id"]
    bu.log_message(user["id"], chat_id, "in", f"CB:{data}", message_type="callback")

    if data.startswith("svc:"):
        choice = data.split(":", 1)[1]
        if choice == "done":
            tg.answer_callback_query(cb_id, "Saved ✅")
            refreshed = bu.get_by_chat_id(chat_id)
            if not refreshed.get("services"):
                tg.answer_callback_query(cb_id, "Pick at least one service!", show_alert=True)
                return
            if refreshed.get("city"):
                # Already has a city (re-run of /services); go back to active.
                if refreshed.get("subscription_status") == "none":
                    refreshed = bu.start_trial(chat_id)
                bu.set_state(chat_id, bu.STATE_ACTIVE)
                _send(
                    chat_id,
                    "Updated ✅  Use /status to review your setup.",
                )
            else:
                _send_city_prompt(chat_id)
            return

        # Toggle
        new_services = bu.toggle_service(chat_id, choice)
        tg.answer_callback_query(cb_id, "✓")
        # Edit the message to refresh checkmarks
        tg.edit_message_text(
            chat_id,
            message.get("message_id"),
            WELCOME_TEXT,
            reply_markup=_services_keyboard(new_services),
        )
        return

    tg.answer_callback_query(cb_id)


def _handle_chat_member(update: dict) -> None:
    """
    Handle chat_member updates from the configured TELEGRAM_CHANNEL_ID.
    When a user joins, start their free trial automatically.
    """
    cm = update.get("chat_member") or update.get("my_chat_member") or {}
    chat = cm.get("chat") or {}
    if CHANNEL_ID and str(chat.get("id")) != str(CHANNEL_ID):
        return

    new = cm.get("new_chat_member") or {}
    old = cm.get("old_chat_member") or {}
    status = (new.get("status") or "").lower()
    prev = (old.get("status") or "").lower()

    sender_user = new.get("user") or {}
    if not sender_user.get("id"):
        return

    became_member = status in ("member", "administrator", "creator") and prev in (
        "left",
        "kicked",
        "restricted",
        "",
    )
    if not became_member:
        return

    # We need a chat_id for private messages — we only have the user_id here.
    # If we've never seen this user in private chat, we can't DM them yet.
    # Store what we can, the actual trial/DM will start on their next /start.
    existing = bu.get_by_chat_id(sender_user["id"])
    if existing:
        bu.set_channel_joined(existing["chat_id"])
        # Auto-start a trial if they don't have one yet
        if existing.get("subscription_status") == "none":
            bu.start_trial(existing["chat_id"])
            try:
                tg.send_message_to(
                    existing["chat_id"],
                    "🎁 *Thanks for joining the MLeads channel!*\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "We've activated your *7-day free trial* automatically.\n"
                    "Run /start to pick your services and city.",
                )
            except Exception as e:
                logger.warning(f"[bot] could not DM new channel member: {e}")
    else:
        # Create a stub — next /start from this user will complete it
        bu.upsert_from_telegram(sender_user, {"id": sender_user["id"]})
        bu.set_channel_joined(sender_user["id"])
        bu.start_trial(sender_user["id"])
        logger.info(
            f"[bot] stub bot_user created for channel join user_id={sender_user.get('id')}"
        )


def _process_update(update: dict) -> None:
    try:
        if "message" in update:
            _handle_message(update["message"])
        elif "callback_query" in update:
            _handle_callback_query(update["callback_query"])
        elif "chat_member" in update or "my_chat_member" in update:
            _handle_chat_member(update)
        else:
            logger.debug(f"[bot] ignoring update: {list(update.keys())}")
    except Exception as e:
        logger.exception(f"[bot] error processing update: {e}")


# ─────────────────────────────────────────────────────
# Poll loop + trial expiry sweep
# ─────────────────────────────────────────────────────

_LAST_TRIAL_SWEEP = 0.0


def _maybe_sweep_trials():
    global _LAST_TRIAL_SWEEP
    now = time.time()
    if now - _LAST_TRIAL_SWEEP < 3600:  # once per hour
        return
    _LAST_TRIAL_SWEEP = now
    try:
        bu.expire_due_trials()
    except Exception as e:
        logger.warning(f"[bot] trial sweep failed: {e}")


def _poll_loop():
    logger.info("[bot] Telegram bot worker starting (long polling)")
    try:
        tg.delete_webhook()
    except Exception:
        pass

    try:
        last_id = int(bu.get_bot_state("last_update_id") or 0)
    except Exception:
        last_id = 0

    allowed = ["message", "callback_query", "chat_member", "my_chat_member"]

    while True:
        try:
            updates = tg.get_updates(
                offset=last_id + 1,
                timeout=POLL_TIMEOUT,
                allowed_updates=allowed,
            )
            for update in updates:
                uid = update.get("update_id")
                if uid and uid > last_id:
                    last_id = uid
                _process_update(update)
            if updates:
                bu.set_bot_state("last_update_id", str(last_id))
            _maybe_sweep_trials()
        except Exception as e:
            logger.exception(f"[bot] poll loop error: {e}")
            time.sleep(5)


_worker_thread: threading.Thread | None = None


def start_bot_worker() -> bool:
    """
    Start the Telegram bot worker in a daemon thread.
    Safe to call multiple times — returns False if not started.
    """
    global _worker_thread

    if not BOT_WORKER_ENABLED:
        logger.info("[bot] worker disabled via BOT_WORKER_ENABLED")
        return False
    if not tg.is_configured():
        logger.info("[bot] TELEGRAM_BOT_TOKEN missing — worker not started")
        return False
    if _worker_thread and _worker_thread.is_alive():
        return True

    _worker_thread = threading.Thread(target=_poll_loop, name="telegram-bot", daemon=True)
    _worker_thread.start()
    logger.info("[bot] worker thread started")
    return True
