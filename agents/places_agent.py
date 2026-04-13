"""
agents/places_agent.py
━━━━━━━━━━━━━━━━━━━━━━
📍 Negocios de Construcción Activos — Bay Area

Fuentes (en orden de prioridad):
  1. Google Places API (Nearby Search) — $200/mes créditos gratuitos
     → Más completo: rating, teléfono, website, horarios
  2. OpenStreetMap Overpass API — COMPLETAMENTE GRATIS, sin key
     → Fallback cuando no hay Google API key configurado
     → Busca: craft=roofer/electrician/painter, office=contractor, shop=doityourself

Lógica: Negocios de construcción activos en Bay Area = potenciales
clientes o referidos para servicios de insulación.
"""

import os
import logging
import requests
from datetime import datetime

from agents.base import BaseAgent
from utils.telegram import send_lead
from utils.contacts_loader import load_all_contacts, lookup_contact
from utils.lead_scoring import score_lead, format_score_line

logger = logging.getLogger(__name__)

GOOGLE_PLACES_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")

# Coordenadas centrales de ciudades Bay Area + radio de búsqueda
_SEARCH_LOCATIONS = [
    {"city": "San Francisco", "lat": 37.7749, "lon": -122.4194, "radius": 8000},
    {"city": "Oakland",       "lat": 37.8044, "lon": -122.2712, "radius": 8000},
    {"city": "San Jose",      "lat": 37.3382, "lon": -121.8863, "radius": 10000},
    {"city": "Fremont",       "lat": 37.5485, "lon": -121.9886, "radius": 6000},
    {"city": "Berkeley",      "lat": 37.8716, "lon": -122.2727, "radius": 5000},
]

# Keywords de búsqueda para negocios relacionados
_SEARCH_KEYWORDS = [
    "general contractor",
    "home remodeling",
    "HVAC contractor",
    "roofing contractor",
    "construction company",
]


class PlacesAgent(BaseAgent):
    name      = "📍 Negocios Construcción — Bay Area"
    emoji     = "📍"
    agent_key = "places"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._contacts = load_all_contacts()

    def fetch_leads(self) -> list:
        if not GOOGLE_PLACES_KEY:
            logger.info("[Places] GOOGLE_PLACES_API_KEY no configurado — usando Overpass (OSM) gratuito")
            return self._fetch_overpass_leads()


        leads = []
        seen_ids = set()

        for location in _SEARCH_LOCATIONS:
            for keyword in _SEARCH_KEYWORDS:
                try:
                    results = self._search_nearby(
                        location["lat"], location["lon"],
                        location["radius"], keyword,
                    )
                    for place in results:
                        place_id = place.get("place_id", "")
                        if not place_id or place_id in seen_ids:
                            continue
                        seen_ids.add(place_id)

                        lead = self._place_to_lead(place, location["city"], keyword)
                        if lead:
                            leads.append(lead)

                except Exception as e:
                    logger.debug(f"[Places/{location['city']}/{keyword}] {e}")

        logger.info(f"[Places] {len(leads)} negocios encontrados en {len(_SEARCH_LOCATIONS)} ciudades")

        # Ordenar por rating (mejor primero)
        leads.sort(key=lambda l: l.get("rating", 0), reverse=True)
        return leads

    def _search_nearby(self, lat: float, lon: float,
                       radius: int, keyword: str) -> list:
        """Google Places Nearby Search."""
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={
                "location": f"{lat},{lon}",
                "radius": radius,
                "keyword": keyword,
                "type": "general_contractor",
                "key": GOOGLE_PLACES_KEY,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            return []

        data = resp.json()
        if data.get("status") != "OK":
            return []

        return data.get("results", [])

    def _place_to_lead(self, place: dict, city: str, keyword: str) -> dict | None:
        """Convierte un resultado de Google Places a lead."""
        name = place.get("name", "")
        address = place.get("vicinity", "") or place.get("formatted_address", "")
        place_id = place.get("place_id", "")
        rating = place.get("rating", 0)
        total_ratings = place.get("user_ratings_total", 0)

        if not name or not address:
            return None

        # Obtener detalles del negocio (teléfono, website)
        details = self._get_place_details(place_id)

        lead = {
            "id":             f"places_{place_id}",
            "city":           city,
            "address":        address,
            "business_name":  name,
            "description":    f"{keyword.title()} — {name}",
            "rating":         rating,
            "total_reviews":  total_ratings,
            "phone":          details.get("phone", ""),
            "website":        details.get("website", ""),
            "business_status": place.get("business_status", ""),
            "search_keyword": keyword,
            "_agent_key":     "places",
        }

        # Contacto via CSV
        match = lookup_contact(name, self._contacts)
        if match:
            lead["contact_phone"]  = match.get("phone", "")
            lead["contact_email"]  = match.get("email", "")
            lead["contact_source"] = f"CSV ({match['source']})"
        elif details.get("phone"):
            lead["contact_phone"] = details["phone"]
            lead["contact_source"] = "Google Places"

        return lead

    def _get_place_details(self, place_id: str) -> dict:
        """Google Places Details — obtiene teléfono y website."""
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/place/details/json",
                params={
                    "place_id": place_id,
                    "fields": "formatted_phone_number,website,opening_hours",
                    "key": GOOGLE_PLACES_KEY,
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return {}
            data = resp.json()
            result = data.get("result", {})
            return {
                "phone":   result.get("formatted_phone_number", ""),
                "website": result.get("website", ""),
            }
        except Exception:
            return {}

    # ── OpenStreetMap Overpass API (fallback gratuito) ────────────────

    def _fetch_overpass_leads(self) -> list:
        """
        OpenStreetMap Overpass API — búsqueda gratuita de contratistas.

        Sin API key requerida. Busca negocios de construcción en Bay Area
        usando tags de OpenStreetMap: craft, office, shop.
        """
        leads = []
        seen_ids = set()

        # Bounding box del Bay Area (sur-oeste, norte-este)
        # lat: 36.9 - 38.9, lon: -123.1 - -121.2
        bbox = "36.9,-123.1,38.9,-121.2"

        overpass_query = f"""
[out:json][timeout:30];
(
  node["craft"~"roofer|electrician|painter|carpenter|plumber|hvac"](bbox:{bbox});
  node["office"="contractor"](bbox:{bbox});
  node["office"="construction_company"](bbox:{bbox});
  node["shop"="doityourself"](bbox:{bbox});
  node["building"="construction"](bbox:{bbox});
  way["craft"~"roofer|electrician|painter|carpenter|plumber|hvac"](bbox:{bbox});
  way["office"="contractor"](bbox:{bbox});
);
out body center;
""".replace("bbox:", f"({bbox}),")

        # Formato correcto de Overpass QL
        overpass_query = f"""
[out:json][timeout:30];
(
  node["craft"~"roofer|electrician|painter|carpenter|plumber|hvac"]({bbox});
  node["office"~"contractor|construction_company"]({bbox});
  node["shop"="doityourself"]({bbox});
  way["craft"~"roofer|electrician|painter|carpenter|plumber|hvac"]({bbox});
  way["office"~"contractor|construction_company"]({bbox});
);
out body center;
"""
        try:
            resp = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": overpass_query},
                timeout=40,
                headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                logger.debug(f"[Places/Overpass] HTTP {resp.status_code}")
                return []

            data = resp.json()
            elements = data.get("elements", [])

            for elem in elements:
                tags = elem.get("tags", {})
                name = tags.get("name", "")
                if not name:
                    continue

                elem_id = f"osm_{elem.get('type', 'node')}_{elem.get('id', '')}"
                if elem_id in seen_ids:
                    continue
                seen_ids.add(elem_id)

                # Coordenadas (nodo directo o centroide de way)
                lat = elem.get("lat") or elem.get("center", {}).get("lat")
                lon = elem.get("lon") or elem.get("center", {}).get("lon")

                # Construir dirección desde tags
                street = tags.get("addr:street", "")
                housen = tags.get("addr:housenumber", "")
                city   = tags.get("addr:city", "")
                postc  = tags.get("addr:postcode", "")
                address = " ".join(filter(None, [housen, street])) or "Bay Area"
                if city:
                    address = f"{address}, {city}"

                craft  = tags.get("craft", "")
                office = tags.get("office", "")
                trade_label = (craft or office or "contractor").replace("_", " ").title()

                phone   = tags.get("phone", "") or tags.get("contact:phone", "")
                website = tags.get("website", "") or tags.get("contact:website", "")
                email   = tags.get("email", "") or tags.get("contact:email", "")

                # Determinar trade
                trade_map = {
                    "roofer": "ROOFING",
                    "electrician": "ELECTRICAL",
                    "painter": "PAINTING",
                    "plumber": "PLUMBING",
                    "carpenter": "FRAMING",
                    "hvac": "HVAC",
                }
                trade = trade_map.get(craft.lower(), "GENERAL")

                score_data = score_lead(
                    project_value=0,
                    source_type="places",
                    days_ago=0,
                    service_type=trade,
                )

                lead = {
                    "id":             elem_id,
                    "city":           city or "Bay Area",
                    "address":        address,
                    "business_name":  name,
                    "description":    f"{trade_label} — {name}",
                    "phone":          phone,
                    "website":        website,
                    "contact_email":  email,
                    "contact_phone":  phone,
                    "contact_source": "OpenStreetMap",
                    "search_keyword": craft or office,
                    "lat":            lat,
                    "lon":            lon,
                    "_scoring":       score_data,
                    "_trade":         trade,
                    "_agent_key":     "places",
                    "source":         "Overpass/OSM",
                }

                # Lookup en CSV local
                match = lookup_contact(name, self._contacts)
                if match:
                    lead["contact_phone"]  = match.get("phone", "") or phone
                    lead["contact_email"]  = match.get("email", "") or email
                    lead["contact_source"] = f"CSV ({match['source']})"

                leads.append(lead)

            logger.info(f"[Places/Overpass] {len(leads)} contratistas encontrados en Bay Area (OSM)")

        except Exception as e:
            logger.warning(f"[Places/Overpass] Error: {e}")

        return leads

    def notify(self, lead: dict):
        rating = lead.get("rating", 0)
        reviews = lead.get("total_reviews", 0)

        star_str = "⭐" * int(rating) if rating else "Sin rating"

        fields = {
            "📍 Ciudad":       lead.get("city"),
            "🏢 Negocio":     lead.get("business_name"),
            "🔍 Categoría":   lead.get("search_keyword", "").title(),
            "⭐ Rating":       f"{rating}/5 ({reviews} reviews)" if rating else "—",
        }

        if lead.get("contact_phone"):
            src = lead.get("contact_source", "")
            fields["📞 Teléfono"] = (
                f"{lead['contact_phone']}  _(via {src})_" if src
                else lead["contact_phone"]
            )

        if lead.get("contact_email"):
            fields["✉️  Email"] = lead["contact_email"]

        if lead.get("website"):
            fields["🌐 Website"] = lead["website"]

        send_lead(
            agent_name=self.name, emoji=self.emoji,
            title=f"{lead['city']} — {lead['business_name']}",
            fields=fields,
            cta="📍 Contratista activo = potencial cliente o referido para insulación.",
        )
