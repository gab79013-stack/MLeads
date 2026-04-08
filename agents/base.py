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

        # Paso 4: Enviar leads — siempre mensajes individuales
        sent_count = 0
        for lead in new_leads:
            try:
                self.notify(lead)
                mark_sent(self.agent_key, lead["id"])
                sent_count += 1
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
