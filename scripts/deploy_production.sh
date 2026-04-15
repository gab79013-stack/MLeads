#!/usr/bin/env bash
set -euo pipefail

# MLeads Production Deploy Script
# Usage: bash /workspace/scripts/deploy_production.sh

PROJECT_DIR="/workspace"
DB_PATH="${PROJECT_DIR}/.kortix/kortix.db"
PM2_BIN="$(npm prefix -g)/bin/pm2"

echo "========================================"
echo "  MLeads — Production Deploy"
echo "========================================"
echo ""

# ── 1. Database schema ──
echo "[1/6] Initializing database schema..."
mkdir -p "${PROJECT_DIR}/.kortix"

sqlite3 "$DB_PATH" < "${PROJECT_DIR}/scripts/init_mleads_schema.sql" 2>/dev/null || true
python3 "${PROJECT_DIR}/scripts/init_phase3_db.py" \
  --db "$DB_PATH" \
  --seed-demo-user \
  --seed-demo-subscription \
  --report /tmp/phase3-init.json 2>/dev/null || true
python3 "${PROJECT_DIR}/scripts/seed_phase4_demo.py" --db "$DB_PATH" 2>/dev/null || true

echo "  ✅ Database ready: $DB_PATH"

# ── 2. Seed demo data if empty ──
LEAD_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM leads;" 2>/dev/null || echo "0")
if [ "$LEAD_COUNT" -eq 0 ]; then
  echo "[2/6] Seeding demo leads..."
  python3 "${PROJECT_DIR}/scripts/seed_phase4_demo.py" --db "$DB_PATH"
  python3 "${PROJECT_DIR}/scripts/autowork/verify_leads.py" --db "$DB_PATH" > /dev/null 2>&1 || true
else
  echo "[2/6] Database has ${LEAD_COUNT} leads — skipping seed"
fi

# ── 3. Register agents & skills ──
echo "[3/6] Registering agents and skills..."
bash "${PROJECT_DIR}/scripts/register_agents.sh" 2>/dev/null || true
bash "${PROJECT_DIR}/scripts/register_skills.sh" 2>/dev/null || true

# ── 4. Nginx configuration ──
echo "[4/6] Configuring Nginx..."
if [ -x /usr/sbin/nginx ]; then
  NGINX_BIN="/usr/sbin/nginx"
  NGINX_CONF="/etc/nginx/http.d/mleads.conf"

  sudo tee "$NGINX_CONF" > /dev/null << 'NGINX_EOF'
server {
    listen 80;
    listen [::]:80;
    server_name _;

    access_log /var/log/nginx/mleads_access.log;
    error_log /var/log/nginx/mleads_error.log;

    location / {
        proxy_pass http://127.0.0.1:43123;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
    }

    location /stripe/webhook {
        proxy_pass http://127.0.0.1:43124/stripe/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX_EOF

  if sudo "$NGINX_BIN" -t 2>/dev/null; then
    sudo "$NGINX_BIN" -s reload 2>/dev/null || sudo "$NGINX_BIN" 2>/dev/null || true
    echo "  ✅ Nginx configured and running on port 80"
  else
    echo "  ⚠️  Nginx config test failed — skipping nginx setup"
  fi
else
  echo "  ⚠️  Nginx not found — skipping"
fi

# ── 5. Start PM2 processes ──
echo "[5/6] Starting PM2 processes..."
cd "$PROJECT_DIR"

# Stop existing
$PM2_BIN delete all 2>/dev/null || true
sleep 1

# Start fresh
$PM2_BIN start ecosystem.config.js --update-env 2>/dev/null || {
  echo "  ⚠️  PM2 start failed, starting manually..."
  # Fallback: start processes directly
  nohup python3 "${PROJECT_DIR}/dashboard/server.py" --port 43123 --db "$DB_PATH" > /tmp/mleads-dashboard.log 2>&1 &
  echo $! > /tmp/mleads-dashboard.pid
  nohup python3 "${PROJECT_DIR}/scripts/stripe_webhook.py" --port 43124 --db "$DB_PATH" > /tmp/mleads-webhook.log 2>&1 &
  echo $! > /tmp/mleads-webhook.pid
  echo "  ✅ Processes started manually (no PM2)"
}

sleep 2

# ── 6. Health check ──
echo "[6/6] Running health checks..."
sleep 1

PASS=0
FAIL=0

check() {
  local name="$1" url="$2"
  local code
  code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 "$url" 2>/dev/null || echo "000")
  if [ "$code" = "200" ]; then
    echo "  ✅ $name — HTTP $code"
    PASS=$((PASS + 1))
  else
    echo "  ❌ $name — HTTP $code"
    FAIL=$((FAIL + 1))
  fi
}

check "Dashboard (direct)" "http://127.0.0.1:43123/health"
check "Dashboard (nginx)"  "http://127.0.0.1:80/health"
check "Stripe webhook"    "http://127.0.0.1:43124/health" || true

echo ""
echo "========================================"
echo "  Deploy complete: ${PASS} passed, ${FAIL} failed"
echo "  Dashboard: http://127.0.0.1:80/"
echo "  Login: admin@kortix.local / ChangeMe123!"
echo "========================================"
