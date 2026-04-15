# Producción — hardening y operación

## Artefactos creados
- PM2: `/workspace/ecosystem.config.js`
- Deploy: `/workspace/scripts/deploy_production.sh`
- PM2 start: `/workspace/scripts/start_pm2.sh`
- Cron/autowork: `/workspace/scripts/install_cron.sh`
- Smoke test runtime: `/workspace/scripts/smoke_test_runtime.sh`
- Nginx sample: `/workspace/config/nginx.mleads.conf`

## Secuencia recomendada
```bash
/workspace/scripts/deploy_production.sh /workspace/.kortix/kortix.db
/workspace/scripts/start_pm2.sh
/workspace/scripts/smoke_test_runtime.sh /workspace/reports/production/smoke.db
```

## Scheduler
- Preferido en este proyecto: `pm2 cron_restart` con app `mleads-autowork`
- `install_cron.sh` queda como opción para hosts donde `crontab` esté habilitado

## Qué valida el smoke test
- health del dashboard
- login local
- creación de lead con idempotencia
- stats API
- webhook Stripe firmado
- actualización de plan en subscriptions

## Proxy/TLS
Usar el bloque de ejemplo en `/workspace/config/nginx.mleads.conf` y ajustar `server_name` + certificados.
