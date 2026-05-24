"""
utils/lead_scoring.py
━━━━━━━━━━━━━━━━━━━━━
Motor de Lead Scoring v2 — prioriza leads por probabilidad de conversión.

Mejoras v2:
  - Usa campos de AI classifier multi-dimensional
  - project_scope EMERGENCY = +8 pts
  - decision_maker GC/HOMEOWNER = mejor conversion
  - _sub_trades = oportunidades de upsell
  - _key_pain_point = signal de urgencia real
  - _competing_subs = ajuste de prioridad por competencia
  - _best_contact_time = info para el sub

Score = f(valor_proyecto, tipo_proyecto, datos_contacto, recencia,
          demografía, fuente, señales_servicio, inspección_próxima,
          AI_trade_urgency, AI_scope, AI_decision_maker, cross_source_signals)

Escala: 0-100
  90-100: HOT    — contactar de inmediato
  70-89:  WARM   — alta prioridad
  50-69:  MEDIUM — seguimiento estándar
  25-49:  COOL   — baja prioridad
  0-24:   COLD   — archivo
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ── Pesos por categoría ───────────────────────────────────────────

_WEIGHTS = {
    "project_value":    20,
    "project_type":     15,
    "contact_quality":  20,
    "recency":          15,
    "geography":        10,
    "source_type":      10,
    "service_signal":   10,
    "inspection_timing": 8,
    "ai_scope":          8,
    "ai_decision_maker": 5,
    "competing_subs":   -5,  # negative = penaliza competencia alta
}


# ── Keywords de alta intención ────────────────────────────────────

_HIGH_INTENT_KEYWORDS = [
    "roof", "roofing", "re-roof", "reroof", "roof replacement",
    "shingle", "shingles", "tile roof", "flat roof", "torch down",
    "drywall", "sheetrock", "gypsum board", "wall board",
    "taping", "texturing", "patch drywall",
    "paint", "painting", "repaint", "exterior paint", "interior paint",
    "painter", "primer", "stucco paint",
    "landscaping", "landscape", "hardscape", "irrigation",
    "sprinkler system", "sod", "retaining wall", "paver",
    "artificial turf", "drought tolerant",
    "electrical", "electric", "panel upgrade", "service upgrade",
    "200 amp", "rewire", "wiring", "ev charger", "sub panel",
    "main panel", "electrical panel",
    "demolition", "demolish", "raze", "tear down", "wrecking",
    "abatement", "full demo", "partial demo", "selective demo",
    "interior demo", "hazmat", "asbestos",
    "hvac", "heating", "cooling", "air conditioning", "furnace",
    "duct", "ductwork", "mechanical",
    "plumbing", "water heater", "sewer", "drain", "pipe",
    "fixture", "sewer line", "water line",
    "concrete", "slab", "driveway", "sidewalk", "flatwork",
    "foundation", "footing",
    "framing", "frame", "structural", "shear wall",
    "flooring", "hardwood", "tile floor", "vinyl plank",
    "carpet", "laminate",
    "window", "windows", "door", "glazing", "fenestration",
    "insulation", "insulate", "weatherization", "energy audit",
    "title 24", "energy retrofit",
]

_MEDIUM_INTENT_KEYWORDS = [
    "adu", "accessory dwelling", "addition", "new construction",
    "remodel", "renovation", "garage conversion", "tenant improvement",
    "single family", "residential", "kitchen remodel", "bath remodel",
]

_LOW_INTENT_KEYWORDS = [
    "swimming pool", "fence", "sign",
    "fire sprinkler", "solar", "photovoltaic",
]

# Pain point urgency signals (from AI classifier)
_PAIN_URGENCY_KEYWORDS = [
    "leak", "flood", "damage", "broken", "collapsed", "hazard",
    "violation", "unsafe", "emergency", "failed", "cracked",
    "mold", "fire", "storm", "structural failure",
]


def score_lead(lead: dict) -> dict:
    """Calcula el score de un lead (0-100) con razones."""
    total = 0.0
    reasons = []

    # ── 1. Valor del proyecto (0-20 pts) ─────────────────────────
    value = lead.get("value_float", 0)
    if value >= 500000:
        total += 20
        reasons.append(f"Proyecto alto valor (${value:,.0f})")
    elif value >= 200000:
        total += 15
    elif value >= 100000:
        total += 12
    elif value >= 50000:
        total += 8
    elif value > 0:
        total += 4

    # AI budget estimate (from classifier, more accurate)
    ai_budget_max = lead.get("_budget_max")
    if not value and ai_budget_max:
        if ai_budget_max >= 100000:
            total += 12
            reasons.append(f"AI budget estimate ${ai_budget_max:,}")
        elif ai_budget_max >= 30000:
            total += 8
        elif ai_budget_max >= 10000:
            total += 5

    # ── 2. Tipo de proyecto (0-15 pts) ───────────────────────────
    desc = ((lead.get("description") or "") + " " +
            (lead.get("permit_type") or "") + " " +
            (lead.get("desc") or "")).lower()

    if any(kw in desc for kw in _HIGH_INTENT_KEYWORDS):
        total += 15
        reasons.append("Servicio target detectado")
    elif any(kw in desc for kw in _MEDIUM_INTENT_KEYWORDS):
        total += 10
        reasons.append("Proyecto relacionado (ADU/remodel)")
    elif any(kw in desc for kw in _LOW_INTENT_KEYWORDS):
        total += 3
    else:
        total += 5

    # ── 3. Calidad de contacto (0-20 pts) ────────────────────────
    has_phone = bool(lead.get("contact_phone"))
    has_email = bool(lead.get("contact_email"))
    has_contractor = bool(lead.get("contractor"))
    has_owner = bool(lead.get("owner"))

    contact_score = 0
    if has_phone:   contact_score += 8
    if has_email:   contact_score += 6
    if has_contractor: contact_score += 4
    if has_owner:   contact_score += 2
    total += min(contact_score, 20)

    if has_phone and has_email:
        reasons.append("Contacto completo (tel + email)")
    elif has_phone:
        reasons.append("Telefono disponible")

    # ── 4. Recencia (0-15 pts) ───────────────────────────────────
    date_str = (lead.get("date") or lead.get("issued_date") or
                lead.get("filed_date") or "")
    if date_str:
        try:
            lead_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
            days_ago = (datetime.utcnow() - lead_date).days
            if days_ago <= 7:
                total += 15
                reasons.append("Lead de esta semana")
            elif days_ago <= 14:
                total += 12
            elif days_ago <= 30:
                total += 9
            elif days_ago <= 60:
                total += 5
            else:
                total += 2
        except (ValueError, TypeError):
            total += 5

    # ── 5. Geografia (0-10 pts) ──────────────────────────────────
    city = (lead.get("city") or "").lower()
    _high_demand_cities = {
        "san francisco": 10, "oakland": 9, "berkeley": 9,
        "richmond": 8, "san jose": 7, "hayward": 7,
        "alameda": 8, "san leandro": 7, "emeryville": 8,
        "albany": 7, "el cerrito": 7,
        "fremont": 6, "sunnyvale": 5, "santa clara": 5,
        "concord": 7, "walnut creek": 6, "martinez": 7,
        "pleasant hill": 6, "pittsburg": 7, "antioch": 7,
        "vallejo": 8, "fairfield": 6, "napa": 6,
        "san rafael": 6, "novato": 5, "petaluma": 5,
        "daly city": 7, "south san francisco": 6,
        "san mateo": 6, "burlingame": 5, "san bruno": 6,
        "dublin": 4, "pleasanton": 4, "livermore": 5,
        "san ramon": 4, "danville": 4, "lafayette": 4,
        "orinda": 4, "moraga": 4, "union city": 5,
        "newark": 5, "castro valley": 6, "san lorenzo": 6,
        "millbrae": 5, "vacaville": 5, "benicia": 5,
        "hercules": 6, "pinole": 6, "oakley": 5,
        "brentwood": 4, "clayton": 4, "tracy": 4,
        "stockton": 5, "sonoma": 4, "suisun city": 5,
        "rio vista": 4, "alamo": 4, "redwood city": 5,
        "contra costa county": 7, "alameda county": 7,
        "san mateo county": 6, "solano county": 6,
        "marin county": 6, "napa county": 6,
        "sonoma county": 5, "san joaquin county": 5,
        # Extended cities
        "dallas": 7, "austin": 7, "honolulu": 6, "new york": 8,
        "chicago": 7, "houston": 7, "san antonio": 6,
    }
    geo_score = _high_demand_cities.get(city, 4)
    total += geo_score

    # ── 6. Tipo de fuente (0-10 pts) ─────────────────────────────
    agent_key = lead.get("_agent_key", "")
    _source_scores = {
        "permits": 10, "construction": 10, "deconstruction": 9,
        "realestate": 9, "solar": 8, "energy": 7,
        "rodents": 6, "places": 5, "yelp": 4, "flood": 5,
        "plumbing": 9, "hvac": 9, "paint": 8, "flooring_concrete": 8,
        "tdlr": 7, "weather": 6, "disaster": 8,
    }
    total += _source_scores.get(agent_key, 5)

    # ── 7. Senales de servicio target (0-10 pts) ─────────────────
    service_type = (lead.get("service_type") or
                    lead.get("trade") or
                    lead.get("category") or "").lower()

    target_services = {
        "roof": 10, "roofing": 10, "reroof": 10,
        "drywall": 10, "sheetrock": 10,
        "paint": 9, "painting": 9, "painter": 9,
        "landscape": 9, "landscaping": 9, "irrigation": 8,
        "electrical": 10, "electric": 9,
        "demolition": 10, "demo": 9, "demolish": 9,
        "hvac": 9, "heating": 8, "cooling": 8,
        "plumbing": 9, "plumber": 9,
        "concrete": 9, "slab": 8, "flatwork": 8,
        "framing": 9, "frame": 8,
        "flooring": 9, "hardwood": 8, "tile": 8,
        "window": 9, "windows": 9, "glazing": 8,
        "insulation": 9, "insulate": 8,
    }
    for key, pts in target_services.items():
        if key in service_type:
            total += min(pts, 10)
            reasons.append(f"Servicio target: {key}")
            break

    # ── 8a. AI Trade + Urgency boost ─────────────────────────────
    ai_urgency = lead.get("_urgency", "")
    ai_trade   = lead.get("_trade", "")
    _target_trades = {"ROOFING", "ELECTRICAL", "DRYWALL", "PAINTING",
                      "LANDSCAPING", "INSULATION", "HVAC", "DEMOLITION",
                      "PLUMBING", "CONCRETE", "FRAMING", "FLOORING", "WINDOWS"}

    if ai_urgency == "HIGH":
        total += 10
        reasons.append(f"AI: urgencia ALTA ({ai_trade})")
    elif ai_urgency == "MEDIUM":
        total += 5

    if ai_trade in _target_trades:
        total += 8

    # ── 8b. AI Project Scope (NEW) ──────────────────────────────
    ai_scope = lead.get("_project_scope", "")
    if ai_scope == "EMERGENCY":
        total += 8
        reasons.append("Proyecto de emergencia")
    elif ai_scope == "FULL":
        total += 3
    elif ai_scope == "REPAIR":
        total += 2

    # ── 8c. AI Decision Maker (NEW) ────────────────────────────
    decision_maker = lead.get("_decision_maker", "")
    if decision_maker == "HOMEOWNER":
        total += 5
        reasons.append("Decision maker: Homeowner")
    elif decision_maker == "GC":
        total += 3
    elif decision_maker in ("ARCHITECT", "PM"):
        total += 2

    # ── 8d. Competing subs (NEW - negative weight) ──────────────
    competing = lead.get("_competing_subs", 3)
    if competing <= 1:
        total += 3
        reasons.append("Baja competencia")
    elif competing >= 5:
        total -= 3

    # ── 8e. Key pain point urgency ──────────────────────────────
    pain_point = (lead.get("_key_pain_point") or "").lower()
    if pain_point and any(kw in pain_point for kw in _PAIN_URGENCY_KEYWORDS):
        total += 5
        reasons.append("Pain point urgente")

    # ── 8f. Sub-trades = upsell signals ─────────────────────────
    sub_trades = lead.get("_sub_trades", [])
    if sub_trades:
        total += 2
        if len(sub_trades) >= 2:
            total += 2

    # ── 8g. Cross-source signal boost ──────────────────────────
    cross_count = lead.get("_cross_agent_count", 0)
    if cross_count >= 3:
        total += 15
        reasons.insert(0, f"{cross_count} fuentes cruzadas")
    elif cross_count == 2:
        total += 8

    # ── 9. Inspeccion proxima (0-8 pts) ─────────────────────────
    next_insp_date = lead.get("next_scheduled_inspection_date")
    if next_insp_date:
        try:
            if isinstance(next_insp_date, str):
                insp_date = datetime.strptime(next_insp_date[:10], "%Y-%m-%d").date()
            else:
                insp_date = next_insp_date
            today = datetime.utcnow().date()
            days_until = (insp_date - today).days
            if 0 <= days_until <= 7:
                total += 8
                reasons.append(f"Inspeccion en {days_until} dias")
            elif days_until <= 14:
                total += 6
            elif days_until <= 30:
                total += 4
        except (ValueError, TypeError, AttributeError):
            pass

    # ── Calcular grado ───────────────────────────────────────────
    score = max(0, min(int(total), 100))

    if score >= 90:
        grade, emoji = "HOT", "fire"
    elif score >= 70:
        grade, emoji = "WARM", "orange"
    elif score >= 50:
        grade, emoji = "MEDIUM", "yellow"
    elif score >= 25:
        grade, emoji = "COOL", "blue"
    else:
        grade, emoji = "COLD", "white"

    return {
        "score":       score,
        "grade":       grade,
        "grade_emoji": emoji,
        "reasons":     reasons[:4],
    }


def format_score_line(scoring: dict) -> str:
    """Formatea el score para Telegram."""
    s = scoring
    reasons_str = " | ".join(s["reasons"]) if s["reasons"] else ""
    return f"{s['grade_emoji']} {s['score']}/100 ({s['grade']})" + (
        f" - {reasons_str}" if reasons_str else ""
    )
