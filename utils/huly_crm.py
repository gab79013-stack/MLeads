"""
utils/huly_crm.py
🔗 MLeads → Huly CRM Integration (via Node.js bridge)

MLeads pushes leads to a local Node.js bridge service that uses
the official Huly platform-api SDK. The bridge handles the Huly
protocol (WebSocket + RPC) while MLeads uses simple REST.

Bridge URL: http://localhost:5010 (default)
  POST /api/push-lead  — Push a lead with tripartite scores
  GET  /api/test       — Test connection to Huly
  GET  /api/health     — Bridge health check

When a lead is pushed:
  1. Bridge creates a contact:Person in Huly
  2. Bridge creates a tracker:Issue (deal) linked to the contact
  3. Tags, scoring, Property DNA included
"""

import os
import logging
import requests
from typing import Optional, Dict

logger = logging.getLogger(__name__)

BRIDGE_URL = os.getenv("HULY_BRIDGE_URL", "http://localhost:5010")

# Minimum scores to push to CRM
MIN_GC_SCORE = int(os.getenv("HULY_MIN_GC_SCORE", "50"))
MIN_SUB_SCORE = int(os.getenv("HULY_MIN_SUB_SCORE", "50"))
MIN_INS_SCORE = int(os.getenv("HULY_MIN_INS_SCORE", "40"))


class HulyCRM:
    """Integration with Huly CRM via the Node.js bridge."""

    def __init__(self):
        self.bridge_url = BRIDGE_URL
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    @property
    def is_configured(self) -> bool:
        """Check if bridge is reachable."""
        try:
            resp = self.session.get(f"{self.bridge_url}/api/health", timeout=5)
            return resp.status_code == 200
        except:
            return False

    def push_lead(self, lead: dict, scores: dict = None) -> Optional[Dict]:
        """Push a lead to Huly CRM via the bridge."""
        scores = scores or lead.get("_tripartite", {})

        # Check minimum scores
        gc_score = scores.get("gc_score", 0)
        sub_score = scores.get("subcontractor_score", 0)
        ins_score = scores.get("insurance_score", 0)

        if gc_score < MIN_GC_SCORE and sub_score < MIN_SUB_SCORE and ins_score < MIN_INS_SCORE:
            logger.debug(f"[Huly] Lead {lead.get('id')} below score thresholds — skipping")
            return None

        try:
            resp = self.session.post(
                f"{self.bridge_url}/api/push-lead",
                json={"lead": lead, "scores": scores},
                timeout=15,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") == "pushed":
                    logger.info(
                        f"[Huly] Pushed lead {lead.get('id')} — "
                        f"contact={result.get('huly_contact_id', '?')[:8]}, "
                        f"deal={result.get('huly_deal_id', '?')[:8]}"
                    )
                return result
            else:
                logger.debug(f"[Huly] Bridge error: {resp.status_code}")
                return None

        except requests.ConnectionError:
            logger.debug("[Huly] Bridge not reachable — skipping")
            return None
        except Exception as e:
            logger.debug(f"[Huly] Push error: {e}")
            return None

    def test_connection(self) -> Dict:
        """Test connection to Huly via bridge."""
        try:
            resp = self.session.get(f"{self.bridge_url}/api/test", timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {"connected": False, "error": f"HTTP {resp.status_code}"}
        except requests.ConnectionError:
            return {"connected": False, "error": "Bridge not reachable"}
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
    """Push a lead to Huly CRM."""
    return get_huly_crm().push_lead(lead, scores)
