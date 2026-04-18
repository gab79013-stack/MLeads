"""
agents/tdlr_agent.py
━━━━━━━━━━━━━━━━━━━━
Texas TDLR Licenses Agent  v2

Fuente de datos:
  Texas Department of Licensing and Regulation (TDLR)
  Dataset "TDLR All Licenses" — 7358-krk7
  https://data.texas.gov/dataset/TDLR-All-Licenses/7358-krk7

API:
  Socrata Open Data API (SoQL)
  Endpoint base: https://{domain}.gov/resource/{dataset_id}.json
  Librería: requests nativo

Campos reales del dataset (verificados vía /api/views/7358-krk7.json):
  license_type, license_number, license_subtype
  business_name, business_county
  business_address_line1, business_address_line2, business_city_state_zip
  business_telephone, owner_name, owner_telephone
  mailing_address_line1, mailing_address_line2
  mailing_address_city_state_zip, mailing_address_county
  license_expiration_date_mmddccyy, continuing_education_flag, business_mailing

NOTA: El dataset no expone un campo `license_status` ni `city` directamente.
  - Filtrado por ciudad → se usa `business_county` (Dallas→DALLAS, etc.)
  - "Activo" → licencias cuya fecha de expiración >= hoy (filtro client-side)

Filtro equivalente al OData del usuario:
  ?$filter=license_status eq 'Active' and city eq 'Dallas'
  →  $where=upper(business_county)='DALLAS'
     + filtro client-side por fecha de expiración

AI (Qwen-turbo via DashScope):
  Clasifica el tipo de trade a partir del license_type y enriquece el lead.

SQLite:
  Tabla `tdlr_licenses` → histórico + dedup independiente.

Config (env vars):
  TDLR_CITIES        — ciudades TX separadas por coma (default: Dallas,Houston,Austin,...)
  TDLR_LICENSE_TYPES — tipos a incluir (vacío = todos)
  TDLR_APP_TOKEN     — Socrata app token (opcional, mayor rate limit)
  TDLR_LIMIT         — registros por ciudad por ciclo (default: 200)
  AGENT_TDLR         — true/false
  INTERVAL_TDLR      — minutos entre ciclos (default: 360)
"""

import os
import re
import logging
import requests
from datetime import datetime

from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.lead_scoring import score_lead, format_score_line
from utils.tdlr_db import upsert_license, init_tdlr_db

logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

_SOCRATA_BASE = "https://data.texas.gov/resource/7358-krk7.json"
_DATASET_URL  = "https://data.texas.gov/dataset/TDLR-All-Licenses/7358-krk7"

TDLR_APP_TOKEN = os.getenv("TDLR_APP_TOKEN", "")
TDLR_LIMIT     = int(os.getenv("TDLR_LIMIT", "200"))
TDLR_TIMEOUT   = int(os.getenv("TDLR_TIMEOUT", "30"))

_DEFAULT_CITIES = "Dallas,Houston,Austin,San Antonio,Fort Worth,Arlington,El Paso,Corpus Christi"
TDLR_CITIES_RAW = os.getenv("TDLR_CITIES", _DEFAULT_CITIES)
TDLR_CITIES     = [c.strip() for c in TDLR_CITIES_RAW.split(",") if c.strip()]

TDLR_LICENSE_TYPES_RAW = os.getenv("TDLR_LICENSE_TYPES", "")
TDLR_LICENSE_TYPES     = [t.strip().upper() for t in TDLR_LICENSE_TYPES_RAW.split(",") if t.strip()]

# ── Mapeo ciudad → condado Texas ──────────────────────────────────────────────
# Socrata filtra por business_county, no por city

_CITY_TO_COUNTY: dict[str, list[str]] = {
    "Dallas":          ["DALLAS"],
    "Houston":         ["HARRIS"],
    "Austin":          ["TRAVIS"],
    "San Antonio":     ["BEXAR"],
    "Fort Worth":      ["TARRANT"],
    "Arlington":       ["TARRANT"],
    "El Paso":         ["EL PASO"],
    "Corpus Christi":  ["NUECES"],
    "Laredo":          ["WEBB"],
    "Lubbock":         ["LUBBOCK"],
    "Garland":         ["DALLAS"],
    "Irving":          ["DALLAS"],
    "Plano":           ["COLLIN"],
    "Amarillo":        ["POTTER", "RANDALL"],
    "Grand Prairie":   ["DALLAS", "TARRANT"],
    "McKinney":        ["COLLIN"],
    "Frisco":          ["COLLIN", "DENTON"],
    "Brownsville":     ["CAMERON"],
    "Killeen":         ["BELL"],
    "Denton":          ["DENTON"],
    "Waco":            ["MCLENNAN"],
    "Midland":         ["MIDLAND"],
    "Odessa":          ["ECTOR"],
}


def _counties_for_city(city: str) -> list[str]:
    """Retorna los condados asociados a una ciudad. Fallback: nombre de ciudad en mayúsculas."""
    return _CITY_TO_COUNTY.get(city, [city.upper()])


# ── Mapeo licencia → trade ────────────────────────────────────────────────────

_TRADE_KEYWORDS: list[tuple[str, str]] = [
    # Orden importa — más específico primero
    ("MASTER ELECTRICIAN",        "ELECTRICAL"),
    ("JOURNEYMAN ELECTRICIAN",    "ELECTRICAL"),
    ("RESIDENTIAL WIREMAN",       "ELECTRICAL"),
    ("ELECTRICIAN",               "ELECTRICAL"),
    ("ELECTRICAL",                "ELECTRICAL"),
    ("A/C TECHNICIAN",            "HVAC"),
    ("AIR CONDITIONING",          "HVAC"),
    ("HVAC",                      "HVAC"),
    ("REFRIGERATION",             "HVAC"),
    ("MECHANICAL",                "HVAC"),
    ("BOILER",                    "HVAC"),
    ("MASTER PLUMBER",            "PLUMBING"),
    ("JOURNEYMAN PLUMBER",        "PLUMBING"),
    ("PLUMBER",                   "PLUMBING"),
    ("PLUMBING",                  "PLUMBING"),
    ("MOLD ASSESSMENT",           "DEMOLITION"),
    ("MOLD REMEDIATION",          "DEMOLITION"),
    ("ASBESTOS",                  "DEMOLITION"),
    ("DEMOLITION",                "DEMOLITION"),
    ("IRRIGAT",                   "LANDSCAPING"),
    ("LANDSCAPE",                 "LANDSCAPING"),
    ("PEST CONTROL",              "GENERAL"),
    ("COSMETOLOG",                "GENERAL"),
    ("BARBER",                    "GENERAL"),
    ("TOWING",                    "GENERAL"),
    ("TOW TRUCK",                 "GENERAL"),
    ("PROPERTY TAX",              "GENERAL"),
    ("VEHICLE STORAGE",           "GENERAL"),
    ("ROOF",                      "ROOFING"),
    ("PAINT",                     "PAINTING"),
    ("CONCRETE",                  "CONCRETE"),
    ("MASON",                     "CONCRETE"),
    ("DRYWALL",                   "DRYWALL"),
    ("INSULATION",                "INSULATION"),
    ("ELEVATOR",                  "GENERAL"),
    ("SIGN",                      "GENERAL"),
    ("APPLIANCE",                 "GENERAL"),
]

# License types de bajo interés para MLeads (subcontratistas de construcción)
_LOW_INTEREST_TYPES = {
    "cosmetolog", "barber", "tow truck", "towing", "vehicle storage",
    "property tax", "auctioneers", "combative sports",
    "driver education", "massage therapy", "health spa",
}


def _guess_trade(license_type: str) -> str:
    """Clasifica el trade a partir del tipo de licencia TDLR."""
    if not license_type:
        return "GENERAL"
    upper = license_type.upper()
    for keyword, trade in _TRADE_KEYWORDS:
        if keyword in upper:
            return trade
    return "GENERAL"


def _is_low_interest(license_type: str) -> bool:
    """Verdadero si el tipo de licencia no es relevante para MLeads."""
    lower = license_type.lower()
    return any(kw in lower for kw in _LOW_INTEREST_TYPES)


# ── Parser de fecha MM/DD/CCYY ────────────────────────────────────────────────

def _parse_exp_date(raw: str) -> datetime | None:
    """
    Parsea la fecha de expiración del dataset TDLR.
    Formatos conocidos: "MM/DD/YYYY", "MMDDCCYY" (sin separadores)
    """
    if not raw:
        return None
    raw = raw.strip()
    # Formato con barras: 04/19/2026
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def _is_active(rec: dict) -> bool:
    """
    Considera la licencia como activa si:
      - No tiene fecha de expiración → asumimos activa
      - Fecha de expiración >= hoy
    """
    exp_raw = rec.get("license_expiration_date_mmddccyy", "")
    if not exp_raw:
        return True
    exp = _parse_exp_date(exp_raw)
    if exp is None:
        return True
    return exp >= datetime.utcnow()


# ── Parser de city_state_zip ──────────────────────────────────────────────────

def _extract_city(city_state_zip: str) -> str:
    """
    Extrae solo la ciudad de un string tipo 'DALLAS TX 75201' o 'DALLAS COUNTY TX 75201'.
    """
    if not city_state_zip:
        return ""
    # Remover sufijos de condado
    s = re.sub(r"\s+COUNTY\b", "", city_state_zip.upper()).strip()
    # Quitar estado+zip del final: "TX 75201" o "TX" solo
    s = re.sub(r"\s+[A-Z]{2}\s+\d{5}(-\d{4})?\s*$", "", s).strip()
    s = re.sub(r"\s+[A-Z]{2}\s*$", "", s).strip()
    return s.title()


# ── Fetcher SoQL ─────────────────────────────────────────────────────────────

def _build_headers() -> dict:
    h = {"Accept": "application/json"}
    if TDLR_APP_TOKEN:
        h["X-App-Token"] = TDLR_APP_TOKEN
    return h


def _fetch_county(county: str, city_label: str) -> list[dict]:
    """
    Consulta Socrata para un condado específico.

    SoQL equivalente al filtro OData del usuario:
      ?$filter=license_status eq 'Active' and city eq 'Dallas'
    →  $where=upper(business_county)='DALLAS'
         AND (upper(license_type) LIKE '%ELECTR%' OR ...)
       (filtro de status=Active se aplica client-side por fecha de expiración)

    Pre-filtramos tipos de licencia en el servidor para no descargar
    cosmetología, towing, barbería, etc. que no interesan a MLeads.
    """
    # Si el usuario configuró tipos específicos, los usamos directamente
    if TDLR_LICENSE_TYPES:
        type_filter = "(" + " OR ".join(
            f"upper(license_type) LIKE '%{t}%'" for t in TDLR_LICENSE_TYPES
        ) + ")"
    else:
        # Filtro de construcción predeterminado — solo trades relevantes
        construction_keywords = [
            "ELECTR", "PLUMB", "A/C", "AIR COND", "HVAC", "REFRIGER",
            "BOILER", "MOLD", "ASBESTOS", "IRRIGAT", "LANDSCAPE",
            "APPLIANCE INSTALL", "ELEVATOR", "SIGN ELECTRICIAN",
        ]
        type_filter = "(" + " OR ".join(
            f"upper(license_type) LIKE '%{kw}%'" for kw in construction_keywords
        ) + ")"

    where = f"upper(business_county)='{county.upper()}' AND {type_filter}"

    params: dict = {
        "$where": where,
        "$limit": TDLR_LIMIT,
        "$order": "license_expiration_date_mmddccyy DESC",
    }

    try:
        resp = requests.get(
            _SOCRATA_BASE,
            params=params,
            headers=_build_headers(),
            timeout=TDLR_TIMEOUT,
        )
        resp.raise_for_status()
        records = resp.json()
        logger.info(f"[tdlr] {city_label} ({county}): {len(records)} registros crudos")
        return records
    except requests.exceptions.HTTPError as e:
        logger.error(f"[tdlr] HTTP {resp.status_code} para {county}: {e} — {resp.text[:300]}")
    except Exception as e:
        logger.error(f"[tdlr] Error fetcheando {county}: {e}")
    return []


# ── Normalización de registro → lead ─────────────────────────────────────────

def _normalize(rec: dict, city_label: str) -> dict | None:
    """
    Convierte un registro TDLR en un lead normalizado para MLeads.
    Retorna None si falta el número de licencia o es de bajo interés.
    """
    lic_number = (rec.get("license_number") or "").strip()
    if not lic_number:
        return None

    lic_type = (rec.get("license_type") or "").strip()

    # Filtrar tipos de bajo interés para subcontratistas de construcción
    if _is_low_interest(lic_type):
        return None

    # Campos de nombre
    business_name = (rec.get("business_name") or "").strip()
    owner_name    = (rec.get("owner_name") or "").strip()
    display_name  = business_name or owner_name or "N/A"

    # Dirección — preferir business, caer en mailing
    addr1  = (rec.get("business_address_line1") or rec.get("mailing_address_line1") or "").strip()
    addr2  = (rec.get("business_address_line2") or rec.get("mailing_address_line2") or "").strip()
    csz    = (rec.get("business_city_state_zip") or rec.get("mailing_address_city_state_zip") or "").strip()
    county = (rec.get("business_county") or rec.get("mailing_address_county") or "").strip().title()

    address_parts = [p for p in [addr1, addr2] if p]
    address = ", ".join(address_parts)
    city_from_csz = _extract_city(csz)
    city = city_from_csz or city_label

    # Extraer ZIP del csz
    zip_match = re.search(r"\b(\d{5}(?:-\d{4})?)\b", csz)
    zip_code  = zip_match.group(1) if zip_match else ""

    # Teléfono — preferir business
    phone = (rec.get("business_telephone") or rec.get("owner_telephone") or "").strip()
    # Formatear como (XXX) XXX-XXXX si es 10 dígitos
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        phone = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"

    # Fechas
    exp_raw  = (rec.get("license_expiration_date_mmddccyy") or "").strip()
    exp_date = _parse_exp_date(exp_raw)
    exp_str  = exp_date.strftime("%Y-%m-%d") if exp_date else exp_raw

    lic_subtype = (rec.get("license_subtype") or "").strip()
    ce_flag     = (rec.get("continuing_education_flag") or "N").strip()

    # ID único
    lead_id = f"tdlr_{lic_number}"

    # Descripción para el clasificador Qwen
    description = " ".join(filter(None, [lic_type, lic_subtype, business_name, county]))

    trade = _guess_trade(lic_type)

    return {
        "id":              lead_id,
        "title":           display_name,
        "business_name":   business_name,
        "owner_name":      owner_name,
        "license_number":  lic_number,
        "license_type":    lic_type,
        "license_subtype": lic_subtype,
        "license_status":  "Active",    # filtrado por fecha de expiración
        "address":         address,
        "city":            city,
        "state":           "TX",
        "zip":             zip_code,
        "county":          county,
        "contact_phone":   phone,
        "expiration_date": exp_str,
        "ce_flag":         ce_flag,
        "description":     description,
        "source":          "tdlr",
        "_trade":          trade,
        "_raw":            rec,
    }


# ── Agente ────────────────────────────────────────────────────────────────────

class TDLRAgent(BaseAgent):
    """
    Agente TDLR — Licencias de contratistas activos en Texas.

    Cada ciclo:
      1. Fetchea por condado (mapeado desde la ciudad configurada)
      2. Filtra licencias activas (expiración >= hoy)
      3. Normaliza registros → leads (descarta tipos sin interés)
      4. Guarda en SQLite tdlr_licenses
      5. Pipeline BaseAgent: dedup cross-agent → Qwen classify → score → Telegram
    """

    name:      str = "TDLR Texas Licenses"
    emoji:     str = "🤠"
    agent_key: str = "tdlr"

    def __init__(self):
        init_tdlr_db()
        logger.info(
            f"[tdlr] Agente inicializado — "
            f"ciudades: {', '.join(TDLR_CITIES)}"
        )

    # ── fetch_leads ──────────────────────────────────────────────────

    def fetch_leads(self) -> list[dict]:
        all_leads: list[dict] = []
        seen_ids: set[str] = set()   # dedup dentro del mismo ciclo

        for city in TDLR_CITIES:
            counties = _counties_for_city(city)
            for county in counties:
                records = _fetch_county(county, city)
                active_count = 0
                for rec in records:
                    if not _is_active(rec):
                        continue
                    active_count += 1
                    lead = _normalize(rec, city)
                    if lead is None:
                        continue
                    lid = lead["id"]
                    if lid in seen_ids:
                        continue
                    seen_ids.add(lid)
                    # Persistir en SQLite TDLR
                    try:
                        upsert_license(lead)
                    except Exception as e:
                        logger.debug(f"[tdlr] upsert error {lid}: {e}")
                    all_leads.append(lead)
                logger.debug(
                    f"[tdlr] {city}/{county}: "
                    f"{active_count} activas, {len(all_leads)} leads acum."
                )

        logger.info(
            f"[tdlr] Total: {len(all_leads)} licencias activas "
            f"en {len(TDLR_CITIES)} ciudades"
        )
        return all_leads

    # ── notify ───────────────────────────────────────────────────────

    def notify(self, lead: dict):
        """Formatea y envía el lead a Telegram."""
        scoring    = score_lead(lead)
        lead["_scoring"] = scoring
        score_line = format_score_line(scoring)

        trade      = lead.get("_trade", "GENERAL")
        name       = lead.get("title", "N/A")
        lic_num    = lead.get("license_number", "")
        lic_type   = lead.get("license_type", "")
        lic_sub    = lead.get("license_subtype", "")
        city       = lead.get("city", "")
        county     = lead.get("county", "")
        address    = lead.get("address", "")
        phone      = lead.get("contact_phone", "")
        exp        = lead.get("expiration_date", "")
        ai_summary = lead.get("_ai_summary", "")

        trade_emoji = {
            "ELECTRICAL":  "⚡",
            "HVAC":        "❄️",
            "PLUMBING":    "🔧",
            "ROOFING":     "🏠",
            "PAINTING":    "🎨",
            "CONCRETE":    "🏗️",
            "DEMOLITION":  "💥",
            "LANDSCAPING": "🌿",
            "DRYWALL":     "🧱",
            "INSULATION":  "🪵",
            "GENERAL":     "🔨",
        }.get(trade, "🔨")

        lic_label = lic_type
        if lic_sub:
            lic_label += f" ({lic_sub})"

        lines = [
            f"{self.emoji} *TDLR — {trade_emoji} {trade}*",
            "",
            f"🏢 *{name}*",
            f"🪪 `{lic_num}` — {lic_label}",
        ]

        if address:
            loc = f"{address}, {city}, TX"
        elif city:
            loc = f"{city}, TX"
        else:
            loc = f"{county}, TX"
        lines.append(f"📍 {loc}")

        if phone:
            lines.append(f"📞 {phone}")
        if exp:
            lines.append(f"📅 Vence: {exp}")
        if ai_summary:
            lines.append(f"💡 _{ai_summary}_")

        lines += ["", score_line, f"[Ver dataset TDLR]({_DATASET_URL})"]

        send_lead("\n".join(lines))
