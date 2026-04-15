# Fase 1 — Base técnica e infraestructura

## Objetivo
Dejar MLeads listo para correr sobre Kortix con una base reproducible: Ubuntu, SQLite migrable, variables de entorno, agentes críticos en `agent.md`, backup, healthcheck y verificación inicial.

## Entregables implementados
- `scripts/bootstrap_ubuntu.sh`: bootstrap de Ubuntu con dependencias base.
- `scripts/init_mleads_schema.sql`: esquema SQLite de leads y memoria persistente.
- `scripts/migrate_mleads_sqlite.py`: inicialización y migración desde una SQLite origen.
- `scripts/backup_sqlite.sh`: backup consistente del target DB.
- `scripts/register_agents.sh`: sincroniza los agentes fuente hacia `.opencode/agents`.
- `scripts/healthcheck_phase1.py`: healthcheck técnico de Fase 1.
- `scripts/verify_phase1.sh`: secuencia reproducible de verificación.
- `agents/*/agent.md`: 3 agentes críticos convertidos.
- `.env.example`: plantilla de variables necesarias.

## Comandos exactos de despliegue

### 1. Preparar Ubuntu
```bash
chmod +x /workspace/scripts/bootstrap_ubuntu.sh
/workspace/scripts/bootstrap_ubuntu.sh
```

### 2. Configurar variables
```bash
cp /workspace/.env.example /workspace/.env
chmod 600 /workspace/.env
```

### 3. Inicializar o migrar la base
```bash
python3 /workspace/scripts/migrate_mleads_sqlite.py \
  --target /workspace/.kortix/kortix.db \
  --schema /workspace/scripts/init_mleads_schema.sql \
  --backup-dir /workspace/backups/phase1
```

Si existe una SQLite origen:
```bash
python3 /workspace/scripts/migrate_mleads_sqlite.py \
  --source /ruta/origen/mleads.db \
  --target /workspace/.kortix/kortix.db \
  --schema /workspace/scripts/init_mleads_schema.sql \
  --backup-dir /workspace/backups/phase1 \
  --report /workspace/reports/migration-report.json
```

### 4. Registrar agentes críticos en el runtime
```bash
chmod +x /workspace/scripts/register_agents.sh
/workspace/scripts/register_agents.sh
```

### 5. Ejecutar verificación técnica
```bash
chmod +x /workspace/scripts/verify_phase1.sh
/workspace/scripts/verify_phase1.sh
```

## Riesgos cubiertos en esta fase
- Bloqueo o corrupción de SQLite: mitigado con backup previo e `integrity_check`.
- Variables faltantes: mitigado con `.env.example` y validación de healthcheck.
- Pérdida de paridad funcional de agentes: mitigado con agentes fuente + registro al runtime.
- Diferencias entre entornos: mitigado con bootstrap reproducible para Ubuntu.

## Métricas de éxito de Fase 1
- `PRAGMA integrity_check = ok`
- 4 tablas MLeads presentes en `.kortix/kortix.db`
- 3 agentes sincronizados a `.opencode/agents`
- 100% de variables críticas presentes en `.env` o `.env.example`
- Verificación automática exitosa vía `scripts/verify_phase1.sh`
