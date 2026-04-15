#!/usr/bin/env bash
set -euo pipefail

VERIFY_DIR="/workspace/reports/phase3"
VERIFY_DB="$VERIFY_DIR/phase3_verify.db"
mkdir -p "$VERIFY_DIR" /workspace/backups/phase3
rm -f "$VERIFY_DB"

python3 /workspace/scripts/migrate_mleads_sqlite.py \
  --target "$VERIFY_DB" \
  --schema /workspace/scripts/init_mleads_schema.sql \
  --backup-dir /workspace/backups/phase3 \
  --report "$VERIFY_DIR/phase3-base-migration.json"

python3 /workspace/scripts/init_phase3_db.py \
  --db "$VERIFY_DB" \
  --seed-demo-user \
  --seed-demo-subscription \
  --report "$VERIFY_DIR/phase3-db-init.json"

python3 /workspace/scripts/seed_phase3_demo.py --db "$VERIFY_DB"
python3 /workspace/scripts/evaluate_triggers.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/trigger-evaluation.json"
python3 /workspace/scripts/healthcheck_phase3.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/phase3-healthcheck.json"

PORTS=$(python3 - <<'PY'
import random
ports = random.sample(range(10000, 59999), 2)
print(ports[0], ports[1])
PY
)
DASHBOARD_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f1)
WEBHOOK_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f2)

export STRIPE_WEBHOOK_SECRET="whsec_phase3_test"

python3 /workspace/dashboard/server.py --db "$VERIFY_DB" --port "$DASHBOARD_PORT" > "$VERIFY_DIR/dashboard.log" 2>&1 &
DASHBOARD_PID=$!
python3 /workspace/scripts/stripe_webhook.py --db "$VERIFY_DB" --port "$WEBHOOK_PORT" --webhook-secret "$STRIPE_WEBHOOK_SECRET" > "$VERIFY_DIR/stripe-webhook.log" 2>&1 &
WEBHOOK_PID=$!

cleanup() {
  kill "$DASHBOARD_PID" "$WEBHOOK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

sleep 2

curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/login" \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@kortix.local","password":"ChangeMe123!"}' > "$VERIFY_DIR/login.json"

TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path('/workspace/reports/phase3/login.json').read_text())
print(data['token'])
PY
)

curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/stats" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/stats.json"
curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/leads?limit=10" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/leads.json"
curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/notifications" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/notifications.json"
curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/subscription" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/subscription.json"
curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/triggers/evaluate" -H "Authorization: Bearer $TOKEN" > "$VERIFY_DIR/triggers-api.json"

cat > "$VERIFY_DIR/stripe-event.json" <<'EOF'
{
  "id": "evt_phase3_checkout_001",
  "type": "checkout.session.completed",
  "data": {
    "object": {
      "id": "cs_test_phase3",
      "customer": "cus_local_admin",
      "subscription": "sub_user_local_admin",
      "client_reference_id": "user_local_admin",
      "metadata": {
        "plan": "pro"
      }
    }
  }
}
EOF

SIGNATURE=$(python3 - <<'PY'
import hashlib, hmac, time
from pathlib import Path
payload = Path('/workspace/reports/phase3/stripe-event.json').read_bytes()
secret = 'whsec_phase3_test'
timestamp = str(int(time.time()))
sig = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
print(f"t={timestamp},v1={sig}")
PY
)

curl -s -X POST "http://127.0.0.1:$WEBHOOK_PORT/stripe/webhook" \
  -H "Content-Type: application/json" \
  -H "Stripe-Signature: $SIGNATURE" \
  --data-binary "@$VERIFY_DIR/stripe-event.json" > "$VERIFY_DIR/stripe-response.json"

sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM notifications;" > "$VERIFY_DIR/notification-count.txt"
sqlite3 "$VERIFY_DB" "SELECT plan, status, leads_limit FROM subscriptions WHERE id = 'sub_user_local_admin';" > "$VERIFY_DIR/subscription-after-webhook.txt"
