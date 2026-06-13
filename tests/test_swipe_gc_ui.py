from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "web" / "templates" / "swipe.html"


def _html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


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
