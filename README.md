# 0brix — Lead Generation Platform for Subcontractors

Lead generation platform for contractors: detects building permits, classifies AI-generated leads, and delivers them via a swipe feed + CRM pipeline.

**Stack:** Python Flask + SQLite + Vue/JS frontend | **AI:** DeepSeek-V3.2-NVFP4 (free via Vultr Inference) | **Server:** 45.32.89.38

---

## Quick Start

```bash
ssh root@45.32.89.38
cd /opt/MLeads
git pull origin QWEN
systemctl restart mleads-web
```

**Live URLs:**
- Swipe feed: `http://45.32.89.38:5001/swipe`
- Pipeline CRM: `http://45.32.89.38:5001/pipeline`
- Dashboard: `http://45.32.89.38:5001/`
- Huly CRM: `http://45.32.89.38:8080`

---

## Project Structure

```
MLeads/
├── web/
│   ├── app.py              # Flask app (4540 lines — refactoring in progress)
│   ├── auth.py             # JWT auth helpers
│   ├── routes/             # Blueprints (target structure)
│   │   ├── leads.py        # Leads CRUD + stats + notes
│   │   ├── pipeline.py      # CRM: Kanban board
│   │   ├── swipe.py        # Swipe feed + action
│   │   ├── admin.py        # Admin: users, scheduler
│   │   └── ai_routes.py    # AI: classify, crossdata, disasters
│   └── templates/
│       ├── index.html      # Dashboard
│       ├── swipe.html      # Tinder-style swipe feed
│       └── pipeline.html   # Kanban CRM board
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
- AI-classified leads only (DeepSeek-V3.2-NVFP4 via Vultr Inference, free)
- Filter by city, service category, score, value
- **Keyboard shortcuts:** `→` like | `←` dislike | `Q` qualify | `Esc` close
- Like = auto-add to pipeline

### Pipeline CRM (`/pipeline`)
- 6-column Kanban: Nuevo → Contactado → Propuesta → Negociación → Ganado / Perdido
- Drag & drop between columns
- Notes, follow-ups, estimates, contact log
- Auto-advances status on contact/estimate actions

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
  - `lead_pipeline` — CRM pipeline state
  - `swipe_actions` — swipe history
  - `users` — auth/users
  - `swipe_actions` — swipe history

---

## Deploy

```bash
ssh root@45.32.89.38
cd /opt/MLeads
git pull origin QWEN
systemctl restart mleads-web
```
