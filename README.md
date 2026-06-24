# 0brix — Premium Lead Platform for General Contractors

Lead generation platform for General Contractors: detects permit, weather, property and homeowner-intent signals, classifies sellable opportunities, and delivers them through a swipe feed, public marketing site, and TwentyHQ CRM handoff.

**Stack:** Python Flask + SQLite + Vue/JS frontend | **AI:** DeepSeek-V3.2-NVFP4 (free via Vultr Inference) | **Server:** 2.25.162.58

---

## Quick Start

```bash
ssh root@2.25.162.58
cd /opt/MLeads
git pull origin fix/opportunity-trade-routing
systemctl restart mleads-web
```

For Docker-based deployment:

```bash
docker build -t mleads-web .
docker run --rm -p 5001:5001 -v "$PWD/data:/app/data" -v "$PWD/contacts:/app/contacts" mleads-web
```

**Live URLs:**
- Public homepage: `http://2.25.162.58/`
- Collaborators / investors: `http://2.25.162.58/colaboradores`
- Swipe feed: `http://2.25.162.58/swipe`
- Homeowner intake: `http://2.25.162.58/homeowner-intake`
- Internal dashboard: `http://2.25.162.58/app`
- User CRM pipeline: `http://2.25.162.58/pipeline`
- Twenty CRM: `TWENTY_URL` or `http://<server-ip>:3000` when Twenty runs on the same server

**Same-server domains:**
- Point every domain's `A` record to `2.25.162.58`.
- Use `scripts/setup_nginx_domain.sh` to route each domain by hostname.
- Example: Yami / Taco Taco static site → `/var/www/taco-taco`.
- Example: Obrits / 0brix app → `http://127.0.0.1:5001`.
- Example: CRM subdomain → `http://127.0.0.1:3000`.

---

## Project Structure

```
MLeads/
├── web/
│   ├── app.py              # Flask app (4540 lines — refactoring in progress)
│   ├── auth.py             # JWT auth helpers
│   ├── routes/             # Blueprints (target structure)
│   │   ├── leads.py        # Leads CRUD + stats + notes
│   │   ├── pipeline.py      # Legacy pipeline API helpers
│   │   ├── swipe.py        # Swipe feed + action
│   │   ├── admin.py        # Admin: users, scheduler
│   │   └── ai_routes.py    # AI: classify, crossdata, disasters
│   └── templates/
│       ├── index.html      # Dashboard
│       ├── swipe.html      # Tinder-style swipe feed
│       ├── homeowner_intake.html # Public homeowner project intake
│       └── pipeline.html   # Legacy Kanban UI retained for reference
├── utils/
│   ├── web_db.py           # Multi-user DB schema (1450 lines)
│   ├── ai_classifier.py    # DeepSeek classification
│   ├── lead_scoring.py     # Lead scoring engine
│   ├── gc_detector.py      # GC self-pull detection
│   └── ...
├── agents/                 # 26 lead detection agents
│   ├── permits_agent.py
│   ├── solar_agent.py
│   ├── plumbing_agent.py
│   ├── hvac_agent.py
│   └── ...
├── workers/
│   ├── inspection_scheduler.py   # Auto-fetch permit calendars
│   └── telegram_bot.py           # Telegram notifications
└── data/leads.db          # SQLite database
```

---

## Key Features

### Swipe Feed (`/swipe`)
- Public Tinder-style swipe UI — no login required
- 10-card anonymous preview with paywall/signup handoff
- AI-classified leads only (DeepSeek-V3.2-NVFP4 via Vultr Inference, free)
- Filter by city, service category, score, value
- **Keyboard shortcuts:** `→` like | `←` dislike | `Q` qualify | `Esc` close
- Like = auto-add to pipeline
- GC-focused routing prioritizes storm/weather, open owner/GC opportunities, and independently verifiable official sources
- Elite-only filter for curated, high-confidence leads
- Public market-readiness API reports which cities are ready for Elite, pilot-only, or need more inventory
- Market readiness includes gap-to-Elite and next-action recommendations so ops knows how to turn a city into a `$500/month` market
- Public Elite sales-proof API returns ROI-style proof points for explaining the $500/month price
- Admin Elite uplift queue ranks near-Elite leads by missing requirements so ops can enrich existing inventory into sellable supply

### Premium Monetization
- Free preview and authenticated free quota
- Public free-leads preview API for top-of-funnel acquisition
- Pro/Quality/Premium/Elite Stripe checkout support
- Admin billing-readiness API/dashboard card shows whether production has Stripe keys, plan price IDs, webhook secret, return URL, and sellable Quality/Elite markets before sales pushes paid checkout
- Quality plan designed for `$199/month` positioning
- Admin Quality readiness API/dashboard identifies markets ready for the `$199/month Quality plan`
- Quality readiness separates inventory readiness from `STRIPE_PRICE_ID_QUALITY` checkout readiness
- Admins can use ready/pilot/needs-inventory status to decide whether to sell now, pilot, or enrich more data
- Elite plan designed for `$500/month` positioning
- Elite checkout is blocked unless the selected market/filter is `ready_for_elite` with a `$500+` recommended price
- Blocked Elite checkout attempts are saved as pilot requests so sales can follow up when the market becomes sellable
- Elite leads can be reserved exclusively per contractor for a configurable claim window
- Admin dashboard audits active, reported and expired Elite reservations by contractor and lead
- Elite qualification requires a verified source, phone contact, high score, fresh signal, and either project value, action window, or direct homeowner intent
- Swipe feed returns an `elite_certificate` for buyer-facing proof of source, contact, freshness, value and exclusivity evidence
- Swipe shows Elite users billable usage, remaining quota and available replacement credits
- Contacted-leads history keeps Elite certificate, source and reservation evidence for post-sale follow-up
- Admin quality report shows sellability, contact coverage, source coverage, project value coverage and market readiness
- Admin dashboard surfaces blocked Elite checkout demand by market/service for follow-up and inventory prioritization
- Public quality-sales-proof and quality-inventory APIs expose the mid-tier plan with quality evidence and recommended pricing
- Admins can mark Elite pilot requests as contacted or closed after sales follow-up
- Contractors can report bad Elite leads from Swipe; eligible reports auto-grant replacement credits that extend the effective Elite quota
- Replacement credits reduce Elite billable swipe counts in both feed access and swipe actions
- Replacement credits are marked redeemed when an Elite user consumes quota beyond the base Elite limit
- Admin quality reports summarize open replacement-credit liability for Elite guarantee operations
- Admins can mark lead-quality reports as reviewing, resolved or dismissed after QA review

### Homeowner Intake (`/homeowner-intake`)
- Public form for homeowners planning an addition, ADU, garage conversion, kitchen remodel, bathroom remodel or whole-home remodel
- Captures owners before they search for a GC or file permits
- Requires phone and project context to keep lead quality high
- Publishes submissions into `consolidated_leads` as `remodel` GC opportunities with `planning` phase, homeowner decision-maker metadata and HOT/WARM scoring
- Keeps raw submissions in `homeowner_project_intakes` for audit, QA and follow-up

### Post-Sale Remodel Radar
- New Swipe channel: `post_sale_remodel` / `Radar post-venta`
- Targets recently sold properties where a GC may win remodel work before permits appear
- Sellability signals: recent deed/transfer date, cash/LLC/investor buyer, older home, as-is/TLC/fixer language, high sale value and verifiable public source
- Leads are published into `consolidated_leads` and become filterable in Swipe with `service_cats=post_sale_remodel`

Publish one enriched post-sale lead:

```bash
curl -X POST http://2.25.162.58/api/post-sale-remodel/leads \
  -H 'Content-Type: application/json' \
  -d '{
    "address": "125 Main St",
    "city": "Austin",
    "state": "TX",
    "zip": "78704",
    "buyer_name": "Example Homes LLC",
    "contact_phone": "5125550199",
    "sale_date": "2026-06-20",
    "sale_price": 735000,
    "year_built": 1974,
    "description": "Recently sold as-is fixer with renovation potential",
    "source_url": "https://public-recorder.example.gov/deed/ABC123"
  }'
```

### CRM
- `/pipeline` serves the per-user 0brix CRM. A new user starts with an empty pipeline, and each swipe-right inserts that lead into their own `lead_pipeline` records.
- `/crm` redirects to TwentyHQ for the external/admin CRM.
- Set `TWENTY_URL` in the environment to point `/crm` to your Twenty deployment, or expose Twenty on port `3000` on the same server.

To install Twenty on the same server:

```bash
sudo SERVER_URL=http://2.25.162.58:3000 ./scripts/setup_twenty_crm.sh
```

Then add the CRM destination to the 0brix runtime environment:

```bash
TWENTY_URL=http://2.25.162.58:3000
```

On the production VM the setup script updates `/etc/mleads/mleads.env`
automatically when that file exists, so restarting `0brix-web` applies the
redirect.

### AI Classification
- DeepSeek-V3.2-NVFP4 (free) via Vultr Inference API
- 17 fields: trade, pain point, upsell, sub-trades, urgency, decision maker, best time
- Self-pull detection: GC pulling own trade → reclassify to downstream trade

---

## Running

### Web server
```bash
systemctl restart mleads-web   # production (systemd)
# or
cd /opt/MLeads && python3 web_server.py   # dev
```

### Lead agents
```bash
cd /opt/MLeads && python3 main.py              # all agents
cd /opt/MLeads && python3 main.py --run permits  # single agent
```

---

## Database

- **Path:** `/opt/MLeads/data/leads.db` (SQLite WAL mode)
- **Key tables:**
  - `consolidated_leads` — main lead storage
  - `homeowner_project_intakes` — raw homeowner project requests
  - `lead_pipeline` — CRM pipeline state
  - `swipe_actions` — swipe history
  - `users` — auth/users
  - `elite_lead_claims` — exclusive Elite lead reservations
  - `lead_quality_reports` — user-reported lead quality issues
  - `elite_replacement_credits` — replacement credits for reported Elite leads
  - `elite_pilot_requests` — captured Elite demand for markets not ready for `$500/month`

## Environment

- `TWENTY_URL`: public URL of the TwentyHQ instance used for `/crm` redirects. On the current server this is `http://2.25.162.58:3000` until `crm.0brix.com` has a public DNS record.

---

## Validation

```bash
python3 -m py_compile web/app.py web/routes/swipe.py web/routes/leads.py utils/web_db.py tests/test_swipe_gc_ui.py scripts/audit_elite_real_data.py
python3 - <<'PY'
import importlib.util
from pathlib import Path
p = Path('tests/test_swipe_gc_ui.py')
spec = importlib.util.spec_from_file_location('test_swipe_gc_ui', p)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for name in dir(mod):
    if name.startswith('test_'):
        getattr(mod, name)()
        print(name, 'ok')
PY
python3 scripts/audit_elite_real_data.py --base-url http://2.25.162.58
```

---

## Deploy

```bash
ssh root@2.25.162.58
cd /opt/MLeads
git pull origin fix/opportunity-trade-routing
systemctl restart mleads-web
```

For Docker-based deployment:

```bash
docker build -t mleads-web .
docker run --rm -p 5001:5001 -v "$PWD/data:/app/data" -v "$PWD/contacts:/app/contacts" mleads-web
```

## Multi-domain hosting

This server can host many domains on the same IP. DNS decides which domains
reach the box; Nginx decides which app or static folder each domain serves.

DNS records:

```text
Type  Host  Value
A     @     2.25.162.58
A     www   2.25.162.58
```

Create a static site domain:

```bash
sudo ./scripts/setup_nginx_domain.sh \
  --domain yami.com \
  --www \
  --static-root /var/www/taco-taco \
  --enable-ssl
```

Create an app domain for 0brix:

```bash
sudo ./scripts/setup_nginx_domain.sh \
  --domain obrits.com \
  --www \
  --proxy http://127.0.0.1:5001 \
  --enable-ssl
```

Create a CRM domain for Twenty:

```bash
sudo ./scripts/setup_nginx_domain.sh \
  --domain crm.0brix.com \
  --proxy http://127.0.0.1:3000 \
  --enable-ssl
```
