"""
agents/permits_agent.py  v8
━━━━━━━━━━━━━━━━━━━━━━━━━━

ROOT CAUSE ANALYSIS v8:

  ❌ SF 400 Bad Request — DOS causas en $select:
     1. 'street_sfx' NO existe → campo real es 'street_suffix'
     2. 'contact_1_*' NO existen en i98e-djp9 → son un dataset separado
     FIX: Eliminar $select completamente (evitar el problema de raíz)
           Dejar que la API devuelva todos los campos.
           Mapear 'street_suffix' correctamente en field_map.

  ❌ SJ tarda exactamente 45s (= SOURCE_TIMEOUT) → el endpoint
     ckan_search tarda en responder con 200 registros sin filtro de fecha.
     FIX: Usar el endpoint SQL (datastore_search_sql) con WHERE en ISSUEDATE
          para filtrar server-side y recibir solo registros del período.
          Timeout separado de 30s para SJ (más agresivo).

  ❌ Datos de contacto SF ausentes:
     contractor_company_name existe pero frecuentemente vacío.
     Los datos reales de contacto están en dataset separado (kvek-u79k).
     FIX: Para GCs sin datos, enriquecer via CSLB usando el owner name
          como último recurso si no hay contractor.
"""

import os
import re
import time
import logging
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.contacts_loader import load_all_contacts, lookup_contact

logger = logging.getLogger(__name__)

PARALLEL_CITIES  = int(os.getenv("PARALLEL_CITIES", "6"))
MIN_PERMIT_VALUE = float(os.getenv("MIN_PERMIT_VALUE", "50000"))
PERMIT_MONTHS    = int(os.getenv("PERMIT_MONTHS", "3"))
SOURCE_TIMEOUT   = int(os.getenv("SOURCE_TIMEOUT", "45"))


def _cutoff_iso() -> str:
    return (datetime.utcnow() - timedelta(days=30 * PERMIT_MONTHS)).strftime("%Y-%m-%dT00:00:00")

def _cutoff_ymd() -> str:
    return (datetime.utcnow() - timedelta(days=30 * PERMIT_MONTHS)).strftime("%Y-%m-%d")


def _parse_value(v) -> float:
    if not v:
        return 0.0
    try:
        return float(re.sub(r"[^\d.]", "", str(v)) or "0")
    except Exception:
        return 0.0


def _build_sources() -> list:
    cutoff_iso = _cutoff_iso()
    cutoff_ymd = _cutoff_ymd()

    return [

        # ── San Francisco ─────────────────────────────────────────
        # ✅ FIX: Sin $select — evita el 400 por campos inválidos.
        #    Solo usamos $where y $order que son campos válidos confirmados.
        #    Campos reales del dataset: street_suffix (no street_sfx),
        #    contractor_company_name, contractor_license (cuando existen).
        {
            "city": "San Francisco", "engine": "socrata",
            "url":  "https://data.sfgov.org/resource/i98e-djp9.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200,
                "$order": "issued_date DESC",
                "$where": (
                    f"status IN('issued','complete') "
                    f"AND issued_date >= '{cutoff_iso}' "
                    f"AND permit_type_definition IN("
                    f"'additions alterations or repairs',"
                    f"'new construction wood frame',"
                    f"'otc additions',"
                    f"'accessory dwelling units',"
                    f"'new construction - wood frame')"
                ),
                # ✅ SIN $select — la API devuelve todos los campos disponibles
            },
            "field_map": {
                "id":          "permit_number",
                "address":     "street_number",
                "address_sfx": "street_number_suffix",  # ej: "1/2"
                "address2":    "street_name",
                "address_type":"street_suffix",          # ✅ FIX: "street_suffix" no "street_sfx"
                "permit_type": "permit_type_definition",
                "description": "description",
                "status":      "status",
                "filed_date":  "filed_date",
                "issued_date": "issued_date",
                "contractor":  "contractor_company_name",
                "lic_number":  "contractor_license",     # campo real en el dataset
                "owner":       "owner",
                "value":       "estimated_cost",
                "url_tpl":     "https://sfdbi.org/permit/{permit_number}",
                # ✅ Eliminados contact_1_* que no existen en i98e-djp9
            },
        },

        # ── San Jose — SQL server-side con filtro de fecha ────────
        # ✅ FIX: Usar datastore_search_sql con WHERE en ISSUEDATE
        #    para filtrar en el servidor y evitar descargar 1500+ registros.
        #    Timeout reducido a 30s (más agresivo que el global).
        {
            "city": "San Jose", "engine": "ckan_sql",
            "url":  "https://data.sanjoseca.gov/api/3/action/datastore_search_sql",
            "timeout": 30,
            "params": {
                "sql": (
                    f'SELECT "FOLDERNUMBER","gx_location","FOLDERNAME","WORKDESCRIPTION",'
                    f'"Status","ISSUEDATE","CONTRACTOR","OWNERNAME","PERMITVALUATION" '
                    f'FROM "761b7ae8-3be1-4ad6-923d-c7af6404a904" '
                    f'WHERE "ISSUEDATE" >= \'{cutoff_ymd}\' '
                    f'ORDER BY "ISSUEDATE" DESC '
                    f'LIMIT 200'
                )
            },
            "field_map": {
                "id":          "FOLDERNUMBER",
                "address":     "gx_location",
                "address2":    None,
                "permit_type": "FOLDERNAME",
                "description": "WORKDESCRIPTION",
                "status":      "Status",
                "filed_date":  None,
                "issued_date": "ISSUEDATE",
                "contractor":  "CONTRACTOR",
                "lic_number":  None,
                "owner":       "OWNERNAME",
                "value":       "PERMITVALUATION",
                "url_tpl":     "https://www.sjpermits.org/",
            },
        },

        # ── Sunnyvale ─────────────────────────────────────────────
        {
            "city": "Sunnyvale", "engine": "socrata", "_skip_if_no_data": True,
            "url":  "https://data.sunnyvale.ca.gov/resource/7xm5-teup.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"permit_status='Issued' AND issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description",
                "status":"permit_status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license_number",
                "owner":"property_owner","value":"project_value",
                "url_tpl":"https://sunapps.sunnyvale.ca.gov/pds/",
            },
        },

        # ── Santa Clara ───────────────────────────────────────────
        {
            "city": "Santa Clara", "engine": "socrata", "_skip_if_no_data": True,
            "url":  "https://data.santa-clara.ca.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"status='Issued' AND issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor","lic_number":"license_number",
                "owner":"owner","value":"value",
                "url_tpl":"https://www.santaclaraca.gov/government/departments/community-development/building-division",
            },
        },

        # ── Richmond ──────────────────────────────────────────────
        {
            "city": "Richmond", "engine": "socrata", "_skip_if_no_data": True,
            "url":  "https://data.ci.richmond.ca.us/resource/permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_issued DESC",
                "$where": f"status='ISSUED' AND date_issued >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"site_address","address2":None,
                "permit_type":"permit_type","description":"work_description","status":"status",
                "filed_date":"application_date","issued_date":"date_issued",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://www.ci.richmond.ca.us/1357/Building-Permits",
            },
        },

        # ── Fremont ───────────────────────────────────────────────
        {
            "city": "Fremont", "engine": "socrata", "_skip_if_no_data": True,
            "url":  "https://www.fremont.gov/CivicAlerts.aspx",
            "timeout": SOURCE_TIMEOUT, "params": {},
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.fremont.gov/government/departments/building-services",
            },
        },

        # ── Hayward ───────────────────────────────────────────────
        {
            "city": "Hayward", "engine": "socrata", "_skip_if_no_data": True,
            "url":  "https://hayward.permitportal.us/api/permits",
            "timeout": SOURCE_TIMEOUT,
            "params": {"status":"Issued","type":"Building","limit":200},
            "field_map": {
                "id":"permit_number","address":"location","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"applied","issued_date":"issued",
                "contractor":"contractor","lic_number":"lic_no",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://hayward.permitportal.us/permit/{permit_number}",
            },
        },

        # ── Oakland (Accela, sin API pública) ─────────────────────
        {
            "city": "Oakland", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.oaklandca.gov/resource/p8h7-gzqg.json",
            "timeout": 10, "params": {"$limit": 1},
            "field_map": {
                "id":"permit_number","address":"site_address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"applied_date","issued_date":"issue_date",
                "contractor":"primary_contractor","lic_number":"contractor_lic_number",
                "owner":"owner_name","value":"valuation",
                "url_tpl":"https://aca-prod.accela.com/OAKLAND/",
            },
        },

        # ── Berkeley (requiere Socrata token en .env) ─────────────
        {
            "city": "Berkeley", "engine": "socrata",
            "_skip_if_no_data": True, "_requires_token": True,
            "url": "https://data.cityofberkeley.info/resource/cqze-unm8.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_issued DESC",
                "$where": f"permit_status IN('ISSUED','FINALED') AND date_issued >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"location_address","address2":None,
                "permit_type":"permit_type","description":"permit_description","status":"permit_status",
                "filed_date":"date_filed","issued_date":"date_issued",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"property_owner","value":"project_valuation",
                "url_tpl":"https://permits.cityofberkeley.info/eTRAKiT/",
            },
        },

        # ── Palo Alto ─────────────────────────────────────────────
        {
            "city": "Palo Alto", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofpaloalto.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"property_owner","value":"valuation",
                "url_tpl":"https://www.cityofpaloalto.org/Departments/Development-Services",
            },
        },

        # ── Mountain View ─────────────────────────────────────────
        {
            "city": "Mountain View", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.mountainview.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor","lic_number":"license_number",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.mountainview.gov/depts/comdev/building/",
            },
        },

        # ── Redwood City ──────────────────────────────────────────
        {
            "city": "Redwood City", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.redwoodcity.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"applied_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://www.redwoodcity.org/departments/community-development-department/building-division",
            },
        },

        # ── Daly City ─────────────────────────────────────────────
        {
            "city": "Daly City", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.dalycity.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor","lic_number":"license_number",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.dalycity.org/405/Building-Division",
            },
        },

        # ── San Mateo ─────────────────────────────────────────────
        {
            "city": "San Mateo", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofsanmateo.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.cityofsanmateo.org/3012/Building",
            },
        },

        # ── Concord ───────────────────────────────────────────────
        {
            "city": "Concord", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.concordca.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"valuation",
                "url_tpl":"https://www.cityofconcord.org/349/Building-Division",
            },
        },

        # ── Walnut Creek ──────────────────────────────────────────
        {
            "city": "Walnut Creek", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.walnut-creek.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor","lic_number":"license_number",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.walnut-creek.org/departments/community-development/building",
            },
        },

        # ══════════════════════════════════════════════════════════
        #  COUNTY-LEVEL PORTALS  (cover multiple cities each)
        # ══════════════════════════════════════════════════════════

        # ── Contra Costa County ───────────────────────────────────
        # Covers: Pleasant Hill, Martinez, Clayton, Pittsburg, Lafayette,
        #         Orinda, Antioch, Moraga, Alamo, Danville, Hercules,
        #         Pinole, Oakley, San Ramon, Brentwood, El Cerrito
        # (Walnut Creek, Concord, Richmond already have individual entries)
        {
            "city": "Contra Costa County (Pleasant Hill, Martinez, Clayton, Pittsburg, Lafayette, Orinda, Antioch, Moraga, Alamo, Danville, Hercules, Pinole, Oakley, San Ramon, Brentwood, El Cerrito)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.contracosta.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.contracosta.ca.gov/4839/Building-Inspection",
            },
        },

        # ── Alameda County ────────────────────────────────────────
        # Covers: Dublin, Alameda, San Leandro, Pleasanton, Livermore,
        #         Newark, Castro Valley, San Lorenzo, Emeryville, Albany, Union City
        # (Oakland, Berkeley, Fremont, Hayward already have individual entries)
        {
            "city": "Alameda County (Dublin, San Leandro, Pleasanton, Livermore, Newark, Castro Valley, San Lorenzo, Emeryville, Albany, Union City)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.acgov.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.acgov.org/building/",
            },
        },

        # ── Alameda (City) ────────────────────────────────────────
        {
            "city": "Alameda", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.alamedaca.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.alamedaca.gov/Departments/Building",
            },
        },

        # ── San Mateo County ──────────────────────────────────────
        # Covers: South San Francisco, San Bruno, Millbrae, Burlingame
        # (Daly City, San Mateo, Redwood City already have individual entries)
        {
            "city": "San Mateo County (South San Francisco, San Bruno, Millbrae, Burlingame)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.smcgov.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.smcgov.org/planning-building",
            },
        },

        # ── Solano County ─────────────────────────────────────────
        # Covers: Benicia, Fairfield, Suisun City, Rio Vista, Vacaville
        # (Vallejo has its own individual entry below)
        {
            "city": "Solano County (Benicia, Fairfield, Suisun City, Rio Vista, Vacaville)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.solanocounty.com/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.solanocounty.com/depts/resource_mgmt/building/",
            },
        },

        # ── Vallejo (City) ────────────────────────────────────────
        {
            "city": "Vallejo", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofvallejo.net/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.cityofvallejo.net/city_hall/departments___divisions/building_division",
            },
        },

        # ── Marin County ─────────────────────────────────────────
        # Covers: Novato, San Rafael
        {
            "city": "Marin County (Novato, San Rafael)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.marincounty.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.marincounty.org/depts/cd/divisions/building-and-safety",
            },
        },

        # ── Napa County ──────────────────────────────────────────
        # Covers: Napa
        {
            "city": "Napa County (Napa)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.countyofnapa.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.countyofnapa.org/191/Building",
            },
        },

        # ── Sonoma County ─────────────────────────────────────────
        # Covers: Sonoma, Petaluma
        {
            "city": "Sonoma County (Sonoma, Petaluma)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.sonomacounty.ca.gov/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://sonomacounty.ca.gov/development-services/permit-sonoma",
            },
        },

        # ── San Joaquin County ────────────────────────────────────
        # Covers: Tracy, Stockton
        {
            "city": "San Joaquin County (Tracy, Stockton)",
            "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.sjgov.org/resource/building-permits.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address","address2":None,
                "permit_type":"permit_type","description":"description","status":"status",
                "filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://www.sjgov.org/department/cd/building/",
            },
        },

        # ══════════════════════════════════════════════════════════
        #  NATIONAL EXPANSION — Cities with confirmed Socrata APIs
        # ══════════════════════════════════════════════════════════

        # ── NYC — DOB Permit Issuance ─────────────────────────────
        # Permisos activos del Dept of Buildings: roofing, electrical,
        # plumbing, new construction, additions — señales directas
        # de que un GC necesita sub-contratistas.
        {
            "city": "New York City", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofnewyork.us/resource/ipu4-2q9a.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issuance_date DESC",
                "$where": (
                    f"issuance_date >= '{cutoff_iso}' "
                    f"AND permit_status = 'ISSUED' "
                    f"AND permit_type IN('NB','A1','A2','EW','PL')"
                ),
            },
            "field_map": {
                "id":"job__","address":"house__","address2":"street_name",
                "permit_type":"permit_type","description":"job_description",
                "status":"permit_status","filed_date":"filing_date","issued_date":"issuance_date",
                "contractor":"permittee_s_business_name","lic_number":"permittee_s_license__",
                "owner":"owner_s_business_name","value":"estimated_job_costs",
                "url_tpl":"https://a810-bisweb.nyc.gov/bisweb/JobsQueryByNumberServlet?passjobnumber={job__}",
            },
        },

        # ── NYC — DOB NOW Approved Permits ────────────────────────
        # Permisos aprobados via el sistema moderno DOB NOW
        {
            "city": "New York City (DOB NOW)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofnewyork.us/resource/rbx6-tga4.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"job_filing_number","address":"house_no","address2":"street_name",
                "permit_type":"work_type","description":"job_description",
                "status":"filing_status","filed_date":"filing_date","issued_date":"issued_date",
                "contractor":"applicant_business_name","lic_number":"applicant_license_number",
                "owner":"owner_business_name","value":"job_value",
                "url_tpl":"https://a810-bisweb.nyc.gov/bisweb/",
            },
        },

        # ── Chicago — Building Permits ────────────────────────────
        # Permisos de construcción activos: new construction, renovation,
        # repairs — todos requieren sub-contratistas especializados
        {
            "city": "Chicago", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' "
                    f"AND permit_status = 'ISSUED'"
                ),
            },
            "field_map": {
                "id":"id","address":"street_number","address2":"street_name",
                "permit_type":"permit_type","description":"work_description",
                "status":"permit_status","filed_date":"application_start_date","issued_date":"issue_date",
                "contractor":"contractor_1_name","lic_number":"contractor_1_license",
                "owner":"contact_1_name","value":"reported_cost",
                "url_tpl":"https://webapps1.chicago.gov/permitview/",
            },
        },

        # ── Los Ángeles — Building Permits ────────────────────────
        # Dataset consolidado LADBS — nuevas construcciones y remodelaciones
        {
            "city": "Los Angeles", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.lacity.org/resource/nwpn-78w6.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_nbr","address":"address",
                "permit_type":"permit_type","description":"work_description",
                "status":"status","filed_date":"date_application_filed","issued_date":"issue_date",
                "contractor":"contractors_business_name","lic_number":"license_number",
                "owner":"applicant_name","value":"valuation",
                "url_tpl":"https://www.ladbsservices2.lacity.org/OnlineServices/",
            },
        },

        # ── Los Ángeles — Building Permits 2020-Present ───────────
        {
            "city": "Los Angeles (2020+)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.lacity.org/resource/pi9x-tg5x.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_nbr","address":"address",
                "permit_type":"permit_type","description":"work_description",
                "status":"status","filed_date":"date_application_filed","issued_date":"issue_date",
                "contractor":"contractors_business_name","lic_number":"license_number",
                "owner":"applicant_name","value":"valuation",
                "url_tpl":"https://www.ladbsservices2.lacity.org/OnlineServices/",
            },
        },

        # ── Dallas — Building Permits ─────────────────────────────
        {
            "city": "Dallas", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://www.dallasopendata.com/resource/7v99-6h2e.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"applied_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://permits.dallascityhall.com/",
            },
        },

        # ── Seattle — Land Use Permits ────────────────────────────
        # Permisos de uso de suelo: nuevos desarrollos residenciales y comerciales
        {
            "city": "Seattle", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.seattle.gov/resource/m6is-v55d.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "application_date DESC",
                "$where": f"application_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"application_permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"applicant_name","lic_number":None,
                "owner":"owner","value":"value",
                "url_tpl":"https://cosaccela.seattle.gov/portal/",
            },
        },

        # ── Seattle — Residential Building Permits ────────────────
        {
            "city": "Seattle (Residential)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.seattle.gov/resource/rs98-eyib.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"application_permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"applicant","lic_number":None,
                "owner":"owner","value":"value",
                "url_tpl":"https://cosaccela.seattle.gov/portal/",
            },
        },

        # ── Montgomery County MD — Residential Permits ────────────
        {
            "city": "Montgomery County MD (Residential)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.montgomerycountymd.gov/resource/76wv-m3v8.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "addeddate DESC",
                "$where": f"addeddate >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permitno","address":"stno","address2":"stname",
                "permit_type":"worktype","description":"description",
                "status":"status","filed_date":"addeddate","issued_date":"issueddate",
                "contractor":"contractorname","lic_number":"contractorlicenseno",
                "owner":"ownername","value":"jobcost",
                "url_tpl":"https://permitsmc.montgomerycountymd.gov/",
            },
        },

        # ── Montgomery County MD — Commercial Permits ─────────────
        {
            "city": "Montgomery County MD (Commercial)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.montgomerycountymd.gov/resource/c639-6y9s.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "addeddate DESC",
                "$where": f"addeddate >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permitno","address":"stno","address2":"stname",
                "permit_type":"worktype","description":"description",
                "status":"status","filed_date":"addeddate","issued_date":"issueddate",
                "contractor":"contractorname","lic_number":"contractorlicenseno",
                "owner":"ownername","value":"jobcost",
                "url_tpl":"https://permitsmc.montgomerycountymd.gov/",
            },
        },

        # ── New Orleans — Construcción y Renovación ───────────────
        {
            "city": "New Orleans", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.nola.gov/resource/797y-f09m.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://lama.city/new-orleans/",
            },
        },

        # ── Baton Rouge — Building Permits ────────────────────────
        {
            "city": "Baton Rouge", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.brla.gov/resource/9is9-6q79.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://brgov.com/dept/dpdPermitting/",
            },
        },

        # ── Kansas City MO — Building Permits ─────────────────────
        {
            "city": "Kansas City MO", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.kcmo.org/resource/7atp-9642.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://kcmo.gov/building-codes/",
            },
        },

        # ── Fort Worth TX — Building Permits ──────────────────────
        {
            "city": "Fort Worth TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.fortworthtexas.gov/resource/9q7f-h7t2.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://www.fortworthtexas.gov/departments/development-services",
            },
        },

        # ── Orlando FL — Building Permits ─────────────────────────
        {
            "city": "Orlando FL", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityoforlando.net/resource/5p3e-v738.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://www.orlando.gov/Our-Government/Departments-Offices/Building-and-Code-Services",
            },
        },

        # ── Hartford CT — Building Permits ────────────────────────
        {
            "city": "Hartford CT", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.hartford.gov/resource/7vdp-q832.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://www.hartford.gov/",
            },
        },

        # ── Louisville KY — Permits Emitidos ─────────────────────
        {
            "city": "Louisville KY", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.louisvilleky.gov/resource/sc77-q82v.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://louisvilleky.gov/government/codes-regulations",
            },
        },

        # ── Nashville TN — Building Permits ───────────────────────
        {
            "city": "Nashville TN", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.nashville.gov/resource/3wb6-xy3j.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_issued DESC",
                "$where": f"date_issued >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"date_applied","issued_date":"date_issued",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"const_value",
                "url_tpl":"https://www.nashville.gov/departments/codes",
            },
        },

        # ── Mesa AZ — Building Permits ────────────────────────────
        {
            "city": "Mesa AZ", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.mesaaz.gov/resource/is3x-7q4v.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"applied_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://www.mesaaz.gov/business/development-services/development-services-building",
            },
        },

        # ── Austin TX — Building Permits (últimos 30 días) ────────
        {
            "city": "Austin TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/enku-zhee.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
            },
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"calendar_year_issued","issued_date":"issued_date",
                "contractor":"contractor_company_name","lic_number":"contractor_license_number",
                "owner":"legal_entity_name","value":"job_value",
                "url_tpl":"https://abc.austintexas.gov/",
            },
        },

        # ── Austin TX — Permisos Históricos Residenciales ─────────
        {
            "city": "Austin TX (Residential)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/3syk-w9eu.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"calendar_year_issued","issued_date":"issued_date",
                "contractor":"contractor_company_name","lic_number":"contractor_license_number",
                "owner":"legal_entity_name","value":"job_value",
                "url_tpl":"https://abc.austintexas.gov/",
            },
        },

        # ── Boston MA — Building Permits Aprobados ────────────────
        {
            "city": "Boston MA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.boston.gov/resource/5263-8n4g.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permitnumber","address":"address",
                "permit_type":"permittypedescr","description":"comments",
                "status":"status","filed_date":"applicationdate","issued_date":"issued_date",
                "contractor":"applicant","lic_number":"applicantlicno",
                "owner":"ownername","value":"declared_valuation",
                "url_tpl":"https://www.boston.gov/departments/inspectional-services",
            },
        },

        # ── Somerville MA — Building Permits ──────────────────────
        {
            "city": "Somerville MA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.somervillema.gov/resource/uup8-h768.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"job_cost",
                "url_tpl":"https://www.somervillema.gov/departments/inspectional-services",
            },
        },

        # ── Cambridge MA — Building Permits ───────────────────────
        {
            "city": "Cambridge MA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cambridgema.gov/resource/6dei-idid.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"license_number",
                "owner":"owner_name","value":"total_fees",
                "url_tpl":"https://cambridgema.gov/inspection",
            },
        },

        # ── Edmonton Canada — Building Permits ────────────────────
        {
            "city": "Edmonton CA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.edmonton.ca/resource/24u8-4m26.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"location",
                "permit_type":"permit_type","description":"work_type_group",
                "status":"permit_status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"contractor_trade_name","lic_number":"licence_type",
                "owner":"property_use","value":"estimated_job_cost_amount",
                "url_tpl":"https://www.edmonton.ca/permits",
            },
        },

        # ── Calgary Canada — Building Permits ─────────────────────
        {
            "city": "Calgary CA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.calgary.ca/resource/kr8b-c44i.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permitnum","address":"originaladdress",
                "permit_type":"workclassgroup","description":"estateval",
                "status":"statuscurrent","filed_date":"applieddate","issued_date":"issued_date",
                "contractor":"contractorname","lic_number":None,
                "owner":"communityname","value":"estprojectcost",
                "url_tpl":"https://developmentmap.calgary.ca/",
            },
        },

        # ── NYC — DOB Quejas (violations = repair work needed) ────
        # Quejas al DOB → edificio en mal estado → propietario DEBE contratar
        {
            "city": "New York City (DOB Complaints)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofnewyork.us/resource/p5f6-p997.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_entered DESC",
                "$where": (
                    f"date_entered >= '{cutoff_iso}' "
                    f"AND status = 'Open'"
                ),
            },
            "field_map": {
                "id":"complaint_number","address":"house_number","address2":"street_name",
                "permit_type":"category","description":"complaint_description",
                "status":"status","filed_date":"date_entered","issued_date":"date_entered",
                "contractor":None,"lic_number":None,
                "owner":"community_board","value":None,
                "url_tpl":"https://a810-bisweb.nyc.gov/bisweb/",
            },
        },

        # ── Chicago — Violaciones al Código ───────────────────────
        # Violaciones abiertas = propietario obligado a contratar para reparar
        {
            "city": "Chicago (Code Violations)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofchicago.org/resource/22u3-id88.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "violation_date DESC",
                "$where": (
                    f"violation_date >= '{cutoff_iso}' "
                    f"AND violation_status = 'OPEN'"
                ),
            },
            "field_map": {
                "id":"id","address":"address",
                "permit_type":"violation_code","description":"description",
                "status":"violation_status","filed_date":"violation_date","issued_date":"violation_date",
                "contractor":None,"lic_number":None,
                "owner":"property_group","value":None,
                "url_tpl":"https://webapps1.chicago.gov/buildingviolations/",
            },
        },

        # ── Austin TX — Code Violations ───────────────────────────
        {
            "city": "Austin TX (Code Violations)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/9sh9-7u77.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "status_date DESC",
                "$where": (
                    f"status_date >= '{cutoff_iso}' "
                    f"AND case_status = 'OPEN'"
                ),
            },
            "field_map": {
                "id":"case_number","address":"address",
                "permit_type":"case_type","description":"description",
                "status":"case_status","filed_date":"open_date","issued_date":"status_date",
                "contractor":None,"lic_number":None,
                "owner":"owner_name","value":None,
                "url_tpl":"https://codelite.austintexas.gov/",
            },
        },

        # ── New Orleans — Code Enforcement ────────────────────────
        {
            "city": "New Orleans (Code Enforcement)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.nola.gov/resource/997e-4946.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "case_opened DESC",
                "$where": (
                    f"case_opened >= '{cutoff_iso}' "
                    f"AND case_status = 'Open'"
                ),
            },
            "field_map": {
                "id":"case_number","address":"address",
                "permit_type":"case_type","description":"description",
                "status":"case_status","filed_date":"case_opened","issued_date":"case_opened",
                "contractor":None,"lic_number":None,
                "owner":"owner_name","value":None,
                "url_tpl":"https://nola.gov/code-enforcement/",
            },
        },

        # ══════════════════════════════════════════════════════════
        #  BATCH 2 — APIs adicionales con alto valor para subs
        # ══════════════════════════════════════════════════════════

        # ── Austin TX — Permisos Activos Comercial/Multifamiliar ──
        # Proyectos grandes en ejecución = mayor presupuesto sub-contractor
        {
            "city": "Austin TX (Commercial Active)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/hah9-7x5p.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {"$limit": 200, "$order": "issued_date DESC"},
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_class","description":"description",
                "status":"status","filed_date":"calendar_year_issued","issued_date":"issued_date",
                "contractor":"contractor_company_name","lic_number":"contractor_license_number",
                "owner":"legal_entity_name","value":"job_value",
                "url_tpl":"https://abc.austintexas.gov/",
            },
        },

        # ── Austin TX — Issued Permits (puntos georreferenciados) ─
        {
            "city": "Austin TX (Geo)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/quv8-5ckq.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_num","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"calendar_year_issued","issued_date":"issued_date",
                "contractor":"contractor_company_name","lic_number":"contractor_license_number",
                "owner":"legal_entity_name","value":"job_value",
                "url_tpl":"https://abc.austintexas.gov/",
            },
        },

        # ── Los Ángeles — New Housing Units ───────────────────────
        # Unidades nuevas = necesitan TODOS los sub-contractors
        {
            "city": "Los Angeles (New Units)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.lacity.org/resource/cpkv-aajs.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_nbr","address":"address",
                "permit_type":"permit_type","description":"work_description",
                "status":"status","filed_date":"date_application_filed","issued_date":"issue_date",
                "contractor":"contractors_business_name","lic_number":"license_number",
                "owner":"applicant_name","value":"valuation",
                "url_tpl":"https://www.ladbsservices2.lacity.org/OnlineServices/",
            },
        },

        # ── Los Ángeles — Major Remodel ───────────────────────────
        # Remodelaciones mayores = roofing, drywall, electrical, paint
        {
            "city": "Los Angeles (Major Remodel)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.lacity.org/resource/3xpx-f9cg.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_nbr","address":"address",
                "permit_type":"permit_type","description":"work_description",
                "status":"status","filed_date":"date_application_filed","issued_date":"issue_date",
                "contractor":"contractors_business_name","lic_number":"license_number",
                "owner":"applicant_name","value":"valuation",
                "url_tpl":"https://www.ladbsservices2.lacity.org/OnlineServices/",
            },
        },

        # ── Los Ángeles — Certificate of Occupancy ────────────────
        # CO emitido = edificio terminado = instalaciones finales
        {
            "city": "Los Angeles (CO)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.lacity.org/resource/3f9m-afei.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_nbr","address":"address",
                "permit_type":"co_type","description":"work_description",
                "status":"status","filed_date":"date_application_filed","issued_date":"issue_date",
                "contractor":"contractors_business_name","lic_number":"license_number",
                "owner":"applicant_name","value":"valuation",
                "url_tpl":"https://www.ladbsservices2.lacity.org/OnlineServices/",
            },
        },

        # ── Chicago — New Demo Permits ────────────────────────────
        # Vista específica de demoliciones nuevas en Chicago
        {
            "city": "Chicago (New Demo)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofchicago.org/resource/cgh9-n8rk.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": f"issue_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"id","address":"street_number","address2":"street_name",
                "permit_type":"permit_type","description":"work_description",
                "status":"permit_status","filed_date":"application_start_date","issued_date":"issue_date",
                "contractor":"contractor_1_name","lic_number":"contractor_1_license",
                "owner":"contact_1_name","value":"reported_cost",
                "url_tpl":"https://webapps1.chicago.gov/permitview/",
            },
        },

        # ── Chicago — ADU Pre-Approval Applications ───────────────
        # ADUs = nueva unidad habitable = insulación, drywall, electrical completos
        {
            "city": "Chicago (ADU)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofchicago.org/resource/xbwc-ntpx.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "application_date DESC",
                "$where": f"application_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"id","address":"address",
                "permit_type":"adu_type","description":"adu_type",
                "status":"status","filed_date":"application_date","issued_date":"application_date",
                "contractor":None,"lic_number":None,
                "owner":"applicant_name","value":None,
                "url_tpl":"https://www.chicago.gov/city/en/depts/bldgs/supp_info/adu.html",
            },
        },

        # ── Seattle — SDCI Development Sites ─────────────────────
        # Sitios con desarrollo activo en pipeline = oportunidad temprana
        {
            "city": "Seattle (Development Sites)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.seattle.gov/resource/g337-jqnv.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {"$limit": 200, "$order": "status_date DESC"},
            "field_map": {
                "id":"application_permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"status_date",
                "contractor":"applicant_name","lic_number":None,
                "owner":"owner","value":"value",
                "url_tpl":"https://cosaccela.seattle.gov/portal/",
            },
        },

        # ── Seattle — Active Demo Non-SFR ─────────────────────────
        # Demoliciones NO residenciales = proyectos comerciales = presupuestos mayores
        {
            "city": "Seattle (Demo Non-SFR)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.seattle.gov/resource/wk2i-qsrr.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "application_date DESC",
                "$where": f"application_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"application_permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"applicant_name","lic_number":None,
                "owner":"owner","value":"value",
                "url_tpl":"https://cosaccela.seattle.gov/portal/",
            },
        },

        # ── Seattle — New Residential Units ───────────────────────
        # Pipeline de nuevas unidades residenciales en permisos o construcción
        {
            "city": "Seattle (New Residential)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.seattle.gov/resource/snip-55e2.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {"$limit": 200, "$order": "issue_date DESC"},
            "field_map": {
                "id":"application_permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issue_date",
                "contractor":"applicant_name","lic_number":None,
                "owner":"owner","value":"value",
                "url_tpl":"https://cosaccela.seattle.gov/portal/",
            },
        },

        # ── Sonoma County — Construction Permits ──────────────────
        # Cubre Petaluma, Santa Rosa, Rohnert Park, Cloverdale, etc.
        {
            "city": "Sonoma County (Construction)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.sonomacounty.ca.gov/resource/88ms-k5e7.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner","value":"valuation",
                "url_tpl":"https://sonomacounty.ca.gov/development-services/permit-sonoma",
            },
        },

        # ── Norfolk VA — Permits and Inspections ──────────────────
        {
            "city": "Norfolk VA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.norfolk.gov/resource/bnrb-u445.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"job_value",
                "url_tpl":"https://norfolk.gov/permits",
            },
        },

        # ── Kansas City MO — Permits CPD ─────────────────────────
        # Dataset complementario al 7atp-9642 ya incluido
        {
            "city": "Kansas City MO (CPD)", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.kcmo.org/resource/ntw8-aacc.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": f"issued_date >= '{cutoff_iso}'",
            },
            "field_map": {
                "id":"permit_number","address":"address",
                "permit_type":"permit_type","description":"description",
                "status":"status","filed_date":"application_date","issued_date":"issued_date",
                "contractor":"contractor_name","lic_number":"contractor_license",
                "owner":"owner_name","value":"declared_valuation",
                "url_tpl":"https://kcmo.gov/building-codes/",
            },
        },

        # ── Austin TX ──────────────────────────────────────────────
        {
            "city": "Austin TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.austintexas.gov/resource/3syk-w9eu.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' AND ("
                    "UPPER(work_class) LIKE '%ROOF%' OR "
                    "UPPER(work_class) LIKE '%DRYWALL%' OR "
                    "UPPER(work_class) LIKE '%REMODEL%' OR "
                    "UPPER(description) LIKE '%ROOF%' OR "
                    "UPPER(description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "original_address1",
                "permit_type": "permit_type_desc", "description": "work_class",
                "status": "status_current", "issued_date": "issue_date",
                "contractor": "contractor_company_name",
                "owner": "owner_name", "value": "total_valuation",
                "url_tpl": "https://austintexas.gov/permits",
            },
        },

        # ── NYC DOB Permits ─────────────────────────────────────────
        {
            "city": "New York City NY", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofnewyork.us/resource/ipu4-2q9a.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issuance_date DESC",
                "$where": (
                    f"issuance_date >= '{cutoff_iso}' AND ("
                    "permit_type='SH' OR permit_type='EW' OR "
                    "UPPER(work_type) LIKE '%RF%' OR "
                    "UPPER(work_type) LIKE '%GC%')"
                ),
            },
            "field_map": {
                "id": "job__", "address": "house__",
                "address2": "street_name",
                "permit_type": "permit_type", "description": "work_type",
                "status": "permit_status", "issued_date": "issuance_date",
                "contractor": "permittee_s_business_name",
                "phone": "permittee_s_phone__",
                "lic_number": "permittee_s_license__",
                "url_tpl": "https://www.nyc.gov/buildings",
            },
        },

        # ── Chicago General Permits ─────────────────────────────────
        {
            "city": "Chicago IL", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' AND ("
                    "UPPER(work_description) LIKE '%ROOF%' OR "
                    "UPPER(work_description) LIKE '%DRYWALL%' OR "
                    "UPPER(work_description) LIKE '%SHINGLE%' OR "
                    "UPPER(work_description) LIKE '%INTERIOR%' OR "
                    "UPPER(permit_type) LIKE '%ROOF%')"
                ),
            },
            "field_map": {
                "id": "permit_", "address": "street_number",
                "address2": "street_name",
                "permit_type": "permit_type", "description": "work_description",
                "issued_date": "issue_date",
                "contractor": "contact_1_name",
                "value": "reported_cost",
                "url_tpl": "https://webapps1.chicago.gov/permitview/",
            },
        },

        # ── Houston TX ─────────────────────────────────────────────
        {
            "city": "Houston TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.houstontx.gov/resource/yqhd-c7vv.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_issued DESC",
                "$where": (
                    f"date_issued >= '{cutoff_iso}' AND ("
                    "UPPER(proj_desc) LIKE '%ROOF%' OR "
                    "UPPER(proj_desc) LIKE '%DRYWALL%' OR "
                    "UPPER(proj_desc) LIKE '%SHINGLE%')"
                ),
            },
            "field_map": {
                "id": "permit_no", "address": "site_addr",
                "permit_type": "permit_type", "description": "proj_desc",
                "status": "status", "issued_date": "date_issued",
                "contractor": "contractor_trade_name",
                "owner": "owner_name", "value": "est_val",
                "url_tpl": "https://permits.houstontx.gov/",
            },
        },

        # ── Dallas TX ──────────────────────────────────────────────
        {
            "city": "Dallas TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://www.dallasopendata.com/resource/wzn8-6uj2.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' AND ("
                    "UPPER(description) LIKE '%ROOF%' OR "
                    "UPPER(description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_num", "address": "address",
                "permit_type": "type", "description": "description",
                "status": "status", "issued_date": "issue_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "declared_value",
                "url_tpl": "https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/default.aspx",
            },
        },

        # ── Phoenix AZ ─────────────────────────────────────────────
        {
            "city": "Phoenix AZ", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.phoenix.gov/resource/hjjt-zxqf.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "applied_date DESC",
                "$where": (
                    f"applied_date >= '{cutoff_iso}' AND ("
                    "UPPER(description) LIKE '%ROOF%' OR "
                    "UPPER(description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "address",
                "permit_type": "type", "description": "description",
                "status": "status", "issued_date": "applied_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "valuation",
                "url_tpl": "https://permits.phoenix.gov/",
            },
        },

        # ── Denver CO ──────────────────────────────────────────────
        {
            "city": "Denver CO", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.denvergov.org/resource/fbmd-4ufm.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": (
                    f"issued_date >= '{cutoff_iso}' AND ("
                    "UPPER(work_description) LIKE '%ROOF%' OR "
                    "UPPER(work_description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_no", "address": "address",
                "permit_type": "permit_type", "description": "work_description",
                "status": "status", "issued_date": "issued_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "valuation",
                "url_tpl": "https://www.denvergov.org/business/permits-licenses",
            },
        },

        # ── Nashville TN ────────────────────────────────────────────
        {
            "city": "Nashville TN", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.nashville.gov/resource/3h5a-ygsk.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "date_issued DESC",
                "$where": (
                    f"date_issued >= '{cutoff_iso}' AND ("
                    "UPPER(permit_type_description) LIKE '%ROOF%' OR "
                    "UPPER(purpose) LIKE '%ROOF%' OR "
                    "UPPER(purpose) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "address",
                "permit_type": "permit_type_description", "description": "purpose",
                "status": "status", "issued_date": "date_issued",
                "contractor": "contractor_name",
                "owner": "applicant_name", "value": "const_cost",
                "url_tpl": "https://nashville.gov/departments/codes",
            },
        },

        # ── Portland OR ─────────────────────────────────────────────
        {
            "city": "Portland OR", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.portlandoregon.gov/resource/b5b5-mfvq.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": (
                    f"issued_date >= '{cutoff_iso}' AND ("
                    "UPPER(work_description) LIKE '%ROOF%' OR "
                    "UPPER(work_description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "address",
                "permit_type": "type_description", "description": "work_description",
                "status": "status", "issued_date": "issued_date",
                "contractor": "contractor_name",
                "owner": "applicant_name", "value": "declared_valuation",
                "url_tpl": "https://www.portland.gov/bds/permitting",
            },
        },

        # ── San Antonio TX ──────────────────────────────────────────
        {
            "city": "San Antonio TX", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.sanantonio.gov/resource/b8cv-mhqn.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": (
                    f"issued_date >= '{cutoff_iso}' AND ("
                    "UPPER(work_description) LIKE '%ROOF%' OR "
                    "UPPER(work_description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "address",
                "permit_type": "permit_type", "description": "work_description",
                "status": "status", "issued_date": "issued_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "declared_valuation",
                "url_tpl": "https://www.sanantonio.gov/DSD/Permits",
            },
        },

        # ── Minneapolis MN ──────────────────────────────────────────
        {
            "city": "Minneapolis MN", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://opendata.minneapolismn.gov/resource/m3ft-wbkv.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": (
                    f"issued_date >= '{cutoff_iso}' AND ("
                    "UPPER(permitdescription) LIKE '%ROOF%' OR "
                    "UPPER(permitdescription) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permitnum", "address": "address",
                "permit_type": "permittype", "description": "permitdescription",
                "status": "statuscurrent", "issued_date": "issued_date",
                "contractor": "contractorcompanyname",
                "owner": "applicantname", "value": "contractamount",
                "url_tpl": "https://www.minneapolismn.gov/business-services/permits/",
            },
        },

        # ── Boston MA ───────────────────────────────────────────────
        {
            "city": "Boston MA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.boston.gov/resource/6ddd-vnp3.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issued_date DESC",
                "$where": (
                    f"issued_date >= '{cutoff_iso}' AND ("
                    "UPPER(description) LIKE '%ROOF%' OR "
                    "UPPER(description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permitnumber", "address": "address",
                "permit_type": "worktype", "description": "description",
                "status": "status", "issued_date": "issued_date",
                "contractor": "applicantname",
                "owner": "ownername", "value": "declared_valuation",
                "url_tpl": "https://www.boston.gov/departments/inspectional-services",
            },
        },

        # ── Atlanta GA ──────────────────────────────────────────────
        {
            "city": "Atlanta GA", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://data.atlantaga.gov/resource/bxwm-n5d7.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' AND ("
                    "UPPER(description) LIKE '%ROOF%' OR "
                    "UPPER(description) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_number", "address": "address",
                "permit_type": "permit_type", "description": "description",
                "status": "status", "issued_date": "issue_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "declared_value",
                "url_tpl": "https://www.atlantaga.gov/government/departments/city-planning/office-of-buildings",
            },
        },

        # ── Miami-Dade FL ───────────────────────────────────────────
        {
            "city": "Miami FL", "engine": "socrata", "_skip_if_no_data": True,
            "url": "https://opendata.miamidade.gov/resource/ybxr-8n3e.json",
            "timeout": SOURCE_TIMEOUT,
            "params": {
                "$limit": 200, "$order": "issue_date DESC",
                "$where": (
                    f"issue_date >= '{cutoff_iso}' AND ("
                    "UPPER(scope_of_work) LIKE '%ROOF%' OR "
                    "UPPER(scope_of_work) LIKE '%DRYWALL%')"
                ),
            },
            "field_map": {
                "id": "permit_no", "address": "address",
                "permit_type": "permit_type", "description": "scope_of_work",
                "status": "status", "issued_date": "issue_date",
                "contractor": "contractor_name",
                "owner": "owner_name", "value": "job_value",
                "url_tpl": "https://www.miamidade.gov/permits/",
            },
        },
    ]


TARGET_SERVICE_KEYWORDS = [
    # Roofing
    "roof","roofing","re-roof","reroof","shingle","shingles","tile roof",
    # Drywall
    "drywall","sheetrock","gypsum","wallboard",
    # Paint
    "paint","painting","repaint","stucco",
    # Landscaping
    "landscape","landscaping","irrigation","sprinkler","hardscape","paver",
    # Electrical
    "electrical","electric","panel upgrade","service upgrade","rewire",
    "wiring","ev charger","sub panel","main panel",
    # Generic relevant project types
    "adu","accessory dwelling","addition","remodel","renovation",
    "new construction","garage conversion","dwelling","residential",
    "tenant improvement",
]


# ── CSLB fallback ──────────────────────────────────────────────────
_CSLB_URL = "https://www2.cslb.ca.gov/OnlineServices/CheckLicenseII/CheckLicense.aspx"
_CSLB_HDR = {"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"}

def _cslb_lookup(license_number: str = None, company_name: str = None) -> dict:
    result = {}
    try:
        s = requests.Session()
        s.headers.update(_CSLB_HDR)
        r = s.get(_CSLB_URL, timeout=10)
        r.raise_for_status()
        hidden = {t.get("name",""):t.get("value","")
                  for t in BeautifulSoup(r.text,"html.parser").find_all("input",{"type":"hidden"})}
        if license_number and re.match(r"^\d{4,}$", str(license_number).strip()):
            val, typ = str(license_number).strip(), "License"
        elif company_name:
            val, typ = company_name.strip()[:50], "Business"
        else:
            return result
        payload = {**hidden,
                   "ctl00$ContentPlaceHolder1$RadioButtonList1": typ,
                   "ctl00$ContentPlaceHolder1$TextBox1": val,
                   "ctl00$ContentPlaceHolder1$Button1": "Submit"}
        r2 = s.post(_CSLB_URL, data=payload, timeout=10)
        r2.raise_for_status()
        soup2 = BeautifulSoup(r2.text, "html.parser")
        table = (soup2.find("table", {"id": re.compile(r"Grid|Results|License", re.I)})
                 or soup2.find("table"))
        if table:
            for row in table.find_all("tr")[1:2]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) >= 3:
                    result = {
                        "phone":       cells[3] if len(cells) > 3 else "",
                        "cslb_name":   cells[1] if len(cells) > 1 else "",
                        "cslb_city":   cells[2] if len(cells) > 2 else "",
                        "cslb_status": cells[4] if len(cells) > 4 else "",
                    }
    except Exception as e:
        logger.debug(f"CSLB error: {e}")
    return result


# ── Parsers ────────────────────────────────────────────────────────

def _fetch_socrata(source: dict) -> list:
    headers = {"Accept": "application/json"}
    token = os.getenv("SOCRATA_APP_TOKEN", "")
    if token:
        headers["X-App-Token"] = token
    resp = requests.get(source["url"], params=source["params"],
                        timeout=source.get("timeout", 30), headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _fetch_ckan_sql(source: dict) -> list:
    """
    CKAN datastore_search_sql — filtra server-side con WHERE.
    Mucho más rápido que el dump o ckan_search sin filtro.
    """
    resp = requests.get(
        source["url"],
        params=source["params"],
        timeout=source.get("timeout", 30),
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        err = data.get("error", {})
        raise ValueError(f"CKAN SQL error: {err}")
    return data.get("result", {}).get("records", [])


def _fetch_source(source: dict) -> tuple:
    city = source["city"]
    try:
        engine = source.get("engine", "socrata")
        if engine == "ckan_sql":
            records = _fetch_ckan_sql(source)
        else:
            records = _fetch_socrata(source)
        return (city, records, None)
    except Exception as e:
        return (city, [], e)


# ── Normalización ──────────────────────────────────────────────────

def _normalize_permit(raw: dict, field_map: dict, city: str) -> dict:
    """
    Construye el lead normalizado.
    Para SF construye dirección completa con street_suffix.
    """
    get = lambda k: raw.get(field_map.get(k, "") or "", "") or ""

    # Dirección
    parts = [get("address").strip()]
    if field_map.get("address_sfx"):
        sfx = raw.get(field_map.get("address_sfx","") or "", "")
        if sfx:
            parts.append(str(sfx).strip())
    if field_map.get("address2") and raw.get(field_map.get("address2") or ""):
        parts.append(raw[field_map["address2"]].strip())
    if field_map.get("address_type"):
        atype = raw.get(field_map.get("address_type","") or "", "")
        if atype:
            parts.append(str(atype).strip())
    address = " ".join(p for p in parts if p).strip()

    permit_id = get("id")
    raw_vals  = {v: raw.get(v, "") for k, v in field_map.items()
                 if v and k != "url_tpl" and not k.startswith("address")}
    try:
        permit_url = field_map.get("url_tpl", "").format(**raw_vals)
    except KeyError:
        permit_url = field_map.get("url_tpl", "")

    return {
        "id":          f"{city}_{permit_id}",
        "city":        city,
        "address":     address,
        "permit_type": get("permit_type"),
        "description": get("description"),
        "status":      get("status"),
        "filed_date":  get("filed_date")[:10] if get("filed_date") else "",
        "issued_date": get("issued_date")[:10] if get("issued_date") else "",
        "contractor":  get("contractor").strip(),
        "lic_number":  get("lic_number").strip(),
        "owner":       get("owner").strip(),
        "value":       get("value"),
        "value_float": _parse_value(get("value")),
        "permit_url":  permit_url,
    }


def _is_relevant(lead: dict) -> bool:
    if lead["value_float"] < MIN_PERMIT_VALUE:
        return False
    haystack = ((lead.get("description") or "") + " " + (lead.get("permit_type") or "")).lower()
    return any(kw in haystack for kw in TARGET_SERVICE_KEYWORDS)


def _is_recent(lead: dict) -> bool:
    date_str = lead.get("issued_date") or lead.get("filed_date") or ""
    if not date_str:
        return True
    try:
        issued = datetime.strptime(date_str[:10], "%Y-%m-%d")
        return issued >= (datetime.utcnow() - timedelta(days=30 * PERMIT_MONTHS))
    except Exception:
        return True


# ── AGENTE ─────────────────────────────────────────────────────────

class PermitsAgent(BaseAgent):
    name      = "🏗️ Permisos de Construcción — Bay Area"
    emoji     = "🏗️"
    agent_key = "permits"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._contacts   = load_all_contacts()
        self._cslb_cache = {}

    def _enrich_gc(self, lead: dict) -> dict:
        """
        Enriquece datos de contacto del GC:
          1. CSV local por nombre (fuzzy match)
          2. CSLB por número de licencia
          3. CSLB por nombre de empresa
          4. Si contractor vacío pero hay owner → intenta con owner
        """
        contractor = lead.get("contractor", "").strip()
        lic        = lead.get("lic_number", "").strip()
        owner      = lead.get("owner", "").strip()

        # Para SF, si no hay GC pero hay owner, usamos owner como contacto
        search_name = contractor or owner
        cache_key   = lic or search_name
        if not cache_key:
            return {}
        if cache_key in self._cslb_cache:
            return self._cslb_cache[cache_key]

        enrichment = {}

        # ── Paso 1: CSV local ─────────────────────────────────────
        if search_name:
            match = lookup_contact(search_name, self._contacts)
            if match:
                enrichment = {
                    "contact_phone":  match.get("phone", ""),
                    "contact_email":  match.get("email", ""),
                    "contact_source": f"CSV ({match['source']})",
                    "contact_name":   match["raw_name"],
                }

        # ── Paso 2: CSLB si no hay teléfono/email del CSV ─────────
        has_contact = enrichment.get("contact_phone") or enrichment.get("contact_email")
        if not has_contact:
            time.sleep(0.3)
            cslb = {}
            if lic:
                cslb = _cslb_lookup(license_number=lic)
            if not cslb.get("phone") and contractor:
                cslb = _cslb_lookup(company_name=contractor)
            if not cslb.get("phone") and owner and owner != contractor:
                cslb = _cslb_lookup(company_name=owner)

            if cslb:
                enrichment = {
                    "contact_phone":  cslb.get("phone", ""),
                    "contact_email":  "",
                    "contact_source": "CSLB",
                    "contact_name":   cslb.get("cslb_name", search_name),
                    "cslb_city":      cslb.get("cslb_city", ""),
                    "cslb_status":    cslb.get("cslb_status", ""),
                }

        self._cslb_cache[cache_key] = enrichment
        return enrichment

    def fetch_leads(self) -> list:
        all_leads = []
        sources   = _build_sources()
        active    = [s for s in sources
                     if not (s.get("_requires_token") and not os.getenv("SOCRATA_APP_TOKEN"))]

        with ThreadPoolExecutor(max_workers=PARALLEL_CITIES) as executor:
            futures = {executor.submit(_fetch_source, s): s for s in active}
            for fut in as_completed(futures):
                source       = futures[fut]
                city         = source["city"]
                skip_on_fail = source.get("_skip_if_no_data", False)
                _, records, error = fut.result()

                if error:
                    (logger.debug if skip_on_fail else logger.error)(
                        f"[{city}] {'Omitido' if skip_on_fail else 'Error'}: {error}"
                    )
                    continue

                city_n = 0
                for raw in records:
                    lead = _normalize_permit(raw, source["field_map"], city)
                    if _is_relevant(lead) and _is_recent(lead):
                        lead.update(self._enrich_gc(lead))
                        all_leads.append(lead)
                        city_n += 1

                logger.info(
                    f"[{city}] {len(records)} registros → "
                    f"{city_n} leads (>${MIN_PERMIT_VALUE/1000:.0f}K, "
                    f"últimos {PERMIT_MONTHS} meses)"
                )

        return all_leads

    def notify(self, lead: dict):
        phone  = lead.get("contact_phone") or "—"
        email  = lead.get("contact_email") or "—"
        source = lead.get("contact_source", "")
        value  = lead.get("value_float", 0)

        fields = {
            "📍 Ciudad":           lead.get("city"),
            "🔖 Tipo de Permiso":  lead.get("permit_type"),
            "📝 Descripción":      (lead.get("description") or "—")[:200],
            "📅 Fecha Emisión":    lead.get("issued_date"),
            "💰 Valor Estimado":   f"${value:,.0f}" if value else "—",
            "👷 Contratista (GC)": lead.get("contractor") or "—",
            "🪪 Licencia CSLB":    lead.get("lic_number") or "—",
            "📞 Teléfono GC":      f"{phone}  _(via {source})_" if source and phone != "—" else phone,
            "✉️  Email GC":        email,
            "👤 Propietario":      lead.get("owner") or "—",
        }
        if lead.get("contact_source") == "CSLB":
            if lead.get("cslb_city"):
                fields["🏢 Ciudad GC (CSLB)"] = lead["cslb_city"]
            if lead.get("cslb_status"):
                fields["✅ Estado Licencia"]   = lead["cslb_status"]

        send_lead(
            agent_name=self.name, emoji=self.emoji,
            title=f"{lead.get('city')} — {lead.get('address')}",
            fields=fields, url=lead.get("permit_url"),
            cta="📲 Contacta al GC y ofrece insulación para el proyecto",
        )
