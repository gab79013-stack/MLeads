"""
utils/gc_detector.py
━━━━━━━━━━━━━━━━━━━━
GC Self-Pull Detector — "Lead Muerto"

Un lead es "muerto" cuando el GC que saca el permiso ES el mismo contratista
especializado en ese trade.  Ejemplo:
  - Permiso: "re-roof $25,000"  → trade: ROOFING
  - GC:      "Margaroof LLC"    → nombre contiene "roof"
  → El roofer saca su propio permiso. No hay oportunidad para un sub.

Lógica (rule-based, sin costo de API):
  1. Determinar el trade del permiso (_trade del lead)
  2. Extraer el nombre del GC / contractor
  3. Tokenizar y comparar contra palabras clave del mismo trade
  4. Calcular confidence: 0.0 – 1.0
     - HIGH  (≥ 0.8): bloquear lead en swipe feed
     - MED   (≥ 0.5): penalizar score -30 pts, mostrar badge
     - LOW   (< 0.5): no hacer nada

Palabras clave genéricas que NO implican especialización:
  "construction", "contractor", "builders", "group", "services",
  "company", "co", "inc", "llc", "corp", "enterprises", etc.

Se llama desde base.py después de la clasificación AI.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Palabras clave que indican empresa GENÉRICA (no especialista) ────────────
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

# ── Keywords por trade que indican empresa ESPECIALIZADA ────────────────────
# Solo se dispara cuando el keyword del GC coincide con el MISMO trade del permiso
_TRADE_GC_KEYWORDS: dict[str, list[str]] = {
    "ROOFING":     ["roof", "roofing", "roofer", "shingle", "tile roof", "reroofing",
                    "reroof", "re-roof", "tpo", "guttermaster", "gutter"],
    "ELECTRICAL":  ["electric", "electrical", "electrician", "wiring", "power",
                    "volt", "ampere", "sparky", "elec"],
    "PLUMBING":    ["plumb", "plumbing", "plumber", "pipe", "pipefitter",
                    "drain", "sewer", "repipe"],
    "HVAC":        ["hvac", "heating", "cooling", "air cond", "refriger", "duct",
                    "therm", "mechanical", "hvac", "airco", "furnace"],
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
    "GENERAL":     [],  # genérico — no aplica auto-pull
}

# ── Sufijos de empresa a remover antes de comparar ──────────────────────────
_COMPANY_SUFFIXES = re.compile(
    r"\b(llc|inc|corp|co|ltd|lp|plc|dba|doing business as|pllc|pc)\b\.?$",
    re.IGNORECASE,
)

# Caracteres no-alfanuméricos (excepto espacio)
_NONALPHA = re.compile(r"[^a-z0-9 ]")


def _normalize_name(name: str) -> str:
    """Limpia y normaliza el nombre del GC."""
    s = name.lower().strip()
    s = _COMPANY_SUFFIXES.sub("", s).strip()
    s = _NONALPHA.sub(" ", s)
    return " ".join(s.split())


def _tokens(name: str) -> set[str]:
    """Conjunto de palabras del nombre normalizado."""
    return set(_normalize_name(name).split())


def detect_gc_self_pull(lead: dict) -> dict:
    """
    Analiza si el GC del lead es un especialista en el mismo trade del permiso.

    Args:
        lead: dict del lead con _trade, contractor / lic, description, etc.

    Returns:
        {
          "is_self_pull":    bool,
          "confidence":      float 0.0–1.0,
          "reason":          str (descripción legible del motivo),
          "matched_keyword": str (palabra que disparó la detección),
          "gc_name":         str,
        }
    """
    gc_raw = (
        lead.get("contractor")
        or lead.get("gc_name")
        or lead.get("owner")    # some permits only have owner
        or ""
    ).strip()

    trade = (lead.get("_trade") or "GENERAL").upper()

    # Sin GC o trade genérico → no hay nada que detectar
    if not gc_raw or trade == "GENERAL":
        return _no_match(gc_raw)

    trade_keywords = _TRADE_GC_KEYWORDS.get(trade, [])
    if not trade_keywords:
        return _no_match(gc_raw)

    gc_norm = _normalize_name(gc_raw)
    gc_toks = _tokens(gc_raw)

    # Filtrar tokens genéricos — si el GC tiene solo palabras genéricas, no es especialista
    non_generic_toks = gc_toks - _GENERIC_TERMS
    if not non_generic_toks:
        return _no_match(gc_raw)

    # Buscar coincidencias con keywords del trade
    matched_kw = None
    for kw in trade_keywords:
        if kw in gc_norm:
            matched_kw = kw
            break

    if not matched_kw:
        return _no_match(gc_raw)

    # Calcular confidence según cuán específico es el match
    confidence = 0.5

    # Boost: el keyword está en los tokens no-genéricos (word boundary)
    if any(matched_kw in tok for tok in non_generic_toks):
        confidence = 0.85

    # Boost adicional: el keyword es el ÚNICO token no-genérico
    # (ej: "Margaroof" → después de normalizar casi toda la palabra es el keyword)
    if len(non_generic_toks) == 1:
        confidence = min(confidence + 0.1, 0.98)

    # Reducir si hay varios tokens no-genéricos variados (empresa mixta)
    if len(non_generic_toks) >= 4:
        confidence = max(confidence - 0.15, 0.40)

    is_self_pull = confidence >= 0.5

    reason = (
        f"GC '{gc_raw}' parece ser un especialista en {trade.title()} "
        f"(keyword: '{matched_kw}', confidence: {confidence:.0%})"
    )
    logger.debug(f"[gc_detector] {reason}")

    return {
        "is_self_pull":    is_self_pull,
        "confidence":      round(confidence, 2),
        "reason":          reason,
        "matched_keyword": matched_kw,
        "gc_name":         gc_raw,
    }


def _no_match(gc_name: str) -> dict:
    return {
        "is_self_pull":    False,
        "confidence":      0.0,
        "reason":          "",
        "matched_keyword": "",
        "gc_name":         gc_name,
    }


def enrich_lead_with_gc_detection(lead: dict) -> dict:
    """
    Añade los campos de detección GC al lead in-place.
    Aplica penalización de score si es self-pull.

    Campos añadidos:
      _is_gc_self_pull  bool
      _gc_pull_reason   str
      _gc_pull_conf     float
    """
    result = detect_gc_self_pull(lead)

    lead["_is_gc_self_pull"] = result["is_self_pull"]
    lead["_gc_pull_reason"]  = result["reason"]
    lead["_gc_pull_conf"]    = result["confidence"]

    if result["is_self_pull"] and lead.get("_scoring"):
        conf   = result["confidence"]
        penalty = int(50 * conf)   # hasta -50 pts en confidence 1.0
        old_score = lead["_scoring"].get("score", 0)
        new_score = max(old_score - penalty, 0)
        lead["_scoring"]["score"] = new_score
        lead["_scoring"]["reasons"].append(
            f"⚠️ GC self-pull detectado ({result['matched_keyword']}) −{penalty} pts"
        )
        # Re-calcular grade
        if new_score >= 90:   grade, emoji = "HOT",    "🔥"
        elif new_score >= 70: grade, emoji = "WARM",   "🌡️"
        elif new_score >= 50: grade, emoji = "MEDIUM", "🟡"
        elif new_score >= 25: grade, emoji = "COOL",   "🔵"
        else:                 grade, emoji = "COLD",   "⚪"
        lead["_scoring"]["grade"]       = grade
        lead["_scoring"]["grade_emoji"] = emoji

    return lead
