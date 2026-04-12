"""
utils/matching_engine.py
━━━━━━━━━━━━━━━━━━━━━━━━
Lead ↔ Subcontractor Matching Engine

Matches construction leads to registered subcontractors based on:
  1. Trade/license match (C-21, C-33, C-39, etc.)
  2. Geographic proximity (city match or radius)
  3. Availability and qualification data
  4. Lead score and urgency

Used by agents/base.py after AI classification to determine
the best-matched sub for each lead.

CSLB License Classifications mapped:
  C-2  Insulation      C-5  Framing        C-8  Concrete
  C-9  Drywall         C-10 Electrical     C-15 Flooring
  C-17 Glazing         C-20 HVAC           C-21 Demolition
  C-27 Landscaping     C-33 Painting       C-36 Plumbing
  C-39 Roofing
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ── Trade → CSLB License mapping ────────────────────────────────────

TRADE_TO_LICENSE = {
    "DEMOLITION":  "C-21",
    "PAINTING":    "C-33",
    "ROOFING":     "C-39",
    "INSULATION":  "C-2",
    "FRAMING":     "C-5",
    "CONCRETE":    "C-8",
    "DRYWALL":     "C-9",
    "ELECTRICAL":  "C-10",
    "FLOORING":    "C-15",
    "WINDOWS":     "C-17",
    "HVAC":        "C-20",
    "LANDSCAPING": "C-27",
    "PLUMBING":    "C-36",
}

LICENSE_TO_TRADE = {v: k for k, v in TRADE_TO_LICENSE.items()}

# Related trades — when a lead is classified as one trade,
# these adjacent trades may also be interested
RELATED_TRADES = {
    "DEMOLITION": ["ROOFING", "PAINTING", "FRAMING", "CONCRETE", "INSULATION"],
    "ROOFING":    ["PAINTING", "INSULATION", "FRAMING"],
    "PAINTING":   ["DRYWALL", "ROOFING"],
    "DRYWALL":    ["PAINTING", "FRAMING", "INSULATION"],
    "FRAMING":    ["CONCRETE", "DRYWALL", "ROOFING", "INSULATION"],
    "CONCRETE":   ["FRAMING", "PLUMBING"],
    "ELECTRICAL": ["HVAC", "PLUMBING"],
    "HVAC":       ["ELECTRICAL", "PLUMBING", "INSULATION"],
    "PLUMBING":   ["HVAC", "ELECTRICAL", "CONCRETE"],
    "INSULATION": ["DRYWALL", "FRAMING", "HVAC"],
    "FLOORING":   ["PAINTING", "DRYWALL"],
    "WINDOWS":    ["FRAMING", "PAINTING"],
    "LANDSCAPING": [],
}


@dataclass
class MatchResult:
    """Result of matching a lead to a subcontractor."""
    lead_id: str
    sub_id: str
    sub_name: str
    sub_chat_id: str
    match_score: float          # 0.0 - 1.0
    match_reasons: list = field(default_factory=list)
    trade_match: str = ""       # PRIMARY or RELATED
    license_class: str = ""     # e.g. "C-21"
    distance_miles: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lead_id": self.lead_id,
            "sub_id": self.sub_id,
            "sub_name": self.sub_name,
            "match_score": round(self.match_score, 3),
            "match_reasons": self.match_reasons,
            "trade_match": self.trade_match,
            "license_class": self.license_class,
            "distance_miles": round(self.distance_miles, 1),
        }


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def match_lead_to_subs(
    lead: dict,
    agent_key: str,
    max_results: int = 10,
) -> List[MatchResult]:
    """
    Match a lead to registered subcontractors.

    Uses bot_users data to find subs whose:
      - selected services match the lead's trade (primary or related)
      - city/location is within range
      - subscription is active

    Args:
        lead: Lead dict with _trade, city, lat/lon, etc.
        agent_key: Source agent key (e.g. "deconstruction")
        max_results: Max matches to return

    Returns:
        List of MatchResult sorted by match_score descending
    """
    try:
        from utils import bot_users as bu
    except Exception:
        return []

    lead_trade = lead.get("_trade", "GENERAL")
    lead_city = (lead.get("city") or "").strip().lower()
    lead_lat = lead.get("latitude") or lead.get("lat")
    lead_lon = lead.get("longitude") or lead.get("lon") or lead.get("lng")
    lead_score = lead.get("_scoring", {}).get("score", 50)
    lead_urgency = lead.get("_urgency", "MEDIUM")

    # Get the service keys this lead maps to
    lead_service_keys = bu._lead_service_keys(lead, agent_key)

    # Primary trade → service key mapping
    primary_license = TRADE_TO_LICENSE.get(lead_trade, "")
    related_trades = RELATED_TRADES.get(lead_trade, [])

    # Get all active subscribers
    try:
        from utils.web_db import get_db_connection
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            """
            SELECT * FROM bot_users
             WHERE is_active = 1
               AND state = ?
               AND subscription_status IN ('trial', 'paid')
            """,
            (bu.STATE_ACTIVE,),
        )
        rows = [bu.row_to_dict(r) for r in c.fetchall()]
        conn.close()
    except Exception as e:
        logger.debug(f"[matching] DB error: {e}")
        return []

    results = []
    for user in rows:
        if not bu.is_subscription_active(user):
            continue

        user_services = set(user.get("services") or [])
        if not user_services:
            continue

        score = 0.0
        reasons = []
        trade_match_type = ""

        # ── Trade match (0.0 - 0.5) ─────────────────────────────
        # Check if user's services overlap with lead's trade
        if user_services & lead_service_keys:
            # Direct match
            score += 0.5
            reasons.append(f"Direct service match: {user_services & lead_service_keys}")
            trade_match_type = "PRIMARY"
        else:
            # Check related trades
            related_service_keys = set()
            for rt in related_trades:
                rt_lower = rt.lower()
                for svc_key, _label in bu.AVAILABLE_SERVICES:
                    if rt_lower in svc_key or svc_key in rt_lower:
                        related_service_keys.add(svc_key)

            if user_services & related_service_keys:
                score += 0.25
                reasons.append(f"Related trade match: {user_services & related_service_keys}")
                trade_match_type = "RELATED"
            else:
                continue  # No trade overlap at all — skip

        # ── Geography match (0.0 - 0.3) ─────────────────────────
        user_city = (user.get("city") or "").strip().lower()
        user_lat = user.get("latitude")
        user_lon = user.get("longitude")
        distance = 0.0

        if lead_lat and lead_lon and user_lat and user_lon:
            try:
                distance = _haversine_miles(
                    float(lead_lat), float(lead_lon),
                    float(user_lat), float(user_lon)
                )
                radius = float(user.get("radius_miles") or bu.DEFAULT_RADIUS_MILES)
                if distance <= radius:
                    # Closer = higher score
                    geo_score = 0.3 * (1.0 - distance / radius)
                    score += geo_score
                    reasons.append(f"{distance:.0f} mi away (within {radius:.0f} mi radius)")
                else:
                    continue  # Out of range
            except (TypeError, ValueError):
                pass
        elif user_city and lead_city:
            if user_city == lead_city:
                score += 0.3
                reasons.append(f"Same city: {lead_city}")
            elif user_city in lead_city or lead_city in user_city:
                score += 0.15
                reasons.append(f"City overlap: {lead_city}")

        # ── Lead quality boost (0.0 - 0.2) ──────────────────────
        if lead_score >= 80:
            score += 0.15
            reasons.append(f"High-quality lead (score {lead_score})")
        elif lead_score >= 60:
            score += 0.10

        if lead_urgency == "HIGH":
            score += 0.05
            reasons.append("HIGH urgency")

        results.append(MatchResult(
            lead_id=lead.get("id", ""),
            sub_id=str(user.get("id", "")),
            sub_name=user.get("first_name") or user.get("username") or "Sub",
            sub_chat_id=str(user.get("chat_id", "")),
            match_score=min(score, 1.0),
            match_reasons=reasons[:3],
            trade_match=trade_match_type,
            license_class=primary_license,
            distance_miles=distance,
        ))

    # Sort by match score descending
    results.sort(key=lambda r: r.match_score, reverse=True)
    return results[:max_results]


def get_trade_license(trade: str) -> str:
    """Get CSLB license class for a trade."""
    return TRADE_TO_LICENSE.get(trade.upper(), "")


def get_related_trades(trade: str) -> List[str]:
    """Get trades related to the given trade."""
    return RELATED_TRADES.get(trade.upper(), [])


def format_match_summary(matches: List[MatchResult]) -> str:
    """Format match results for Telegram notification."""
    if not matches:
        return "No matching subcontractors found."
    lines = [f"🔗 *{len(matches)} matching sub(s):*"]
    for m in matches[:5]:
        lines.append(
            f"  • {m.sub_name} ({m.trade_match}) — "
            f"score {m.match_score:.0%}"
            + (f" — {m.distance_miles:.0f} mi" if m.distance_miles else "")
        )
    return "\n".join(lines)
