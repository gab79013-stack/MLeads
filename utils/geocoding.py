"""
utils/geocoding.py
━━━━━━━━━━━━━━━━━━
Geocodificación gratuita usando Nominatim (OpenStreetMap).

Prioridad: Google Geocoding API (si está configurado) → Nominatim (gratis, sin key)

Nominatim:
  - Completamente gratuito, sin API key
  - Límite: 1 req/segundo (respetado automáticamente con rate limiter)
  - Cobertura: mundial, excelente para Bay Area
  - Docs: https://nominatim.org/release-docs/latest/api/Overview/
"""

import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

GOOGLE_GEOCODE_KEY = os.getenv("GOOGLE_GEOCODE_API_KEY", "")

# Rate limiter simple para Nominatim (1 req/seg per ToS)
_NOMINATIM_LAST_CALL = 0.0
_NOMINATIM_MIN_INTERVAL = 1.1  # segundos entre llamadas

_NOMINATIM_HEADERS = {
    "User-Agent": "MLeads/1.0 (lead-generation-platform; contact@mleads.io)",
    "Accept-Language": "en-US,en",
}

_SESSION = requests.Session()
_SESSION.headers.update(_NOMINATIM_HEADERS)


def _nominatim_rate_limit():
    global _NOMINATIM_LAST_CALL
    elapsed = time.monotonic() - _NOMINATIM_LAST_CALL
    if elapsed < _NOMINATIM_MIN_INTERVAL:
        time.sleep(_NOMINATIM_MIN_INTERVAL - elapsed)
    _NOMINATIM_LAST_CALL = time.monotonic()


def geocode_address(address: str, city: str = "", state: str = "CA") -> dict | None:
    """
    Geocodifica una dirección → {lat, lon, display_name}.

    Prioridad:
      1. Google Geocoding API (si GOOGLE_GEOCODE_API_KEY está configurado)
      2. Nominatim / OpenStreetMap (gratuito, sin key)

    Retorna None si no se puede geocodificar.
    """
    full_address = _build_query(address, city, state)

    if GOOGLE_GEOCODE_KEY:
        result = _google_geocode(full_address)
        if result:
            return result
        logger.debug(f"[Geocoding] Google falló para '{full_address}', usando Nominatim")

    return _nominatim_geocode(full_address)


def reverse_geocode(lat: float, lon: float) -> dict | None:
    """
    Geocodificación inversa: coordenadas → dirección.

    Prioridad: Google → Nominatim
    Retorna dict con: address, city, state, zip, country
    """
    if GOOGLE_GEOCODE_KEY:
        result = _google_reverse(lat, lon)
        if result:
            return result

    return _nominatim_reverse(lat, lon)


# ── Google Geocoding ──────────────────────────────────────────────────

def _google_geocode(address: str) -> dict | None:
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_GEOCODE_KEY},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") != "OK" or not data.get("results"):
            return None
        loc = data["results"][0]["geometry"]["location"]
        return {
            "lat": loc["lat"],
            "lon": loc["lng"],
            "display_name": data["results"][0].get("formatted_address", address),
            "source": "google",
        }
    except Exception as e:
        logger.debug(f"[Geocoding/Google] {e}")
        return None


def _google_reverse(lat: float, lon: float) -> dict | None:
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"latlng": f"{lat},{lon}", "key": GOOGLE_GEOCODE_KEY},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") != "OK" or not data.get("results"):
            return None
        return _parse_google_address(data["results"][0])
    except Exception as e:
        logger.debug(f"[Geocoding/Google reverse] {e}")
        return None


def _parse_google_address(result: dict) -> dict:
    components = {
        c["types"][0]: c["long_name"]
        for c in result.get("address_components", [])
        if c.get("types")
    }
    return {
        "address": result.get("formatted_address", ""),
        "city": components.get("locality") or components.get("sublocality", ""),
        "state": components.get("administrative_area_level_1", ""),
        "zip": components.get("postal_code", ""),
        "country": components.get("country", ""),
        "source": "google",
    }


# ── Nominatim / OpenStreetMap ─────────────────────────────────────────

def _nominatim_geocode(address: str) -> dict | None:
    try:
        _nominatim_rate_limit()
        resp = _SESSION.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": address,
                "format": "json",
                "limit": 1,
                "countrycodes": "us",
                "addressdetails": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None
        r = results[0]
        return {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r.get("display_name", address),
            "source": "nominatim",
        }
    except Exception as e:
        logger.debug(f"[Geocoding/Nominatim] {address!r}: {e}")
        return None


def _nominatim_reverse(lat: float, lon: float) -> dict | None:
    try:
        _nominatim_rate_limit()
        resp = _SESSION.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return None
        addr = data.get("address", {})
        return {
            "address": data.get("display_name", ""),
            "city": (
                addr.get("city")
                or addr.get("town")
                or addr.get("village")
                or addr.get("suburb", "")
            ),
            "state": addr.get("state", ""),
            "zip": addr.get("postcode", ""),
            "country": addr.get("country_code", "").upper(),
            "source": "nominatim",
        }
    except Exception as e:
        logger.debug(f"[Geocoding/Nominatim reverse] ({lat},{lon}): {e}")
        return None


# ── Helper ────────────────────────────────────────────────────────────

def _build_query(address: str, city: str, state: str) -> str:
    parts = [p.strip() for p in [address, city, state, "USA"] if p.strip()]
    return ", ".join(parts)
