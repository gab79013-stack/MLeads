# 0brix Startup Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 0brix homepage and collaborators page feel like a credible, useful construction-tech startup with clear product value and calls to action for customers, partners, investors, and advocates.

**Architecture:** Keep the existing Flask static-template pattern: `/` serves `web/templates/home.html`, and `/colaboradores` plus `/partners` serve `web/templates/colaboradores.html`. Add static content tests that validate the narrative, CTAs, product flow, and investor/collaborator invitations are present.

**Tech Stack:** Flask templates as standalone HTML/CSS, Python static tests, existing Docker deploy on `0brix-web`.

---

### Task 1: Static Page Content Tests

**Files:**
- Create: `tests/test_public_startup_pages.py`
- Read: `web/templates/home.html`
- Read: `web/templates/colaboradores.html`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_public_startup_pages.py -q`

Expected: FAIL because the current pages do not contain the new positioning and page sections.

### Task 2: Redesign Homepage

**Files:**
- Modify: `web/templates/home.html`

- [ ] **Step 1: Replace generic page with startup landing**

Implement:
- Clear startup hero: product category, problem, value, and CTAs.
- Product mockup using HTML/CSS, showing signal -> lead card -> CRM stage.
- Sections for product capabilities, customer value, data flow, and market readiness.
- Footer links to `/swipe`, `/pipeline`, `/homeowner-intake`, and `/colaboradores`.

- [ ] **Step 2: Verify home test passes**

Run: `python3 -m pytest tests/test_public_startup_pages.py::test_home_positions_0brix_as_useful_startup -q`

Expected: PASS.

### Task 3: Redesign Collaborators Page

**Files:**
- Modify: `web/templates/colaboradores.html`

- [ ] **Step 1: Replace generic invitation with investor/collaborator page**

Implement:
- Clear thesis for construction services demand intelligence.
- Separate paths for Capital, Divulgacion, Operadores locales, Data partners, and Sales partners.
- Proof of existing product: Swipe, CRM por usuario, homeowner intake, premium filters.
- CTA to email `hello@0brix.com` and links back to product.

- [ ] **Step 2: Verify collaborators test passes**

Run: `python3 -m pytest tests/test_public_startup_pages.py::test_collaborators_page_invites_capital_distribution_and_advocacy -q`

Expected: PASS.

### Task 4: Full Verification and Deploy

**Files:**
- Test: `tests/test_public_startup_pages.py`
- Runtime: production `https://0brix.com/` and `https://0brix.com/colaboradores`

- [ ] **Step 1: Run local verification**

Run:

```bash
python3 -m py_compile web/app.py
python3 -m pytest tests/test_public_startup_pages.py tests/test_pipeline_invoice_flow.py -q
```

Expected: PASS, except if `pytest` is not installed locally; in that case run equivalent Python assertions and record the limitation.

- [ ] **Step 2: Commit and push**

```bash
git add docs/superpowers/plans/2026-06-23-0brix-startup-pages.md tests/test_public_startup_pages.py web/templates/home.html web/templates/colaboradores.html
git commit -m "feat: refresh 0brix public startup pages"
git push origin fix/opportunity-trade-routing
```

- [ ] **Step 3: Deploy without overwriting unrelated server changes**

If the production repo is dirty/diverged, patch only the changed templates and rebuild `0brix-web`.

- [ ] **Step 4: Verify production pages**

Run:

```bash
curl -sS https://0brix.com/ | grep -E "infraestructura comercial|CRM por usuario|Explorar leads"
curl -sS https://0brix.com/colaboradores | grep -E "Capital|Divulgacion|hello@0brix.com"
```

Expected: both commands find the refreshed content.
