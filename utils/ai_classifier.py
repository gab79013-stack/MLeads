"""
utils/ai_classifier.py
━━━━━━━━━━━━━━━━━━━━━━
IA #1 — Clasificador de Trade con Claude (Haiku)

Analiza la descripción de un permiso y extrae:
  - trade:         qué sub-contractor se necesita
  - urgency:       HIGH / MEDIUM / LOW
  - budget_range:  rango estimado en USD
  - services:      lista específica de servicios
  - summary:       pitch listo para el sub-contractor

Usa claude-haiku (rápido y económico) con prompt caching.
Costo estimado: ~$0.0003 por lead clasificado.

Graceful degradation: si no hay API key, retorna clasificación
rule-based local (sin coste, sin red).
"""

import os
import json
import logging
import hashlib
from functools import lru_cache

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_ENABLED        = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")
MODEL             = os.getenv("AI_CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")

# Cache en memoria: evita re-clasificar el mismo texto
_cache: dict[str, dict] = {}

# ── Prompt del sistema (cacheado — solo se envía una vez por sesión) ──
_SYSTEM_PROMPT = """You are a lead classifier for a construction subcontractor platform.
Given a building permit description, extract structured data in JSON.

Respond ONLY with valid JSON. No markdown, no explanation.

JSON schema:
{
  "trade": "ROOFING|ELECTRICAL|DRYWALL|PAINTING|LANDSCAPING|HVAC|PLUMBING|INSULATION|FRAMING|CONCRETE|FLOORING|WINDOWS|GENERAL|UNKNOWN",
  "urgency": "HIGH|MEDIUM|LOW",
  "budget_min": <integer USD or null>,
  "budget_max": <integer USD or null>,
  "services": ["specific service 1", "service 2"],
  "is_residential": true|false,
  "is_commercial": true|false,
  "owner_type": "HOMEOWNER|INVESTOR|DEVELOPER|UNKNOWN",
  "summary": "<one sentence pitch for the subcontractor in English>"
}

Rules:
- trade: pick the PRIMARY trade needed
- urgency HIGH = active construction, violation notices, demolition
- urgency MEDIUM = permits just issued, new construction started
- urgency LOW = planning stage, historical data
- budget: estimate from project value if given, or from scope
- summary: max 100 chars, actionable for the sub to contact owner"""


# ── Fallback rule-based (sin red) ────────────────────────────────────

_RULES = [
    ("ROOFING",     ["roof", "roofing", "reroof", "re-roof", "shingle", "tile roof", "flat roof", "torch down"]),
    ("ELECTRICAL",  ["electrical", "electric", "panel upgrade", "service upgrade", "200 amp", "ev charger", "rewire", "wiring", "sub panel"]),
    ("DRYWALL",     ["drywall", "sheetrock", "gypsum", "wallboard", "taping", "texturing"]),
    ("PAINTING",    ["paint", "painting", "repaint", "stucco paint", "primer", "exterior paint"]),
    ("LANDSCAPING", ["landscape", "landscaping", "irrigation", "sprinkler", "hardscape", "paver", "sod", "retaining wall"]),
    ("HVAC",        ["hvac", "heating", "cooling", "air conditioning", "furnace", "duct", "mechanical"]),
    ("PLUMBING",    ["plumbing", "water heater", "sewer", "drain", "pipe", "fixture"]),
    ("INSULATION",  ["insulation", "insulate", "weatherization", "energy audit", "title 24", "energy retrofit"]),
    ("FRAMING",     ["framing", "frame", "structural", "shear wall", "seismic", "foundation"]),
    ("CONCRETE",    ["concrete", "slab", "driveway", "sidewalk", "flatwork"]),
    ("WINDOWS",     ["window", "windows", "door", "glazing", "fenestration"]),
]


def _rule_classify(text: str, value: float = 0) -> dict:
    """Clasificación local sin IA — usado como fallback."""
    lower = text.lower()
    trade = "GENERAL"
    services = []

    for t, keywords in _RULES:
        if any(kw in lower for kw in keywords):
            trade = t
            services = [kw for kw in keywords if kw in lower][:3]
            break

    urgency = "HIGH" if value >= 100000 else "MEDIUM" if value >= 30000 else "LOW"

    budget_min = int(value * 0.05) if value else None
    budget_max = int(value * 0.20) if value else None

    return {
        "trade":          trade,
        "urgency":        urgency,
        "budget_min":     budget_min,
        "budget_max":     budget_max,
        "services":       services,
        "is_residential": any(w in lower for w in ["residential", "single family", "sfr", "dwelling", "house"]),
        "is_commercial":  any(w in lower for w in ["commercial", "office", "retail", "tenant improvement"]),
        "owner_type":     "UNKNOWN",
        "summary":        f"{trade.title()} work needed at this property.",
        "_source":        "rules",
    }


def classify_lead(lead: dict) -> dict:
    """
    Clasifica un lead con Claude Haiku (o fallback rules).

    Args:
        lead: dict del lead con description, permit_type, value_float, city, etc.

    Returns:
        dict con trade, urgency, budget_range, services, summary
    """
    desc = " ".join(filter(None, [
        lead.get("description", ""),
        lead.get("permit_type", ""),
        lead.get("desc", ""),
        lead.get("work_type", ""),
    ])).strip()

    value = float(lead.get("value_float", 0) or 0)
    city  = lead.get("city", "")

    if not desc:
        return _rule_classify("", value)

    # Cache hit
    cache_key = hashlib.md5(f"{desc[:300]}{value}".encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    # Sin API key → fallback
    if not ANTHROPIC_API_KEY or not AI_ENABLED:
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result

    # ── Claude Haiku ─────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        user_content = f"""Permit description: {desc[:500]}
Project value: ${value:,.0f}
City: {city}
Owner: {lead.get('owner', '')}
Contractor: {lead.get('contractor', '')}"""

        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # prompt caching
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )

        raw = response.content[0].text.strip()
        result = json.loads(raw)
        result["_source"] = "claude"

        _cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"[AI Classifier] Claude falló ({e}), usando reglas")
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result


def enrich_lead_with_classification(lead: dict) -> dict:
    """
    Agrega clasificación de trade al lead y ajusta el scoring.
    Modifica el lead in-place y retorna el lead enriquecido.
    """
    classification = classify_lead(lead)
    lead["_trade"]       = classification.get("trade", "GENERAL")
    lead["_urgency"]     = classification.get("urgency", "MEDIUM")
    lead["_budget_min"]  = classification.get("budget_min")
    lead["_budget_max"]  = classification.get("budget_max")
    lead["_services"]    = classification.get("services", [])
    lead["_ai_summary"]  = classification.get("summary", "")
    lead["_is_residential"] = classification.get("is_residential", False)
    lead["_is_commercial"]  = classification.get("is_commercial", False)
    lead["_owner_type"]     = classification.get("owner_type", "UNKNOWN")
    lead["_classifier_source"] = classification.get("_source", "rules")

    # Ajustar scoring según urgencia de IA
    if lead.get("_scoring"):
        urgency_boost = {"HIGH": 10, "MEDIUM": 5, "LOW": 0}.get(
            classification.get("urgency", "LOW"), 0
        )
        lead["_scoring"]["score"] = min(
            lead["_scoring"]["score"] + urgency_boost, 100
        )
        if urgency_boost > 0:
            lead["_scoring"]["reasons"].append(
                f"AI: {classification.get('trade')} urgencia {classification.get('urgency')}"
            )

    return lead


def get_cache_stats() -> dict:
    return {"cached_classifications": len(_cache), "model": MODEL}
