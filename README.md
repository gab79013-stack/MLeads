
# MLeads — Plataforma de Generación de Leads para Subcontratistas

Sistema automatizado de generación de leads con IA para contratistas de Roofing, Drywall, Paint, Landscaping y Electrical en el Bay Area y principales ciudades de EE.UU.

Monitorea **54+ ciudades** en **9 condados** del Bay Area usando APIs públicas y de pago, detecta oportunidades de negocio por tipo de subcontratista, y entrega alertas en tiempo real con datos de contacto del GC directamente en Telegram.

Incluye **dashboard multi-usuario**, **swipe feed público tipo Tinder**, y **bot de Telegram con monetización** integrada.

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
┌─────────────────────────────────────────────────────────────────┐
│                        MLeads Platform                          │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────┐ │
│  │   Agentes    │   │  Lead Engine │   │   Notificaciones    │ │
│  │  (11 tipos)  │──▶│  Scoring AI  │──▶│  Telegram / WA /    │ │
│  │  54 ciudades │   │  Dedup/Merge │   │  Email / Slack      │ │
│  └──────────────┘   └──────┬───────┘   └─────────────────────┘ │
│                            │                                    │
│                    ┌───────▼────────┐                           │
│                    │  SQLite DB     │                           │
│                    │  consolidated_ │                           │
│                    │  leads         │                           │
│                    └───────┬────────┘                           │
│                            │                                    │
│         ┌──────────────────┼───────────────────┐               │
│         ▼                  ▼                   ▼               │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐         │
│  │  Dashboard  │  │  Swipe Feed  │  │  Telegram Bot  │         │
│  │  (admin)    │  │  (público)   │  │  (suscriptores)│         │
│  │  RBAC/JWT   │  │  Tinder UX   │  │  Trials/Pagos  │         │
│  └─────────────┘  └──────────────┘  └────────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Agentes de Detección

| Agente | Fuentes | Ciudades | Intervalo | Tipo de oportunidad |
|--------|---------|----------|-----------|---------------------|
| Permisos de Construcción | Socrata, CKAN (26 fuentes) | 54 | 60 min | ADU/remodel/roofing/electrical |
| Instalaciones Solares | Socrata, Google Solar, Aurora, EnergySage | 54 | 60 min | Solar → roofing/electrical/paint |
| Reportes 311 Plagas | SeeClickFix, Socrata, Thumbtack | 54 | 2 hrs | Plagas → drywall/paint reparación |
| Alertas NOAA Inundación | NOAA Weather API | 13 zonas | 30 min | Agua → drywall/paint/roofing |
| Construcciones Activas | Socrata, BuildZoom | 54 | 60 min | Framing → roofing/electrical |
| **Calendario Inspecciones** | **PDF (CC/Berkeley) + CKAN (SJ) + Predicción** | **54** | **Daily 9 AM** | **GC en sitio — timing perfecto** |
| Deconstrucción | Socrata, ATTOM | 54 | 2 hrs | Demolición → roofing/drywall |
| Propiedades Vendidas | Socrata (assessor data) | 10 condados | 2 hrs | Nuevo dueño → renovación |
| Eficiencia Energética | Socrata (benchmarking) | SF/Oak/SJ | 6 hrs | Baja eficiencia → panel upgrade |
| Google Places | Google Places API | Bay Area | 24 hrs | Constructores activos |
| Yelp Contractors | Yelp Fusion API | Bay Area | 24 hrs | Contratistas activos |

---

## Cobertura Geográfica (54 Ciudades)

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

---

## Motor de IA

### Lead Scoring (0–100)
Score automático por lead basado en múltiples señales:

| Score | Grado | Acción |
|-------|-------|--------|
| 90–100 | 🔥 HOT | Contactar de inmediato |
| 70–89 | 🌡️ WARM | Alta prioridad |
| 50–69 | 🌤️ MEDIUM | Seguimiento estándar |
| 25–49 | ❄️ COOL | Baja prioridad |
| 0–24 | ❄️ COLD | Archivo |

Factores de scoring: valor del proyecto · tipo de proyecto · calidad de contacto · recencia · geografía · fuente · señales de servicio · proximidad de inspección.

### Termómetro de Relevancia por Subcontratista
Cuando el usuario activa filtros de categoría (roofing, drywall, electrical…), el termómetro de cada lead cambia de mostrar el score genérico a mostrar el **índice de afinidad de tipo** (0–100):

- `70%` basado en match de palabras clave en descripción y tipo de servicio
- `30%` blended con el score bruto del lead
- Icono `🎯` reemplaza `°C` cuando el filtro está activo

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
- **Oportunidad de visita en persona** — cuando hay inspección programada, se muestra prominentemente como ventana de contacto
- **Link a Google Maps** en cada lead (dirección + ciudad)
- **Historial anónimo** — se guarda en localStorage; al registrarse migra al perfil
- **Alerta automática al admin** cuando un usuario acumula 50 rechazos consecutivos

### Cuotas
| Perfil | Leads disponibles |
|--------|-------------------|
| Anónimo | 10 likes |
| Usuario gratuito | 40 likes |
| Pro ($29/mes) | 200 leads |
| Premium ($99/mes) | Ilimitado |

### Filtros disponibles
- Categoría de subcontratista (roofing, drywall, paint, landscaping, electrical)
- Tipo de lead (permisos, solar, construcción, real estate, flood, energy)
- Ciudad + radio en millas
- Valor mínimo/máximo del proyecto
- Solo HOT

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

### Quick Start
```bash
pip install -r requirements.txt
python web_server.py
# Dashboard: http://localhost:5001/
# Login: http://localhost:5001/login.html
# Credenciales por defecto: admin / admin123
```

### API Endpoints principales
```
POST /api/auth/login
POST /api/auth/refresh
POST /api/auth/logout

GET  /api/leads
GET  /api/leads/{id}
POST /api/leads/{id}/contact
GET  /api/leads/{id}/contact-history
POST /api/leads/{id}/notes

GET  /api/stats
GET  /api/audit-log

GET  /api/admin/users
POST /api/admin/users
PUT  /api/admin/users/{id}
PUT  /api/admin/users/{id}/roles
PUT  /api/admin/users/{id}/access
PUT  /api/admin/users/{id}/expiration
DELETE /api/admin/users/{id}

GET  /api/swipe/feed
POST /api/swipe/action
GET  /api/swipe/my-contacts
POST /api/swipe/claim-anon
POST /api/swipe/feedback

GET  /api/scheduled_inspections?jurisdiction=berkeley
GET  /api/leads/{id}/scheduled_inspections
POST /api/admin/scheduler/fetch-now
```

---

## Calendario de Inspecciones

Integración automática de calendarios públicos para saber cuándo el GC estará en el sitio.

- **Contra Costa County:** PDF diario (actualizado a las 8:45 AM)
- **Berkeley:** PDF diario
- **San Jose:** Open Data Portal (CKAN API)
- **Otras ciudades:** Predicción automática por fase de construcción

Beneficios:
- **Timing perfecto:** el lead muestra cuántos días faltan para la inspección
- **Score boost:** leads con inspección en < 7 días reciben +8 puntos
- **CTA de visita:** mensaje prominente invitando a presentarse en persona antes de la inspección

Ver [CALENDAR_INTEGRATION.md](CALENDAR_INTEGRATION.md) para documentación completa.

---

## Bot de Telegram con Monetización

Bot integrado con sistema de trials y pagos vía Stripe.

- Usuarios nuevos reciben trial automático (configurable, por defecto 7 días)
- Al vencer el trial, se bloquea el acceso y se ofrece suscripción
- Stripe webhook actualiza el estado de pago automáticamente
- Panel de admin con lista de bot_users, extensión de trial y activación manual

---

## Notificaciones Multi-Canal

| Canal | Trigger |
|-------|---------|
| Telegram | Todos los leads nuevos en tiempo real |
| WhatsApp (Twilio) | Leads HOT al celular |
| Email (SendGrid) | Leads WARM + digest diario |
| Slack | Webhook para equipo |

---

## Funcionalidades Avanzadas

### Cross-Agent Deduplication
Cuando la misma propiedad aparece en múltiples agentes, se consolida en un "super lead" con datos fusionados y score boosteado.

### Hot Zone Detection
Clustering geográfico en tiempo real. Cuando 3+ leads caen dentro de 500m, genera una alerta de "zona caliente" con link a Google Maps.

### Contact Enrichment
1. CSVs locales en `contacts/` — fuzzy matching por nombre
2. CSLB — California Contractors State License Board
3. Hunter.io — email finder por dominio
4. Apollo.io — enrichment de contactos

---

## Seguridad

- JWT con `JWT_SECRET_KEY` validado en startup (falla si es el valor por defecto)
- Rate limiting: login (10/min), endpoints admin (20/min), delete (10/min)
- Validación de whitelist en parámetros: `status`, `contact_type`, `expires_at`
- Mensajes de error genéricos al cliente — detalles solo en logs
- Advertencia en startup si `ALLOWED_ORIGINS=*` (CORS abierto)
- Singleton de agentes con double-checked locking thread-safe

---

## Variables de Entorno

```env
# Base de datos
DB_PATH=data/leads.db

# Servidor
FLASK_ENV=production
PORT=5001
HOST=0.0.0.0
ALLOWED_ORIGINS=https://tu-dominio.com

# JWT (requerido — usar valor aleatorio largo)
JWT_SECRET_KEY=<random-256-bit-string>
JWT_ACCESS_EXPIRY=3600
JWT_REFRESH_EXPIRY=604800

# Telegram
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>

# IA (Claude)
ANTHROPIC_API_KEY=<key>
AI_CLASSIFIER_MODEL=claude-haiku-4-5-20251001
AI_ENRICHMENT_MODEL=claude-sonnet-4-6
AI_ENABLED=true

# Swipe quotas (opcionales)
SWIPE_ANON_LIMIT=10
SWIPE_FREE_LIMIT=40
SWIPE_PRO_LIMIT=200

# APIs externas (opcionales)
NREL_API_KEY=
GOOGLE_PLACES_KEY=
GOOGLE_SOLAR_KEY=
GOOGLE_MAPS_API_KEY=
YELP_API_KEY=
GOOGLE_CLIENT_ID=
FACEBOOK_APP_ID=
STRIPE_WEBHOOK_SECRET=
SENDGRID_API_KEY=
```

---

## Estructura del Proyecto

```
MLeads/
├── main.py                    # Entry point — orquesta todos los agentes
├── web_server.py              # Servidor web Flask
├── requirements.txt
│
├── agents/                    # Agentes de detección
│   ├── base.py
│   ├── permits_agent.py
│   ├── solar_agent.py
│   ├── rodents_agent.py
│   ├── flood_agent.py
│   ├── construction_agent.py
│   ├── deconstruction_agent.py
│   ├── realestate_agent.py
│   ├── energy_agent.py
│   ├── places_agent.py
│   └── yelp_agent.py
│
├── web/                       # Dashboard y API
│   ├── app.py                 # Flask API (150+ endpoints)
│   ├── auth.py                # JWT auth, RBAC
│   └── templates/
│       ├── index.html         # Dashboard admin
│       ├── login.html
│       └── swipe.html         # Feed público Tinder-style
│
├── workers/                   # Workers en background
│   ├── inspection_scheduler.py
│   └── telegram_bot.py
│
├── utils/
│   ├── web_db.py              # Schema y migraciones SQLite
│   ├── lead_scoring.py        # Scoring 0-100
│   ├── dedup.py               # Deduplicación cross-agent
│   ├── hot_zones.py           # Clustering geográfico
│   ├── contact_enrichment.py  # Enriquecimiento de contactos
│   ├── ai_classifier.py       # Clasificación AI (Claude)
│   ├── ai_outreach.py         # Mensajes AI personalizados
│   ├── ai_bot.py              # Bot AI conversacional
│   ├── telegram.py            # Notificaciones Telegram
│   ├── notifications.py       # Multi-canal (WA/Email/Slack)
│   ├── bot_users.py           # Gestión de suscriptores bot
│   └── billing.py             # Stripe webhook
│
├── contacts/                  # CSVs de contactos para fuzzy matching
├── data/                      # Base de datos SQLite
├── logs/                      # Logs del sistema
└── scripts/                   # Scripts de utilidad
```

---

## Changelog reciente

### v3.0 — Seguridad, UX y lógica de swipe
- Termómetro 100% correlacionado con tipo de subcontratista seleccionado
- Solo leads con teléfono o email son mostrados en el feed
- Solo swipes a la derecha (likes) consumen cuota — rechazos son gratis
- Progresión de score avanza solo con likes (no con dislikes)
- Alerta automática al admin al acumular 50 rechazos por usuario
- Historial anónimo migra a `lead_contacts` al registrarse
- Fix: tabla `beta_feedback` faltante → feedback de usuarios ahora funciona
- Fix: mismatch `JWT_SECRET_KEY` / `JWT_SECRET` entre módulos
- Rate limiting en todos los endpoints admin (20 req/min)
- Validación de `status`, `contact_type`, `expires_at` con whitelist
- Mensajes de error genéricos en respuestas 500
- Double-checked locking thread-safe en singleton de agentes

### v2.0 — Dashboard y Monetización
- Dashboard multi-usuario con RBAC completo
- Bot Telegram con trials y Stripe
- Feed público Tinder-style con OAuth social login
- Calendario de inspecciones integrado

### v1.0 — Motor de Agentes
- 11 agentes de detección para Bay Area
- Scoring 0-100 con IA
- Deduplicación cross-agent
- Hot zones geográficas
- Notificaciones multi-canal

---

## Licencia

Propietario — Todos los derechos reservados.
