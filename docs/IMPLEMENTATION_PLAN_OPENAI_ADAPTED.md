# Plan adaptado: MLeads + Kortix con OpenAI (4 semanas)

## Semana 1: Migración de infraestructura
- [ ] Configurar máquina Kortix con Ubuntu y dependencias
- [ ] Migrar DB SQLite y variables de entorno
- [ ] Convertir 3 agentes críticos a formato `agent.md`
- [ ] Configurar backups, healthcheck y verificación inicial

## Semana 2: IA y memoria
- [ ] Implementar skill de lead scoring con memoria persistente
- [ ] Conectar OpenAI a través de Kortix con contexto compartido
- [ ] Configurar autowork para verificación automática de leads
- [ ] Generar `shared-context.json` para reutilización entre agentes

## Semana 3: UX y monetización
- [ ] Integrar dashboard web con autenticación de Kortix
- [ ] Conectar Stripe webhook al sistema de billing de Kortix
- [ ] Implementar triggers para notificaciones inteligentes
- [ ] Exponer métricas de scoring, conversión y actividad por lead

## Semana 4: Testing y lanzamiento
- [ ] Pruebas de carga con 100+ leads/hora
- [ ] Validar que no haya duplicación cross-agente
- [ ] Documentación para usuarios y rollback plan
- [ ] Checklist de go-live con validación de OpenAI, DB y webhooks

## Riesgos principales
- Rate limiting o errores transitorios de OpenAI
- Variables de entorno incompletas
- Duplicación de leads entre agentes
- Fallos en billing o webhooks de Stripe
- Degradación de performance al crecer el volumen

## Métricas de éxito
- `PRAGMA integrity_check = ok`
- Score persistido en `lead_scoring_history`
- Duplicación < 1%
- 100+ leads/hora procesados
- Tiempo de scoring p95 < 3s
- Dashboard y autowork operativos en producción

## Variables clave
```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_MAX_OUTPUT_TOKENS=4096
LEAD_SCORING_MODE=auto
KORTIX_DB_PATH=/workspace/.kortix/kortix.db
```

## Comandos exactos de despliegue
```bash
/workspace/scripts/bootstrap_ubuntu.sh
cp /workspace/.env.example /workspace/.env && chmod 600 /workspace/.env
python3 /workspace/scripts/migrate_mleads_sqlite.py --target /workspace/.kortix/kortix.db --schema /workspace/scripts/init_mleads_schema.sql --backup-dir /workspace/backups/phase1 --report /workspace/reports/migration-report.json
/workspace/scripts/register_agents.sh
/workspace/scripts/register_skills.sh
python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json
python3 /workspace/scripts/autowork/verify_leads.py --db /workspace/.kortix/kortix.db --context /workspace/.kortix/memory/shared-context.json --batch-size 50 --concurrency 5 --mode auto
```

## Estado actual del proyecto
- Fase 1 implementada y verificada
- Fase 2 implementada y verificada
- Integración normalizada a OpenAI
