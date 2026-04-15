#!/usr/bin/env bash
set -euo pipefail

mkdir -p /workspace/backups/phase1 /workspace/reports

python3 /workspace/scripts/migrate_mleads_sqlite.py \
  --target /workspace/.kortix/kortix.db \
  --schema /workspace/scripts/init_mleads_schema.sql \
  --backup-dir /workspace/backups/phase1 \
  --report /workspace/reports/migration-report.json

/workspace/scripts/register_agents.sh

python3 /workspace/scripts/healthcheck_phase1.py \
  --db /workspace/.kortix/kortix.db \
  --env-file /workspace/.env.example \
  --source-agents /workspace/agents \
  --runtime-agents /workspace/.opencode/agents \
  --json-out /workspace/reports/phase1-healthcheck.json
