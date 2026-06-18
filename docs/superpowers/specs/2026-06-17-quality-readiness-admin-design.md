# Quality Readiness Admin Design

Date: 2026-06-17

## Purpose

Help the team sell the `Quality` plan at `$199/month` by turning the existing Quality inventory APIs into an admin-facing readiness workflow. The feature should answer three commercial questions:

1. Which cities are ready to sell Quality now?
2. Which cities can support a pilot but need more enrichment before a full pitch?
3. What specific data gaps block a city from being worth selling?

## Scope

This spec covers an admin report and dashboard surface for Quality readiness. It does not change the public swipe experience, Stripe checkout behavior, lead ingestion, or scoring thresholds unless implementation reveals a small bug directly blocking the report.

## Recommended Approach

Add an admin endpoint backed by the existing `_quality_market_readiness_payload()` helper, then surface the same data in the admin dashboard. This reuses the new Quality scoring and avoids creating a second source of truth.

Other approaches considered:

- Build a new scoring model only for admin sales operations. This would be more flexible, but it risks diverging from the public Quality APIs.
- Focus on the free-leads acquisition funnel first. That can help growth, but it does not directly help the team decide where to charge for higher-quality leads.

## Backend Design

Add `GET /api/admin/quality-readiness` in `web/app.py`, protected by `@require_admin`.

The endpoint should accept optional filters:

- `city`
- `service`
- `service_cats`

The response should include:

- `summary`: ready markets, pilot markets, needs-inventory markets, total candidate leads, total quality leads.
- `markets`: top markets sorted by sellability.
- `filters`: normalized filters applied.
- `thresholds`: Quality qualification thresholds.
- `sales_guidance`: concise admin-facing guidance derived from readiness status.
- `billing`: whether `STRIPE_PRICE_ID_QUALITY` appears configured, so sales can distinguish inventory readiness from checkout readiness.

The endpoint should not expose sensitive contact details. It should report counts, coverage percentages, scores, and action text only.

## Dashboard Design

Add a compact admin card in `web/templates/index.html` near existing monetization/readiness cards.

The card should show:

- Ready Quality markets.
- Pilot Quality markets.
- Total Quality leads.
- Top ready market and recommended price.
- A short action list for the top market.
- A warning if Quality inventory is ready but billing is missing.

The UI should degrade gracefully:

- Empty data: show that no markets are ready yet and list enrichment actions.
- API error: show a short unavailable message.
- Missing Stripe price: show checkout is not ready even if inventory is sellable.

## Data Flow

1. Admin opens dashboard.
2. Dashboard JS calls `/api/admin/quality-readiness`.
3. Backend calls `_quality_market_readiness_payload()`.
4. Backend adds sales guidance and billing readiness.
5. UI renders summary, top market, and next actions.

## Testing

Add coverage to `tests/test_swipe_gc_ui.py` that proves:

- `web/app.py` exposes `@app.route('/api/admin/quality-readiness'`.
- The endpoint is protected by `@require_admin`.
- The endpoint reuses `_quality_market_readiness_payload`.
- The admin dashboard calls `/api/admin/quality-readiness`.
- The dashboard contains a Quality readiness card/list.
- README documents the admin Quality readiness workflow.

Run these checks:

- `python3 -m py_compile web/app.py tests/test_swipe_gc_ui.py`
- Existing project test runner if `pytest` is available.
- `git diff --check`

## Self-Review

- Placeholder scan: no incomplete markers or unresolved action items remain.
- Consistency: backend, dashboard, and tests all use the same Quality readiness source.
- Scope: focused on admin sellability visibility, not broad scoring or acquisition changes.
- Ambiguity: readiness and checkout readiness are separate signals in the response and UI.
