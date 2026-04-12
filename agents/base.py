"""
agents/base.py  v5
━━━━━━━━━━━━━━━━━
Clase base para todos los agentes.

⚡ v5:
  - Integración con DeduplicationEngine (cross-agent dedup)
  - Integración con HotZoneDetector (geographic clustering)
  - Los leads pasan por dedup antes de ser enviados
  - Hot zones detectadas automáticamente después de cada batch
"""

import logging
from abc import ABC, abstractmethod
from utils.db import is_sent, mark_sent
from utils.telegram import send_message, send_message_to
from utils.dedup import get_dedup_engine
from utils.hot_zones import get_hot_zone_detector, format_hot_zone_alert

# ── AI modules (graceful import — disabled if SDK not installed) ──────
try:
    from utils.ai_classifier import enrich_lead_with_classification as _ai_classify
    _AI_CLASSIFIER_AVAILABLE = True
except Exception:
    _AI_CLASSIFIER_AVAILABLE = False

try:
    from utils.ai_bot import send_lead_with_actions as _send_with_actions
    _AI_BOT_AVAILABLE = True
except Exception:
    _AI_BOT_AVAILABLE = False

try:
    from utils.matching_engine import match_lead_to_subs as _match_lead
    _MATCHING_AVAILABLE = True
except Exception:
    _MATCHING_AVAILABLE = False

try:
    from utils.fraud_detector import validate_lead_contractor as _validate_license
    _LICENSE_VALIDATOR_AVAILABLE = True
except Exception:
    _LICENSE_VALIDATOR_AVAILABLE = False

logger = logging.getLogger(__name__)


def _fanout_to_bot_users(lead: dict, agent_key: str, formatted_text: str | None = None) -> int:
    """
    Phase 3: deliver the lead to every bot_user whose services/city match.

    Returns the number of successful deliveries. Silently no-ops if the
    bot_users subsystem isn't available (e.g. during tests).
    """
    try:
        from utils import bot_users as bu
    except Exception:
        return 0

    try:
        recipients = bu.find_recipients_for_lead(lead, agent_key)
    except Exception as e:
        logger.warning(f"[fanout] error finding recipients: {e}")
        return 0

    if not recipients:
        return 0

    if not formatted_text:
        # Minimal fallback card — notify() is designed for the main channel
        # with Markdown, so we reuse its output when possible. Here we just
        # build a compact summary.
        title = lead.get("title") or lead.get("address") or "New lead"
        city = lead.get("city") or ""
        contact = lead.get("contact_phone") or lead.get("contact_email") or ""
        formatted_text = (
            f"🔔 *{agent_key.upper()}* — new lead\n"
            f"📍 {title}\n"
            f"🏙️ {city}\n"
            + (f"📞 {contact}\n" if contact else "")
        )

    sent = 0
    for user in recipients:
        try:
            ok = send_message_to(user["chat_id"], formatted_text)
            if ok:
                bu.increment_lead_counter(user["id"])
                bu.log_message(
                    user["id"],
                    user["chat_id"],
                    "out",
                    formatted_text,
                    message_type="lead",
                    lead_id=str(lead.get("id") or ""),
                )
                sent += 1
        except Exception as e:
            logger.warning(f"[fanout] delivery to {user.get('chat_id')} failed: {e}")
    if sent:
        logger.info(f"[fanout] {sent}/{len(recipients)} bot_users received lead {lead.get('id')}")
    return sent


class BaseAgent(ABC):
    name:      str = "Base Agent"
    emoji:     str = "🤖"
    agent_key: str = "base"

    @abstractmethod
    def fetch_leads(self) -> list:
        ...

    @abstractmethod
    def notify(self, lead: dict):
        ...

    def send_if_new(self, lead: dict) -> bool:
        """Envía el lead solo si no fue enviado antes. Retorna True si fue enviado."""
        lead_id = lead.get("id")
        if not lead_id or is_sent(self.agent_key, lead_id):
            return False
        try:
            self.notify(lead)
            mark_sent(self.agent_key, lead_id)
            return True
        except Exception as e:
            logger.error(f"[{self.agent_key}] Error al notificar {lead_id}: {e}")
            return False

    def send_batch(self, leads: list) -> int:
        """
        Envía una lista de leads nuevos con:
          1. Deduplicación cross-agent (consolida leads de múltiples agentes)
          2. Hot zone detection (detecta clusters geográficos)
          3. Protección anti-ráfaga (digest mode si > MAX_BURST)

        Retorna el número de leads nuevos enviados.
        """
        dedup = get_dedup_engine()
        hz_detector = get_hot_zone_detector()

        # Paso 1: Registrar en dedup engine + enriquecer con cross-agent data
        enriched_leads = []
        for lead in leads:
            consolidated = dedup.register_lead(lead, self.agent_key)
            enriched_leads.append(consolidated)

        # Paso 2: Filtrar solo los que no han sido enviados
        new_leads = [
            l for l in enriched_leads
            if l.get("id") and not is_sent(self.agent_key, l["id"])
        ]

        if not new_leads:
            return 0

        # Paso 3: Registrar en hot zone detector
        for lead in new_leads:
            hz_detector.add_lead(lead)

        # Paso 3b: Enriquecer con clasificación AI (trade, urgency, summary)
        if _AI_CLASSIFIER_AVAILABLE:
            for lead in new_leads:
                try:
                    _ai_classify(lead)
                except Exception as e:
                    logger.debug(f"[{self.agent_key}] AI classify error: {e}")

        # Paso 3c: Validate contractor licenses on leads that have them
        if _LICENSE_VALIDATOR_AVAILABLE:
            for lead in new_leads:
                if lead.get("lic") or lead.get("contractor"):
                    try:
                        _validate_license(lead)
                    except Exception as e:
                        logger.debug(f"[{self.agent_key}] License validation error: {e}")

        # Paso 3d: Match leads to subcontractors (for targeted fanout)
        if _MATCHING_AVAILABLE:
            for lead in new_leads:
                try:
                    matches = _match_lead(lead, self.agent_key, max_results=5)
                    if matches:
                        lead["_matched_subs"] = len(matches)
                        lead["_top_match_score"] = matches[0].match_score
                except Exception as e:
                    logger.debug(f"[{self.agent_key}] Matching error: {e}")

        # Paso 4: Enviar leads — siempre mensajes individuales
        sent_count = 0
        for lead in new_leads:
            try:
                self.notify(lead)
                mark_sent(self.agent_key, lead["id"])
                sent_count += 1

                # Botones interactivos para leads HOT/WARM
                if _AI_BOT_AVAILABLE:
                    grade = lead.get("_scoring", {}).get("grade", "")
                    if grade in ("HOT", "WARM"):
                        try:
                            score = lead.get("_scoring", {}).get("score", 0)
                            grade_emoji = lead.get("_scoring", {}).get("grade_emoji", "")
                            trade = lead.get("_trade", "")
                            addr = lead.get("address", "")[:60]
                            action_text = (
                                f"{grade_emoji} *Lead {grade}* — {score}/100\n"
                                f"📍 {addr}"
                                + (f"\n🔨 {trade}" if trade else "")
                                + "\n\n¿Te interesa este lead?"
                            )
                            _send_with_actions(lead, action_text)
                        except Exception as e:
                            logger.debug(f"[{self.agent_key}] AI bot action error: {e}")

                # Fan out to individual bot_users whose preferences match.
                try:
                    _fanout_to_bot_users(lead, self.agent_key)
                except Exception as fe:
                    logger.warning(f"[{self.agent_key}] fanout failed: {fe}")
            except Exception as e:
                logger.error(f"[{self.agent_key}] Error notificando {lead.get('id')}: {e}")

        # Paso 5: Detectar y alertar hot zones nuevas
        new_zones = hz_detector.get_new_hot_zones()
        for zone in new_zones:
            try:
                alert_msg = format_hot_zone_alert(zone)
                send_message(alert_msg)
                logger.info(
                    f"[HotZone] 🔥 Zona detectada: {', '.join(zone['cities'])} — "
                    f"{zone['lead_count']} leads, {zone['agent_count']} agentes"
                )
            except Exception as e:
                logger.error(f"[HotZone] Error enviando alerta: {e}")

        # Log consolidación cross-agent
        consolidated_count = sum(
            1 for l in new_leads if l.get("_is_consolidated")
        )
        if consolidated_count:
            logger.info(
                f"[{self.agent_key}] {consolidated_count}/{len(new_leads)} "
                f"leads consolidados con datos de otros agentes"
            )

        return sent_count
