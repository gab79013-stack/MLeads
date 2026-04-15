#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-/workspace/reports/production/smoke.db}"
WORK_DIR="/workspace/reports/production/smoke"
mkdir -p "$WORK_DIR"

python3 /workspace/scripts/migrate_mleads_sqlite.py --target "$DB_PATH" --schema /workspace/scripts/init_mleads_schema.sql --backup-dir /workspace/backups/smoke --report "$WORK_DIR/base-migration.json"
python3 /workspace/scripts/init_phase3_db.py --db "$DB_PATH" --seed-demo-user --seed-demo-subscription --report "$WORK_DIR/phase3-init.json"
python3 /workspace/scripts/init_phase4_db.py --db "$DB_PATH" --report "$WORK_DIR/phase4-init.json"

PORTS=$(python3 - <<'PY'
import random
ports = random.sample(range(10000, 59999), 2)
print(ports[0], ports[1])
PY
)
DASHBOARD_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f1)
WEBHOOK_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f2)
export STRIPE_WEBHOOK_SECRET="whsec_smoke_test"

python3 /workspace/dashboard/server.py --db "$DB_PATH" --port "$DASHBOARD_PORT" > "$WORK_DIR/dashboard.log" 2>&1 &
DASHBOARD_PID=$!
python3 /workspace/scripts/stripe_webhook.py --db "$DB_PATH" --port "$WEBHOOK_PORT" --webhook-secret "$STRIPE_WEBHOOK_SECRET" > "$WORK_DIR/stripe.log" 2>&1 &
WEBHOOK_PID=$!

cleanup() {
  kill "$DASHBOARD_PID" "$WEBHOOK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sleep 2

curl -s "http://127.0.0.1:$DASHBOARD_PORT/health" > "$WORK_DIR/dashboard-health.json"
curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/login" -H "Content-Type: application/json" -d '{"email":"admin@kortix.local","password":"ChangeMe123!"}' > "$WORK_DIR/login.json"
TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('/workspace/reports/production/smoke/login.json').read_text())['token'])
PY
)
curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/leads" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "X-Idempotency-Key: smoke-lead-1" -d '{"first_name":"Smoke","last_name":"Tester","email":"smoke@example.com","source":"smoke"}' > "$WORK_DIR/create-lead.json"
curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/leads" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "X-Idempotency-Key: smoke-lead-1" -d '{"first_name":"Smoke","last_name":"Tester","email":"smoke@example.com","source":"smoke"}' > "$WORK_DIR/create-lead-replay.json"
curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/stats" -H "Authorization: Bearer $TOKEN" > "$WORK_DIR/stats.json"

cat > "$WORK_DIR/stripe-event.json" <<'EOF'
{
  "id": "evt_smoke_checkout_001",
  "type": "checkout.session.completed",
  "data": {
    "object": {
      "customer": "cus_local_admin",
      "subscription": "sub_user_local_admin",
      "client_reference_id": "user_local_admin",
      "metadata": { "plan": "pro" }
    }
  }
}
EOF
SIGNATURE=$(python3 - <<'PY'
import hashlib, hmac, time
from pathlib import Path
payload = Path('/workspace/reports/production/smoke/stripe-event.json').read_bytes()
secret = 'whsec_smoke_test'
timestamp = str(int(time.time()))
sig = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
print(f"t={timestamp},v1={sig}")
PY
)
curl -s -X POST "http://127.0.0.1:$WEBHOOK_PORT/stripe/webhook" -H "Content-Type: application/json" -H "Stripe-Signature: $SIGNATURE" --data-binary "@$WORK_DIR/stripe-event.json" > "$WORK_DIR/stripe-response.json"
sqlite3 "$DB_PATH" "SELECT plan, status, leads_limit FROM subscriptions WHERE id = 'sub_user_local_admin';" > "$WORK_DIR/subscription.txt"
