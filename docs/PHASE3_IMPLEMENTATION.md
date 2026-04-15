# Fase 3 — UX y monetización

## Objetivo
Entregar dashboard web con autenticación local sobre Kortix, billing con Stripe webhook y triggers de notificación para operación comercial.

## Entregables implementados
- `dashboard/server.py`: API/dashboard web autenticado.
- `dashboard/index.html`: interfaz mínima para login, métricas, leads y notificaciones.
- `scripts/init_phase3_schema.sql`: tablas de auth, billing, sesiones, notificaciones y webhooks.
- `scripts/init_phase3_db.py`: inicialización de DB real y seed opcional de usuario admin.
- `scripts/stripe_webhook.py`: receptor Stripe con validación de firma e idempotencia.
- `scripts/trigger_utils.py` + `scripts/evaluate_triggers.py`: triggers inteligentes y notificaciones persistidas.
- `scripts/verify_phase3.sh`: verificación end-to-end de Fase 3.

## DB real
La Fase 3 reutiliza `/workspace/.kortix/kortix.db` como base real de Kortix y añade tablas de aplicación para auth, billing y notificaciones.

## Credenciales locales por defecto
```text
email: admin@kortix.local
password: ChangeMe123!
```

Cambiar inmediatamente si se usa fuera de entorno local.

## Comandos exactos de despliegue
```bash
/workspace/scripts/backup_sqlite.sh /workspace/.kortix/kortix.db /workspace/backups/phase3
python3 /workspace/scripts/init_phase3_db.py --db /workspace/.kortix/kortix.db --seed-demo-user --seed-demo-subscription --report /workspace/reports/phase3/real-db-init.json
python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json
python3 /workspace/scripts/evaluate_triggers.py --db /workspace/.kortix/kortix.db --json-out /workspace/reports/phase3/real-trigger-evaluation.json
python3 /workspace/dashboard/server.py --db /workspace/.kortix/kortix.db --port 43123
python3 /workspace/scripts/stripe_webhook.py --db /workspace/.kortix/kortix.db --port 43124
```

## Healthcheck
```bash
python3 /workspace/scripts/healthcheck_phase3.py --db /workspace/.kortix/kortix.db
```

## Trigger rules implementadas
- `lead_qualified`: notifica cuando un lead supera score 80.
- `duplicate_pool_alert`: alerta cuando hay 3 o más fingerprints sin resolver.
- `usage_limit_warning`: alerta cuando el uso supera 80% del plan.
