"""
utils/lead_enrichment.py
━━━━━━━━━━━━━━━━━━━━━━━━━
Enriquecimiento de leads con APIs gubernamentales.

Soporta:
  1. CSLB (Contractors State License Board) — Licencias de contratistas CA
  2. County Assessor APIs — Datos de propiedad y valoración
  3. Building Permit APIs — Historial de permisos de construcción (50+ ciudades)
  4. Secretary of State Business Search — Información de empresas registradas
  5. Census API — Datos demográficos del área
  6. NYC DOB — Permits, Violations, Complaints, COs
  7. SF DBI — Building permits, inspections, complaints
  8. Chicago BLDG — Permits, violations, energy benchmarking
  9. Seattle SDCI — Land use, building permits, code compliance
  10. Austin Development — Building permits, demolitions
  11. LA LADBS — Building permits, inspections
  12. Calgary — Building permits, assessments, energy
  13. Montgomery County MD — Permits, energy benchmarking
  14. Buffalo — Permits, code violations, assessments
  15. Providence — Permits, tax rolls, energy
  16. Cambridge — Building permits, housing starts, BEUDO
  17. Norfolk — Permits, violations, assessments
  18. Kansas City — Permits, dangerous buildings, energy
  19. Honolulu — Building permits
  20. Cincinnati — Building permits, code enforcement
  21. Energy Benchmarking — LL84 (NYC), Chicago, Seattle, Austin, Montgomery Co

Prioridad: Cache local → APIs específicas por ciudad → CSLB → Assessor → Permits → SoS → Census

Uso:
    from utils.lead_enrichment import enrich_lead, get_government_data

    lead = {
        "contractor": "ABC Construction",
        "contractor_license": "123456",
        "address": "123 Main St",
        "city": "Oakland",
        "zip": "94601",
        "county": "Alameda",
        "owner": "John Doe",
    }

    enriched = enrich_lead(lead)
    # Retorna: license_info, property_value, permit_history, business_info, demographics
"""

import os
import logging
import requests
from functools import lru_cache
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# ── Configuración de APIs ────────────────────────────────────────
CSLB_API_BASE     = os.getenv("CSLB_API_BASE", "https://api.cslb.ca.gov/api/v1")
CSLB_API_KEY      = os.getenv("CSLB_API_KEY", "")
ASSESSOR_API_URL  = os.getenv("ASSESSOR_API_URL", "")  # URL genérica, se personaliza por county
CENSUS_API_KEY    = os.getenv("CENSUS_API_KEY", "")

# NYC Open Data API
NYC_API_BASE      = os.getenv("NYC_API_BASE", "https://data.cityofnewyork.us/resource")
NYC_APP_TOKEN     = os.getenv("NYC_APP_TOKEN", "")

# San Francisco Open Data API
SF_API_BASE       = os.getenv("SF_API_BASE", "https://data.sfgov.org/resource")
SF_APP_TOKEN      = os.getenv("SF_APP_TOKEN", "")

# Chicago Open Data API
CHICAGO_API_BASE  = os.getenv("CHICAGO_API_BASE", "https://data.cityofchicago.org/resource")
CHICAGO_APP_TOKEN = os.getenv("CHICAGO_APP_TOKEN", "")

# Seattle Open Data API
SEATTLE_API_BASE  = os.getenv("SEATTLE_API_BASE", "https://data.seattle.gov/resource")
SEATTLE_APP_TOKEN = os.getenv("SEATTLE_APP_TOKEN", "")

# Austin Open Data API
AUSTIN_API_BASE   = os.getenv("AUSTIN_API_BASE", "https://data.austintexas.gov/resource")
AUSTIN_APP_TOKEN  = os.getenv("AUSTIN_APP_TOKEN", "")

# Los Angeles Open Data API
LA_API_BASE       = os.getenv("LA_API_BASE", "https://data.lacity.org/resource")
LA_APP_TOKEN      = os.getenv("LA_APP_TOKEN", "")

# Calgary Open Data API
CALGARY_API_BASE  = os.getenv("CALGARY_API_BASE", "https://data.calgary.ca/resource")
CALGARY_APP_TOKEN = os.getenv("CALGARY_APP_TOKEN", "")

# Montgomery County MD Open Data API
MONTCO_API_BASE   = os.getenv("MONTCO_API_BASE", "https://data.montgomerycountymd.gov/resource")
MONTCO_APP_TOKEN  = os.getenv("MONTCO_APP_TOKEN", "")

# Buffalo Open Data API
BUFFALO_API_BASE  = os.getenv("BUFFALO_API_BASE", "https://data.buffalony.gov/resource")
BUFFALO_APP_TOKEN = os.getenv("BUFFALO_APP_TOKEN", "")

# Providence Open Data API
PROVIDENCE_API_BASE = os.getenv("PROVIDENCE_API_BASE", "https://data.providenceri.gov/resource")
PROVIDENCE_APP_TOKEN = os.getenv("PROVIDENCE_APP_TOKEN", "")

# Cambridge Open Data API
CAMBRIDGE_API_BASE = os.getenv("CAMBRIDGE_API_BASE", "https://data.cambridgema.gov/resource")
CAMBRIDGE_APP_TOKEN = os.getenv("CAMBRIDGE_APP_TOKEN", "")

# Norfolk Open Data API
NORFOLK_API_BASE  = os.getenv("NORFOLK_API_BASE", "https://data.norfolk.gov/resource")
NORFOLK_APP_TOKEN = os.getenv("NORFOLK_APP_TOKEN", "")

# Kansas City Open Data API
KCMO_API_BASE     = os.getenv("KCMO_API_BASE", "https://data.kcmo.org/resource")
KCMO_APP_TOKEN    = os.getenv("KCMO_APP_TOKEN", "")

# Honolulu Open Data API
HONOLULU_API_BASE = os.getenv("HONOLULU_API_BASE", "https://data.honolulu.gov/resource")
HONOLULU_APP_TOKEN = os.getenv("HONOLULU_APP_TOKEN", "")

# Cincinnati Open Data API
CINCINNATI_API_BASE = os.getenv("CINCINNATI_API_BASE", "https://data.cincinnati-oh.gov/resource")
CINCINNATI_APP_TOKEN = os.getenv("CINCINNATI_APP_TOKEN", "")

# Sonoma County Open Data API
SONOMA_API_BASE   = os.getenv("SONOMA_API_BASE", "https://data.sonomacounty.ca.gov/resource")

# Cache en memoria para evitar llamadas repetidas
_enrichment_cache: Dict[str, Any] = {}
_cache_timestamps: Dict[str, datetime] = {}
_CACHE_TTL_HOURS = 24  # Las APIs gubernamentales cambian poco, cache de 24h


def enrich_lead(lead: dict, force_refresh: bool = False) -> dict:
    """
    Enriquece un lead con datos de múltiples fuentes gubernamentales.
    
    Args:
        lead: Diccionario con datos del lead (contractor, address, city, etc.)
        force_refresh: Si True, ignora el cache y consulta las APIs
    
    Returns:
        dict con:
            - license_info: Información de licencia CSLB
            - property_info: Datos de propiedad del assessor
            - permit_history: Historial de permisos recientes
            - business_info: Información de registro empresarial
            - demographics: Datos demográficos del área (Census)
            - enrichment_score: 0-100 indicando calidad del enriquecimiento
            - sources: Lista de fuentes consultadas exitosamente
    """
    cache_key = _build_cache_key(lead)
    
    # Verificar cache
    if not force_refresh and cache_key in _enrichment_cache:
        cached_at = _cache_timestamps.get(cache_key)
        if cached_at and datetime.now() - cached_at < timedelta(hours=_CACHE_TTL_HOURS):
            logger.debug(f"[Lead Enrichment] Usando cache para {cache_key}")
            return _enrichment_cache[cache_key]
    
    result = {
        "license_info": {},
        "property_info": {},
        "permit_history": [],
        "business_info": {},
        "demographics": {},
        "enrichment_score": 0,
        "sources": [],
        "errors": [],
    }
    
    sources_count = 0
    
    # ── 1. CSLB — Licencia de contratista ────────────────────────
    license_num = lead.get("contractor_license") or lead.get("license_number") or ""
    contractor_name = lead.get("contractor") or lead.get("company_name") or ""
    
    if license_num or contractor_name:
        license_data = _cslb_lookup(license_num, contractor_name)
        if license_data:
            result["license_info"] = license_data
            result["sources"].append("CSLB")
            sources_count += 1
    
    # ── 2. County Assessor — Datos de propiedad ──────────────────
    address = lead.get("address") or ""
    city = lead.get("city") or ""
    zip_code = lead.get("zip") or lead.get("zip_code") or ""
    county = lead.get("county") or lead.get("_county") or ""
    apn = lead.get("apn") or lead.get("parcel_number") or ""  # APN si ya se conoce
    
    if (address and city) or apn:
        property_data = _assessor_lookup(address, city, zip_code, county, apn)
        if property_data:
            result["property_info"] = property_data
            result["sources"].append("Assessor")
            sources_count += 1
    
    # ── 3. Building Permits — Historial de permisos ──────────────
    if address and city:
        permits = _permits_history_lookup(address, city, county)
        if permits:
            result["permit_history"] = permits
            result["sources"].append("Permits")
            sources_count += 1
    
    # ── 4. Secretary of State — Registro empresarial ─────────────
    if contractor_name:
        business_data = _sos_business_lookup(contractor_name)
        if business_data:
            result["business_info"] = business_data
            result["sources"].append("Secretary of State")
            sources_count += 1
    
    # ── 5. Census API — Datos demográficos ───────────────────────
    if zip_code or (city and county):
        demo_data = _census_demographics_lookup(zip_code, city, county)
        if demo_data:
            result["demographics"] = demo_data
            result["sources"].append("Census")
            sources_count += 1

    # ── 6. FEMA Flood Zone — Zona de inundación (gratuito) ───────
    lat = lead.get("lat") or lead.get("_lat")
    lon = lead.get("lon") or lead.get("_lon") or lead.get("long")
    if lat and lon:
        try:
            flood_data = _fema_flood_zone_lookup(float(lat), float(lon))
            if flood_data:
                result["flood_zone"] = flood_data
                result["sources"].append("FEMA")
                sources_count += 1
        except (ValueError, TypeError):
            pass

    # Calcular score de enriquecimiento
    result["enrichment_score"] = min(sources_count * 16, 100)
    
    # Guardar en cache
    _enrichment_cache[cache_key] = result
    _cache_timestamps[cache_key] = datetime.now()
    
    return result


def _build_cache_key(lead: dict) -> str:
    """Construye una clave única para el cache basada en el lead."""
    parts = [
        lead.get("contractor_license") or "",
        lead.get("contractor") or "",
        lead.get("address") or "",
        lead.get("city") or "",
        lead.get("apn") or "",
    ]
    return "|".join(parts).strip("|") or f"lead_{hash(str(lead))}"


# ── 1. CSLB API ───────────────────────────────────────────────────

def _cslb_lookup(license_num: str = "", contractor_name: str = "") -> dict:
    """
    Consulta la API de CSLB para obtener información de licencias.
    
    Free tier: Público sin autenticación para búsquedas básicas.
    Datos disponibles:
        - Estado de licencia (Active/Expired/Suspended)
        - Clasificación (A, B, C-10, etc.)
        - Fecha de emisión y expiración
        - Dirección del negocio
        - Bond e insurance info
        - Disciplinary actions
    """
    try:
        # Si tenemos número de licencia, búsqueda directa
        if license_num:
            url = f"{CSLB_API_BASE}/LicenseDetails/{license_num}"
            headers = {"Authorization": f"Bearer {CSLB_API_KEY}"} if CSLB_API_KEY else {}
            
            resp = requests.get(url, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                return _parse_cslb_response(data)
            elif resp.status_code == 404:
                logger.debug(f"[CSLB] Licencia {license_num} no encontrada")
            else:
                logger.warning(f"[CSLB] Error {resp.status_code}: {resp.text[:200]}")
        
        # Búsqueda por nombre de contratista
        if contractor_name:
            url = f"{CSLB_API_BASE}/LicenseSearch"
            params = {
                "businessName": contractor_name[:50],  # Limitar longitud
                "limit": 5,
            }
            headers = {"Authorization": f"Bearer {CSLB_API_KEY}"} if CSLB_API_KEY else {}
            
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                if results:
                    # Retornar el primer resultado más relevante
                    return _parse_cslb_response(results[0])
        
        # Fallback: intentar scraping de sitio público si API falla
        return _cslb_public_search(license_num, contractor_name)
        
    except Exception as e:
        logger.debug(f"[CSLB] Error en lookup: {e}")
        return {}
    
    return {}


def _parse_cslb_response(data: dict) -> dict:
    """Parsea respuesta de CSLB a formato estandarizado."""
    license_info = data.get("license", {}) or data
    
    status = (license_info.get("status") or "").upper()
    is_active = status in ("ACTIVE", "CURRENT")
    
    classifications = license_info.get("classifications", [])
    primary_class = classifications[0] if classifications else {}
    
    # Extraer información relevante
    return {
        "license_number": license_info.get("licenseNumber") or license_info.get("number", ""),
        "status": status,
        "is_active": is_active,
        "issue_date": license_info.get("issueDate") or license_info.get("issued", ""),
        "expire_date": license_info.get("expirationDate") or license_info.get("expires", ""),
        "classification_code": primary_class.get("code", ""),
        "classification_description": primary_class.get("description", ""),
        "business_name": license_info.get("businessName") or license_info.get("name", ""),
        "address": license_info.get("address", {}).get("street", ""),
        "city": license_info.get("address", {}).get("city", ""),
        "state": license_info.get("address", {}).get("state", "CA"),
        "zip": license_info.get("address", {}).get("zip", ""),
        "phone": license_info.get("phoneNumber", ""),
        "bond_amount": license_info.get("bondAmount", 0),
        "has_insurance": bool(license_info.get("insuranceInfo")),
        "disciplinary_actions": len(license_info.get("disciplinaryActions", [])),
        "source": "CSLB",
    }


def _cslb_public_search(license_num: str = "", contractor_name: str = "") -> dict:
    """
    Fallback: búsqueda en el portal público de CSLB.
    Nota: Esto es un placeholder. En producción se implementaría
    web scraping respetando robots.txt y términos de servicio.
    """
    # Placeholder para implementación futura
    # En producción, usar selenium o requests + BeautifulSoup
    logger.debug(f"[CSLB Public] Búsqueda fallback para {license_num or contractor_name}")
    return {}


# ── 2. County Assessor API ────────────────────────────────────────

def _assessor_lookup(address: str = "", city: str = "", zip_code: str = "",
                     county: str = "", apn: str = "") -> dict:
    """
    Consulta APIs de County Assessor para datos de propiedad.
    
    Cada condado tiene su propia API. Ejemplos:
        - Alameda: https://acgov.org/assessor/
        - San Francisco: https://sfassessor.org/
        - Los Angeles: https://assessor.lacounty.gov/
    
    Datos disponibles:
        - APN (Assessor Parcel Number)
        - Valor tasado (land + improvements)
        - Año de construcción
        - Sqft de vivienda
        - Número de habitaciones/baños
        - Tipo de propiedad
        - Owner name & mailing address
        - Última venta (fecha y precio)
    """
    try:
        # Si tenemos APN, búsqueda directa (más precisa)
        if apn:
            return _assessor_lookup_by_apn(apn, county)
        
        # Búsqueda por dirección
        if address and city:
            return _assessor_lookup_by_address(address, city, zip_code, county)
            
    except Exception as e:
        logger.debug(f"[Assessor] Error en lookup: {e}")
    
    return {}


def _assessor_lookup_by_apn(apn: str, county: str = "") -> dict:
    """Búsqueda directa por APN."""
    # Determinar endpoint basado en county
    county = (county or "").lower().replace(" county", "").replace(" ", "_")
    
    # URLs de ejemplo (en producción, configurar por environment variables)
    assessor_endpoints = {
        "alameda": "https://search.acgov.org/property/parcel/",
        "san_francisco": "https://sfassessor.org/property/",
        "los_angeles": "https://assessor.lacounty.gov/parcels/",
        "orange": "https://ocassessor.gov/property/",
        "san_diego": "https://sdttc.sandiegocounty.gov/property/",
    }
    
    base_url = assessor_endpoints.get(county, ASSESSOR_API_URL)
    if not base_url:
        logger.debug(f"[Assessor] No hay endpoint configurado para {county}")
        return {}
    
    try:
        # Normalizar APN (remover guiones, espacios)
        apn_clean = apn.replace("-", "").replace(" ", "")
        
        # Intentar llamada API (cada condado es diferente)
        if "acgov" in base_url:  # Alameda
            resp = requests.get(f"{base_url}{apn_clean}", timeout=10)
            if resp.status_code == 200:
                return _parse_alameda_assessor(resp.json(), apn)
        
        # Para otros condados, implementar parsers específicos
        # O usar scraping como fallback
        
    except Exception as e:
        logger.debug(f"[Assessor APN] Error: {e}")
    
    return {}


def _assessor_lookup_by_address(address: str, city: str, zip_code: str,
                                 county: str = "") -> dict:
    """Búsqueda por dirección."""
    # Muchos assessors permiten búsqueda por dirección
    # Implementación genérica
    
    try:
        # Construir query parameters
        params = {
            "address": address[:100],
            "city": city[:50],
            "zip": zip_code,
        }
        
        # Intentar con API genérica si está configurada
        if ASSESSOR_API_URL:
            resp = requests.get(ASSESSOR_API_URL, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("results"):
                    return _parse_generic_assessor(data["results"][0])
        
        # Fallback: retornar estructura vacía pero indicar que se intentó
        logger.debug(f"[Assessor Address] Sin datos para {address}, {city}")
        
    except Exception as e:
        logger.debug(f"[Assessor Address] Error: {e}")
    
    return {}


def _parse_alameda_assessor(data: dict, apn: str) -> dict:
    """Parsea respuesta de Alameda County Assessor."""
    prop = data.get("property", {}) or data
    
    assessed_value = prop.get("assessedValue", {})
    land_value = assessed_value.get("land", 0)
    improvement_value = assessed_value.get("improvements", 0)
    
    return {
        "apn": apn,
        "property_type": prop.get("propertyType", ""),
        "year_built": prop.get("yearBuilt", 0),
        "sqft": prop.get("livingArea", 0),
        "lot_size": prop.get("lotSize", 0),
        "bedrooms": prop.get("bedrooms", 0),
        "bathrooms": prop.get("bathrooms", 0),
        "assessed_land_value": land_value,
        "assessed_improvement_value": improvement_value,
        "total_assessed_value": land_value + improvement_value,
        "last_sale_date": prop.get("lastSaleDate", ""),
        "last_sale_price": prop.get("lastSalePrice", 0),
        "owner_name": prop.get("owner", {}).get("name", ""),
        "owner_mailing_address": prop.get("owner", {}).get("mailingAddress", ""),
        "tax_year": prop.get("taxYear", ""),
        "source": "County Assessor",
    }


def _parse_generic_assessor(data: dict) -> dict:
    """Parser genérico para respuestas de assessor."""
    return {
        "apn": data.get("apn") or data.get("parcelNumber", ""),
        "property_type": data.get("propertyType", ""),
        "year_built": data.get("yearBuilt", 0),
        "sqft": data.get("sqft") or data.get("livingArea", 0),
        "lot_size": data.get("lotSize") or data.get("acreage", 0),
        "bedrooms": data.get("bedrooms", 0),
        "bathrooms": data.get("bathrooms", 0),
        "assessed_land_value": data.get("landValue", 0),
        "assessed_improvement_value": data.get("improvementValue", 0),
        "total_assessed_value": data.get("totalValue", 0),
        "last_sale_date": data.get("saleDate", ""),
        "last_sale_price": data.get("salePrice", 0),
        "owner_name": data.get("ownerName", ""),
        "source": "County Assessor",
    }


# ── 3. Building Permits History ───────────────────────────────────

def _permits_history_lookup(address: str, city: str, county: str = "") -> list:
    """
    Busca historial de permisos de construcción para una propiedad.
    
    Fuentes:
        - City building department APIs
        - County planning departments
        - Open data portals (Socrata, etc.)
    
    Retorna lista de permisos recientes (últimos 5 años).
    """
    try:
        # Muchas ciudades usan plataformas como:
        # - Accela Civic Platform
        # - Viewpoint (antiguo e-PlanReview)
        # - Socrata Open Data
        # - Custom APIs
        
        # Ejemplo de búsqueda en portal open data
        permits = _search_open_data_permits(address, city, county)
        
        if permits:
            return permits
        
        # Fallback: intentar con endpoints conocidos
        return _search_city_permit_portal(address, city, county)
        
    except Exception as e:
        logger.debug(f"[Permits] Error: {e}")
    
    return []


def _permits_history_lookup_extended(address: str, city: str, county: str = "", 
                                      property_type: str = "residential") -> dict:
    """
    Búsqueda extendida de permisos incluyendo violaciones, energy benchmarking y assessment.
    
    Retorna un diccionario con:
        - permits: Lista de permisos de construcción/demolición
        - violations: Violaciones activas/históricas
        - complaints: Quejas recibidas
        - energy_benchmarking: Datos de energía (si disponibles)
        - assessments: Valoraciones de propiedad
    """
    result = {
        "permits": [],
        "violations": [],
        "complaints": [],
        "energy_benchmarking": {},
        "assessments": {},
    }
    
    try:
        city_key = city.lower()
        
        # ── NYC: Violations, Complaints, Energy Benchmarking ─────────────
        if city_key == "new york":
            result["violations"] = _nyc_violations_lookup(address)
            result["complaints"] = _nyc_complaints_lookup(address)
            result["energy_benchmarking"] = _nyc_energy_benchmarking_lookup(address)
        
        # ── San Francisco: Inspections, Complaints ───────────────────────
        elif city_key == "san francisco":
            result["violations"] = _sf_violations_lookup(address)
            result["complaints"] = _sf_complaints_lookup(address)
        
        # ── Chicago: Energy Benchmarking, Violations ─────────────────────
        elif city_key == "chicago":
            result["energy_benchmarking"] = _chicago_energy_benchmarking_lookup(address)
            result["violations"] = _chicago_violations_lookup(address)
        
        # ── Seattle: Code Compliance ─────────────────────────────────────
        elif city_key == "seattle":
            result["violations"] = _seattle_code_compliance_lookup(address)
        
        # ── Austin: Code Cases ───────────────────────────────────────────
        elif city_key == "austin":
            result["complaints"] = _austin_code_cases_lookup(address)
        
        # ── Los Angeles: Inspections ─────────────────────────────────────
        elif city_key == "los angeles":
            result["violations"] = _la_inspections_lookup(address)
        
        # ── Calgary: Energy Performance ──────────────────────────────────
        elif city_key == "calgary":
            result["energy_benchmarking"] = _calgary_energy_lookup(address)
        
        # ── Montgomery County MD: Energy Benchmarking ────────────────────
        elif "montgomery" in city_key:
            result["energy_benchmarking"] = _montco_energy_lookup(address)
        
        # ── Buffalo: Code Violations ─────────────────────────────────────
        elif city_key == "buffalo":
            result["violations"] = _buffalo_violations_lookup(address)
        
        # ── Providence: Tax Rolls ────────────────────────────────────────
        elif city_key == "providence":
            result["assessments"] = _providence_tax_roll_lookup(address)
        
        # ── Cambridge: BEUDO Energy ──────────────────────────────────────
        elif city_key == "cambridge":
            result["energy_benchmarking"] = _cambridge_beudo_lookup(address)
        
        # ── Norfolk: Violations, Assessments ─────────────────────────────
        elif city_key == "norfolk":
            result["violations"] = _norfolk_violations_lookup(address)
            result["assessments"] = _norfolk_assessments_lookup(address)
        
        # ── Kansas City: Dangerous Buildings, Energy ─────────────────────
        elif "kansas" in city_key:
            result["violations"] = _kc_dangerous_buildings_lookup(address)
            result["energy_benchmarking"] = _kc_energy_lookup(address)
        
        # ── Cincinnati: Code Enforcement ─────────────────────────────────
        elif city_key == "cincinnati":
            result["violations"] = _cincinnati_code_enforcement_lookup(address)
        
    except Exception as e:
        logger.debug(f"[Extended Permits] Error: {e}")
    
    return result


# ── NYC DOB Functions ─────────────────────────────────────────────────

def _nyc_violations_lookup(address: str) -> list:
    """Busca violaciones activas e históricas de NYC DOB."""
    datasets = [
        "3h2n-5cm9",  # DOB Violations
        "sjhj-bc8q",  # Active DoB Violations
    ]
    return _query_nyc_datasets(datasets, address)


def _nyc_complaints_lookup(address: str) -> list:
    """Busca quejas recibidas por NYC DOB."""
    dataset = "eabe-havv"  # DOB Complaints Received
    return _query_nyc_datasets([dataset], address)


def _nyc_energy_benchmarking_lookup(address: str) -> dict:
    """Busca datos de energy benchmarking de NYC (Local Law 84)."""
    datasets = [
        "5zyy-y8am",  # 2023 to Present
        "usc3-8zwd",  # 2021 (Data for 2020)
        "wcm8-aq5w",  # 2020 (Data for 2019)
    ]
    results = _query_nyc_datasets(datasets, address)
    return results[0] if results else {}


def _query_nyc_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de NYC."""
    results = []
    
    params = {
        "$where": f"LOWER(house_number) LIKE '%{address.split()[0].lower()}%' OR LOWER(street_name) LIKE '%{' '.join(address.split()[1:]).lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if NYC_APP_TOKEN:
        params["$$app_token"] = NYC_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{NYC_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
                    if len(results) >= 5:
                        break
        except Exception as e:
            logger.debug(f"[NYC {dataset_id}] Error: {e}")
    
    return results


# ── San Francisco DBI Functions ────────────────────────────────────────

def _sf_violations_lookup(address: str) -> list:
    """Busca violaciones de SF DBI."""
    datasets = [
        "nbtm-fbw5",  # Notices of Violation issued by DBI
    ]
    return _query_sf_datasets(datasets, address)


def _sf_complaints_lookup(address: str) -> list:
    """Busca quejas de SF DBI."""
    datasets = [
        "gm2e-bten",  # DBI Complaints (All Divisions)
        "8kfg-ti6d",  # Top Inspections Complaints
    ]
    return _query_sf_datasets(datasets, address)


def _query_sf_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de SF."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if SF_APP_TOKEN:
        params["$$app_token"] = SF_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{SF_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[SF {dataset_id}] Error: {e}")
    
    return results


# ── Chicago Functions ──────────────────────────────────────────────────

def _chicago_energy_benchmarking_lookup(address: str) -> dict:
    """Busca energy benchmarking de Chicago."""
    datasets = [
        "3a36-5x9a",  # 2023 Data Reported in 2024
        "mz3g-jagv",  # 2022 Data Reported in 2023
        "g5i5-yz37",  # Energy Benchmarking Covered Buildings
    ]
    results = _query_chicago_datasets(datasets, address)
    return results[0] if results else {}


def _chicago_violations_lookup(address: str) -> list:
    """Busca violaciones de Chicago."""
    # Chicago no tiene endpoint directo de violaciones en la lista proporcionada
    # Se puede usar dangerous buildings como proxy
    return []


def _query_chicago_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Chicago."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if CHICAGO_APP_TOKEN:
        params["$$app_token"] = CHICAGO_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{CHICAGO_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Chicago {dataset_id}] Error: {e}")
    
    return results


# ── Seattle Functions ──────────────────────────────────────────────────

def _seattle_code_compliance_lookup(address: str) -> list:
    """Busca code compliance de Seattle."""
    datasets = [
        "8s4s-3hc9",  # Code Complaints and Violations
        "ud3x-cvhp",  # Code Compliance Complaints by Year
    ]
    return _query_seattle_datasets(datasets, address)


def _query_seattle_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Seattle."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if SEATTLE_APP_TOKEN:
        params["$$app_token"] = SEATTLE_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{SEATTLE_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Seattle {dataset_id}] Error: {e}")
    
    return results


# ── Austin Functions ───────────────────────────────────────────────────

def _austin_code_cases_lookup(address: str) -> list:
    """Busca code cases de Austin."""
    datasets = [
        "6wtj-zbtb",  # Austin Code Complaint Cases
        "iuda-bhaq",  # Code Cases Closed FY 2020
    ]
    return _query_austin_datasets(datasets, address)


def _query_austin_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Austin."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if AUSTIN_APP_TOKEN:
        params["$$app_token"] = AUSTIN_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{AUSTIN_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Austin {dataset_id}] Error: {e}")
    
    return results


# ── Los Angeles Functions ──────────────────────────────────────────────

def _la_inspections_lookup(address: str) -> list:
    """Busca inspecciones de LA Building and Safety."""
    dataset = "9w5z-rg2h"  # Building and Safety Inspections
    return _query_la_datasets([dataset], address)


def _query_la_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de LA."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if LA_APP_TOKEN:
        params["$$app_token"] = LA_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{LA_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[LA {dataset_id}] Error: {e}")
    
    return results


# ── Calgary Functions ──────────────────────────────────────────────────

def _calgary_energy_lookup(address: str) -> dict:
    """Busca energy performance de Calgary."""
    datasets = [
        "gmrr-dmz6",  # Building Energy Performance - Filter by Year
        "crbp-innf",  # Corporate Energy Consumption
    ]
    results = _query_calgary_datasets(datasets, address)
    return results[0] if results else {}


def _query_calgary_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Calgary."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if CALGARY_APP_TOKEN:
        params["$$app_token"] = CALGARY_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{CALGARY_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Calgary {dataset_id}] Error: {e}")
    
    return results


# ── Montgomery County MD Functions ─────────────────────────────────────

def _montco_energy_lookup(address: str) -> dict:
    """Busca energy benchmarking de Montgomery County MD."""
    datasets = [
        "g6nn-rgwc",  # 2024 Energy Benchmarking All Sites
        "a2za-msqw",  # 2022 Energy Benchmarking All Sites
        "awze-8dwk",  # 2021 energy benchmarking all sites
        "izzs-2bn4",  # Building Energy Benchmarking Results
    ]
    results = _query_montco_datasets(datasets, address)
    return results[0] if results else {}


def _query_montco_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Montgomery County MD."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if MONTCO_APP_TOKEN:
        params["$$app_token"] = MONTCO_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{MONTCO_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[MontCo {dataset_id}] Error: {e}")
    
    return results


# ── Buffalo Functions ──────────────────────────────────────────────────

def _buffalo_violations_lookup(address: str) -> list:
    """Busca code violations de Buffalo."""
    datasets = [
        "ivrf-k9vm",  # Code Violations
        "abwd-pczc",  # Active Code Violations
        "kj78-h6e8",  # Lead Paint Code Violations
    ]
    return _query_buffalo_datasets(datasets, address)


def _query_buffalo_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Buffalo."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if BUFFALO_APP_TOKEN:
        params["$$app_token"] = BUFFALO_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{BUFFALO_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Buffalo {dataset_id}] Error: {e}")
    
    return results


# ── Providence Functions ───────────────────────────────────────────────

def _providence_tax_roll_lookup(address: str) -> dict:
    """Busca tax rolls de Providence."""
    # Usar el más reciente (2025)
    datasets = [
        "6ub4-iebe",  # 2025 Property Tax Roll
        "xvti-7dtw",  # 2024 Property Tax Roll
    ]
    results = _query_providence_datasets(datasets, address)
    return results[0] if results else {}


def _query_providence_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Providence."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if PROVIDENCE_APP_TOKEN:
        params["$$app_token"] = PROVIDENCE_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{PROVIDENCE_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Providence {dataset_id}] Error: {e}")
    
    return results


# ── Cambridge Functions ────────────────────────────────────────────────

def _cambridge_beudo_lookup(address: str) -> dict:
    """Busca BEUDO (Building Energy Use Disclosure Ordinance) de Cambridge."""
    datasets = [
        "72g6-j7aq",  # Building Energy Use Disclosure Ordinance (BEUDO)
        "w3yf-stdn",  # 2019 Cambridge BEUDO
    ]
    results = _query_cambridge_datasets(datasets, address)
    return results[0] if results else {}


def _query_cambridge_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Cambridge."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if CAMBRIDGE_APP_TOKEN:
        params["$$app_token"] = CAMBRIDGE_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{CAMBRIDGE_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Cambridge {dataset_id}] Error: {e}")
    
    return results


# ── Norfolk Functions ──────────────────────────────────────────────────

def _norfolk_violations_lookup(address: str) -> list:
    """Busca violations de Norfolk."""
    datasets = [
        "agip-sqwc",  # Violation Tracking System
    ]
    return _query_norfolk_datasets(datasets, address)


def _norfolk_assessments_lookup(address: str) -> dict:
    """Busca property assessments de Norfolk."""
    datasets = [
        "m5ya-5grb",  # Property Assessment and Sales - FY26
        "9gmp-9x4c",  # Property Assessment and Sales - FY24
    ]
    results = _query_norfolk_datasets(datasets, address)
    return results[0] if results else {}


def _query_norfolk_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Norfolk."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if NORFOLK_APP_TOKEN:
        params["$$app_token"] = NORFOLK_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{NORFOLK_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Norfolk {dataset_id}] Error: {e}")
    
    return results


# ── Kansas City Functions ──────────────────────────────────────────────

def _kc_dangerous_buildings_lookup(address: str) -> list:
    """Busca dangerous buildings de Kansas City."""
    datasets = [
        "ax3m-jhxx",  # Dangerous Buildings List
        "u8q5-qug6",  # Dangerous Buildings Demolished
        "843w-mn7j",  # Dangerous Buildings Scheduled for Demolition
    ]
    return _query_kc_datasets(datasets, address)


def _kc_energy_lookup(address: str) -> dict:
    """Busca energy benchmarking de Kansas City."""
    datasets = [
        "j5a7-mcmg",  # 2021 Kansas City Energy and Water Consumption Benchmarking
        "p8a5-sdg4",  # 2019 Kansas City Energy and Water Consumption Benchmarking
    ]
    results = _query_kc_datasets(datasets, address)
    return results[0] if results else {}


def _query_kc_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Kansas City."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if KCMO_APP_TOKEN:
        params["$$app_token"] = KCMO_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{KCMO_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[KC {dataset_id}] Error: {e}")
    
    return results


# ── Cincinnati Functions ───────────────────────────────────────────────

def _cincinnati_code_enforcement_lookup(address: str) -> list:
    """Busca code enforcement de Cincinnati."""
    datasets = [
        "cncm-znd6",  # Code Enforcement
        "pk9w-99n6",  # Private Lot Abatement Program
    ]
    return _query_cincinnati_datasets(datasets, address)


def _query_cincinnati_datasets(dataset_ids: list, address: str) -> list:
    """Función genérica para consultar datasets de Cincinnati."""
    results = []
    
    params = {
        "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
        "$limit": 10,
    }
    
    if CINCINNATI_APP_TOKEN:
        params["$$app_token"] = CINCINNATI_APP_TOKEN
    
    for dataset_id in dataset_ids:
        try:
            url = f"{CINCINNATI_API_BASE}/{dataset_id}.json"
            resp = requests.get(url, params=params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data[:5])
        except Exception as e:
            logger.debug(f"[Cincinnati {dataset_id}] Error: {e}")
    
    return results


def _search_open_data_permits(address: str, city: str, county: str = "") -> list:
    """Busca en portales de open data (Socrata, etc.)."""
    # Ciudades con APIs públicas de permisos - endpoints Socrata .json
    # Ver: https://dev.socrata.com/ para documentación de API
    
    city_key = city.lower()
    
    # Mapeo de ciudades a sus bases de API y dataset IDs
    # Permisos / demolición / desarrollo
    permit_datasets = {
        # ── NYC ────────────────────────────────────────────────
        "new york": {
            "base": NYC_API_BASE,
            "datasets": [
                "ipu4-2q9a",  # DOB Permit Issuance
                "rbx6-tga4",  # DOB NOW: Build – Approved Permits
                "g76y-dcqj",  # DOB After Hour Variance Permits
                "kfp4-dz4h",  # DOB NOW: Build Elevator Permit Applications
                "52dp-yji6",  # DOB NOW: Safety Boiler
            ],
            "token": NYC_APP_TOKEN,
        },
        # ── San Francisco ─────────────────────────────────────
        "san francisco": {
            "base": SF_API_BASE,
            "datasets": [
                "gzxm-jz5j",  # Building Permit Application Issuance Metrics
                "tyz3-vt28",  # PermitSF Permitting Data
                "b2xm-net3",  # Building Permits with Permit Contacts
                "87xy-gk8d",  # Building Permit Addenda with Routing
                "p4e4-a5a7",  # Building Permits filed on or after Jan 1, 2013
                "n644-pp3v",  # Permits Issued (Jan 2013–Current)
            ],
            "token": SF_APP_TOKEN,
        },
        # ── Chicago ───────────────────────────────────────────
        "chicago": {
            "base": CHICAGO_API_BASE,
            "datasets": [
                "e4xk-pud8",  # Demolition Permits (Chicago usa este endpoint)
            ],
            "token": CHICAGO_APP_TOKEN,
        },
        # ── Seattle ───────────────────────────────────────────
        "seattle": {
            "base": SEATTLE_API_BASE,
            "datasets": [
                "ht3q-kdvx",  # Land Use Permits
                "54j8-iz5t",  # Demo permits that are active
                "rs98-eyib",  # Residential Building Permits Issued and Final since 1990
            ],
            "token": SEATTLE_APP_TOKEN,
        },
        # ── Austin ────────────────────────────────────────────
        "austin": {
            "base": AUSTIN_API_BASE,
            "datasets": [
                "enku-zhee",  # Building Permits Issued in the last 30 days
                "e7se-4evh",  # Residential Building Permits Since 1980
                "3syk-w9eu",  # Issued Construction Permits
                "rifm-ftf3",  # Building Permits for US Census (High Valuation)
            ],
            "token": AUSTIN_APP_TOKEN,
        },
        # ── Los Angeles ───────────────────────────────────────
        "los angeles": {
            "base": LA_API_BASE,
            "datasets": [
                "hbkd-qubn",  # LADBS-Permits
                "xnhu-aczu",  # LA BUILD PERMITS
                "pi9x-tg5x",  # Building Permits Issued from 2020 to Present
            ],
            "token": LA_APP_TOKEN,
        },
        # ── Calgary ───────────────────────────────────────────
        "calgary": {
            "base": CALGARY_API_BASE,
            "datasets": [
                "kr8b-c44i",  # Building Permits by Community
            ],
            "token": CALGARY_APP_TOKEN,
        },
        # ── Montgomery County MD ──────────────────────────────
        "montgomery": {
            "base": MONTCO_API_BASE,
            "datasets": [
                "m88u-pqki",  # Residential Permit
                "i26v-w6bd",  # Commercial Permits
                "qxie-8qnp",  # Electrical Building Permits
                "m9e5-pvwj",  # ElectricalBuildingPermits-API
            ],
            "token": MONTCO_APP_TOKEN,
        },
        # ── Buffalo ───────────────────────────────────────────
        "buffalo": {
            "base": BUFFALO_API_BASE,
            "datasets": [
                "9p2d-f3yt",  # Permits
                "i3tg-pndu",  # Permits related to windows and paint
                "7f3h-uj5i",  # Permits in the Right of Way
            ],
            "token": BUFFALO_APP_TOKEN,
        },
        # ── Providence ────────────────────────────────────────
        "providence": {
            "base": PROVIDENCE_API_BASE,
            "datasets": [
                "ufmm-rbej",  # Department of Inspections and Standards Permits (2009–2018)
            ],
            "token": PROVIDENCE_APP_TOKEN,
        },
        # ── Cambridge ─────────────────────────────────────────
        "cambridge": {
            "base": CAMBRIDGE_API_BASE,
            "datasets": [
                "9qm7-wbdc",  # Building Permits: New Construction
                "qu2z-8suj",  # Building Permits: Addition / Alteration
                "kcfi-ackv",  # Demolition Permits
                "4bmb-xuad",  # Tent Building Permits
            ],
            "token": CAMBRIDGE_APP_TOKEN,
        },
        # ── Norfolk ───────────────────────────────────────────
        "norfolk": {
            "base": NORFOLK_API_BASE,
            "datasets": [
                "fahm-yuh4",  # Permits Issued
            ],
            "token": NORFOLK_APP_TOKEN,
        },
        # ── Kansas City ───────────────────────────────────────
        "kansas city": {
            "base": KCMO_API_BASE,
            "datasets": [
                "ntw8-aacc",  # Permits (CPD Dataset)
                "6h9j-mu65",  # CPD Permits - Status Change Dataset
            ],
            "token": KCMO_APP_TOKEN,
        },
        # ── Honolulu ──────────────────────────────────────────
        "honolulu": {
            "base": HONOLULU_API_BASE,
            "datasets": [
                "4vab-c87q",  # Building Permits - Jan 1, 2005 through Jun 30, 2025
                "ycwt-ujqt",  # 2016 Building Permits
                "3fr8-2hnx",  # 2010–2016 Building Permits
            ],
            "token": HONOLULU_APP_TOKEN,
        },
        # ── Cincinnati ────────────────────────────────────────
        "cincinnati": {
            "base": CINCINNATI_API_BASE,
            "datasets": [
                "uhjb-xac9",  # Cincinnati Building Permits
                "85pt-d6vq",  # Building Permits
            ],
            "token": CINCINNATI_APP_TOKEN,
        },
        # ── Oakland (fallback) ────────────────────────────────
        "oakland": {
            "base": "https://data.oaklandnet.com/resource",
            "datasets": ["2dqy-6z3g"],
            "token": "",
        },
        # ── San Jose ──────────────────────────────────────────
        "san jose": {
            "base": "https://data.sanjoseca.gov/resource",
            "datasets": ["t4nf-ywfu"],
            "token": "",
        },
    }
    
    city_config = permit_datasets.get(city_key)
    if not city_config:
        return []
    
    base_url = city_config["base"]
    datasets = city_config["datasets"]
    app_token = city_config.get("token", "")
    
    all_permits = []
    
    try:
        # Construir query parameters para Socrata API
        params = {
            "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%' OR LOWER(street_number) LIKE '%{address.split()[0].lower()}%'" if address else "",
            "$limit": 20,
            "$order": "issue_date DESC, filed_date DESC",
        }
        
        # Agregar token si está disponible
        if app_token:
            params["$$app_token"] = app_token
        
        # Iterar sobre los datasets de la ciudad
        for dataset_id in datasets:
            try:
                full_url = f"{base_url}/{dataset_id}.json"
                resp = requests.get(full_url, params=params, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        parsed = [_parse_permit_record(p, city) for p in data[:5]]
                        all_permits.extend(parsed)
                        # Si encontramos permisos, no necesitamos buscar más datasets
                        if len(all_permits) >= 5:
                            break
            except Exception as e:
                logger.debug(f"[Open Data Permits] Error en dataset {dataset_id}: {e}")
                continue
        
        # Retornar los primeros 10 permisos encontrados
        return all_permits[:10]
        
    except Exception as e:
        logger.debug(f"[Open Data Permits] Error general: {e}")
    
    return []


def _search_city_permit_portal(address: str, city: str, county: str = "") -> list:
    """Fallback para portales municipales de permisos."""
    # Implementación específica por ciudad
    # Algunos requieren autenticación o tienen APIs privadas
    
    # Búsqueda de demolitions específicas
    demolition_datasets = {
        "austin": {
            "base": AUSTIN_API_BASE,
            "datasets": [
                "x6mf-sksh",  # Residential Demolitions dataset
                "i3yb-4c5h",  # Single Family and Duplex Demolition Permits Issued in 2016
            ],
            "token": AUSTIN_APP_TOKEN,
        },
        "chicago": {
            "base": CHICAGO_API_BASE,
            "datasets": [
                "cgh9-n8rk",  # New Demo Permits
            ],
            "token": CHICAGO_APP_TOKEN,
        },
        "los angeles": {
            "base": LA_API_BASE,
            "datasets": [
                "fsgi-y87k",  # Demolition permit
                "8tb2-jn5y",  # Total Permits Issued LADBS 2013–2017 Demolitions
            ],
            "token": LA_APP_TOKEN,
        },
        "seattle": {
            "base": SEATTLE_API_BASE,
            "datasets": [
                "wk2i-qsrr",  # Active and in process Demo permits for non SFR
            ],
            "token": SEATTLE_APP_TOKEN,
        },
        "kansas city": {
            "base": KCMO_API_BASE,
            "datasets": [
                "u8q5-qug6",  # Dangerous Buildings Demolished
                "843w-mn7j",  # Dangerous Buildings Scheduled for Demolition
            ],
            "token": KCMO_APP_TOKEN,
        },
    }
    
    city_key = city.lower()
    demo_config = demolition_datasets.get(city_key)
    
    if not demo_config:
        return []
    
    base_url = demo_config["base"]
    datasets = demo_config["datasets"]
    app_token = demo_config.get("token", "")
    
    all_demolitions = []
    
    try:
        params = {
            "$where": f"LOWER(address) LIKE '%{address[:50].lower()}%'" if address else "",
            "$limit": 10,
        }
        
        if app_token:
            params["$$app_token"] = app_token
        
        for dataset_id in datasets:
            try:
                full_url = f"{base_url}/{dataset_id}.json"
                resp = requests.get(full_url, params=params, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        parsed = [_parse_permit_record(p, city, permit_type="Demolition") for p in data[:5]]
                        all_demolitions.extend(parsed)
            except Exception as e:
                logger.debug(f"[Demo Permits] Error en dataset {dataset_id}: {e}")
                continue
        
        return all_demolitions[:10]
        
    except Exception as e:
        logger.debug(f"[Demo Permits] Error general: {e}")
    
    return []


def _parse_permit_record(data: dict, city: str, permit_type: str = None) -> dict:
    """Parsea un registro de permiso a formato estandarizado."""
    # Determinar tipo de permiso
    ptype = permit_type or data.get("permit_type") or data.get("type", "") or data.get("work_type", "")
    
    # Normalizar fechas
    issue_date = (
        data.get("issue_date") or 
        data.get("filed_date") or 
        data.get("application_date") or 
        data.get("permit_date") or 
        ""
    )
    
    # Extraer valuación/proyecto cost
    valuation = (
        float(data.get("valuation") or 
              data.get("project_cost") or 
              data.get("estimated_cost") or 
              data.get("cost") or 0)
    )
    
    return {
        "permit_number": data.get("permit_number") or data.get("permit_id") or data.get("permitno", ""),
        "permit_type": ptype,
        "description": data.get("description") or data.get("work_description") or data.get("scope_of_work") or "",
        "status": data.get("status") or data.get("current_status") or data.get("permit_status") or "",
        "issue_date": issue_date,
        "expiration_date": data.get("expiration_date") or data.get("expires") or "",
        "completion_date": data.get("completion_date") or data.get("final_date") or data.get("closed_date") or "",
        "valuation": valuation,
        "contractor_name": data.get("contractor_name") or data.get("contractor") or "",
        "contractor_license": data.get("contractor_license") or data.get("license_number") or "",
        "address": data.get("address") or data.get("location") or "",
        "inspections": data.get("inspections", []),
        "source": f"{city} Building Dept",
    }


# ── 4. Secretary of State Business Search ─────────────────────────

def _sos_business_lookup(business_name: str) -> dict:
    """
    Busca información de registro empresarial en Secretary of State.
    
    California SOS: https://bizfileonline.sos.ca.gov/
    
    Datos disponibles:
        - Entity number
        - Entity type (Corporation, LLC, LP, etc.)
        - Registration date
        - Status (Active, Suspended, Dissolved)
        - Registered agent
        - Principal address
        - Officers/Directors
    """
    try:
        # California SOS API (si disponible)
        # Nota: CA SOS no tiene API pública oficial, pero hay servicios terceros
        
        sos_api_url = os.getenv("SOS_API_URL", "")
        
        if sos_api_url:
            resp = requests.get(
                sos_api_url,
                params={"entityName": business_name[:100], "limit": 3},
                timeout=10,
            )
            
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("entities", [])
                if results:
                    return _parse_sos_entity(results[0])
        
        # Fallback: estructura vacía
        logger.debug(f"[SOS] Sin datos para {business_name}")
        
    except Exception as e:
        logger.debug(f"[SOS] Error: {e}")
    
    return {}


def _parse_sos_entity(data: dict) -> dict:
    """Parsea entidad de Secretary of State."""
    return {
        "entity_number": data.get("entityNumber") or data.get("file_number", ""),
        "entity_type": data.get("entityType") or data.get("entity_kind", ""),
        "entity_name": data.get("entityName") or data.get("name", ""),
        "registration_date": data.get("registrationDate") or data.get("filed_date", ""),
        "status": data.get("status", ""),
        "jurisdiction": data.get("jurisdiction", "CA"),
        "registered_agent": data.get("registeredAgent", {}).get("name", ""),
        "principal_address": data.get("principalAddress", {}).get("street", ""),
        "principal_city": data.get("principalAddress", {}).get("city", ""),
        "principal_state": data.get("principalAddress", {}).get("state", "CA"),
        "officers": data.get("officers", []),
        "source": "Secretary of State",
    }


# ── 5. Census API ─────────────────────────────────────────────────

def _census_demographics_lookup(zip_code: str = "", city: str = "",
                                 county: str = "") -> dict:
    """
    Consulta Census API para datos demográficos del área.
    
    API: https://www.census.gov/data/developers/data-sets.html
    
    Datos disponibles:
        - Población total
        - Edad mediana
        - Ingreso familiar mediano
        - Nivel educativo
        - Tipo de vivienda (owner vs renter)
        - Valor mediano de propiedades
    """
    if not CENSUS_API_KEY:
        logger.debug("[Census] API key no configurada")
        return {}
    
    try:
        # Determinar geocode (FIPS codes)
        geo_id = _get_census_geo_id(zip_code, city, county)
        
        if not geo_id:
            # Fallback: usar ZIP Code Tabulation Area (ZCTA)
            if zip_code:
                geo_id = f"zip_code_tabulation_area:{zip_code}"
            else:
                return {}
        
        # Variables del Census ACS5 (American Community Survey)
        variables = [
            "B01002_001E",  # Total population
            "B19013_001E",  # Median household income
            "B25077_001E",  # Median home value
            "B25003_001E",  # Total housing units
            "B25003_002E",  # Owner occupied
            "B25003_003E",  # Renter occupied
            "B15003_022E",  # Bachelor's degree or higher
        ]
        
        vars_str = ",".join(variables)
        
        url = (
            f"https://api.census.gov/data/2022/acs/acs5/profile"
            f"?get={vars_str}&for={geo_id}&key={CENSUS_API_KEY}"
        )
        
        resp = requests.get(url, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            if len(data) > 1:
                return _parse_census_response(data[1], variables)
        
    except Exception as e:
        logger.debug(f"[Census] Error: {e}")
    
    return {}


def _get_census_geo_id(zip_code: str, city: str, county: str) -> str:
    """
    Obtiene el GEOID del Census para una ubicación.
    
    Formatos:
        - State: 06 (California)
        - County: 06001 (Alameda), 06075 (San Francisco)
        - Place (city): 0667000 (Oakland)
        - ZCTA: zip_code_tabulation_area:94601
    """
    # Mapeo simplificado de counties de CA
    ca_counties = {
        "alameda": "06001",
        "contra costa": "06013",
        "san francisco": "06075",
        "san mateo": "06081",
        "santa clara": "06085",
        "los angeles": "06037",
        "orange": "06059",
        "san diego": "06073",
        "solano": "06095",
        "marin": "06041",
        "napa": "06055",
        "sonoma": "06097",
    }
    
    county_key = (county or "").lower().replace(" county", "")
    county_fips = ca_counties.get(county_key, "")
    
    if county_fips:
        return f"county:{county_fips}"
    
    return ""


def _parse_census_response(row: list, variables: list) -> dict:
    """Parsea respuesta del Census."""
    def safe_int(val):
        try:
            return int(val) if val else 0
        except (ValueError, TypeError):
            return 0
    
    data = dict(zip(variables, row))
    
    total_housing = safe_int(data.get("B25003_001E", 0))
    owner_occupied = safe_int(data.get("B25003_002E", 0))
    renter_occupied = safe_int(data.get("B25003_003E", 0))
    
    owner_rate = (owner_occupied / total_housing * 100) if total_housing else 0
    
    return {
        "total_population": safe_int(data.get("B01002_001E", 0)),
        "median_household_income": safe_int(data.get("B19013_001E", 0)),
        "median_home_value": safe_int(data.get("B25077_001E", 0)),
        "total_housing_units": total_housing,
        "owner_occupied_rate": round(owner_rate, 1),
        "bachelor_or_higher": safe_int(data.get("B15003_022E", 0)),
        "source": "US Census ACS5",
    }


# ── Utilidades ────────────────────────────────────────────────────

def get_enrichment_stats() -> dict:
    """Retorna estadísticas del cache de enriquecimiento."""
    total = len(_enrichment_cache)
    with_license = sum(1 for v in _enrichment_cache.values() 
                       if v.get("license_info"))
    with_property = sum(1 for v in _enrichment_cache.values() 
                        if v.get("property_info"))
    
    return {
        "total_lookups": total,
        "with_license_info": with_license,
        "with_property_info": with_property,
        "cache_size_mb": len(str(_enrichment_cache)) / 1024 / 1024,
    }


def clear_cache():
    """Limpia el cache de enriquecimiento."""
    _enrichment_cache.clear()
    _cache_timestamps.clear()


# ── 6. FEMA Flood Zone (gratuito, sin key) ────────────────────────────

# Códigos de zona FEMA y su significado para roofing/waterproofing
_FEMA_ZONE_LABELS = {
    "A":   "Área inundable (100 años) — riesgo alto",
    "AE":  "Área inundable con BFE — riesgo alto",
    "AH":  "Inundación superficial — riesgo alto",
    "AO":  "Flujo superficial — riesgo alto",
    "AR":  "Área en restauración — riesgo medio",
    "A99": "Área con protección futura — riesgo alto",
    "V":   "Zona costera (olas) — riesgo muy alto",
    "VE":  "Zona costera con BFE — riesgo muy alto",
    "X":   "Zona de bajo riesgo (500 años)",
    "B":   "Riesgo moderado",
    "C":   "Riesgo mínimo",
    "D":   "Sin análisis disponible",
}

_FEMA_HIGH_RISK_ZONES = {"A", "AE", "AH", "AO", "AR", "A99", "V", "VE"}


def _fema_flood_zone_lookup(lat: float, lon: float) -> dict:
    """
    Consulta FEMA NFHL (National Flood Hazard Layer) para zona de inundación.

    API: ArcGIS REST Services del FEMA NFHL — completamente gratuita, sin key.
    URL: https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer

    Retorna: flood_zone, zone_description, sfha (Special Flood Hazard Area),
             is_high_risk (bool útil para scoring de leads de roofing)
    """
    try:
        resp = requests.get(
            "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query",
            params={
                "geometry":          f"{lon},{lat}",
                "geometryType":      "esriGeometryPoint",
                "inSR":              "4326",
                "spatialRel":        "esriSpatialRelIntersects",
                "outFields":         "FLD_ZONE,ZONE_SUBTY,SFHA_TF,STUDY_TYP",
                "returnGeometry":    "false",
                "returnCountOnly":   "false",
                "f":                 "json",
            },
            timeout=15,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return {}

        data = resp.json()
        features = data.get("features", [])
        if not features:
            return {}

        attrs    = features[0].get("attributes", {})
        zone     = (attrs.get("FLD_ZONE") or "").strip()
        subtype  = (attrs.get("ZONE_SUBTY") or "").strip()
        sfha     = attrs.get("SFHA_TF", "F")  # T = Special Flood Hazard Area
        study    = attrs.get("STUDY_TYP", "")

        if not zone:
            return {}

        zone_label = _FEMA_ZONE_LABELS.get(zone, f"Zona {zone}")
        is_sfha    = sfha == "T"
        is_high    = zone in _FEMA_HIGH_RISK_ZONES

        return {
            "flood_zone":     zone,
            "zone_subtype":   subtype,
            "zone_label":     zone_label,
            "sfha":           is_sfha,
            "is_high_risk":   is_high,
            "study_type":     study,
            "source":         "FEMA NFHL",
        }

    except Exception as e:
        logger.debug(f"[FEMA FloodZone] ({lat},{lon}): {e}")
        return {}


def format_enrichment_summary(enriched: dict) -> str:
    """
    Formatea resumen de enriquecimiento para mostrar en UI/Telegram.
    
    Ejemplo:
        📋 CSLB: Active (B-General) | 💰 Property: $850K | 🏗️ 3 permits (2023)
    """
    parts = []
    
    # License info
    license_info = enriched.get("license_info", {})
    if license_info:
        status = "✅" if license_info.get("is_active") else "⚠️"
        classification = license_info.get("classification_code", "")
        parts.append(f"{status} CSLB: {license_info.get('status', '')} ({classification})")
    
    # Property info
    property_info = enriched.get("property_info", {})
    if property_info:
        value = property_info.get("total_assessed_value", 0)
        if value:
            parts.append(f"💰 Property: ${value:,}")
    
    # Permits
    permits = enriched.get("permit_history", [])
    if permits:
        recent = [p for p in permits if p.get("issue_date", "") >= 
                  (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")]
        if recent:
            parts.append(f"🏗️ {len(recent)} permits (último año)")
    
    # Business info
    business_info = enriched.get("business_info", {})
    if business_info:
        status = business_info.get("status", "")
        entity_type = business_info.get("entity_type", "")
        parts.append(f"🏢 {entity_type} ({status})")
    
    # Demographics
    demographics = enriched.get("demographics", {})
    if demographics:
        income = demographics.get("median_household_income", 0)
        if income:
            parts.append(f"📊 Income: ${income:,}")

    # FEMA Flood Zone
    flood_zone = enriched.get("flood_zone", {})
    if flood_zone:
        zone = flood_zone.get("flood_zone", "")
        is_high = flood_zone.get("is_high_risk", False)
        emoji_fz = "🌊" if is_high else "✅"
        parts.append(f"{emoji_fz} Flood Zone: {zone}")

    return " | ".join(parts) if parts else "Sin datos de enriquecimiento"


# ── Demo / Testing ────────────────────────────────────────────────

if __name__ == "__main__":
    # Ejemplo de uso
    test_lead = {
        "contractor": "ABC Construction Inc",
        "contractor_license": "123456",
        "address": "123 Main Street",
        "city": "Oakland",
        "zip": "94601",
        "county": "Alameda",
        "owner": "John Smith",
    }
    
    print("Enriqueciendo lead...")
    result = enrich_lead(test_lead)
    
    print("\n=== Resultado ===")
    print(f"Fuentes: {', '.join(result.get('sources', []))}")
    print(f"Score: {result.get('enrichment_score')}/100")
    
    if result.get("license_info"):
        print("\n📋 CSLB:")
        for k, v in result["license_info"].items():
            print(f"  {k}: {v}")
    
    if result.get("property_info"):
        print("\n🏠 Property:")
        for k, v in result["property_info"].items():
            print(f"  {k}: {v}")
    
    if result.get("permit_history"):
        print(f"\n🏗️ Permits: {len(result['permit_history'])} encontrados")
    
    print("\n" + format_enrichment_summary(result))
