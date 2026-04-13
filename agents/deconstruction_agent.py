"""
agents/deconstruction_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔨 Deconstrucción & Demolición — Bay Area

Detecta permisos de demolición, abatimiento de asbesto, remoción de
materiales peligrosos y proyectos de deconstrucción.

¿Por qué es valioso para subcontratistas?
  1. Demolición → C-21 (Demolition) subs necesarios
  2. Post-demo → C-39 (Roofing) + C-33 (Painting) para acabados
  3. Abatimiento asbesto → C-21 con certificación hazmat
  4. Deconstrucción selectiva → múltiples trades involucrados

Fuentes gratuitas:
  1. SF Demolition Permits (Socrata)
  2. SF Asbestos Abatement (Socrata — BAAQMD notifications)
  3. San Jose demolition permits (CKAN)
  4. Oakland demolition (Socrata)
  5. Berkeley demolition (Socrata)
  6. EPA ECHO API — instalaciones con violaciones ambientales activas
     (asbesto, hazmat, residuos peligrosos) en Bay Area (GRATIS, sin key)

Fuentes de pago:
  7. ATTOM Property Pre-foreclosure — propiedades en pre-foreclosure
     frecuentemente se demolitan o renovan completamente
"""

import os
import re
import logging
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.contacts_loader import load_all_contacts, lookup_contact
from utils.lead_scoring import score_lead, format_score_line
from utils.notifications import notify_multichannel

logger = logging.getLogger(__name__)

SOURCE_TIMEOUT     = int(os.getenv("SOURCE_TIMEOUT", "45"))
DECON_MONTHS       = int(os.getenv("DECON_MONTHS", "3"))
PARALLEL_DECON     = int(os.getenv("PARALLEL_DECON", "6"))
MIN_DECON_VALUE    = float(os.getenv("MIN_DECON_VALUE", "50000"))
ATTOM_API_KEY      = os.getenv("ATTOM_API_KEY", "")


def _cutoff_iso() -> str:
    return (datetime.utcnow() - timedelta(days=30 * DECON_MONTHS)).strftime("%Y-%m-%dT00:00:00")

def _cutoff_ymd() -> str:
    return (datetime.utcnow() - timedelta(days=30 * DECON_MONTHS)).strftime("%Y-%m-%d")

def _parse_value(v) -> float:
    try:
        return float(re.sub(r"[^\d.]", "", str(v) or "") or "0")
    except Exception:
        return 0.0


# ── Clasificación de tipo de deconstrucción ──────────────────────────

DECON_TYPES = {
    "demolition": {
        "keywords": ["DEMOLITION", "DEMOLISH", "RAZE", "TEAR DOWN", "WRECKING",
                      "DEMOLER", "DEMOLICION", "FULL DEMO", "COMPLETE DEMO",
                      "STRUCTURE REMOVAL"],
        "priority": 5,
        "emoji":    "🏚️",
        "opportunity": "Permiso de demolición activo — subcontratista C-21 necesario para demo estructural",
    },
    "asbestos": {
        "keywords": ["ASBESTOS", "ASBESTO", "AMIANTO", "ACM REMOVAL",
                      "HAZMAT", "HAZARDOUS MATERIAL", "LEAD PAINT REMOVAL",
                      "LEAD ABATEMENT", "ABATEMENT"],
        "priority": 5,
        "emoji":    "⚠️",
        "opportunity": "Remoción de hazmat/asbesto — requiere C-21 con certificación hazmat",
    },
    "selective_demo": {
        "keywords": ["SELECTIVE DEMO", "PARTIAL DEMO", "INTERIOR DEMO",
                      "STRIP OUT", "GUT RENOVATION", "GUT REHAB",
                      "INTERIOR REMOVAL", "SOFT DEMO", "SOFT STRIP"],
        "priority": 4,
        "emoji":    "🔧",
        "opportunity": "Demo selectiva/interior — oportunidad para C-21 + pintura (C-33) posterior",
    },
    "deconstruction": {
        "keywords": ["DECONSTRUCTION", "DECONSTRUCCION", "SALVAGE",
                      "MATERIAL RECOVERY", "GREEN DEMOLITION",
                      "SUSTAINABLE DEMO"],
        "priority": 4,
        "emoji":    "♻️",
        "opportunity": "Deconstrucción verde — C-21 para demo + C-33/C-39 para acabados posteriores",
    },
    "fire_damage": {
        "keywords": ["FIRE DAMAGE", "FIRE REPAIR", "FIRE RESTORATION",
                      "FIRE REBUILD", "BURNED", "FIRE LOSS",
                      "SMOKE DAMAGE"],
        "priority": 5,
        "emoji":    "🔥",
        "opportunity": "Daño por fuego — demolición (C-21) + re-roofing (C-39) + pintura (C-33) necesarios",
    },
    "structural_repair": {
        "keywords": ["STRUCTURAL REPAIR", "FOUNDATION REPAIR",
                      "SEISMIC RETROFIT", "SEISMIC UPGRADE",
                      "EARTHQUAKE REPAIR", "SOFT STORY",
                      "STRUCTURAL UPGRADE"],
        "priority": 3,
        "emoji":    "🏗️",
        "opportunity": "Reparación estructural — puede requerir demo parcial (C-21) + acabados (C-33/C-39)",
    },
}


def _classify_decon(text: str) -> dict | None:
    """Clasifica el tipo de deconstrucción."""
    upper = (text or "").upper()
    for decon_type, info in DECON_TYPES.items():
        if any(kw in upper for kw in info["keywords"]):
            return {
                "decon_type":   decon_type,
                "priority":     info["priority"],
                "decon_emoji":  info["emoji"],
                "opportunity":  info["opportunity"],
            }
    return None


# ── Fuentes de datos ─────────────────────────────────────────────────

DECON_SOURCES = [
    # ── SF Demolition Permits ────────────────────────────────────
    {
        "city":    "San Francisco",
        "engine":  "socrata",
        "url":     "https://data.sfgov.org/resource/i98e-djp9.json",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "$limit": 100,
            "$order": "filed_date DESC",
            "$where": (
                "filed_date >= '{cutoff_iso}' "
                "AND (UPPER(permit_type_definition) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%ABATEMENT%' "
                "OR UPPER(description) LIKE '%HAZMAT%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%' "
                "OR UPPER(description) LIKE '%SEISMIC RETROFIT%' "
                "OR UPPER(description) LIKE '%SOFT STORY%' "
                "OR UPPER(description) LIKE '%GUT RENOVATION%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "street_number",
            "address2":    "street_name",
            "desc":        "description",
            "permit_type": "permit_type_definition",
            "status":      "status",
            "date":        "filed_date",
            "contractor":  "contractor_company_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "estimated_cost",
        },
    },
    # ── SF BAAQMD Asbestos Notifications ─────────────────────────
    {
        "city":    "San Francisco",
        "engine":  "socrata",
        "url":     "https://data.sfgov.org/resource/nwq7-v4e4.json",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "$limit": 50,
            "$order": "notification_date DESC",
            "$where": "notification_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "notification_number",
            "address":     "site_address",
            "desc":        "work_description",
            "status":      "notification_status",
            "date":        "notification_date",
            "contractor":  "contractor_name",
            "owner":       "owner_operator",
        },
        "_is_asbestos": True,
        "_skip_if_no_data": True,
    },
    # ── Oakland Demolition ───────────────────────────────────────
    {
        "city":    "Oakland",
        "engine":  "socrata",
        "url":     "https://data.oaklandca.gov/resource/uymu-f5cz.json",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "$limit": 50,
            "$order": "application_date DESC",
            "$where": (
                "application_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "application_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license_number",
            "owner":       "owner_name",
            "value":       "job_value",
        },
        "_skip_if_no_data": True,
    },
    # ── San Jose Demolition — CKAN ───────────────────────────────
    {
        "city":    "San Jose",
        "engine":  "ckan",
        "url":     "https://data.sanjoseca.gov/api/3/action/datastore_search",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "resource_id": "761b7ae8-3be1-4ad6-923d-c7af6404a904",
            "limit":       200,
            "sort":        "ISSUEDATE desc",
        },
        "field_map": {
            "id":          "FOLDERNUMBER",
            "address":     "gx_location",
            "desc":        "WORKDESCRIPTION",
            "permit_type": "FOLDERNAME",
            "status":      "Status",
            "date":        "ISSUEDATE",
            "contractor":  "CONTRACTOR",
            "owner":       "OWNERNAME",
            "value":       "PERMITVALUATION",
        },
        "_filter_decon": True,
    },
    # ── Berkeley Demolition ──────────────────────────────────────
    {
        "city":    "Berkeley",
        "engine":  "socrata",
        "url":     "https://data.cityofberkeley.info/resource/k92i-t48y.json",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "$limit": 50,
            "$order": "issue_date DESC",
            "$where": (
                "issue_date >= '{cutoff_iso}' "
                "AND (UPPER(project_description) LIKE '%DEMOLITION%' "
                "OR UPPER(project_description) LIKE '%DEMOLISH%' "
                "OR UPPER(project_description) LIKE '%ASBESTOS%')"
            ),
        },
        "field_map": {
            "id":          "record_number",
            "address":     "address",
            "desc":        "project_description",
            "status":      "record_status",
            "date":        "issue_date",
            "contractor":  "contractor",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "job_value",
        },
        "_skip_if_no_data": True,
    },
    # ── Sunnyvale ────────────────────────────────────────────────
    {
        "city":    "Sunnyvale",
        "engine":  "socrata",
        "url":     "https://data.sunnyvale.ca.gov/resource/irbr-7ykz.json",
        "timeout": SOURCE_TIMEOUT,
        "params": {
            "$limit": 50,
            "$order": "issueddate DESC",
            "$where": (
                "issueddate >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issueddate",
            "contractor":  "contractor",
            "lic":         "license_number",
            "owner":       "owner",
            "value":       "valuation",
        },
        "_skip_if_no_data": True,
    },
    # ── Contra Costa County ─────────────────────────────────────
    {
        "city":    "Contra Costa County",
        "engine":  "socrata",
        "url":     "https://data.contracosta.gov/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── Alameda County ──────────────────────────────────────────
    {
        "city":    "Alameda County",
        "engine":  "socrata",
        "url":     "https://data.acgov.org/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── San Mateo County ────────────────────────────────────────
    {
        "city":    "San Mateo County",
        "engine":  "socrata",
        "url":     "https://data.smcgov.org/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── Solano County ───────────────────────────────────────────
    {
        "city":    "Solano County",
        "engine":  "socrata",
        "url":     "https://data.solanocounty.com/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── Marin County ────────────────────────────────────────────
    {
        "city":    "Marin County",
        "engine":  "socrata",
        "url":     "https://data.marincounty.org/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── Napa County ─────────────────────────────────────────────
    {
        "city":    "Napa County",
        "engine":  "socrata",
        "url":     "https://data.countyofnapa.org/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── Sonoma County ───────────────────────────────────────────
    {
        "city":    "Sonoma County",
        "engine":  "socrata",
        "url":     "https://data.sonomacounty.ca.gov/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },
    # ── San Joaquin County ──────────────────────────────────────
    {
        "city":    "San Joaquin County",
        "engine":  "socrata",
        "url":     "https://data.sjgov.org/resource/building-permits.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 50,
            "$order": "issued_date DESC",
            "$where": (
                "issued_date >= '{cutoff_iso}' "
                "AND (UPPER(description) LIKE '%DEMOLITION%' "
                "OR UPPER(description) LIKE '%DEMOLISH%' "
                "OR UPPER(description) LIKE '%ASBESTOS%' "
                "OR UPPER(description) LIKE '%FIRE DAMAGE%')"
            ),
        },
        "field_map": {
            "id":          "permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_name",
            "lic":         "contractor_license",
            "owner":       "owner",
            "value":       "valuation",
        },
    },

    # ══════════════════════════════════════════════════════════════
    #  NATIONAL — Demolición específica con endpoints confirmados
    # ══════════════════════════════════════════════════════════════

    # ── Chicago — Demolition Permits (dataset específico) ────────
    # Permisos SOLO de demolición: nueva construcción sigue = insulación nueva
    {
        "city":    "Chicago",
        "engine":  "socrata",
        "url":     "https://data.cityofchicago.org/resource/769j-m8ee.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "issue_date DESC",
            "$where": "issue_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "permit_",
            "address":     "street_number",
            "desc":        "work_description",
            "status":      "permit_status",
            "date":        "issue_date",
            "contractor":  "contractor_1_name",
            "lic":         "contractor_1_license",
            "owner":       "contact_1_name",
            "value":       "reported_cost",
        },
    },

    # ── Chicago — Demolition Permits (dataset alternativo) ───────
    {
        "city":    "Chicago (demolition-alt)",
        "engine":  "socrata",
        "url":     "https://data.cityofchicago.org/resource/e4xk-pud8.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "issue_date DESC",
            "$where": "issue_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "permit_",
            "address":     "street_number",
            "desc":        "work_description",
            "status":      "permit_status",
            "date":        "issue_date",
            "contractor":  "contractor_1_name",
            "lic":         "contractor_1_license",
            "owner":       "contact_1_name",
            "value":       "reported_cost",
        },
    },

    # ── Los Ángeles — Demolition Permits ─────────────────────────
    # Demo → nueva construcción → toda la insulación nueva
    {
        "city":    "Los Angeles",
        "engine":  "socrata",
        "url":     "https://data.lacity.org/resource/nbyx-6y8e.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "issue_date DESC",
            "$where": "issue_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "permit_nbr",
            "address":     "address",
            "desc":        "work_description",
            "status":      "status",
            "date":        "issue_date",
            "contractor":  "contractors_business_name",
            "lic":         "license_number",
            "owner":       "applicant_name",
            "value":       "valuation",
        },
    },

    # ── Seattle — Demo Permits Activos ───────────────────────────
    {
        "city":    "Seattle",
        "engine":  "socrata",
        "url":     "https://data.seattle.gov/resource/54j8-iz5t.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "application_date DESC",
            "$where": "application_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "application_permit_number",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "application_date",
            "contractor":  "applicant_name",
            "lic":         None,
            "owner":       "owner",
            "value":       "value",
        },
    },

    # ── Austin TX — Residential Demolitions ──────────────────────
    {
        "city":    "Austin TX",
        "engine":  "socrata",
        "url":     "https://data.austintexas.gov/resource/x6mf-sksh.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "issued_date DESC",
            "$where": "issued_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "permit_num",
            "address":     "address",
            "desc":        "description",
            "status":      "status",
            "date":        "issued_date",
            "contractor":  "contractor_company_name",
            "lic":         "contractor_license_number",
            "owner":       "legal_entity_name",
            "value":       "job_value",
        },
    },

    # ── NYC — HPD Housing Violations ─────────────────────────────
    # Violaciones de mantenimiento de vivienda ABIERTAS = propietario
    # obligado a contratar para reparar (roofing, plumbing, electrical)
    {
        "city":    "New York City (HPD Violations)",
        "engine":  "socrata",
        "url":     "https://data.cityofnewyork.us/resource/wvxf-8bms.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "novissueddate DESC",
            "$where": (
                "novissueddate >= '{cutoff_iso}' "
                "AND currentstatus = 'Open'"
            ),
        },
        "field_map": {
            "id":          "violationid",
            "address":     "housenumber",
            "desc":        "novdescription",
            "status":      "currentstatus",
            "date":        "novissueddate",
            "contractor":  None,
            "lic":         None,
            "owner":       "ownername",
            "value":       None,
        },
        "_is_violation": True,
    },

    # ── Chicago — Edificios Vacantes ─────────────────────────────
    # Edificios vacantes reportados = candidatos a demolición/renovación
    {
        "city":    "Chicago (Vacant Buildings)",
        "engine":  "socrata",
        "url":     "https://data.cityofchicago.org/resource/8v9j-7f9s.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "date_service_request_was_received DESC",
            "$where": "date_service_request_was_received >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "service_request_number",
            "address":     "street_address",
            "desc":        "type_of_service_request",
            "status":      "status",
            "date":        "date_service_request_was_received",
            "contractor":  None,
            "lic":         None,
            "owner":       None,
            "value":       None,
        },
        "_is_violation": True,
    },

    # ══════════════════════════════════════════════════════════════
    #  BATCH 2 — Violaciones, asbestos y demoliciones adicionales
    # ══════════════════════════════════════════════════════════════

    # ── NYC — Asbestos Notifications (más reciente) ───────────────
    # Remoción de asbesto = insulación vieja retirada = REEMPLAZO OBLIGATORIO
    # Mejor oportunidad directa para sub-contractors de insulación
    {
        "city":    "New York City (Asbestos)",
        "engine":  "socrata",
        "url":     "https://data.cityofnewyork.us/resource/qvad-kvk3.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_asbestos": True,
        "params": {
            "$limit": 100,
            "$order": "start_date DESC",
            "$where": "start_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "job_number",
            "address":     "house_number",
            "desc":        "description",
            "status":      "filing_status",
            "date":        "start_date",
            "contractor":  "filing_representative",
            "lic":         None,
            "owner":       "owner",
            "value":       None,
        },
    },

    # ── NYC — HPD Housing Maintenance Complaints ──────────────────
    # Quejas abiertas de mantenimiento = propietario debe contratar reparaciones
    {
        "city":    "New York City (HPD Complaints)",
        "engine":  "socrata",
        "url":     "https://data.cityofnewyork.us/resource/ygpa-z7cr.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "complaint_open_date DESC",
            "$where": (
                "complaint_open_date >= '{cutoff_iso}' "
                "AND status = 'Open'"
            ),
        },
        "field_map": {
            "id":          "complaint_id",
            "address":     "buildingaddress",
            "desc":        "complaint_category",
            "status":      "status",
            "date":        "complaint_open_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "buildingid",
            "value":       None,
        },
    },

    # ── NYC — Open HPD Violations ────────────────────────────────
    # Vista optimizada de violaciones activas solamente
    {
        "city":    "New York City (Open HPD)",
        "engine":  "socrata",
        "url":     "https://data.cityofnewyork.us/resource/csn4-vhvf.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "novissueddate DESC",
            "$where": "novissueddate >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "violationid",
            "address":     "housenumber",
            "desc":        "novdescription",
            "status":      "currentstatus",
            "date":        "novissueddate",
            "contractor":  None,
            "lic":         None,
            "owner":       "ownername",
            "value":       None,
        },
    },

    # ── NYC — Housing Litigations ────────────────────────────────
    # Litigaciones judiciales = reparaciones obligadas por tribunal
    # Alta urgencia: propietario DEBE contratar o paga multas
    {
        "city":    "New York City (Litigations)",
        "engine":  "socrata",
        "url":     "https://data.cityofnewyork.us/resource/59kj-x8nc.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "caseopendate DESC",
            "$where": (
                "caseopendate >= '{cutoff_iso}' "
                "AND casestatus = 'OPEN'"
            ),
        },
        "field_map": {
            "id":          "litigationid",
            "address":     "buildingaddress",
            "desc":        "openpaper",
            "status":      "casestatus",
            "date":        "caseopendate",
            "contractor":  None,
            "lic":         None,
            "owner":       "respondent",
            "value":       None,
        },
    },

    # ── SF — Building Inspections ─────────────────────────────────
    # Inspecciones = detectan violaciones → propietario debe contratar
    {
        "city":    "San Francisco (Inspections)",
        "engine":  "socrata",
        "url":     "https://data.sfgov.org/resource/vckc-dh2h.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "inspection_date DESC",
            "$where": (
                "inspection_date >= '{cutoff_iso}' "
                "AND result = 'Fail'"
            ),
        },
        "field_map": {
            "id":          "inspection_number",
            "address":     "address",
            "desc":        "violation_description",
            "status":      "result",
            "date":        "inspection_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── SF — DBI Complaints ───────────────────────────────────────
    # Quejas a DBI (Dept. of Building Inspection) = reparaciones pendientes
    {
        "city":    "San Francisco (DBI Complaints)",
        "engine":  "socrata",
        "url":     "https://data.sfgov.org/resource/gm2e-bten.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "complaint_date DESC",
            "$where": (
                "complaint_date >= '{cutoff_iso}' "
                "AND status = 'Open'"
            ),
        },
        "field_map": {
            "id":          "complaint_number",
            "address":     "address",
            "desc":        "complaint_description",
            "status":      "status",
            "date":        "complaint_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── SF — Notices of Violation (DBI) ──────────────────────────
    # NOV emitido = propietario tiene plazo legal para contratar reparación
    {
        "city":    "San Francisco (NOV)",
        "engine":  "socrata",
        "url":     "https://data.sfgov.org/resource/nbtm-fbw5.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "notice_date DESC",
            "$where": "notice_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "notice_number",
            "address":     "address",
            "desc":        "violation_description",
            "status":      "status",
            "date":        "notice_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── Seattle — Code Complaints & Violations ────────────────────
    # Quejas de código abierto = propietario debe remediar = contratar subs
    {
        "city":    "Seattle (Code Violations)",
        "engine":  "socrata",
        "url":     "https://data.seattle.gov/resource/8s4s-3hc9.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "complaint_date DESC",
            "$where": (
                "complaint_date >= '{cutoff_iso}' "
                "AND status = 'Open'"
            ),
        },
        "field_map": {
            "id":          "complaint_id",
            "address":     "address",
            "desc":        "complaint_type",
            "status":      "status",
            "date":        "complaint_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── Seattle — URM Buildings (Unreinforced Masonry) ────────────
    # Edificios de mampostería sin refuerzo = retrofit sísmico OBLIGATORIO
    # = contratos grandes de reconstrucción + insulación
    {
        "city":    "Seattle (URM Seismic)",
        "engine":  "socrata",
        "url":     "https://data.seattle.gov/resource/jgaf-27y2.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {"$limit": 100},
        "field_map": {
            "id":          "objectid",
            "address":     "address",
            "desc":        "occupancy",
            "status":      "urm_status",
            "date":        None,
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
        "_is_violation": True,
    },

    # ── Austin TX — Code Complaint Cases ─────────────────────────
    {
        "city":    "Austin TX (Code Complaints)",
        "engine":  "socrata",
        "url":     "https://data.austintexas.gov/resource/6wtj-zbtb.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {
            "$limit": 100,
            "$order": "open_date DESC",
            "$where": (
                "open_date >= '{cutoff_iso}' "
                "AND case_status = 'OPEN'"
            ),
        },
        "field_map": {
            "id":          "case_number",
            "address":     "address",
            "desc":        "case_type",
            "status":      "case_status",
            "date":        "open_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── Los Ángeles — Propiedades en Foreclosure 2025 ─────────────
    # Propiedades en foreclosure = nuevo propietario (banco/inversor)
    # que habitualmente renueva/demuele antes de revender
    {
        "city":    "Los Angeles (Foreclosure 2025)",
        "engine":  "socrata",
        "url":     "https://data.lacity.org/resource/2qnc-kq4g.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {"$limit": 100},
        "field_map": {
            "id":          "apn",
            "address":     "address",
            "desc":        "property_type",
            "status":      "status",
            "date":        "registration_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── Los Ángeles — Propiedades en Foreclosure 2024 ─────────────
    {
        "city":    "Los Angeles (Foreclosure 2024)",
        "engine":  "socrata",
        "url":     "https://data.lacity.org/resource/aegg-btkk.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {"$limit": 100},
        "field_map": {
            "id":          "apn",
            "address":     "address",
            "desc":        "property_type",
            "status":      "status",
            "date":        "registration_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "owner_name",
            "value":       None,
        },
    },

    # ── Los Ángeles — Demolition Permits (vista específica) ───────
    {
        "city":    "Los Angeles (Demo 2)",
        "engine":  "socrata",
        "url":     "https://data.lacity.org/resource/fsgi-y87k.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "params": {
            "$limit": 100,
            "$order": "issue_date DESC",
            "$where": "issue_date >= '{cutoff_iso}'",
        },
        "field_map": {
            "id":          "permit_nbr",
            "address":     "address",
            "desc":        "work_description",
            "status":      "status",
            "date":        "issue_date",
            "contractor":  "contractors_business_name",
            "lic":         "license_number",
            "owner":       "applicant_name",
            "value":       "valuation",
        },
    },

    # ── Chicago — Foreclosed Rental Properties ────────────────────
    # Propiedades de alquiler en foreclosure = banco busca GC para reparar/vender
    {
        "city":    "Chicago (Foreclosed Rental)",
        "engine":  "socrata",
        "url":     "https://data.cityofchicago.org/resource/yhcw-iu53.json",
        "timeout": SOURCE_TIMEOUT,
        "_skip_if_no_data": True,
        "_is_violation": True,
        "params": {"$limit": 100},
        "field_map": {
            "id":          "pin",
            "address":     "address",
            "desc":        "property_type",
            "status":      "registration_status",
            "date":        "registration_date",
            "contractor":  None,
            "lic":         None,
            "owner":       "contact_name",
            "value":       None,
        },
    },
]


# ── Fetchers ─────────────────────────────────────────────────────────

def _fetch_socrata(source: dict) -> list:
    cutoff_iso = _cutoff_iso()
    params = {
        k: v.replace("{cutoff_iso}", cutoff_iso) if isinstance(v, str) else v
        for k, v in source["params"].items()
    }
    token = os.getenv("SOCRATA_APP_TOKEN", "")
    headers = {"Accept": "application/json"}
    if token:
        headers["X-App-Token"] = token
    resp = requests.get(source["url"], params=params,
                        timeout=source.get("timeout", 30), headers=headers)
    if resp.status_code == 400:
        logger.warning(f"[Decon/{source['city']}] 400 Bad Request")
        return []
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _fetch_ckan(source: dict) -> list:
    resp = requests.get(
        source["url"], params=source["params"],
        timeout=source.get("timeout", 30),
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []

    records = data.get("result", {}).get("records", [])

    if source.get("_filter_decon"):
        cutoff = _cutoff_ymd()
        filtered = []
        for r in records:
            date_val = (r.get("ISSUEDATE") or "")[:10]
            if date_val and date_val < cutoff:
                continue
            text = f"{r.get('WORKDESCRIPTION', '')} {r.get('FOLDERNAME', '')}"
            if _classify_decon(text):
                filtered.append(r)
        return filtered

    return records


def _fetch_source(source: dict) -> tuple[str, list]:
    engine = source.get("engine", "socrata")
    if engine == "ckan":
        records = _fetch_ckan(source)
    else:
        records = _fetch_socrata(source)
    return source["city"], records


# ── EPA ECHO — Instalaciones con violaciones ambientales (gratis) ────

# Ciudades principales del Bay Area para EPA ECHO
_EPA_ECHO_CITIES = [
    "San Francisco", "Oakland", "San Jose", "Berkeley",
    "Fremont", "Hayward", "Concord", "Richmond",
    "Sunnyvale", "Santa Clara", "San Mateo", "Vallejo",
]

_EPA_ECHO_BASE = "https://echodata.epa.gov/echo/air_rest_services"


def _fetch_epa_echo_hazmat() -> list:
    """
    EPA ECHO API — instalaciones con violaciones del Clean Air Act activas.

    Detecta sitios con incumplimiento de asbesto NESHAP (Clean Air Act) en
    el Bay Area. Estas instalaciones necesitan abatimiento certificado → C-21.

    API: https://echodata.epa.gov/echo/ — completamente gratuita, sin API key.
    Proceso: 1) get_facilities (obtiene QueryID) → 2) get_qid (páginas de resultados)
    """
    leads = []
    seen_ids: set = set()

    for city in _EPA_ECHO_CITIES:
        try:
            # Paso 1: Obtener QueryID
            r1 = requests.get(
                f"{_EPA_ECHO_BASE}.get_facilities",
                params={
                    "output":      "JSON",
                    "p_st":        "CA",
                    "p_city":      city,
                    "p_act":       "Y",
                    "p_vio":       "Y",
                    "responseset": "50",
                },
                timeout=20,
                headers={"Accept": "application/json"},
            )
            if r1.status_code != 200:
                continue

            results_meta = r1.json().get("Results", {})
            qid       = results_meta.get("QueryID", "")
            total_rows = int(results_meta.get("QueryRows", 0) or 0)

            if not qid or total_rows == 0:
                continue

            # Paso 2: Obtener primera página de resultados
            r2 = requests.get(
                f"{_EPA_ECHO_BASE}.get_qid",
                params={"output": "JSON", "qid": qid, "pageno": 1},
                timeout=20,
                headers={"Accept": "application/json"},
            )
            if r2.status_code != 200:
                continue

            facilities = r2.json().get("Results", {}).get("Facilities", []) or []

            for fac in facilities:
                fac_id    = (fac.get("SourceID") or fac.get("RegistryID") or "").strip()
                name      = (fac.get("AIRName") or "").strip()
                address   = (fac.get("AIRStreet") or "").strip()
                city_fac  = (fac.get("AIRCity") or city).strip().title()
                zip_code  = (fac.get("AIRZip") or "").strip()

                # Violaciones: quarters with violation + recent violation count
                qtrs_viol  = int(fac.get("AIRQtrsWithViol", 0) or 0)
                recent_cnt = int(fac.get("AIRRecentViolCnt", 0) or 0)
                last_viol  = (fac.get("AIRLastViolDate") or "")[:10]
                hpv_status = fac.get("AIRHpvStatus", "") or ""
                compl_stat = fac.get("AIRComplStatus", "") or ""
                is_hpv     = "High Priority" in hpv_status

                if not fac_id or fac_id in seen_ids:
                    continue
                if not name or not address:
                    continue
                # Filter: must have actual violations
                if qtrs_viol == 0 and recent_cnt == 0 and not is_hpv:
                    continue

                # Post-filter: solo Bay Area
                _bay_cities_lower = {
                    "san francisco", "oakland", "san jose", "berkeley",
                    "fremont", "hayward", "concord", "richmond", "sunnyvale",
                    "santa clara", "san mateo", "vallejo", "antioch", "daly city",
                    "san leandro", "livermore", "napa", "petaluma", "santa rosa",
                    "fairfield", "pittsburg", "vacaville", "alameda",
                    "walnut creek", "el cerrito", "pleasant hill", "emeryville",
                }
                if city_fac.lower() not in _bay_cities_lower:
                    continue

                seen_ids.add(fac_id)
                full_address = f"{address}, {city_fac}, CA {zip_code}".strip(", ")
                num_viols = max(qtrs_viol, recent_cnt)

                leads.append({
                    "id":           f"epa_echo_{fac_id}",
                    "city":         city_fac,
                    "address":      full_address,
                    "description":  (
                        f"EPA CAA: {'⚡HPV — ' if is_hpv else ''}"
                        f"{num_viols} violación(es) — {name}"
                    ),
                    "contractor":   name,
                    "status":       hpv_status or compl_stat or "EPA Violation",
                    "date":         last_viol,
                    "source":       "EPA ECHO",
                    "recent_violations": num_viols,
                    "is_hpv":       is_hpv,
                    "decon_type":    "asbestos",
                    "decon_priority": 5 if is_hpv else 4,
                    "decon_emoji":   "⚠️",
                    "opportunity":   f"Violación EPA activa ({name}) — abatimiento hazmat/asbesto necesario",
                    "_agent_key":    "deconstruction",
                })

            if facilities:
                logger.info(f"[EPA ECHO/{city}] {total_rows} total, "
                            f"{len(facilities)} en página 1, "
                            f"{sum(1 for l in leads if l.get('city','').lower()==city.lower())} con violaciones")

        except Exception as e:
            logger.debug(f"[EPA ECHO/{city}] {e}")

    return leads


# ── ATTOM Pre-Foreclosure (pago) ─────────────────────────────────────

def _fetch_preforeclosure(city: str, state: str = "CA") -> list:
    """
    ATTOM Pre-Foreclosure API — propiedades en proceso de ejecución.
    Pre-foreclosure → frecuentemente se demolitan o renovan completamente
    antes de la reventa = oportunidad de insulación en la reconstrucción.
    """
    if not ATTOM_API_KEY:
        return []
    try:
        resp = requests.get(
            "https://api.gateway.attomdata.com/propertyapi/v1.0.0/property/preforeclosure",
            headers={
                "Accept": "application/json",
                "apikey": ATTOM_API_KEY,
            },
            params={
                "address2": f"{city}, {state}",
                "orderby": "filingDate desc",
                "pagesize": 25,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        return data.get("property", []) if isinstance(data, dict) else []
    except Exception as e:
        logger.debug(f"[ATTOM PreForeclosure/{city}] {e}")
        return []


class DeconstuctionAgent(BaseAgent):
    name      = "🔨 Deconstrucción & Demolición — Bay Area"
    emoji     = "🔨"
    agent_key = "deconstruction"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._contacts = load_all_contacts()

    def fetch_leads(self) -> list:
        leads = []

        # ⚡ Fetch paralelo de todas las fuentes
        with ThreadPoolExecutor(max_workers=PARALLEL_DECON) as executor:
            futures = {
                executor.submit(_fetch_source, src): src
                for src in DECON_SOURCES
            }
            for fut in as_completed(futures):
                src = futures[fut]
                try:
                    city, records = fut.result()
                    fm = src["field_map"]
                    get = lambda r, k, _fm=fm: r.get(_fm.get(k) or "", "") or ""

                    for raw in records:
                        desc = get(raw, "desc")
                        permit_type = get(raw, "permit_type")
                        full_text = f"{desc} {permit_type}"

                        # Clasificar tipo de deconstrucción
                        decon_info = _classify_decon(full_text)

                        # Para fuentes de asbesto, asignar directamente
                        if not decon_info and src.get("_is_asbestos"):
                            decon_info = {
                                "decon_type":  "asbestos",
                                "priority":    5,
                                "decon_emoji": "⚠️",
                                "opportunity": "Remoción de asbesto → reemplazo de insulación obligatorio",
                            }

                        # Para violaciones de código (reparaciones obligatorias)
                        if not decon_info and src.get("_is_violation"):
                            decon_info = {
                                "decon_type":  "code_violation",
                                "priority":    4,
                                "decon_emoji": "🚨",
                                "opportunity": "Violación de código abierta → propietario obligado a contratar reparaciones",
                            }

                        if not decon_info:
                            continue

                        addr = get(raw, "address")
                        if fm.get("address2") and raw.get(fm.get("address2", "") or ""):
                            addr = f"{addr} {raw[fm['address2']]}".strip()

                        value = _parse_value(get(raw, "value"))

                        # Filtrar por valor mínimo (violaciones no tienen valor — se eximen)
                        if not src.get("_is_violation") and value < MIN_DECON_VALUE:
                            continue

                        lead = {
                            "id":            f"{city}_decon_{get(raw, 'id')}",
                            "city":          city,
                            "address":       addr,
                            "description":   desc[:200],
                            "permit_type":   permit_type,
                            "status":        get(raw, "status"),
                            "date":          get(raw, "date")[:10] if get(raw, "date") else "",
                            "contractor":    get(raw, "contractor"),
                            "lic_number":    get(raw, "lic"),
                            "owner":         get(raw, "owner"),
                            "value":         str(value) if value else "",
                            "value_float":   value,
                            "decon_type":    decon_info["decon_type"],
                            "decon_priority": decon_info["priority"],
                            "decon_emoji":   decon_info["decon_emoji"],
                            "opportunity":   decon_info["opportunity"],
                            "_agent_key":    "deconstruction",
                        }

                        # Enriquecer contacto
                        contractor = lead["contractor"]
                        if contractor:
                            match = lookup_contact(contractor, self._contacts)
                            if match:
                                lead["contact_phone"]  = match.get("phone", "")
                                lead["contact_email"]  = match.get("email", "")
                                lead["contact_source"] = f"CSV ({match['source']})"

                        # Lead scoring con boost por tipo
                        scoring = score_lead(lead)
                        decon_boost = decon_info["priority"] * 4
                        scoring["score"] = min(scoring["score"] + decon_boost, 100)

                        type_labels = {
                            "demolition": "Demolición total",
                            "asbestos": "Abatimiento asbesto",
                            "fire_damage": "Daño por fuego",
                            "selective_demo": "Demo selectiva",
                            "deconstruction": "Deconstrucción",
                            "structural_repair": "Reparación estructural",
                        }
                        scoring["reasons"].insert(
                            0, f"{decon_info['decon_emoji']} {type_labels.get(decon_info['decon_type'], '')}"
                        )
                        lead["_scoring"] = scoring

                        leads.append(lead)

                    logger.info(f"[Decon/{city}] {len(records)} registros procesados")

                except Exception as e:
                    if not src.get("_skip_if_no_data"):
                        logger.error(f"[Decon/{src['city']}] Error: {e}")
                    else:
                        logger.debug(f"[Decon/{src['city']}] {e}")

        # ── ATTOM Pre-Foreclosure (pago, opcional) ──────────────
        if ATTOM_API_KEY:
            for city_name in ["San Francisco", "Oakland", "San Jose", "Berkeley",
                               "Richmond", "Fremont", "Hayward", "Concord",
                               "Walnut Creek", "Vallejo", "Daly City", "San Mateo",
                               "Livermore", "Pleasanton", "San Rafael", "Napa",
                               "Fairfield", "Antioch", "Pittsburg", "Stockton", "Tracy"]:
                try:
                    properties = _fetch_preforeclosure(city_name)
                    for prop in properties:
                        address_info = prop.get("address", {})
                        addr = f"{address_info.get('line1', '')} {address_info.get('line2', '')}".strip()
                        if not addr:
                            continue

                        building = prop.get("building", {}).get("summary", {})
                        year_built = building.get("yearbuilt", 0)
                        age = (datetime.now().year - int(year_built)) if year_built and int(year_built) > 1900 else 0

                        lead = {
                            "id":            f"preforec_{city_name}_{prop.get('identifier', {}).get('apn', '')}",
                            "city":          city_name,
                            "address":       addr,
                            "description":   "Pre-foreclosure — posible demolición/renovación completa",
                            "status":        "Pre-foreclosure",
                            "date":          (prop.get("vintage", {}).get("lastModified") or "")[:10],
                            "owner":         f"{prop.get('owner', {}).get('owner1', {}).get('firstnameandmi', '')} {prop.get('owner', {}).get('owner1', {}).get('lastnameorsurname', '')}".strip(),
                            "value_float":   float(prop.get("assessment", {}).get("assessed", {}).get("assdttlvalue", 0) or 0),
                            "year_built":    int(year_built) if year_built else None,
                            "property_age":  age if age > 0 else None,
                            "decon_type":    "demolition",
                            "decon_priority": 3,
                            "decon_emoji":   "📋",
                            "opportunity":   "Pre-foreclosure → renovación/demolición probable → insulación nueva",
                            "source":        "ATTOM Pre-Foreclosure",
                            "_agent_key":    "deconstruction",
                        }

                        scoring = score_lead(lead)
                        if age > 40:
                            scoring["score"] = min(scoring["score"] + 15, 100)
                            scoring["reasons"].append(f"Propiedad antigua ({age} años)")
                        lead["_scoring"] = scoring
                        leads.append(lead)

                    if properties:
                        logger.info(f"[ATTOM PreForeclosure/{city_name}] {len(properties)} propiedades")
                except Exception as e:
                    logger.debug(f"[ATTOM PreForeclosure/{city_name}] {e}")

        # ── EPA ECHO — Hazmat/Asbestos violations (gratis) ──────────
        try:
            epa_leads = _fetch_epa_echo_hazmat()
            for lead in epa_leads:
                scoring = score_lead(lead)
                scoring["score"] = min(scoring["score"] + 16, 100)
                scoring["reasons"].insert(0, "⚠️ Violación EPA activa")
                lead["_scoring"] = scoring
            leads.extend(epa_leads)
            if epa_leads:
                logger.info(f"[EPA ECHO] {len(epa_leads)} instalaciones con violaciones hazmat")
        except Exception as e:
            logger.debug(f"[EPA ECHO] Error: {e}")

        # Ordenar por prioridad y score
        leads.sort(key=lambda l: (
            -l.get("decon_priority", 0),
            -l.get("_scoring", {}).get("score", 0),
        ))

        return leads

    def notify(self, lead: dict):
        scoring = lead.get("_scoring", {})
        score_line = format_score_line(scoring) if scoring else ""

        decon_emoji = lead.get("decon_emoji", "🔨")
        decon_type = lead.get("decon_type", "")
        opportunity = lead.get("opportunity", "")
        value = lead.get("value_float", 0)

        type_display = {
            "demolition":        "🏚️ DEMOLICIÓN TOTAL",
            "asbestos":          "⚠️ ABATIMIENTO ASBESTO",
            "selective_demo":    "🔧 DEMO SELECTIVA",
            "deconstruction":    "♻️ DECONSTRUCCIÓN",
            "fire_damage":       "🔥 DAÑO POR FUEGO",
            "structural_repair": "🏗️ REPARACIÓN ESTRUCTURAL",
        }.get(decon_type, decon_type.upper())

        fields = {
            "📍 Ciudad":            lead.get("city"),
            f"{decon_emoji} Tipo":  type_display,
            "📝 Descripción":      (lead.get("description") or "")[:200],
            "📊 Estado":           lead.get("status"),
            "📅 Fecha":            lead.get("date"),
        }

        if value:
            fields["💰 Valor"] = f"${value:,.0f}"

        if lead.get("contractor"):
            fields["👷 Contratista"] = lead["contractor"]
        if lead.get("lic_number"):
            fields["🪪 Licencia"] = lead["lic_number"]
        if lead.get("owner"):
            fields["👤 Propietario"] = lead["owner"]

        if lead.get("year_built"):
            fields["🏗️ Antigüedad"] = f"{lead.get('property_age', '?')} años (construida {lead['year_built']})"

        if lead.get("contact_phone"):
            src = lead.get("contact_source", "")
            fields["📞 Teléfono"] = (
                f"{lead['contact_phone']}  _(via {src})_" if src
                else lead["contact_phone"]
            )
        if lead.get("contact_email"):
            fields["✉️  Email"] = lead["contact_email"]

        if lead.get("source"):
            fields["📡 Fuente"] = lead["source"]

        if score_line:
            fields["🎯 Lead Score"] = score_line

        send_lead(
            agent_name=self.name, emoji=self.emoji,
            title=f"{lead['city']} — {lead['address']}",
            fields=fields,
            cta=f"{decon_emoji} {opportunity}",
        )

        # Multi-canal para leads de alta prioridad
        if scoring.get("score", 0) >= 70:
            notify_multichannel(lead, scoring)
