# SPEC-001: Swipe Feed

## Qué
Feed estilo Tinder de leads de construcción, ordenados por frescura.

## Por qué
Es la interfaz principal del producto. El subcontractor abre la app, ve leads nuevos, swipea, y los que le gustan van al pipeline automáticamente.

## Comportamiento esperado

### Carga del feed
- `GET /api/swipe/feed` retorna leads con teléfono, no swipados por el usuario
- **Orden: `first_seen DESC, score DESC`** (más frescos primero, score como tiebreaker)
- Requiere: `has_phone = 1` AND `is_dead_lead = 0`
- Round-robin por ciudad (no monopolizar una sola ciudad)
- Anon users: `anon_id` enviado siempre como fallback

### Swipe action
- `POST /api/swipe/action` con `{lead_id, action, anon_id}`
- Like (swipe right) → auto-add to pipeline status "Nuevo"
- Dislike (swipe left) → solo se registra, no consume quota
- **Quota:** Anon = 9999, Free user = 40 likes, Paid = ilimitado
- Respuesta incluye `swipes_count` para actualizar contador

### Swipe counter (frontend)
- **Actualización optimista:** Like → counter baja inmediatamente
- Si el POST falla → rollback al valor anterior + toast de error
- Si el POST éxito → reconciliar con `data.swipes_count` del servidor
- Paid users ven "Pro ✨" en vez de contador

### Frescura (badges)
- < 2 días: "NUEVO · Xd" (verde)
- 2-7 días: "Xd atrás" (amarillo)
- > 7 días: "Xw/sem atrás" (gris)

### Pipeline auto-add
- Like → `INSERT OR IGNORE INTO lead_pipeline (lead_id, user_id, status='Nuevo')`
- Visible en `/pipeline`

## Endpoints
| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| GET | `/api/swipe/feed` | Optional | Lista de leads no swipados |
| POST | `/api/swipe/action` | Optional | Registrar swipe |
| GET | `/api/swipe/pulse` | None | Polling para nuevos leads |
| POST | `/api/swipe/log-contact` | Registered | Log de click en teléfono/email |
| GET | `/api/swipe/my-contacts` | Required | Leads con like del usuario |

## Datos
- Tabla: `consolidated_leads` (8,407 rows)
- Tabla: `swipe_actions` (user_id, anon_id, lead_id, action, created_at)
- Tabla: `lead_pipeline` (lead_id, user_id, status, created_at, updated_at)

## Edge cases
- Token expirado en localStorage → `S.authed=true` pero sin identidad → siempre mandar `anon_id`
- Feed vacío → "No hay más leads" con botón "Recargar"
- 500 error → no mostrar como "no hay leads", mostrar error real
