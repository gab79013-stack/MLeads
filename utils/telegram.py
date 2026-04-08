"""
utils/telegram.py  v7
━━━━━━━━━━━━━━━━━━━
Rate limiter + retry 429 + digest completo con descripción.

FIX v7: Digest ahora incluye descripción del proyecto.
"""

import os
import time
import logging
import threading
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

TELEGRAM_API        = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_MSG_PER_MINUTE = int(os.getenv("TELEGRAM_MAX_MSG_MIN", "20"))
_MIN_INTERVAL       = 60.0 / _MAX_MSG_PER_MINUTE
MAX_BURST           = int(os.getenv("TELEGRAM_MAX_BURST", "10"))

_rate_lock      = threading.Lock()
_last_send_time = 0.0


def _token() -> str:
    t = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not t:
        raise EnvironmentError("TELEGRAM_BOT_TOKEN no configurado")
    return t

def _chat_id() -> str:
    c = os.getenv("TELEGRAM_CHAT_ID", "")
    if not c:
        raise EnvironmentError("TELEGRAM_CHAT_ID no configurado")
    return c

def _wait_for_slot():
    global _last_send_time
    with _rate_lock:
        elapsed = time.monotonic() - _last_send_time
        wait    = _MIN_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_send_time = time.monotonic()

def _esc(text: str) -> str:
    for ch in ["_", "*", "`", "["]:
        text = str(text).replace(ch, f"\\{ch}")
    return text


def send_message(text: str, max_retries: int = 4) -> bool:
    _wait_for_slot()
    url = TELEGRAM_API.format(token=_token())
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id":                  _chat_id(),
                    "text":                     text[:4096],
                    "parse_mode":               "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 30)
                logger.warning(f"Telegram 429 — esperando {retry_after}s")
                time.sleep(retry_after + 2)
                _wait_for_slot()
                continue
            if resp.status_code == 400:
                # Markdown roto — reintentar sin parse_mode
                resp2 = requests.post(
                    url,
                    json={
                        "chat_id":                  _chat_id(),
                        "text":                     text[:4096],
                        "disable_web_page_preview": True,
                    },
                    timeout=15,
                )
                if resp2.status_code == 200:
                    return True
                logger.error(f"Telegram 400 even without Markdown: {resp2.text[:200]}")
                return False
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
            else:
                logger.error(f"Telegram send error: {e}")
        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return False
    return False


def send_lead(agent_name, emoji, title, fields, url=None, cta=None) -> bool:
    """Mensaje individual para un lead."""
    lines = []
    label = agent_name.upper().replace(emoji, "").strip()
    lines.append(f"{emoji} *{label}*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📌 *{_esc(title)}*")
    lines.append("")
    for lbl, val in fields.items():
        if val and str(val).strip() not in ("—", "-", ""):
            lines.append(f"▸ *{lbl}:* {_esc(str(val))}")
    if url and "http" in str(url):
        lines.append(f"▸ *🔗 Ver permiso:* {url}")
    if cta:
        lines.append(f"\n💡 _{_esc(cta)}_")
    lines.append(f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    return send_message("\n".join(lines))


def send_digest(agent_name: str, emoji: str, leads: list) -> bool:
    """
    Digest v7 — cada lead en bloque separado con TODOS los datos relevantes:
    dirección completa, descripción del trabajo, contratista, teléfono,
    email, valor y enlace. Páginas de 15 leads.
    """
    total      = len(leads)
    page_size  = 5
    pages      = [leads[i:i+page_size] for i in range(0, min(total, 200), page_size)]
    label      = agent_name.upper().replace(emoji, "").strip()
    timestamp  = datetime.now().strftime("%d/%m/%Y %H:%M")
    ok         = True

    for p_idx, page in enumerate(pages):
        n_pages    = len(pages)
        page_label = f" • página {p_idx+1}/{n_pages}" if n_pages > 1 else ""
        lines = [
            f"{emoji} *{label}*",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"📦 *{total} leads nuevos*{page_label}  •  🕐 {timestamp}",
            "",
        ]

        for i, lead in enumerate(page, start=p_idx * page_size + 1):
            city        = lead.get("city", "")
            addr        = lead.get("address", "—")
            desc        = (lead.get("description") or "").strip()[:120]
            permit_type = lead.get("permit_type") or ""
            issued      = lead.get("issued_date") or ""
            value       = lead.get("value_float") or _parse_value(lead.get("value",""))
            contractor  = lead.get("contractor") or lead.get("contact_name") or ""
            phone       = lead.get("contact_phone") or ""
            email       = lead.get("contact_email") or ""
            lic         = lead.get("lic_number") or ""
            url         = lead.get("permit_url") or ""
            contact_src = lead.get("contact_source") or ""

            lines.append("▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬")
            lines.append(f"*#{i} — {_esc(city)}*")
            lines.append(f"📍 {_esc(addr)}")

            if desc:
                lines.append(f"📝 {_esc(desc)}")
            elif permit_type:
                lines.append(f"🔖 {_esc(permit_type)}")

            if issued:
                lines.append(f"📅 Emitido: {issued}")

            if value and value > 0:
                lines.append(f"💰 *${value:,.0f}*")

            # Bloque contacto GC
            if contractor:
                lines.append(f"👷 *{_esc(contractor)}*")
            if lic:
                lines.append(f"🪪 Lic: {_esc(lic)}")
            if phone:
                src_tag = f" _({_esc(contact_src)})_" if contact_src else ""
                lines.append(f"📞 {_esc(phone)}{src_tag}")
            if email:
                lines.append(f"✉️  {_esc(email)}")
            if not contractor and not phone and not email:
                lines.append("📵 _Sin datos de contacto_")

            if url and "http" in url:
                lines.append(f"🔗 {url}")

            lines.append("")

        ok = send_message("\n".join(lines)) and ok

    return ok


def _parse_value(v) -> float:
    import re
    try:
        return float(re.sub(r"[^\d.]", "", str(v) or "") or "0")
    except Exception:
        return 0.0


# ═════════════════════════════════════════════════════
# Bidirectional bot helpers (Phase 3 — bot_users)
# ═════════════════════════════════════════════════════
#
# The helpers above always post to TELEGRAM_CHAT_ID (the main channel or
# ops group). The ones below are used by the interactive bot worker to
# talk with individual users who opened a private chat with the bot.
# They support:
#   - Sending a message to an arbitrary chat_id
#   - Attaching inline keyboards (buttons)
#   - Answering callback queries (button press acks)
#   - Long polling via getUpdates
#   - Editing / deleting messages
#
# All helpers share the global rate-limiter to stay under Telegram's
# global 30 msg/s limit.

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


def _api_url(method: str) -> str:
    return TELEGRAM_API_BASE.format(token=_token(), method=method)


def send_message_to(
    chat_id,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "Markdown",
    disable_web_page_preview: bool = True,
    max_retries: int = 3,
) -> bool:
    """
    Send a message to a specific chat_id (vs TELEGRAM_CHAT_ID).
    `reply_markup` may be an inline_keyboard dict for interactive buttons.
    Falls back to plain text if Markdown parsing fails.
    """
    _wait_for_slot()
    url = _api_url("sendMessage")
    payload: dict = {
        "chat_id": chat_id,
        "text": text[:4096],
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    for attempt in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 10)
                time.sleep(retry_after + 1)
                _wait_for_slot()
                continue
            if resp.status_code == 400 and parse_mode:
                # Retry without Markdown in case the text has stray tokens
                plain = payload.copy()
                plain.pop("parse_mode", None)
                resp2 = requests.post(url, json=plain, timeout=15)
                if resp2.status_code == 200:
                    return True
                logger.error(f"Telegram send_message_to 400: {resp2.text[:200]}")
                return False
            resp.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                logger.error(f"Telegram send_message_to error: {e}")
        except Exception as e:
            logger.error(f"Telegram send_message_to error: {e}")
            return False
    return False


def answer_callback_query(callback_id: str, text: str = "", show_alert: bool = False) -> bool:
    """Acknowledge an inline-keyboard button press."""
    try:
        resp = requests.post(
            _api_url("answerCallbackQuery"),
            json={"callback_query_id": callback_id, "text": text[:200], "show_alert": show_alert},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram answer_callback_query error: {e}")
        return False


def edit_message_text(
    chat_id,
    message_id: int,
    text: str,
    reply_markup: dict | None = None,
    parse_mode: str | None = "Markdown",
) -> bool:
    """Edit a previously sent message (e.g. to update the onboarding buttons)."""
    try:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = requests.post(_api_url("editMessageText"), json=payload, timeout=10)
        if resp.status_code == 400:
            # "message is not modified" is fine
            return "not modified" in resp.text.lower()
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram edit_message_text error: {e}")
        return False


def get_updates(offset: int = 0, timeout: int = 25, allowed_updates: list | None = None) -> list:
    """
    Long-poll Telegram for new updates.
    Returns the list of update dicts (may be empty).
    """
    try:
        payload = {"offset": offset, "timeout": timeout}
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        resp = requests.post(
            _api_url("getUpdates"),
            json=payload,
            timeout=timeout + 10,
        )
        if resp.status_code != 200:
            logger.warning(f"Telegram getUpdates non-200: {resp.status_code} {resp.text[:200]}")
            return []
        data = resp.json()
        if not data.get("ok"):
            logger.warning(f"Telegram getUpdates error: {data}")
            return []
        return data.get("result", []) or []
    except requests.exceptions.Timeout:
        return []
    except Exception as e:
        logger.error(f"Telegram get_updates error: {e}")
        return []


def delete_webhook() -> bool:
    """Ensure no webhook is set (long polling mode)."""
    try:
        resp = requests.post(_api_url("deleteWebhook"), timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


def is_configured() -> bool:
    """Return True if TELEGRAM_BOT_TOKEN is set."""
    return bool(os.getenv("TELEGRAM_BOT_TOKEN"))
