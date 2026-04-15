#!/usr/bin/env bash
set -euo pipefail

VERIFY_DIR="/workspace/reports/phase5"
VERIFY_DB="$VERIFY_DIR/phase5_verify.db"
mkdir -p "$VERIFY_DIR" /workspace/backups/phase5
rm -f "$VERIFY_DB"

python3 /workspace/scripts/migrate_mleads_sqlite.py --target "$VERIFY_DB" --schema /workspace/scripts/init_mleads_schema.sql --backup-dir /workspace/backups/phase5 --report "$VERIFY_DIR/base-migration.json"
python3 /workspace/scripts/init_phase3_db.py --db "$VERIFY_DB" --seed-demo-user --seed-demo-subscription --report "$VERIFY_DIR/phase3-init.json"
python3 /workspace/scripts/init_phase4_db.py --db "$VERIFY_DB" --report "$VERIFY_DIR/phase4-init.json"

PORT=$(python3 - <<'PY'
import random
print(random.randint(10000, 59999))
PY
)

python3 /workspace/dashboard/server.py --db "$VERIFY_DB" --port "$PORT" > "$VERIFY_DIR/dashboard.log" 2>&1 &
SERVER_PID=$!
cleanup() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sleep 2

python3 /workspace/scripts/test_phase5_load.py --base-url "http://127.0.0.1:$PORT" --email admin@kortix.local --password ChangeMe123! --total 120 --concurrency 12 --json-out "$VERIFY_DIR/load-test.json"
python3 /workspace/scripts/reconcile_dedup.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/dedup-report.json"
python3 /workspace/scripts/test_phase5_integrity.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/integrity-report.json"
python3 /workspace/scripts/healthcheck_phase4.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/phase5-healthcheck.json"

curl -s -X POST "http://127.0.0.1:$PORT/api/login" -H "Content-Type: application/json" -d '{"email":"admin@kortix.local","password":"ChangeMe123!"}' > "$VERIFY_DIR/login.json"
TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('/workspace/reports/phase5/login.json').read_text())['token'])
PY
)

curl -s "http://127.0.0.1:$PORT/api/stats" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/stats.json"
curl -s "http://127.0.0.1:$PORT/api/audit-logs" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/audit-logs.json"

PRE_DIR=$(/workspace/scripts/pre_deploy_backup.sh "$VERIFY_DB" "/workspace/backups/phase5/predeploy")
printf '%s\n' "$PRE_DIR" > "$VERIFY_DIR/predeploy-backup-path.txt"
sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM leads;" > "$VERIFY_DIR/lead-count.txt"
