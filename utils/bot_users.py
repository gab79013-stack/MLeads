"""
utils/bot_users.py
━━━━━━━━━━━━━━━━━━
Helper layer for the `bot_users` table.

The Telegram bot (workers/telegram_bot.py) uses these helpers to:
  - Look up or create bot users on any incoming update
  - Persist conversational state during onboarding
  - Start/extend trials and subscriptions
  - Query which users should receive a given lead

All timestamps are stored as ISO strings in UTC.
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta
from typing import Iterable

from utils.web_db import get_db_connection

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────

TRIAL_DAYS = int(os.getenv("BOT_TRIAL_DAYS", "7"))
DEFAULT_RADIUS_MILES = int(os.getenv("BOT_DEFAULT_RADIUS_MILES", "35"))
SUBSCRIPTION_PRICE_USD = float(os.getenv("BOT_PRICE_USD", "99"))

# Services the user can subscribe to. Keys align with agent_key values
# so we can filter leads purely by matching against this list.
# The 5 target services appear first; auxiliary sources follow.
AVAILABLE_SERVICES = [
    ("roofing", "🏠 Roofing"),
    ("drywall", "🧱 Drywall"),
    ("paint", "🎨 Paint"),
    ("landscaping", "🌳 Landscaping"),
    ("electrical", "⚡ Electrical"),
    ("permits", "🏗️ Building Permits"),
    ("construction", "👷 Construction"),
    ("realestate", "🏠 Real Estate"),
    ("deconstruction", "💥 Demolition"),
    ("flood", "💧 Water Damage"),
    ("rodents", "🐭 Pest Control"),
    ("solar", "☀️ Solar"),
]

# Keyword → service_key mapping used by the lead filter so we also catch
# leads tagged as "framing", "paint", "roofing", etc.
KEYWORD_SERVICE_MAP = {
    # Roofing
    "roof": "roofing",
    "roofing": "roofing",
    "reroof": "roofing",
    "re-roof": "roofing",
    "shingle": "roofing",
    "shingles": "roofing",
    # Drywall
    "drywall": "drywall",
    "sheetrock": "drywall",
    "gypsum": "drywall",
    # Paint
    "paint": "paint",
    "painter": "paint",
    "painting": "paint",
    "repaint": "paint",
    # Landscaping
    "landscape": "landscaping",
    "landscaping": "landscaping",
    "irrigation": "landscaping",
    "sprinkler": "landscaping",
    "hardscape": "landscaping",
    "sod": "landscaping",
    # Electrical
    "electrical": "electrical",
    "electric": "electrical",
    "panel upgrade": "electrical",
    "service upgrade": "electrical",
    "rewire": "electrical",
    "wiring": "electrical",
    "ev charger": "electrical",
    # Construction (generic fall-through)
    "framing": "construction",
    "framer": "construction",
    "carpenter": "construction",
    "carpentry": "construction",
    "permit": "permits",
    "demolition": "deconstruction",
    "demo": "deconstruction",
    "deconstruction": "deconstruction",
    "sale": "realestate",
    "sold": "realestate",
    "listing": "realestate",
    "flood": "flood",
    "water damage": "flood",
    "rodent": "rodents",
    "pest": "rodents",
    "solar": "solar",
    "pv": "solar",
    "photovoltaic": "solar",
}


# ─────────────────────────────────────────────────────
# Conversational states
# ─────────────────────────────────────────────────────

STATE_NEW = "new"
STATE_AWAITING_SERVICES = "awaiting_services"
STATE_AWAITING_CITY = "awaiting_city"
STATE_ACTIVE = "active"
STATE_TRIAL_EXPIRED = "trial_expired"
STATE_SUSPENDED = "suspended"


# ─────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────

def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except Exception:
            return None


def row_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    # Decode JSON column for services
    services_raw = d.get("services") or "[]"
    try:
        d["services"] = json.loads(services_raw) if isinstance(services_raw, str) else services_raw
    except Exception:
        d["services"] = []
    return d


def get_by_chat_id(chat_id: str | int) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM bot_users WHERE chat_id = ?", (str(chat_id),))
    row = c.fetchone()
    conn.close()
    return row_to_dict(row)


def get_by_id(bot_user_id: int) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM bot_users WHERE id = ?", (bot_user_id,))
    row = c.fetchone()
    conn.close()
    return row_to_dict(row)


def upsert_from_telegram(msg_from: dict, chat: dict) -> dict:
    """
    Look up a bot user by chat_id; create it if missing.
    `msg_from` and `chat` are the Telegram `from` / `chat` JSON objects.
    """
    chat_id = str(chat.get("id") or msg_from.get("id"))
    tg_user_id = str(msg_from.get("id") or "")
    username = msg_from.get("username") or ""
    first = msg_from.get("first_name") or ""
    last = msg_from.get("last_name") or ""

    existing = get_by_chat_id(chat_id)
    if existing:
        # Refresh name fields in case the user updated them.
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            UPDATE bot_users
               SET username = ?, first_name = ?, last_name = ?,
                   telegram_user_id = ?, updated_at = ?
             WHERE chat_id = ?
            """,
            (username, first, last, tg_user_id, _now(), chat_id),
        )
        conn.commit()
        conn.close()
        return get_by_chat_id(chat_id)

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO bot_users (
            chat_id, telegram_user_id, username, first_name, last_name,
            state, services, radius_miles, subscription_status
        ) VALUES (?, ?, ?, ?, ?, ?, '[]', ?, 'none')
        """,
        (chat_id, tg_user_id, username, first, last, STATE_NEW, DEFAULT_RADIUS_MILES),
    )
    conn.commit()
    conn.close()
    logger.info(f"[bot_users] Created new bot user chat_id={chat_id} username={username}")
    return get_by_chat_id(chat_id)


def set_state(chat_id: str | int, state: str) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE bot_users SET state = ?, updated_at = ? WHERE chat_id = ?",
        (state, _now(), str(chat_id)),
    )
    conn.commit()
    conn.close()


def set_services(chat_id: str | int, services: list[str]) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE bot_users SET services = ?, updated_at = ? WHERE chat_id = ?",
        (json.dumps(services), _now(), str(chat_id)),
    )
    conn.commit()
    conn.close()


def toggle_service(chat_id: str | int, service_key: str) -> list[str]:
    user = get_by_chat_id(chat_id)
    current = list(user.get("services") or [])
    if service_key in current:
        current.remove(service_key)
    else:
        current.append(service_key)
    set_services(chat_id, current)
    return current


def set_city(
    chat_id: str | int,
    city: str,
    latitude: float | None = None,
    longitude: float | None = None,
) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET city = ?, latitude = ?, longitude = ?, updated_at = ?
         WHERE chat_id = ?
        """,
        (city, latitude, longitude, _now(), str(chat_id)),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────
# Trial / subscription
# ─────────────────────────────────────────────────────

def start_trial(chat_id: str | int, days: int = TRIAL_DAYS) -> dict:
    """Start (or restart) a free trial for the user."""
    now = datetime.utcnow()
    ends = now + timedelta(days=days)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'trial',
               trial_started_at = ?,
               trial_ends_at = ?,
               is_active = 1,
               updated_at = ?
         WHERE chat_id = ?
        """,
        (
            now.strftime("%Y-%m-%d %H:%M:%S"),
            ends.strftime("%Y-%m-%d %H:%M:%S"),
            _now(),
            str(chat_id),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"[bot_users] Trial started chat_id={chat_id} ends={ends.isoformat()}")
    return get_by_chat_id(chat_id)


def mark_paid(chat_id: str | int, until: datetime,
              stripe_customer_id: str | None = None,
              stripe_subscription_id: str | None = None) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'paid',
               paid_until = ?,
               stripe_customer_id = COALESCE(?, stripe_customer_id),
               stripe_subscription_id = COALESCE(?, stripe_subscription_id),
               is_active = 1,
               updated_at = ?
         WHERE chat_id = ?
        """,
        (
            until.strftime("%Y-%m-%d %H:%M:%S"),
            stripe_customer_id,
            stripe_subscription_id,
            _now(),
            str(chat_id),
        ),
    )
    conn.commit()
    conn.close()


def mark_expired(chat_id: str | int) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'expired',
               state = ?,
               updated_at = ?
         WHERE chat_id = ?
        """,
        (STATE_TRIAL_EXPIRED, _now(), str(chat_id)),
    )
    conn.commit()
    conn.close()


def set_channel_joined(chat_id: str | int) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE bot_users SET joined_channel_at = ?, updated_at = ? WHERE chat_id = ?",
        (_now(), _now(), str(chat_id)),
    )
    conn.commit()
    conn.close()


def is_subscription_active(user: dict) -> bool:
    """Return True if the user's trial or paid plan is still valid."""
    status = user.get("subscription_status") or "none"
    if status == "paid":
        paid_until = _parse_ts(user.get("paid_until"))
        return bool(paid_until and paid_until > datetime.utcnow())
    if status == "trial":
        ends = _parse_ts(user.get("trial_ends_at"))
        return bool(ends and ends > datetime.utcnow())
    return False


def update_subscription_status(user: dict) -> str:
    """
    Recompute the correct status from timestamps.
    Returns the new status and persists it if it changed.
    """
    old = user.get("subscription_status") or "none"

    new = old
    if old in ("trial", "paid"):
        if not is_subscription_active(user):
            new = "expired"

    if new != old:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "UPDATE bot_users SET subscription_status = ?, updated_at = ? WHERE id = ?",
            (new, _now(), user["id"]),
        )
        conn.commit()
        conn.close()
        logger.info(f"[bot_users] Status transition {old}→{new} for {user.get('chat_id')}")
    return new


# ─────────────────────────────────────────────────────
# Lead filtering
# ─────────────────────────────────────────────────────

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


def _lead_service_keys(lead: dict, agent_key: str) -> set[str]:
    """
    Derive the set of service keys a lead matches. Always includes the
    source agent_key, plus any keyword matches in description/permit_type.
    """
    keys: set[str] = set()
    if agent_key:
        keys.add(agent_key)

    text_parts = [
        lead.get("description"),
        lead.get("permit_type"),
        lead.get("title"),
        lead.get("work_description"),
    ]
    haystack = " ".join(str(p or "") for p in text_parts).lower()
    for keyword, service in KEYWORD_SERVICE_MAP.items():
        if keyword in haystack:
            keys.add(service)

    return keys


def find_recipients_for_lead(lead: dict, agent_key: str) -> list[dict]:
    """
    Return the list of active bot_users who should receive this lead.

    A bot user matches if:
      - subscription is active (trial or paid)
      - state is ACTIVE (onboarding completed)
      - lead service overlaps with the user's selected services
      - lead location is within the user's radius of their city
        (or the user's city name matches the lead city exactly)
    """
    lead_services = _lead_service_keys(lead, agent_key)
    if not lead_services:
        return []

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT * FROM bot_users
         WHERE is_active = 1
           AND state = ?
           AND subscription_status IN ('trial', 'paid')
        """,
        (STATE_ACTIVE,),
    )
    rows = [row_to_dict(r) for r in c.fetchall()]
    conn.close()

    lead_lat = lead.get("latitude") or lead.get("lat")
    lead_lon = lead.get("longitude") or lead.get("lon") or lead.get("lng")
    lead_city = (lead.get("city") or "").strip().lower()

    recipients: list[dict] = []
    for user in rows:
        # Subscription expiry check (cheap, no DB)
        if not is_subscription_active(user):
            continue
        user_services = set(user.get("services") or [])
        if not user_services & lead_services:
            continue

        # Location match
        user_city = (user.get("city") or "").strip().lower()
        in_radius = False
        if user_city and lead_city and user_city == lead_city:
            in_radius = True
        elif lead_lat and lead_lon and user.get("latitude") and user.get("longitude"):
            try:
                d = _haversine_miles(
                    float(user["latitude"]),
                    float(user["longitude"]),
                    float(lead_lat),
                    float(lead_lon),
                )
                if d <= float(user.get("radius_miles") or DEFAULT_RADIUS_MILES):
                    in_radius = True
            except (TypeError, ValueError):
                pass
        elif not user_city and not user.get("latitude"):
            # User has no location set → skip (can't geofilter safely)
            continue

        if in_radius:
            recipients.append(user)

    return recipients


def increment_lead_counter(bot_user_id: int) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET leads_sent_count = COALESCE(leads_sent_count, 0) + 1,
               last_lead_at = ?
         WHERE id = ?
        """,
        (_now(), bot_user_id),
    )
    conn.commit()
    conn.close()


def log_message(
    bot_user_id: int,
    chat_id: str | int,
    direction: str,
    text: str = "",
    message_type: str = "text",
    lead_id: str | None = None,
) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO bot_messages (
            bot_user_id, chat_id, direction, message_type, lead_id, text
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (bot_user_id, str(chat_id), direction, message_type, lead_id, text[:2000]),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────
# Admin / dashboard queries
# ─────────────────────────────────────────────────────

def list_bot_users(limit: int = 500) -> list[dict]:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        SELECT * FROM bot_users
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (limit,),
    )
    rows = [row_to_dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_stats() -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM bot_users")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM bot_users WHERE subscription_status = 'trial'")
    trial = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM bot_users WHERE subscription_status = 'paid'")
    paid = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM bot_users WHERE subscription_status = 'expired'")
    expired = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(leads_sent_count), 0) FROM bot_users")
    total_leads = c.fetchone()[0]
    conn.close()
    return {
        "total": total,
        "trial": trial,
        "paid": paid,
        "expired": expired,
        "total_leads_sent": total_leads,
        "monthly_revenue_usd": paid * SUBSCRIPTION_PRICE_USD,
    }


def expire_due_trials() -> int:
    """Mark as expired any trial that has passed its end date. Returns count."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        UPDATE bot_users
           SET subscription_status = 'expired',
               state = ?,
               updated_at = ?
         WHERE subscription_status = 'trial'
           AND trial_ends_at IS NOT NULL
           AND trial_ends_at < ?
        """,
        (STATE_TRIAL_EXPIRED, _now(), _now()),
    )
    count = c.rowcount
    conn.commit()
    conn.close()
    if count:
        logger.info(f"[bot_users] Auto-expired {count} trials")
    return count


# ─────────────────────────────────────────────────────
# Bot state (last update_id, etc.)
# ─────────────────────────────────────────────────────

def get_bot_state(key: str) -> str | None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def set_bot_state(key: str, value: str) -> None:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, _now()),
    )
    conn.commit()
    conn.close()
