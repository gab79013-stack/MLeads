#!/usr/bin/env bash
set -euo pipefail

VERIFY_DIR="/workspace/reports/phase2"
VERIFY_DB="$VERIFY_DIR/phase2_verify.db"
SHARED_CONTEXT="$VERIFY_DIR/shared-context.json"

mkdir -p "$VERIFY_DIR" /workspace/.kortix/memory
rm -f "$VERIFY_DB"

python3 /workspace/scripts/migrate_mleads_sqlite.py \
  --target "$VERIFY_DB" \
  --schema /workspace/scripts/init_mleads_schema.sql \
  --backup-dir /workspace/backups/phase2 \
  --report "$VERIFY_DIR/migration-report.json"

python3 /workspace/scripts/seed_phase2_demo.py --db "$VERIFY_DB"
/workspace/scripts/register_skills.sh
python3 /workspace/scripts/build_shared_context.py --db "$VERIFY_DB" --output "$SHARED_CONTEXT"
python3 /workspace/scripts/build_shared_context.py --db /workspace/.kortix/kortix.db --output /workspace/.kortix/memory/shared-context.json

python3 /workspace/scripts/score_lead.py \
  --db "$VERIFY_DB" \
  --context "$SHARED_CONTEXT" \
  --lead-id lead_demo_001 \
  --mode heuristic \
  --json-out "$VERIFY_DIR/score-lead-demo.json"

python3 /workspace/scripts/autowork/verify_leads.py \
  --db "$VERIFY_DB" \
  --context "$SHARED_CONTEXT" \
  --batch-size 10 \
  --concurrency 2 \
  --mode heuristic \
  --json-out "$VERIFY_DIR/autowork-results.json"

python3 /workspace/scripts/healthcheck_phase2.py \
  --shared-context "$SHARED_CONTEXT" \
  --json-out "$VERIFY_DIR/phase2-healthcheck.json"
