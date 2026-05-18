# MLeads — Plataforma de Generación de Leads + Disaster Intelligence

Sistema automatizado de generación de leads con IA para contratistas de Roofing, Drywall, Paint, Landscaping y Electrical en el Bay Area y principales ciudades de EE.UU. Evoluciona hacia **Sistema Operativo de Respuesta a Desastres** para la industria de construcción y seguros.

Monitorea **54+ ciudades** en **9 condados** del Bay Area usando APIs públicas y de pago, detecta oportunidades de negocio por tipo de subcontratista, y entrega alertas en tiempo real con datos de contacto del GC directamente en Telegram.

Incluye **dashboard multi-tenant**, **swipe feed público tipo Tinder**, **bot de Telegram con monetización** integrada, **14 agentes de detección** con Disaster Intelligence, **Property DNA**, **scoring tripartito**, y **CSLB license verification**.

---

## ⚡ Quick Start (Instalación en 30 segundos)

### En Ubuntu/Debian (recomendado para Azure VM):
```bash
curl -s https://raw.githubusercontent.com/gab79013-stack/MLeads/main/quick-install.sh | sudo bash
```

**Eso es todo.** El script instala dependencias, configura la base de datos, inicia ambos servicios y queda listo en `http://localhost`.

[Ver documentación de instalación completa →](INSTALL_UBUNTU.md)

---

## Arquitectura General

```
┌──────────────────────────────────────────────────────────────────────┐
│                       MLeads Platform v4                             │
│                                                                      │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────────┐  │
│  │   Agentes    │  │  Lead Engine   │  │   Notificaciones        │  │
│  │  (14 tipos)  │─▶│  Tripartite    │─▶│  Telegram / WA /        │  │
│  │  54 ciudades │  │  Scoring AI    │  │  Email / Slack          │  │
│  │  Disaster IQ │  │  Property DNA  │  │                         │  │
│  └──────────────┘  │  Dedup/Merge   │  └─────────────────────────┘  │
│                     └───────┬────────┘                               │
│                             │                                        │
│              ┌──────────────┼──────────────┐                         │
│              ▼              ▼              ▼                         │
│     ┌─────────────┐ ┌────────────┐ ┌─────────────────┐              │
│     │  PostgreSQL │ │  Lead      │ │  CSLB License   │              │
│     │  (prod)     │ │  Router    │ │  Verifier       │              │
│     │  SQLite     │ │  (GC/Sub   │ │  (CA contractors│              │
│     │  (dev)      │ │  assign)   │ │  verification)  │              │
│     └──────┬──────┘ └─────┬──────┘ └─────────────────┘              │
│            │              │                                          │
│    ┌───────┼──────────────┼──────────────────┐                      │
│    ▼       ▼              ▼                  ▼                      │
│ ┌────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐               │
│ │Dash-   │ │Swipe Feed│ │Telegram  │ │Disaster      │               │
│ │board   │ │(público) │ │Bot       │ │Intelligence  │               │
│ │Multi-  │ │Tinder UX │ │Trials/   │ │Dashboard     │               │
│ │tenant  │ │Freemium  │ │Stripe    │ │NOAA/FEMA/    │               │
│ │RBAC    │ │          │ │Payments  │ │NASA FIRMS    │               │
│ └────────┘ └──────────┘ └──────────┘ └──────────────┘               │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Agentes de Detección (14)

| Agente | Fuentes | Ciudades | Intervalo | Tipo de oportunidad |
|--------|---------|----------|-----------|---------------------|
| Permisos de Construcción | Socrata, CKAN (26 fuentes) | 54 | 60 min | ADU/remodel/roofing/electrical |
| Instalaciones Solares | Socrata, Google Solar, Aurora, EnergySage | 54 | 60 min | Solar → roofing/electrical/paint |
| Reportes 311 Plagas | SeeClickFix, Socrata, Thumbtack + Nominatim/OSM | 54 | 2 hrs | Plagas → drywall/paint reparación |
| Alertas NOAA Inundación | NOAA Weather API + FEMA NFHL | 13 zonas | 30 min | Agua → drywall/paint/roofing |
| Construcciones Activas | Socrata, BuildZoom | 54 | 60 min | Framing → roofing/electrical |
| Calendario Inspecciones | PDF (CC/Berkeley) + CKAN (SJ) + Predicción | 54 | Daily 9 AM | GC en sitio — timing perfecto |
| Deconstrucción | Socrata, ATTOM + EPA ECHO | 54 | 2 hrs | Demolición → roofing/drywall |
| Propiedades Vendidas | Socrata (assessor data) | 10 condados | 2 hrs | Nuevo dueño → renovación |
| Eficiencia Energética | Socrata (benchmarking) | SF/Oak/SJ | 6 hrs | Baja eficiencia → panel upgrade |
| Google Places | Google Places API + Nominatim/OSM | Bay Area | 24 hrs | Constructores activos |
| Yelp Contractors | Yelp Fusion API | Bay Area | 24 hrs | Contratistas activos |
| Pronóstico de Lluvia | Open-Meteo (gratuito, sin key) | 9 ciudades | 1 hr | Tormenta → roofing/gutters/drywall |
| Contratos Federales | USASpending.gov (gratuito, sin key) | 9 condados | 6 hrs | Contratos federales → subcontratistas |
| **🚨 Disaster Intelligence** | **NOAA + FEMA + NASA FIRMS** | **Bay Area** | **30 min** | **Desastres → roofing/drywall/paint/electrical** |

---

## 🚨 Disaster Intelligence (NUEVO)

Detección de eventos de desastre en tiempo real con 3 fuentes gratuitas:

| Fuente | Datos | Requiere key |
|--------|-------|-------------|
| NOAA Weather Alerts | Flood warnings, severe storms, red flag, tornado | No |
| FEMA Disaster Declarations | Declaraciones federales de desastre para CA | No |
| NASA FIRMS | Incendios activos vía satélite (VIIRS) | MAP_KEY gratuita |

**Clasificación automática:** flood, wildfire, wind, tornado, earthquake, severe_storm, winter_storm

**Mapeo de trades:** cada tipo de desastre se mapea al trade más relevante:
- Flood → DRYWALL (agua = drywall/paint)
- Wildfire → ROOFING (fuego = reconstrucción)
- Hail → ROOFING (granizo = reemplazo techo garantizado)
- Earthquake → CONCRETE (inspección foundation)

**API Endpoints:**
- `GET /api/disasters/active` — Eventos activos
- `POST /api/disasters/run` — Scan manual

---

## 🏠 Property DNA (NUEVO)

Enriquecimiento de propiedades con datos de assessor públicos:

| Dato | Fuente |
|------|--------|
| Año de construcción | County Assessor API (4 condados) |
| Material del techo | Inferido de año + código de uso |
| Valor de la propiedad | County Assessor |
| Sqft | County Assessor |
| Zona de flood | FEMA NFHL API |
| Tipo de estructura | County Assessor use code |

**Condado con datos:** Alameda, Santa Clara, San Francisco, Los Angeles (vía Socrata)

**Inferencia de techo** basada en patrones del Bay Area:
- Pre-1960: wood shake o tar & gravel
- 1960-1985: composition shingle
- 1985-2010: composition shingle o concrete tile
- 2010+: concrete tile, composition shingle, solar-ready
- Comercial: TPO, modified bitumen, tar & gravel

**Fallback:** cuando no hay datos de assessor, estima por demografía de la ciudad.

**Cache:** 90 días para evitar re-fetches.

**API Endpoints:**
- `GET /api/property-dna/<lead_id>` — Datos de una propiedad
- `POST /api/property-dna/enrich` — Enriquecer batch

---

## 📊 Tripartite Scoring (NUEVO)

Un lead, tres perspectivas. Cada lead recibe 3 scores independientes:

| Score | Rango | Para quién | Factores principales |
|-------|-------|------------|---------------------|
| 👷 Subcontractor | 0–100 | Subcontratistas | Trade match, proximidad, licencia, timing, disaster alignment |
| 🏗️ GC | 0–100 | General Contractors | Valor del proyecto, contact quality, disaster zone, Property DNA |
| 🏢 Insurance | 0–100 | Aseguradoras | Flood zone, edad edificio, valor, disaster activo, damage severity |

### Factores detallados por score:

**Subcontractor Score (0-100):**
- 30% — Especialidad del sub coincide con tipo de trabajo
- 20% — Proximidad geográfica
- 15% — Licencia activa para ese trabajo
- 15% — Timing (lead timing vs schedule)
- 10% — Valor del trabajo
- 10% — Disaster alignment (hail → roofing = high affinity)

**GC Score (0-100):**
- 25% — Valor del proyecto
- 20% — Probabilidad de cierre (contact quality)
- 15% — Propiedad en zona de desastre
- 15% — Property DNA (año + techo)
- 15% — Cross-source signals (múltiples agentes)
- 10% — Timing (inspección próxima)

**Insurance Score (0-100):**
- 25% — Zona de flood / FEMA declarada
- 20% — Property DNA (edad + material)
- 15% — Valor de la propiedad
- 15% — Evento de desastre activo
- 15% — Historial de claims proxy
- 10% — Tipo de daño (agua > fuego > viento)

**API Endpoints:**
- `GET /api/scoring/tripartite/<lead_id>` — Scores para un lead
- `POST /api/scoring/tripartite-batch` — Calcular batch

---

## 🔀 Lead Router (NUEVO)

Asignación automática de leads al GC o Sub correcto:

1. Lead entra con tripartite scores
2. Si `gc_score ≥ 60` → busca GCs en el área
3. Si `sub_score ≥ 60` → busca Subs con la especialidad
4. Si `insurance_score ≥ 50` → marca para follow-up aseguradora
5. Asigna al mejor match (score + disponibilidad)

**Criterios de matching:**
- Especialidad del sub coincide con trade del lead
- Zona de servicio incluye la ciudad del lead
- No ha superado límite mensual
- Prioridad: Premium > Pro > Free

**API Endpoints:**
- `POST /api/leads/<id>/assign` — Asignar manualmente
- `GET /api/leads/assigned` — Leads del usuario actual

---

## 🔍 CSLB License Verifier (NUEVO)

Verificación de licencias de contratistas de California vía scraping del portal CSLB:

| Función | Descripción |
|---------|-------------|
| `verify_license("123456")` | Verifica una licencia por número |
| `search_by_name("Bay Area Roofing")` | Busca contratistas por nombre |
| `batch_verify([...])` | Verifica múltiples licencias |
| `verify_subcontractor_profile(lic, trades)` | Verifica especialidades declaradas vs licencia real |

**Datos extraídos:**
- Status (Active, Expired, Suspended, Revoked)
- Classification codes (C-39, B, A, etc.)
- Issue & expiration dates
- Bond amount & effective date
- Workers' compensation insurance
- Disciplinary actions
- Business address, phone, entity type

**Trade → CSLB Classification mapping:**

| Trade | Classificaciones válidas |
|-------|-------------------------|
| Roofing | C-39, B |
| Drywall | C-9, B |
| Painting | C-33, B |
| Electrical | C-10, C-46 |
| Landscaping | C-27 |
| HVAC | C-20, B |
| Demolition | C-21, A, B |
| Concrete | C-8, A, B |
| Plumbing | C-36, B |

**Risk Levels:**

| Level | Criterio |
|-------|----------|
| CRITICAL | Licencia inactiva/expirada |
| HIGH | Trade declarado no coincide con clasificación CSLB |
| MEDIUM | Acciones disciplinarias registradas |
| LOW | Todo verificado ✅ |

**API Endpoints:**
- `GET /api/cslb/verify/<license_number>` — Verificar licencia
- `GET /api/cslb/search?name=...&city=...` — Buscar por nombre
- `POST /api/cslb/verify-sub` — Verificar perfil de sub (licencia + especialidades)
- `POST /api/cslb/batch` — Batch verify (hasta 20 licencias)

---

## Multi-Tenant Architecture (NUEVO)

### Roles de Usuario

| Rol | Permisos | Scoring |
|-----|----------|---------|
| 🔧 Subcontractor | Recibe trabajos asignados por GCs | `subcontractor_score` |
| 🏗️ GC | Recibe leads de property owners, asigna a subs | `gc_score` |
| 🏢 Insurance | Accede a reportes y contractor network (Fase 2) | `insurance_score` |

### Flujo de Asignación

```
1. Evento climático detectado (NOAA / FEMA / NASA FIRMS)
          │
2. MLeads Engine cruza datos:
   • Propiedades en radio de impacto
   • Año construcción + material techo (Property DNA)
   • Historial de permisos
   • Contractors verificados cercanos (CSLB)
          │
3. Calcula 3 scores por lead:
   • subcontractor_score: fit técnico
   • gc_score: valor comercial
   • insurance_score: prob. de claim
          │
4. Router asigna según disponibilidad:
   • GCs reciben alerta de leads HOT
   • Subs reciben alerta de trabajo
   • Si daño confirmado → reporte aseguradora
```

### Profile Data (JSONB por rol)

**Subcontractor:**
```json
{
  "license_number": "123456",
  "license_state": "CA",
  "specialties": ["roofing", "drywall"],
  "service_areas": ["94596", "94597"],
  "disaster_certified": true,
  "avg_response_time_hours": 2.5
}
```

**GC:**
```json
{
  "company_name": "Bay Area Roofing",
  "cslb_license": "987654",
  "service_radius_miles": 25,
  "preferred_trades": ["roofing", "gutter"],
  "min_lead_score": 75
}
```

---

## PostgreSQL Migration (NUEVO)

MLeads soporta PostgreSQL para producción con migración automática desde SQLite:

```bash
# 1. Instalar PostgreSQL
createdb mleads

# 2. Migrar datos existentes
pip install psycopg2-binary
python migrate_to_postgres.py

# 3. Activar en .env
USE_POSTGRES=true
DATABASE_URL=postgresql://mleads:mleads@localhost:5432/mleads
```

**Ventajas de PostgreSQL:**
- Connection pooling (2-20 conexiones)
- JSONB con GIN index para búsqueda en lead_data
- Enums nativos (user_role, subscription_tier)
- Tripartite scoring columns indexadas
- Geo queries (lat/lon con index condicional)
- Disaster events + lead-disaster links
- Concurrent writes sin WAL corruption

**Backward compatible:** SQLite sigue funcionando si no activas `USE_POSTGRES`.

---

## Motor de IA

### Lead Scoring (0–100) + Tripartite
Score automático por lead con scoring tripartito (sub/gc/insurance) basado en múltiples señales:

| Score | Grado | Acción |
|-------|-------|--------|
| 90–100 | 🔥 HOT | Contactar de inmediato |
| 70–89 | 🟠 WARM | Alta prioridad |
| 50–69 | 🟡 MEDIUM | Seguimiento estándar |
| 25–49 | 🔵 COOL | Baja prioridad |
| 0–24 | ⚪ COLD | Archivo |

Factores de scoring: valor del proyecto · tipo de proyecto · calidad de contacto · recencia · geografía · fuente · señales de servicio · proximidad de inspección · Property DNA · disaster signals · tripartite scores.

### Clasificación AI por Descripción
El módulo `ai_classifier.py` usa Claude (claude-haiku) para enriquecer leads ambiguos con la categoría de subcontratista más probable cuando los keywords no son suficientes.

### Outreach AI
`ai_outreach.py` genera mensajes de presentación personalizados para cada lead usando el contexto del proyecto (tipo, valor, GC, inspección próxima).

---

## Swipe Feed Público (UX tipo Tinder)

Feed público sin login para descubrir leads. Disponible en `/swipe`.

### Características
- **Solo leads con contacto** — teléfono o email visibles; el resto se archiva
- **Termómetro inteligente** — refleja afinidad con el tipo de subcontratista seleccionado
- **Progresión automática de score:**
  - Primeros 10 likes: leads HOT (≥90)
  - Siguientes 10: WARM (71–89)
  - Siguientes 10: MEDIUM (51–70)
  - Resto: todos los leads
- **Solo los swipes a la derecha (likes) consumen cuota** — los rechazos son gratuitos
- **Oportunidad de visita en persona** — cuando hay inspección programada
- **Link a Google Maps** en cada lead

### Cuotas
| Perfil | Leads disponibles |
|--------|-------------------|
| Anónimo | 10 likes |
| Usuario gratuito | 40 likes |
| Pro ($29/mes) | 200 leads |
| Premium ($99/mes) | Ilimitado |

---

## Dashboard Multi-Usuario

Panel web con autenticación JWT y RBAC para gestionar leads con múltiples usuarios.

### Roles
| Rol | Permisos |
|-----|----------|
| admin | Acceso total + gestión de usuarios |
| manager | Ver todos los leads, gestionar equipo |
| user | Ver leads asignados, registrar contactos |
| viewer | Solo lectura |

### Características
- Control por ciudad — cada usuario ve solo las ciudades asignadas
- Control por agente — cada usuario ve solo los tipos de lead asignados
- Acceso temporal — expiración automática por horas o fecha exacta
- Audit logging — registro de toda la actividad
- Gestor de Bot Users con badges de trial y extensión desde el panel

---

## API Endpoints (Completo)

```
# Auth
POST /api/auth/login
POST /api/auth/register
POST /api/auth/refresh
POST /api/auth/logout

# Leads
GET  /api/leads
GET  /api/leads/{id}
POST /api/leads/{id}/contact
GET  /api/leads/{id}/contact-history
POST /api/leads/{id}/notes
POST /api/leads/{id}/assign
GET  /api/leads/assigned

# Stats
GET  /api/stats
GET  /api/audit-log

# Disaster Intelligence (NUEVO)
GET  /api/disasters/active
POST /api/disasters/run

# Property DNA (NUEVO)
GET  /api/property-dna/{lead_id}
POST /api/property-dna/enrich

# Tripartite Scoring (NUEVO)
GET  /api/scoring/tripartite/{lead_id}
POST /api/scoring/tripartite-batch

# CSLB Verification (NUEVO)
GET  /api/cslb/verify/{license_number}
GET  /api/cslb/search?name=...&city=...
POST /api/cslb/verify-sub
POST /api/cslb/batch

# Cross-Data
POST /api/crossdata/run
GET  /api/crossdata/stats

# Admin
GET  /api/admin/users
POST /api/admin/users
PUT  /api/admin/users/{id}
PUT  /api/admin/users/{id}/expiration
GET  /api/admin/scheduler/status
POST /api/admin/scheduler/fetch-now

# Inspections
GET  /api/scheduled_inspections?jurisdiction=berkeley
GET  /api/leads/{id}/scheduled_inspections
POST /api/scheduled_inspections

# Swipe Feed
GET  /api/swipe/feed
POST /api/swipe/action
GET  /api/swipe/my-contacts
POST /api/swipe/feedback
```

---

## Cobertura Geográfica (54+ Ciudades)

**Contra Costa County (19):** Pleasant Hill, Walnut Creek, Martinez, Clayton, Pittsburg, Lafayette, Orinda, Antioch, Moraga, Alamo, Danville, Hercules, Pinole, Oakley, San Ramon, Richmond, Brentwood, El Cerrito, Concord

**Alameda County (15):** Oakland, Berkeley, Fremont, Hayward, Dublin, Alameda, San Leandro, Pleasanton, Livermore, Newark, Castro Valley, San Lorenzo, Emeryville, Albany, Union City

**San Mateo County (7):** Daly City, South San Francisco, San Bruno, Millbrae, Burlingame, San Mateo, Redwood City

**Solano County (6):** Benicia, Fairfield, Vallejo, Suisun City, Rio Vista, Vacaville

**Santa Clara County (5):** San Jose, Sunnyvale, Santa Clara, Palo Alto, Mountain View

**Marin County (2):** Novato, San Rafael
**Sonoma County (2):** Sonoma, Petaluma
**Napa County (1):** Napa
**San Joaquin County (2):** Tracy, Stockton
**San Francisco County (1):** San Francisco

**US Expansion:** Los Angeles, New York, Chicago, Houston, Austin, Dallas, Seattle, Atlanta, Phoenix, Miami, Denver, Boston, San Diego, Philadelphia, Charlotte, Raleigh, Portland, Sacramento, Tampa, Las Vegas, San Antonio, Tucson, Pasadena, Long Beach

---

## Seguridad

- JWT con `JWT_SECRET_KEY` validado en startup
- Rate limiting: login (10/min), admin (20/min), CSLB batch (5/min)
- Validación de whitelist en parámetros
- Mensajes de error genéricos al cliente
- Advertencia si `ALLOWED_ORIGINS=*`
- CSLB scraping con rate limiting (2s entre requests) y User-Agent identificable
- Connection pooling thread-safe (PostgreSQL)

---

## Variables de Entorno

```env
# Base de datos
DB_PATH=data/leads.db

# PostgreSQL (producción)
USE_POSTGRES=false
DATABASE_URL=postgresql://mleads:mleads@localhost:5432/mleads

# Servidor
FLASK_ENV=production
PORT=5001
HOST=0.0.0.0
ALLOWED_ORIGINS=https://tu-dominio.com

# JWT
JWT_SECRET_KEY=<random-256-bit-string>

# Telegram
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>

# IA (Claude)
ANTHROPIC_API_KEY=<key>

# Disaster Intelligence
AGENT_DISASTER=true
INTERVAL_DISASTER=30
NASA_FIRMS_MAP_KEY=<free-key>
DISASTER_RADIUS_MILES=25

# Property DNA
PROPERTY_DNA_CACHE_DAYS=90
SOCRATA_APP_TOKEN=

# Lead Router
ROUTER_MIN_GC_SCORE=60
ROUTER_MIN_SUB_SCORE=60
ROUTER_MIN_INS_SCORE=50

# CSLB
CSLB_MIN_REQUEST_INTERVAL=2

# APIs externas (opcionales)
NREL_API_KEY=
GOOGLE_PLACES_KEY=
GOOGLE_SOLAR_KEY=
YELP_API_KEY=
STRIPE_WEBHOOK_SECRET=
SENDGRID_API_KEY=
```

---

## Estructura del Proyecto

```
MLeads/
├── main.py                         # Entry point — orquesta todos los agentes
├── web_server.py                   # Servidor web Flask
├── db_postgres.py                  # PostgreSQL schema + migración (NUEVO)
├── migrate_to_postgres.py          # SQLite → PostgreSQL migration script (NUEVO)
├── requirements.txt
│
├── agents/                         # Agentes de detección (14)
│   ├── base.py                     # Pipeline: classify → Property DNA → tripartite → route
│   ├── permits_agent.py
│   ├── solar_agent.py
│   ├── rodents_agent.py
│   ├── flood_agent.py
│   ├── construction_agent.py
│   ├── deconstruction_agent.py
│   ├── realestate_agent.py
│   ├── energy_agent.py
│   ├── places_agent.py
│   ├── yelp_agent.py
│   ├── weather_agent.py
│   ├── federal_contracts_agent.py
│   ├── disaster_agent.py           # 🚨 NOAA + FEMA + NASA FIRMS (NUEVO)
│   └── marketing/                  # 7 agentes de marketing (opt-in)
│
├── web/                            # Dashboard y API
│   ├── app.py                      # Flask API (80+ endpoints)
│   ├── auth.py                     # JWT auth, RBAC
│   └── templates/
│       ├── index.html
│       ├── login.html
│       └── swipe.html
│
├── workers/
│   ├── inspection_scheduler.py
│   └── telegram_bot.py
│
├── utils/
│   ├── web_db.py                   # Schema SQLite + auto-switch PostgreSQL
│   ├── lead_scoring.py             # Scoring genérico 0-100
│   ├── tripartite_scoring.py       # 📊 Scoring sub/gc/insurance (NUEVO)
│   ├── property_dna.py             # 🏠 Assessor + FEMA data (NUEVO)
│   ├── lead_router.py              # 🔀 GC/Sub assignment (NUEVO)
│   ├── cslb_verifier.py            # 🔍 License verification (NUEVO)
│   ├── fraud_detector.py           # Risk scoring + CSLB integration
│   ├── dedup.py                    # Deduplicación cross-agent
│   ├── hot_zones.py                # Clustering geográfico
│   ├── contact_enrichment.py
│   ├── ai_classifier.py            # Clasificación AI (Claude)
│   ├── ai_outreach.py              # Mensajes AI personalizados
│   ├── ai_bot.py                   # Bot AI conversacional
│   ├── telegram.py
│   ├── notifications.py            # Multi-canal
│   ├── bot_users.py
│   └── billing.py                  # Stripe
│
├── contacts/                       # CSVs para fuzzy matching
├── data/                           # Base de datos
├── logs/
└── scripts/
```

---

## Changelog

### v4.0 — Disaster Intelligence + Multi-Tenant + PostgreSQL
- **🚨 Disaster Agent:** NOAA + FEMA + NASA FIRMS — detección de flood, wildfire, hail, tornado, earthquake
- **🏠 Property DNA:** County Assessor (4 condados) + FEMA NFHL flood zones + roof material inference
- **📊 Tripartite Scoring:** 3 scores independientes (subcontractor, GC, insurance) por lead
- **🔀 Lead Router:** asignación automática de leads a GCs y Subs por especialidad y zona
- **🔍 CSLB Verifier:** verificación real de licencias CA vía scraping, risk levels, batch verify
- **🐘 PostgreSQL:** schema completo, connection pooling, JSONB + GIN index, migración desde SQLite
- **Multi-tenant roles:** subcontractor, gc, insurance con profile_data JSONB
- **8 nuevos API endpoints:** disasters, property-dna, tripartite scoring, lead assignment
- **4 nuevos API endpoints:** CSLB verify, search, verify-sub, batch
- Pipeline integrado en `base.py`: AI classify → Property DNA → Tripartite → Route

### v3.1 — 6 APIs Gratuitas
- Agente weather (Open-Meteo): alertas de lluvia y tormentas
- Agente federal contracts (USASpending.gov): contratos federales
- Geocoding gratuito Nominatim/OSM
- EPA ECHO en deconstruction_agent
- FEMA NFHL en lead_enrichment

### v3.0 — Seguridad, UX y Swipe
- Termómetro correlacionado con tipo de subcontratista
- Solo likes consumen cuota
- Alerta automática por rechazos consecutivos
- Rate limiting en admin endpoints
- Validación de whitelist

### v2.0 — Dashboard y Monetización
- Dashboard multi-usuario con RBAC
- Bot Telegram con trials y Stripe
- Feed público Tinder-style con OAuth
- Calendario de inspecciones

### v1.0 — Motor de Agentes
- 11 agentes de detección para Bay Area
- Scoring 0-100 con IA
- Deduplicación cross-agent
- Hot zones geográficas

---

## Meta de Valuación

| Etapa | Features | Valuación estimada |
|-------|----------|-------------------|
| Lead gen básico (v1) | 11 agentes, scoring, dedup | $1M–$5M |
| + Disaster Intelligence + Multi-tenant (v4) | 14 agentes, tripartite, Property DNA, CSLB | $50M–$150M |
| + AI + Integración aseguradoras (Fase 2) | Claims, Xactimate, SLA tracking | $400M–$1B+ |

---

## Licencia

Propietario — Todos los derechos reservados.
