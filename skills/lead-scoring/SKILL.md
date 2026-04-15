# Lead Scoring Skill

## Purpose
Asigna score a leads B2B usando OpenAI, contexto compartido de Kortix y memoria persistente en SQLite.

## Inputs
```json
{
  "lead_id": "required|string",
  "mode": "auto|openai|heuristic"
}
```

## Outputs
```json
{
  "lead_id": "string",
  "score": 0,
  "status": "new",
  "confidence": 0.0,
  "factors": {},
  "reasoning": "string",
  "model": "string",
  "duplicate_of": null
}
```

## Shared Context
- Lee `/workspace/.kortix/CONTEXT.md`
- Lee `/workspace/.kortix/memory/shared-context.json`
- Consulta `leads`, `lead_interactions`, `lead_scoring_history` y `dedup_pool`

## Execution Flow
1. Cargar lead e historial.
2. Resolver fingerprint y revisar duplicados.
3. Construir contexto compartido desde Kortix.
4. Usar OpenAI si hay credenciales; si no, aplicar scoring heurístico.
5. Persistir score, factores e interacción de scoring.

## Thresholds
- `>= 80`: `qualified`
- `50 - 79`: `contacted`
- `< 50`: `new`

## Example Command
```bash
python3 /workspace/scripts/score_lead.py --lead-id lead_demo_001 --mode auto
```
