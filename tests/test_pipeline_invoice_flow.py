from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "web" / "app.py"
WEB_DB = Path(__file__).resolve().parents[1] / "utils" / "web_db.py"
PIPELINE = Path(__file__).resolve().parents[1] / "web" / "templates" / "pipeline.html"


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_swipe_like_creates_pipeline_entry_for_registered_user():
    src = _src(APP)
    assert "INSERT OR IGNORE INTO lead_pipeline" in src
    assert "status, notes" in src
    assert "'Nuevo'" in src


def test_pipeline_and_invoice_api_routes_are_registered_in_live_app():
    src = _src(APP)
    assert "@app.route('/api/pipeline', methods=['GET'])" in src
    assert "@app.route('/api/pipeline/estimate', methods=['POST'])" in src
    assert "@app.route('/api/pipeline/invoice', methods=['POST'])" in src


def test_pipeline_invoice_tables_are_initialized():
    src = _src(WEB_DB)
    assert "CREATE TABLE IF NOT EXISTS lead_pipeline" in src
    assert "CREATE TABLE IF NOT EXISTS lead_estimates" in src
    assert "CREATE TABLE IF NOT EXISTS lead_invoices" in src


def test_pipeline_page_uses_swipe_auth_token_and_has_invoice_button():
    html = _src(PIPELINE)
    assert "localStorage.getItem(\"access_token\")" in html
    assert "Preparar invoice" in html
    assert "/api/pipeline/invoice" in html
