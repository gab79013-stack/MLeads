"""
utils/ai_classifier.py
━━━━━━━━━━━━━━━━━━━━━━
IA #1 — Clasificador de Trade Multi-Dimensional con Qwen

Analiza la descripción de un permiso y extrae:
  - trade:             qué sub-contractor se necesita
  - sub_trades:        trades secundarios derivados del proyecto
  - urgency:           HIGH / MEDIUM / LOW + razón
  - budget_range:      rango estimado en USD
  - services:          lista específica de servicios
  - is_residential:    bool
  - is_commercial:     bool
  - owner_type:        HOMEOWNER|INVESTOR|DEVELOPER|GC|GOVERNMENT|UNKNOWN
  - project_phase:     PLANNING|PERMITTING|ACTIVE|INSPECTION|COMPLETION
  - project_scope:     FULL|PARTIAL|REPAIR|EMERGENCY|MAINTENANCE
  - decision_maker:    HOMEOWNER|GC|PM|ARCHITECT|UNKNOWN
  - competing_subs:    0-5 (estimación de competencia)
  - best_contact_time: MORNING|AFTERNOON|EVENING|ANY
  - key_pain_point:    string — problema principal que resolver
  - upsell_opportunity: string — servicio adicional sugerido
  - summary:           pitch personalizado para el sub-contractor
  - confidence:        0.0-1.0 — confianza en la clasificación

Usa qwen-plus (mejor precisión) via DashScope International.
Graceful degradation: si la API falla, retorna clasificación
rule-based local (sin coste, sin red).
"""

import os
import json
import logging
import hashlib
import time
from datetime import datetime

logger = logging.getLogger(__name__)

QWEN_API_KEY  = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
MODEL         = os.getenv("AI_CLASSIFIER_MODEL", "MiniMaxAI/MiniMax-M2.5")
FALLBACK_MODEL = os.getenv("AI_CLASSIFIER_FALLBACK_MODEL", "nvidia/DeepSeek-V3.2-NVFP4")
AI_ENABLED    = os.getenv("AI_ENABLED", "true").lower() not in ("false", "0", "no")

# Rate limiting: max 30 calls/min to Qwen
_last_call_times: list[float] = []
_MAX_CALLS_PER_MIN = 30

# Cache en memoria: evita re-clasificar el mismo texto
_cache: dict[str, dict] = {}

# ── Enhanced System prompt ─────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are an expert construction lead analyst for a subcontractor platform serving the US market. You deeply understand construction phases, subcontractor dynamics, and project lifecycles.

Given a building permit or construction lead, extract ALL structured data in JSON.

Respond ONLY with valid JSON. No markdown, no explanation, no code blocks. Output the complete JSON in a single response - do not truncate.

JSON schema:
{
  "trade": "ROOFING|ELECTRICAL|DRYWALL|PAINTING|LANDSCAPING|HVAC|PLUMBING|INSULATION|FRAMING|CONCRETE|FLOORING|WINDOWS|DEMOLITION|GENERAL|UNKNOWN",
  "sub_trades": ["SECONDARY_TRADE_1", "TRADE_2"],
  "urgency": "HIGH|MEDIUM|LOW",
  "urgency_reason": "brief reason for urgency level",
  "budget_min": <integer USD or null>,
  "budget_max": <integer USD or null>,
  "budget_confidence": "HIGH|MEDIUM|LOW",
  "services": ["specific service 1", "service 2"],
  "is_residential": true|false,
  "is_commercial": true|false,
  "owner_type": "HOMEOWNER|INVESTOR|DEVELOPER|GC|GOVERNMENT|UNKNOWN",
  "project_phase": "PLANNING|PERMITTING|ACTIVE|INSPECTION|COMPLETION",
  "project_scope": "FULL|PARTIAL|REPAIR|EMERGENCY|MAINTENANCE",
  "decision_maker": "HOMEOWNER|GC|PM|ARCHITECT|UNKNOWN",
  "competing_subs": <integer 0-5 estimated competition level>,
  "best_contact_time": "MORNING|AFTERNOON|EVENING|ANY",
  "key_pain_point": "<main problem the property owner needs solved>",
  "upsell_opportunity": "<additional service the sub could offer>",
  "summary": "<2-sentence personalized pitch for the subcontractor, in English>",
  "confidence": <float 0.0-1.0>
}

Analysis rules:
- trade: the PRIMARY trade needed for this specific permit
- sub_trades: OTHER trades that will likely be needed as a result (e.g., roofing → drywall/water damage, demolition → framing, plumbing → drywall repair)
- urgency: 
  HIGH = active violation, stop-work, emergency repair, demolition in progress, water damage, code enforcement
  MEDIUM = permit just issued, new construction started, recent sale, ADU
  LOW = planning stage, routine maintenance, historical data, permit renewal
- budget: estimate from project value if given. If not, estimate from scope:
  - ADU: $80k-200k, Roof replacement: $8k-25k, Panel upgrade: $2k-5k
  - Full rewire: $8k-15k, HVAC install: $5k-15k, Kitchen remodel: $25k-75k
  - Bathroom remodel: $15k-40k, Foundation repair: $10k-30k, Concrete slab: $5k-20k
  - Plumbing repipe: $4k-15k, Exterior paint: $3k-12k, Drywall repair: $1k-5k
  - Flooring install: $3k-15k, Window replacement: $5k-20k
- owner_type: GC if contractor name matches permit, DEVELOPER if large multi-unit, GOVERNMENT if public work
- project_phase: where is this project NOW in its lifecycle
- project_scope: EMERGENCY = water/fire/storm damage, REPAIR = fix existing, FULL = complete new install
- decision_maker: who most likely decides which sub to hire
- competing_subs: 0=no other subs likely, 5=highly competitive market
- best_contact_time: MORNING=7-11am, AFTERNOON=11am-4pm, EVENING=4-7pm
- key_pain_point: what problem does the owner have RIGHT NOW
- upsell_opportunity: natural additional service (e.g., roofing → gutters, electrical → EV charger, plumbing → water heater)
- summary: personalized, actionable, mentions specific trade and project details
- confidence: how sure are you about the PRIMARY trade classification"""


# ── Fallback rule-based (sin red) ─────────────────────────────────────────────

_RULES = [
    ("DEMOLITION",  ["demolition", "demolish", "raze", "tear down", "wrecking", "abatement", "full demo", "partial demo", "hazmat", "asbestos", "selective demo"]),
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

_SUB_TRADE_MAP = {
    "ROOFING": ["DRYWALL", "PAINTING", "INSULATION"],
    "ELECTRICAL": ["DRYWALL", "PAINTING"],
    "PLUMBING": ["DRYWALL", "FLOORING", "CONCRETE"],
    "HVAC": ["ELECTRICAL", "DRYWALL", "INSULATION"],
    "DEMOLITION": ["FRAMING", "DRYWALL", "CONCRETE"],
    "CONCRETE": ["PLUMBING", "FRAMING"],
    "FRAMING": ["ELECTRICAL", "PLUMBING", "DRYWALL"],
    "FLOORING": ["CONCRETE", "PAINTING"],
    "WINDOWS": ["DRYWALL", "PAINTING", "INSULATION"],
    "PAINTING": ["DRYWALL"],
    "DRYWALL": ["PAINTING", "INSULATION"],
}


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

    # Infer sub-trades
    sub_trades = _SUB_TRADE_MAP.get(trade, [])

    # Infer project scope
    scope = "REPAIR"
    if any(w in lower for w in ["new", "install", "construction"]):
        scope = "FULL"
    elif any(w in lower for w in ["emergency", "urgent", "leak", "break", "damage"]):
        scope = "EMERGENCY"
    elif any(w in lower for w in ["maintain", "service", "inspect"]):
        scope = "MAINTENANCE"

    # Infer project phase
    phase = "PERMITTING"
    if any(w in lower for w in ["active", "under construction", "in progress"]):
        phase = "ACTIVE"
    elif any(w in lower for w in ["inspec", "final", "completion"]):
        phase = "INSPECTION"
    elif any(w in lower for w in ["plan", "design", "proposal"]):
        phase = "PLANNING"

    # Infer decision maker
    decision_maker = "UNKNOWN"
    if any(w in lower for w in ["homeowner", "owner-occupied", "sfr", "single family"]):
        decision_maker = "HOMEOWNER"
    elif any(w in lower for w in ["general contractor", "gc", "builder"]):
        decision_maker = "GC"

    # Infer owner type
    owner_type = "UNKNOWN"
    if any(w in lower for w in ["commercial", "retail", "office", "tenant improvement"]):
        owner_type = "INVESTOR"
    elif any(w in lower for w in ["city", "county", "state", "public", "school"]):
        owner_type = "GOVERNMENT"
    elif any(w in lower for w in ["developer", "llc", "inc", "corp"]):
        owner_type = "DEVELOPER"
    elif any(w in lower for w in ["homeowner", "residential", "sfr"]):
        owner_type = "HOMEOWNER"

    # Pain point inference
    pain_points = {
        "ROOFING": "Roof damage or aging roof needing replacement",
        "PLUMBING": "Plumbing failure or pipe deterioration",
        "ELECTRICAL": "Electrical system inadequate or unsafe",
        "HVAC": "Heating/cooling system failing or inadequate",
        "PAINTING": "Interior or exterior paint deteriorating",
        "DRYWALL": "Wall damage from water, impact, or age",
        "CONCRETE": "Concrete cracking, settling, or new installation needed",
        "FRAMING": "Structural issues requiring repair or new construction",
        "DEMOLITION": "Structure or interior needs removal before new work",
        "FLOORING": "Flooring worn, damaged, or outdated",
        "WINDOWS": "Windows failing, drafty, or non-compliant",
        "LANDSCAPING": "Landscape renovation or irrigation needs",
        "INSULATION": "Energy inefficiency or code compliance needed",
    }

    # Upsell map
    upsell_map = {
        "ROOFING": "Gutter replacement, solar-ready roof, skylight install",
        "ELECTRICAL": "EV charger, smart home wiring, solar panel connection",
        "PLUMBING": "Water heater upgrade, water softener, gas line for appliances",
        "HVAC": "Smart thermostat, air purification, duct sealing",
        "PAINTING": "Cabinet refinishing, deck staining, waterproof coating",
        "DRYWALL": "Soundproofing, crown molding, accent wall texture",
        "CONCRETE": "Decorative stamping, epoxy coating, drainage improvement",
        "FRAMING": "Seismic retrofit, shear wall upgrade, attic framing",
        "DEMOLITION": "Asbestos testing, material salvage, site cleanup",
        "FLOORING": "Baseboard installation, subfloor repair, moisture barrier",
        "WINDOWS": "Security film, window treatments, smart glass",
        "LANDSCAPING": "Outdoor lighting, fire pit, artificial turf, drainage",
        "INSULATION": "Solar attic fan, radiant barrier, crawlspace encapsulation",
    }

    return {
        "trade":              trade,
        "sub_trades":         sub_trades,
        "urgency":            urgency,
        "urgency_reason":     f"Project value ${value:,.0f}" if value >= 100000 else "Standard priority",
        "budget_min":         budget_min,
        "budget_max":         budget_max,
        "budget_confidence":  "LOW",
        "services":           services,
        "is_residential":     any(w in lower for w in ["residential", "single family", "sfr", "dwelling", "house"]),
        "is_commercial":      any(w in lower for w in ["commercial", "office", "retail", "tenant improvement"]),
        "owner_type":         owner_type,
        "project_phase":      phase,
        "project_scope":      scope,
        "decision_maker":     decision_maker,
        "competing_subs":     3,
        "best_contact_time":  "MORNING",
        "key_pain_point":     pain_points.get(trade, "Property improvement needed"),
        "upsell_opportunity": upsell_map.get(trade, "General improvement services"),
        "summary":            f"{trade.title()} work needed at this property.",
        "confidence":         0.5,
        "_source":            "rules",
    }


def _rate_limit_check() -> bool:
    """Check if we can make another API call (rate limit)."""
    global _last_call_times
    now = time.monotonic()
    _last_call_times = [t for t in _last_call_times if now - t < 60]
    return len(_last_call_times) < _MAX_CALLS_PER_MIN


def _get_client():
    """Retorna cliente OpenAI apuntando a DashScope."""
    from openai import OpenAI
    return OpenAI(api_key=QWEN_API_KEY, base_url=QWEN_BASE_URL)


# ── Clasificación principal ───────────────────────────────────────────────────

def classify_lead(lead: dict) -> dict:
    """
    Clasifica un lead con Qwen-plus (o fallback rules).
    """
    desc = " ".join(filter(None, [
        lead.get("description", ""),
        lead.get("title", ""),
        lead.get("permit_type", ""),
        lead.get("desc", ""),
        lead.get("work_type", ""),
        lead.get("primary_service_type", ""),
    ])).strip()

    value = float(lead.get("value_float", 0) or 0)
    city  = lead.get("city", "")

    if not desc:
        return _rule_classify("", value)

    # Cache hit
    cache_key = hashlib.md5(f"{desc[:300]}{value}{city}".encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    # Sin API key o IA desactivada → fallback
    if not QWEN_API_KEY or not AI_ENABLED:
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result

    # Rate limit check
    if not _rate_limit_check():
        logger.debug("[AI Classifier] Rate limited, using rules")
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result

    # ── Qwen via DashScope ───────────────────────────────────────────
    try:
        client = _get_client()

        # Build rich context for the AI
        contractor = lead.get("contractor", "")
        owner = lead.get("owner", "")
        service_type = lead.get("service_type", "")
        contact_phone = lead.get("contact_phone", "")
        lat = lead.get("lat", "")
        lon = lead.get("lon", "")

        user_content = (
            f"Permit description: {desc[:600]}\n"
            f"Project value: ${value:,.0f}\n"
            f"City: {city}\n"
            f"Owner: {owner}\n"
            f"Contractor: {contractor}\n"
            f"Service type: {service_type}\n"
            f"Has phone: {'Yes' if contact_phone else 'No'}\n"
            f"Coordinates: {lat}, {lon}"
        )

        _last_call_times.append(time.monotonic())

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_content},
            ],
            max_tokens=800,
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()

        # Clean markdown if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        # Try to parse JSON, repair if truncated
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Attempt to close truncated JSON
            import re
            # Count brackets
            opens = raw.count('{') - raw.count('}')
            brackets = raw.count('[') - raw.count(']')
            repaired = raw + (']' * max(0, brackets)) + ('}' * max(0, opens))
            try:
                result = json.loads(repaired)
            except json.JSONDecodeError:
                # Last resort: extract key-value pairs with regex
                result = {}
                for m in re.finditer(r'"(\w+)":\s*"?([^",}\]]+)"?', raw):
                    key, val = m.group(1), m.group(2).strip()
                    if key not in result:
                        result[key] = val
        result["_source"] = "qwen"
        result["_model"]  = response.model

        # Validate required fields, fill missing with defaults
        for field, default in [
            ("trade", "GENERAL"), ("sub_trades", []), ("urgency", "MEDIUM"),
            ("urgency_reason", ""), ("budget_min", None), ("budget_max", None),
            ("budget_confidence", "MEDIUM"), ("services", []),
            ("is_residential", False), ("is_commercial", False),
            ("owner_type", "UNKNOWN"), ("project_phase", "PERMITTING"),
            ("project_scope", "PARTIAL"), ("decision_maker", "UNKNOWN"),
            ("competing_subs", 3), ("best_contact_time", "MORNING"),
            ("key_pain_point", ""), ("upsell_opportunity", ""),
            ("summary", ""), ("confidence", 0.5),
        ]:
            if field not in result:
                result[field] = default

        _cache[cache_key] = result
        return result

    except json.JSONDecodeError as e:
        logger.warning(f"[AI Classifier] Qwen JSON invalid ({e}), using rules")
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"[AI Classifier] Qwen failed ({e}), using rules")
        result = _rule_classify(desc, value)
        _cache[cache_key] = result
        return result


# ── Batch classifier ─────────────────────────────────────────────────────────

def classify_leads_batch(leads: list, max_workers: int = 8) -> list:
    """Clasifica una lista de leads en paralelo."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [None] * len(leads)

    def _classify_one(idx_lead):
        idx, lead = idx_lead
        return idx, enrich_lead_with_classification(lead)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_classify_one, (i, l)): i for i, l in enumerate(leads)}
        for fut in as_completed(futures):
            try:
                idx, enriched = fut.result()
                results[idx] = enriched
            except Exception as e:
                logger.warning(f"[AI Classifier] batch error: {e}")
                results[futures[fut]] = leads[futures[fut]]

    return results


# ── Enriquecimiento de lead ───────────────────────────────────────────────────

def enrich_lead_with_classification(lead: dict) -> dict:
    """
    Agrega clasificación multi-dimensional al lead y ajusta scoring.
    Modifica el lead in-place y retorna el lead enriquecido.
    """
    classification = classify_lead(lead)

    # Core fields
    lead["_trade"]             = classification.get("trade", "GENERAL")
    lead["_urgency"]           = classification.get("urgency", "MEDIUM")
    lead["_urgency_reason"]    = classification.get("urgency_reason", "")
    lead["_budget_min"]        = classification.get("budget_min")
    lead["_budget_max"]        = classification.get("budget_max")
    lead["_budget_confidence"] = classification.get("budget_confidence", "LOW")
    lead["_services"]          = classification.get("services", [])
    lead["_ai_summary"]        = classification.get("summary", "")
    lead["_is_residential"]    = classification.get("is_residential", False)
    lead["_is_commercial"]     = classification.get("is_commercial", False)
    lead["_owner_type"]        = classification.get("owner_type", "UNKNOWN")
    lead["_classifier_source"] = classification.get("_source", "rules")

    # New rich fields
    lead["_sub_trades"]           = classification.get("sub_trades", [])
    lead["_project_phase"]        = classification.get("project_phase", "PERMITTING")
    lead["_project_scope"]        = classification.get("project_scope", "PARTIAL")
    lead["_decision_maker"]       = classification.get("decision_maker", "UNKNOWN")
    lead["_competing_subs"]       = classification.get("competing_subs", 3)
    lead["_best_contact_time"]    = classification.get("best_contact_time", "MORNING")
    lead["_key_pain_point"]       = classification.get("key_pain_point", "")
    lead["_upsell_opportunity"]   = classification.get("upsell_opportunity", "")
    lead["_ai_confidence"]        = classification.get("confidence", 0.5)

    # Adjust scoring
    if lead.get("_scoring"):
        urgency_boost = {"HIGH": 10, "MEDIUM": 5, "LOW": 0}.get(
            classification.get("urgency", "LOW"), 0
        )
        lead["_scoring"]["score"] = min(
            lead["_scoring"]["score"] + urgency_boost, 100
        )
        if urgency_boost > 0:
            trade = classification.get("trade", "")
            urgency = classification.get("urgency", "")
            lead["_scoring"]["reasons"].append(
                f"AI: {trade} — urgency {urgency}"
            )

        # Scope bonus: emergency = higher priority
        scope = classification.get("project_scope", "")
        if scope == "EMERGENCY":
            lead["_scoring"]["score"] = min(lead["_scoring"]["score"] + 8, 100)
            lead["_scoring"]["reasons"].append("Emergency repair")
        elif scope == "FULL":
            lead["_scoring"]["score"] = min(lead["_scoring"]["score"] + 3, 100)

        # Confidence adjustment: low confidence = lower score
        confidence = classification.get("confidence", 0.5)
        if confidence < 0.3:
            lead["_scoring"]["score"] = max(lead["_scoring"]["score"] - 5, 0)

    return lead


def get_cache_stats() -> dict:
    return {
        "cached_classifications": len(_cache),
        "model": MODEL,
        "provider": "Qwen / Alibaba DashScope",
        "base_url": QWEN_BASE_URL,
        "rate_limit_per_min": _MAX_CALLS_PER_MIN,
    }
