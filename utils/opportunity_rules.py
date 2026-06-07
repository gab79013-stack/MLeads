"""
utils/opportunity_rules.py
━━━━━━━━━━━━━━━━━━━━━━━━━━
Central rules for deciding which subcontractor trade has a REAL opportunity.

Important distinction:
- Needed trade: what the permit/work description says the project is about.
- Opportunity trade: who can still sell work on the job.

If a trade-specialty contractor already pulled the permit for that same trade,
that trade is considered taken. The lead should not be routed back to companies
in that trade; it can be routed only to downstream/adjacent trades if useful.
"""

from __future__ import annotations

import re

TRADE_TO_SERVICE = {
    "ROOFING": "roofing",
    "ELECTRICAL": "electrical",
    "DRYWALL": "drywall",
    "PAINTING": "paint",
    "LANDSCAPING": "landscaping",
    "HVAC": "hvac",
    "PLUMBING": "plumbing",
    "INSULATION": "insulation",
    "FRAMING": "framing",
    "CONCRETE": "concrete",
    "FLOORING": "flooring",
    "WINDOWS": "windows",
    "DEMOLITION": "deconstruction",
    "SOLAR": "solar",
}

SERVICE_TO_TRADE = {v: k for k, v in TRADE_TO_SERVICE.items()}

LICENSE_TRADE_PREFIXES = {
    # Florida DBPR / Miami-Dade common prefixes
    "CCC": "ROOFING",       # Certified roofing contractor
    "RC": "ROOFING",        # Registered roofing contractor (often written RC00...)
    "CAC": "HVAC",
    "CMC": "HVAC",
    "EC": "ELECTRICAL",
    "ER": "ELECTRICAL",
    "CFC": "PLUMBING",
    "RF": "PLUMBING",
    # California CSLB classifications often present in imported contacts/leads
    "C39": "ROOFING",
    "C-39": "ROOFING",
    "C10": "ELECTRICAL",
    "C-10": "ELECTRICAL",
    "C36": "PLUMBING",
    "C-36": "PLUMBING",
    "C20": "HVAC",
    "C-20": "HVAC",
    "C33": "PAINTING",
    "C-33": "PAINTING",
    "C9": "DRYWALL",
    "C-9": "DRYWALL",
    "C8": "CONCRETE",
    "C-8": "CONCRETE",
    "C15": "FLOORING",
    "C-15": "FLOORING",
    "C5": "FRAMING",
    "C-5": "FRAMING",
    "C17": "WINDOWS",
    "C-17": "WINDOWS",
    "C2": "INSULATION",
    "C-2": "INSULATION",
    "C21": "DEMOLITION",
    "C-21": "DEMOLITION",
    "C27": "LANDSCAPING",
    "C-27": "LANDSCAPING",
}

LICENSE_FIELDS = (
    "lic", "license", "license_number", "lic_number", "contractor_license",
    "contractor_license_number", "contractor_number", "contractor_no",
    "license_no", "state_license", "qualifier_license",
    "licenseNumber", "contractorNumber", "ContractorNumber",
    "Contractor_License", "LicenseNumber", "LicNum", "LicNo",
)

CONTRACTOR_FIELDS = (
    "contractor", "gc_name", "contractor_name", "contractor_business_name",
    "business_name", "company", "applicant", "permittee", "owner",
    "Contractor", "ContractorName", "CONTRACTOR", "CONTRACTOR_NAME",
    "BusinessName", "Company", "Applicant", "Permittee", "Owner",
)

_NON_ALNUM = re.compile(r"[^A-Z0-9-]")


def normalize_trade(value: str | None) -> str:
    text = (value or "").strip().upper().replace(" ", "_")
    aliases = {
        "DECONSTRUCTION": "DEMOLITION",
        "DEMO": "DEMOLITION",
        "PAINT": "PAINTING",
        "ROOF": "ROOFING",
        "ELECTRIC": "ELECTRICAL",
        "WINDOW": "WINDOWS",
    }
    return aliases.get(text, text)


def trade_to_service(trade: str | None) -> str | None:
    return TRADE_TO_SERVICE.get(normalize_trade(trade))


def service_to_trade(service: str | None) -> str | None:
    return SERVICE_TO_TRADE.get((service or "").strip().lower())


def _iter_license_values(lead: dict):
    for field in LICENSE_FIELDS:
        value = lead.get(field)
        if value:
            yield str(value)

    raw = lead.get("raw")
    if isinstance(raw, dict):
        lower_raw = {str(k).lower(): v for k, v in raw.items()}
        for field in LICENSE_FIELDS:
            value = raw.get(field) or raw.get(field.upper()) or raw.get(field.title())
            if value is None:
                value = lower_raw.get(field.lower())
            if value:
                yield str(value)


def extract_contractor_name(lead: dict) -> str:
    """Return the best contractor/permittee name available on a normalized or raw lead."""
    for field in CONTRACTOR_FIELDS:
        value = lead.get(field)
        if value:
            return str(value).strip()

    raw = lead.get("raw")
    if isinstance(raw, dict):
        lower_raw = {str(k).lower(): v for k, v in raw.items()}
        for field in CONTRACTOR_FIELDS:
            value = raw.get(field) or raw.get(field.upper()) or raw.get(field.title())
            if value is None:
                value = lower_raw.get(field.lower())
            if value:
                return str(value).strip()
    return ""


def infer_trade_from_license(lead: dict) -> str | None:
    """Infer contractor specialty from license/classification codes on a lead."""
    values = list(_iter_license_values(lead))
    classification = lead.get("classification") or lead.get("classification_code")
    if classification:
        values.append(str(classification))

    for value in values:
        cleaned = _NON_ALNUM.sub("", value.upper())
        dashed = value.upper().replace(" ", "")
        candidates = {cleaned, dashed}
        for prefix, trade in LICENSE_TRADE_PREFIXES.items():
            p_clean = _NON_ALNUM.sub("", prefix.upper())
            if any(c.startswith(p_clean) or prefix.upper() in c for c in candidates):
                return trade
    return None


def current_opportunity_services(lead: dict, agent_key: str = "") -> set[str]:
    """Return service keys that should receive this lead now."""
    keys: set[str] = set()

    trade = normalize_trade(lead.get("_trade") or lead.get("trade") or "")
    service = trade_to_service(trade)
    if service:
        keys.add(service)

    primary = (lead.get("primary_service_type") or "").strip().lower()
    if primary:
        keys.add(primary)

    # For self-pulls, the original trade is already taken. Never route to it.
    if lead.get("_is_gc_self_pull"):
        original_trade = normalize_trade(lead.get("_original_trade") or "")
        original_service = trade_to_service(original_trade)
        if original_service:
            keys.discard(original_service)

        for downstream_trade in lead.get("_sub_trades") or []:
            downstream_service = trade_to_service(str(downstream_trade))
            if downstream_service:
                keys.add(downstream_service)

        return keys

    # Non-classified auxiliary leads can still be routed by source agent.
    if agent_key and not keys:
        keys.add(agent_key)
    return keys
