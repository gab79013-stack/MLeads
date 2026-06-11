"""
utils/weather_property_enrichment.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Weather impact-zone → property-candidate enrichment.

This module turns a NOAA/SPC weather impact-zone lead into property-level
*candidates* for GCs/restoration buyers. It intentionally does not claim damage
on a specific property; every output keeps a clear verification disclaimer.

Free data source for MVP:
  - OpenStreetMap Overpass API for nearby buildings/addresses.

Later upgrades can add county assessor/parcel APIs for owner, mailing address,
year built, assessed value and roof metadata.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

OVERPASS_URL = os.getenv("WEATHER_OVERPASS_URL", "https://overpass-api.de/api/interpreter")
OVERPASS_URLS = [
    url.strip()
    for url in os.getenv(
        "WEATHER_OVERPASS_URLS",
        f"{OVERPASS_URL},https://overpass.kumi.systems/api/interpreter,https://overpass.openstreetmap.ru/api/interpreter",
    ).split(",")
    if url.strip()
]
USER_AGENT = os.getenv("WEATHER_USER_AGENT", "0brix-weather-agent/1.0")
HTTP_TIMEOUT = int(os.getenv("WEATHER_ENRICH_HTTP_TIMEOUT", "25"))
MAX_RADIUS_METERS = int(os.getenv("WEATHER_ENRICH_MAX_RADIUS_METERS", "12000"))
DEFAULT_PER_ZONE_LIMIT = int(os.getenv("WEATHER_ENRICH_PER_ZONE_LIMIT", "5"))
DEFAULT_TOTAL_LIMIT = int(os.getenv("WEATHER_ENRICH_TOTAL_LIMIT", "25"))
OVERPASS_ZONE_DELAY_SECONDS = float(os.getenv("WEATHER_OVERPASS_ZONE_DELAY_SECONDS", "1.0"))

_PROPERTY_BUILDING_VALUES = {
    "house",
    "detached",
    "residential",
    "apartments",
    "condominium",
    "terrace",
    "semidetached_house",
    "commercial",
    "retail",
    "office",
    "industrial",
    "warehouse",
}

_EXCLUDED_BUILDING_VALUES = {
    "school",
    "church",
    "cathedral",
    "chapel",
    "government",
    "public",
    "toilets",
    "garage",
    "garages",
    "shed",
    "carport",
    "roof",
    "service",
}

_DAMAGE_COPY = {
    "hail": "granizo severo cerca de la propiedad",
    "wind": "viento severo/árboles/roof-siding risk cerca de la propiedad",
    "tornado": "tornado o daño extremo cerca de la propiedad",
    "rain": "lluvia fuerte/flood-leak risk cerca de la propiedad",
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.7613
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _safe_float(value, default=None):
    try:
        if value in (None, "", "UNK"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _impact_radius_to_meters(zone: dict) -> int:
    miles = _safe_float(zone.get("impact_radius_miles"), 3.0) or 3.0
    # Keep Overpass queries bounded; a full 15-mile city query is too expensive.
    return max(800, min(int(miles * 1609.344), MAX_RADIUS_METERS))


def _overpass_query(lat: float, lon: float, radius_meters: int, limit: int) -> str:
    # `out center` gives centroids for ways/relations. We intentionally query
    # buildings with addresses first so candidates are contactable enough for GC
    # review even before assessor enrichment.
    return f"""
[out:json][timeout:25];
(
  way["building"]["addr:housenumber"]["addr:street"](around:{radius_meters},{lat},{lon});
  relation["building"]["addr:housenumber"]["addr:street"](around:{radius_meters},{lat},{lon});
);
out tags center {limit};
"""


def _fetch_overpass_buildings(lat: float, lon: float, radius_meters: int, limit: int) -> list[dict]:
    data = urlencode({"data": _overpass_query(lat, lon, radius_meters, limit * 4)}).encode()
    last_error = None
    for endpoint in OVERPASS_URLS:
        req = Request(
            endpoint,
            data=data,
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8", "ignore"))
            return payload.get("elements") or []
        except Exception as exc:
            last_error = exc
            logger.debug(f"[WeatherPropertyEnrichment] Overpass endpoint failed {endpoint}: {exc}")
    if last_error:
        raise last_error
    return []


def _building_lat_lon(element: dict) -> tuple[float | None, float | None]:
    if "lat" in element and "lon" in element:
        return _safe_float(element.get("lat")), _safe_float(element.get("lon"))
    center = element.get("center") or {}
    return _safe_float(center.get("lat")), _safe_float(center.get("lon"))


def _property_address(tags: dict, fallback_city: str, fallback_state: str) -> str:
    parts = []
    number = tags.get("addr:housenumber")
    street = tags.get("addr:street")
    unit = tags.get("addr:unit") or tags.get("addr:suite")
    city = tags.get("addr:city") or fallback_city
    state = tags.get("addr:state") or fallback_state
    postcode = tags.get("addr:postcode")

    line1 = " ".join(str(p).strip() for p in [number, street] if p)
    if unit:
        line1 = f"{line1} #{unit}" if line1 else str(unit)
    if line1:
        parts.append(line1)
    locality = ", ".join(str(p).strip() for p in [city, state] if p)
    if postcode:
        locality = f"{locality} {postcode}" if locality else str(postcode)
    if locality:
        parts.append(locality)
    return ", ".join(parts) or f"Property near {fallback_city}, {fallback_state}"


def _building_type(tags: dict) -> str:
    return str(tags.get("building") or tags.get("building:use") or "building").lower()


def _is_useful_property(tags: dict) -> bool:
    btype = _building_type(tags)
    if btype in _EXCLUDED_BUILDING_VALUES:
        return False
    # Keep unknown but addressed buildings; many OSM records simply say yes.
    return btype in _PROPERTY_BUILDING_VALUES or btype == "yes" or bool(tags.get("addr:housenumber"))


def _property_kind(tags: dict) -> str:
    btype = _building_type(tags)
    if btype in {"apartments", "condominium"}:
        return "multifamily"
    if btype in {"commercial", "retail", "office", "industrial", "warehouse"}:
        return "commercial"
    return "residential"


def _event_strength(zone: dict) -> int:
    etype = zone.get("event_type")
    metric = _safe_float(zone.get("event_metric"), 0) or 0
    severity = zone.get("severity")
    if etype == "tornado":
        return 40
    if etype == "hail":
        return 35 if metric >= 1.75 else 28
    if etype == "wind":
        return 35 if metric >= 70 or severity == "HIGH" else 25
    if etype == "rain":
        return 22
    return 20


def _distance_score(distance_miles: float, impact_radius_miles: float) -> int:
    if distance_miles <= 1:
        return 35
    if distance_miles <= 3:
        return 28
    if distance_miles <= max(5, impact_radius_miles * 0.6):
        return 20
    return 12


def score_property_candidate(zone: dict, element: dict) -> dict:
    lat, lon = _building_lat_lon(element)
    distance = haversine_miles(zone["lat"], zone["lon"], lat, lon) if lat is not None and lon is not None else 99
    tags = element.get("tags") or {}
    kind = _property_kind(tags)
    btype = _building_type(tags)
    impact_radius = _safe_float(zone.get("impact_radius_miles"), 3.0) or 3.0

    score = 0
    reasons = []

    score += _event_strength(zone)
    reasons.append(f"Evento {zone.get('event_type')} severo")

    ds = _distance_score(distance, impact_radius)
    score += ds
    reasons.append(f"A {distance:.1f} mi del reporte")

    if kind == "multifamily":
        score += 15
        reasons.append("Multifamily/condo")
    elif kind == "commercial":
        score += 12
        reasons.append("Comercial")
    elif kind == "residential":
        score += 10
        reasons.append("Residencial")

    if btype in {"apartments", "commercial", "retail", "office", "warehouse", "industrial"}:
        score += 5

    if tags.get("roof:material") or tags.get("roof:shape"):
        score += 3
        reasons.append("Roof metadata disponible")

    final = max(0, min(100, int(score)))
    grade = "HOT" if final >= 80 else "WARM" if final >= 60 else "MEDIUM" if final >= 40 else "COOL"
    return {
        "score": final,
        "grade": grade,
        "grade_emoji": "fire" if grade == "HOT" else "orange" if grade == "WARM" else "yellow" if grade == "MEDIUM" else "blue",
        "reasons": reasons[:4],
        "distance_miles": round(distance, 2),
        "property_kind": kind,
        "building_type": btype,
    }


def build_property_candidate(zone: dict, element: dict, rank: int) -> dict | None:
    tags = element.get("tags") or {}
    if not _is_useful_property(tags):
        return None
    lat, lon = _building_lat_lon(element)
    if lat is None or lon is None:
        return None

    scoring = score_property_candidate(zone, element)
    state = tags.get("addr:state") or zone.get("state") or ""
    city = tags.get("addr:city") or zone.get("city") or ""
    address = _property_address(tags, city, state)
    services = zone.get("recommended_services") or ["roofing", "siding", "windows"]
    event_type = zone.get("event_type") or "weather"
    risk_copy = _DAMAGE_COPY.get(event_type, "clima severo cerca de la propiedad")

    osm_type = element.get("type", "osm")
    osm_id = element.get("id", rank)
    source_zone_id = zone.get("id", "weather_zone")
    lead_id = f"{source_zone_id}_prop_{osm_type}_{osm_id}"

    return {
        "id": lead_id,
        "title": f"Property candidate — {address}",
        "address": address,
        "city": city,
        "county": zone.get("county"),
        "state": state,
        "lat": lat,
        "lon": lon,
        "description": (
            f"Propiedad dentro de zona NOAA/SPC de impacto probable: {risk_copy}. "
            f"Distancia al reporte: {scoring['distance_miles']} mi. "
            f"Servicios recomendados para GC/restoration: {', '.join(services)}. "
            "No afirmar daño confirmado; usar para inspección/outreach con verificación."
        ),
        "date": zone.get("date"),
        "source": "NOAA/SPC + OpenStreetMap building enrichment",
        "source_url": zone.get("source_url") or "https://www.spc.noaa.gov/climo/reports/",
        "lead_type": "weather_property_candidate",
        "parent_weather_zone_id": source_zone_id,
        "event_type": event_type,
        "event_label": zone.get("event_label"),
        "event_metric": zone.get("event_metric"),
        "event_time": zone.get("event_time"),
        "metro": zone.get("metro"),
        "impact_radius_miles": zone.get("impact_radius_miles"),
        "distance_to_event_miles": scoring["distance_miles"],
        "property_kind": scoring["property_kind"],
        "building_type": scoring["building_type"],
        "recommended_services": services,
        "service_type": "roofing",
        "primary_service_type": "roofing",
        "_trade": "ROOFING",
        "_sub_trades": [str(s).upper() for s in services if s != "roofing"],
        "_urgency": "HIGH" if scoring["score"] >= 80 else "MEDIUM",
        "_project_scope": "EMERGENCY" if scoring["score"] >= 80 else "REPAIR",
        "_decision_maker": "GC",
        "_key_pain_point": "probable storm impact zone property candidate",
        "_competing_subs": 2,
        "_agent_key": "weather",
        "_scoring": scoring,
        "_weather_confidence": "property_candidate_in_probable_impact_zone",
        "_disclaimer": "Property is inside a probable weather impact zone; damage is not confirmed.",
        "osm_id": osm_id,
        "osm_type": osm_type,
        "osm_tags": tags,
    }


def enrich_zone_to_property_candidates(zone: dict, limit: int = DEFAULT_PER_ZONE_LIMIT) -> list[dict]:
    lat = _safe_float(zone.get("lat"))
    lon = _safe_float(zone.get("lon"))
    if lat is None or lon is None:
        return []

    radius_meters = _impact_radius_to_meters(zone)
    try:
        elements = _fetch_overpass_buildings(lat, lon, radius_meters, limit)
    except Exception as exc:
        logger.warning(f"[WeatherPropertyEnrichment] Overpass failed for {zone.get('id')}: {exc}")
        return []

    candidates = []
    seen_addresses = set()
    for idx, element in enumerate(elements):
        candidate = build_property_candidate(zone, element, idx)
        if not candidate:
            continue
        address_key = candidate["address"].lower()
        if address_key in seen_addresses:
            continue
        seen_addresses.add(address_key)
        candidates.append(candidate)

    candidates.sort(key=lambda item: (-item.get("_scoring", {}).get("score", 0), item.get("distance_to_event_miles", 99)))
    return candidates[:limit]


def enrich_weather_zones(
    zones: list[dict],
    per_zone_limit: int = DEFAULT_PER_ZONE_LIMIT,
    total_limit: int = DEFAULT_TOTAL_LIMIT,
    include_zone_leads: bool = False,
) -> list[dict]:
    """Return property candidates from weather zones, optionally keeping zone leads."""
    output = list(zones) if include_zone_leads else []
    seen_addresses = {str(zone.get("address", "")).lower() for zone in output if zone.get("address")}
    produced = 0
    for zone in zones:
        if produced >= total_limit:
            break
        remaining = total_limit - produced
        candidates = enrich_zone_to_property_candidates(zone, limit=min(per_zone_limit, remaining))
        for candidate in candidates:
            address_key = str(candidate.get("address", "")).lower()
            if address_key and address_key in seen_addresses:
                continue
            if address_key:
                seen_addresses.add(address_key)
            output.append(candidate)
            produced += 1
            if produced >= total_limit:
                break
        if OVERPASS_ZONE_DELAY_SECONDS > 0 and produced < total_limit:
            time.sleep(OVERPASS_ZONE_DELAY_SECONDS)
    return output
