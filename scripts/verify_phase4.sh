#!/usr/bin/env bash
set -euo pipefail

VERIFY_DIR="/workspace/reports/phase4"
VERIFY_DB="$VERIFY_DIR/phase4_verify.db"
mkdir -p "$VERIFY_DIR" /workspace/backups/phase4
rm -f "$VERIFY_DB"

python3 /workspace/scripts/migrate_mleads_sqlite.py --target "$VERIFY_DB" --schema /workspace/scripts/init_mleads_schema.sql --backup-dir /workspace/backups/phase4 --report "$VERIFY_DIR/base-migration.json"
python3 /workspace/scripts/init_phase3_db.py --db "$VERIFY_DB" --seed-demo-user --seed-demo-subscription --report "$VERIFY_DIR/phase3-init.json"
python3 /workspace/scripts/init_phase4_db.py --db "$VERIFY_DB" --report "$VERIFY_DIR/phase4-init.json"
python3 /workspace/scripts/seed_phase4_demo.py --db "$VERIFY_DB"
python3 /workspace/scripts/reconcile_dedup.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/dedup-report.json"
python3 /workspace/scripts/build_shared_context.py --db "$VERIFY_DB" --output "$VERIFY_DIR/shared-context.json"

python3 /workspace/scripts/score_lead.py --db "$VERIFY_DB" --context "$VERIFY_DIR/shared-context.json" --lead-id lead_phase4_001 --mode heuristic --actor verifier-a --idempotency-key phase4-score-001 --json-out "$VERIFY_DIR/score-first.json"
python3 /workspace/scripts/score_lead.py --db "$VERIFY_DB" --context "$VERIFY_DIR/shared-context.json" --lead-id lead_phase4_001 --mode heuristic --actor verifier-a --idempotency-key phase4-score-001 --json-out "$VERIFY_DIR/score-replay.json"

python3 /workspace/scripts/healthcheck_phase4.py --db "$VERIFY_DB" --json-out "$VERIFY_DIR/phase4-healthcheck.json"

PORTS=$(python3 - <<'PY'
import random
ports = random.sample(range(10000, 59999), 2)
print(ports[0], ports[1])
PY
)
DASHBOARD_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f1)
WEBHOOK_PORT=$(printf '%s' "$PORTS" | cut -d' ' -f2)
export STRIPE_WEBHOOK_SECRET="whsec_phase4_test"

python3 /workspace/dashboard/server.py --db "$VERIFY_DB" --port "$DASHBOARD_PORT" > "$VERIFY_DIR/dashboard.log" 2>&1 &
DASHBOARD_PID=$!
python3 /workspace/scripts/stripe_webhook.py --db "$VERIFY_DB" --port "$WEBHOOK_PORT" --webhook-secret "$STRIPE_WEBHOOK_SECRET" > "$VERIFY_DIR/stripe.log" 2>&1 &
WEBHOOK_PID=$!

cleanup() {
  kill "$DASHBOARD_PID" "$WEBHOOK_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT
sleep 2

curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/login" -H "Content-Type: application/json" -d '{"email":"admin@kortix.local","password":"ChangeMe123!"}' > "$VERIFY_DIR/login-admin.json"
ADMIN_TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('/workspace/reports/phase4/login-admin.json').read_text())['token'])
PY
)

curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/login" -H "Content-Type: application/json" -d '{"email":"ops@kortix.local","password":"OpsPass123!"}' > "$VERIFY_DIR/login-ops.json"
OPS_TOKEN=$(python3 - <<'PY'
import json
from pathlib import Path
print(json.loads(Path('/workspace/reports/phase4/login-ops.json').read_text())['token'])
PY
)

curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/audit-logs" -H "Authorization: Bearer $ADMIN_TOKEN" > "$VERIFY_DIR/audit-logs.json"
curl -s "http://127.0.0.1:$DASHBOARD_PORT/api/dedup-report" -H "Authorization: Bearer $OPS_TOKEN" > "$VERIFY_DIR/dedup-api.json"
curl -s -X POST "http://127.0.0.1:$DASHBOARD_PORT/api/triggers/evaluate" -H "Authorization: Bearer $OPS_TOKEN" > "$VERIFY_DIR/trigger-evaluate-api.json"

cat > "$VERIFY_DIR/stripe-event.json" <<'EOF'
{
  "id": "evt_phase4_invoice_paid_001",
  "type": "invoice.paid",
  "data": {
    "object": {
      "id": "in_phase4_001",
      "customer": "cus_local_admin",
      "payment_intent": "pi_phase4_001",
      "amount_paid": 49900,
      "currency": "usd"
    }
  }
}
EOF

SIGNATURE=$(python3 - <<'PY'
import hashlib, hmac, time
from pathlib import Path
payload = Path('/workspace/reports/phase4/stripe-event.json').read_bytes()
secret = 'whsec_phase4_test'
timestamp = str(int(time.time()))
sig = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
print(f"t={timestamp},v1={sig}")
PY
)

curl -s -X POST "http://127.0.0.1:$WEBHOOK_PORT/stripe/webhook" -H "Content-Type: application/json" -H "Stripe-Signature: $SIGNATURE" --data-binary "@$VERIFY_DIR/stripe-event.json" > "$VERIFY_DIR/stripe-response-first.json"
curl -s -X POST "http://127.0.0.1:$WEBHOOK_PORT/stripe/webhook" -H "Content-Type: application/json" -H "Stripe-Signature: $SIGNATURE" --data-binary "@$VERIFY_DIR/stripe-event.json" > "$VERIFY_DIR/stripe-response-second.json"

sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM lead_scoring_history WHERE lead_id = 'lead_phase4_001';" > "$VERIFY_DIR/scoring-history-count.txt"
sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM idempotency_keys WHERE scope = 'score_lead' AND idempotency_key = 'phase4-score-001';" > "$VERIFY_DIR/score-idempotency-count.txt"
sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM webhook_events WHERE provider = 'stripe' AND event_id = 'evt_phase4_invoice_paid_001';" > "$VERIFY_DIR/webhook-event-count.txt"
sqlite3 "$VERIFY_DB" "SELECT COUNT(*) FROM payments WHERE stripe_payment_id = 'pi_phase4_001';" > "$VERIFY_DIR/payment-count.txt"
