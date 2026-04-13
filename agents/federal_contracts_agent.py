"""
agents/federal_contracts_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏛️ Contratos Federales de Construcción — Bay Area (USASpending.gov)

Detecta contratos federales de construcción otorgados en el Bay Area.
Contratos federales → necesitan subcontratistas locales (roofing, electrical,
drywall, painting, landscaping).

API: USASpending.gov (https://api.usaspending.gov)
  - Completamente GRATUITA, sin API key
  - Datos en tiempo real del Tesoro de EE.UU.
  - Incluye: NAICS codes, montos, localización, receptor
  - Docs: https://api.usaspending.gov/docs/

Lógica:
  - Busca contratos de construcción (NAICS 236xxx, 237xxx, 238xxx)
  - Filtra por Bay Area (condados: San Francisco, Alameda, Santa Clara,
    Contra Costa, San Mateo, Marin, Sonoma, Napa, Solano)
  - Monto mínimo: $100K (configurable con FEDERAL_MIN_AWARD_USD)
  - Ventana: últimos 90 días (configurable con FEDERAL_MONTHS)
"""

import os
import re
import logging
import requests
from datetime import datetime, timedelta
from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.lead_scoring import score_lead, format_score_line

logger = logging.getLogger(__name__)

FEDERAL_MIN_AWARD_USD = float(os.getenv("FEDERAL_MIN_AWARD_USD", "100000"))
FEDERAL_MONTHS        = int(os.getenv("FEDERAL_MONTHS", "3"))

# NAICS codes de construcción relevantes
_CONSTRUCTION_NAICS = [
    # Construcción residencial y comercial
    "236115", "236116", "236117", "236118", "236210", "236220",
    # Construcción de ingeniería civil
    "237110", "237120", "237130", "237210", "237310", "237990",
    # Contratistas especializados (los que MÁS interesan como clientes)
    "238110",  # Poured concrete
    "238120",  # Structural steel & precast concrete
    "238130",  # Framing contractors
    "238140",  # Masonry contractors
    "238150",  # Glass & glazing
    "238160",  # Roofing contractors ← clave
    "238170",  # Siding contractors
    "238190",  # Other foundation/exterior
    "238210",  # Electrical contractors ← clave
    "238220",  # HVAC contractors
    "238290",  # Other mechanical
    "238310",  # Drywall & insulation ← clave
    "238320",  # Painting & wall covering ← clave
    "238330",  # Flooring
    "238340",  # Tile & terrazzo
    "238350",  # Finish carpentry
    "238390",  # Other finish
    "238910",  # Site preparation
    "238990",  # Other specialty trade
    # Servicios relacionados
    "541330",  # Engineering services (design-build)
    "562910",  # Remediation services (asbestos, hazmat)
]

# Condados del Bay Area (FIPS codes de California)
_BAY_AREA_COUNTIES = [
    "San Francisco County",
    "Alameda County",
    "Santa Clara County",
    "Contra Costa County",
    "San Mateo County",
    "Marin County",
    "Sonoma County",
    "Napa County",
    "Solano County",
]

_USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"


def _cutoff_date() -> str:
    return (datetime.utcnow() - timedelta(days=30 * FEDERAL_MONTHS)).strftime("%Y-%m-%d")


def _today() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _parse_value(v) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(v) or "") or "0")
    except Exception:
        return 0.0


# Mapeo de NAICS a trade relevante para subcontratistas
_NAICS_TO_TRADE = {
    "238160": "ROOFING",
    "238210": "ELECTRICAL",
    "238310": "DRYWALL",
    "238320": "PAINTING",
    "238220": "HVAC",
    "238910": "LANDSCAPING",
    "562910": "DEMOLITION",
}


def _naics_to_trade(naics: str) -> str:
    if not naics:
        return "GENERAL"
    code = str(naics)[:6]
    if code in _NAICS_TO_TRADE:
        return _NAICS_TO_TRADE[code]
    if code.startswith("238"):
        return "GENERAL"  # specialty trade
    if code.startswith("236") or code.startswith("237"):
        return "GENERAL"
    return "GENERAL"


class FederalContractsAgent(BaseAgent):
    name      = "🏛️ Contratos Federales Construcción — Bay Area"
    emoji     = "🏛️"
    agent_key = "federal_contracts"

    def fetch_leads(self) -> list:
        leads = []
        try:
            raw = self._query_usaspending()
            for award in raw:
                lead = self._award_to_lead(award)
                if lead:
                    leads.append(lead)
        except Exception as e:
            logger.warning(f"[FederalContracts] Error consultando USASpending: {e}")

        logger.info(f"[FederalContracts] {len(leads)} contratos federales encontrados")
        return leads

    def _query_usaspending(self) -> list:
        payload = {
            "filters": {
                "time_period": [
                    {"start_date": _cutoff_date(), "end_date": _today()}
                ],
                "award_type_codes": ["A", "B", "C", "D"],  # contracts
                "place_of_performance_locations": [
                    {"country": "USA", "state": "CA"}
                ],
                "naics_codes": _CONSTRUCTION_NAICS,
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Award Amount",
                "Place of Performance City Name",
                "Place of Performance County Name",
                "Place of Performance State Code",
                "Period of Performance Start Date",
                "Period of Performance Current End Date",
                "Description",
                "NAICS Code",
                "NAICS Description",
                "Awarding Agency",
                "Awarding Sub Agency",
                "Contract Award Type",
            ],
            "sort": "Award Amount",
            "order": "desc",
            "limit": 100,
            "page": 1,
        }

        resp = requests.post(
            _USASPENDING_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])

        # Filtrar solo Bay Area counties
        bay_area_results = []
        for award in results:
            county = (award.get("Place of Performance County Name") or "").title()
            if not county:
                city = (award.get("Place of Performance City Name") or "").title()
                # Incluir si está en ciudades conocidas del Bay Area
                bay_cities = {
                    "San Francisco", "Oakland", "San Jose", "Fremont", "Berkeley",
                    "San Mateo", "Hayward", "Sunnyvale", "Santa Clara", "Concord",
                    "Richmond", "Antioch", "Daly City", "San Leandro", "Vallejo",
                    "Napa", "Petaluma", "Santa Rosa", "Fairfield",
                }
                if city not in bay_cities:
                    continue
            else:
                county_full = county if "County" in county else f"{county} County"
                if county_full not in _BAY_AREA_COUNTIES:
                    continue
            bay_area_results.append(award)

        return bay_area_results

    def _award_to_lead(self, award: dict) -> dict | None:
        amount = _parse_value(award.get("Award Amount", 0))
        if amount < FEDERAL_MIN_AWARD_USD:
            return None

        award_id   = award.get("Award ID", "")
        recipient  = award.get("Recipient Name", "N/A")
        city       = (award.get("Place of Performance City Name") or "").title()
        county     = (award.get("Place of Performance County Name") or "").title()
        naics      = str(award.get("NAICS Code", ""))
        naics_desc = award.get("NAICS Description", "")
        agency     = award.get("Awarding Agency", "")
        sub_agency = award.get("Awarding Sub Agency", "")
        desc       = award.get("Description", "")
        start_date = (award.get("Period of Performance Start Date") or "")[:10]
        end_date   = (award.get("Period of Performance Current End Date") or "")[:10]

        if not award_id or not recipient:
            return None

        trade = _naics_to_trade(naics)
        location = city or (county.replace(" County", "") if county else "Bay Area")

        score_data = score_lead(
            project_value=amount,
            source_type="federal_contracts",
            days_ago=0,
            service_type=trade,
        )

        return {
            "id":           f"fed_{award_id}",
            "city":         location,
            "county":       county,
            "address":      f"{location}, CA",
            "description":  desc or naics_desc or "Contrato federal de construcción",
            "contractor":   recipient,
            "value":        amount,
            "naics_code":   naics,
            "naics_desc":   naics_desc,
            "agency":       agency,
            "sub_agency":   sub_agency,
            "start_date":   start_date,
            "end_date":     end_date,
            "_scoring":     score_data,
            "_trade":       trade,
            "_agent_key":   "federal_contracts",
        }

    def notify(self, lead: dict):
        value      = lead.get("value", 0)
        city       = lead.get("city", "")
        recipient  = lead.get("contractor", "N/A")
        agency     = lead.get("agency", "")
        sub_agency = lead.get("sub_agency", "")
        naics_desc = lead.get("naics_desc", "")
        desc       = lead.get("description", "")
        start_date = lead.get("start_date", "")
        end_date   = lead.get("end_date", "")
        trade      = lead.get("_trade", "")
        score_line = format_score_line(lead.get("_scoring", {}))

        awarding_org = sub_agency or agency or "Agencia federal"
        value_str = f"${value:,.0f}"

        send_lead(
            agent_name=self.name,
            emoji=self.emoji,
            title=f"🏛️ Contrato Federal — {recipient[:40]} — {city}",
            fields={
                "📍 Ciudad":        city,
                "🏢 Receptor":      recipient[:60],
                "💰 Monto":         value_str,
                "🏗️ Tipo obra":     naics_desc[:60] if naics_desc else desc[:60],
                "🏛️ Agencia":       awarding_org[:60],
                "📅 Inicio":        start_date,
                "📅 Fin":           end_date,
                score_line:         "",
            },
            cta=(
                f"🔧 Contrato federal = GC necesita subs locales para {trade.lower()}. "
                f"Contacta a {recipient[:30]} para ofrecerte como subcontratista."
            ),
        )
