"""
utils/competitive_analyzer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trade Competition Analyzer — Competitive intelligence for subcontractors

Analyzes competition density by trade and location using CSLB data
and permit activity. Helps subcontractors understand:
  - How many licensed competitors operate in their area
  - Which areas are underserved (opportunity zones)
  - Permit volume trends by trade and city

Exposed as /competition command in the Telegram bot.

Usage:
    from utils.competitive_analyzer import analyze_competition
    report = analyze_competition(trade="DEMOLITION", city="San Francisco")
"""

import logging
import os
from typing import Dict, List, Optional, Any
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

# CSLB classification codes for each trade
TRADE_CSLB_CODES = {
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


def analyze_competition(
    trade: str,
    city: str = "",
    county: str = "",
) -> Dict[str, Any]:
    """
    Analyze competition for a specific trade in a geographic area.

    Uses CSLB data to count active licensed contractors and
    permit data to estimate market activity.

    Args:
        trade: Trade name (e.g. "DEMOLITION", "ROOFING")
        city: City to analyze
        county: County to analyze (broader scope)

    Returns:
        Dict with competition metrics and insights
    """
    trade = trade.upper()
    cslb_code = TRADE_CSLB_CODES.get(trade, "")

    report = {
        "trade": trade,
        "cslb_code": cslb_code,
        "city": city,
        "county": county,
        "competitors": [],
        "competitor_count": 0,
        "market_density": "UNKNOWN",  # LOW, MEDIUM, HIGH, SATURATED
        "permit_volume_30d": 0,
        "avg_project_value": 0,
        "opportunity_score": 0,  # 0-100
        "insights": [],
        "analyzed_at": datetime.utcnow().isoformat(),
    }

    # ── 1. Count competitors via CSLB ────────────────────────────
    competitors = _search_cslb_by_trade(cslb_code, city, county)
    report["competitors"] = competitors[:20]  # Top 20
    report["competitor_count"] = len(competitors)

    # ── 2. Get permit volume from DB ─────────────────────────────
    permit_stats = _get_permit_stats(trade, city)
    report["permit_volume_30d"] = permit_stats.get("count_30d", 0)
    report["avg_project_value"] = permit_stats.get("avg_value", 0)

    # ── 3. Calculate market density ──────────────────────────────
    comp_count = report["competitor_count"]
    if comp_count >= 50:
        report["market_density"] = "SATURATED"
    elif comp_count >= 25:
        report["market_density"] = "HIGH"
    elif comp_count >= 10:
        report["market_density"] = "MEDIUM"
    else:
        report["market_density"] = "LOW"

    # ── 4. Calculate opportunity score ───────────────────────────
    # High permits + low competitors = high opportunity
    permits = report["permit_volume_30d"]
    if comp_count > 0 and permits > 0:
        ratio = permits / comp_count
        report["opportunity_score"] = min(int(ratio * 20), 100)
    elif permits > 0 and comp_count == 0:
        report["opportunity_score"] = 95  # No competitors, has demand
    else:
        report["opportunity_score"] = 50  # Unknown

    # ── 5. Generate insights ─────────────────────────────────────
    insights = _generate_insights(report)
    report["insights"] = insights

    return report


def _search_cslb_by_trade(
    cslb_code: str,
    city: str = "",
    county: str = "",
) -> List[Dict]:
    """
    Search CSLB for active contractors with a specific classification.
    """
    if not cslb_code:
        return []

    try:
        from utils.lead_enrichment import CSLB_API_BASE, CSLB_API_KEY
        import requests

        params = {
            "classification": cslb_code,
            "status": "Active",
            "limit": 100,
        }
        if city:
            params["city"] = city
        if county:
            params["county"] = county

        headers = {}
        if CSLB_API_KEY:
            headers["Authorization"] = f"Bearer {CSLB_API_KEY}"

        resp = requests.get(
            f"{CSLB_API_BASE}/LicenseSearch",
            params=params,
            headers=headers,
            timeout=15,
        )

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            return [
                {
                    "license": r.get("licenseNumber", ""),
                    "name": r.get("businessName", ""),
                    "city": r.get("address", {}).get("city", ""),
                    "status": r.get("status", ""),
                }
                for r in results
            ]
    except Exception as e:
        logger.debug(f"[CompAnalyzer] CSLB search failed: {e}")

    return []


def _get_permit_stats(trade: str, city: str = "") -> Dict:
    """Get permit statistics for a trade from the leads DB."""
    try:
        import sqlite3
        DB_PATH = os.getenv("DB_PATH", "data/leads.db")
        if not os.path.exists(DB_PATH):
            return {}

        with sqlite3.connect(DB_PATH) as conn:
            # Count permits in last 30 days matching this trade
            trade_lower = trade.lower()
            query = """
                SELECT COUNT(*), AVG(CAST(value_float AS REAL))
                FROM sent_leads
                WHERE LOWER(description) LIKE ?
            """
            params = [f"%{trade_lower}%"]

            if city:
                query += " AND LOWER(city) LIKE ?"
                params.append(f"%{city.lower()}%")

            c = conn.cursor()
            c.execute(query, params)
            row = c.fetchone()

            if row:
                return {
                    "count_30d": row[0] or 0,
                    "avg_value": round(row[1] or 0, 0),
                }
    except Exception as e:
        logger.debug(f"[CompAnalyzer] DB query failed: {e}")

    return {}


def _generate_insights(report: Dict) -> List[str]:
    """Generate actionable insights from competition data."""
    insights = []
    trade = report["trade"]
    city = report["city"] or "this area"
    density = report["market_density"]
    opp_score = report["opportunity_score"]
    permits = report["permit_volume_30d"]

    if density == "LOW":
        insights.append(
            f"Low competition for {trade} in {city} — "
            f"only {report['competitor_count']} active licensed subs. "
            f"Good opportunity to establish presence."
        )
    elif density == "SATURATED":
        insights.append(
            f"Highly competitive market for {trade} in {city} — "
            f"{report['competitor_count']} active subs. "
            f"Consider specializing or expanding to nearby areas."
        )

    if opp_score >= 75:
        insights.append(
            f"High opportunity score ({opp_score}/100): "
            f"demand ({permits} permits) outpaces competition."
        )
    elif opp_score <= 25:
        insights.append(
            f"Low opportunity score ({opp_score}/100): "
            f"market may be saturated relative to demand."
        )

    if report["avg_project_value"] > 100000:
        insights.append(
            f"Average project value: ${report['avg_project_value']:,.0f} — "
            f"high-value market worth pursuing."
        )

    return insights


def format_competition_for_telegram(report: Dict) -> str:
    """Format competition analysis for Telegram message."""
    density_emoji = {
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🟠",
        "SATURATED": "🔴",
        "UNKNOWN": "⚪",
    }

    trade = report.get("trade", "")
    city = report.get("city", "Unknown")
    code = report.get("cslb_code", "")

    lines = [
        f"📊 *Competition Report — {trade} ({code})*",
        f"📍 {city}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"{density_emoji.get(report.get('market_density', ''), '⚪')} "
        f"Density: *{report.get('market_density', 'Unknown')}* "
        f"({report.get('competitor_count', 0)} active subs)",
        f"📋 Permits (30d): *{report.get('permit_volume_30d', 0)}*",
        f"💰 Avg value: *${report.get('avg_project_value', 0):,.0f}*",
        f"🎯 Opportunity: *{report.get('opportunity_score', 0)}/100*",
    ]

    insights = report.get("insights", [])
    if insights:
        lines.append("")
        lines.append("💡 *Insights:*")
        for insight in insights[:3]:
            lines.append(f"  • {insight}")

    return "\n".join(lines)
