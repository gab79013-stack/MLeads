# MLeads — Architecture

```
┌─────────────────────────────────────────────────────┐
│                    INTERNET                          │
│                                                      │
│  Contractor (mobile) ──→ /swipe ──→ Swipe Feed      │
│  Contractor (mobile) ──→ /pipeline ──→ Kanban       │
│  Admin ──→ /admin ──→ Dashboard                      │
└──────────────────────┬──────────────────────────────┘
                       │
                  Flask (5001)
                       │
┌──────────────────────┴──────────────────────────────┐
│                   BACKEND                            │
│                                                      │
│  web/app.py ─── create_app()                         │
│  web/routes/                                         │
│    ├── swipe.py      → /api/swipe/*                  │
│    ├── pipeline.py   → /api/pipeline/*               │
│    ├── auth.py       → /api/auth/*                   │
│    ├── leads.py      → /api/leads/*                  │
│    ├── ai.py         → /api/ai/*                     │
│    ├── admin.py      → /api/admin/*                  │
│    └── static_pages  → /swipe, /login, /pipeline     │
│                                                      │
│  web/helpers/                                        │
│    ├── swipe.py      → identity, count, swiped_ids   │
│    └── geocode.py    → coords, haversine             │
│                                                      │
│  agents/                                             │
│    ├── permits_agent  → scrape permits (15 min rot)  │
│    ├── weather_agent  → Open-Meteo storms            │
│    ├── flood_agent    → NOAA floods                  │
│    └── disaster_agent → FEMA/NASA emergencies        │
│                                                      │
│  workers/                                            │
│    ├── inspection_scheduler → scheduled_inspections  │
│    └── telegram_bot         → notifications          │
└──────────────────────┬──────────────────────────────┘
                       │
              SQLite (WAL mode)
              /opt/MLeads/data/leads.db
                       │
┌──────────────────────┴──────────────────────────────┐
│                  DATA LAYER                          │
│                                                      │
│  consolidated_leads (8,407)  → deduplicated leads    │
│  swipe_actions (125)         → user interactions     │
│  lead_pipeline (76)          → tracked leads         │
│  users                       → auth + tier info      │
│  service_types               → categories + emojis   │
│  scheduled_inspections       → GC presence signals   │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│              EXTERNAL SERVICES                      │
│                                                      │
│  Vultr Inference (free) → AI classification          │
│  NOAA / FEMA / NASA FIRMS → disaster data            │
│  Open-Meteo → weather data                           │
│  City permit APIs → construction permits             │
│  Google OAuth / Facebook OAuth → auth                │
└─────────────────────────────────────────────────────┘
```

## Key files
| Archivo | Rol |
|---------|-----|
| `web_server.py` | Entry point, runs Flask on 5001 |
| `web/app.py` | App factory, config, shared constants |
| `web/templates/swipe.html` | Single-page swipe UI (~2200 lines) |
| `web/routes/swipe.py` | Swipe feed + action endpoints |
| `web/routes/pipeline.py` | Kanban endpoints |
| `web/helpers/swipe.py` | Identity, quota, swiped IDs |
| `web/helpers/geocode.py` | City coords, haversine distance |
| `agents/permits_agent.py` | Main permit scraper |
| `utils/permits_importer.py` | Import + dedup permits to DB |
| `reclassify2.py` | AI classification batch job |

## Cron jobs (OpenClaw)
| Job ID | Schedule | Qué hace |
|--------|----------|----------|
| bfcf7665 | 3am UTC daily | Backup DB (rotación 7 días) |
| ed612677 | Lunes 9am UTC | Pulso semanal |
| 01fe8a72 | Cada 6h | Health check + alertas |
