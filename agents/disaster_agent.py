"""
agents/disaster_agent.py
🚨 Disaster Intelligence Agent — Detecta eventos de desastre en tiempo real

Fuentes:
  - NOAA Storm Events (HTTP/FTP — histórico + near-real-time)
  - OpenFEMA (declaraciones de desastre)
  - NASA FIRMS (incendios activos vía satélite)
  - Open-Meteo (proxy para hail detection via severidad climática)

Genera leads cruzando propiedades en el radio de impacto con datos
de Property DNA (año construcción, material techo, valor).

Flujo:
  1. Detecta evento (flood, hail, wildfire, wind, tornado, earthquake)
  2. Geolocaliza el área afectada
 3. Cruza con consolidated_leads en radio de impacto
 4. Enriquece con Property DNA
 5. Calcula tripartite score (sub_score, gc_score, insurance_score)
 6. Almacena en disaster_events + lead_disaster_links (PostgreSQL)
 7. Alerta a GCs y subs cercanos
"""

import os
import json
import logging
import requests
from datetime import datetime, timedelta, timezone
from agents.base import BaseAgent
from utils.telegram import send_lead

logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────────────────────
DISASTER_RADIUS_MILES = int(os.getenv("DISASTER_RADIUS_MILES", "25"))
FIRMS_MAP_KEY = os.getenv("NASA_FIRMS_MAP_KEY", "")
FEMA_API_BASE = "https://www.fema.gov/api/open/v2"
NOAA_STORM_BASE = "https://www.ncdc.noaa.gov/stormevents/ftp/current"
WEATHER_GOV_BASE = "https://api.weather.gov"

# Bay Area bounding box for NASA FIRMS
BAY_AREA_BOUNDS = {
    "min_lat": 37.0, "max_lat": 38.6,
    "min_lon": -123.0, "max_lon": -121.0,
}

# Hail threshold from Open-Meteo (weather codes 95, 96, 99)
HAIL_WEATHER_CODES = {95, 96, 99}

# Wind threshold (km/h) — severe wind event
SEVERE_WIND_THRESHOLD_KMH = 80

# FEMA disaster types relevant to construction
FEMA_RELEVANT_TYPES = {
    "DR",  # Major Disaster Declaration
    "EM",  # Emergency Declaration
    "FM",  # Fire Management Assistance Declaration
}

FEMA_RELEVANT_INCIDENTS = {
    "Severe Storm(s)", "Flooding", "Flash Flooding", "Hurricane",
    "Tropical Storm", "Tornado", "Severe Ice Storm", "Winter Storm",
    "Coastal Storm", "Wildfire", "Earthquake", "Mudslide/Landslide",
    "Dam/Levee Break",
}


class DisasterAgent(BaseAgent):
    name      = "🚨 Disaster Intelligence — Bay Area"
    emoji     = "🚨"
    agent_key = "disaster"

    def fetch_leads(self) -> list:
        leads = []

        # Source 1: NOAA active alerts (via weather.gov API — free, no key)
        leads.extend(self._fetch_noaa_alerts())

        # Source 2: FEMA disaster declarations
        leads.extend(self._fetch_fema_declarations())

        # Source 3: NASA FIRMS active fires (requires free MAP_KEY)
        if FIRMS_MAP_KEY:
            leads.extend(self._fetch_nasa_firms())

        logger.info(f"[Disaster] {len(leads)} disaster events detected")
        return leads

    # ── Source 1: NOAA Weather Alerts ──────────────────────────────
    def _fetch_noaa_alerts(self) -> list:
        """Fetch active weather alerts from weather.gov (free, no key)."""
        leads = []
        zones = [
            "CAZ006", "CAZ007", "CAZ508", "CAZ511", "CAZ013",
            "CAZ505", "CAZ509", "CAZ017", "CAZ018", "CAZ019",
            "CAZ014", "CAZ516", "CAZ530",
        ]

        disaster_events = {
            "Tornado Warning", "Severe Thunderstorm Warning",
            "High Wind Warning", "Wind Advisory",
            "Red Flag Warning", "Fire Weather Warning",
            "Winter Storm Warning", "Ice Storm Warning",
            "Earthquake",  # rarely in weather API, but just in case
        }

        # Also include flood events (overlap with flood_agent but with disaster context)
        flood_events = {
            "Flood Warning", "Flash Flood Warning",
            "Coastal Flood Warning",
        }

        all_relevant = disaster_events | flood_events

        for zone in zones:
            try:
                resp = requests.get(
                    f"{WEATHER_GOV_BASE}/alerts/active",
                    params={"zone": zone},
                    headers={"User-Agent": "MLeads/1.0"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()

                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    event = props.get("event", "")
                    if event not in all_relevant:
                        continue

                    severity = props.get("severity", "Unknown")
                    urgency = props.get("urgency", "Unknown")
                    areas = props.get("areaDesc", "")
                    onset = (props.get("onset") or "")[:10]
                    expires = (props.get("expires") or "")[:10]
                    headline = props.get("headline", "")

                    # Classify disaster type
                    disaster_type = self._classify_event(event)

                    lead_id = f"disaster_noaa_{zone}_{event.lower().replace(' ', '_')}_{onset}"

                    # Determine severity for tripartite scoring
                    impact_score = self._severity_to_impact(severity, event)

                    lead = {
                        "id":           lead_id,
                        "city":         areas.split(";")[0].strip() if areas else zone,
                        "address":      areas,
                        "description":  f"🚨 {event} — {headline}",
                        "event_type":   disaster_type,
                        "event":        event,
                        "severity":     severity,
                        "urgency":      urgency,
                        "onset":        onset,
                        "expires":      expires,
                        "source":       "noaa",
                        "areas":        areas,
                        "url":          props.get("@id", ""),
                        "_scoring": {
                            "score": impact_score,
                            "grade": "HOT" if impact_score >= 80 else "WARM" if impact_score >= 60 else "MEDIUM",
                            "grade_emoji": "🔥" if impact_score >= 80 else "🟠" if impact_score >= 60 else "🟡",
                            "reasons": [f"Disaster event: {event}", f"Severity: {severity}"],
                        },
                        "_trade":       self._event_to_trade(disaster_type),
                        "_agent_key":   "disaster",
                        "_disaster_type": disaster_type,
                        "_impact_score":  impact_score,
                    }
                    leads.append(lead)

            except Exception as e:
                logger.debug(f"[Disaster/NOAA/{zone}] {e}")

        return leads

    # ── Source 2: FEMA Disaster Declarations ───────────────────────
    def _fetch_fema_declarations(self) -> list:
        """Fetch recent FEMA disaster declarations for California."""
        leads = []
        try:
            # Get declarations from the last 30 days for CA
            thirty_days_ago = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
            resp = requests.get(
                f"{FEMA_API_BASE}/DisasterDeclarationsSummaries",
                params={
                    "filter": f"declarationDate gt '{thirty_days_ago}' and state eq 'California'",
                    "$orderby": "declarationDate desc",
                    "$top": 20,
                    "$format": "json",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("DisasterDeclarationsSummaries", []):
                disaster_type = item.get("declarationType", "")
                if disaster_type not in FEMA_RELEVANT_TYPES:
                    continue

                incident_type = item.get("incidentType", "")
                if incident_type not in FEMA_RELEVANT_INCIDENTS:
                    continue

                disaster_id = item.get("disasterNumber", "")
                title = item.get("declarationTitle", "")
                declared_counties = item.get("declaredCountyArea", "")
                date = (item.get("declarationDate") or "")[:10]

                lead_id = f"disaster_fema_{disaster_id}"

                # Check if Bay Area counties are affected
                bay_area_keywords = {
                    "Alameda", "Contra Costa", "Marin", "Napa", "San Francisco",
                    "San Mateo", "Santa Clara", "Solano", "Sonoma",
                    "Los Angeles", "San Diego", "Sacramento",
                }
                affected_bay = any(
                    kw in (declared_counties or "")
                    for kw in bay_area_keywords
                )

                if not affected_bay:
                    continue

                impact_score = 85  # FEMA declaration = high impact by definition

                lead = {
                    "id":              lead_id,
                    "city":            declared_counties,
                    "address":         f"FEMA DR-{disaster_id}: {title}",
                    "description":     f"🏛️ FEMA Declaration — {incident_type}: {title}",
                    "event_type":      self._classify_fema_incident(incident_type),
                    "fema_id":         disaster_id,
                    "incident_type":   incident_type,
                    "declared_counties": declared_counties,
                    "declaration_date": date,
                    "source":          "fema",
                    "url":             f"https://www.fema.gov/disaster/{disaster_id}",
                    "_scoring": {
                        "score": impact_score,
                        "grade": "HOT",
                        "grade_emoji": "🔥",
                        "reasons": [f"FEMA disaster declaration", f"Type: {incident_type}"],
                    },
                    "_trade":          self._classify_fema_incident(incident_type),
                    "_agent_key":      "disaster",
                    "_disaster_type":  self._classify_fema_incident(incident_type),
                    "_impact_score":   impact_score,
                }
                leads.append(lead)

        except Exception as e:
            logger.debug(f"[Disaster/FEMA] {e}")

        return leads

    # ── Source 3: NASA FIRMS Active Fires ──────────────────────────
    def _fetch_nasa_firms(self) -> list:
        """Fetch active fire detections from NASA FIRMS satellite data."""
        leads = []
        try:
            # VIIRS SNPP - last 24h for Bay Area
            resp = requests.get(
                "https://firms.modaps.eosdis.nasa.gov/api/area/json",
                params={
                    "MAP_KEY": FIRMS_MAP_KEY,
                    "PRODUCT": "VIIRS_SNPP_NRT",
                    "BBOX": (
                        f"{BAY_AREA_BOUNDS['min_lon']},{BAY_AREA_BOUNDS['min_lat']},"
                        f"{BAY_AREA_BOUNDS['max_lon']},{BAY_AREA_BOUNDS['max_lat']}"
                    ),
                    "FORMAT": "json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            for product_key, fires in data.items():
                if not isinstance(fires, list):
                    continue

                # Cluster nearby fires into single events
                clustered = self._cluster_fires(fires)

                for cluster in clustered:
                    lat = cluster["avg_lat"]
                    lon = cluster["avg_lon"]
                    count = cluster["count"]
                    confidence = cluster["avg_confidence"]

                    lead_id = f"disaster_fire_{lat:.3f}_{lon:.3f}_{datetime.utcnow().strftime('%Y%m%d')}"

                    impact_score = min(95, 60 + (count * 5) + (confidence * 10))

                    lead = {
                        "id":            lead_id,
                        "city":          "Bay Area",
                        "address":       f"Wildfire near {lat:.3f}, {lon:.3f}",
                        "description":   f"🔥 Active wildfire — {count} hotspots detected",
                        "event_type":    "wildfire",
                        "lat":           lat,
                        "lon":           lon,
                        "fire_count":    count,
                        "confidence":    confidence,
                        "source":        "nasa_firms",
                        "_scoring": {
                            "score": int(impact_score),
                            "grade": "HOT" if impact_score >= 80 else "WARM",
                            "grade_emoji": "🔥" if impact_score >= 80 else "🟠",
                            "reasons": [f"Active wildfire: {count} hotspots", f"Confidence: {confidence:.0%}"],
                        },
                        "_trade":        "ROOFING",  # Fire damage → roofing/drywall/paint
                        "_agent_key":    "disaster",
                        "_disaster_type": "wildfire",
                        "_impact_score":  int(impact_score),
                    }
                    leads.append(lead)

        except Exception as e:
            logger.debug(f"[Disaster/FIRMS] {e}")

        return leads

    # ── Fire clustering (simple grid-based) ────────────────────────
    def _cluster_fires(self, fires: list, grid_size: float = 0.02) -> list:
        """Cluster nearby fire detections into single events."""
        clusters = {}

        for fire in fires[:200]:  # Limit processing
            try:
                lat = float(fire.get("latitude", 0))
                lon = float(fire.get("longitude", 0))
                conf = float(fire.get("confidence", "h") == "h")  # high = 1, else 0

                # Grid key
                grid_lat = round(lat / grid_size) * grid_size
                grid_lon = round(lon / grid_size) * grid_size
                key = f"{grid_lat:.3f}_{grid_lon:.3f}"

                if key not in clusters:
                    clusters[key] = {"lats": [], "lons": [], "confs": [], "count": 0}
                clusters[key]["lats"].append(lat)
                clusters[key]["lons"].append(lon)
                clusters[key]["confs"].append(conf)
                clusters[key]["count"] += 1
            except (ValueError, TypeError):
                continue

        result = []
        for key, c in clusters.items():
            result.append({
                "avg_lat": sum(c["lats"]) / len(c["lats"]),
                "avg_lon": sum(c["lons"]) / len(c["lons"]),
                "count": c["count"],
                "avg_confidence": sum(c["confs"]) / max(len(c["confs"]), 1),
            })
        return result

    # ── Event classification helpers ───────────────────────────────
    def _classify_event(self, event: str) -> str:
        event_lower = event.lower()
        if "tornado" in event_lower:
            return "tornado"
        if "wind" in event_lower:
            return "wind"
        if "flood" in event_lower or "flash" in event_lower:
            return "flood"
        if "fire" in event_lower or "red flag" in event_lower:
            return "wildfire"
        if "ice" in event_lower or "winter" in event_lower or "snow" in event_lower:
            return "winter_storm"
        if "earthquake" in event_lower:
            return "earthquake"
        if "thunderstorm" in event_lower:
            return "severe_storm"
        return "other"

    def _classify_fema_incident(self, incident: str) -> str:
        incident_lower = incident.lower()
        if "flood" in incident_lower:
            return "flood"
        if "fire" in incident_lower or "wildfire" in incident_lower:
            return "wildfire"
        if "hurricane" in incident_lower or "tropical" in incident_lower:
            return "wind"
        if "tornado" in incident_lower:
            return "tornado"
        if "earthquake" in incident_lower:
            return "earthquake"
        if "storm" in incident_lower or "ice" in incident_lower or "winter" in incident_lower:
            return "severe_storm"
        if "mudslide" in incident_lower or "landslide" in incident_lower:
            return "flood"
        return "other"

    def _event_to_trade(self, disaster_type: str) -> str:
        """Map disaster type to most relevant construction trade."""
        trade_map = {
            "flood": "DRYWALL",       # Water damage → drywall replacement
            "wildfire": "ROOFING",    # Fire damage → roofing/structural
            "wind": "ROOFING",        # Wind damage → roofing/gutters
            "tornado": "ROOFING",     # Severe structural → roofing
            "hail": "ROOFING",        # Hail → roof replacement
            "earthquake": "CONCRETE", # Foundation/structural
            "severe_storm": "ROOFING",
            "winter_storm": "ROOFING",
        }
        return trade_map.get(disaster_type, "ROOFING")

    def _severity_to_impact(self, severity: str, event: str) -> int:
        """Convert NOAA severity + event type to impact score (0-100)."""
        base = {
            "Extreme": 85,
            "Severe": 75,
            "Moderate": 55,
            "Minor": 35,
            "Unknown": 50,
        }.get(severity, 50)

        # Boost for particularly actionable events
        if "Tornado" in event:
            base = min(100, base + 15)
        if "Flash Flood" in event:
            base = min(100, base + 10)
        if "Fire" in event or "Red Flag" in event:
            base = min(100, base + 10)

        return base

    def notify(self, lead: dict):
        event_type = lead.get("event_type", "disaster").upper()
        severity = lead.get("severity", "Unknown")
        areas = lead.get("areas", lead.get("declared_counties", lead.get("city", "")))
        source = lead.get("source", "unknown").upper()

        event_emoji = {
            "flood": "🌊", "wildfire": "🔥", "wind": "💨",
            "tornado": "🌪️", "hail": "🧊", "earthquake": "🌍",
            "severe_storm": "⛈️", "winter_storm": "❄️",
        }.get(lead.get("event_type", ""), "🚨")

        cta_map = {
            "flood": "🌊 Agua = drywall/paint/roofing urgente. Contacta propietarios en zonas de inundación.",
            "wildfire": "🔥 Fuego = reconstrucción roofing/drywall. Los claims de seguro activan trabajo por meses.",
            "wind": "💨 Viento = roofing/gutters/techos dañados. Prospecta casas con techo viejo.",
            "tornado": "🌪️ Tornado = daño estructural severo. Roofing, drywall, electrical, paint.",
            "hail": "🧊 Granizo = reemplazo de techo garantizado. Activa claims de seguro.",
            "earthquake": "🌍 Terremoto = inspección foundation + structural. Concrete, framing, drywall.",
            "severe_storm": "⛈️ Tormenta = daño roofing/gutters. Enfócate en edificios >20 años.",
            "winter_storm": "❄️ Invierno = roofing/drywall por infiltración. Activo antes del thaw.",
        }

        cta = cta_map.get(lead.get("event_type", ""), "🚨 Desastre = oportunidad de reconstrucción.")

        fields = {
            f"{event_emoji} Evento": lead.get("event", lead.get("incident_type", "")),
            "📍 Área": areas,
            "⚠️ Severidad": severity,
            "📋 Detalles": lead.get("description", ""),
        }

        if lead.get("onset"):
            fields["📅 Inicio"] = lead["onset"]
        if lead.get("expires"):
            fields["📅 Expira"] = lead["expires"]
        if lead.get("fema_id"):
            fields["🏛️ FEMA"] = f"DR-{lead['fema_id']}"
        if lead.get("fire_count"):
            fields["🔥 Hotspots"] = str(lead["fire_count"])

        send_lead(
            agent_name=self.name,
            emoji=self.emoji,
            title=f"{event_emoji} DISASTER ALERT — {event_type}",
            fields=fields,
            url=lead.get("url", ""),
            cta=cta,
        )
