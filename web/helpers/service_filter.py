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

DEFAULT_SERVICE_CATEGORY_ALIASES = {
    "weather": {"weather", "flood", "disaster"},
}


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
    for cat in selected_cats:
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
        return None, []
    return "(" + " OR ".join(parts) + ")", params
