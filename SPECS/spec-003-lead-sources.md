# SPEC-003: Lead Sources & Agents

## Qué
Sistema de agents que scrapean y consolidan leads de múltiples fuentes públicas.

## Por qué
Sin leads, no hay producto. Los agents corren en background, traen data nueva diariamente. Los 2 mercados (permits y clima) requieren strategies diferentes.

## Fuentes de datos

### Permits de construcción → Subcontractors
| Agente | Fuente | Ciudades | Frecuencia |
|--------|--------|----------|------------|
| permits | APIs públicas de city/portales | Chicago, San Jose, Dallas, SF, Honolulu, Cambridge, El Paso, Austin, Baton Rouge, Calgary, Edmonton, San Diego, Seattle, etc. | Diaria (cada 15 min por ciudad, rotando) |

Datos capturados: dirección, tipo de permit, contractor, owner, valor, fase, fecha, score

### Clima/Desastres → General Contractors
| Agente | Fuente | Detecta |
|--------|--------|---------|
| weather | Open-Meteo API (gratis) | Lluvia fuerte, nieve, viento, temperatura extrema |
| flood | NOAA AHPS (gratis) | Inundaciones, ríos desbordados |
| disaster | NOAA + FEMA + NASA FIRMS (gratis) | Tornados, incendios, declaraciones de emergencia |

Datos generados: tipo de evento, severidad, ubicación, propiedades afectadas, urgencia

### Energy/Rodents/Solar → Nichos
| Fuente | Target | Leads |
|--------|--------|-------|
| energy (Crossdata) | Solar/energy contractors | 392 leads, 220 con teléfono |
| rodents | Pest control | 114 leads, 60 con teléfono |
| solar | Solar installers | 125 leads, 37 con teléfono |

## Consolidación
- **Tabla:** `consolidated_leads` — deduplica por `address_key`
- **Merged sources:** un address puede tener leads de múltiples agents → `agent_sources` (comma-separated)
- **Enriquecimiento:** teléfono, email, score, AI classification

## Phone enrichment
1. **Embedded extraction** (gratis, inmediato): Regex sobre datos del permit — 433 leads de Honolulu
2. **DuckDuckGo search** (gratis, lento): Buscar contractor + ciudad — 36% hit rate businesses
3. **Google Places API** ($200 gratis/mes): Mejor hit rate, requiere API key

## AI Classification
- Modelo: Vultr Inference (MiniMax-M2.5, gratis)
- Script: `reclassify2.py`
- Clasifica: tipo de subcontractor (roofing, drywall, electrical, etc.)
- Score: 0-100, grade: HOT/WARM/MEDIUM/COLD
- 558/600 leads con teléfono clasificados (93%)
- **Pendiente:** Batch para los ~7,000 sin teléfono

## Self-pull detection
- Cuando un trade company (e.g. plumber) saca su propio permit → no es un lead de plomería
- Reclassify a downstream trade (plumber pulling permit → DRYWALL lead)
- Columna `is_dead_lead = 1` para filtrar del feed

## Stats actuales
- 8,407 leads totales
- 1,141 con teléfono (13.6%)
- 76 en pipeline
- 8,067 actualizados en los últimos 7 días
