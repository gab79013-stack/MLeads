"""
utils/gc_detector.py  v2
━━━━━━━━━━━━━━━━━━━━━━━━
GC Self-Pull Detector + Trade Reclassifier

When a specialized contractor (e.g., plumbing company) pulls a permit for their
own trade, this is NOT a lead for that trade — it's a lead for the DOWNSTREAM
trades that will be needed as a result of the work.

Example:
  - "Bay Area Plumbing Corp" pulls a sewer replacement permit
  - The plumbing is already being done by that company
  - The OPPORTUNITY is for: drywall repair, paint, flooring (after plumbing is done)
  - So the lead gets RECLASSIFIED from PLUMBING → DRYWALL (primary downstream trade)

Logic:
  1. Detect if the GC/contractor is specialized in the same trade as the permit
  2. If yes (self-pull):
     - Reclassify the primary trade to the #1 downstream trade
     - Mark as self-pull with the original trade
     - Add downstream trades as sub_trades
  3. If no: leave as-is (good lead for the classified trade)

This ensures subcontractors only see leads where they have a REAL opportunity.
"""

import re
import logging

try:
    from utils.opportunity_rules import (
        extract_contractor_name,
        infer_trade_from_license,
        infer_trade_from_text,
        normalize_trade,
    )
except Exception:  # pragma: no cover - keep detector safe during partial installs
    extract_contractor_name = None
    infer_trade_from_license = None
    infer_trade_from_text = None
    normalize_trade = None

logger = logging.getLogger(__name__)

# ── Generic company terms (not trade-specific) ──────────────────────────────
_GENERIC_TERMS = {
    "construction", "contractor", "contractors", "contracting", "builders",
    "builder", "building", "group", "services", "service", "company", "co",
    "inc", "llc", "corp", "enterprises", "enterprise", "solutions", "solution",
    "general", "management", "mgmt", "systems", "associates", "partners",
    "renovations", "renovation", "improvements", "improvement", "remodeling",
    "remodel", "development", "developments", "works", "projects", "project",
    "home", "homes", "property", "properties", "residential", "commercial",
    "professional", "pros", "pro", "expert", "experts",
}

# ── Trade-specific keywords that indicate a SPECIALIZED company ─────────────
_TRADE_GC_KEYWORDS: dict[str, list[str]] = {
    "ROOFING":     ["roof", "roofing", "roofer", "shingle", "tile roof", "reroofing",
                    "reroof", "re-roof", "tpo", "guttermaster", "gutter"],
    "ELECTRICAL":  ["electric", "electrical", "electrician", "wiring", "power",
                    "volt", "ampere", "sparky", "elec"],
    "PLUMBING":    ["plumb", "plumbing", "plumber", "pipe", "pipefitter",
                    "drain", "sewer", "repipe"],
    "HVAC":        ["hvac", "heating", "cooling", "air cond", "refriger", "duct",
                    "therm", "mechanical", "airco", "furnace"],
    "PAINTING":    ["paint", "painting", "painter", "coat", "coating", "finisher",
                    "repaint", "colormaster"],
    "CONCRETE":    ["concrete", "cement", "masonry", "mason", "slab", "flatwork",
                    "driveway spec"],
    "LANDSCAPING": ["landscape", "landscaping", "lawn", "garden", "turf", "sod",
                    "tree", "grass", "mow", "irrigat", "sprinkler"],
    "DRYWALL":     ["drywall", "drywaller", "plaster", "gypsum", "sheetrock",
                    "textur", "taper"],
    "FLOORING":    ["floor", "flooring", "hardwood", "carpet", "tile master",
                    "vinyl floor"],
    "FRAMING":     ["framing", "framer", "lumber", "structural", "stud"],
    "DEMOLITION":  ["demo", "demolition", "wreck", "destruct", "abat", "hazmat"],
    "WINDOWS":     ["window", "glazing", "glass", "fenestration"],
    "INSULATION":  ["insulation", "insulate", "foam", "weatheriz"],
    "SOLAR":       ["solar", "photovolt", "pv install", "sunpower", "solartek"],
    "GENERAL":     [],
}

# ── DOWNSTREAM TRADES: what trades are needed AFTER the self-pull trade ─────
# When a company does work in their trade, what OTHER trades will be needed?
# Format: {SELF_PULL_TRADE: [PRIMARY downstream, secondary, ...]}
# The PRIMARY downstream becomes the new _trade for the lead.
_DOWNSTREAM_TRADES: dict[str, list[str]] = {
    "ROOFING":     ["DRYWALL", "PAINTING", "INSULATION"],
    "ELECTRICAL":  ["DRYWALL", "PAINTING"],          # after electrical rough-in, walls need repair
    "PLUMBING":    ["DRYWALL", "FLOORING", "PAINTING"],  # after plumbing, drywall + floor repair
    "HVAC":        ["DRYWALL", "INSULATION", "PAINTING"], # ductwork needs drywall + insulation
    "PAINTING":    ["DRYWALL"],                        # painter doing their own = drywall prep needed
    "CONCRETE":    ["PLUMBING", "FRAMING"],            # after slab, plumbing rough-in + framing
    "LANDSCAPING": ["CONCRETE", "FLOORING"],           # hardscape needs concrete work
    "DRYWALL":     ["PAINTING", "INSULATION"],         # after drywall, paint + insulation
    "FLOORING":    ["CONCRETE", "PAINTING"],           # floor install may need subfloor + baseboard paint
    "FRAMING":     ["ELECTRICAL", "PLUMBING", "DRYWALL"], # after framing, MEP rough-in + drywall
    "DEMOLITION":  ["FRAMING", "DRYWALL", "CONCRETE"], # after demo, rebuild needed
    "WINDOWS":     ["DRYWALL", "PAINTING", "INSULATION"], # window install needs drywall + paint + insulation
    "INSULATION":  ["DRYWALL", "PAINTING"],            # after insulation, walls need closing up
    "SOLAR":       ["ELECTRICAL", "ROOFING"],          # solar needs electrical + roof work
    "GENERAL":     [],
}

# ── Downstream trade labels for the UI ──────────────────────────────────────
_DOWNSTREAM_LABELS = {
    "ROOFING":     "Roof done → needs drywall/paint repair",
    "ELECTRICAL":  "Electrical done → needs drywall/paint patch",
    "PLUMBING":    "Plumbing done → needs drywall/floor/paint repair",
    "HVAC":        "HVAC done → needs drywall/insulation/paint",
    "PAINTING":    "Paint done → needs drywall prep",
    "CONCRETE":    "Slab done → needs plumbing/framing",
    "LANDSCAPING": "Landscape done → needs concrete/flooring",
    "DRYWALL":     "Drywall done → needs paint/insulation",
    "FLOORING":    "Floor done → needs subfloor/paint",
    "FRAMING":     "Framing done → needs electrical/plumbing/drywall",
    "DEMOLITION":  "Demo done → needs framing/drywall/concrete rebuild",
    "WINDOWS":     "Windows done → needs drywall/paint/insulation",
    "INSULATION":  "Insulation done → needs drywall/paint closing",
    "SOLAR":       "Solar install → needs electrical/roofing",
    "GENERAL":     "",
}

_COMPANY_SUFFIXES = re.compile(
    r"\b(llc|inc|corp|co|ltd|lp|plc|dba|doing business as|pllc|pc)\b\.?$",
    re.IGNORECASE,
)
_NONALPHA = re.compile(r"[^a-z0-9 ]")


def _normalize_name(name: str) -> str:
    s = name.lower().strip()
    s = _COMPANY_SUFFIXES.sub("", s).strip()
    s = _NONALPHA.sub(" ", s)
    return " ".join(s.split())


def _tokens(name: str) -> set[str]:
    return set(_normalize_name(name).split())


def detect_gc_self_pull(lead: dict) -> dict:
    """Detect if GC is specialized in the same trade as the permit."""
    gc_raw = (
        extract_contractor_name(lead) if extract_contractor_name else (
            lead.get("contractor")
            or lead.get("gc_name")
            or lead.get("owner")
            or ""
        )
    ).strip()

    detected_trade = (
        lead.get("_trade")
        or lead.get("trade")
        or (infer_trade_from_text(lead) if infer_trade_from_text else None)
        or "GENERAL"
    )
    trade = normalize_trade(detected_trade) if normalize_trade else str(detected_trade).upper()
    license_trade = infer_trade_from_license(lead) if infer_trade_from_license else None

    if not gc_raw and license_trade != trade:
        return _no_match(gc_raw)
    if trade == "GENERAL":
        return _no_match(gc_raw)

    trade_keywords = _TRADE_GC_KEYWORDS.get(trade, [])
    if not trade_keywords and license_trade != trade:
        return _no_match(gc_raw)

    gc_norm = _normalize_name(gc_raw)
    gc_toks = _tokens(gc_raw)

    non_generic_toks = gc_toks - _GENERIC_TERMS
    if not non_generic_toks and license_trade != trade:
        return _no_match(gc_raw)

    matched_kw = None
    match_source = "name"
    for kw in trade_keywords:
        if kw in gc_norm:
            matched_kw = kw
            break

    if license_trade == trade:
        matched_kw = matched_kw or f"license:{license_trade}"
        match_source = "license"

    if not matched_kw:
        return _no_match(gc_raw)

    confidence = 0.5
    if match_source == "license":
        confidence = 0.95
    elif any(matched_kw in tok for tok in non_generic_toks):
        confidence = 0.85
    if len(non_generic_toks) == 1:
        confidence = min(confidence + 0.1, 0.98)
    if len(non_generic_toks) >= 4 and match_source != "license":
        confidence = max(confidence - 0.15, 0.40)

    is_self_pull = confidence >= 0.5

    reason = (
        f"GC '{gc_raw}' is a {trade.title()} specialist "
        f"({match_source}: '{matched_kw}', conf: {confidence:.0%})"
    )
    logger.debug(f"[gc_detector] {reason}")

    return {
        "is_self_pull":    is_self_pull,
        "confidence":      round(confidence, 2),
        "reason":          reason,
        "matched_keyword": matched_kw,
        "gc_name":         gc_raw,
        "original_trade":  trade,
        "downstream_trades": _DOWNSTREAM_TRADES.get(trade, []),
        "downstream_label":  _DOWNSTREAM_LABELS.get(trade, ""),
    }


def _no_match(gc_name: str) -> dict:
    return {
        "is_self_pull":      False,
        "confidence":        0.0,
        "reason":            "",
        "matched_keyword":   "",
        "gc_name":           gc_name,
        "original_trade":    "",
        "downstream_trades": [],
        "downstream_label":  "",
    }


# ── AI Trade mapping ────────────────────────────────────────────────────────
_AI_TRADE_MAP = {
    "ROOFING": "roofing", "ELECTRICAL": "electrical", "DRYWALL": "drywall",
    "PAINTING": "paint", "LANDSCAPING": "landscaping", "HVAC": "hvac",
    "PLUMBING": "plumbing", "INSULATION": "insulation", "FRAMING": "framing",
    "CONCRETE": "concrete", "FLOORING": "flooring", "WINDOWS": "windows",
    "DEMOLITION": "demolition", "GENERAL": "general", "UNKNOWN": "unknown",
}


def enrich_lead_with_gc_detection(lead: dict) -> dict:
    """
    Detect GC self-pull and RECLASSIFY the trade to downstream.
    
    If a plumbing company pulls a plumbing permit:
      - _trade changes from PLUMBING → DRYWALL (primary downstream)
      - _sub_trades becomes [PAINTING, FLOORING] (other downstream)
      - _is_gc_self_pull = True
      - _original_trade = PLUMBING (preserved for reference)
      - primary_service_type updated to the new trade
      
    This ensures the lead appears for the RIGHT subcontractor.
    """
    result = detect_gc_self_pull(lead)

    lead["_is_gc_self_pull"] = result["is_self_pull"]
    lead["_gc_pull_reason"]  = result["reason"]
    lead["_gc_pull_conf"]    = result["confidence"]

    if result["is_self_pull"]:
        original_trade = result["original_trade"]
        downstream = result["downstream_trades"]
        
        if downstream:
            new_primary_trade = downstream[0]  # e.g., PLUMBING → DRYWALL
            new_sub_trades = downstream[1:]     # e.g., [PAINTING, FLOORING]
            
            # Store the original trade for reference
            lead["_original_trade"] = original_trade
            
            # Reclassify!
            lead["_trade"] = new_primary_trade
            lead["_sub_trades"] = new_sub_trades
            
            # Update primary_service_type for swipe filtering
            new_type = _AI_TRADE_MAP.get(new_primary_trade, "general")
            lead["primary_service_type"] = new_type
            
            # Update key_pain_point
            lead["_key_pain_point"] = result["downstream_label"]
            
            # Update AI summary to reflect the reclassification
            original_lower = _AI_TRADE_MAP.get(original_trade, "general")
            lead["_ai_summary"] = (
                f"{original_trade.title()} work by {result.get('gc_name', 'GC')} — "
                f"downstream opportunity for {new_primary_trade.title()} subcontractor."
            )
            
            # Update urgency — downstream work is typically MEDIUM (after primary work completes)
            if lead.get("_urgency") == "HIGH":
                lead["_urgency"] = "MEDIUM"
                lead["_urgency_reason"] = f"Downstream work after {original_trade.lower()} by GC"
            
            logger.info(
                f"[gc_detector] Reclassified: {original_trade} → {new_primary_trade} "
                f"(GC: {result.get('gc_name', '')})"
            )

        # Score penalty: self-pull leads are less valuable (the GC controls the job)
        if lead.get("_scoring"):
            conf = result["confidence"]
            # Smaller penalty now — the lead IS valuable, just for a different trade
            penalty = int(15 * conf)
            old_score = lead["_scoring"].get("score", 0)
            new_score = max(old_score - penalty, 0)
            lead["_scoring"]["score"] = new_score
            lead["_scoring"]["reasons"].append(
                f"GC self-pull: {result['matched_keyword']} → reclassified to {lead.get('_trade', '')}"
            )
            # Recalculate grade
            if new_score >= 90:   grade, emoji = "HOT",    "🔥"
            elif new_score >= 70: grade, emoji = "WARM",   "🌡️"
            elif new_score >= 50: grade, emoji = "MEDIUM", "🟡"
            elif new_score >= 25: grade, emoji = "COOL",   "🔵"
            else:                 grade, emoji = "COLD",   "⚪"
            lead["_scoring"]["grade"]       = grade
            lead["_scoring"]["grade_emoji"] = emoji

    return lead
