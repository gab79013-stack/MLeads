"""
Helpers for swipe-feed service category SQL filters.

Subcontractor categories must be matched against the current sellable
opportunity, not raw permit text. A stale row may still have
primary_service_type='roofing' even after lead_data says a CCC roofer already
pulled a reroof permit; the self-pull guard below prevents those rows from
leaking back into roofing searches.
"""

from __future__ import annotations

DEFAULT_TRADE_SERVICE_TO_AI = {
    "roofing": "ROOFING",
    "drywall": "DRYWALL",
    "paint": "PAINTING",
    "electrical": "ELECTRICAL",
    "plumbing": "PLUMBING",
    "hvac": "HVAC",
    "flooring": "FLOORING",
    "concrete": "CONCRETE",
    "framing": "FRAMING",
    "windows": "WINDOWS",
    "landscaping": "LANDSCAPING",
    "deconstruction": "DEMOLITION",
    "insulation": "INSULATION",
}

# Canonicalize legacy/shared-link category names before building SQL. The
# public UI has used a few labels over time while the database stores canonical
# primary_service_type values. Unknown non-empty categories must stay
# restrictive instead of silently disabling the service filter and leaking
# unrelated fallback leads.
GC_CATEGORY_ALIASES = {
    "demolition": "deconstruction",
    "demo": "deconstruction",
    "rebuild": "deconstruction",
    "property_sale": "realestate",
    "property-sale": "realestate",
    "real_estate": "realestate",
}

# Categories that intentionally expand to multiple stored primary_service_type
# values. Keep these separate from one-to-one aliases so new inventory channels
# such as post_sale_remodel remain independently countable/filterable.
DEFAULT_SERVICE_CATEGORY_ALIASES = {
    "weather": {"weather", "flood", "disaster"},
    "post_sale": {"post_sale_remodel"},
    "post_sale_remodel": {"post_sale_remodel"},
}


def normalize_service_category(category: str) -> str:
    cat = (category or "").strip().lower()
    return GC_CATEGORY_ALIASES.get(cat, cat)


def build_service_category_filter(
    selected_cats: list[str],
    trade_map: dict[str, str] | None = None,
    service_type_cats: set[str] | None = None,
    service_aliases: dict[str, set[str]] | None = None,
) -> tuple[str | None, list]:
    """Return SQLite WHERE fragment + params for selected swipe categories."""
    if not selected_cats:
        return None, []

    trade_map = trade_map or DEFAULT_TRADE_SERVICE_TO_AI
    service_type_cats = service_type_cats or set()
    service_aliases = service_aliases or DEFAULT_SERVICE_CATEGORY_ALIASES

    parts: list[str] = []
    params: list = []
    for raw_cat in selected_cats:
        cat = normalize_service_category(raw_cat)
        if cat in trade_map:
            ai_trade = trade_map[cat]
            parts.append(
                "("
                "(primary_service_type = ? "
                "OR UPPER(COALESCE(json_extract(lead_data, '$._trade'), '')) = ? "
                "OR UPPER(COALESCE(json_extract(lead_data, '$._sub_trades'), '')) LIKE ?) "
                "AND NOT ("
                "COALESCE(json_extract(lead_data, '$._is_gc_self_pull'), 0) IN (1, 'true', 'True') "
                "AND UPPER(COALESCE(json_extract(lead_data, '$._original_trade'), '')) = ?"
                ")"
                ")"
            )
            params.extend([cat, ai_trade, f'%"{ai_trade}"%', ai_trade])
        elif cat in service_aliases:
            aliases = sorted(service_aliases.get(cat) or {cat})
            placeholders = ",".join("?" * len(aliases))
            parts.append(f"primary_service_type IN ({placeholders})")
            params.extend(aliases)
        elif cat in service_type_cats:
            parts.append("primary_service_type = ?")
            params.append(cat)

    if not parts:
        return "0 = 1", []
    return "(" + " OR ".join(parts) + ")", params
