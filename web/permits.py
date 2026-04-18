"""
permits.py — Blueprint Flask para permisos de construcción/demolición

Agrega 13 fuentes de datos en vivo de Socrata y portales abiertos,
normaliza los campos a un esquema común orientado a leads, y los expone
mediante endpoints unificados bajo /api/permits/.

Fuentes activas (verificadas):
  dallas       Dallas, TX          – Building Permits
  cambridge    Cambridge, MA       – New Construction Permits
  edmonton     Edmonton, CA        – All Years Permits
  montgomery   Montgomery Co, MD   – SS Construction Permits
  nj           New Jersey          – NJ Construction Permit Data
  batonrouge   Baton Rouge, LA     – EBR Building Permits
  honolulu1    Honolulu, HI        – Building Permits 2010-2016
  honolulu2    Honolulu, HI        – Building Permits 2005-2025
  austin       Austin, TX          – Issued Construction Permits
  sandiego     San Diego Co, CA    – Building Permits
  walker       Walker Co, AL       – Building Permits
  chicago      Chicago, IL         – Building Permits
  seattle      Seattle, WA         – Building Permits
"""

import fcntl
import logging
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import requests as req
from flask import Blueprint, jsonify, request

logger = logging.getLogger("permits")

# ── Worker singleton: only one gunicorn worker imports to DB ──────────────────
# Multiple gunicorn workers would otherwise race on the SQLite DB.
_IMPORTER_LOCK_PATH = "/tmp/mleads_permits_importer.lock"
_importer_lock_fd = None

def _try_become_importer() -> bool:
    """Try to acquire an exclusive file lock. Returns True for the one worker that wins."""
    global _importer_lock_fd
    try:
        _importer_lock_fd = open(_IMPORTER_LOCK_PATH, "w")
        fcntl.flock(_importer_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _importer_lock_fd.write(str(os.getpid()))
        _importer_lock_fd.flush()
        return True
    except (IOError, OSError):
        return False

_IS_IMPORTER_WORKER: bool = _try_become_importer()
if _IS_IMPORTER_WORKER:
    logger.info(f"[permits] Worker {os.getpid()} is the designated DB importer")

# ── Catálogo de fuentes ───────────────────────────────────────────────────────

SOURCES: dict[str, dict] = {
    "dallas": {
        "city": "Dallas", "state": "TX",
        "url": "https://www.dallasopendata.com/resource/e7gq-4sah.json",
        "fields": {
            "permit_number": "permit_number",
            "permit_type":   "permit_type",
            "issue_date":    "issued_date",
            "address":       "street_address",
            "zip_code":      "zip_code",
            "contractor":    "contractor",
            "value":         "value",
            "description":   "work_description",
        },
    },
    "cambridge": {
        "city": "Cambridge", "state": "MA",
        "url": "https://data.cambridgema.gov/resource/9qm7-wbdc.json",
        "fields": {
            "permit_number": "id",
            "permit_type":   "permit_type",
            "issue_date":    "issue_date",
            "address":       "full_address",
            "owner":         "licensed_name",
            "value":         "total_cost_of_construction",
            "description":   "description_of_work",
            "latitude":      "centroid_latitude",
            "longitude":     "centroid_longitude",
            "status":        "status",
        },
    },
    "edmonton": {
        "city": "Edmonton", "state": "AB",
        "url": "https://data.edmonton.ca/resource/tmrn-cmdc.json",
        "fields": {
            "permit_type":   "job_category",
            "issue_date":    "issue_date",
            "address":       "address",
            "description":   "job_description",
            "latitude":      "latitude",
            "longitude":     "longitude",
        },
    },
    "montgomery": {
        "city": "Montgomery County", "state": "MD",
        "url": "https://data.montgomerycountymd.gov/resource/i9kt-f3rn.json",
        "fields": {
            "permit_number": "permitno",
            "permit_type":   "permittypedesc",
            "issue_date":    "permitdate",
            "status":        "permitstatusdesc",
            "value":         "constcost",
        },
    },
    "nj": {
        "city": "New Jersey", "state": "NJ",
        "url": "https://data.nj.gov/resource/w9se-dmra.json",
        "fields": {
            "permit_number": "permitno",
            "permit_type":   "permittypedesc",
            "issue_date":    "permitdate",
            "status":        "permitstatusdesc",
            "value":         "constcost",
            "zip_code":      None,
        },
    },
    "batonrouge": {
        "city": "Baton Rouge", "state": "LA",
        "url": "https://data.brla.gov/resource/7fq7-8j7r.json",
        "fields": {
            "permit_number": "permitnumber",
            "permit_type":   "permittype",
            "issue_date":    "issueddate",
            "address":       "streetaddress",
            "zip_code":      "zip",
            "contractor":    "contractorname",
            "contractor_address": "contractoraddress",
            "owner":         "applicantname",
            "value":         "projectvalue",
            "description":   "projectdescription",
        },
    },
    "honolulu1": {
        "city": "Honolulu", "state": "HI",
        "url": "https://data.honolulu.gov/resource/3fr8-2hnx.json",
        "fields": {
            "permit_number": "buildingpermitno",
            "issue_date":    "issuedate",
            "contractor":    "contractor",
            "value":         "estimatedvalueofwork",
            "status":        "statusdescription",
        },
    },
    "honolulu2": {
        "city": "Honolulu", "state": "HI",
        "url": "https://data.honolulu.gov/resource/4vab-c87q.json",
        "fields": {
            "permit_number": "buildingpermitno",
            "issue_date":    "issuedate",
            "contractor":    "contractor",
            "owner":         "applicant",
            "value":         "estimatedvalueofwork",
            "status":        "statusdescription",
            "address":       "address",
        },
    },
    "austin": {
        "city": "Austin", "state": "TX",
        "url": "https://data.austintexas.gov/resource/3syk-w9eu.json",
        "fields": {
            "permit_number": "permit_number",
            "permit_type":   "permit_type_desc",
            "issue_date":    "issue_date",
            "address":       "original_address1",
            "zip_code":      "original_zip",
            "value":         None,
            "description":   "description",
            "status":        "status_current",
            "latitude":      "latitude",
            "longitude":     "longitude",
        },
    },
    "sandiego": {
        "city": "San Diego County", "state": "CA",
        "url": "https://data.sandiegocounty.gov/resource/dyzh-7eat.json",
        "fields": {
            "permit_number": "record_id",
            "permit_type":   "record_type",
            "issue_date":    "issued_date",
            "address":       "street_address",
            "zip_code":      "zip_code",
            "contractor":    "contractor_name",
            "contractor_address": "contractor_address",
            "contractor_phone":   "contractor_phone",
            "owner":         "homeowner_biz_owner",
            "status":        "record_status",
        },
    },
    "walker": {
        "city": "Walker County", "state": "AL",
        "url": "https://data.wcad.org/resource/fqhf-gyjx.json",
        "fields": {
            "permit_number": "permitnumber",
            "permit_type":   "permittypedescription",
            "issue_date":    "issuedate",
            "address":       "situsaddress",
            "contractor":    "partyname",
            "status":        "permitstatusdescription",
        },
    },
    "chicago": {
        "city": "Chicago", "state": "IL",
        "url": "https://data.cityofchicago.org/resource/ydr8-5enu.json",
        "fields": {
            "permit_number": "permit_",
            "permit_type":   "permit_type",
            "issue_date":    "issue_date",
            "address":       "street_name",
            "contractor":    "contact_1_name",
            "contractor_address": "contact_1_city",
            "value":         "reported_cost",
            "description":   "work_description",
            "latitude":      "latitude",
            "longitude":     "longitude",
        },
    },
    "seattle": {
        "city": "Seattle", "state": "WA",
        "url": "https://data.seattle.gov/resource/76t5-zqzr.json",
        "fields": {
            "permit_number": "permitnum",
            "permit_type":   "permittypedesc",
            "address":       "originaladdress1",
            "zip_code":      "originalzip",
            "description":   "description",
            "status":        "statuscurrent",
            "latitude":      "latitude",
            "longitude":     "longitude",
        },
    },
}

CACHE_TTL = 300
DEFAULT_FETCH = 500


# ── Normalización ─────────────────────────────────────────────────────────────

def _normalize(raw: dict, source_id: str) -> dict:
    """Mapea los campos crudos de una fuente al esquema común de lead."""
    mapping = SOURCES[source_id]["fields"]
    src = SOURCES[source_id]

    def _get(field_key: str):
        mapped = mapping.get(field_key)
        if mapped is None:
            return None
        return raw.get(mapped)

    def _float(v):
        try:
            return float(v) if v not in (None, "") else None
        except (ValueError, TypeError):
            return None

    return {
        "source":             source_id,
        "city":               src["city"],
        "state":              src["state"],
        "permit_number":      _get("permit_number"),
        "permit_type":        _get("permit_type"),
        "issue_date":         _get("issue_date"),
        "status":             _get("status"),
        "address":            _get("address"),
        "zip_code":           _get("zip_code"),
        "contractor":         _get("contractor"),
        "contractor_address": _get("contractor_address"),
        "contractor_phone":   _get("contractor_phone"),
        "owner":              _get("owner"),
        "project_value":      _float(_get("value")),
        "description":        _get("description"),
        "latitude":           _float(_get("latitude")),
        "longitude":          _float(_get("longitude")),
    }


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _import_to_leads(records: list[dict]):
    """Importa registros normalizados a consolidated_leads (ejecutado en hilo daemon)."""
    if not _IS_IMPORTER_WORKER:
        return  # Only the designated worker does DB imports
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from utils.permits_importer import import_permits
        stats = import_permits(records)
        logger.info(f"DB import: {stats}")
    except Exception as e:
        logger.error(f"DB import failed: {e}")


# ── Caché por fuente ──────────────────────────────────────────────────────────

class _SourceCache:
    def __init__(self, source_id: str):
        self.source_id = source_id
        self._records: list[dict] = []
        self._last_updated: Optional[datetime] = None
        self._error: Optional[str] = None
        self._lock = threading.Lock()

    @property
    def records(self):
        return self._records

    @property
    def last_updated(self):
        return self._last_updated

    @property
    def error(self):
        return self._error

    @property
    def is_stale(self):
        if not self._last_updated:
            return True
        return datetime.utcnow() - self._last_updated > timedelta(seconds=CACHE_TTL)

    def status(self) -> dict:
        return {
            "source": self.source_id,
            "city": SOURCES[self.source_id]["city"],
            "state": SOURCES[self.source_id]["state"],
            "records_cached": len(self._records),
            "last_updated": self._last_updated.isoformat() if self._last_updated else None,
            "stale": self.is_stale,
            "error": self._error,
        }

    def refresh(self, limit: int = DEFAULT_FETCH) -> int:
        url = SOURCES[self.source_id]["url"]
        try:
            resp = req.get(
                url,
                params={"$limit": min(limit, 5000), "$order": ":id DESC"},
                timeout=20,
            )
            resp.raise_for_status()
            raw = resp.json()
            normalized = [_normalize(r, self.source_id) for r in raw]
            with self._lock:
                self._records = normalized
                self._last_updated = datetime.utcnow()
                self._error = None
            logger.info(f"[{self.source_id}] Refreshed: {len(normalized)} records")
            # Importar al sistema de leads en hilo separado para no bloquear
            threading.Thread(
                target=_import_to_leads,
                args=(normalized,),
                daemon=True,
                name=f"import-{self.source_id}",
            ).start()
            return len(normalized)
        except Exception as e:
            self._error = str(e)
            logger.error(f"[{self.source_id}] Refresh failed: {e}")
            raise

    def get(self, limit: int = DEFAULT_FETCH) -> list[dict]:
        if self.is_stale:
            try:
                self.refresh(limit)
            except Exception:
                pass  # return stale data if available
        return self._records


class _PermitsCache:
    def __init__(self):
        self._sources: dict[str, _SourceCache] = {
            sid: _SourceCache(sid) for sid in SOURCES
        }

    def source(self, sid: str) -> _SourceCache:
        return self._sources[sid]

    def all_records(self) -> list[dict]:
        out = []
        for sc in self._sources.values():
            out.extend(sc.records)
        return out

    def statuses(self) -> list[dict]:
        return [sc.status() for sc in self._sources.values()]

    def refresh_all(self, limit: int = DEFAULT_FETCH):
        errors = []
        for sid, sc in self._sources.items():
            try:
                sc.refresh(limit)
            except Exception as e:
                errors.append({"source": sid, "error": str(e)})
        return errors

    def start_background_refresh(self, interval: int = CACHE_TTL, limit: int = DEFAULT_FETCH):
        def _loop():
            while True:
                self.refresh_all(limit)
                time.sleep(interval)
        t = threading.Thread(target=_loop, daemon=True, name="permits-refresh")
        t.start()


cache = _PermitsCache()


# Carga inicial en paralelo
def _initial_load():
    threads = []
    for sid, sc in cache._sources.items():
        t = threading.Thread(target=lambda s=sc: s.refresh(), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=25)

_load_thread = threading.Thread(target=_initial_load, daemon=True, name="permits-init")
_load_thread.start()
if _IS_IMPORTER_WORKER:
    cache.start_background_refresh()


# ── Filtrado ──────────────────────────────────────────────────────────────────

def _filter(records, source=None, state=None, permit_type=None, status=None,
            has_contractor=False, min_value=None, max_value=None,
            start_date=None, end_date=None):
    out = records
    if source:
        s = source.lower()
        out = [r for r in out if r["source"] == s]
    if state:
        st = state.upper()
        out = [r for r in out if (r.get("state") or "").upper() == st]
    if permit_type:
        pt = permit_type.upper()
        out = [r for r in out if pt in (r.get("permit_type") or "").upper()]
    if status:
        sv = status.upper()
        out = [r for r in out if sv in (r.get("status") or "").upper()]
    if has_contractor:
        out = [r for r in out if r.get("contractor")]
    if min_value is not None:
        out = [r for r in out if (r.get("project_value") or 0) >= min_value]
    if max_value is not None:
        out = [r for r in out if (r.get("project_value") or 0) <= max_value]
    if start_date:
        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v)[:19])
            except Exception:
                return None
        out = [r for r in out if _dt(r.get("issue_date")) and _dt(r.get("issue_date")) >= start_date]
    if end_date:
        def _dt2(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v)[:19])
            except Exception:
                return None
        out = [r for r in out if _dt2(r.get("issue_date")) and _dt2(r.get("issue_date")) <= end_date]
    return out


def _parse_date(name: str) -> Optional[datetime]:
    v = request.args.get(name)
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        from flask import abort
        abort(400, f"'{name}' debe estar en formato ISO 8601")


# ── Blueprint ─────────────────────────────────────────────────────────────────

bp = Blueprint("permits", __name__, url_prefix="/api/permits")


@bp.get("/")
def index():
    return jsonify({
        "service": "Construction & Demolition Permits — Lead Generator",
        "sources": cache.statuses(),
        "total_cached": sum(len(cache.source(s).records) for s in SOURCES),
        "endpoints": {
            "GET /api/permits/":                   "Estado de todas las fuentes",
            "GET /api/permits/leads":              "Leads unificados (todos los permisos, filtros opcionales)",
            "GET /api/permits/leads/contractors":  "Solo permisos con nombre de contratista",
            "GET /api/permits/summary":            "Resumen estadístico por ciudad y tipo",
            "GET /api/permits/by-source":          "Permisos agrupados por fuente",
            "GET /api/permits/by-state":           "Permisos agrupados por estado",
            "GET /api/permits/{source_id}":        "Permisos de una fuente específica",
            "POST /api/permits/refresh":           "Forzar refresco de todas las fuentes",
            "POST /api/permits/{source_id}/refresh": "Forzar refresco de una fuente",
        },
        "filters_available": [
            "source", "state", "permit_type", "status",
            "has_contractor (bool)", "min_value", "max_value",
            "start_date", "end_date", "limit", "offset",
        ],
    })


@bp.get("/leads")
def leads():
    limit = min(int(request.args.get("limit", 200)), 5000)
    offset = int(request.args.get("offset", 0))
    records = cache.all_records()
    records = _filter(
        records,
        source=request.args.get("source"),
        state=request.args.get("state"),
        permit_type=request.args.get("permit_type"),
        status=request.args.get("status"),
        has_contractor=request.args.get("has_contractor", "").lower() in ("1", "true", "yes"),
        min_value=float(request.args["min_value"]) if request.args.get("min_value") else None,
        max_value=float(request.args["max_value"]) if request.args.get("max_value") else None,
        start_date=_parse_date("start_date"),
        end_date=_parse_date("end_date"),
    )
    page = [_drop_none(r) for r in records[offset: offset + limit]]
    return jsonify({
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "data": page,
    })


@bp.get("/leads/contractors")
def leads_contractors():
    """Solo devuelve permisos que tienen contratista identificado — máxima calidad para leads."""
    limit = min(int(request.args.get("limit", 200)), 5000)
    offset = int(request.args.get("offset", 0))
    records = cache.all_records()
    records = _filter(
        records,
        source=request.args.get("source"),
        state=request.args.get("state"),
        permit_type=request.args.get("permit_type"),
        status=request.args.get("status"),
        has_contractor=True,
        min_value=float(request.args["min_value"]) if request.args.get("min_value") else None,
        max_value=float(request.args["max_value"]) if request.args.get("max_value") else None,
        start_date=_parse_date("start_date"),
        end_date=_parse_date("end_date"),
    )
    page = [_drop_none(r) for r in records[offset: offset + limit]]
    return jsonify({
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "data": page,
    })


@bp.get("/summary")
def summary():
    records = cache.all_records()
    records = _filter(
        records,
        source=request.args.get("source"),
        state=request.args.get("state"),
        permit_type=request.args.get("permit_type"),
        status=request.args.get("status"),
        has_contractor=request.args.get("has_contractor", "").lower() in ("1", "true", "yes"),
        min_value=float(request.args["min_value"]) if request.args.get("min_value") else None,
        max_value=float(request.args["max_value"]) if request.args.get("max_value") else None,
    )

    # By city
    by_city: dict = defaultdict(list)
    for r in records:
        by_city[f"{r['city']}, {r['state']}"].append(r)

    # By permit type (top 20)
    by_type: dict = defaultdict(int)
    for r in records:
        by_type[r.get("permit_type") or "Unknown"] += 1

    # By status
    by_status: dict = defaultdict(int)
    for r in records:
        by_status[(r.get("status") or "Unknown").upper()] += 1

    # Value stats
    values = [r["project_value"] for r in records if r.get("project_value")]
    value_stats = {}
    if values:
        value_stats = {
            "count": len(values),
            "total": round(sum(values), 2),
            "avg": round(sum(values) / len(values), 2),
            "min": min(values),
            "max": max(values),
        }

    # Contractor coverage
    with_contractor = sum(1 for r in records if r.get("contractor"))

    return jsonify({
        "total_records": len(records),
        "with_contractor": with_contractor,
        "contractor_coverage_pct": round(with_contractor / len(records) * 100, 1) if records else 0,
        "value_stats": value_stats,
        "by_status": dict(sorted(by_status.items(), key=lambda x: -x[1])),
        "by_permit_type": dict(sorted(by_type.items(), key=lambda x: -x[1])[:20]),
        "by_city": {
            city: {
                "total": len(items),
                "with_contractor": sum(1 for r in items if r.get("contractor")),
                "top_permit_types": _top_types(items),
            }
            for city, items in sorted(by_city.items(), key=lambda x: -len(x[1]))
        },
    })


def _top_types(items: list, n: int = 5) -> list[dict]:
    counts: dict = defaultdict(int)
    for r in items:
        counts[r.get("permit_type") or "Unknown"] += 1
    return [{"type": t, "count": c} for t, c in sorted(counts.items(), key=lambda x: -x[1])[:n]]


@bp.get("/by-source")
def by_source():
    return jsonify({
        sid: {
            "city": SOURCES[sid]["city"],
            "state": SOURCES[sid]["state"],
            "total": len(cache.source(sid).records),
            "last_updated": cache.source(sid).last_updated.isoformat() if cache.source(sid).last_updated else None,
            "error": cache.source(sid).error,
            "records": [_drop_none(r) for r in cache.source(sid).records],
        }
        for sid in SOURCES
    })


@bp.get("/by-state")
def by_state():
    records = cache.all_records()
    grouped: dict = defaultdict(list)
    for r in records:
        grouped[r.get("state") or "UNKNOWN"].append(r)
    return jsonify({
        st: {
            "total": len(items),
            "cities": sorted({f"{r['city']}" for r in items}),
            "records": [_drop_none(r) for r in items],
        }
        for st, items in sorted(grouped.items())
    })


@bp.get("/<source_id>")
def get_source(source_id: str):
    if source_id not in SOURCES:
        return jsonify({
            "error": f"Fuente '{source_id}' no encontrada",
            "available": list(SOURCES.keys()),
        }), 404
    sc = cache.source(source_id)
    limit = min(int(request.args.get("limit", 200)), 5000)
    offset = int(request.args.get("offset", 0))
    records = sc.records
    records = _filter(
        records,
        permit_type=request.args.get("permit_type"),
        status=request.args.get("status"),
        has_contractor=request.args.get("has_contractor", "").lower() in ("1", "true", "yes"),
        min_value=float(request.args["min_value"]) if request.args.get("min_value") else None,
        max_value=float(request.args["max_value"]) if request.args.get("max_value") else None,
    )
    page = [_drop_none(r) for r in records[offset: offset + limit]]
    return jsonify({
        "source": source_id,
        "city": SOURCES[source_id]["city"],
        "state": SOURCES[source_id]["state"],
        "total": len(records),
        "offset": offset,
        "limit": limit,
        "last_updated": sc.last_updated.isoformat() if sc.last_updated else None,
        "error": sc.error,
        "data": page,
    })


@bp.post("/refresh")
def refresh_all():
    errors = cache.refresh_all(limit=int(request.args.get("limit", DEFAULT_FETCH)))
    return jsonify({
        "status": "ok",
        "sources": cache.statuses(),
        "errors": errors,
    })


@bp.post("/<source_id>/refresh")
def refresh_source(source_id: str):
    if source_id not in SOURCES:
        return jsonify({"error": f"Fuente '{source_id}' no encontrada"}), 404
    limit = int(request.args.get("limit", DEFAULT_FETCH))
    count = cache.source(source_id).refresh(limit=limit)
    sc = cache.source(source_id)
    return jsonify({
        "status": "ok",
        "source": source_id,
        "records_fetched": count,
        "last_updated": sc.last_updated.isoformat() if sc.last_updated else None,
    })
