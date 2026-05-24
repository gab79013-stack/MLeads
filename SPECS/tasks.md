# MLeads — Task List

## 🔴 Crítico (bloquea negocio)
- [ ] Configurar Stripe/PayPal para cobrar suscripciones
- [ ] Landing page (hero: "Know every construction project before your competitors")
- [ ] Verificar que el swipe feed funciona end-to-end desde navegador real
- [ ] Limpiar `localStorage` de usuarios con tokens expirados

## 🟡 Alto impacto
- [ ] Mobile touch support para pipeline (drag & drop en móvil)
- [ ] Google Places API para phone enrichment (2,783 leads con nombre, $0 los primeros 5K)
- [ ] Batch AI classification para 7,000+ leads sin teléfono
- [ ] Email SMTP para outreach (registro + follow-up)
- [ ] WhatsApp Business API para outreach directo

## 🟢 Mejoras
- [ ] Dashboard de métricas (conversión, tiempo en pipeline, leads/día)
- [ ] Auto-reload cuando hay nuevos leads (WebSocket o polling)
- [ ] Export pipeline a CSV
- [ ] Notificaciones push cuando hay leads calientes nuevos
- [ ] Multi-usuario (roles, permisos)
- [ ] Refinar phone enrichment: filtrar falso positivos de DuckDuckGo

## 📋 Infraestructura
- [ ] Push a GitHub (deploy key o token)
- [ ] CI/CD (auto-deploy on push)
- [ ] Monitoring (alertas si el server o la DB falla) — **cron job activo**
- [ ] Backup nocturno — **cron job activo (bfcf7665)**
- [ ] SSL/HTTPS (Let's Encrypt)

## 🎯 Fase 1: Validación (semanas 1-4)
Meta: 10 contractors pagando

1. Landing page live
2. 5 leads gratis por email a 50 contractors
3. Primer pago → iterar

## 🎯 Fase 2: Producto (semanas 5-8)
Meta: Portal del contractor, notificaciones, AI recommendations

## 🎯 Fase 3: Escala (semanas 9-12)
Meta: Enrichment masivo, referral program, partnerships
