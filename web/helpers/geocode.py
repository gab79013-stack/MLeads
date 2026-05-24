"""
geocode.py — Geolocation helpers for swipe/pipeline blueprints

Provides city coordinate lookups, haversine distance, and IP geolocation.
"""

import math as _math
import os
import json
import logging

logger = logging.getLogger("web_api")

# ── City coordinates for radius filtering ─────────────────────────────────────

CITY_COORDS: dict[str, tuple[float, float]] = {
    # California – Bay Area
    "san francisco": (37.7749, -122.4194),
    "oakland": (37.8044, -122.2712),
    "berkeley": (37.8716, -122.2727),
    "san jose": (37.3382, -121.8863),
    "fremont": (37.5485, -121.9886),
    "hayward": (37.6688, -122.0808),
    "sunnyvale": (37.3688, -122.0363),
    "santa clara": (37.3541, -121.9552),
    "mountain view": (37.3861, -122.0839),
    "palo alto": (37.4419, -122.1430),
    "redwood city": (37.4852, -122.2364),
    "san mateo": (37.5630, -122.3255),
    "daly city": (37.6879, -122.4702),
    "richmond": (37.9358, -122.3477),
    "concord": (37.9780, -122.0311),
    "vallejo": (38.1041, -122.2566),
    "antioch": (37.9960, -121.8058),
    "richmond ca": (37.9358, -122.3477),
    "san leandro": (37.7249, -122.1561),
    "livermore": (37.6819, -121.7681),
    "pleasanton": (37.6624, -121.8747),
    "walnut creek": (37.9101, -122.0652),
    "san rafael": (37.9735, -122.5311),
    "napa": (38.2975, -122.2869),
    "santa rosa": (38.4404, -122.7141),
    "petaluma": (38.2324, -122.6367),
    "novato": (38.1074, -122.5697),
    "los angeles": (34.0522, -118.2437),
    "long beach": (33.7701, -118.1937),
    "anaheim": (33.8366, -117.9143),
    "santa ana": (33.7455, -117.8677),
    "irvine": (33.6846, -117.8265),
    "san diego": (32.7157, -117.1611),
    "sacramento": (38.5816, -121.4944),
    "fresno": (36.7378, -119.7871),
    "bakersfield": (35.3733, -119.0187),
    "stockton": (37.9577, -121.2908),
    "modesto": (37.6391, -120.9969),
    # Other major US cities
    "new york": (40.7128, -74.0060),
    "brooklyn": (40.6782, -73.9442),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "philadelphia": (39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "dallas": (32.7767, -96.7970),
    "austin": (30.2672, -97.7431),
    "jacksonville": (30.3322, -81.6557),
    "columbus": (39.9612, -82.9988),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "nashville": (36.1627, -86.7816),
    "portland": (45.5051, -122.6750),
    "las vegas": (36.1699, -115.1398),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "boston": (42.3601, -71.0589),
}


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = _math.radians(lat2 - lat1)
    dlon = _math.radians(lon2 - lon1)
    a = (_math.sin(dlat / 2) ** 2
         + _math.cos(_math.radians(lat1))
         * _math.cos(_math.radians(lat2))
         * _math.sin(dlon / 2) ** 2)
    return R * 2 * _math.asin(_math.sqrt(a))


def city_coords(city_name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city name, or None if unknown."""
    return CITY_COORDS.get((city_name or "").strip().lower())


# Cache IP geolocations to avoid repeated API calls
_IP_GEO_CACHE: dict[str, tuple[float, float] | None] = {}


def _geo_locate_ip(ip: str) -> tuple[float, float] | None:
    """Geolocate an IP address using ip-api.com (free, no key, 45 req/min)."""
    if not ip or ip in ("127.0.0.1", "localhost", "::1") or ip.startswith("10.") or ip.startswith("192.168."):
        return None
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=status,lat,lon", timeout=3)
        data = json.loads(resp.read())
        if data.get("status") == "success":
            return (float(data["lat"]), float(data["lon"]))
    except Exception:
        pass
    return None


def get_ip_geo(ip: str) -> tuple[float, float] | None:
    """Get IP geolocation with caching."""
    if ip in _IP_GEO_CACHE:
        return _IP_GEO_CACHE[ip]
    result = _geo_locate_ip(ip)
    _IP_GEO_CACHE[ip] = result
    # Evict cache if too large
    if len(_IP_GEO_CACHE) > 500:
        _IP_GEO_CACHE.pop(next(iter(_IP_GEO_CACHE)))
    return result

def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = _math.radians(lat2 - lat1)
    dlon = _math.radians(lon2 - lon1)
    a = (_math.sin(dlat / 2) ** 2
         + _math.cos(_math.radians(lat1))
         * _math.cos(_math.radians(lat2))
         * _math.sin(dlon / 2) ** 2)
    return R * 2 * _math.asin(_math.sqrt(a))



def _city_coords(city_name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city name, or None if unknown."""
    return CITY_COORDS.get((city_name or "").strip().lower())


def _get_ip_geo(ip: str) -> tuple[float, float] | None:
    """Get IP geolocation with caching."""
    if ip in _IP_GEO_CACHE:
        return _IP_GEO_CACHE[ip]
    result = _geo_locate_ip(ip)
    _IP_GEO_CACHE[ip] = result
    # Evict cache if too large
    if len(_IP_GEO_CACHE) > 500:
        _IP_GEO_CACHE.pop(next(iter(_IP_GEO_CACHE)))
    return result



