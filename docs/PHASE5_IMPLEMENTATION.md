# Fase 5 — Testing y lanzamiento

## Objetivo
Validar capacidad operativa, integridad de datos, respaldo/rollback y checklist de salida a producción.

## Entregables implementados
- `scripts/test_phase5_load.py`: prueba de carga vía API dashboard.
- `scripts/test_phase5_integrity.py`: validación de duplicados y orfandades.
- `scripts/pre_deploy_backup.sh`: backup pre-deploy.
- `scripts/rollback_from_backup.sh`: restauración desde backup.
- `scripts/verify_phase5.sh`: verificación integral de Fase 5.

## Comandos exactos de despliegue
```bash
/workspace/scripts/pre_deploy_backup.sh /workspace/.kortix/kortix.db /workspace/backups/predeploy
python3 /workspace/scripts/test_phase5_integrity.py --db /workspace/.kortix/kortix.db --json-out /workspace/reports/phase5/real-integrity-report.json
python3 /workspace/scripts/healthcheck_phase4.py --db /workspace/.kortix/kortix.db --json-out /workspace/reports/phase5/real-go-live-healthcheck.json
```

## Prueba de carga
```bash
python3 /workspace/dashboard/server.py --db /workspace/.kortix/kortix.db --port 43123
python3 /workspace/scripts/test_phase5_load.py --base-url http://127.0.0.1:43123 --email admin@kortix.local --password ChangeMe123! --total 120 --concurrency 12 --json-out /workspace/reports/phase5/real-load-test.json
```

## Rollback
```bash
BACKUP_DIR=$(/workspace/scripts/pre_deploy_backup.sh /workspace/.kortix/kortix.db /workspace/backups/predeploy)
/workspace/scripts/rollback_from_backup.sh "$BACKUP_DIR" /workspace/.kortix/kortix.db
```

## Criterios de salida
- Carga >= 100 leads/hora
- Integridad OK sin duplicados exactos ni orfandades
- Healthcheck OK
- Backups y rollback probados
