from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "web" / "templates" / "home.html"
COLLAB = ROOT / "web" / "templates" / "colaboradores.html"
PIPELINE = ROOT / "web" / "templates" / "pipeline.html"


def _html(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_home_positions_0brix_as_useful_startup():
    html = _html(HOME)
    for text in [
        "infraestructura comercial para contratistas",
        "Explorar leads",
        "CRM por usuario",
        "Homeowner intake",
        "Detecta",
        "Califica",
        "Guarda en CRM",
        "Da seguimiento",
    ]:
        assert text in html


def test_home_uses_internal_pipeline_not_unbranded_external_crm():
    html = _html(HOME)
    assert 'CRM externo' not in html
    assert 'href="/crm"' not in html
    assert '<a class="btn" href="/pipeline">Pipeline 0brix</a>' in html
    assert 'href="/pipeline">Abrir Pipeline 0brix</a>' in html


def test_home_explains_paid_beta_proof_before_charging():
    html = _html(HOME)
    for text in [
        "Lo que compras en Beta Pro",
        "Beta Pro $99/mes",
        "Fuente oficial auditable",
        "Swipe-right crea pipeline",
        "Reemplazo si falla la evidencia",
    ]:
        assert text in html
    assert "Listo para ventas" not in html


def test_pipeline_empty_state_directs_user_to_swipe_without_fake_examples():
    html = _html(PIPELINE)
    for text in [
        "Guarda un lead desde Swipe para verlo aquí",
        "No mostramos ejemplos falsos como oportunidades reales",
        "Explorar leads reales",
        "pipelineEmpty",
    ]:
        assert text in html


def test_public_upgrade_copy_uses_single_beta_pro_offer():
    app_src = (ROOT / "web" / "app.py").read_text(encoding="utf-8")
    swipe_html = (ROOT / "web" / "templates" / "swipe.html").read_text(encoding="utf-8")
    upgrade_start = app_src.index("@app.route('/api/swipe/upgrade-info'")
    upgrade_end = app_src.index("@app.route('/api/swipe/free-leads'", upgrade_start)
    upgrade_src = app_src[upgrade_start:upgrade_end]

    assert '"id": "beta_pro"' in upgrade_src
    assert '"price": 99' in upgrade_src
    assert '"label": "Beta Pro"' in upgrade_src
    assert '"quality_limit"' not in upgrade_src
    assert '"elite_limit"' not in upgrade_src
    assert "handleUpgrade('beta_pro')" in swipe_html
    assert "handleUpgrade('premium')" not in swipe_html


def test_collaborators_page_invites_capital_distribution_and_advocacy():
    html = _html(COLLAB)
    for text in [
        "Capital",
        "Divulgacion",
        "Operadores locales",
        "Data partners",
        "Producto funcional",
        "Invertir o colaborar",
        "hello@0brix.com",
    ]:
        assert text in html
