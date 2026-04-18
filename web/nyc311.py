"""
nyc311.py — Blueprint Flask para NYC 311 Service Requests API

Expone los datos de solicitudes 311 de la Ciudad de Nueva York
organizados por borough, agencia, tipo de queja y estado.

Rutas disponibles:
  GET /api/nyc311/                   — info y estado de caché
  POST /api/nyc311/refresh           — forzar refresco de caché
  GET /api/nyc311/requests           — listar solicitudes (filtros opcionales)
  GET /api/nyc311/requests/<key>     — detalle de una solicitud
  GET /api/nyc311/summary            — resumen estadístico
  GET /api/nyc311/by-borough         — agrupado por borough
  GET /api/nyc311/by-agency          — agrupado por agencia
  GET /api/nyc311/by-complaint-type  — agrupado por tipo de queja
  GET /api/nyc311/by-status          — agrupado por estado
"""

import logging
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Optional

import requests as req
from flask import Blueprint, jsonify, request

logger = logging.getLogger("nyc311")

NYC_API = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
CACHE_TTL = 300          # segundos entre refrescos automáticos
DEFAULT_FETCH = 1000     # registros por defecto al refrescar


# ── Caché en memoria ──────────────────────────────────────────────────────────

class _Cache:
    def __init__(self):
        self._records: list[dict] = []
        self._last_updated: Optional[datetime] = None
        self._lock = threading.Lock()

    @property
    def records(self):
        return self._records

    @property
    def last_updated(self):
        return self._last_updated

    @property
    def is_stale(self):
        if not self._last_updated:
            return True
        return datetime.utcnow() - self._last_updated > timedelta(seconds=CACHE_TTL)

    def refresh(self, limit: int = DEFAULT_FETCH) -> int:
        logger.info("Refreshing NYC 311 cache…")
        params = {
            "$limit": min(limit, 5000),
            "$order": "created_date DESC",
        }
        resp = req.get(NYC_API, params=params, timeout=30)
        resp.raise_for_status()
        raw = resp.json()
        with self._lock:
            self._records = raw
            self._last_updated = datetime.utcnow()
        logger.info(f"NYC 311 cache updated: {len(raw)} records")
        return len(raw)

    def get(self, limit: int = DEFAULT_FETCH) -> list[dict]:
        if self.is_stale:
            self.refresh(limit)
        return self._records


_cache = _Cache()


def _start_background_refresh(interval: int = CACHE_TTL, limit: int = DEFAULT_FETCH):
    """Lanza un hilo daemon que refresca la caché cada `interval` segundos."""
    def _loop():
        while True:
            try:
                _cache.refresh(limit)
            except Exception as e:
                logger.error(f"NYC 311 background refresh failed: {e}")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True, name="nyc311-refresh")
    t.start()


# Arranque inicial (primer carga + loop)
try:
    _cache.refresh()
except Exception as e:
    logger.warning(f"NYC 311 initial cache load failed: {e}")

_start_background_refresh()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _parse_dt(v) -> Optional[datetime]:
    if v in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _clean(r: dict) -> dict:
    """Normaliza y limpia un registro crudo."""
    return {
        "unique_key": r.get("unique_key"),
        "created_date": r.get("created_date"),
        "closed_date": r.get("closed_date"),
        "due_date": r.get("due_date"),
        "agency": r.get("agency"),
        "agency_name": r.get("agency_name"),
        "complaint_type": r.get("complaint_type"),
        "descriptor": r.get("descriptor"),
        "incident_address": r.get("incident_address"),
        "street_name": r.get("street_name"),
        "incident_zip": r.get("incident_zip"),
        "address_type": r.get("address_type"),
        "city": r.get("city"),
        "borough": r.get("borough"),
        "community_board": r.get("community_board"),
        "council_district": r.get("council_district"),
        "police_precinct": r.get("police_precinct"),
        "status": r.get("status"),
        "resolution_description": r.get("resolution_description"),
        "latitude": _parse_float(r.get("latitude")),
        "longitude": _parse_float(r.get("longitude")),
        "bbl": r.get("bbl"),
    }


def _filter(records, borough=None, complaint_type=None, agency=None,
            status=None, start_date=None, end_date=None):
    out = records
    if borough:
        b = borough.upper()
        out = [r for r in out if (r.get("borough") or "").upper() == b]
    if complaint_type:
        c = complaint_type.upper()
        out = [r for r in out if (r.get("complaint_type") or "").upper() == c]
    if agency:
        a = agency.upper()
        out = [r for r in out if (r.get("agency") or "").upper() == a]
    if status:
        s = status.upper()
        out = [r for r in out if (r.get("status") or "").upper() == s]
    if start_date:
        out = [r for r in out if _parse_dt(r.get("created_date")) and
               _parse_dt(r.get("created_date")) >= start_date]
    if end_date:
        out = [r for r in out if _parse_dt(r.get("created_date")) and
               _parse_dt(r.get("created_date")) <= end_date]
    return out


def _parse_date_param(name: str):
    v = request.args.get(name)
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        from flask import abort
        abort(400, f"'{name}' debe estar en formato ISO 8601")


# ── Blueprint ─────────────────────────────────────────────────────────────────

bp = Blueprint("nyc311", __name__, url_prefix="/api/nyc311")


@bp.get("/")
def index():
    return jsonify({
        "service": "NYC 311 Service Requests",
        "cache": {
            "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
            "records_cached": len(_cache.records),
            "ttl_seconds": CACHE_TTL,
            "stale": _cache.is_stale,
        },
        "endpoints": {
            "GET /api/nyc311/requests": "Listar solicitudes (filtros: borough, complaint_type, agency, status, start_date, end_date, limit, offset)",
            "GET /api/nyc311/requests/<key>": "Detalle de una solicitud",
            "GET /api/nyc311/summary": "Resumen estadístico por borough, agencia y tipo",
            "GET /api/nyc311/by-borough": "Solicitudes agrupadas por borough",
            "GET /api/nyc311/by-agency": "Solicitudes agrupadas por agencia",
            "GET /api/nyc311/by-complaint-type": "Solicitudes agrupadas por tipo de queja",
            "GET /api/nyc311/by-status": "Solicitudes agrupadas por estado",
            "POST /api/nyc311/refresh": "Forzar refresco inmediato de la caché",
        },
        "data_source": NYC_API,
    })


@bp.post("/refresh")
def force_refresh():
    limit = int(request.args.get("limit", DEFAULT_FETCH))
    count = _cache.refresh(limit=min(limit, 5000))
    return jsonify({
        "status": "ok",
        "records_fetched": count,
        "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
    })


@bp.get("/requests")
def list_requests():
    limit = min(int(request.args.get("limit", 100)), 5000)
    offset = int(request.args.get("offset", 0))
    records = _cache.get()
    records = _filter(
        records,
        borough=request.args.get("borough"),
        complaint_type=request.args.get("complaint_type"),
        agency=request.args.get("agency"),
        status=request.args.get("status"),
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )
    page = [_clean(r) for r in records[offset: offset + limit]]
    return jsonify({"total": len(records), "offset": offset, "limit": limit, "data": page})


@bp.get("/requests/<unique_key>")
def get_request(unique_key):
    for r in _cache.records:
        if r.get("unique_key") == unique_key:
            return jsonify(_clean(r))
    # fallback a la API en vivo
    resp = req.get(NYC_API, params={"$where": f"unique_key='{unique_key}'", "$limit": 1}, timeout=15)
    data = resp.json()
    if not data:
        return jsonify({"error": "Not found"}), 404
    return jsonify(_clean(data[0]))


@bp.get("/summary")
def summary():
    records = _cache.get()
    records = _filter(
        records,
        borough=request.args.get("borough"),
        complaint_type=request.args.get("complaint_type"),
        agency=request.args.get("agency"),
        status=request.args.get("status"),
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )

    dates = [_parse_dt(r.get("created_date")) for r in records if r.get("created_date")]
    date_range = (
        {"oldest": min(dates).isoformat(), "newest": max(dates).isoformat()}
        if dates else {}
    )

    status_counts = Counter((r.get("status") or "UNKNOWN").upper() for r in records)

    # By borough
    by_borough_map: dict = defaultdict(list)
    for r in records:
        by_borough_map[(r.get("borough") or "UNKNOWN").upper()].append(r)

    by_borough = []
    for borough, items in sorted(by_borough_map.items()):
        ct_counter = Counter(r.get("complaint_type") for r in items if r.get("complaint_type"))
        by_borough.append({
            "borough": borough,
            "total": len(items),
            "open": sum(1 for r in items if (r.get("status") or "").upper() == "OPEN"),
            "closed": sum(1 for r in items if (r.get("status") or "").upper() == "CLOSED"),
            "in_progress": sum(1 for r in items if (r.get("status") or "").upper() == "IN PROGRESS"),
            "top_complaint_types": [{"type": t, "count": c} for t, c in ct_counter.most_common(5)],
        })

    # By agency
    by_agency_map: dict = defaultdict(list)
    for r in records:
        by_agency_map[r.get("agency") or "UNKNOWN"].append(r)

    by_agency = []
    for agency, items in sorted(by_agency_map.items()):
        by_agency.append({
            "agency": agency,
            "agency_name": next((r.get("agency_name") for r in items if r.get("agency_name")), agency),
            "total": len(items),
            "open": sum(1 for r in items if (r.get("status") or "").upper() == "OPEN"),
            "closed": sum(1 for r in items if (r.get("status") or "").upper() == "CLOSED"),
        })

    # By complaint type (top 20)
    by_ct_map: dict = defaultdict(list)
    for r in records:
        by_ct_map[r.get("complaint_type") or "UNKNOWN"].append(r)

    by_complaint_type = [
        {
            "complaint_type": ct,
            "total": len(items),
            "boroughs_affected": sorted({(r.get("borough") or "UNKNOWN").upper() for r in items}),
        }
        for ct, items in sorted(by_ct_map.items(), key=lambda x: -len(x[1]))[:20]
    ]

    return jsonify({
        "total_records": len(records),
        "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
        "date_range": date_range,
        "status_breakdown": dict(status_counts),
        "by_borough": by_borough,
        "by_agency": by_agency,
        "by_complaint_type": by_complaint_type,
    })


@bp.get("/by-borough")
def by_borough():
    records = _cache.get()
    records = _filter(
        records,
        borough=request.args.get("borough"),
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )
    grouped: dict = defaultdict(list)
    for r in records:
        grouped[(r.get("borough") or "UNKNOWN").upper()].append(r)

    return jsonify({
        b: {
            "total": len(items),
            "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
            "requests": [_clean(r) for r in items],
        }
        for b, items in sorted(grouped.items())
    })


@bp.get("/by-agency")
def by_agency():
    records = _cache.get()
    records = _filter(
        records,
        agency=request.args.get("agency"),
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )
    grouped: dict = defaultdict(list)
    for r in records:
        grouped[r.get("agency") or "UNKNOWN"].append(r)

    return jsonify({
        ag: {
            "agency_name": items[0].get("agency_name", ag) if items else ag,
            "total": len(items),
            "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
            "requests": [_clean(r) for r in items],
        }
        for ag, items in sorted(grouped.items())
    })


@bp.get("/by-complaint-type")
def by_complaint_type():
    records = _cache.get()
    records = _filter(
        records,
        complaint_type=request.args.get("complaint_type"),
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )
    grouped: dict = defaultdict(list)
    for r in records:
        grouped[r.get("complaint_type") or "UNKNOWN"].append(r)

    return jsonify({
        ct: {
            "total": len(items),
            "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
            "requests": [_clean(r) for r in items],
        }
        for ct, items in sorted(grouped.items())
    })


@bp.get("/by-status")
def by_status():
    records = _cache.get()
    records = _filter(
        records,
        start_date=_parse_date_param("start_date"),
        end_date=_parse_date_param("end_date"),
    )
    grouped: dict = defaultdict(list)
    for r in records:
        grouped[(r.get("status") or "UNKNOWN").upper()].append(r)

    return jsonify({
        st: {
            "total": len(items),
            "last_updated": _cache.last_updated.isoformat() if _cache.last_updated else None,
            "requests": [_clean(r) for r in items],
        }
        for st, items in sorted(grouped.items())
    })
