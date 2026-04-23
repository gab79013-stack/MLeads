"""
agents/marketing/base_marketing_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Clase base abstracta para todos los Marketing Agents.

Extiende BaseAgent satisfaciendo su contrato (fetch_leads, notify) pero
remapeando la semántica para tareas de marketing:

  fetch_leads() → lista de tareas de marketing (dicts con type, topic, etc.)
  notify(task)  → ejecuta una tarea (publica contenido, envía email, reporta)
  send_batch()  → loop simple sin dedup/hot-zones (bypass del pipeline de leads)

Cada agente concreto debe implementar:
  - fetch_leads()              → descubrir qué tareas hay pendientes
  - notify(task)               → ejecutar una tarea
  - _claude_system_prompt: str → system prompt para ephemeral caching
"""

import os
import logging
from abc import abstractmethod
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLED        = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")
CLAUDE_MODEL      = os.getenv("AI_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
MKT_TELEGRAM_CHAT = os.getenv("MKT_TELEGRAM_REPORTS_CHAT", "")


class BaseMarketingAgent(BaseAgent):
    """Abstract base for all MLeads marketing automation agents."""

    name:      str = "Base Marketing Agent"
    emoji:     str = "📣"
    agent_key: str = "mkt_base"

    _claude_system_prompt: str = ""

    # ── Abstract contract (same as BaseAgent) ────────────────────────
    @abstractmethod
    def fetch_leads(self) -> list:
        """Return list of marketing task dicts to execute."""
        ...

    @abstractmethod
    def notify(self, task: dict):
        """Execute one marketing task."""
        ...

    # ── Override send_batch — no dedup/hot-zones for marketing ───────
    def send_batch(self, tasks: list) -> int:
        """Execute marketing tasks. Returns count of successful executions."""
        if not tasks:
            return 0
        executed = 0
        for task in tasks:
            try:
                self.notify(task)
                executed += 1
            except Exception as e:
                logger.error(f"[{self.agent_key}] Task error: {e}", exc_info=True)
        logger.info(f"[{self.agent_key}] {executed}/{len(tasks)} tasks completed")
        return executed

    # ── Claude Haiku with ephemeral caching ──────────────────────────
    def _generate_content(self, user_prompt: str, max_tokens: int = 1500) -> str | None:
        """
        Call Claude Haiku with ephemeral-cached system prompt.
        Returns None on failure so caller can use fallback.
        """
        if not ANTHROPIC_API_KEY or not AI_ENABLED:
            return None
        if not self._claude_system_prompt:
            logger.warning(f"[{self.agent_key}] No _claude_system_prompt defined — skipping AI")
            return None
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": self._claude_system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Claude error: {e}")
            return None

    def _generate_json(self, user_prompt: str, max_tokens: int = 1500) -> dict | None:
        """Generate content and parse as JSON. Returns None on failure."""
        import json
        raw = self._generate_content(user_prompt, max_tokens=max_tokens)
        if not raw:
            return None
        try:
            # Strip markdown code fences if present
            text = raw.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.warning(f"[{self.agent_key}] JSON parse error: {e} — raw: {raw[:200]}")
            return None

    # ── Telegram reporting helpers ────────────────────────────────────
    def _send_report(self, message: str):
        """Send a marketing report to Telegram (marketing-specific chat or main chat)."""
        try:
            from utils.telegram import send_message, send_message_to
            if MKT_TELEGRAM_CHAT:
                send_message_to(MKT_TELEGRAM_CHAT, message)
            else:
                send_message(message)
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Telegram report error: {e}")

    # ── DB persistence helpers ────────────────────────────────────────
    def _store_content(self, content_type: str, title: str, body: str,
                       platform: str = None, keywords: list = None,
                       meta_description: str = None, ai_source: str = "claude",
                       status: str = "draft") -> int:
        """Persist generated content to marketing_content. Returns row id."""
        try:
            from utils.marketing_db import save_content
            return save_content(
                content_type=content_type, title=title, body=body,
                agent_key=self.agent_key, platform=platform,
                keywords=keywords, meta_description=meta_description,
                ai_source=ai_source, status=status,
            )
        except Exception as e:
            logger.error(f"[{self.agent_key}] DB store error: {e}")
            return -1

    def _queue_social_post(self, platform: str, text: str,
                           scheduled_at=None, content_id: int = None) -> int:
        """Queue a social post for dispatch."""
        try:
            from utils.marketing_db import queue_social_post
            return queue_social_post(platform, text, scheduled_at, content_id)
        except Exception as e:
            logger.error(f"[{self.agent_key}] Queue post error: {e}")
            return -1
