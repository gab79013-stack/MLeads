"""
utils/tripartite_scoring.py
📊 Tripartite Scoring Engine — Un lead, tres perspectivas

Cada lead recibe 3 scores independientes:
  1. subcontractor_score (0-100): fit técnico para el sub
  2. gc_score (0-100): valor comercial para el GC
  3. insurance_score (0-100): probabilidad de claim para aseguradora

Factores por score:
  ────────────────────────────────────────────────────────────
  SUBCONTRACTOR:
    + Especialidad del sub coincide con tipo de trabajo (30%)
    + Proximidad geográfica (20%)
    + Tiene licencia activa para ese trabajo (15%)
    + Disponibilidad (lead timing vs. schedule) (15%)
    + Valor del trabajo (sub quiere jobs grandes) (10%)
    + Ratio de repetición (GC que repite = estable) (10%)

  GC (General Contractor):
    + Valor del proyecto (25%)
    + Probabilidad de cierre (contact quality) (20%)
    + Propiedad en zona de desastre (15%)
    + Property DNA: año construcción + material techo (15%)
    + Cross-source signals (múltiples agentes detectaron) (15%)
    + Timing (inspección próxima, urgencia) (10%)

  INSURANCE:
    + Zona de flood/FEMA declarada (25%)
    + Property DNA: edad del edificio + material (20%)
    + Valor de la propiedad (15%)
    + Evento de desastre activo en el área (15%)
    + Historial de claims previos (15%)
    + Tipo de daño (agua > fuego > viento) (10%)
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Trade-to-disaster mapping ──────────────────────────────────────────────
TRADE_DISASTER_AFFINITY = {
    "ROOFING":     {"wind": 0.9, "hail": 0.95, "wildfire": 0.8, "severe_storm": 0.85, "flood": 0.6},
    "DRYWALL":     {"flood": 0.95, "wildfire": 0.8, "severe_storm": 0.5, "wind": 0.4},
    "PAINTING":    {"flood": 0.8, "wildfire": 0.6, "severe_storm": 0.4},
    "ELECTRICAL":  {"flood": 0.7, "wildfire": 0.7, "severe_storm": 0.5},
    "LANDSCAPING": {"flood": 0.5, "wildfire": 0.3, "severe_storm": 0.3},
    "HVAC":        {"wildfire": 0.6, "severe_storm": 0.4},
    "PLUMBING":    {"flood": 0.7, "earthquake": 0.6},
    "CONCRETE":    {"earthquake": 0.8, "flood": 0.5},
    "DEMOLITION":  {"wildfire": 0.7, "flood": 0.4, "earthquake": 0.6},
    "FRAMING":     {"earthquake": 0.8, "wind": 0.7, "flood": 0.4},
    "INSULATION":  {"flood": 0.7, "wildfire": 0.5},
    "FLOORING":    {"flood": 0.8, "wildfire": 0.4},
    "WINDOWS":     {"wind": 0.8, "hail": 0.7, "flood": 0.3},
}

# Damage type severity for insurance
DAMAGE_SEVERITY = {
    "water":  0.8,   # Water damage = high claim probability
    "fire":   0.9,   # Fire = very high
    "wind":   0.6,   # Wind = moderate
    "hail":   0.7,   # Hail = moderate-high
    "earth":  0.85,  # Earthquake = very high
    "mold":   0.75,  # Mold = high (follows water)
}


def calculate_tripartite_scores(lead: dict) -> dict:
    """
    Calculate all three scores for a lead.
    
    Returns:
        {
            "subcontractor_score": int (0-100),
            "gc_score": int (0-100),
            "insurance_score": int (0-100),
            "sub_factors": list[str],
            "gc_factors": list[str],
            "ins_factors": list[str],
        }
    """
    sub = _calc_sub_score(lead)
    gc = _calc_gc_score(lead)
    ins = _calc_insurance_score(lead)

    return {
        "subcontractor_score": sub["score"],
        "gc_score": gc["score"],
        "insurance_score": ins["score"],
        "sub_factors": sub["factors"],
        "gc_factors": gc["factors"],
        "ins_factors": ins["factors"],
    }


def _calc_sub_score(lead: dict) -> dict:
    """Calculate subcontractor-specific score."""
    score = 0
    factors = []

    # 1. Trade match (30%)
    trade = lead.get("_trade", "").upper()
    description = ((lead.get("description") or "") + " " + (lead.get("permit_type") or "")).lower()

    trade_keywords = {
        "ROOFING": ["roof", "reroof", "shingle", "tile roof", "flat roof"],
        "DRYWALL": ["drywall", "sheetrock", "wall board", "taping", "texture"],
        "PAINTING": ["paint", "painting", "repaint", "stucco", "primer"],
        "ELECTRICAL": ["electrical", "panel", "wiring", "ev charger", "200 amp"],
        "LANDSCAPING": ["landscape", "hardscape", "irrigation", "sprinkler", "paver"],
        "DEMOLITION": ["demolition", "demo", "tear down", "abatement"],
        "HVAC": ["hvac", "heating", "cooling", "duct", "furnace"],
        "PLUMBING": ["plumbing", "water heater", "sewer", "drain", "pipe"],
        "CONCRETE": ["concrete", "slab", "driveway", "foundation", "footing"],
        "FRAMING": ["framing", "structural", "shear wall"],
        "FLOORING": ["flooring", "hardwood", "vinyl plank", "carpet", "laminate"],
        "WINDOWS": ["window", "glazing", "fenestration"],
        "INSULATION": ["insulation", "insulate", "title 24", "energy retrofit"],
    }

    if trade in trade_keywords:
        matches = sum(1 for kw in trade_keywords[trade] if kw in description)
        if matches >= 3:
            score += 30
            factors.append(f"Strong {trade} match ({matches} keywords)")
        elif matches >= 1:
            score += 20
            factors.append(f"Partial {trade} match")
        else:
            score += 10  # Trade assigned but no keyword match
    else:
        score += 5

    # 2. Geographic proximity (20%)
    # If the lead is in a city the sub serves, high score
    # (This is evaluated at routing time, so we use city tier as proxy)
    city = (lead.get("city") or "").lower()
    high_demand = {"san francisco", "oakland", "berkeley", "san jose",
                   "richmond", "hayward", "fremont"}
    if city in high_demand:
        score += 20
        factors.append(f"High-demand city: {city.title()}")
    else:
        score += 12

    # 3. License requirement match (15%)
    # Jobs requiring permits = need licensed sub = valuable lead
    has_permit = bool(lead.get("permit_id") or lead.get("permit_number"))
    if has_permit:
        score += 15
        factors.append("Permit-verified project")
    else:
        score += 7

    # 4. Timing (15%)
    next_insp = lead.get("next_scheduled_inspection_date")
    if next_insp:
        try:
            insp_date = next_insp[:10] if isinstance(next_insp, str) else str(next_insp)
            score += 15
            factors.append(f"Inspection: {insp_date}")
        except:
            score += 8
    else:
        score += 5

    # 5. Project value (10%)
    value = lead.get("value_float", 0)
    if value >= 100000:
        score += 10
        factors.append(f"High-value: ${value:,.0f}")
    elif value >= 50000:
        score += 7
    elif value > 0:
        score += 4
    else:
        score += 3

    # 6. Disaster signal (10% bonus)
    disaster_type = lead.get("_disaster_type", "")
    if disaster_type and trade in TRADE_DISASTER_AFFINITY:
        affinity = TRADE_DISASTER_AFFINITY[trade].get(disaster_type, 0)
        bonus = int(affinity * 10)
        score += bonus
        if bonus >= 7:
            factors.append(f"Disaster alignment: {disaster_type} → {trade}")

    return {"score": min(int(score), 100), "factors": factors[:3]}


def _calc_gc_score(lead: dict) -> dict:
    """Calculate General Contractor-specific score."""
    score = 0
    factors = []

    # 1. Project value (25%)
    value = lead.get("value_float", 0)
    if value >= 500000:
        score += 25
        factors.append(f"Mega project: ${value:,.0f}")
    elif value >= 200000:
        score += 20
        factors.append(f"Large project: ${value:,.0f}")
    elif value >= 100000:
        score += 15
    elif value >= 50000:
        score += 10
    elif value > 0:
        score += 5

    # 2. Contact quality (20%)
    has_phone = bool(lead.get("contact_phone"))
    has_email = bool(lead.get("contact_email"))
    has_gc = bool(lead.get("contractor") or lead.get("gc_name"))
    has_owner = bool(lead.get("owner"))

    contact_pts = 0
    if has_phone: contact_pts += 8
    if has_email: contact_pts += 6
    if has_gc: contact_pts += 4
    if has_owner: contact_pts += 2
    score += min(contact_pts, 20)

    if has_phone and has_email:
        factors.append("Full contact info")
    elif has_phone:
        factors.append("Phone available")

    # 3. Disaster zone (15%)
    disaster_type = lead.get("_disaster_type", "")
    if disaster_type:
        score += 15
        factors.append(f"In disaster zone: {disaster_type}")
    elif lead.get("flood_zone"):
        score += 10
        factors.append(f"Flood zone: {lead['flood_zone']}")

    # 4. Property DNA (15%)
    year_built = lead.get("property_year_built")
    roof_material = lead.get("property_roof_material", "")
    property_value = lead.get("property_value", 0)

    dna_pts = 0
    if year_built and year_built < 1970:
        dna_pts += 8
        factors.append(f"Old building ({year_built})")
    elif year_built and year_built < 1990:
        dna_pts += 5

    if "shake" in roof_material or "tar" in roof_material:
        dna_pts += 7
        factors.append(f"Aging roof: {roof_material}")

    if property_value and property_value > 800000:
        dna_pts += 5

    score += min(dna_pts, 15)

    # 5. Cross-source signals (15%)
    cross_count = lead.get("_cross_agent_count", 0)
    if cross_count >= 3:
        score += 15
        factors.append(f"🔗 {cross_count} sources detected")
    elif cross_count == 2:
        score += 8
    elif lead.get("agent_sources", "").count(",") >= 1:
        score += 6  # Multiple agent sources in consolidated

    # 6. Timing (10%)
    next_insp = lead.get("next_scheduled_inspection_date")
    if next_insp:
        score += 10
        factors.append("Inspection upcoming")
    else:
        score += 4

    # Base from existing generic score
    existing_score = lead.get("_scoring", {}).get("score", 0)
    if existing_score >= 80:
        score = max(score, int(score * 0.7 + existing_score * 0.3))

    return {"score": min(int(score), 100), "factors": factors[:3]}


def _calc_insurance_score(lead: dict) -> dict:
    """Calculate Insurance-specific score (claim probability)."""
    score = 0
    factors = []

    # 1. Flood zone / FEMA (25%)
    flood_zone = lead.get("flood_zone", "")
    disaster_type = lead.get("_disaster_type", "")

    if disaster_type == "flood":
        score += 25
        factors.append("Active flood event")
    elif disaster_type:
        score += 15
        factors.append(f"Active disaster: {disaster_type}")

    if flood_zone:
        zone_code = flood_zone.split(" ")[0].upper()
        if zone_code in ("A", "AE", "AH", "AO", "VE", "V"):
            score += 20
            factors.append(f"High-risk flood zone: {zone_code}")
        elif zone_code in ("X", "X500", "B", "C"):
            score += 5  # Moderate/minimal risk
    elif not disaster_type:
        score += 5  # No flood info, no disaster = low

    # 2. Property DNA: age + material (20%)
    year_built = lead.get("property_year_built")
    roof_material = lead.get("property_roof_material", "")

    age_pts = 0
    if year_built:
        age = datetime.utcnow().year - year_built
        if age > 60:
            age_pts += 15
            factors.append(f"Aging property ({age}y old)")
        elif age > 40:
            age_pts += 10
        elif age > 20:
            age_pts += 5

    if "shake" in roof_material or "wood" in roof_material:
        age_pts += 8
        factors.append("Combustible roof material")
    elif "tar" in roof_material or "flat" in roof_material:
        age_pts += 5  # Flat roofs = water pooling risk

    score += min(age_pts, 20)

    # 3. Property value (15%)
    property_value = lead.get("property_value", 0)
    value = lead.get("value_float", 0)
    max_value = max(property_value, value)

    if max_value >= 1000000:
        score += 15
        factors.append(f"High-value property: ${max_value:,.0f}")
    elif max_value >= 500000:
        score += 10
    elif max_value >= 250000:
        score += 7
    elif max_value > 0:
        score += 4

    # 4. Active disaster in area (15%)
    # Already counted above, but separate factor
    if disaster_type:
        damage_severity = DAMAGE_SEVERITY.get(disaster_type, 0.5)
        disaster_pts = int(damage_severity * 15)
        score += disaster_pts
    else:
        score += 2

    # 5. Claim history proxy (15%)
    # We don't have actual claim data, but:
    # - Multiple permits on same address = repeated work = likely claims
    # - Rodent/pest signals = potential damage claims
    agent_sources = lead.get("agent_sources", "")
    if "rodents" in agent_sources:
        score += 10
        factors.append("Pest damage indicator")
    if agent_sources.count(",") >= 2:
        score += 8
        factors.append("Multiple issues detected")

    # 6. Damage type (10%)
    # Inferred from disaster type + trade
    trade = lead.get("_trade", "").upper()
    damage_type = _infer_damage_type(disaster_type, trade)
    severity = DAMAGE_SEVERITY.get(damage_type, 0.3)
    score += int(severity * 10)
    if severity >= 0.7:
        factors.append(f"High-severity damage: {damage_type}")

    return {"score": min(int(score), 100), "factors": factors[:3]}


def _infer_damage_type(disaster_type: str, trade: str) -> str:
    """Infer the type of damage from disaster and trade context."""
    if disaster_type == "flood":
        return "water"
    if disaster_type == "wildfire":
        return "fire"
    if disaster_type in ("earthquake",):
        return "earth"
    if disaster_type in ("wind", "tornado", "severe_storm"):
        return "wind"
    if disaster_type == "hail":
        return "hail"

    # Infer from trade
    trade_damage = {
        "ROOFING": "water", "DRYWALL": "water", "PAINTING": "water",
        "ELECTRICAL": "water", "PLUMBING": "water", "DEMOLITION": "fire",
    }
    return trade_damage.get(trade, "water")


def format_tripartite_summary(scores: dict) -> str:
    """Format tripartite scores for display (Telegram, dashboard)."""
    sub = scores.get("subcontractor_score", 0)
    gc = scores.get("gc_score", 0)
    ins = scores.get("insurance_score", 0)

    def grade(s):
        if s >= 90: return "🔥"
        if s >= 70: return "🟠"
        if s >= 50: return "🟡"
        if s >= 25: return "🔵"
        return "⚪"

    return (
        f"👷 Sub: {grade(sub)} {sub}/100  "
        f"🏗️ GC: {grade(gc)} {gc}/100  "
        f"🏢 Ins: {grade(ins)} {ins}/100"
    )
