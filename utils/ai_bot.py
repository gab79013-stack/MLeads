"""
utils/ai_bot.py
━━━━━━━━━━━━━━━
IA #8 — Bot Telegram Conversacional con Calificación de Leads

El bot de Telegram pasa de notificador pasivo a agente activo:

Flujo por lead HOT/WARM:
  1. Lead llega → bot envía notificación normal
  2. Bot agrega botones: [Me interesa] [Ver outreach] [No aplica]
  3. Si sub-contractor presiona "Me interesa":
     → Bot pregunta: trade, radio de trabajo, disponibilidad
  4. Respuestas actualizan el scoring del lead en DB
  5. Si lead calificado: genera outreach + cierra el loop

También responde preguntas en lenguaje natural:
  "Dame los 5 leads más calientes de hoy"
  "¿Cuántos permisos hubo en Chicago esta semana?"
  "Muestra leads de roofing en Austin"

Usa Claude Haiku para bajo costo.
Requiere que el bot esté configurado para recibir updates
(webhook o polling activo).
"""

import os
import json
import logging
import threading
import time
import requests

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLED        = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")
MODEL             = os.getenv("AI_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
BOT_TOKEN         = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID           = os.getenv("TELEGRAM_CHAT_ID", "")

TGAPI = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Estado de conversación por chat_id (in-memory)
_conv_state: dict[str, dict] = {}
# Leads pendientes de calificación {lead_id: lead_dict}
_pending_leads: dict[str, dict] = {}


# ── Enviar mensaje con botones inline ────────────────────────────────

def send_lead_with_actions(lead: dict, message_text: str) -> bool:
    """
    Envía un lead con botones de acción inline.
    El sub-contractor puede responder directamente desde Telegram.
    """
    if not BOT_TOKEN or not CHAT_ID:
        return False

    lead_id = lead.get("id", "")[:50]
    _pending_leads[lead_id] = lead

    score   = lead.get("_scoring", {}).get("score", 0)
    grade   = lead.get("_scoring", {}).get("grade", "")
    trade   = lead.get("_trade", "")

    # Solo agregar botones para leads WARM o HOT
    if grade not in ("HOT", "WARM"):
        return False

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Me interesa", "callback_data": f"interest:{lead_id}"},
                {"text": "📤 Ver outreach", "callback_data": f"outreach:{lead_id}"},
            ],
            [
                {"text": "❌ No aplica", "callback_data": f"skip:{lead_id}"},
                {"text": "📋 Más info", "callback_data": f"info:{lead_id}"},
            ],
        ]
    }

    try:
        resp = requests.post(
            f"{TGAPI}/sendMessage",
            json={
                "chat_id":    CHAT_ID,
                "text":       message_text[:4096],
                "parse_mode": "Markdown",
                "reply_markup": keyboard,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"[AI Bot] send error: {e}")
        return False


# ── Procesar callback (botones presionados) ───────────────────────────

def handle_callback(callback_query: dict):
    """Procesa cuando el usuario presiona un botón inline."""
    data    = callback_query.get("data", "")
    chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id", ""))
    msg_id  = callback_query.get("message", {}).get("message_id")
    user    = callback_query.get("from", {}).get("first_name", "Sub")

    action, _, lead_id = data.partition(":")
    lead = _pending_leads.get(lead_id, {})

    if action == "interest":
        _conv_state[chat_id] = {
            "step":    "ask_trade",
            "lead_id": lead_id,
            "lead":    lead,
        }
        _send(chat_id, (
            f"¡Genial {user}! Cuéntame un poco más para conectarte mejor:\n\n"
            f"*¿Cuál es tu especialidad principal?*\n"
            f"Responde: roofing / electrical / drywall / painting / landscaping / hvac / otra"
        ))
        _answer_callback(callback_query.get("id", ""))

    elif action == "outreach":
        if lead:
            from utils.ai_outreach import generate_outreach, format_outreach_for_telegram
            outreach = generate_outreach(lead, sub_name=user)
            msg = format_outreach_for_telegram(outreach, lead)
            _send(chat_id, msg)
        _answer_callback(callback_query.get("id", ""))

    elif action == "skip":
        _send(chat_id, f"Entendido. Este lead se archivó para ti, {user}.")
        _answer_callback(callback_query.get("id", ""), "Lead archivado")

    elif action == "info":
        if lead:
            fields = []
            if lead.get("city"):    fields.append(f"🏙️ Ciudad: {lead['city']}")
            if lead.get("value"):   fields.append(f"💰 Valor: ${float(lead.get('value_float',0)):,.0f}")
            if lead.get("owner"):   fields.append(f"👤 Dueño: {lead['owner']}")
            if lead.get("_trade"):  fields.append(f"🔨 Trade: {lead['_trade']}")
            if lead.get("_ai_summary"): fields.append(f"💡 {lead['_ai_summary']}")
            _send(chat_id, "\n".join(fields) or "Sin información adicional")
        _answer_callback(callback_query.get("id", ""))


def handle_message(message: dict):
    """
    Procesa mensajes de texto:
    - Responde preguntas en lenguaje natural sobre leads
    - Continúa flujos de calificación activos
    """
    chat_id = str(message.get("chat", {}).get("id", ""))
    text    = message.get("text", "").strip()
    user    = message.get("from", {}).get("first_name", "")

    if not text or not chat_id:
        return

    # ── Flujo de calificación activo ─────────────────────────────
    state = _conv_state.get(chat_id, {})
    if state.get("step") == "ask_trade":
        trade = text.upper().replace("Á","A").replace("É","E").replace("Í","I")
        state["trade"] = trade
        state["step"]  = "ask_radius"
        _conv_state[chat_id] = state
        _send(chat_id, (
            f"Perfecto, *{trade}*. ¿Cuál es tu radio de trabajo?\n"
            f"Ej: '10 miles', '20 km', 'todo Dallas', etc."
        ))
        return

    if state.get("step") == "ask_radius":
        state["radius"] = text
        state["step"]   = "ask_availability"
        _conv_state[chat_id] = state
        _send(chat_id, "¿Cuándo puedes empezar? (ej: esta semana, en 2 semanas, flexible)")
        return

    if state.get("step") == "ask_availability":
        state["availability"] = text
        lead = state.get("lead", {})
        # Guardar calificación
        _save_qualification(chat_id, user, state, lead)
        del _conv_state[chat_id]

        # Generar outreach con los datos del sub
        sub_name = f"{user} ({state.get('trade', '')})"
        from utils.ai_outreach import generate_outreach, format_outreach_for_telegram
        outreach = generate_outreach(lead, sub_name=sub_name)
        outreach_msg = format_outreach_for_telegram(outreach, lead)

        _send(chat_id, (
            f"✅ *Perfecto {user}!*\n"
            f"Trade: {state.get('trade')} | Radio: {state.get('radius')} | "
            f"Disponible: {state.get('availability')}\n\n"
            f"Aquí tu outreach personalizado:"
        ))
        _send(chat_id, outreach_msg)
        return

    # ── Responder preguntas en lenguaje natural con Claude ────────
    if text.startswith("/") or len(text) < 5:
        return

    ai_response = _natural_language_query(text, chat_id)
    if ai_response:
        _send(chat_id, ai_response)


def _natural_language_query(question: str, chat_id: str) -> str:
    """
    Responde preguntas sobre los leads usando Claude + datos de DB.
    """
    if not ANTHROPIC_API_KEY or not AI_ENABLED:
        return "Escribe /help para ver los comandos disponibles."

    # Obtener stats básicos de DB para contexto
    context = _get_db_context()

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=(
                "You are a helpful assistant for a construction lead generation platform. "
                "Answer questions about leads, permits, and subcontractor opportunities. "
                "Be concise. Use emojis. Respond in the same language as the user. "
                f"Current platform stats: {context}"
            ),
            messages=[{"role": "user", "content": question}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.debug(f"[AI Bot] NL query error: {e}")
        return "No pude procesar esa consulta. Intenta de nuevo."


def _get_db_context() -> str:
    """Obtiene stats básicos de la DB para el contexto de Claude."""
    try:
        from utils.db import get_stats
        stats = get_stats()
        return json.dumps(stats, default=str)[:500]
    except Exception:
        return "{}"


def _send(chat_id: str, text: str):
    """Envía mensaje a un chat."""
    try:
        requests.post(
            f"{TGAPI}/sendMessage",
            json={
                "chat_id":    chat_id,
                "text":       text[:4096],
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"[AI Bot] send error: {e}")


def _answer_callback(callback_id: str, text: str = ""):
    """Responde al callback query (elimina el loading spinner)."""
    try:
        requests.post(
            f"{TGAPI}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def _save_qualification(chat_id: str, user: str, state: dict, lead: dict):
    """Guarda la calificación del sub-contractor en DB."""
    try:
        import sqlite3
        DB_PATH = os.getenv("DB_PATH", "data/leads.db")
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS lead_qualifications (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id      TEXT,
                    user_name    TEXT,
                    lead_id      TEXT,
                    trade        TEXT,
                    radius       TEXT,
                    availability TEXT,
                    qualified_at TEXT
                )
            """)
            conn.execute("""
                INSERT INTO lead_qualifications
                (chat_id, user_name, lead_id, trade, radius, availability, qualified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                chat_id, user,
                lead.get("id", ""),
                state.get("trade", ""),
                state.get("radius", ""),
                state.get("availability", ""),
                __import__("datetime").datetime.utcnow().isoformat(),
            ))
            conn.commit()
    except Exception as e:
        logger.debug(f"[AI Bot] save qualification error: {e}")


# ── Polling de updates (alternativa a webhook) ───────────────────────

def start_polling(interval: float = 2.0):
    """
    Inicia el polling de updates de Telegram en un thread separado.
    Usar solo si no se tiene webhook configurado.
    """
    if not BOT_TOKEN:
        logger.warning("[AI Bot] BOT_TOKEN no configurado — polling desactivado")
        return

    def _poll():
        offset = 0
        logger.info("[AI Bot] Polling iniciado")
        while True:
            try:
                resp = requests.get(
                    f"{TGAPI}/getUpdates",
                    params={"timeout": 30, "offset": offset},
                    timeout=35,
                )
                if resp.status_code != 200:
                    time.sleep(interval)
                    continue

                updates = resp.json().get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    if "callback_query" in upd:
                        threading.Thread(
                            target=handle_callback,
                            args=(upd["callback_query"],),
                            daemon=True,
                        ).start()
                    elif "message" in upd:
                        threading.Thread(
                            target=handle_message,
                            args=(upd["message"],),
                            daemon=True,
                        ).start()

            except Exception as e:
                logger.debug(f"[AI Bot] Poll error: {e}")
                time.sleep(interval * 5)

    t = threading.Thread(target=_poll, daemon=True, name="ai-bot-polling")
    t.start()
    return t
