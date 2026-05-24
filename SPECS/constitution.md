# MLeads — Constitution

## Propósito
Plataforma de lead generation para subcontractors de construcción en EE.UU. Detecta proyectos ANTES de que el homeowner busque contractor.

## Principios

### Stack
- **Backend:** Python 3.12 + Flask + SQLite (WAL mode)
- **Frontend:** Vanilla HTML/CSS/JS (no frameworks)
- **AI:** Vultr Inference (gratis) — modelos Qwen/MiniMax
- **Server:** 45.32.89.38 (Vultr VPS, $6/mes)
- **No paid APIs** a menos que no exista alternativa gratuita

### Datos
- **2 mercados:**
  1. Permits de construcción → Subcontractors (roofing, drywall, paint, electrical, plumbing, HVAC, flooring, concrete, framing, windows, landscaping)
  2. Clima/Desastres → General Contractors (storm damage, flood, fire)
- **Fuentes (todas gratis):** APIs públicas de permits, NOAA, FEMA, NASA FIRMS, Open-Meteo
- **Deduplicación:** `address_key` como PK en `consolidated_leads`
- **Phone required:** Solo leads con teléfono se muestran en el swipe feed

### UI/UX
- **Mobile-first** (subcontractors están en campo)
- **Swipe UI** tipo Tinder — like = auto-add to pipeline
- **Pipeline Kanban** nativo (no Huly)
- **2 idiomas:** Español (default) + English
- **Freemium:** Anon users ven leads con límite, registered = más, paid = ilimitado

### Código
- Shell escaping is a nightmare → **siempre** escribir Python patches como archivos, SCP, luego ejecutar
- SQLite WAL mode puede lock → **siempre** stop mleads-web antes de schema changes
- `git checkout` restaura código → verificar patches después
- `txt.replace()` debe matchear EXACTO — newlines, quotes, todo
- Imports: verificar que todo lo que se usa esté importado (lección de los 3 bugs del feed)

### Seguridad
- No exponer datos privados del usuario
- `trash` > `rm`
- Preguntar antes de acciones externas (emails, tweets, cualquier cosa pública)

### Negocio
- **Lead = permit + teléfono + AI classification**
- **Like (swipe right) = auto-add to pipeline.** No buttons extra.
- **Self-pull detection:** Contractor pulling own permit → reclassify to downstream trade
- **Costo marginal por lead: ~$0.10** (scraping gratis, IA gratis)
- **Break-even: 1 suscriptor Pro ($99/mes)**
