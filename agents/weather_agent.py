"""
agents/weather_agent.py
━━━━━━━━━━━━━━━━━━━━━━━
🌪️ Storm Damage Intelligence — priority US metros

Detecta reportes oficiales de clima severo (NOAA/SPC) en los mercados
prioritarios para vender zonas de impacto a General Contractors. Los leads son
zonas probablemente impactadas — no afirmaciones de daño confirmado por
propiedad individual — y se enriquecen con lat/lon, radio de impacto, servicios
recomendados y score para GC/restoration.

Fuentes gratuitas:
  - NOAA/SPC daily storm reports CSV (today + yesterday)
  - Open-Meteo forecast como fallback para lluvia fuerte en los mismos metros

MVP priority metros:
  1. Dallas–Fort Worth
  2. Houston
  3. San Antonio–Austin corridor
  4. Denver–Colorado Springs
  5. Oklahoma City–Tulsa
  6. Kansas City
  7. St. Louis
  8. Omaha–Lincoln
  9. Nashville–Memphis
 10. Atlanta / Charlotte
"""

from __future__ import annotations

import csv
import logging
import math
import os
from datetime import datetime, timezone
from io import StringIO
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agents.base import BaseAgent
from utils.lead_scoring import format_score_line, score_lead
from utils.telegram import send_lead

logger = logging.getLogger(__name__)

SPC_REPORT_URLS = [
    "https://www.spc.noaa.gov/climo/reports/today.csv",
    "https://www.spc.noaa.gov/climo/reports/yesterday.csv",
]
USER_AGENT = os.getenv("WEATHER_USER_AGENT", "0brix-weather-agent/1.0")
HTTP_TIMEOUT = int(os.getenv("WEATHER_HTTP_TIMEOUT", "20"))

# Thresholds for actionable storm-restoration opportunities.
MIN_HAIL_INCHES = float(os.getenv("WEATHER_MIN_HAIL_INCHES", "1.0"))
MIN_WIND_MPH = int(os.getenv("WEATHER_MIN_WIND_MPH", "58"))
RAIN_THRESHOLD_MM = float(os.getenv("WEATHER_RAIN_THRESHOLD_MM", "50"))

# Radius settings intentionally sell an impact zone, not a confirmed property.
BASE_RADIUS_MILES = float(os.getenv("WEATHER_BASE_RADIUS_MILES", "3"))
MAX_RADIUS_MILES = float(os.getenv("WEATHER_MAX_RADIUS_MILES", "15"))

# Top markets recommended for the 0brix WeatherAgent rollout.
PRIORITY_METROS = [
    {
        "key": "dfw",
        "name": "Dallas–Fort Worth",
        "city": "Dallas",
        "state": "TX",
        "lat": 32.7767,
        "lon": -96.7970,
        "radius_miles": 55,
        "priority": 1,
        "aliases": ["Dallas", "Fort Worth", "Arlington", "Plano", "Frisco", "McKinney"],
        "counties": ["Dallas", "Tarrant", "Collin", "Denton"],
    },
    {
        "key": "houston",
        "name": "Houston",
        "city": "Houston",
        "state": "TX",
        "lat": 29.7604,
        "lon": -95.3698,
        "radius_miles": 55,
        "priority": 1,
        "aliases": ["Houston", "Katy", "Cypress", "Spring", "The Woodlands", "Sugar Land"],
        "counties": ["Harris", "Fort Bend", "Montgomery"],
    },
    {
        "key": "satx_austin",
        "name": "San Antonio–Austin Corridor",
        "city": "San Antonio",
        "state": "TX",
        "lat": 29.4241,
        "lon": -98.4936,
        "radius_miles": 85,
        "priority": 1,
        "aliases": ["San Antonio", "Austin", "New Braunfels", "San Marcos", "Round Rock"],
        "counties": ["Bexar", "Travis", "Williamson", "Hays", "Comal"],
    },
    {
        "key": "denver_cos",
        "name": "Denver–Colorado Springs",
        "city": "Denver",
        "state": "CO",
        "lat": 39.7392,
        "lon": -104.9903,
        "radius_miles": 75,
        "priority": 1,
        "aliases": ["Denver", "Aurora", "Lakewood", "Colorado Springs"],
        "counties": ["Denver", "Adams", "Arapahoe", "Jefferson", "El Paso"],
    },
    {
        "key": "okc_tulsa",
        "name": "Oklahoma City–Tulsa",
        "city": "Oklahoma City",
        "state": "OK",
        "lat": 35.4676,
        "lon": -97.5164,
        "radius_miles": 95,
        "priority": 1,
        "aliases": ["Oklahoma City", "Edmond", "Norman", "Moore", "Tulsa", "Broken Arrow"],
        "counties": ["Oklahoma", "Cleveland", "Tulsa", "Canadian"],
    },
    {
        "key": "kansas_city",
        "name": "Kansas City",
        "city": "Kansas City",
        "state": "MO",
        "lat": 39.0997,
        "lon": -94.5786,
        "radius_miles": 45,
        "priority": 2,
        "aliases": ["Kansas City", "Overland Park", "Olathe", "Independence"],
        "counties": ["Jackson", "Clay", "Johnson", "Wyandotte"],
    },
    {
        "key": "st_louis",
        "name": "St. Louis",
        "city": "St. Louis",
        "state": "MO",
        "lat": 38.6270,
        "lon": -90.1994,
        "radius_miles": 45,
        "priority": 2,
        "aliases": ["St. Louis", "St Charles", "O'Fallon"],
        "counties": ["St. Louis", "St. Charles", "Jefferson"],
    },
    {
        "key": "omaha_lincoln",
        "name": "Omaha–Lincoln",
        "city": "Omaha",
        "state": "NE",
        "lat": 41.2565,
        "lon": -95.9345,
        "radius_miles": 60,
        "priority": 2,
        "aliases": ["Omaha", "Lincoln", "Council Bluffs"],
        "counties": ["Douglas", "Lancaster", "Sarpy", "Pottawattamie"],
    },
    {
        "key": "nashville_memphis",
        "name": "Nashville–Memphis",
        "city": "Nashville",
        "state": "TN",
        "lat": 36.1627,
        "lon": -86.7816,
        "radius_miles": 115,
        "priority": 2,
        "aliases": ["Nashville", "Memphis", "Murfreesboro", "Mount Juliet"],
        "counties": ["Davidson", "Shelby", "Rutherford", "Wilson"],
    },
    {
        "key": "atlanta",
        "name": "Atlanta",
        "city": "Atlanta",
        "state": "GA",
        "lat": 33.7490,
        "lon": -84.3880,
        "radius_miles": 60,
        "priority": 2,
        "aliases": ["Atlanta", "Marietta", "Roswell", "Sandy Springs"],
        "counties": ["Fulton", "Cobb", "DeKalb", "Gwinnett"],
    },
    {
        "key": "charlotte",
        "name": "Charlotte",
        "city": "Charlotte",
        "state": "NC",
        "lat": 35.2271,
        "lon": -80.8431,
        "radius_miles": 55,
        "priority": 2,
        "aliases": ["Charlotte", "Concord", "Gastonia", "Rock Hill"],
        "counties": ["Mecklenburg", "Cabarrus", "Gaston", "York"],
    },
]

_DAMAGE_KEYWORDS = (
    "damage",
    "roof",
    "siding",
    "shingle",
    "window",
    "tree",
    "home",
    "house",
    "building",
    "power poles",
    "structural",
    "flood",
)

_EVENT_LABELS = {
    "tornado": "🌪️ Tornado",
    "wind": "💨 Viento severo",
    "hail": "🧊 Granizo",
    "rain": "🌧️ Lluvia/flood risk",
}


def _http_get(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "ignore")


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _to_float(value, default=None):
    try:
        if value in (None, "", "UNK"):
            return default
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _nearest_priority_metro(lat: float, lon: float, state: str = "") -> tuple[dict, float] | tuple[None, None]:
    candidates = []
    for metro in PRIORITY_METROS:
        dist = _haversine_miles(lat, lon, metro["lat"], metro["lon"])
        if dist <= float(metro["radius_miles"]):
            # Prefer same-state metros when a corridor radius overlaps.
            state_penalty = 0 if not state or metro["state"] == state else 25
            candidates.append((dist + state_penalty, metro, dist))
    if not candidates:
        return None, None
    _, metro, dist = min(candidates, key=lambda item: item[0])
    return metro, dist


def _parse_spc_reports(csv_text: str, report_date_label: str) -> list[dict]:
    """Parse SPC's three-section CSV: tornado, wind and hail."""
    reports = []
    current_type = None
    for raw_row in csv.reader(StringIO(csv_text)):
        row = [cell.strip() for cell in raw_row]
        if not row or not any(row):
            continue
        if row[0] == "Time":
            if len(row) > 1 and row[1] == "F_Scale":
                current_type = "tornado"
            elif len(row) > 1 and row[1] == "Speed":
                current_type = "wind"
            elif len(row) > 1 and row[1] == "Size":
                current_type = "hail"
            else:
                current_type = None
            continue
        if not current_type or len(row) < 8:
            continue

        metric = row[1]
        lat = _to_float(row[5])
        lon = _to_float(row[6])
        if lat is None or lon is None:
            continue

        comments = row[7]
        reports.append(
            {
                "event_type": current_type,
                "time": row[0],
                "metric": metric,
                "location": row[2],
                "county": row[3],
                "state": row[4],
                "lat": lat,
                "lon": lon,
                "comments": comments,
                "report_date_label": report_date_label,
            }
        )
    return reports


def _is_actionable_report(report: dict) -> bool:
    event_type = report.get("event_type")
    metric = _to_float(report.get("metric"))
    comments = (report.get("comments") or "").lower()

    if event_type == "tornado":
        return True
    if event_type == "hail":
        return metric is not None and metric >= MIN_HAIL_INCHES
    if event_type == "wind":
        if metric is not None and metric >= MIN_WIND_MPH:
            return True
        return any(keyword in comments for keyword in _DAMAGE_KEYWORDS)
    return False


def _impact_radius_miles(report: dict) -> float:
    event_type = report.get("event_type")
    metric = _to_float(report.get("metric"), 0) or 0
    comments = (report.get("comments") or "").lower()

    radius = BASE_RADIUS_MILES
    if event_type == "tornado":
        radius = 8
    elif event_type == "hail":
        radius = 3 + max(0, metric - 1.0) * 2
    elif event_type == "wind":
        radius = 4
        if metric >= 70:
            radius += 3
        if any(keyword in comments for keyword in ("home", "roof", "siding", "trees down", "power poles")):
            radius += 3
    return round(min(radius, MAX_RADIUS_MILES), 1)


def _recommended_services(report: dict) -> list[str]:
    event_type = report.get("event_type")
    comments = (report.get("comments") or "").lower()
    services = ["roofing", "gutters", "siding"]
    if event_type == "tornado":
        services = ["roofing", "framing", "windows", "drywall", "paint", "debris removal"]
    elif event_type == "wind":
        services = ["roofing", "tree/debris removal", "siding", "windows", "fencing"]
    elif event_type == "hail":
        services = ["roofing", "gutters", "siding", "windows", "paint"]
    if any(word in comments for word in ("flood", "water", "rain")):
        services.extend(["drywall", "flooring", "mitigation"])
    # Stable order without duplicates.
    return list(dict.fromkeys(services))


def _severity(report: dict) -> str:
    event_type = report.get("event_type")
    metric = _to_float(report.get("metric"), 0) or 0
    comments = (report.get("comments") or "").lower()
    if event_type == "tornado":
        return "CRITICAL"
    if event_type == "hail" and metric >= 1.75:
        return "HIGH"
    if event_type == "wind" and (metric >= 70 or any(k in comments for k in ("home", "roof", "siding"))):
        return "HIGH"
    return "MEDIUM"


def build_spc_lead(report: dict, metro: dict) -> dict:
    event_type = report["event_type"]
    metric = report.get("metric") or "UNK"
    services = _recommended_services(report)
    radius = _impact_radius_miles(report)
    sev = _severity(report)
    event_label = _EVENT_LABELS.get(event_type, event_type.title())
    loc = report.get("location") or metro["city"]
    county = report.get("county") or ""
    state = report.get("state") or metro["state"]
    report_day = report.get("report_date_label") or "recent"

    metric_text = {
        "tornado": f"Escala {metric}",
        "wind": f"{metric} mph" if metric != "UNK" else "viento con daño reportado",
        "hail": f"{metric}\" hail",
    }.get(event_type, str(metric))

    lead_id = (
        f"weather_spc_{report_day}_{metro['key']}_{event_type}_"
        f"{report.get('time','')}_{str(loc).lower().replace(' ', '_').replace('/', '_')}"
    )
    description = (
        f"{event_label} — {metric_text} cerca de {loc}, {county} County, {state}. "
        f"Zona de impacto estimada {radius} mi. Servicios probables: {', '.join(services)}. "
        f"NOAA/SPC comments: {report.get('comments') or 'Sin comentario'}"
    )

    lead = {
        "id": lead_id,
        "title": f"{event_label} — {metro['name']} impact zone",
        "address": f"Impact zone near {loc}, {state}",
        "city": metro["city"],
        "county": county,
        "state": state,
        "lat": report["lat"],
        "lon": report["lon"],
        "description": description,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": "NOAA/SPC Storm Reports",
        "source_url": "https://www.spc.noaa.gov/climo/reports/",
        "lead_type": "weather_impact_zone",
        "event_type": event_type,
        "event_label": event_label,
        "event_metric": metric,
        "event_time": report.get("time"),
        "report_date_label": report_day,
        "affected_location": loc,
        "metro": metro["name"],
        "metro_priority": metro["priority"],
        "impact_radius_miles": radius,
        "recommended_services": services,
        "service_type": "roofing",
        "primary_service_type": "roofing",
        "_trade": "ROOFING",
        "_sub_trades": [s.upper() for s in services if s != "roofing"],
        "_urgency": "HIGH" if sev in ("HIGH", "CRITICAL") else "MEDIUM",
        "_project_scope": "EMERGENCY" if sev in ("HIGH", "CRITICAL") else "REPAIR",
        "_decision_maker": "GC",
        "_key_pain_point": "storm damage probability",
        "_competing_subs": 2 if metro["priority"] == 1 else 3,
        "_agent_key": "weather",
        "_weather_confidence": "probable_impact_zone",
        "_disclaimer": "Weather impact zone; property-level damage must be verified before outreach.",
    }

    scoring = score_lead(lead)
    severity_boost = {"CRITICAL": 15, "HIGH": 10, "MEDIUM": 5}.get(sev, 0)
    if metro["priority"] == 1:
        severity_boost += 5
    scoring["score"] = min(100, scoring.get("score", 50) + severity_boost)
    if scoring["score"] >= 90:
        scoring["grade"], scoring["grade_emoji"] = "HOT", "fire"
    elif scoring["score"] >= 70:
        scoring["grade"], scoring["grade_emoji"] = "WARM", "orange"
    lead["_scoring"] = scoring
    lead["severity"] = sev
    return lead


class WeatherAgent(BaseAgent):
    name = "🌪️ Storm Damage Intelligence"
    emoji = "🌪️"
    agent_key = "weather"

    def fetch_leads(self) -> list:
        zone_leads = self._fetch_spc_storm_reports()
        if not zone_leads and os.getenv("WEATHER_ENABLE_FORECAST_FALLBACK", "1").lower() not in ("0", "false", "no"):
            zone_leads = self._fetch_forecast_fallback()

        # Default: return enriched property candidates, not just broad zones.
        # Set WEATHER_ENRICH_PROPERTIES=0 to keep old impact-zone-only behavior.
        if os.getenv("WEATHER_ENRICH_PROPERTIES", "1").lower() not in ("0", "false", "no"):
            try:
                from utils.weather_property_enrichment import enrich_weather_zones

                leads = enrich_weather_zones(
                    zone_leads,
                    include_zone_leads=os.getenv("WEATHER_INCLUDE_ZONE_LEADS", "0").lower() in ("1", "true", "yes"),
                )
                if leads:
                    logger.info(
                        f"[Weather] {len(leads)} enriched property candidates from {len(zone_leads)} impact zones"
                    )
                    return leads
            except Exception as exc:
                logger.warning(f"[Weather] property enrichment failed; returning zone leads: {exc}")

        logger.info(f"[Weather] {len(zone_leads)} weather impact leads from priority metros")
        return zone_leads

    def _fetch_spc_storm_reports(self) -> list[dict]:
        leads = []
        seen_ids = set()
        for url in SPC_REPORT_URLS:
            label = "today" if "today" in url else "yesterday"
            try:
                reports = _parse_spc_reports(_http_get(url), label)
            except Exception as exc:
                logger.warning(f"[Weather/SPC] Could not fetch {url}: {exc}")
                continue

            for report in reports:
                if not _is_actionable_report(report):
                    continue
                metro, _dist = _nearest_priority_metro(report["lat"], report["lon"], report.get("state", ""))
                if not metro:
                    continue
                lead = build_spc_lead(report, metro)
                if lead["id"] not in seen_ids:
                    leads.append(lead)
                    seen_ids.add(lead["id"])
        return sorted(leads, key=lambda l: (l.get("metro_priority", 9), -l.get("_scoring", {}).get("score", 0)))

    def _fetch_forecast_fallback(self) -> list[dict]:
        """Fallback: heavy-rain/flood-risk alerts for top priority metros."""
        leads = []
        for metro in [m for m in PRIORITY_METROS if m["priority"] == 1]:
            try:
                params = urlencode(
                    {
                        "latitude": metro["lat"],
                        "longitude": metro["lon"],
                        "daily": "precipitation_sum,weathercode,precipitation_hours",
                        "forecast_days": 3,
                        "timezone": "auto",
                    }
                )
                data = __import__("json").loads(_http_get(f"https://api.open-meteo.com/v1/forecast?{params}"))
                daily = data.get("daily", {})
                dates = daily.get("time", [])
                precip = daily.get("precipitation_sum", [])
                if not dates:
                    continue
                rain_72h = sum(float(p or 0) for p in precip[:3])
                if rain_72h < RAIN_THRESHOLD_MM:
                    continue
                report = {
                    "event_type": "rain",
                    "time": "forecast",
                    "metric": f"{rain_72h:.0f}mm/72h",
                    "location": metro["city"],
                    "county": ", ".join(metro.get("counties", [])[:2]),
                    "state": metro["state"],
                    "lat": metro["lat"],
                    "lon": metro["lon"],
                    "comments": f"Open-Meteo forecast {rain_72h:.0f}mm precipitation in next 72h",
                    "report_date_label": dates[0],
                }
                lead = build_spc_lead(report, metro)
                lead["id"] = f"weather_forecast_{metro['key']}_{dates[0]}"
                lead["source"] = "Open-Meteo Forecast"
                lead["source_url"] = "https://open-meteo.com/"
                lead["recommended_services"] = ["roofing", "waterproofing", "gutters", "drywall", "flooring", "mitigation"]
                lead["description"] = (
                    f"🌧️ Heavy rain/flood risk — {rain_72h:.0f}mm forecast in 72h for {metro['name']}. "
                    "Likely GC/restoration opportunities: roof leaks, waterproofing, drywall, flooring and mitigation."
                )
                leads.append(lead)
            except Exception as exc:
                logger.debug(f"[Weather/Forecast/{metro['key']}] {exc}")
        return leads

    def notify(self, lead: dict):
        score_line = format_score_line(lead.get("_scoring", {"score": 0, "grade": "NA", "grade_emoji": "white", "reasons": []}))
        services = ", ".join(lead.get("recommended_services") or [])
        source_url = lead.get("source_url") or "https://www.spc.noaa.gov/climo/reports/"
        title = lead.get("title") or lead.get("description", "Weather impact zone")[:80]

        if lead.get("lead_type") == "weather_property_candidate":
            fields = {
                "🏠 Propiedad": lead.get("address"),
                "📍 Metro": lead.get("metro") or lead.get("city"),
                "📏 Distancia evento": f"{lead.get('distance_to_event_miles')} mi",
                "🏢 Tipo": f"{lead.get('property_kind')} / {lead.get('building_type')}",
                "⚠️ Evento": f"{lead.get('event_label')} — {lead.get('event_metric')}",
                "🛠️ Servicios GC": services,
                score_line: "",
                "📝 Nota": "Candidato dentro de zona probable; NO afirmar daño confirmado sin verificación.",
            }
        else:
            fields = {
                "📍 Metro": lead.get("metro") or lead.get("city"),
                "🧭 Zona": f"{lead.get('affected_location', lead.get('city'))} / {lead.get('county', '')} County",
                "📌 Radio estimado": f"{lead.get('impact_radius_miles', BASE_RADIUS_MILES)} mi",
                "⚠️ Evento": f"{lead.get('event_label')} — {lead.get('event_metric')}",
                "🛠️ Servicios GC": services,
                score_line: "",
                "📝 Nota": "Zona probablemente impactada; verificar daño por propiedad antes de contactar.",
            }

        send_lead(
            agent_name=self.name,
            emoji=self.emoji,
            title=title,
            fields=fields,
            url=source_url,
            cta=(
                "Prioridad para GCs/restoration: enriquecer/verificar con dueño, "
                "valor, roof age y señales de permits antes de outreach."
            ),
        )
