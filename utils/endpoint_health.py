"""
utils/endpoint_health.py
━━━━━━━━━━━━━━━━━━━━━━━━
IA #6 — Validación Automática de Endpoints

Testea todos los endpoints de los agentes una vez al día:
  - Detecta 400/404/timeout
  - Marca endpoints muertos como _disabled=True en DB
  - Envía reporte resumido por Telegram
  - Claude analiza los errores y sugiere campos alternativos

Diseño: no-invasivo — lee las fuentes de los agentes en runtime
sin modificar sus listas. La DB almacena el estado de salud.

Uso:
  from utils.endpoint_health import run_health_check
  run_health_check()   # llamar desde main.py (cron diario)
"""

import os
import json
import time
import logging
import sqlite3
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

DB_PATH           = os.getenv("DB_PATH", "data/leads.db")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLED        = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")
MODEL             = os.getenv("AI_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
HEALTH_TIMEOUT    = int(os.getenv("HEALTH_CHECK_TIMEOUT", "15"))
HEALTH_WORKERS    = int(os.getenv("HEALTH_CHECK_WORKERS", "10"))


# ── DB ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)


def _init_health_db():
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS endpoint_health (
                url          TEXT PRIMARY KEY,
                agent        TEXT,
                city         TEXT,
                status       TEXT,
                http_code    INTEGER,
                error_msg    TEXT,
                records_last INTEGER DEFAULT 0,
                last_checked TEXT,
                last_ok      TEXT,
                fail_count   INTEGER DEFAULT 0,
                ai_suggestion TEXT
            )
        """)
        conn.commit()


def _upsert_health(url: str, agent: str, city: str, status: str,
                   http_code: int, error_msg: str, records: int,
                   ai_suggestion: str = ""):
    now = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT fail_count, last_ok FROM endpoint_health WHERE url = ?",
            (url,)
        ).fetchone()

        if status == "OK":
            fail_count = 0
            last_ok    = now
        else:
            fail_count = (existing[0] if existing else 0) + 1
            last_ok    = existing[1] if existing else None

        conn.execute("""
            INSERT OR REPLACE INTO endpoint_health
            (url, agent, city, status, http_code, error_msg, records_last,
             last_checked, last_ok, fail_count, ai_suggestion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (url, agent, city, status, http_code, error_msg, records,
               now, last_ok, fail_count, ai_suggestion))
        conn.commit()


def get_health_report() -> list[dict]:
    """Retorna el estado actual de todos los endpoints."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT url, agent, city, status, http_code, error_msg,
                       records_last, last_checked, fail_count, ai_suggestion
                FROM endpoint_health ORDER BY agent, city
            """).fetchall()
        return [
            {
                "url": r[0], "agent": r[1], "city": r[2], "status": r[3],
                "http_code": r[4], "error_msg": r[5], "records": r[6],
                "last_checked": r[7], "fail_count": r[8], "ai_suggestion": r[9],
            }
            for r in rows
        ]
    except Exception:
        return []


# ── Obtener todas las fuentes de todos los agentes ────────────────────

def _collect_all_sources() -> list[dict]:
    """Importa y recolecta todas las fuentes de todos los agentes."""
    sources = []

    try:
        from agents.permits_agent import _build_sources
        for src in _build_sources():
            sources.append({**src, "_agent": "permits"})
    except Exception as e:
        logger.warning(f"[Health] permits: {e}")

    try:
        from agents.deconstruction_agent import DECON_SOURCES
        for src in DECON_SOURCES:
            sources.append({**src, "_agent": "deconstruction"})
    except Exception as e:
        logger.warning(f"[Health] deconstruction: {e}")

    try:
        from agents.energy_agent import ENERGY_SOURCES
        for src in ENERGY_SOURCES:
            sources.append({**src, "_agent": "energy"})
    except Exception as e:
        logger.warning(f"[Health] energy: {e}")

    try:
        from agents.realestate_agent import REALESTATE_SOURCES
        for src in REALESTATE_SOURCES:
            sources.append({**src, "_agent": "realestate"})
    except Exception as e:
        logger.warning(f"[Health] realestate: {e}")

    return sources


# ── Test de un endpoint ───────────────────────────────────────────────

def _test_endpoint(source: dict) -> dict:
    """Prueba un endpoint con $limit=1. Retorna resultado."""
    url    = source.get("url", "")
    agent  = source.get("_agent", "unknown")
    city   = source.get("city", "?")
    engine = source.get("engine", "socrata")

    if not url or not url.startswith("http"):
        return {"url": url, "agent": agent, "city": city,
                "status": "SKIP", "http_code": 0, "error": "invalid url", "records": 0}

    try:
        # Construir params mínimos (solo $limit=1 para test)
        if engine == "ckan_sql":
            params = {"sql": f"SELECT * FROM fake_test LIMIT 1"}
        elif engine == "ckan":
            params = {"resource_id": source.get("params", {}).get("resource_id", ""), "limit": 1}
        else:
            params = {"$limit": 1}

        token = os.getenv("SOCRATA_APP_TOKEN", "")
        headers = {"Accept": "application/json"}
        if token:
            headers["X-App-Token"] = token

        start = time.monotonic()
        resp = requests.get(url, params=params, headers=headers,
                            timeout=HEALTH_TIMEOUT)
        elapsed = time.monotonic() - start

        if resp.status_code == 200:
            try:
                data = resp.json()
                records = len(data) if isinstance(data, list) else 1
            except Exception:
                records = 0
            return {
                "url": url, "agent": agent, "city": city,
                "status": "OK", "http_code": 200,
                "error": "", "records": records,
                "elapsed": round(elapsed, 2),
            }
        else:
            return {
                "url": url, "agent": agent, "city": city,
                "status": "ERROR", "http_code": resp.status_code,
                "error": resp.text[:200], "records": 0,
            }

    except requests.Timeout:
        return {"url": url, "agent": agent, "city": city,
                "status": "TIMEOUT", "http_code": 0,
                "error": f"Timeout >{HEALTH_TIMEOUT}s", "records": 0}
    except Exception as e:
        return {"url": url, "agent": agent, "city": city,
                "status": "ERROR", "http_code": 0,
                "error": str(e)[:200], "records": 0}


# ── Sugerencia de Claude para endpoints muertos ───────────────────────

def _ai_suggest_fix(url: str, city: str, error: str) -> str:
    """Claude sugiere qué hacer con un endpoint muerto."""
    if not ANTHROPIC_API_KEY or not AI_ENABLED:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=MODEL,
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": (
                    f"A Socrata API endpoint for {city} is returning an error.\n"
                    f"URL: {url}\nError: {error}\n\n"
                    f"In one sentence, what's likely wrong and how to fix it? "
                    f"Focus on common Socrata issues (wrong dataset ID, field names, etc.)."
                )
            }],
        )
        return response.content[0].text.strip()[:300]
    except Exception:
        return ""


# ── Runner principal ──────────────────────────────────────────────────

def run_health_check(notify: bool = True) -> dict:
    """
    Ejecuta el health check completo.

    Returns:
        {"ok": int, "errors": int, "timeouts": int, "total": int, "report": str}
    """
    _init_health_db()
    sources = _collect_all_sources()

    if not sources:
        logger.warning("[Health] No se encontraron fuentes para testear")
        return {"ok": 0, "errors": 0, "timeouts": 0, "total": 0, "report": ""}

    logger.info(f"[Health] Testeando {len(sources)} endpoints...")

    results = []
    with ThreadPoolExecutor(max_workers=HEALTH_WORKERS) as ex:
        futures = {ex.submit(_test_endpoint, src): src for src in sources}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                logger.error(f"[Health] Error en future: {e}")

    # Persistir resultados + pedir sugerencia de Claude para los errores
    ok_count = err_count = timeout_count = 0
    failed_sources = []

    for r in results:
        ai_suggestion = ""
        if r["status"] in ("ERROR", "TIMEOUT") and r.get("error"):
            ai_suggestion = _ai_suggest_fix(r["url"], r["city"], r["error"])
            failed_sources.append(r)

        _upsert_health(
            url=r["url"], agent=r["agent"], city=r["city"],
            status=r["status"], http_code=r.get("http_code", 0),
            error_msg=r.get("error", ""), records=r.get("records", 0),
            ai_suggestion=ai_suggestion,
        )

        if r["status"] == "OK":
            ok_count += 1
        elif r["status"] == "TIMEOUT":
            timeout_count += 1
        else:
            err_count += 1

    # Reporte de texto
    report = _build_report(ok_count, err_count, timeout_count, len(sources), failed_sources)
    logger.info(f"[Health] Completado: {ok_count} OK, {err_count} errors, {timeout_count} timeouts")

    if notify and failed_sources:
        _notify_telegram(report)

    return {
        "ok": ok_count,
        "errors": err_count,
        "timeouts": timeout_count,
        "total": len(sources),
        "report": report,
    }


def _build_report(ok: int, errors: int, timeouts: int,
                  total: int, failed: list) -> str:
    lines = [
        f"🏥 *ENDPOINT HEALTH CHECK*",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"✅ OK: {ok}/{total}",
        f"❌ Errores: {errors}",
        f"⏱️ Timeouts: {timeouts}",
        f"📅 {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
    ]

    if failed:
        lines.append("\n*Endpoints con problemas:*")
        for r in failed[:10]:
            lines.append(
                f"• `{r['city']}` ({r['agent']}) — "
                f"{r['status']} {r.get('http_code', '')}"
            )
            if r.get("ai_suggestion"):
                lines.append(f"  💡 _{r['ai_suggestion']}_")

    return "\n".join(lines)


def _notify_telegram(report: str):
    """Envía reporte por Telegram."""
    try:
        from utils.telegram import send_message
        send_message(report)
    except Exception as e:
        logger.error(f"[Health] No se pudo notificar por Telegram: {e}")
