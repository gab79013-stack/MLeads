"""
MLeads → Huly CRM Bridge

Syncs leads from MLeads SQLite to Huly CRM as Projects with Tasks.
When a user "likes" a lead in the swipe, it automatically appears in Huly
as a project with follow-up tasks.

Huly API: REST via transactor (port 8080)
Auth: Uses the Huly account system
"""

import os
import json
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

HULY_URL = os.getenv("HULY_URL", "http://localhost:8080")
HULY_SECRET = os.getenv("HULY_SECRET", "JYaJd6Z1saoQnDEtMY93zC_0o6GMsTSoR9_FL1r0GLkTvMwCCwdxy8TtSV3MrJ0W")
HULY_WORKSPACE = os.getenv("HULY_WORKSPACE", "mleads")

# Huly transactor internal URL (docker network)
HULY_TRANSACTOR = os.getenv("HULY_TRANSACTOR", "http://localhost:8080")


class HulyBridge:
    """Bridge between MLeads and Huly CRM."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
        })
        self.token = None
        self.workspace_id = None

    def _api(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make an API call to Huly transactor."""
        url = f"{HULY_TRANSACTOR}/{endpoint}"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = self.session.request(method, url, json=data, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.json() if resp.text else {}
        except Exception as e:
            logger.warning(f"[Huly] API error {method} {endpoint}: {e}")
            return {}

    def login(self, email: str = "mleads@selfhost", password: str = "mleads2024") -> bool:
        """Login to Huly and get a token."""
        try:
            resp = self.session.post(
                f"{HULY_TRANSACTOR}/api/v1/login",
                json={"email": email, "password": password},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                self.token = data.get("token")
                logger.info("[Huly] Login successful")
                return True
        except Exception as e:
            logger.warning(f"[Huly] Login failed: {e}")
        return False

    def sync_lead(self, lead: dict, user_id: str = None) -> dict | None:
        """
        Sync a lead from MLeads to Huly as a Project.
        Creates a project with tasks for follow-up.
        """
        lead_id = lead.get("id") or lead.get("address_key", "")
        address = lead.get("address", "")
        city = lead.get("city", "")
        trade = lead.get("_trade") or lead.get("ai_trade") or "General"
        score = lead.get("_scoring", {}).get("score", 0) if isinstance(lead.get("_scoring"), dict) else lead.get("score", 0)
        contractor = lead.get("contractor", "")
        phone = lead.get("contact_phone", "")
        value = lead.get("value_float", 0)
        description = lead.get("description", "")
        ai_summary = lead.get("_ai_summary") or lead.get("ai_summary", "")
        urgency = lead.get("_urgency") or lead.get("ai_urgency", "MEDIUM")
        pain_point = lead.get("_key_pain_point") or lead.get("ai_key_pain_point", "")
        upsell = lead.get("_upsell_opportunity") or lead.get("ai_upsell_opportunity", "")
        best_time = lead.get("_best_contact_time") or lead.get("ai_best_contact_time", "")

        project_name = f"[{trade}] {address[:50]}"
        if score >= 90:
            project_name = "🔥 " + project_name
        elif score >= 70:
            project_name = "🌡️ " + project_name

        # Build project description
        desc_parts = [
            f"📍 {address}, {city}",
            f"🎯 Score: {score}/100 | Urgency: {urgency}",
            f"🏗️ Contractor: {contractor}" if contractor else "",
            f"📞 Phone: {phone}" if phone else "",
            f"💰 Value: ${value:,.0f}" if value else "",
            f"\n📋 {description[:200]}" if description else "",
            f"\n🤖 AI: {ai_summary}" if ai_summary else "",
            f"\n💡 Pain: {pain_point}" if pain_point else "",
            f"\n💰 Upsell: {upsell}" if upsell else "",
            f"\n🕑 Best time: {best_time}" if best_time else "",
        ]
        project_desc = "\n".join(p for p in desc_parts if p)

        # Tasks to create
        tasks = []

        if urgency == "HIGH":
            tasks.append({
                "title": "📞 Call contractor ASAP",
                "description": f"Call {contractor} at {phone}. High urgency lead.",
            })
            tasks.append({
                "title": "📧 Send intro email",
                "description": "Send introduction email with company info and availability.",
            })
        elif urgency == "MEDIUM":
            tasks.append({
                "title": "📞 Contact contractor this week",
                "description": f"Call {contractor} at {phone}.",
            })
        else:
            tasks.append({
                "title": "📞 Follow up when available",
                "description": f"Contact {contractor} at {phone} at your convenience.",
            })

        if upsell:
            tasks.append({
                "title": f"💡 Offer upsell: {upsell[:60]}",
                "description": upsell,
            })

        if best_time and best_time != "ANY":
            tasks.append({
                "title": f"🕐 Best contact time: {best_time}",
                "description": f"Try calling during {best_time} for best results.",
            })

        return {
            "project_name": project_name,
            "project_desc": project_desc,
            "tasks": tasks,
            "lead_id": lead_id,
            "trade": trade,
            "score": score,
        }

    def format_for_webhook(self, lead: dict) -> dict:
        """Format lead data for Huly import via webhook/manual entry."""
        return self.sync_lead(lead)


# Singleton
_bridge: HulyBridge | None = None

def get_huly_bridge() -> HulyBridge:
    global _bridge
    if _bridge is None:
        _bridge = HulyBridge()
    return _bridge
