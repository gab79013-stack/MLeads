"""General Contractor buyer-intent filters for the swipe feed.

The GC-facing product should show opportunities where a real GC can still win
work, not projects that already have a GC or specialty contractor assigned.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

GC_OPPORTUNITY_SERVICE_TYPES = {
    "flood",
    "weather",
    "disaster",
    "remodel",
    "permits",
    "deconstruction",
    "realestate",
    "crossdata",
    "rodents",
}

# We keep construction as a supported backend category only for genuinely open,
# pre-award rows. The UI label should never imply "active construction" as a GC
# lead because active jobs usually already have a GC.
PRE_AWARD_CONSTRUCTION_SERVICE_TYPES = {"construction"}

_OPEN_CONTRACTOR_TERMS = (
    "owner builder",
    "owner-builder",
    "homeowner",
    "home owner",
    "property owner",
    "by owner",
    "self",
    "tbd",
    "to be determined",
    "pending",
    "not assigned",
    "unassigned",
    "n/a",
    "none",
    "unknown",
)

_PRE_AWARD_TERMS = (
    "owner-builder",
    "owner builder",
    "seeking bids",
    "request for bids",
    "bid",
    "bidding",
    "planning",
    "pre-construction",
    "preconstruction",
    "permit ready",
    "permit issued",
    "ready for bids",
    "estimate",
    "quote",
    "rfp",
)

_CLOSED_OR_ACTIVE_TERMS = (
    "finaled",
    "final inspection passed",
    "completed",
    "closed",
    "certificate of occupancy issued",
    "co issued",
)

_GC_OR_TRADE_LICENSE_RE = re.compile(
    r"\b(?:CGC|CRC|CBC|CCC|RC|CAC|EC|CFC|C-?39|C-?10|C-?20|C-?36)\d*\b",
    re.IGNORECASE,
)

_SOURCE_URL_FIELDS = (
    "source_url",
    "permit_url",
    "record_url",
    "url",
    "link",
    "source_link",
    "report_url",
)


def _text(*values: Any) -> str:
    return " ".join(str(v or "") for v in values).strip().lower()


def _contractor_name(lead: Mapping[str, Any]) -> str:
    return str(
        lead.get("contractor")
        or lead.get("contractor_name")
        or lead.get("applicant")
        or lead.get("permittee")
        or ""
    ).strip()


def _license_text(lead: Mapping[str, Any]) -> str:
    return _text(
        lead.get("contractor_number"),
        lead.get("lic_number"),
        lead.get("license"),
        lead.get("contractor_license"),
    )


def _contractor_is_open_or_owner(lead: Mapping[str, Any]) -> bool:
    contractor = _contractor_name(lead)
    if not contractor:
        return True

    haystack = _text(contractor)
    if any(term in haystack for term in _OPEN_CONTRACTOR_TERMS):
        return True

    owner = str(lead.get("owner") or lead.get("property_owner") or "").strip().lower()
    if owner and owner == haystack:
        return True

    return False


def _has_assigned_contractor(lead: Mapping[str, Any]) -> bool:
    """True when the row appears awarded to a GC/specialty contractor."""
    if _contractor_is_open_or_owner(lead):
        return False
    if _contractor_name(lead):
        return True
    return bool(_GC_OR_TRADE_LICENSE_RE.search(_license_text(lead)))


def _is_pre_award_signal(lead: Mapping[str, Any]) -> bool:
    phase = _text(
        lead.get("_project_phase"),
        lead.get("project_phase"),
        lead.get("status"),
        lead.get("permit_status"),
    )
    desc = _text(
        lead.get("description"),
        lead.get("desc"),
        lead.get("permit_type"),
        lead.get("_ai_summary"),
    )
    haystack = f"{phase} {desc}"
    return any(term in haystack for term in _PRE_AWARD_TERMS)


def _is_closed_or_completed(lead: Mapping[str, Any]) -> bool:
    haystack = _text(
        lead.get("description"),
        lead.get("desc"),
        lead.get("status"),
        lead.get("permit_status"),
        lead.get("_project_phase"),
        lead.get("project_phase"),
    )
    return any(term in haystack for term in _CLOSED_OR_ACTIVE_TERMS)


def _source_url(lead: Mapping[str, Any]) -> str:
    for field in _SOURCE_URL_FIELDS:
        value = str(lead.get(field) or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return ""


def build_gc_insight(lead: Mapping[str, Any], service_type: str | None) -> dict[str, Any]:
    """Explain why this lead is useful to a General Contractor buyer."""
    service = (service_type or lead.get("primary_service_type") or "").strip().lower()
    desc = _text(lead.get("description"), lead.get("desc"), lead.get("permit_type"), lead.get("_ai_summary"))
    contractor_open = _contractor_is_open_or_owner(lead)
    has_source = bool(_source_url(lead))
    score = 0
    badges: list[str] = []
    reasons: list[str] = []

    if contractor_open:
        score += 25
        if "owner" in _text(_contractor_name(lead)) or "owner" in desc:
            badges.append("Owner-builder")
        else:
            badges.append("Sin GC confirmado")
        reasons.append("No hay GC confirmado; el contacto parece owner-controlled o abierto.")

    if service in {"flood", "weather", "disaster"}:
        score += 25
        badges.append("Daño por tormenta")
        reasons.append("Señal de clima/daño: requiere confirmar daños y alcance antes de venderlo como verificado.")
    elif service == "permits":
        score += 20
        badges.append("Permiso abierto")
        reasons.append("Permiso o registro público indica una oportunidad temprana para cotizar.")
    elif service == "remodel":
        score += 20
        badges.append("Remodelación")
        reasons.append("El alcance parece remodelación/reparación, útil para GC con equipo multi-trade.")
    elif service == "construction":
        score += 20
        badges.append("Pre-award")
        reasons.append("Proyecto en fase temprana o sin contratista confirmado, no construcción activa ya tomada.")
    elif service == "deconstruction":
        score += 20
        badges.append("Rebuild / demolición")
        reasons.append("Demolición o rebuild suele abrir trabajo integral para GC.")
    elif service == "realestate":
        score += 15
        badges.append("Venta reciente")
        reasons.append("Venta/propiedad en transición puede detonar remodelación o reparación.")
    elif service == "crossdata":
        score += 20
        badges.append("Cross-data")
        reasons.append("Varias señales públicas apuntan a una oportunidad; revisar fuente antes de contactar.")

    if lead.get("contact_phone") or lead.get("phone"):
        score += 15
        badges.append("Teléfono disponible")
    if has_source:
        score += 25
        badges.append("Fuente verificable")
    if _is_pre_award_signal(lead):
        score += 10
        if "Etapa temprana" not in badges:
            badges.append("Etapa temprana")

    # Preserve order while removing duplicates.
    badges = list(dict.fromkeys(badges))[:5]
    reasons = list(dict.fromkeys(reasons))[:4]

    if has_source and score >= 70:
        confidence = "verified"
        confidence_label = "Verificado"
    elif score >= 45:
        confidence = "candidate"
        confidence_label = "Candidato"
    else:
        confidence = "exploratory"
        confidence_label = "Exploratorio"

    url = _source_url(lead)
    return {
        "confidence": confidence,
        "confidence_label": confidence_label,
        "badges": badges,
        "reasons": reasons or ["Oportunidad abierta para GC; validar alcance y decisión maker."],
        "source_url": url,
        "source_label": "Fuente verificable" if url else "Fuente no verificada",
    }


def is_gc_interesting_lead(lead: Mapping[str, Any], service_type: str | None) -> bool:
    """Return True only for leads worth showing to a GC buyer.

    Rules:
    - Reject rows already awarded to a contractor unless they are owner-builder /
      owner-controlled / explicitly unassigned.
    - Reject completed/finaled rows.
    - Allow storm/property damage, remodel, open permits, demolition/rebuild,
      real-estate renovation signals, and cross-data rows.
    - Allow `construction` only when it is pre-award/open; active construction is
      not a sellable GC lead.
    """
    service = (service_type or lead.get("primary_service_type") or "").strip().lower()

    if lead.get("_is_dead_lead") or lead.get("is_dead_lead"):
        return False
    if _is_closed_or_completed(lead):
        return False
    if _has_assigned_contractor(lead):
        return False

    if service in PRE_AWARD_CONSTRUCTION_SERVICE_TYPES:
        return _is_pre_award_signal(lead)

    if service in GC_OPPORTUNITY_SERVICE_TYPES:
        return True

    # Permit agents sometimes store the specific trade as primary_service_type but
    # still carry a usable open/owner-builder permit signal in lead_data.
    return _is_pre_award_signal(lead)


def build_gc_interest_sql_filter() -> str:
    """Broad SQLite prefilter; Python `is_gc_interesting_lead` is authoritative."""
    open_terms = [
        "owner%builder",
        "owner-builder",
        "homeowner",
        "property owner",
        "tbd",
        "pending",
        "not assigned",
        "unassigned",
        "n/a",
        "none",
        "unknown",
    ]
    contractor_expr = "LOWER(COALESCE(json_extract(lead_data, '$.contractor'), json_extract(lead_data, '$.contractor_name'), ''))"
    open_clause = " OR ".join([f"{contractor_expr} LIKE '{term}'" for term in open_terms])
    eligible = sorted(GC_OPPORTUNITY_SERVICE_TYPES | PRE_AWARD_CONSTRUCTION_SERVICE_TYPES)
    eligible_list = ", ".join(f"'{s}'" for s in eligible)
    return (
        "(primary_service_type IN (" + eligible_list + ") "
        "OR LOWER(COALESCE(json_extract(lead_data, '$._project_phase'), '')) IN ('planning', 'permitting', 'preconstruction', 'pre-construction')) "
        "AND ("
        f"TRIM({contractor_expr}) = '' OR {open_clause}"
        ")"
    )
