"""
utils/lead_scoring.py
━━━━━━━━━━━━━━━━━━━━━
Motor de Lead Scoring — prioriza leads por probabilidad de conversión.

Enfoque en 5 servicios clave:
  Roofing, Drywall, Paint, Landscaping, Electrical

Score = f(valor_proyecto, tipo_proyecto, datos_contacto, recencia,
          demografía, fuente, señales_servicio, inspección_próxima,
          AI_trade_urgency, cross_source_signals)

IA #2 — Boost por clasificación de trade (Claude):
  - urgencia HIGH  → +10 pts
  - urgencia MEDIUM → +5 pts
  - trade exacto en servicios target → +8 pts extra

Escala: 0-100
  90-100: 🔥 HOT    — contactar de inmediato
  70-89:  🟠 WARM   — alta prioridad
  50-69:  🟡 MEDIUM — seguimiento estándar
  25-49:  🔵 COOL   — baja prioridad
  0-24:   ⚪ COLD   — archivo
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# ── Pesos por categoría (suman ~100 puntos máximo) ───────────────────

_WEIGHTS = {
    "project_value":    20,   # Valor del proyecto ($)
    "project_type":     15,   # Tipo de proyecto (ADU, new construction, etc.)
    "contact_quality":  20,   # Tiene teléfono, email, contratista
    "recency":          15,   # Qué tan reciente es el lead
    "geography":        10,   # Zona demográfica favorable
    "source_type":      10,   # Fuente del lead (permit > solar > rodent > etc.)
    "service_signal":   10,   # Señales directas de los 5 servicios target
    "inspection_timing": 8,   # Próxima inspección programada
}


# ── Keywords de alta intención (servicios target) ────────────────────
# Roofing, Drywall, Paint, Landscaping, Electrical

_HIGH_INTENT_KEYWORDS = [
    # Roofing (C-39)
    "roof", "roofing", "re-roof", "reroof", "roof replacement",
    "shingle", "shingles", "tile roof", "flat roof", "torch down",
    # Drywall
    "drywall", "sheetrock", "gypsum board", "wall board",
    "taping", "texturing", "patch drywall",
    # Paint (C-33)
    "paint", "painting", "repaint", "exterior paint", "interior paint",
    "painter", "primer", "stucco paint",
    # Landscaping
    "landscaping", "landscape", "hardscape", "irrigation",
    "sprinkler system", "sod", "retaining wall", "paver",
    "artificial turf", "drought tolerant",
    # Electrical
    "electrical", "electric", "panel upgrade", "service upgrade",
    "200 amp", "rewire", "wiring", "ev charger", "sub panel",
    "main panel", "electrical panel",
    # Demolition (C-21)
    "demolition", "demolish", "raze", "tear down", "wrecking",
    "abatement", "full demo", "partial demo", "selective demo",
    "interior demo", "hazmat", "asbestos",
    # HVAC (C-20)
    "hvac", "heating", "cooling", "air conditioning", "furnace",
    "duct", "ductwork", "mechanical",
    # Plumbing (C-36)
    "plumbing", "water heater", "sewer", "drain", "pipe",
    "fixture", "sewer line", "water line",
    # Concrete (C-8)
    "concrete", "slab", "driveway", "sidewalk", "flatwork",
    "foundation", "footing",
    # Framing (C-5)
    "framing", "frame", "structural", "shear wall",
    # Flooring (C-15)
    "flooring", "hardwood", "tile floor", "vinyl plank",
    "carpet", "laminate",
    # Windows (C-17)
    "window", "windows", "door", "glazing", "fenestration",
    # Insulation (C-2)
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


def score_lead(lead: dict) -> dict:
    """
    Calcula el score de un lead y retorna dict con:
      - score: int (0-100)
      - grade: str ('HOT', 'WARM', 'MEDIUM', 'COOL', 'COLD')
      - grade_emoji: str
      - reasons: list[str] — factores principales
    """
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

    # ── 2. Tipo de proyecto (0-15 pts) ───────────────────────────
    desc = ((lead.get("description") or "") + " " +
            (lead.get("permit_type") or "") + " " +
            (lead.get("desc") or "")).lower()

    if any(kw in desc for kw in _HIGH_INTENT_KEYWORDS):
        total += 15
        reasons.append("Servicio target (roofing/drywall/paint/landscape/electrical)")
    elif any(kw in desc for kw in _MEDIUM_INTENT_KEYWORDS):
        total += 10
        reasons.append("Proyecto relacionado (ADU/remodelación/construcción)")
    elif any(kw in desc for kw in _LOW_INTENT_KEYWORDS):
        total += 3
    else:
        total += 5  # genérico

    # ── 3. Calidad de contacto (0-20 pts) ────────────────────────
    has_phone = bool(lead.get("contact_phone"))
    has_email = bool(lead.get("contact_email"))
    has_contractor = bool(lead.get("contractor"))
    has_owner = bool(lead.get("owner"))

    contact_score = 0
    if has_phone:
        contact_score += 8
    if has_email:
        contact_score += 6
    if has_contractor:
        contact_score += 4
    if has_owner:
        contact_score += 2
    total += min(contact_score, 20)

    if has_phone and has_email:
        reasons.append("Contacto completo (tel + email)")
    elif has_phone:
        reasons.append("Teléfono disponible")

    # ── 4. Recencia (0-15 pts) ───────────────────────────────────
    date_str = (lead.get("date") or lead.get("issued_date") or
                lead.get("filed_date") or "")
    if date_str:
        try:
            from datetime import datetime, timedelta
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
            total += 5  # fecha no parseable, asumir reciente

    # ── 5. Geografía (0-10 pts) ──────────────────────────────────
    # Zonas con casas antiguas y alto volumen de remodelación
    city = (lead.get("city") or "").lower()
    _high_demand_cities = {
        # Tier 1 — casas antiguas, alta densidad, alta demanda
        "san francisco": 10, "oakland": 9, "berkeley": 9,
        "richmond": 8, "san jose": 7, "hayward": 7,
        "alameda": 8, "san leandro": 7, "emeryville": 8,
        "albany": 7, "el cerrito": 7,
        # Tier 2 — suburban, demanda media-alta
        "fremont": 6, "sunnyvale": 5, "santa clara": 5,
        "concord": 7, "walnut creek": 6, "martinez": 7,
        "pleasant hill": 6, "pittsburg": 7, "antioch": 7,
        "vallejo": 8, "fairfield": 6, "napa": 6,
        "san rafael": 6, "novato": 5, "petaluma": 5,
        "daly city": 7, "south san francisco": 6,
        "san mateo": 6, "burlingame": 5, "san bruno": 6,
        # Tier 3 — newer suburbs, demanda media
        "dublin": 4, "pleasanton": 4, "livermore": 5,
        "san ramon": 4, "danville": 4, "lafayette": 4,
        "orinda": 4, "moraga": 4, "union city": 5,
        "newark": 5, "castro valley": 6, "san lorenzo": 6,
        "millbrae": 5, "vacaville": 5, "benicia": 5,
        "hercules": 6, "pinole": 6, "oakley": 5,
        "brentwood": 4, "clayton": 4, "tracy": 4,
        "stockton": 5, "sonoma": 4, "suisun city": 5,
        "rio vista": 4, "alamo": 4, "redwood city": 5,
        # County-level sources
        "contra costa county": 7, "alameda county": 7,
        "san mateo county": 6, "solano county": 6,
        "marin county": 6, "napa county": 6,
        "sonoma county": 5, "san joaquin county": 5,
    }
    geo_score = _high_demand_cities.get(city, 4)
    total += geo_score

    # ── 6. Tipo de fuente (0-10 pts) ─────────────────────────────
    agent_key = lead.get("_agent_key", "")
    _source_scores = {
        "permits": 10, "construction": 10, "deconstruction": 9,
        "realestate": 9, "solar": 8, "energy": 7,
        "rodents": 6, "places": 5, "yelp": 4, "flood": 5,
    }
    total += _source_scores.get(agent_key, 5)

    # ── 7. Señales de servicio target (0-10 pts) ─────────────────
    # Detecta menciones explícitas a Roofing / Drywall / Paint /
    # Landscaping / Electrical en campos específicos del lead.
    service_signals = 0
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
            service_signals = max(service_signals, pts)
            reasons.append(f"Servicio target: {key}")
            break

    # Permits con trabajo estructural o envolvente suelen derivar
    # en roofing/drywall/paint
    if "reroof" in desc or "re-roof" in desc or "roof replace" in desc:
        service_signals = max(service_signals, 10)
    if "panel upgrade" in desc or "service upgrade" in desc:
        service_signals = max(service_signals, 9)

    total += min(service_signals, 10)

    # ── 8a. AI Trade Urgency boost (0-18 pts) ────────────────────
    # Si el clasificador de IA ya corrió, aplicar boost de urgencia y trade
    ai_urgency = lead.get("_urgency", "")
    ai_trade   = lead.get("_trade", "")
    _target_trades = {"ROOFING", "ELECTRICAL", "DRYWALL", "PAINTING",
                      "LANDSCAPING", "INSULATION", "HVAC", "DEMOLITION",
                      "PLUMBING", "CONCRETE", "FRAMING", "FLOORING",
                      "WINDOWS"}

    if ai_urgency == "HIGH":
        total += 10
        reasons.append(f"AI urgencia ALTA ({ai_trade or 'general'})")
    elif ai_urgency == "MEDIUM":
        total += 5

    if ai_trade in _target_trades:
        total += 8
        reasons.append(f"Trade target: {ai_trade}")

    # ── 8b. Cross-source signal boost (0-15 pts) ─────────────────
    # Propiedad detectada por múltiples agentes = señal más fuerte
    cross_count = lead.get("_cross_agent_count", 0)
    if cross_count >= 3:
        total += 15
        reasons.insert(0, f"🔗 {cross_count} fuentes cruzadas")
    elif cross_count == 2:
        total += 8

    # ── 9. Inspección próxima (0-8 pts) ──────────────────────────────
    # Leads con inspecciones próximas merecen prioridad (GC en sitio)
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
                reasons.append(f"Inspección en {days_until} días (GC en sitio)")
            elif days_until <= 14:
                total += 6
                reasons.append(f"Inspección en {days_until} días")
            elif days_until <= 30:
                total += 4
                reasons.append(f"Inspección próximo mes")
        except (ValueError, TypeError, AttributeError):
            pass

    # ── Calcular grado ───────────────────────────────────────────
    score = min(int(total), 100)

    if score >= 90:
        grade, emoji = "HOT", "🔥"
    elif score >= 70:
        grade, emoji = "WARM", "🟠"
    elif score >= 50:
        grade, emoji = "MEDIUM", "🟡"
    elif score >= 25:
        grade, emoji = "COOL", "🔵"
    else:
        grade, emoji = "COLD", "⚪"

    return {
        "score":       score,
        "grade":       grade,
        "grade_emoji": emoji,
        "reasons":     reasons[:3],  # Top 3 razones
    }


def format_score_line(scoring: dict) -> str:
    """Formatea el score para Telegram."""
    s = scoring
    reasons_str = " | ".join(s["reasons"]) if s["reasons"] else ""
    return f"{s['grade_emoji']} {s['score']}/100 ({s['grade']})" + (
        f" — {reasons_str}" if reasons_str else ""
    )
