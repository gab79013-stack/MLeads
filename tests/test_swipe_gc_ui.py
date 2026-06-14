from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "swipe.html"
INDEX_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "index.html"
HOMEOWNER_TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "homeowner_intake.html"


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _index_html() -> str:
    return INDEX_TEMPLATE.read_text(encoding="utf-8")


def _homeowner_html() -> str:
    return HOMEOWNER_TEMPLATE.read_text(encoding="utf-8")


def test_swipe_filters_are_gc_focused_not_subcontractor_trade_picker():
    html = _html()

    assert "Subcontratistas" not in html
    assert "Subcontractors" not in html
    assert 'id="subPills"' not in html
    assert 'data-cat="roofing"' not in html
    assert 'data-cat="plumbing"' not in html
    assert "Oportunidad para GC" in html
    assert "GC Opportunity" in html


def test_swipe_gc_filter_options_match_general_contractor_buying_intent():
    html = _html()

    expected = [
        "Daño por tormenta",
        "Remodelación / reparación",
        "Permisos listos",
        "Proyecto sin GC confirmado",
        "Demolición / rebuild",
        "Venta de propiedad",
        "Cross-data verificado",
    ]
    for label in expected:
        assert label in html
    assert "Construcción activa" not in html


def test_swipe_feed_request_only_sends_gc_opportunity_categories():
    html = _html()

    assert "const allCats = [...F.leadTypes];" in html
    assert "[...F.subCats, ...F.leadTypes]" not in html
    assert "F.subCats.add" not in html


def test_swipe_card_surfaces_gc_insight_confidence_source_and_actions():
    html = _html()

    assert "Por qué le sirve a un GC" in html
    assert "Nivel de confianza" in html
    assert "Ver fuente" in html
    assert "Agregar al pipeline" in html
    assert "Verificado" in html
    assert "Candidato" in html


def test_swipe_maps_uses_real_location_parts_not_hardcoded_california():
    html = _html()

    assert "function leadMapsQuery(lead)" in html
    assert "lead.state" in html
    assert "lead.zip" in html
    assert "+ ', CA'" not in html


def test_swipe_has_paid_value_proof_before_paywall():
    html = _html()

    assert 'id="valueStrip"' in html
    assert "Leads verificados para GCs listos para llamar" in html
    assert "Por qué vale pagarlo" in html
    assert "puede pagarse muchas veces" in html


def test_swipe_first_run_onboarding_sets_buyer_filters():
    html = _html()

    assert 'id="onboarding"' in html
    assert "ONBOARDING_DONE_KEY" in html
    assert "shouldShowOnboarding()" in html
    assert "startFromOnboarding()" in html
    assert "F.leadTypes = new Set([selected]);" in html
    assert "F.city = document.getElementById('obCity').value.trim();" in html
    assert "F.eliteOnly = false;" in html
    assert "saveFilters();" in html


def test_swipe_supports_elite_500_plan_and_quality_evidence():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert "Elite" in html
    assert "$500" in html
    assert "handleUpgrade('elite')" in html
    assert "premium_quality_score" in html
    assert "elite_certificate" in html
    assert "Lead certificado Elite" in html
    assert "eliteUsageText" in html
    assert "billableSwipes" in html
    assert "replacementCredits" in html
    assert "elite_certificate" in readme
    assert "elite_only" in html
    assert 'url.searchParams.set("elite_only", "1")' in html or "url.searchParams.set('elite_only', '1')" in html
    assert "STRIPE_PRICE_ID_ELITE" in (TEMPLATE.parents[2] / ".env.example").read_text(encoding="utf-8")
    assert "'elite'" in app_src
    assert "required_tier" in app_src and "elite_only" in app_src
    assert "required_tier" in route_src and "elite_only" in route_src
    assert "subscription_tier" in db_src


def test_elite_quality_gate_requires_phone_source_score_and_action_signal():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    index_html = _index_html()
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    for src in (app_src, route_src):
        assert "def _lead_age_days" in src
        assert "has_source = bool(gc_insight.get(\"source_url\"))" in src
        assert "has_phone = bool((lead_data.get(\"contact_phone\") or \"\").strip())" in src
        assert "fresh_limit_days = 21 if service in {\"weather\", \"flood\", \"disaster\"} else 45" in src
        assert "has_recent_signal" in src
        assert "and has_source" in src
        assert "and has_phone" in src
        assert "and score >= 85" in src
        assert "has_value or has_action_window or has_direct_owner_intent" in src
        assert "and has_recent_signal" in src
        assert "No Elite: falta teléfono" in src
        assert "No Elite: señal vieja o sin fecha" in src
        assert "first_seen" in src

    assert 'id="eliteQaFresh"' in index_html
    assert "fresh_signal" in app_src
    assert "Elite qualification requires a verified source, phone contact, high score, fresh signal" in readme


def test_swipe_exposes_elite_inventory_for_sales_proof():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")

    assert 'id="eliteInventoryText"' in html
    assert "loadEliteInventory()" in html
    assert "/api/swipe/elite-inventory" in html
    assert "total_elite_leads" in html
    assert "average_quality_score" in html
    assert "@app.route('/api/swipe/elite-inventory'" in app_src
    assert "@bp.route('/swipe/elite-inventory'" in route_src
    assert "def _elite_inventory_payload" in app_src
    assert "def _elite_inventory_payload" in route_src
    assert "top_markets" in app_src
    assert "samples" in app_src


def test_swipe_exposes_market_readiness_for_elite_sales():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    script_src = (TEMPLATE.parents[1].parents[0] / "scripts" / "audit_elite_real_data.py").read_text(encoding="utf-8")
    index_html = _index_html()
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert "@app.route('/api/swipe/market-readiness'" in app_src
    assert "@bp.route('/swipe/market-readiness'" in route_src
    assert "def _elite_market_readiness_payload" in app_src
    assert "recommended_price" in app_src
    assert '"ready_for_elite": {"elite_leads": 50' in app_src
    assert '"pilot_market": {"elite_leads": 15' in app_src
    assert "fresh_signal_pct" in app_src
    assert "/api/swipe/market-readiness" in script_src
    assert "source\": \"market-readiness\"" in script_src
    assert 'id="marketReadinessCard"' in index_html
    assert "loadMarketReadiness()" in index_html
    assert "/api/swipe/market-readiness" in index_html
    assert 'id="marketReadinessList"' in index_html
    assert "recommended_price" in index_html
    assert "market-readiness API" in readme


def test_swipe_exposes_elite_sales_proof_for_500_pricing():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    script_src = (TEMPLATE.parents[1].parents[0] / "scripts" / "audit_elite_real_data.py").read_text(encoding="utf-8")
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert 'id="eliteSalesProof"' in html
    assert "function loadEliteSalesProof()" in html
    assert "/api/swipe/elite-sales-proof" in html
    assert "loadEliteSalesProof();" in html
    assert "@app.route('/api/swipe/elite-sales-proof'" in app_src
    assert "@bp.route('/swipe/elite-sales-proof'" in route_src
    assert "def _elite_sales_proof_payload" in app_src
    assert "proof_points" in app_src
    assert "estimated_pipeline_value" in app_src
    assert "break_even_months_per_close" in app_src
    assert "conservative_close_rate" in app_src
    assert "/api/swipe/elite-sales-proof" in script_src
    assert "sales_proof" in script_src
    assert "sales-proof API" in readme


def test_admin_elite_quality_report_supports_sellability_audit():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")

    assert "@app.route('/api/admin/elite-quality-report'" in app_src
    assert "@require_admin" in app_src
    assert "def _elite_quality_report_payload" in app_src
    assert "sellability" in app_src
    assert "ready_for_elite" in app_src
    assert "pilot_market" in app_src
    assert "coverage" in app_src
    assert "official_source" in app_src
    assert "project_value" in app_src
    assert "audit_samples" in app_src
    assert "alerts" in app_src


def test_admin_dashboard_surfaces_elite_quality_report():
    html = _index_html()

    assert 'id="eliteQualityCard"' in html
    assert "loadEliteQualityReport()" in html
    assert "/api/admin/elite-quality-report" in html
    assert "Ready for Elite" in html
    assert "Pilot market" in html
    assert "Needs inventory" in html
    assert 'id="eliteQaPhone"' in html
    assert 'id="eliteQaValue"' in html
    assert 'id="eliteClaimsCard"' in html
    assert 'id="eliteClaimsTotal"' in html
    assert "loadEliteClaims()" in html
    assert "/api/admin/elite-claims?status=active" in html
    assert "Active Elite leads are reserved exclusively" in html
    assert 'id="elitePilotDemandCard"' in html
    assert 'id="elitePilotTotal"' in html
    assert 'id="elitePilotMarkets"' in html
    assert "loadElitePilotDemand()" in html
    assert "updateElitePilotRequest" in html
    assert "Contacted" in html
    assert "Closed" in html
    assert "/api/admin/elite-pilot-requests?status=open" in html
    assert "/api/admin/elite-pilot-requests/${id}" in html
    assert "Contractors tried to buy Elite" in html


def test_elite_leads_are_exclusive_claims_for_500_plan():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS elite_lead_claims" in db_src
    assert "SWIPE_ELITE_CLAIM_DAYS" in app_src
    assert "@app.route('/api/admin/elite-claims'" in app_src
    assert "def admin_elite_claims" in app_src
    assert "active_contractors" in app_src
    assert "def _claim_elite_lead" in app_src
    assert "def _elite_certificate" in app_src
    assert "elite_certificate" in app_src
    assert "def _active_elite_claim" in app_src
    assert "source_url" in app_src
    assert "'elite_certificate': _elite_certificate" in app_src
    assert "exclusive_unavailable" in app_src
    assert "elite_claimed_by_me" in app_src
    assert "elite_claim_expires_at" in app_src
    assert "finally:\n        conn.close()\n    return jsonify({'contacts': contacts}), 200" in app_src
    assert "def _claim_elite_lead" in route_src
    assert "def _elite_certificate" in route_src
    assert "elite_certificate" in route_src
    assert "'elite_certificate': _elite_certificate" in route_src
    assert "exclusive_unavailable" in route_src
    assert "finally:\n        conn.close()\n    return jsonify({'contacts': contacts}), 200" in route_src
    assert "Reservado para tu empresa" in html
    assert "Reserved until" in html
    assert "View source" in html
    assert "Lead Elite reservado para ti" in html


def test_elite_quality_guarantee_has_user_reports_and_admin_queue():
    html = _html()
    index_html = _index_html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS lead_quality_reports" in db_src
    assert "/api/swipe/report-lead" in html
    assert "reportLeadQuality" in html
    assert "Reportar problema" in html
    assert "@app.route('/api/swipe/report-lead'" in app_src
    assert "@bp.route('/swipe/report-lead'" in route_src
    assert "replacement_review" in app_src
    assert "lead_quality_reports" in app_src
    assert "credit_granted = False" in app_src
    assert "replacement_credits = 0" in app_src
    assert "credit_granted = False" in route_src
    assert "replacement_credits = 0" in route_src
    assert "@app.route('/api/admin/lead-quality-reports'" in app_src
    assert "@app.route('/api/admin/lead-quality-reports/<int:report_id>', methods=['PATCH'])" in app_src
    assert "def admin_update_lead_quality_report" in app_src
    assert 'id="qualityReportList"' in index_html
    assert "/api/admin/lead-quality-reports?status=open" in index_html
    assert "/api/admin/lead-quality-reports/${id}" in index_html
    assert "updateQualityReport" in index_html
    assert "Resolved" in index_html
    assert "Dismissed" in index_html
    assert "Reportes de calidad" in index_html


def test_elite_reports_auto_grant_replacement_credits_for_guarantee():
    html = _html()
    index_html = _index_html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS elite_replacement_credits" in db_src
    assert "idx_elite_replacement_credits_user_status" in db_src
    assert "def _elite_replacement_credit_count" in app_src
    assert "def _grant_elite_replacement_credit" in app_src
    assert "def _redeem_elite_replacement_credit" in app_src
    assert "def _redeem_elite_replacement_credit" in route_src
    assert "replacement_credit_redeemed" in app_src
    assert "replacement_credit_redeemed" in route_src
    assert "replacement_credit_granted" in app_src
    assert "replacement_credits" in app_src
    assert "billable_swipes" in app_src
    assert "billable_swipes_count" in app_src
    assert "_billable_current = max(_current - _replacement_credits, 0)" in app_src
    assert "_billable_current = max(_current - _replacement_credits, 0)" in route_src
    assert "billable_swipes_count = max(swipes_count - replacement_credits, 0)" in app_src
    assert "billable_swipes_count = max(swipes_count - replacement_credits, 0)" in route_src
    assert "replacement_credit_granted" in route_src
    assert "billable_swipes" in route_src
    assert "Te agregamos 1 crédito de reemplazo Elite" in html
    assert "replacement_credit_status" in app_src
    assert "open_replacement_credits" in app_src
    assert "users_with_open_replacements" in app_src
    assert "créditos abiertos" in index_html
    assert "crédito " in index_html
    assert "auto-grant replacement credits" in readme


def test_real_data_elite_auditor_script_exists_for_sales_checks():
    script = (TEMPLATE.parents[1].parents[0] / "scripts" / "audit_elite_real_data.py").read_text(encoding="utf-8")

    assert "Audit real Swipe inventory" in script
    assert "/api/health" in script
    assert "/api/swipe/feed" in script
    assert "ready_for_elite" in script
    assert "pilot_market" in script
    assert "needs_inventory" in script
    assert "phone_coverage_pct" in script
    assert "value_coverage_pct" in script


def test_stripe_elite_subscription_state_is_persisted_for_recurring_billing():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "leads.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert "city: F.city || ''" in html
    assert "service: [...F.leadTypes].join(',')" in html
    assert "subscription_data={'metadata': checkout_metadata}" in app_src
    assert "subscription_data={'metadata': checkout_metadata}" in route_src
    assert "stripe_customer_id" in db_src
    assert "stripe_subscription_id" in db_src
    assert "paid_until" in db_src
    assert "def _elite_checkout_guard" in app_src
    assert "def _elite_checkout_guard" in route_src
    assert "def _record_elite_pilot_request" in app_src
    assert "def _record_elite_pilot_request" in route_src
    assert "elite_market_not_ready" in app_src
    assert "elite_market_not_ready" in route_src
    assert "pilot_request_saved" in app_src
    assert "pilot_request_saved" in route_src
    assert "CREATE TABLE IF NOT EXISTS elite_pilot_requests" in db_src
    assert "idx_elite_pilot_requests_status_market" in db_src
    assert "@app.route('/api/admin/elite-pilot-requests'" in app_src
    assert "@app.route('/api/admin/elite-pilot-requests/<int:request_id>', methods=['PATCH'])" in app_src
    assert "def admin_update_elite_pilot_request" in app_src
    assert "function showElitePilotRequest" in html
    assert "Solicitud piloto registrada" in html
    assert 'status != "ready_for_elite" or recommended_price < 500' in app_src
    assert 'status != "ready_for_elite" or recommended_price < 500' in route_src
    assert "'elite_market_status': status" in app_src or '"elite_market_status": status' in app_src
    assert "Elite checkout is blocked" in readme
    assert "elite_pilot_requests" in readme
    assert "def _resolve_web_user_id_from_stripe_object" in app_src
    assert "stripe_subscription_id = COALESCE" in app_src
    assert "stripe_customer_id = COALESCE" in app_src
    assert "SELECT COALESCE(is_paid, 0), COALESCE(subscription_tier, 'free'), paid_until" in app_src


def test_swipe_refreshes_subscription_status_after_stripe_return():
    html = _html()

    assert "function refreshSubscriptionStatus" in html
    assert "/api/swipe/upgrade-info" in html
    assert "refreshSubscriptionStatus(3)" in html
    assert "Activando tu plan" in html


def test_swipe_feed_requires_official_verifiable_source_url():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    helper_src = (TEMPLATE.parents[1] / "helpers" / "gc_interest.py").read_text(encoding="utf-8")

    assert "must be independently verifiable" in app_src
    assert 'if not gc_insight.get("source_url"):' in app_src
    assert "_SOCRATA_PERMIT_SOURCES" in helper_src
    assert "data.honolulu.gov/resource/4vab-c87q.json" in helper_src


def test_swipe_actions_rerender_remaining_queue_after_each_card():
    html = _html()

    assert "S.queue.shift();" in html
    assert "renderDeck();" in html
    assert "cards 6–10 become visible" in html
    assert "quota is exhausted" in html
    assert "misleading \"no more leads\"" in html
    assert "S.queue.shift(); restack();" not in html


def test_swipe_filter_drawer_uses_live_filter_options_api():
    html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")
    helper_src = (TEMPLATE.parents[1] / "helpers" / "service_filter.py").read_text(encoding="utf-8")

    assert "/api/swipe/filter-options" in html
    assert "function loadFilterOptions()" in html
    assert "S.availableServiceCounts = data.available_service_counts || {};" in html
    assert "loadFilterOptions();" in html
    assert "@app.route('/api/swipe/filter-options'" in app_src
    assert "@bp.route('/swipe/filter-options'" in route_src
    assert "def _swipe_filter_options_payload" in app_src
    assert '"weather": {"weather", "flood", "disaster"}' in app_src
    assert "DEFAULT_SERVICE_CATEGORY_ALIASES" in helper_src
    assert '"weather", "flood", "disaster"' in helper_src
    assert "filter_categories" in app_src
    assert "raw_service_counts" in app_src
    assert "top_cities" in app_src


def test_swipe_city_autocomplete_uses_live_inventory_not_only_static_coords():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    route_src = (TEMPLATE.parents[1] / "routes" / "swipe.py").read_text(encoding="utf-8")

    assert "FROM consolidated_leads" in app_src
    assert "FROM consolidated_leads" in route_src
    assert "city_set.update" in app_src
    assert "city_set.update" in route_src
    assert "City autocomplete DB lookup failed" in app_src


def test_homeowner_intake_channel_captures_pre_gc_addition_leads():
    html = _homeowner_html()
    swipe_html = _html()
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    db_src = (TEMPLATE.parents[1].parents[0] / "utils" / "web_db.py").read_text(encoding="utf-8")

    assert "@app.route('/homeowner-intake'" in app_src
    assert "@app.route('/api/homeowner-intake', methods=['POST'])" in app_src
    assert "def homeowner_intake_submit" in app_src
    assert "homeowner_project_intakes" in db_src
    assert "INSERT OR REPLACE INTO consolidated_leads" in app_src
    assert "homeowner_intake" in app_src
    assert '"_project_phase": "planning"' in app_src
    assert '"_decision_maker": "homeowner"' in app_src
    assert "'remodel'" in app_src

    assert "/api/homeowner-intake" in html
    assert "Home addition" in html
    assert "ADU" in html
    assert "Kitchen remodel" in html
    assert 'name="phone"' in html and "required" in html
    assert 'href="/homeowner-intake"' in swipe_html
    assert "¿Homeowner? Solicita GC" in swipe_html
