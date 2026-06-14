# SPEC-004: Auth & Pricing

## Qué
Sistema de autenticación freemium con 3 tiers.

## Por qué
El producto necesita monetización. El freemium atrae usuarios y el tier de pago convierte los que ven valor.

## Tiers

### Anonymous (sin login)
- 9,999 likes (prácticamente ilimitado por ahora)
- Ve leads con teléfono
- No puede enviar estimados
- `anon_id` en localStorage (no persiste si limpia browser)

### Free (registrado, $0)
- 40 likes
- Pipeline ilimitado
- Enviar estimados
- Login: Google OAuth, Facebook OAuth, email/password

### Pro ($29/mes)
- 200 leads/mes
- Todos los filtros
- Contacto completo
- Pipeline

### Premium ($99/mes)
- Leads ilimitados
- Señales HOT priorizadas
- Inspecciones en tiempo real
- Soporte prioritario

### Elite ($500/mes)
- Leads curados de alta evidencia
- `premium_quality_score >= 70`
- Fuente oficial auditable
- Contacto disponible cuando aplica
- Score HOT o ventana clara de acción
- Pensado para GCs donde un cierre de $10k+ paga el servicio

## Storm leads (para GCs) — NO implementado aún
| Tier | Precio | Incluye |
|------|--------|---------|
| Storm Alert | $49/mes | Alertas de clima, leads sin contacto |
| Storm Pro | $149/mes | Alertas + propiedades afectadas + contacto |
| First Responder | $299/mes | Tiempo real, exclusividad, API |

## Auth flow
1. `localStorage.ml_anon` → `anon_id` (generado en primer visit)
2. Google/Facebook OAuth → JWT (access + refresh tokens)
3. `S.authed = !!getToken()` — **PROBLEMA CONOCIDO:** token expirado = sin identidad
4. **Fix:** siempre mandar `anon_id` como fallback

## Endpoints de auth
| Método | Ruta | Descripción |
|--------|------|-------------|
| POST | `/api/auth/register` | Email/password signup |
| POST | `/api/auth/login` | Email/password login |
| GET | `/api/auth/google` | Google OAuth redirect |
| GET | `/api/auth/facebook` | Facebook OAuth redirect |
| POST | `/api/swipe/claim-anon` | Merge anon swipes to registered user |

## Pendiente
- [x] Stripe Checkout para Pro/Premium/Elite
- [ ] Gating completo por tier en todas las superficies
- [ ] JWT refresh token rotation
- [ ] Email verification
