"""
utils/tdlr_db.py
━━━━━━━━━━━━━━━━
SQLite — almacenamiento de licencias TDLR

Tabla `tdlr_licenses`:
  - Histórico de todas las licencias vistas (activas e inactivas)
  - Upsert: actualiza si el registro ya existe (por license_number)
  - Índices en city, license_type, _trade para búsquedas rápidas

Se puede consultar directamente con:
  SELECT * FROM tdlr_licenses WHERE city='Dallas' AND _trade='ELECTRICAL'

También expone helpers para el bot y la API web:
  get_licenses_by_city(city, trade=None, limit=50)
  get_license_stats()
  search_licenses(query, city=None, trade=None, limit=20)
"""

import os
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/leads.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init_tdlr_db():
    """Crea la tabla tdlr_licenses si no existe."""
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tdlr_licenses (
                license_number          TEXT PRIMARY KEY,
                license_type            TEXT,
                license_status          TEXT,
                business_name           TEXT,
                title                   TEXT,
                address                 TEXT,
                city                    TEXT,
                state                   TEXT,
                zip                     TEXT,
                county                  TEXT,
                contact_phone           TEXT,
                issued_date             TEXT,
                expiration_date         TEXT,
                primary_service_type    TEXT,
                secondary_service_type  TEXT,
                description             TEXT,
                _trade                  TEXT,
                _ai_summary             TEXT,
                _score                  INTEGER DEFAULT 0,
                first_seen              TEXT,
                last_updated            TEXT
            )
        """)
        # Índices para consultas frecuentes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tdlr_city  ON tdlr_licenses(city)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tdlr_trade ON tdlr_licenses(_trade)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tdlr_type  ON tdlr_licenses(license_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tdlr_status ON tdlr_licenses(license_status)")
        conn.commit()
    logger.debug("[tdlr_db] Tabla tdlr_licenses lista")


# ── Escritura ─────────────────────────────────────────────────────────────────

def upsert_license(lead: dict):
    """
    Inserta o actualiza una licencia.
    Preserva `first_seen` si el registro ya existe.
    """
    now = datetime.utcnow().isoformat()
    lic_num = lead.get("license_number", "")
    if not lic_num:
        return

    scoring = lead.get("_scoring") or {}
    score   = scoring.get("score", 0)

    with _conn() as conn:
        # Verificar si ya existe para preservar first_seen
        row = conn.execute(
            "SELECT first_seen FROM tdlr_licenses WHERE license_number=?",
            (lic_num,)
        ).fetchone()
        first_seen = row["first_seen"] if row else now

        conn.execute("""
            INSERT INTO tdlr_licenses (
                license_number, license_type, license_status,
                business_name, title, address, city, state, zip, county,
                contact_phone, issued_date, expiration_date,
                primary_service_type, secondary_service_type, description,
                _trade, _ai_summary, _score, first_seen, last_updated
            ) VALUES (
                :license_number, :license_type, :license_status,
                :business_name, :title, :address, :city, :state, :zip, :county,
                :contact_phone, :issued_date, :expiration_date,
                :primary_service_type, :secondary_service_type, :description,
                :_trade, :_ai_summary, :_score, :first_seen, :last_updated
            )
            ON CONFLICT(license_number) DO UPDATE SET
                license_status          = excluded.license_status,
                business_name           = excluded.business_name,
                title                   = excluded.title,
                address                 = excluded.address,
                city                    = excluded.city,
                state                   = excluded.state,
                zip                     = excluded.zip,
                county                  = excluded.county,
                contact_phone           = excluded.contact_phone,
                issued_date             = excluded.issued_date,
                expiration_date         = excluded.expiration_date,
                primary_service_type    = excluded.primary_service_type,
                secondary_service_type  = excluded.secondary_service_type,
                description             = excluded.description,
                _trade                  = excluded._trade,
                _ai_summary             = excluded._ai_summary,
                _score                  = excluded._score,
                last_updated            = excluded.last_updated
        """, {
            "license_number":        lic_num,
            "license_type":          lead.get("license_type", ""),
            "license_status":        lead.get("license_status", ""),
            "business_name":         lead.get("business_name", ""),
            "title":                 lead.get("title", ""),
            "address":               lead.get("address", ""),
            "city":                  lead.get("city", ""),
            "state":                 lead.get("state", "TX"),
            "zip":                   lead.get("zip", ""),
            "county":                lead.get("county", ""),
            "contact_phone":         lead.get("contact_phone", ""),
            "issued_date":           lead.get("issued_date", ""),
            "expiration_date":       lead.get("expiration_date", ""),
            "primary_service_type":  lead.get("primary_service_type", ""),
            "secondary_service_type":lead.get("secondary_service_type", ""),
            "description":           lead.get("description", ""),
            "_trade":                lead.get("_trade", "GENERAL"),
            "_ai_summary":           lead.get("_ai_summary", ""),
            "_score":                score,
            "first_seen":            first_seen,
            "last_updated":          now,
        })
        conn.commit()


# ── Lectura ───────────────────────────────────────────────────────────────────

def get_licenses_by_city(city: str, trade: str | None = None, limit: int = 50) -> list[dict]:
    """Retorna licencias activas para una ciudad, opcionalmente filtradas por trade."""
    with _conn() as conn:
        if trade:
            rows = conn.execute(
                """SELECT * FROM tdlr_licenses
                   WHERE city=? AND _trade=? AND license_status='Active'
                   ORDER BY _score DESC, last_updated DESC LIMIT ?""",
                (city.title(), trade.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM tdlr_licenses
                   WHERE city=? AND license_status='Active'
                   ORDER BY _score DESC, last_updated DESC LIMIT ?""",
                (city.title(), limit),
            ).fetchall()
    return [dict(r) for r in rows]


def get_license_stats() -> dict:
    """Estadísticas globales de la tabla tdlr_licenses."""
    with _conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM tdlr_licenses").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM tdlr_licenses WHERE license_status='Active'"
        ).fetchone()[0]
        by_trade = conn.execute(
            "SELECT _trade, COUNT(*) as cnt FROM tdlr_licenses "
            "WHERE license_status='Active' GROUP BY _trade ORDER BY cnt DESC"
        ).fetchall()
        by_city = conn.execute(
            "SELECT city, COUNT(*) as cnt FROM tdlr_licenses "
            "WHERE license_status='Active' GROUP BY city ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
    return {
        "total":    total,
        "active":   active,
        "by_trade": {r["_trade"]: r["cnt"] for r in by_trade},
        "by_city":  {r["city"]: r["cnt"] for r in by_city},
    }


def search_licenses(query: str, city: str | None = None, trade: str | None = None, limit: int = 20) -> list[dict]:
    """
    Busca licencias por nombre de negocio, número de licencia o tipo.
    Soporta filtro opcional por ciudad y/o trade.
    """
    like = f"%{query.upper()}%"
    clauses = [
        "license_status='Active'",
        "(upper(business_name) LIKE ? OR upper(license_number) LIKE ? OR upper(license_type) LIKE ?)",
    ]
    params: list = [like, like, like]

    if city:
        clauses.append("city=?")
        params.append(city.title())
    if trade:
        clauses.append("_trade=?")
        params.append(trade.upper())

    params.append(limit)
    sql = (
        "SELECT * FROM tdlr_licenses WHERE "
        + " AND ".join(clauses)
        + " ORDER BY _score DESC LIMIT ?"
    )

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_expiring_soon(days: int = 30, city: str | None = None) -> list[dict]:
    """
    Retorna licencias activas que vencen en los próximos `days` días.
    Útil para detectar contratistas que pronto necesitarán renovar.
    """
    from datetime import timedelta
    cutoff = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")
    today  = datetime.utcnow().strftime("%Y-%m-%d")

    clauses = [
        "license_status='Active'",
        "expiration_date >= ?",
        "expiration_date <= ?",
    ]
    params: list = [today, cutoff]

    if city:
        clauses.append("city=?")
        params.append(city.title())

    sql = (
        "SELECT * FROM tdlr_licenses WHERE "
        + " AND ".join(clauses)
        + " ORDER BY expiration_date ASC LIMIT 100"
    )

    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
