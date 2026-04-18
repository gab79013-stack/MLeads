"""
agents/crossdata_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━
🔮 Cross-Data Prediction Agent

Correlaciona señales de TODOS los agentes activos para predecir:
  1. Qué propiedades necesitarán un subcontratista pronto
  2. Qué fase de construcción viene después
  3. Score de confianza compuesto por señales cruzadas

Lógica de predicción:
  ──────────────────────────────────────────────────────────────
  señal permits  +  señal construction  →  fase activa conocida
  señal permits  +  señal realestate    →  compra + remodelación inminente
  señal permits  +  señal energy        →  upgrade energético en marcha
  señal solar    +  señal construction  →  instalación solar en proyecto nuevo
  señal rodents  +  señal permits       →  renovación post-plaga
  señal flood    +  señal permits       →  reparación de daños activa
  señal realestate  (reciente)          →  flip / remodelación probable
  valor permiso alto + fase temprana    →  proyecto grande, sub necesario pronto
  ──────────────────────────────────────────────────────────────

Salida:
  - Actualiza `lead_data._cross_prediction` en consolidated_leads
  - Inserta predicciones de inspección en scheduled_inspections
  - Crea nuevos leads cross-agent donde la dedup no los ha detectado aún
  - Registra las predicciones en property_signals con agent_key='crossdata'

Parámetros de entorno:
  AGENT_CROSSDATA=true/false   (default: true)
  CROSSDATA_MIN_SIGNALS=2      señales mínimas para activar predicción
  CROSSDATA_INTERVAL=360       minutos entre corridas (default: 6h)
"""

import json
import logging
import os
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from agents.base import BaseAgent
from utils.inspection_predictor import predict_next_inspection, estimate_gc_presence
from utils.lead_scoring import score_lead

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/leads.db")
MIN_SIGNALS = int(os.getenv("CROSSDATA_MIN_SIGNALS", "2"))


# ── Pesos de señal por agente ─────────────────────────────────────────────────
# Cuánto "valor predictivo" aporta cada agente al score cruzado
AGENT_WEIGHTS = {
    "construction":  3.0,   # inspección activa = máxima certeza
    "permits":       2.5,   # permiso emitido = trabajo inminente
    "deconstruction":2.0,   # demolición → reconstrucción
    "realestate":    2.0,   # venta reciente → remodelación probable
    "flood":         1.8,   # daño por agua → reparación urgente
    "solar":         1.5,   # señal de mejora de propiedad
    "energy":        1.2,   # audit energético → upgrades
    "rodents":       1.0,   # señal de deterioro / renovación
    "weather":       0.8,   # evento climático → reparación
    "crossdata":     0.0,   # no se cuenta a sí mismo
}

# ── Combinaciones de señales → predicciones ───────────────────────────────────
COMBOS = [
    {
        "agents": {"construction", "permits"},
        "label":  "Construcción activa con permiso",
        "trades": ["ROOFING", "ELECTRICAL", "DRYWALL", "FRAMING"],
        "urgency": "HIGH",
        "confidence_boost": 25,
    },
    {
        "agents": {"construction", "deconstruction"},
        "label":  "Demolición + reconstrucción",
        "trades": ["GENERAL", "CONCRETE", "FRAMING"],
        "urgency": "HIGH",
        "confidence_boost": 20,
    },
    {
        "agents": {"permits", "realestate"},
        "label":  "Compra reciente + permiso emitido",
        "trades": ["ROOFING", "PAINTING", "LANDSCAPING", "DRYWALL"],
        "urgency": "HIGH",
        "confidence_boost": 22,
    },
    {
        "agents": {"permits", "flood"},
        "label":  "Reparación de daños + permiso",
        "trades": ["ROOFING", "DRYWALL", "PLUMBING", "ELECTRICAL"],
        "urgency": "HIGH",
        "confidence_boost": 28,
    },
    {
        "agents": {"construction", "solar"},
        "label":  "Proyecto nuevo con solar",
        "trades": ["ELECTRICAL", "ROOFING"],
        "urgency": "MEDIUM",
        "confidence_boost": 18,
    },
    {
        "agents": {"permits", "energy"},
        "label":  "Upgrade energético con permiso",
        "trades": ["HVAC", "INSULATION", "ELECTRICAL"],
        "urgency": "MEDIUM",
        "confidence_boost": 15,
    },
    {
        "agents": {"rodents", "permits"},
        "label":  "Renovación post-plaga",
        "trades": ["DRYWALL", "INSULATION", "PAINTING"],
        "urgency": "MEDIUM",
        "confidence_boost": 12,
    },
    {
        "agents": {"realestate", "energy"},
        "label":  "Propiedad vendida con audit energético",
        "trades": ["HVAC", "INSULATION", "WINDOWS"],
        "urgency": "MEDIUM",
        "confidence_boost": 10,
    },
    {
        "agents": {"deconstruction", "energy"},
        "label":  "Demolición + señal energética",
        "trades": ["GENERAL", "HVAC", "INSULATION"],
        "urgency": "MEDIUM",
        "confidence_boost": 14,
    },
    {
        "agents": {"construction", "realestate"},
        "label":  "Flip activo en construcción",
        "trades": ["ROOFING", "FLOORING", "PAINTING", "LANDSCAPING"],
        "urgency": "HIGH",
        "confidence_boost": 20,
    },
]


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).lower().strip())


def _load_property_signals() -> dict[str, dict]:
    """
    Carga todas las señales agrupadas por address_key.
    Retorna: { address_key: { agent_key: signal_data, ... } }
    """
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT address_key, agent_key, signal_data, detected_at FROM property_signals"
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, dict] = defaultdict(dict)
    for r in rows:
        try:
            sd = json.loads(r["signal_data"] or "{}")
        except Exception:
            sd = {}
        grouped[r["address_key"]][r["agent_key"]] = {
            "data": sd,
            "detected_at": r["detected_at"],
        }
    return dict(grouped)


def _load_consolidated(address_keys: list[str]) -> dict[str, dict]:
    """Carga leads consolidados para las address_keys dadas."""
    if not address_keys:
        return {}
    conn = _get_conn()
    try:
        ph = ",".join("?" * len(address_keys))
        rows = conn.execute(
            f"SELECT address_key, address, city, agent_sources, lead_data FROM consolidated_leads WHERE address_key IN ({ph})",
            address_keys,
        ).fetchall()
    finally:
        conn.close()
    return {r["address_key"]: dict(r) for r in rows}


def _match_combo(agent_keys: set[str]) -> Optional[dict]:
    """Devuelve el combo de mayor confianza que aplique a este conjunto de agentes."""
    best = None
    best_score = 0
    for combo in COMBOS:
        if combo["agents"].issubset(agent_keys):
            if combo["confidence_boost"] > best_score:
                best = combo
                best_score = combo["confidence_boost"]
    return best


def _compute_cross_score(agent_keys: set[str], lead_data: dict, combo: Optional[dict]) -> int:
    """Calcula score cruzado combinando base score + señales extra."""
    base = lead_data.get("_scoring", {}).get("score", 0) or 0
    signal_bonus = sum(AGENT_WEIGHTS.get(a, 0.5) * 5 for a in agent_keys if a != "crossdata")
    combo_bonus = combo["confidence_boost"] if combo else 0
    return min(100, int(base + signal_bonus + combo_bonus))


def _correlate_contractor_permits() -> int:
    """
    Detecta contratistas que aparecen en múltiples fuentes/ciudades de permisos.
    Crea/actualiza un lead por contratista con el historial consolidado.
    Retorna cuántos leads de contratista se crearon/actualizaron.
    """
    conn = _get_conn()
    try:
        # Permisos con contratista identificado
        rows = conn.execute("""
            SELECT address_key, address, city,
                   json_extract(lead_data, '$.contractor')   AS contractor,
                   json_extract(lead_data, '$.contact_phone') AS phone,
                   json_extract(lead_data, '$.source')       AS source,
                   json_extract(lead_data, '$.value_float')  AS val,
                   json_extract(lead_data, '$.permit_type')  AS ptype,
                   json_extract(lead_data, '$.issue_date')   AS issued
            FROM consolidated_leads
            WHERE agent_sources = 'permits'
              AND json_extract(lead_data, '$.contractor') IS NOT NULL
              AND json_extract(lead_data, '$.contractor') != ''
        """).fetchall()
    finally:
        conn.close()

    # Agrupar por contratista normalizado
    by_contractor: dict = defaultdict(list)
    for r in rows:
        raw_name = (r["contractor"] or "").strip()
        # Normalizar: extraer nombre antes de dirección/teléfono
        name = re.split(r"\s{2,}|\d{3}[-.\s]\d{3}", raw_name)[0].strip().upper()
        if len(name) < 4:
            continue
        by_contractor[name].append(dict(r))

    # Solo contratistas con ≥2 permisos
    multi_contractors = {k: v for k, v in by_contractor.items() if len(v) >= 2}
    if not multi_contractors:
        return 0

    conn = _get_conn()
    upserted = 0
    now = datetime.utcnow().isoformat()
    try:
        for name, permits in multi_contractors.items():
            addr_key = _norm_key(f"contractor_{name}")
            cities = list({p["city"] for p in permits if p["city"]})
            sources = list({p["source"] for p in permits if p["source"]})
            phones = [p["phone"] for p in permits if p.get("phone")]
            total_val = sum(float(p["val"] or 0) for p in permits)
            types = list({p["ptype"] for p in permits if p["ptype"]})

            lead_data = {
                "contractor":     name,
                "address":        f"Multi-city contractor: {', '.join(cities[:3])}",
                "city":           cities[0] if cities else "",
                "contact_phone":  phones[0] if phones else "",
                "value_float":    total_val,
                "permit_type":    " | ".join(types[:3]),
                "description":    f"Contratista activo en {len(permits)} permisos en {len(cities)} ciudades",
                "permit_count":   len(permits),
                "active_cities":  cities,
                "permit_sources": sources,
                "_agent_key":     "crossdata",
                "_is_contractor_profile": True,
            }
            scoring = _score_contractor(lead_data, permits)
            lead_data["_scoring"] = scoring
            has_contact = 1 if lead_data.get("contact_phone") else 0

            conn.execute("""
                INSERT OR REPLACE INTO consolidated_leads
                (address_key, address, city, agent_sources,
                 first_seen, last_updated, lead_data, notified,
                 primary_service_type, has_contact)
                VALUES (?,?,?,?,
                    COALESCE((SELECT first_seen FROM consolidated_leads WHERE address_key=?), ?),
                    ?,?,0,'crossdata',?)
            """, (
                addr_key,
                lead_data["address"],
                lead_data["city"],
                "crossdata",
                addr_key, now, now,
                json.dumps(lead_data, default=str),
                has_contact,
            ))
            upserted += 1

        conn.commit()
    finally:
        conn.close()

    logger.info(f"[CrossData] Contractor profiles: {upserted} upserted")
    return upserted


def _score_contractor(lead: dict, permits: list) -> dict:
    """Score de contratista basado en volumen y recencia de permisos."""
    score = min(40, len(permits) * 5)           # hasta 40 pts por volumen
    total_val = lead.get("value_float", 0) or 0
    if total_val >= 1_000_000: score += 25
    elif total_val >= 500_000: score += 20
    elif total_val >= 100_000: score += 12
    elif total_val > 0:        score += 5
    if lead.get("contact_phone"): score += 10
    score = min(100, score)
    grade, emoji = _grade(score)
    return {
        "score":       score,
        "grade":       grade,
        "grade_emoji": emoji,
        "reasons":     [
            f"📋 {len(permits)} permisos activos",
            f"🏙️ {len(lead.get('active_cities', []))} ciudades",
            *(["📞 Teléfono disponible"] if lead.get("contact_phone") else []),
        ],
    }


def run_cross_prediction() -> dict:
    """
    Corre el ciclo completo de predicción cross-data.
    Retorna stats del ciclo.
    """
    logger.info("[CrossData] Iniciando ciclo de predicción cross-data...")

    signals_map = _load_property_signals()
    logger.info(f"[CrossData] {len(signals_map)} propiedades con señales")

    # Solo procesar propiedades con ≥ MIN_SIGNALS agentes distintos
    multi = {
        k: v for k, v in signals_map.items()
        if len({a for a in v if a != "crossdata"}) >= MIN_SIGNALS
    }
    logger.info(f"[CrossData] {len(multi)} propiedades con ≥{MIN_SIGNALS} señales")

    if not multi:
        return {"processed": 0, "updated": 0, "new_leads": 0, "inspections": 0}

    consolidated = _load_consolidated(list(multi.keys()))

    conn = _get_conn()
    updated = new_leads = inspections = 0
    now = datetime.utcnow().isoformat()

    try:
        for addr_key, agent_map in multi.items():
            agent_keys = {a for a in agent_map if a != "crossdata"}
            combo = _match_combo(agent_keys)

            # Obtener o reconstruir lead_data
            existing = consolidated.get(addr_key)
            if existing:
                try:
                    lead_data = json.loads(existing["lead_data"] or "{}")
                except Exception:
                    lead_data = {}
                address = existing["address"]
                city = existing["city"]
            else:
                # Reconstruir desde señales
                lead_data = {}
                address = addr_key
                city = ""
                for ag, sig in agent_map.items():
                    d = sig.get("data", {})
                    if not address or address == addr_key:
                        address = d.get("address", addr_key)
                    if not city:
                        city = d.get("city", "")
                    for k, v in d.items():
                        if k not in lead_data and v:
                            lead_data[k] = v

            # ── Construir predicción cross-data ──────────────────────
            cross = {
                "agent_signals": sorted(agent_keys),
                "signal_count": len(agent_keys),
                "combo_matched": combo["label"] if combo else None,
                "predicted_trades": combo["trades"] if combo else [],
                "urgency": combo["urgency"] if combo else "LOW",
                "computed_at": now,
            }

            # Predicción de inspección (usa datos de construction si disponible)
            construction_data = agent_map.get("construction", {}).get("data", {})
            if construction_data or lead_data.get("phase"):
                merged_for_pred = {**lead_data, **construction_data}
                insp_pred = predict_next_inspection(merged_for_pred)
                if insp_pred:
                    cross["next_inspection"] = insp_pred
                    try:
                        from datetime import date as _date
                        insp_date_obj = _date.fromisoformat(
                            str(insp_pred.get("predicted_date") or insp_pred.get("date", ""))[:10]
                        )
                        gc_prob = estimate_gc_presence(
                            merged_for_pred,
                            insp_date_obj,
                            insp_pred.get("inspection_type") or insp_pred.get("type"),
                        )
                    except Exception:
                        gc_prob = 0.5
                    cross["gc_presence_probability"] = gc_prob

                    # Guardar en scheduled_inspections
                    insp_date = insp_pred.get("predicted_date") or insp_pred.get("date", "")
                    insp_type = insp_pred.get("inspection_type") or insp_pred.get("type", "")
                    if insp_date and insp_type:
                        try:
                            conn.execute("""
                                INSERT OR REPLACE INTO scheduled_inspections
                                (address_key, address, inspection_date, inspection_type,
                                 gc_presence_probability, created_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                addr_key, address, insp_date, insp_type,
                                gc_prob, now,
                            ))
                            inspections += 1
                        except Exception as e:
                            logger.debug(f"[CrossData] inspection insert: {e}")

            # Score cruzado mejorado
            new_score = _compute_cross_score(agent_keys, lead_data, combo)
            if new_score > (lead_data.get("_scoring", {}).get("score") or 0):
                scoring = lead_data.get("_scoring") or {}
                scoring["score"] = new_score
                scoring["grade"], scoring["grade_emoji"] = _grade(new_score)
                reasons = scoring.get("reasons") or []
                if combo:
                    reasons = [f"🔗 Cross-signal: {combo['label']}"] + reasons
                reasons = [f"📡 {len(agent_keys)} fuentes: {', '.join(sorted(agent_keys))}"] + reasons
                scoring["reasons"] = reasons[:5]
                lead_data["_scoring"] = scoring
                cross["boosted_score"] = new_score

            lead_data["_cross_prediction"] = cross
            lead_data["_gc_presence_probability"] = cross.get("gc_presence_probability", 0)

            has_contact = 1 if (
                (lead_data.get("contact_phone") or "").strip()
                or (lead_data.get("contact_email") or "").strip()
            ) else 0

            all_agents = sorted(agent_keys)

            if existing:
                conn.execute("""
                    UPDATE consolidated_leads
                    SET lead_data=?, last_updated=?, agent_sources=?, has_contact=?,
                        primary_service_type=?
                    WHERE address_key=?
                """, (
                    json.dumps(lead_data, default=str),
                    now,
                    ",".join(all_agents),
                    has_contact,
                    all_agents[0] if all_agents else "crossdata",
                    addr_key,
                ))
                updated += 1
            else:
                conn.execute("""
                    INSERT OR IGNORE INTO consolidated_leads
                    (address_key, address, city, agent_sources,
                     first_seen, last_updated, lead_data, notified,
                     primary_service_type, has_contact)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """, (
                    addr_key, address, city,
                    ",".join(all_agents),
                    now, now,
                    json.dumps(lead_data, default=str),
                    all_agents[0] if all_agents else "crossdata",
                    has_contact,
                ))
                new_leads += 1

            # Registrar señal del agente crossdata
            conn.execute("""
                INSERT OR REPLACE INTO property_signals
                (address_key, agent_key, signal_type, signal_data, detected_at)
                VALUES (?, 'crossdata', 'cross_prediction', ?, ?)
            """, (
                addr_key,
                json.dumps({
                    "combo": combo["label"] if combo else None,
                    "agents": sorted(agent_keys),
                    "score": new_score,
                    "trades": combo["trades"] if combo else [],
                }, default=str),
                now,
            ))

        conn.commit()
    finally:
        conn.close()

    # Correlación por contratista multi-fuente
    contractor_profiles = _correlate_contractor_permits()

    stats = {
        "processed":            len(multi),
        "updated":              updated,
        "new_leads":            new_leads,
        "inspections":          inspections,
        "contractor_profiles":  contractor_profiles,
    }
    logger.info(f"[CrossData] Ciclo completado: {stats}")
    return stats


def _grade(score: int) -> tuple[str, str]:
    if score >= 75: return "HOT",    "🔴"
    if score >= 60: return "WARM",   "🟠"
    if score >= 45: return "MEDIUM", "🟡"
    if score >= 30: return "COOL",   "🔵"
    return             "COLD",   "⚫"


# ── Integración con el sistema de agentes ─────────────────────────────────────

class CrossDataAgent(BaseAgent):
    """
    Agente de predicción cross-data.
    Corre cada CROSSDATA_INTERVAL minutos (default: 360 = 6h).
    """
    name      = "🔮 CrossData Prediction Agent"
    emoji     = "🔮"
    agent_key = "crossdata"

    def __init__(self):
        super().__init__()

    def fetch_leads(self) -> list:
        stats = run_cross_prediction()
        # Retornar las propiedades actualizadas como leads para logging
        conn = _get_conn()
        try:
            rows = conn.execute("""
                SELECT address_key, address, city, lead_data
                FROM consolidated_leads
                WHERE lead_data LIKE '%_cross_prediction%'
                  AND json_extract(lead_data, '$._cross_prediction.urgency') = 'HIGH'
                ORDER BY CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) DESC
                LIMIT 20
            """).fetchall()
        finally:
            conn.close()

        leads = []
        for r in rows:
            try:
                ld = json.loads(r["lead_data"] or "{}")
            except Exception:
                ld = {}
            cross = ld.get("_cross_prediction", {})
            leads.append({
                "address":     r["address"],
                "city":        r["city"],
                "address_key": r["address_key"],
                "score":       ld.get("_scoring", {}).get("score", 0),
                "agents":      cross.get("agent_signals", []),
                "combo":       cross.get("combo_matched"),
                "trades":      cross.get("predicted_trades", []),
                "urgency":     cross.get("urgency", "LOW"),
                "contact_phone": ld.get("contact_phone", ""),
                "_agent_key":  "crossdata",
                "_scoring":    ld.get("_scoring", {}),
                "_stats":      stats,
            })
        return leads

    def notify(self, lead: dict):
        if lead.get("urgency") != "HIGH":
            return
        agents_str = " + ".join(lead.get("agents", []))
        trades_str = ", ".join(lead.get("trades", [])[:3])
        score = lead.get("score", 0)
        combo = lead.get("combo", "Cross-signal")
        msg = (
            f"🔮 *CrossData HOT Lead* — Score {score}\n"
            f"📍 {lead.get('address', '')}\n"
            f"🏙️ {lead.get('city', '')}\n"
            f"🔗 {combo}\n"
            f"📡 Señales: {agents_str}\n"
            f"🔧 Trades: {trades_str}\n"
            + (f"📞 {lead['contact_phone']}\n" if lead.get("contact_phone") else "")
        )
        from utils.telegram import send_message
        try:
            send_message(msg)
        except Exception:
            pass
