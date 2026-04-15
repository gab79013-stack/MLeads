# Carga segura de secretos y despliegue

## Cargar secretos sin escribirlos en archivos
Usar el secrets manager local:

```bash
curl -s -X POST http://localhost:8000/env/OPENAI_API_KEY -d '{"value":"<OPENAI_API_KEY>"}'
curl -s -X POST http://localhost:8000/env/OPENAI_MODEL -d '{"value":"gpt-4.1-mini"}'
curl -s -X POST http://localhost:8000/env/OPENAI_MAX_OUTPUT_TOKENS -d '{"value":"4096"}'
```

Opcionalmente también:

```bash
curl -s -X POST http://localhost:8000/env/STRIPE_SECRET_KEY -d '{"value":"<STRIPE_SECRET_KEY>"}'
curl -s -X POST http://localhost:8000/env/STRIPE_WEBHOOK_SECRET -d '{"value":"<STRIPE_WEBHOOK_SECRET>"}'
curl -s -X POST http://localhost:8000/env/NEXTAUTH_SECRET -d '{"value":"<NEXTAUTH_SECRET>"}'
```

## Ver secretos cargados
```bash
curl -s http://localhost:8000/env/OPENAI_MODEL
curl -s http://localhost:8000/env/OPENAI_MAX_OUTPUT_TOKENS
```

## Despliegue técnico
```bash
/workspace/scripts/deploy_production.sh /workspace/.kortix/kortix.db
/workspace/scripts/start_pm2.sh
/workspace/scripts/install_cron.sh
```

## Verificación rápida post-deploy
```bash
/workspace/scripts/smoke_test_runtime.sh /workspace/reports/production/smoke.db
pm2 status
crontab -l
```
