"""
agents/flooring_concrete_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Flooring & Concrete Permits Agent

Fuentes:
  1. Socrata — Permisos de flooring, concrete, foundation
  2. USGS Earthquake hazards — Post-quake foundation inspections
 3. City building safety — Slab/foundation repair permits

Detecta oportunidades para subcontratistas:
  - Flooring: hardwood, tile, laminate, vinyl, carpet, epoxy
  - Concrete: foundation, slab, sidewalk, driveway, flatwork, footing
  - Post-disaster foundation repair leads

Intervalo default: 240 min
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from agents.base import BaseAgent

logger = logging.getLogger(__name__)

AGENT_KEY = "flooring_concrete"

SOCRATA_SOURCES = {
    "sf":       {"domain": "data.sfgov.org",   "dataset": "i98e-djp9", "city": "San Francisco"},
    "oakland":  {"domain": "data.oaklandca.gov","dataset": "3xq4-76g3", "city": "Oakland"},
    "sanjose":  {"domain": "data.sanjoseca.gov","dataset": "5kp3-qn2w", "city": "San Jose"},
    "dallas":   {"domain": "www.dallasopendata.com","dataset": "8rn6-ky5r", "city": "Dallas"},
    "austin":   {"domain": "data.austintexas.gov","dataset": "3syk-w9gj", "city": "Austin"},
    "honolulu": {"domain": "data.honolulu.gov",  "dataset": "m5a4-68h5", "city": "Honolulu"},
}

FLOORING_KEYWORDS = [
    "flooring", "hardwood floor", "tile floor", "floor tile",
    "laminate", "vinyl plank", "carpet", "epoxy floor",
    "subfloor", "ceramic tile", "floor install", "wood floor",
    "concrete", "cement", "foundation", "slab", "sidewalk",
    "driveway", "flatwork", "footing", "stem wall", "curb",
    "concrete work", "slab on grade", "retaining wall",
    "post tension", "rebar", "concrete slab", "foundation repair",
]

EXCLUDE_KEYWORDS = ["re-inspection", "cancel", "expired", "void"]


class FlooringConcreteAgent(BaseAgent):
    name      = "Flooring & Concrete Agent"
    emoji     = "🧱"
    agent_key = AGENT_KEY

    def __init__(self):
        self.app_token = os.getenv("SOCRATA_APP_TOKEN", "")
        self.timeout   = int(os.getenv("SOURCE_TIMEOUT", "30"))
        self.months    = int(os.getenv("FLOORING_MONTHS", "6"))

    def fetch_leads(self) -> list:
        all_leads = []
        for src_key, src in SOCRATA_SOURCES.items():
            try:
                leads = self._fetch_socrata(src_key, src)
                all_leads.extend(leads)
            except Exception as e:
                logger.warning(f"[{AGENT_KEY}] Error en {src_key}: {e}")
        logger.info(f"[{AGENT_KEY}] Total leads: {len(all_leads)}")
        return all_leads

    def _fetch_socrata(self, src_key: str, src: dict) -> list:
        domain  = src["domain"]
        dataset = src["dataset"]
        city    = src["city"]

        base_url = f"https://{domain}/resource/{dataset}.json"
        since = (datetime.utcnow() - timedelta(days=self.months * 30)).strftime("%Y-%m-%dT00:00:00")

        keyword_clauses = []
        for kw in FLOORING_KEYWORDS:
            keyword_clauses.append(f"lower(description) like lower('%{kw}%')")
            keyword_clauses.append(f"lower(permit_type) like lower('%{kw}%')")
        kw_filter = " OR ".join(keyword_clauses)

        where = f"({kw_filter}) AND filed_date >= '{since}'"
        params = {
            "$where": where,
            "$order": "filed_date DESC",
            "$limit": 500,
        }
        if self.app_token:
            params["$$app_token"] = self.app_token

        try:
            resp = requests.get(base_url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            rows = resp.json()
        except Exception as e:
            logger.debug(f"[{AGENT_KEY}] Socrata {src_key} error: {e}")
            return []

        leads = []
        for r in rows:
            desc = (r.get("description") or "").lower()
            ptype = (r.get("permit_type") or "").lower()
            text = f"{desc} {ptype}"

            if any(ex in desc or ex in ptype for ex in EXCLUDE_KEYWORDS):
                continue
            if not any(kw in text for kw in FLOORING_KEYWORDS):
                continue

            lead = self._normalize(r, city, src_key)
            if lead:
                leads.append(lead)

        logger.info(f"[{AGENT_KEY}] {src_key}: {len(leads)} flooring/concrete leads")
        return leads

    def _normalize(self, r: dict, city: str, src_key: str) -> dict | None:
        address = r.get("street_name") or r.get("address") or r.get("location_1_address") or ""
        if not address:
            return None

        permit_id = r.get("permit_number") or r.get("record_id") or src_key + "_" + str(r.get(":id", ""))
        contractor = r.get("contractor_name") or r.get("applicant_name") or ""
        owner = r.get("owner_name") or ""
        contact_phone = r.get("contractor_phone") or r.get("phone") or ""
        value_str = r.get("estimated_cost") or r.get("permit_value") or "0"
        try:
            value_float = float(str(value_str).replace("$", "").replace(",", ""))
        except (ValueError, TypeError):
            value_float = 0
        description = r.get("description") or r.get("work_description") or ""
        permit_type = r.get("permit_type") or r.get("record_type") or ""
        issue_date = r.get("filed_date") or r.get("issued_date") or r.get("status_date") or ""
        lat = r.get("latitude") or ""
        lon = r.get("longitude") or ""

        # Determine sub-type for service_type
        text = f"{description} {permit_type}".lower()
        if any(kw in text for kw in ["concrete", "cement", "foundation", "slab", "footing", "driveway", "sidewalk", "flatwork", "curb", "stem wall"]):
            svc = "concrete"
        else:
            svc = "flooring"

        addr_key = address.split()[0].lower() + chr(32) + chr(32).join(address.lower().split()[:3]) + chr(32) + city.lower()

        return {
            "id": f"floorcon_{permit_id}",
            "address": f"{address}, {city}",
            "city": city,
            "address_key": addr_key,
            "permit_id": permit_id,
            "permit_type": permit_type,
            "description": description,
            "contractor": contractor,
            "owner": owner,
            "contact_phone": contact_phone,
            "value_float": value_float,
            "issue_date": issue_date,
            "lat": lat,
            "lon": lon,
            "service_type": svc,
            "source": f"floorcon_{src_key}",
            "_agent_key": AGENT_KEY,
        }

    def notify(self, lead: dict):
        from utils.telegram import send_message
        from utils.lead_scoring import score_lead

        scoring = score_lead(lead)
        lead["_scoring"] = scoring

        vf = lead.get("value_float") or 0
        value_str = f"${vf:,.0f}" if vf else "N/A"
        phone = lead.get("contact_phone", "")
        contractor = lead.get("contractor", "")
        urgency = lead.get("_urgency", "")
        scope = lead.get("_project_scope", "")
        pain = lead.get("_key_pain_point", "")
        upsell = lead.get("_upsell_opportunity", "")
        sub_trades = ", ".join(lead.get("_sub_trades", []))
        best_time = lead.get("_best_contact_time", "")

        extra = ""
        if urgency == "HIGH":
            extra += "\n⚠️ URGENCIA ALTA"
        if scope == "EMERGENCY":
            extra += "\n🚨 EMERGENCIA"
        if pain:
            extra += "\n💡 " + pain[:80]
        if sub_trades:
            extra += "\n🔗 Sub-trades: " + sub_trades
        if upsell:
            extra += "\n💰 Upsell: " + upsell[:60]
        if best_time:
            extra += "\n🕑 Best time: " + best_time

        msg = (
            "🧱 *FLOOR/CONCRETE* — " + scoring["grade_emoji"] + " " + scoring["grade"] + "\n"
            "📍 " + str(lead.get("address", "")) + "\n"
            "🏙️ " + str(lead.get("city", "")) + "\n"
            "📋 " + str(lead.get("description", "") or "")[:120] + "\n"
            "🏷️ " + str(lead.get("permit_type", "")) + "\n"
            "💰 " + value_str + "\n"
            "🏗️ " + contractor + "\n"
            "📞 " + phone + "\n"
            "🎯 Score: " + str(scoring["score"]) + "/100"
            + extra
        )
        send_message(msg)


        scoring = score_lead(lead)
        lead["_scoring"] = scoring

        value_str = f"${lead[value_float]:,.0f}" if lead.get("value_float") else "N/A"
        svc_label = "CONCRETE" if lead.get("service_type") == "concrete" else "FLOORING"
        svc_emoji = "🧱" if lead.get("service_type") == "concrete" else "🪵"

        msg = (
            f"{svc_emoji} *{svc_label}* — {scoring[grade_emoji]} {scoring[grade]}\n"
            f"📍 {lead.get(address, )}\n"
            f"🏙️ {lead.get(city, )}\n"
            f"📋 {lead.get(description, )[:120]}\n"
            f"🏷️ Tipo: {lead.get(permit_type, )}\n"
            f"💰 Valor: {value_str}\n"
            f"🏗️ GC: {lead.get(contractor, )}\n"
            f"📞 {lead.get(contact_phone, )}\n"
            f"🎯 Score: {scoring[score]}/100"
        )
        send_message(msg)
