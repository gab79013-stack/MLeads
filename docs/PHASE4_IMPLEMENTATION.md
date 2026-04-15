# Fase 4 — Confiabilidad y gobierno

## Objetivo
Reducir errores operativos y escalar el sistema con deduplicación cross-agente, idempotencia, locking, auditoría y logging estructurado.

## Entregables implementados
- `scripts/init_phase4_schema.sql`: tablas de auditoría, idempotencia, locks, matches duplicados y snapshots.
- `scripts/reliability_utils.py`: utilidades comunes de confiabilidad.
- `scripts/init_phase4_db.py`: inicializa esquema de Fase 4.
- `scripts/reconcile_dedup.py`: deduplicación cross-agente y actualización del pool.
- `scripts/seed_phase4_demo.py`: dataset de prueba con duplicados operativos.
- `scripts/healthcheck_phase4.py`: healthcheck de confiabilidad.
- `scripts/verify_phase4.sh`: verificación end-to-end.

## Mejoras aplicadas
- `score_lead.py`: ahora usa lock por lead, idempotency key, auditoría y structured logs.
- `autowork/verify_leads.py`: usa idempotency keys por ciclo y structured logs.
- `stripe_webhook.py`: incorpora idempotencia formal y auditoría.
- `dashboard/server.py`: RBAC básico, auditoría y endpoints de gobierno (`/api/audit-logs`, `/api/dedup-report`).

## Comandos exactos de despliegue
```bash
/workspace/scripts/backup_sqlite.sh /workspace/.kortix/kortix.db /workspace/backups/phase4
python3 /workspace/scripts/init_phase4_db.py --db /workspace/.kortix/kortix.db --report /workspace/reports/phase4/real-phase4-init.json
python3 /workspace/scripts/reconcile_dedup.py --db /workspace/.kortix/kortix.db --json-out /workspace/reports/phase4/real-dedup-report.json
python3 /workspace/scripts/healthcheck_phase4.py --db /workspace/.kortix/kortix.db --json-out /workspace/reports/phase4/real-phase4-healthcheck.json
```

## Logs estructurados
- `/workspace/logs/lead_scoring.jsonl`
- `/workspace/logs/autowork.jsonl`
- `/workspace/logs/stripe_webhook.jsonl`
- `/workspace/logs/dashboard_access.jsonl`

## Resultado esperado
- Una sola ejecución efectiva por idempotency key.
- Sin doble scoring simultáneo del mismo lead.
- Trazabilidad de acciones sensibles vía `audit_logs`.
- Visibilidad de duplicados cross-agente vía `lead_duplicate_matches`.
