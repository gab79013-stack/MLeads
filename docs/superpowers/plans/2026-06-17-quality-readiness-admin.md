# Quality Readiness Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an admin Quality readiness workflow that shows which markets can support the `$199/month` Quality plan and what must be fixed before sales pitches.

**Architecture:** Reuse `_quality_market_readiness_payload()` as the single source of truth, wrap it in a protected admin endpoint, then render the summary in the existing admin dashboard readiness card grid. Keep the report public-safe by exposing market counts, coverage, score, billing readiness, and action text only.

**Tech Stack:** Flask routes in `web/app.py`, plain HTML/CSS/JavaScript in `web/templates/index.html`, string-based regression tests in `tests/test_swipe_gc_ui.py`, project docs in `README.md`.

---

## File Structure

- Modify `tests/test_swipe_gc_ui.py`: add regression coverage for the admin endpoint, dashboard card, and README documentation.
- Modify `web/app.py`: add `_quality_admin_guidance_payload()` and `GET /api/admin/quality-readiness`.
- Modify `web/templates/index.html`: add a Quality readiness admin card and `loadQualityReadiness()` dashboard loader.
- Modify `README.md`: document the admin Quality readiness workflow.

---

### Task 1: Add Failing Coverage For Quality Admin Readiness

**Files:**
- Modify: `tests/test_swipe_gc_ui.py`

- [ ] **Step 1: Add the failing regression test**

Append this test near `test_swipe_supports_free_leads_preview_and_quality_plan()`:

```python
def test_admin_dashboard_surfaces_quality_readiness_for_sales():
    app_src = (TEMPLATE.parents[1] / "app.py").read_text(encoding="utf-8")
    index_html = _index_html()
    readme = (TEMPLATE.parents[2] / "README.md").read_text(encoding="utf-8")

    assert "@app.route('/api/admin/quality-readiness'" in app_src
    assert "def admin_quality_readiness" in app_src
    assert "@require_admin" in app_src
    assert "def _quality_admin_guidance_payload" in app_src
    assert "_quality_market_readiness_payload(city, service)" in app_src
    assert "STRIPE_PRICE_ID_QUALITY" in app_src

    assert 'id="qualityReadinessCard"' in index_html
    assert 'id="qualityReadyCount"' in index_html
    assert 'id="qualityReadinessList"' in index_html
    assert "/api/admin/quality-readiness" in index_html
    assert "loadQualityReadiness()" in index_html

    assert "Admin Quality readiness" in readme
    assert "$199/month Quality plan" in readme
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
root = Path('.')
html = (root / 'web/templates/index.html').read_text(encoding='utf-8')
app_src = (root / 'web/app.py').read_text(encoding='utf-8')
readme = (root / 'README.md').read_text(encoding='utf-8')
checks = [
    ("admin route", "@app.route('/api/admin/quality-readiness'" in app_src),
    ("admin handler", "def admin_quality_readiness" in app_src),
    ("guidance helper", "def _quality_admin_guidance_payload" in app_src),
    ("dashboard card", 'id="qualityReadinessCard"' in html),
    ("dashboard fetch", "/api/admin/quality-readiness" in html),
    ("readme doc", "Admin Quality readiness" in readme),
]
missing = [name for name, ok in checks if not ok]
if not missing:
    raise SystemExit("Expected missing Quality admin readiness pieces, but all checks passed")
print("Expected failing coverage:", ", ".join(missing))
PY
```

Expected: prints missing pieces such as `admin route`, `dashboard card`, and `readme doc`.

---

### Task 2: Implement The Admin Quality Readiness Endpoint

**Files:**
- Modify: `web/app.py`

- [ ] **Step 1: Add helper below `_billing_readiness_payload()`**

Insert this code after `_billing_readiness_payload()`:

```python
def _quality_admin_guidance_payload(readiness: dict) -> dict:
    """Add admin-only sales guidance to Quality readiness data."""
    summary = readiness.get("summary") or {}
    markets = readiness.get("markets") or []
    top_market = markets[0] if markets else {}
    ready_count = int(summary.get("ready_markets") or 0)
    pilot_count = int(summary.get("pilot_markets") or 0)
    quality_price_configured = bool((os.getenv("STRIPE_PRICE_ID_QUALITY") or os.getenv("STRIPE_PRICE_ID") or "").strip())

    if ready_count and quality_price_configured:
        status = "ready_to_sell"
        headline = "Quality is ready to sell in at least one market."
        primary_action = "Start outreach with the top ready market and use free leads as the entry offer."
    elif ready_count:
        status = "billing_blocked"
        headline = "Quality inventory is ready, but checkout is not fully configured."
        primary_action = "Configure STRIPE_PRICE_ID_QUALITY before sending buyers to checkout."
    elif pilot_count:
        status = "pilot_only"
        headline = "Quality can support pilot pricing, but needs more proof for the $199 pitch."
        primary_action = "Sell a limited pilot and enrich phone/source coverage before full rollout."
    else:
        status = "needs_inventory"
        headline = "Quality needs more sellable inventory before a paid push."
        primary_action = "Prioritize homeowner intake, permits, and contact enrichment in the highest-candidate markets."

    next_actions = list(top_market.get("next_actions") or [])
    if primary_action not in next_actions:
        next_actions.insert(0, primary_action)

    return {
        "status": status,
        "headline": headline,
        "primary_action": primary_action,
        "top_market": top_market,
        "next_actions": next_actions[:4],
        "billing": {
            "quality_price_configured": quality_price_configured,
            "missing": [] if quality_price_configured else ["STRIPE_PRICE_ID_QUALITY"],
        },
    }
```

- [ ] **Step 2: Add admin route below `admin_billing_readiness()`**

Insert this route after `admin_billing_readiness()`:

```python
@app.route('/api/admin/quality-readiness', methods=['GET'])
@require_admin
def admin_quality_readiness():
    """Show admin sales readiness for the $199 Quality plan."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    readiness = _quality_market_readiness_payload(city, service)
    guidance = _quality_admin_guidance_payload(readiness)
    payload = {
        **readiness,
        "sales_guidance": {
            "status": guidance["status"],
            "headline": guidance["headline"],
            "primary_action": guidance["primary_action"],
            "next_actions": guidance["next_actions"],
            "top_market": guidance["top_market"],
        },
        "billing": guidance["billing"],
    }
    return jsonify(payload), 200
```

- [ ] **Step 3: Compile the backend**

Run:

```bash
python3 -m py_compile web/app.py
```

Expected: no output and exit code `0`.

---

### Task 3: Add Quality Readiness To The Admin Dashboard

**Files:**
- Modify: `web/templates/index.html`

- [ ] **Step 1: Add the dashboard card**

Insert this card after `billingReadinessCard` and before `eliteQualityCard`:

```html
<div class="elite-quality-card" id="qualityReadinessCard">
    <div class="elite-quality-kpi">
        <div class="stat-label">Quality readiness</div>
        <div class="stat-value" id="qualityReadyCount">0</div>
        <span class="elite-quality-status warn" id="qualityReadinessStatus">Loading</span>
    </div>
    <div class="elite-quality-detail">
        <div><strong id="qualityPilotCount">0</strong><span>Pilot markets</span></div>
        <div><strong id="qualityNeedsCount">0</strong><span>Needs inventory</span></div>
        <div><strong id="qualityLeadTotal">0</strong><span>Quality leads</span></div>
        <div class="elite-quality-alert" id="qualityReadinessAlerts">Checking Quality sellability...</div>
        <div class="market-readiness-list" id="qualityReadinessList"></div>
    </div>
</div>
```

- [ ] **Step 2: Add the JavaScript loader after `loadBillingReadiness()`**

Insert this function after `loadBillingReadiness()`:

```javascript
async function loadQualityReadiness() {
    const statusEl = document.getElementById('qualityReadinessStatus');
    const listEl = document.getElementById('qualityReadinessList');
    if (!statusEl || !listEl) return;
    try {
        const response = await fetch('/api/admin/quality-readiness', {
            headers: { 'Authorization': `Bearer ${currentAccessToken}` }
        });
        if (!response.ok) throw new Error('quality readiness unavailable');
        const data = await response.json();
        const summary = data.summary || {};
        const markets = data.markets || [];
        const guidance = data.sales_guidance || {};
        const billing = data.billing || {};
        const readyMarkets = Number(summary.ready_markets || 0);
        const pilotMarkets = Number(summary.pilot_markets || 0);
        document.getElementById('qualityReadyCount').textContent = readyMarkets;
        document.getElementById('qualityPilotCount').textContent = pilotMarkets;
        document.getElementById('qualityNeedsCount').textContent = summary.needs_inventory_markets || 0;
        document.getElementById('qualityLeadTotal').textContent = summary.total_quality_leads || 0;

        const labels = {
            ready_to_sell: 'Sell Quality',
            billing_blocked: 'Setup billing',
            pilot_only: 'Pilot only',
            needs_inventory: 'Needs inventory'
        };
        statusEl.textContent = labels[guidance.status] || 'Review';
        statusEl.classList.toggle('danger', guidance.status === 'needs_inventory' || guidance.status === 'billing_blocked');
        statusEl.classList.toggle('warn', guidance.status === 'pilot_only');
        document.getElementById('qualityReadinessAlerts').textContent = billing.quality_price_configured === false
            ? `${guidance.headline || 'Quality needs review'} Missing STRIPE_PRICE_ID_QUALITY.`
            : (guidance.headline || 'Quality readiness loaded.');

        listEl.innerHTML = markets.slice(0, 6).map(m => `
            <div class="market-readiness-row">
                <strong>${escHtml(m.city || 'Unknown')}</strong>
                <span>${escHtml(m.status || '')} · ${Number(m.quality_leads || 0)} Quality · ${Number(m.average_quality_score || 0)}/100</span>
                <span class="market-readiness-price">${m.recommended_price ? '$' + Number(m.recommended_price).toLocaleString() + '/mo' : 'Hold'}</span>
                <div class="market-readiness-action">
                    ${escHtml((m.next_actions || guidance.next_actions || [])[0] || 'Review enrichment gaps before selling Quality.')}
                </div>
            </div>
        `).join('') || '<div class="elite-quality-alert">No Quality readiness data yet.</div>';
    } catch (e) {
        statusEl.textContent = 'Unavailable';
        statusEl.classList.add('danger');
        document.getElementById('qualityReadinessAlerts').textContent = 'Quality readiness could not be loaded.';
        listEl.innerHTML = '';
    }
}
```

- [ ] **Step 3: Call the loader when admin dashboard loads**

Find the dashboard initialization block that calls `loadBillingReadiness()` and add:

```javascript
loadQualityReadiness();
```

- [ ] **Step 4: Verify template strings are present**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
html = Path('web/templates/index.html').read_text(encoding='utf-8')
for needle in [
    'id="qualityReadinessCard"',
    'id="qualityReadyCount"',
    'id="qualityReadinessList"',
    '/api/admin/quality-readiness',
    'loadQualityReadiness()',
]:
    assert needle in html, needle
print('quality dashboard strings present')
PY
```

Expected: `quality dashboard strings present`.

---

### Task 4: Document The Admin Workflow

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add README bullets near the monetization section**

Add these bullets near the existing Quality/Elite monetization bullets:

```markdown
- Admin Quality readiness API/dashboard identifies markets ready for the `$199/month Quality plan`
- Quality readiness separates inventory readiness from `STRIPE_PRICE_ID_QUALITY` checkout readiness
- Admins can use ready/pilot/needs-inventory status to decide whether to sell now, pilot, or enrich more data
```

- [ ] **Step 2: Verify docs strings**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
readme = Path('README.md').read_text(encoding='utf-8')
assert 'Admin Quality readiness' in readme
assert '$199/month Quality plan' in readme
assert 'STRIPE_PRICE_ID_QUALITY' in readme
print('readme quality readiness docs present')
PY
```

Expected: `readme quality readiness docs present`.

---

### Task 5: Run Verification And Commit

**Files:**
- Verify: `web/app.py`
- Verify: `web/templates/index.html`
- Verify: `tests/test_swipe_gc_ui.py`
- Verify: `README.md`

- [ ] **Step 1: Run Python compilation**

Run:

```bash
python3 -m py_compile web/app.py tests/test_swipe_gc_ui.py
```

Expected: no output and exit code `0`.

- [ ] **Step 2: Run focused test if pytest exists**

Run:

```bash
python3 -m pytest -q tests/test_swipe_gc_ui.py
```

Expected if pytest is installed: tests pass. If `No module named pytest`, record that pytest is unavailable and continue with the manual string verification below.

- [ ] **Step 3: Run manual regression check**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
root = Path('.')
html = (root / 'web/templates/index.html').read_text(encoding='utf-8')
app_src = (root / 'web/app.py').read_text(encoding='utf-8')
readme = (root / 'README.md').read_text(encoding='utf-8')
checks = [
    ("admin route", "@app.route('/api/admin/quality-readiness'" in app_src),
    ("admin handler", "def admin_quality_readiness" in app_src),
    ("admin auth", "@require_admin" in app_src),
    ("guidance helper", "def _quality_admin_guidance_payload" in app_src),
    ("quality source", "_quality_market_readiness_payload(city, service)" in app_src),
    ("stripe quality billing", "STRIPE_PRICE_ID_QUALITY" in app_src),
    ("dashboard card", 'id="qualityReadinessCard"' in html),
    ("dashboard count", 'id="qualityReadyCount"' in html),
    ("dashboard list", 'id="qualityReadinessList"' in html),
    ("dashboard fetch", "/api/admin/quality-readiness" in html),
    ("dashboard loader", "loadQualityReadiness()" in html),
    ("readme admin", "Admin Quality readiness" in readme),
    ("readme price", "$199/month Quality plan" in readme),
]
missing = [name for name, ok in checks if not ok]
if missing:
    raise SystemExit("Missing checks: " + ", ".join(missing))
print("manual quality admin readiness checks passed")
PY
```

Expected: `manual quality admin readiness checks passed`.

- [ ] **Step 4: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output and exit code `0`.

- [ ] **Step 5: Commit implementation**

Run:

```bash
git add web/app.py web/templates/index.html tests/test_swipe_gc_ui.py README.md
git commit -m "Add admin quality readiness workflow"
```

Expected: commit succeeds.

---

## Self-Review

- Spec coverage: Task 2 implements the admin endpoint, billing signal, sales guidance, and reuse of `_quality_market_readiness_payload()`. Task 3 implements the dashboard card and graceful empty/error/billing states. Task 4 documents the workflow. Task 5 verifies compilation, tests, manual checks, and whitespace.
- Placeholder scan: no incomplete markers or vague implementation steps remain.
- Type consistency: endpoint returns `summary`, `markets`, `filters`, `thresholds`, `sales_guidance`, and `billing`; dashboard reads those exact names.
