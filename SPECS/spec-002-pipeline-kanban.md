# SPEC-002: Pipeline Kanban

## Qué
Tablero Kanban para trackear leads desde "Nuevo" hasta "Cerrado".

## Por qué
El subcontractor necesita follow-up. Un lead sin seguimiento es dinero perdido. El pipeline hace visible el progreso.

## Comportamiento esperado

### Columnas
1. **Nuevo** — Lead recién likeado (auto-creado por swipe)
2. **Contactado** — Se llamó/envió mensaje
3. **Propuesta** — Se envió estimado
4. **Negociación** — Discutiendo términos
5. **Cerrado** — Ganado o perdido (sub-estados: "Ganado", "Perdido", "No calificado")

### Acciones
- Drag & drop entre columnas (desktop)
- Touch support para mobile (PENDIENTE)
- Click en lead → ver detalle (teléfono, email, dirección, valor, score)
- "Enviar Estimado" → requiere registration (muro de paywall para anon)
- Nota/text input por lead

### Auto-acciones
- Like en swipe → auto-crea en "Nuevo"
- Click en teléfono/email → auto-avanza a "Contactado"

## Endpoints
| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | `/api/pipeline` | Optional | Leads en pipeline del usuario |
| PUT | `/api/pipeline/:id` | Required | Mover lead de columna |
| POST | `/api/pipeline/estimate` | Required | Enviar estimado |

## Datos
- Tabla: `lead_pipeline` (lead_id, user_id, status, notes, created_at, updated_at)
- Status inicial: "Nuevo"
- 76 leads en pipeline actualmente

## Pendiente
- [ ] Mobile touch support (drag & drop no funciona en móvil)
- [ ] Notificaciones cuando un lead lleva >7 días sin moverse
- [ ] Export a CSV
