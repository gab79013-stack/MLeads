from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "web" / "templates" / "home.html"
COLLAB = ROOT / "web" / "templates" / "colaboradores.html"


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


def test_home_crm_nav_opens_external_crm_entrypoint():
    html = _html(HOME)
    assert '<a class="btn" href="/crm">CRM externo</a>' in html
    assert 'href="/pipeline">CRM</a>' not in html
    assert 'href="/pipeline">Abrir CRM</a>' not in html


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
