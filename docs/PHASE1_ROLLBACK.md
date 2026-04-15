# Fase 1 — Rollback

## Rollback de base de datos
```bash
ls -1 /workspace/backups/phase1
sqlite3 /workspace/.kortix/kortix.db ".restore '/workspace/backups/phase1/<archivo>.db'"
sqlite3 /workspace/.kortix/kortix.db "PRAGMA integrity_check;"
```

## Rollback de agentes
```bash
rm -rf /workspace/.opencode/agents/lead-qualifier \
       /workspace/.opencode/agents/lead-nurturer \
       /workspace/.opencode/agents/lead-analyzer
/workspace/scripts/register_agents.sh
```

## Rollback de configuración
```bash
rm -f /workspace/.env
cp /workspace/.env.example /workspace/.env
chmod 600 /workspace/.env
```

## Validación posterior
```bash
/workspace/scripts/verify_phase1.sh
```
