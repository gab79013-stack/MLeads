# Lead Analyzer

## Purpose
Genera métricas operativas y patrones de conversión para optimizar el pipeline de MLeads.

## Inputs
```json
{
  "analysis_type": "conversion|trends|anomaly|report",
  "date_range": "7d|30d|90d",
  "group_by": "source|status|agent|score_range"
}
```

## Outputs
```json
{
  "summary": "string",
  "metrics": {},
  "recommendations": ["string"]
}
```

## Runtime Contract
- Aggregates from `leads`, `lead_interactions`, and `lead_scoring_history`
- Must produce reproducible metrics from persisted data only
- Must not mutate lead state

## Core Metrics
- Conversion by source
- Average score by status
- Average lead age by status
- Duplicate candidate rate
- Interaction volume by agent

## Failure Modes
- Empty dataset
- Invalid date range
- Missing schema after migration

## Limits
- Read only
- Target runtime under 30s for the first 100k leads

## Example Command
```bash
kortix agent run lead-analyzer --input '{"analysis_type":"report","date_range":"7d","group_by":"source"}'
```
