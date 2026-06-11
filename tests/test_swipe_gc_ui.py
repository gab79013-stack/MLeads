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
