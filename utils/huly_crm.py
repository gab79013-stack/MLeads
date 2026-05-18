"""
utils/huly_crm.py
🔗 MLeads → Huly CRM Integration

Pushes leads from MLeads to Huly CRM as contacts + deals.
When a lead is scored (tripartite), it automatically creates:
  - A Contact in Huly (if not exists)
  - A Deal/Opportunity linked to that contact
  - Tags based on disaster type and scoring

Huly API: REST via the transactor service
  - Base URL: http://localhost:8080 (or configured HULY_URL)
  - Auth: Account token from Huly

Flow:
  1. Lead detected by MLeads agents
  2. Tripartite scoring calculated
  3. If gc_score >= 50 or sub_score >= 50:
     a. Check if contact exists in Huly (by email/phone)
     b. Create contact if new
     c. Create deal with lead details + scoring
     d. Tag with disaster type if applicable
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
HULY_WORKSPACE = os.getenv("HULY_WORKSPACE", "mleads")

# Minimum scores to push to CRM
MIN_GC_SCORE = int(os.getenv("HULY_MIN_GC_SCORE", "50"))
MIN_SUB_SCORE = int(os.getenv("HULY_MIN_SUB_SCORE", "50"))
MIN_INS_SCORE = int(os.getenv("HULY_MIN_INS_SCORE", "40"))


class HulyCRM:
    """Integration with Huly CRM for lead management."""

    def __init__(self):
        self.base_url = HULY_URL
        self.token = HULY_TOKEN
        self.workspace = HULY_WORKSPACE
        self.session = requests.Session()
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"
        self.session.headers["Content-Type"] = "application/json"

    @property
    def is_configured(self) -> bool:
        """Check if Huly integration is configured."""
        return bool(self.token)

    def push_lead(self, lead: dict, scores: dict = None) -> Optional[Dict]:
        """
        Push a lead to Huly CRM.
        
        Creates:
          1. Contact (contractor/property owner)
          2. Deal linked to the contact
        
        Args:
            lead: MLeads lead dict
            scores: Tripartite scores dict (optional)
        
        Returns:
            Dict with huly_contact_id and huly_deal_id, or None on failure
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

        result = {"huly_contact_id": None, "huly_deal_id": None}

        # Step 1: Create or find contact
        contact = self._upsert_contact(lead)
        if contact:
            result["huly_contact_id"] = contact.get("id")

        # Step 2: Create deal
        deal = self._create_deal(lead, scores, contact)
        if deal:
            result["huly_deal_id"] = deal.get("id")

        if result["huly_contact_id"] or result["huly_deal_id"]:
            logger.info(
                f"[Huly] Pushed lead {lead.get('id')} — "
                f"contact={result['huly_contact_id']}, deal={result['huly_deal_id']}"
            )

        return result

    def _upsert_contact(self, lead: dict) -> Optional[Dict]:
        """Create or update a contact in Huly."""
        contact_data = {
            "firstName": lead.get("contractor", lead.get("owner", "")).split()[0] if lead.get("contractor", lead.get("owner")) else "Unknown",
            "lastName": " ".join(lead.get("contractor", lead.get("owner", "")).split()[1:]) if lead.get("contractor", lead.get("owner")) else "Contractor",
            "emails": [],
            "phones": [],
            "location": f"{lead.get('city', '')}, CA",
            "company": lead.get("contractor", ""),
            "tags": self._get_lead_tags(lead),
        }

        # Add contact info
        email = lead.get("contact_email", "")
        phone = lead.get("contact_phone", "")
        if email:
            contact_data["emails"].append(email)
        if phone:
            contact_data["phones"].append(phone)

        # Check if contact exists
        existing = self._find_contact(email, phone)
        if existing:
            return existing

        # Create new contact
        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/{self.workspace}/contacts",
                json=contact_data,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.debug(f"[Huly] Contact creation failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"[Huly] Contact creation error: {e}")

        return None

    def _find_contact(self, email: str, phone: str) -> Optional[Dict]:
        """Find existing contact by email or phone."""
        if not email and not phone:
            return None

        try:
            # Search by email
            if email:
                resp = self.session.get(
                    f"{self.base_url}/api/v1/{self.workspace}/contacts",
                    params={"email": email},
                    timeout=10,
                )
                if resp.status_code == 200:
                    results = resp.json().get("items", resp.json()) if isinstance(resp.json(), dict) else resp.json()
                    if results and len(results) > 0:
                        return results[0] if isinstance(results, list) else results

            # Search by phone
            if phone:
                resp = self.session.get(
                    f"{self.base_url}/api/v1/{self.workspace}/contacts",
                    params={"phone": phone},
                    timeout=10,
                )
                if resp.status_code == 200:
                    results = resp.json().get("items", resp.json()) if isinstance(resp.json(), dict) else resp.json()
                    if results and len(results) > 0:
                        return results[0] if isinstance(results, list) else results
        except Exception as e:
            logger.debug(f"[Huly] Contact search error: {e}")

        return None

    def _create_deal(self, lead: dict, scores: dict, contact: Optional[Dict]) -> Optional[Dict]:
        """Create a deal/opportunity in Huly CRM."""
        gc_score = scores.get("gc_score", 0)
        sub_score = scores.get("subcontractor_score", 0)
        ins_score = scores.get("insurance_score", 0)

        # Determine deal stage based on scores
        max_score = max(gc_score, sub_score, ins_score)
        if max_score >= 90:
            stage = "hot"
        elif max_score >= 70:
            stage = "warm"
        elif max_score >= 50:
            stage = "qualified"
        else:
            stage = "new"

        deal_data = {
            "title": f"{lead.get('address', 'Lead')} — {lead.get('city', '')}",
            "description": self._build_deal_description(lead, scores),
            "stage": stage,
            "value": lead.get("value_float", 0) or 0,
            "contactId": contact.get("id") if contact else None,
            "tags": self._get_lead_tags(lead),
            "customFields": {
                "mleads_id": lead.get("id", ""),
                "gc_score": gc_score,
                "sub_score": sub_score,
                "ins_score": ins_score,
                "source": lead.get("agent_sources", lead.get("_agent_key", "")),
                "disaster_type": lead.get("_disaster_type", ""),
                "trade": lead.get("_trade", ""),
            },
        }

        try:
            resp = self.session.post(
                f"{self.base_url}/api/v1/{self.workspace}/deals",
                json=deal_data,
                timeout=15,
            )
            if resp.status_code in (200, 201):
                return resp.json()
            else:
                logger.debug(f"[Huly] Deal creation failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.debug(f"[Huly] Deal creation error: {e}")

        return None

    def _build_deal_description(self, lead: dict, scores: dict) -> str:
        """Build a rich deal description for Huly."""
        lines = []

        # Lead summary
        desc = lead.get("description", "")
        if desc:
            lines.append(desc)

        # Scoring
        from utils.tripartite_scoring import format_tripartite_summary
        try:
            lines.append(format_tripartite_summary(scores))
        except:
            lines.append(f"Sub: {scores.get('subcontractor_score', 0)} | GC: {scores.get('gc_score', 0)} | Ins: {scores.get('insurance_score', 0)}")

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

        return "\n".join(lines)

    def _get_lead_tags(self, lead: dict) -> list:
        """Generate tags for a lead."""
        tags = []

        # Trade tags
        trade = lead.get("_trade", "").lower()
        if trade:
            tags.append(trade)

        # Disaster tags
        disaster = lead.get("_disaster_type", "")
        if disaster:
            tags.append(f"disaster-{disaster}")

        # Score tags
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

        # Source tag
        source = lead.get("_agent_key", "")
        if source:
            tags.append(f"source-{source}")

        # City tag
        city = lead.get("city", "").lower().replace(" ", "-")
        if city:
            tags.append(city)

        return tags

    def get_deals(self, stage: str = None, limit: int = 50) -> list:
        """Get deals from Huly CRM."""
        if not self.is_configured:
            return []

        try:
            params = {"limit": limit}
            if stage:
                params["stage"] = stage

            resp = self.session.get(
                f"{self.base_url}/api/v1/{self.workspace}/deals",
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", data) if isinstance(data, dict) else data
        except Exception as e:
            logger.debug(f"[Huly] Get deals error: {e}")

        return []

    def get_contacts(self, limit: int = 50) -> list:
        """Get contacts from Huly CRM."""
        if not self.is_configured:
            return []

        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/{self.workspace}/contacts",
                params={"limit": limit},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("items", data) if isinstance(data, dict) else data
        except Exception as e:
            logger.debug(f"[Huly] Get contacts error: {e}")

        return []


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
