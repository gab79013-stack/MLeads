# Fase 2 — IA y memoria

## Objetivo
Agregar scoring de leads con memoria persistente, contexto compartido para OpenAI/Kortix y un ciclo de autowork para verificación automática.

## Entregables implementados
- `skills/lead-scoring/SKILL.md`: skill fuente de lead scoring.
- `scripts/register_skills.sh`: sincroniza skills al runtime `.opencode/skills`.
- `scripts/build_shared_context.py`: genera contexto compartido desde SQLite.
- `scripts/score_lead.py`: scoring con OpenAI cuando hay API key y fallback heurístico verificable.
- `scripts/autowork/verify_leads.py`: procesamiento batch para leads pendientes.
- `scripts/seed_phase2_demo.py`: dataset demo para QA local.
- `scripts/healthcheck_phase2.py`: validación de skill, scripts y contexto.
- `scripts/verify_phase2.sh`: verificación integral de la fase.

## Decisiones clave
- OpenAI solo se invoca si `OPENAI_API_KEY` está configurada; si no, el sistema sigue operando con scoring heurístico.
- La memoria persistente queda en `lead_scoring_history` y `lead_interactions`.
- El contexto compartido se materializa en `/workspace/.kortix/memory/shared-context.json` para que otros agentes lo reutilicen.

## Variables requeridas
```env
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
OPENAI_MAX_OUTPUT_TOKENS=4096
LEAD_SCORING_MODE=auto
```

## Comandos exactos de despliegue

### 1. Registrar skill en runtime
```bash
chmod +x /workspace/scripts/register_skills.sh
/workspace/scripts/register_skills.sh
```

### 2. Construir contexto compartido
```bash
python3 /workspace/scripts/build_shared_context.py \
  --db /workspace/.kortix/kortix.db \
  --output /workspace/.kortix/memory/shared-context.json
```

### 3. Calificar un lead
```bash
python3 /workspace/scripts/score_lead.py \
  --db /workspace/.kortix/kortix.db \
  --context /workspace/.kortix/memory/shared-context.json \
  --lead-id lead_123 \
  --mode auto
```

### 4. Ejecutar autowork manualmente
```bash
python3 /workspace/scripts/autowork/verify_leads.py \
  --db /workspace/.kortix/kortix.db \
  --context /workspace/.kortix/memory/shared-context.json \
  --batch-size 50 \
  --concurrency 5 \
  --mode auto
```

### 5. Programar autowork en cron
```bash
(crontab -l 2>/dev/null; echo "*/15 * * * * /usr/bin/python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json >/dev/null 2>&1 && /usr/bin/python3 /workspace/scripts/autowork/verify_leads.py --db /workspace/.kortix/kortix.db --context /workspace/.kortix/memory/shared-context.json --batch-size 50 --concurrency 5 --mode auto >> /workspace/logs/autowork.log 2>&1") | crontab -
```

## Riesgos cubiertos en esta fase
- Ausencia de API key de OpenAI: mitigado con fallback heurístico.
- Contexto compartido obsoleto: mitigado con script dedicado y cron previo al autowork.
- Re-scoring infinito: filtrado por estado y ventana de actualización.

## Métricas de éxito de Fase 2
- Skill registrada en `.opencode/skills/lead-scoring/SKILL.md`
- `shared-context.json` generado con métricas válidas
- `score_lead.py` persiste en `lead_scoring_history`
- `verify_leads.py` procesa lotes sin errores
