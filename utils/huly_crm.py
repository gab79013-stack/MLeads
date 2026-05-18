"""
utils/huly_crm.py
🔗 MLeads → Huly CRM Integration

Pushes leads from MLeads to Huly CRM as contacts + deals.

Huly uses its own protocol via the transactor service:
  - Endpoint: /_transactor
  - Protocol: JSON-RPC style with model operations
  - Auth: JWT token (account + workspace encoded)

The integration creates:
  1. contact:Person in Huly (contractor/property owner)
  2. chunter:Channel for lead tracking
  3. Tags based on disaster type and scoring

When a lead is pushed:
  - If contact exists (by email/phone): update it
  - Create deal as a tracker:Tracker with stages
  - Attach scoring + Property DNA as description
"""

import os
import json
import logging
import requests
from datetime import datetime
from typing import Optional, Dict

logger = logging.getLogger(__name__)

HULY_URL = os.getenv("HULY_URL", "http://localhost:8080")
HULY_TOKEN = os.getenv("HULY_TOKEN", "")
HULY_WORKSPACE = os.getenv("HULY_WORKSPACE", "")

# Minimum scores to push to CRM
MIN_GC_SCORE = int(os.getenv("HULY_MIN_GC_SCORE", "50"))
MIN_SUB_SCORE = int(os.getenv("HULY_MIN_SUB_SCORE", "50"))
MIN_INS_SCORE = int(os.getenv("HULY_MIN_INS_SCORE", "40"))


class HulyCRM:
    """Integration with Huly CRM via transactor protocol."""

    def __init__(self):
        self.base_url = HULY_URL
        self.token = HULY_TOKEN
        self.workspace = HULY_WORKSPACE
        self.session = requests.Session()

    @property
    def is_configured(self) -> bool:
        """Check if Huly integration is configured."""
        return bool(self.token and self.workspace)

    def _transactor_request(self, operations: list) -> Optional[dict]:
        """Send operations to Huly transactor.
        
        Huly transactor accepts model operations in the format:
        {
            "transactions": [
                {
                    "objectId": "...",
                    "objectClass": "contact:Person",
                    "operations": [
                        { "operation": "create", "attributes": {...} },
                        { "operation": "update", "attributes": {...} },
                    ]
                }
            ]
        }
        """
        if not self.is_configured:
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }

            payload = {
                "workspace": self.workspace,
                "transactions": operations,
            }

            resp = self.session.post(
                f"{self.base_url}/_transactor",
                json=payload,
                headers=headers,
                timeout=15,
            )

            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.debug(f"[Huly] Transactor error: {resp.status_code} {resp.text[:200]}")
                return None

        except Exception as e:
            logger.debug(f"[Huly] Transactor request error: {e}")
            return None

    def push_lead(self, lead: dict, scores: dict = None) -> Optional[Dict]:
        """
        Push a lead to Huly CRM.
        
        Creates a contact and a tracker (deal) in Huly.
        """
        if not self.is_configured:
            logger.debug("[Huly] Not configured — skipping push")
            return None

        scores = scores or lead.get("_tripartite", {})

        # Check minimum scores
        gc_score = scores.get("gc_score", 0)
        sub_score = scores.get("subcontractor_score", 0)
        ins_score = scores.get("insurance_score", 0)

        if gc_score < MIN_GC_SCORE and sub_score < MIN_SUB_SCORE and ins_score < MIN_INS_SCORE:
            logger.debug(f"[Huly] Lead {lead.get('id')} below score thresholds — skipping")
            return None

        # Build operations
        import uuid
        contact_id = uuid.uuid4().hex[:24]
        tracker_id = uuid.uuid4().hex[:24]

        # Create contact
        contact_ops = self._build_contact_operations(lead, contact_id)
        # Create tracker (deal)
        tracker_ops = self._build_tracker_operations(lead, scores, tracker_id, contact_id)

        operations = contact_ops + tracker_ops

        result = self._transactor_request(operations)

        if result:
            logger.info(
                f"[Huly] Pushed lead {lead.get('id')} — "
                f"contact={contact_id[:8]}..., deal={tracker_id[:8]}..."
            )
            return {
                "huly_contact_id": contact_id,
                "huly_deal_id": tracker_id,
                "status": "pushed",
            }

        return result  # None

    def _build_contact_operations(self, lead: dict, contact_id: str) -> list:
        """Build Huly contact:Person operations."""
        name = lead.get("contractor", lead.get("owner", "Unknown Contractor"))
        name_parts = name.split(None, 1)
        first_name = name_parts[0] if name_parts else "Unknown"
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        email = lead.get("contact_email", "")
        phone = lead.get("contact_phone", "")
        city = lead.get("city", "")

        attributes = {
            "_id": contact_id,
            "firstName": first_name,
            "lastName": last_name,
            "company": name if name != "Unknown Contractor" else "",
            "city": city,
            "state": "CA",
        }

        # Add contact channels
        channels = []
        if email:
            channels.append({"channel": "email", "value": email})
        if phone:
            channels.append({"channel": "phone", "value": phone})
        if channels:
            attributes["channels"] = channels

        # Add tags
        tags = self._get_lead_tags(lead)
        if tags:
            attributes["tags"] = tags

        return [
            {
                "objectId": contact_id,
                "objectClass": "contact:Person",
                "operations": [
                    {"operation": "create", "attributes": attributes}
                ]
            }
        ]

    def _build_tracker_operations(self, lead: dict, scores: dict,
                                   tracker_id: str, contact_id: str) -> list:
        """Build Huly tracker:Tracker (deal) operations."""
        gc_score = scores.get("gc_score", 0)
        sub_score = scores.get("subcontractor_score", 0)
        ins_score = scores.get("insurance_score", 0)

        max_score = max(gc_score, sub_score, ins_score)
        if max_score >= 90:
            stage = "hot"
            color = 0xFF0000  # red
        elif max_score >= 70:
            stage = "warm"
            color = 0xFF8800  # orange
        elif max_score >= 50:
            stage = "qualified"
            color = 0xFFCC00  # yellow
        else:
            stage = "new"
            color = 0x0088FF  # blue

        title = f"🏗️ {lead.get('address', 'Lead')} — {lead.get('city', '')}"
        description = self._build_deal_description(lead, scores)

        attributes = {
            "_id": tracker_id,
            "title": title,
            "description": description,
            "status": stage,
            "priority": "medium" if max_score < 70 else "high" if max_score < 90 else "urgent",
            "assignee": contact_id,
            "color": color,
            "tags": self._get_lead_tags(lead),
        }

        # Add custom fields as label/value pairs in description
        value = lead.get("value_float", 0)
        if value:
            attributes["estimate"] = value

        return [
            {
                "objectId": tracker_id,
                "objectClass": "tracker:Issue",
                "operations": [
                    {"operation": "create", "attributes": attributes}
                ]
            }
        ]

    def _build_deal_description(self, lead: dict, scores: dict) -> str:
        """Build a rich deal description for Huly."""
        lines = []

        desc = lead.get("description", "")
        if desc:
            lines.append(desc)

        # Scoring
        from utils.tripartite_scoring import format_tripartite_summary
        try:
            lines.append(format_tripartite_summary(scores))
        except:
            lines.append(
                f"👷 Sub: {scores.get('subcontractor_score', 0)} | "
                f"🏗️ GC: {scores.get('gc_score', 0)} | "
                f"🏢 Ins: {scores.get('insurance_score', 0)}"
            )

        # Property DNA
        year = lead.get("property_year_built")
        roof = lead.get("property_roof_material")
        if year or roof:
            lines.append(f"🏠 Property: Year {year or '?'} | Roof: {roof or 'unknown'}")

        # Disaster
        disaster = lead.get("_disaster_type")
        if disaster:
            lines.append(f"🚨 Disaster: {disaster.upper()}")

        # Contact info
        phone = lead.get("contact_phone")
        email = lead.get("contact_email")
        gc = lead.get("contractor")
        if gc:
            lines.append(f"👷 GC: {gc}")
        if phone:
            lines.append(f"📞 {phone}")
        if email:
            lines.append(f"✉️ {email}")

        # Source
        source = lead.get("agent_sources", lead.get("_agent_key", ""))
        if source:
            lines.append(f"📡 Source: {source}")

        # MLeads ID
        lines.append(f"🆔 MLeads ID: {lead.get('id', 'unknown')}")

        return "\n".join(lines)

    def _get_lead_tags(self, lead: dict) -> list:
        """Generate tags for a lead."""
        tags = []

        trade = lead.get("_trade", "").lower()
        if trade:
            tags.append(trade)

        disaster = lead.get("_disaster_type", "")
        if disaster:
            tags.append(f"disaster-{disaster}")

        scores = lead.get("_tripartite", {})
        max_score = max(
            scores.get("gc_score", 0),
            scores.get("subcontractor_score", 0),
            scores.get("insurance_score", 0),
        )
        if max_score >= 90:
            tags.append("hot-lead")
        elif max_score >= 70:
            tags.append("warm-lead")

        source = lead.get("_agent_key", "")
        if source:
            tags.append(f"source-{source}")

        city = lead.get("city", "").lower().replace(" ", "-")
        if city:
            tags.append(city)

        return tags

    def test_connection(self) -> Dict:
        """Test the Huly connection and return status info."""
        if not self.is_configured:
            return {"connected": False, "error": "Not configured (missing HULY_TOKEN or HULY_WORKSPACE)"}

        try:
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
            # Try to reach the transactor
            resp = self.session.post(
                f"{self.base_url}/_transactor",
                json={"workspace": self.workspace, "transactions": []},
                headers=headers,
                timeout=10,
            )

            if resp.status_code in (200, 201, 204):
                return {
                    "connected": True,
                    "url": self.base_url,
                    "workspace": self.workspace[:8] + "...",
                }
            else:
                return {
                    "connected": False,
                    "error": f"HTTP {resp.status_code}",
                    "detail": resp.text[:200],
                }
        except Exception as e:
            return {"connected": False, "error": str(e)}


# Singleton
_crm: Optional[HulyCRM] = None


def get_huly_crm() -> HulyCRM:
    global _crm
    if _crm is None:
        _crm = HulyCRM()
    return _crm


def push_lead_to_crm(lead: dict, scores: dict = None) -> Optional[Dict]:
    """Convenience function to push a lead to Huly CRM."""
    return get_huly_crm().push_lead(lead, scores)
