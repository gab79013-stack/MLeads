"""
permits_importer.py — Importa permisos de construcción/demolición al sistema de leads de MLeads.

Convierte los registros normalizados de web/permits.py al esquema
consolidated_leads, registra las ciudades/agentes necesarios,
y otorga acceso a todos los usuarios existentes.
"""

import json
import logging
import re
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger("permits_importer")

DB_PATH = "data/leads.db"
AGENT_KEY = "permits"


# ── Utilidades ────────────────────────────────────────────────────────────────

def _norm_key(text: str) -> str:
    """Genera un address_key normalizado."""
    return re.sub(r"\s+", " ", str(text).lower().strip())


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ── Scoring de permisos ───────────────────────────────────────────────────────

def _score_permit(lead: dict) -> dict:
    """
    Calcula score usando lead_scoring cuando está disponible,
    con fallback simple orientado a permisos de construcción.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from utils.lead_scoring import score_lead
        return score_lead(lead)
    except Exception:
        pass

    # Fallback manual
    score = 0
    reasons = []
    value = lead.get("value_float") or 0

    if value >= 500_000:
        score += 20; reasons.append(f"Proyecto alto valor (${value:,.0f})")
    elif value >= 100_000:
        score += 12
    elif value > 0:
        score += 5

    desc = ((lead.get("description") or "") + " " +
            (lead.get("permit_type") or "")).lower()
    high = ["roof", "roofing", "drywall", "paint", "landscap", "electrical", "hvac", "solar"]
    med  = ["remodel", "addition", "adu", "demolit", "construct", "alteration"]
    if any(k in desc for k in high):
        score += 15; reasons.append("Tipo de proyecto de alto interés")
    elif any(k in desc for k in med):
        score += 10; reasons.append("Proyecto relacionado")
    else:
        score += 5

    if lead.get("contractor"):
        score += 4; reasons.append("Contratista identificado")
    if lead.get("owner"):
        score += 2

    # Recencia
    date_str = lead.get("issue_date") or ""
    if date_str:
        try:
            d = datetime.fromisoformat(str(date_str)[:10])
            days = (datetime.utcnow() - d).days
            if days <= 30:
                score += 15; reasons.append("Permiso reciente (< 30 días)")
            elif days <= 90:
                score += 8
            elif days <= 180:
                score += 4
        except Exception:
            pass

    score = max(0, min(100, int(score)))
    if score >= 75:
        grade, emoji = "HOT", "🔴"
    elif score >= 60:
        grade, emoji = "WARM", "🟠"
    elif score >= 45:
        grade, emoji = "MEDIUM", "🟡"
    elif score >= 30:
        grade, emoji = "COOL", "🔵"
    else:
        grade, emoji = "COLD", "⚫"

    return {"score": score, "grade": grade, "grade_emoji": emoji, "reasons": reasons}


# ── Importación principal ─────────────────────────────────────────────────────

def _ensure_city(conn: sqlite3.Connection, city_name: str, state: str) -> int:
    """Inserta la ciudad si no existe y retorna su ID."""
    conn.execute(
        "INSERT OR IGNORE INTO cities (name, state) VALUES (?, ?)",
        (city_name, state),
    )
    row = conn.execute("SELECT id FROM cities WHERE name = ?", (city_name,)).fetchone()
    return row["id"]


def _ensure_agent(conn: sqlite3.Connection) -> int:
    """Asegura que el agente 'permits' exista y retorna su ID."""
    conn.execute(
        "INSERT OR IGNORE INTO agents (name, description) VALUES (?, ?)",
        (AGENT_KEY, "Construction & Demolition Permits"),
    )
    row = conn.execute("SELECT id FROM agents WHERE name = ?", (AGENT_KEY,)).fetchone()
    return row["id"]


def _grant_access(conn: sqlite3.Connection, city_ids: set[int], agent_id: int):
    """Otorga acceso a todos los usuarios a las ciudades y agente de permisos."""
    users = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
    for uid in users:
        conn.execute(
            "INSERT OR IGNORE INTO user_agent_access (user_id, agent_id) VALUES (?, ?)",
            (uid, agent_id),
        )
        for cid in city_ids:
            conn.execute(
                "INSERT OR IGNORE INTO user_city_access (user_id, city_id) VALUES (?, ?)",
                (uid, cid),
            )


def import_permits(records: list[dict], batch_size: int = 500) -> dict:
    """
    Inserta/actualiza leads de permisos en consolidated_leads.

    Args:
        records: Lista de permisos normalizados de web/permits.py
        batch_size: Registros por transacción

    Returns:
        dict con stats: inserted, updated, skipped, errors
    """
    inserted = updated = skipped = errors = 0
    city_ids: set[int] = set()
    now = datetime.utcnow().isoformat()

    conn = _get_conn()
    try:
        agent_id = _ensure_agent(conn)
        conn.commit()

        for i in range(0, len(records), batch_size):
            batch = records[i : i + batch_size]
            try:
                with conn:
                    for r in batch:
                        address = (r.get("address") or "").strip()
                        city_name = (r.get("city") or "Unknown").strip()
                        state = (r.get("state") or "").strip()

                        if not address or not city_name:
                            skipped += 1
                            continue

                        # Registrar ciudad
                        cid = _ensure_city(conn, city_name, state)
                        city_ids.add(cid)

                        # address_key único: permiso_id si existe, o addr+city
                        permit_num = r.get("permit_number") or ""
                        if permit_num:
                            addr_key = _norm_key(
                                f"permit_{r['source']}_{permit_num}"
                            )
                        else:
                            addr_key = _norm_key(f"{address} {city_name}")

                        # Construir lead_data
                        lead_data: dict = {
                            "address":      address,
                            "city":         city_name,
                            "state":        state,
                            "source":       r.get("source"),
                            "permit_id":    r.get("permit_number"),
                            "permit_type":  r.get("permit_type"),
                            "description":  r.get("description"),
                            "phase":        r.get("status"),
                            "issue_date":   r.get("issue_date"),
                            "value_float":  r.get("project_value"),
                            "contractor":   r.get("contractor"),
                            "owner":        r.get("owner"),
                            "contact_phone": _extract_phone(r.get("contractor")),
                            "lat":          r.get("latitude"),
                            "lon":          r.get("longitude"),
                            "_agent_key":   AGENT_KEY,
                        }
                        # Limpiar nulos
                        lead_data = {k: v for k, v in lead_data.items() if v is not None}

                        # Scoring
                        scoring = _score_permit(lead_data)
                        lead_data["_scoring"] = scoring

                        has_contact = 1 if lead_data.get("contact_phone") else 0

                        existing_row = conn.execute(
                            "SELECT lead_data FROM consolidated_leads WHERE address_key = ?",
                            (addr_key,),
                        ).fetchone()

                        if existing_row:
                            # Merge: preserve AI classification fields from existing data
                            try:
                                existing_ld = json.loads(existing_row["lead_data"] or "{}")
                                for k in ("_trade", "_urgency", "_budget_min", "_budget_max",
                                          "_services", "_ai_summary", "_is_residential",
                                          "_is_commercial", "_owner_type", "_classifier_source",
                                          "_scoring"):
                                    if k in existing_ld:
                                        lead_data[k] = existing_ld[k]
                            except Exception:
                                pass

                        conn.execute("""
                            INSERT OR REPLACE INTO consolidated_leads
                            (address_key, address, city, agent_sources,
                             first_seen, last_updated, lead_data,
                             notified, primary_service_type, has_contact)
                            VALUES (?, ?, ?, ?,
                                COALESCE((SELECT first_seen FROM consolidated_leads WHERE address_key = ?), ?),
                                ?, ?, 0, ?, ?)
                        """, (
                            addr_key,
                            address,
                            city_name,
                            AGENT_KEY,
                            addr_key, now,
                            now,
                            json.dumps(lead_data, default=str),
                            AGENT_KEY,
                            has_contact,
                        ))

                        # Registrar señal en property_signals para cross-data
                        signal_data = {k: lead_data.get(k) for k in
                            ["contractor", "owner", "value_float", "permit_type",
                             "description", "issue_date", "contact_phone"]
                            if lead_data.get(k)}
                        conn.execute("""
                            INSERT OR REPLACE INTO property_signals
                            (address_key, agent_key, signal_type, signal_data, detected_at)
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            addr_key, AGENT_KEY,
                            r.get("source", AGENT_KEY),
                            json.dumps(signal_data, default=str), now,
                        ))

                        if exists:
                            updated += 1
                        else:
                            inserted += 1

                logger.info(f"Batch {i // batch_size + 1}: {len(batch)} records processed")
            except Exception as e:
                errors += len(batch)
                logger.error(f"Batch {i // batch_size + 1} error: {e}")

        # Otorgar acceso a usuarios
        _grant_access(conn, city_ids, agent_id)
        conn.commit()

    finally:
        conn.close()

    logger.info(
        f"Import complete: {inserted} inserted, {updated} updated, "
        f"{skipped} skipped, {errors} errors"
    )
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "cities_registered": len(city_ids),
    }


def _extract_phone(text: Optional[str]) -> Optional[str]:
    """Extrae un número de teléfono de texto libre (ej. campo contractor de Dallas)."""
    if not text:
        return None
    m = re.search(r"\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}", text)
    return m.group(0).strip() if m else None
