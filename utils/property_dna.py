"""
utils/property_dna.py
🏠 Property DNA — Enriquecimiento de propiedades con datos de assessor

Extrae y cachea datos clave de propiedades:
  - Año de construcción
  - Material del techo
  - Valor de la propiedad
  - Sqft
  - # de unidades
  - Zona de flood (FEMA)
  - Tipo de estructura

Fuentes (todas gratuitas o free tier):
  - County Assessor APIs (Alameda, Santa Clara, SF, LA)
  - Census ACS (demographics por ZIP)
  - FEMA NFHL (flood zone data)
  - Google Solar API (roof condition proxy)
  - Socrata (existing permit data)

Los datos se almacenan en consolidated_leads (columnas Property DNA)
y en cache local (property_dna_cache table) para evitar re-fetches.
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache duration (days) ──────────────────────────────────────────────────
CACHE_DAYS = int(os.getenv("PROPERTY_DNA_CACHE_DAYS", "90"))

# ── County Assessor endpoints ──────────────────────────────────────────────
# These are Socrata-based open data portals with property/assessor data

ASSESSOR_SOURCES = {
    "Alameda": {
        "base_url": "https://data.acgov.org/resource",
        "dataset": "px3c-4v6i",  # Assessor parcel data
        "address_field": "situs",
        "format": "json",
    },
    "Santa Clara": {
        "base_url": "https://data.sccgov.org/resource",
        "dataset": "igwb-g4r6",  # Assessor's Office parcel data
        "address_field": "situs_address",
        "format": "json",
    },
    "San Francisco": {
        "base_url": "https://data.sfgov.org/resource",
        "dataset": "vw6y-z8j6",  # Assessor Historical Secured Property Tax Rolls
        "address_field": "property_location",
        "format": "json",
    },
    "Los Angeles": {
        "base_url": "https://data.lacounty.gov/resource",
        "dataset": "9trm-hzv6",  # Assessor parcel data
        "address_field": "situsaddress",
        "format": "json",
    },
}


class PropertyDNA:
    """Enrich leads with property-level data from public assessors."""

    def __init__(self):
        self.socrata_token = os.getenv("SOCRATA_APP_TOKEN", "")
        self.session = requests.Session()
        if self.socrata_token:
            self.session.headers["X-App-Token"] = self.socrata_token
        self.session.headers["User-Agent"] = "MLeads/1.0"

    def enrich_lead(self, lead: dict) -> dict:
        """
        Enrich a single lead with Property DNA.
        Returns the lead dict with added property_dna fields.
        """
        address = lead.get("address", "")
        city = lead.get("city", "")

        if not address or not city:
            return lead

        # Check cache first
        cached = self._get_cached(address, city)
        if cached:
            lead.update(cached)
            lead["_property_dna_source"] = "cache"
            return lead

        # Determine county
        county = self._city_to_county(city)
        if county not in ASSESSOR_SOURCES:
            # Fallback: estimate from lead data
            self._estimate_property_data(lead)
            lead["_property_dna_source"] = "estimated"
            return lead

        # Fetch from assessor API
        property_data = self._fetch_assessor_data(address, county)
        if property_data:
            lead.update(property_data)
            lead["_property_dna_source"] = "assessor"
            self._cache(address, city, property_data)
        else:
            self._estimate_property_data(lead)
            lead["_property_dna_source"] = "estimated"

        return lead

    def enrich_batch(self, leads: list, batch_size: int = 20) -> int:
        """Enrich a batch of leads. Returns count of enriched leads."""
        enriched = 0
        for lead in leads[:batch_size]:
            try:
                self.enrich_lead(lead)
                enriched += 1
            except Exception as e:
                logger.debug(f"[PropertyDNA] Error enriching lead: {e}")
        return enriched

    def _fetch_assessor_data(self, address: str, county: str) -> Optional[dict]:
        """Fetch property data from county assessor API."""
        source = ASSESSOR_SOURCES.get(county)
        if not source:
            return None

        try:
            # Clean address for search
            search_addr = address.split(",")[0].strip().upper()

            url = f"{source['base_url']}/{source['dataset']}.json"
            resp = self.session.get(
                url,
                params={
                    "$where": f"starts_with({source['address_field']}, '{search_addr[:20]}')",
                    "$limit": 5,
                },
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json()

            if not results:
                return None

            # Pick best match (first result for now)
            best = results[0]

            return self._parse_assessor_record(best, county)

        except Exception as e:
            logger.debug(f"[PropertyDNA/Assessor/{county}] {e}")
            return None

    def _parse_assessor_record(self, record: dict, county: str) -> dict:
        """Parse an assessor record into standardized property DNA fields."""
        data = {}

        # Common fields across counties (field names vary)
        year_field_variants = ["year_built", "yrbuilt", "year_built1", "construction_year", "yearbuilt"]
        value_field_variants = ["total_value", "totalvalue", "roll_totalvalue", "assessed_value", "assessedvalue"]
        sqft_field_variants = ["sqft", "living_sqft", "gross_sqft", "buildingsqft", "lot_sqft"]
        units_field_variants = ["units", "number_of_units", "unitsnumber"]
        use_field_variants = ["use_code", "usecode", "property_use", "use_description"]
        roof_field_variants = ["roof_type", "roof_material", "roof_material_description"]

        def find_field(variants, default=None):
            for v in variants:
                if v in record:
                    return record[v]
            return default

        # Year built
        year_str = find_field(year_field_variants)
        if year_str:
            try:
                data["property_year_built"] = int(str(year_str)[:4])
            except (ValueError, TypeError):
                pass

        # Property value
        value_str = find_field(value_field_variants)
        if value_str:
            try:
                data["property_value"] = float(str(value_str).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                pass

        # Square footage
        sqft_str = find_field(sqft_field_variants)
        if sqft_str:
            try:
                data["property_sqft"] = int(float(str(sqft_str).replace(",", "")))
            except (ValueError, TypeError):
                pass

        # Units
        units_str = find_field(units_field_variants)
        if units_str:
            try:
                data["property_units"] = int(float(str(units_str)))
            except (ValueError, TypeError):
                pass

        # Use code / property type
        use_code = find_field(use_field_variants)
        if use_code:
            data["property_use_code"] = str(use_code)
            # Infer roof material from use code + year
            data["property_roof_material"] = self._infer_roof_material(
                data.get("property_year_built"),
                str(use_code)
            )

        # Roof type (if explicitly available)
        roof_type = find_field(roof_field_variants)
        if roof_type:
            data["property_roof_material"] = str(roof_type)

        # Flood zone (from FEMA NFHL — separate call)
        # We do this lazily, only if lat/lon available
        if "latitude" in record or "lat" in record:
            try:
                lat = float(record.get("latitude", record.get("lat", 0)))
                lon = float(record.get("longitude", record.get("lon", 0)))
                if lat and lon:
                    data["lat"] = lat
                    data["lon"] = lon
                    flood_zone = self._check_flood_zone(lat, lon)
                    if flood_zone:
                        data["flood_zone"] = flood_zone
            except (ValueError, TypeError):
                pass

        return data

    def _infer_roof_material(self, year_built: Optional[int], use_code: str) -> str:
        """Infer roof material from year built and use code.
        
        Bay Area patterns:
        - Pre-1960: wood shake or tar & gravel (flat)
        - 1960-1985: composition shingle or tar & gravel
        - 1985-2010: composition shingle or concrete tile
        - 2010+: concrete tile, composition shingle, or solar-ready
        
        Commercial flat roofs: tar & gravel, TPO, modified bitumen
        """
        if not year_built:
            return "unknown"

        use_lower = use_code.lower()
        is_commercial = any(kw in use_lower for kw in ["commercial", "industrial", "retail", "office"])

        if is_commercial:
            return "flat_commercial"  # TPO, modified bitumen, tar & gravel

        if year_built < 1960:
            return "wood_shake_or_tar_gravel"
        elif year_built < 1985:
            return "composition_shingle"
        elif year_built < 2010:
            return "composition_shingle_or_tile"
        else:
            return "tile_or_composition"

    def _check_flood_zone(self, lat: float, lon: float) -> Optional[str]:
        """Check FEMA flood zone for a property.
        
        Uses the FEMA NFHL (National Flood Hazard Layer) API.
        Free, no key required.
        """
        try:
            resp = self.session.get(
                "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/0/query",
                params={
                    "where": "1=1",
                    "geometry": f"{lon},{lat}",
                    "geometryType": "esriGeometryPoint",
                    "inSR": "4326",
                    "spatialRel": "esriSpatialRelIntersects",
                    "outFields": "FLD_ZONE,ZONE_SUBTY",
                    "returnGeometry": "false",
                    "f": "json",
                    "resultRecordCount": 1,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            features = data.get("features", [])
            if features:
                attrs = features[0].get("attributes", {})
                zone = attrs.get("FLD_ZONE", "")
                subtype = attrs.get("ZONE_SUBTY", "")
                if zone:
                    return f"{zone} ({subtype})" if subtype else zone

        except Exception as e:
            logger.debug(f"[PropertyDNA/FEMA] {e}")

        return None

    def _estimate_property_data(self, lead: dict):
        """Estimate property data from lead context when assessor data unavailable.
        
        Uses permit type, value, and city demographics as proxies.
        """
        # Estimate year built from city demographics
        city = (lead.get("city") or "").lower()
        year_estimates = {
            "san francisco": 1940, "oakland": 1950, "berkeley": 1945,
            "richmond": 1960, "alameda": 1950, "san leandro": 1960,
            "hayward": 1970, "fremont": 1980, "san jose": 1975,
            "sunnyvale": 1975, "santa clara": 1975, "palo alto": 1960,
            "mountain view": 1970, "concord": 1975, "walnut creek": 1970,
            "vallejo": 1965, "fairfield": 1980, "napa": 1965,
        }
        estimated_year = year_estimates.get(city, 1970)
        lead["property_year_built"] = estimated_year
        lead["property_year_built_estimated"] = True

        # Estimate roof material from estimated year
        lead["property_roof_material"] = self._infer_roof_material(estimated_year, "residential")
        lead["property_roof_material_estimated"] = True

        # Use permit value as proxy for property value if available
        value = lead.get("value_float", 0)
        if value and value > 0:
            # Permit value is typically 10-20% of total property value
            lead["property_value"] = value * 7  # rough multiplier
            lead["property_value_estimated"] = True

    def _city_to_county(self, city: str) -> str:
        """Map city name to county."""
        city_lower = city.lower().strip()
        city_county = {
            # Alameda County
            "oakland": "Alameda", "berkeley": "Alameda", "fremont": "Alameda",
            "hayward": "Alameda", "dublin": "Alameda", "alameda": "Alameda",
            "san leandro": "Alameda", "pleasanton": "Alameda", "livermore": "Alameda",
            "newark": "Alameda", "castro valley": "Alameda", "san lorenzo": "Alameda",
            "emeryville": "Alameda", "albany": "Alameda", "union city": "Alameda",
            "piedmont": "Alameda",
            # Contra Costa County
            "concord": "Contra Costa", "walnut creek": "Contra Costa",
            "martinez": "Contra Costa", "clayton": "Contra Costa",
            "pittsburg": "Contra Costa", "lafayette": "Contra Costa",
            "orinda": "Contra Costa", "antioch": "Contra Costa",
            "moraga": "Contra Costa", "alamo": "Contra Costa",
            "danville": "Contra Costa", "hercules": "Contra Costa",
            "pinole": "Contra Costa", "oakley": "Contra Costa",
            "san ramon": "Contra Costa", "richmond": "Contra Costa",
            "brentwood": "Contra Costa", "el cerrito": "Contra Costa",
            "pleasant hill": "Contra Costa",
            # San Mateo County
            "daly city": "San Mateo", "south san francisco": "San Mateo",
            "san bruno": "San Mateo", "millbrae": "San Mateo",
            "burlingame": "San Mateo", "san mateo": "San Mateo",
            "redwood city": "San Mateo", "menlo park": "San Mateo",
            "pacifics": "San Mateo", "east palo alto": "San Mateo",
            "hillsborough": "San Mateo",
            # Santa Clara County
            "san jose": "Santa Clara", "sunnyvale": "Santa Clara",
            "santa clara": "Santa Clara", "palo alto": "Santa Clara",
            "mountain view": "Santa Clara", "los altos": "Santa Clara",
            "los gatos": "Santa Clara", "saratoga": "Santa Clara",
            "cupertino": "Santa Clara", "milpitas": "Santa Clara",
            "campbell": "Santa Clara", "morgan hill": "Santa Clara",
            "gilroy": "Santa Clara",
            # Other counties
            "san francisco": "San Francisco",
            "napa": "Napa", "vallejo": "Solano", "fairfield": "Solano",
            "benicia": "Solano", "vacaville": "Solano", "suisun city": "Solano",
            "rio vista": "Solano",
            "san rafael": "Marin", "novato": "Marin",
            "petaluma": "Sonoma", "sonoma": "Sonoma", "santa rosa": "Sonoma",
            "tracy": "San Joaquin", "stockton": "San Joaquin",
            # Major US cities
            "los angeles": "Los Angeles", "pasadena": "Los Angeles",
            "long beach": "Los Angeles", "sacramento": "Sacramento",
            "san diego": "San Diego",
        }
        return city_county.get(city_lower, "")

    # ── Cache management ───────────────────────────────────────────

    def _get_cached(self, address: str, city: str) -> Optional[dict]:
        """Get cached property data if available and not expired."""
        try:
            import sqlite3
            conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            cutoff = (datetime.utcnow() - timedelta(days=CACHE_DAYS)).isoformat()
            c.execute("""
                SELECT property_data FROM property_dna_cache
                WHERE address = ? AND city = ? AND cached_at > ?
            """, (address, city, cutoff))

            row = c.fetchone()
            conn.close()

            if row:
                return json.loads(row["property_data"])
        except Exception:
            pass

        return None

    def _cache(self, address: str, city: str, data: dict):
        """Cache property data for future lookups."""
        try:
            import sqlite3
            conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            c = conn.cursor()

            # Create cache table if not exists
            c.execute("""
                CREATE TABLE IF NOT EXISTS property_dna_cache (
                    address TEXT,
                    city TEXT,
                    property_data TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    PRIMARY KEY (address, city)
                )
            """)

            c.execute("""
                INSERT OR REPLACE INTO property_dna_cache (address, city, property_data, cached_at)
                VALUES (?, ?, ?, ?)
            """, (address, city, json.dumps(data, default=str), datetime.utcnow().isoformat()))

            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[PropertyDNA/Cache] {e}")

    def _cache_pg(self, address: str, city: str, data: dict):
        """Cache property data in PostgreSQL (when USE_POSTGRES=1)."""
        if not os.getenv("USE_POSTGRES", "").lower() in ("1", "true"):
            self._cache(address, city, data)
            return

        try:
            from db_postgres import get_conn, put_conn
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO property_dna_cache (address, city, property_data, cached_at)
                    VALUES (%s, %s, %s, NOW())
                    ON CONFLICT (address, city) DO UPDATE
                    SET property_data = EXCLUDED.property_data, cached_at = NOW()
                """, (address, city, json.dumps(data, default=str)))
            conn.commit()
            put_conn(conn)
        except Exception as e:
            logger.debug(f"[PropertyDNA/Cache/PG] {e}")
            self._cache(address, city, data)  # fallback to SQLite


# Singleton
_dna_instance: Optional[PropertyDNA] = None


def get_property_dna() -> PropertyDNA:
    global _dna_instance
    if _dna_instance is None:
        _dna_instance = PropertyDNA()
    return _dna_instance


def enrich_lead_with_property_dna(lead: dict) -> dict:
    """Convenience function to enrich a single lead."""
    return get_property_dna().enrich_lead(lead)
