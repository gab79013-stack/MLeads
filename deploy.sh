#!/bin/bash
# Deployment script for MLeads droplet
# Usage: bash deploy.sh

set -e

PROJECT_DIR="/home/mleads/MLeads"
BRANCH="claude/check-lead-calendar-integration-K0AOx"
SERVICE_NAME="mleads-web"

echo "=========================================="
echo "MLeads Deployment Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}❌ This script must be run as root${NC}"
    exit 1
fi

echo -e "${YELLOW}📍 Step 1: Navigate to project directory${NC}"
cd "$PROJECT_DIR"
echo "✓ Working directory: $(pwd)"
echo ""

echo -e "${YELLOW}📍 Step 2: Fetch latest changes from Git${NC}"
git fetch origin "$BRANCH"
echo "✓ Fetched from origin"
echo ""

echo -e "${YELLOW}📍 Step 3: Switch to deployment branch${NC}"
git checkout "$BRANCH"
git pull origin "$BRANCH"
echo "✓ On branch: $(git branch --show-current)"
echo "✓ Latest commit: $(git log --oneline -1)"
echo ""

echo -e "${YELLOW}📍 Step 4: Verify key files exist${NC}"
files=(
    "web/app.py"
    "web_server.py"
    "workers/inspection_scheduler.py"
    "web/templates/index.html"
    "web/templates/home.html"
    "web/templates/colaboradores.html"
    "utils/web_db.py"
    "agents/construction_agent.py"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        echo "✓ $file"
    else
        echo -e "${RED}✗ MISSING: $file${NC}"
        exit 1
    fi
done
echo ""

echo -e "${YELLOW}📍 Step 5: Install/update Python dependencies${NC}"
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo "✓ Virtual environment activated"
else
    echo "⚠ Virtual environment not found, installing..."
    python3 -m venv venv
    source venv/bin/activate
fi

pip install --upgrade pip > /dev/null 2>&1
pip install -q -r requirements.txt 2>/dev/null || echo "⚠ requirements.txt not found, skipping"
echo "✓ Dependencies checked"
echo ""

echo -e "${YELLOW}📍 Step 6: Stop current web service${NC}"
if systemctl is-active --quiet $SERVICE_NAME; then
    systemctl stop $SERVICE_NAME
    sleep 2
    echo "✓ Service stopped"
else
    echo "⚠ Service not running"
fi
echo ""

echo -e "${YELLOW}📍 Step 7: Kill any lingering Python/gunicorn processes${NC}"
pkill -f "gunicorn.*mleads" || true
pkill -f "python.*web_server" || true
sleep 2
echo "✓ Old processes cleaned up"
echo ""

echo -e "${YELLOW}📍 Step 8: Start web service${NC}"
systemctl start $SERVICE_NAME
sleep 3

if systemctl is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}✓ Service started successfully${NC}"
else
    echo -e "${RED}✗ Service failed to start${NC}"
    echo ""
    echo "Error log:"
    journalctl -u $SERVICE_NAME -n 20 --no-pager
    exit 1
fi
echo ""

echo -e "${YELLOW}📍 Step 9: Verify service is listening${NC}"
sleep 2
if netstat -tuln 2>/dev/null | grep -q ":5001\|:5000"; then
    PORT=$(netstat -tuln | grep -E ":(5001|5000)" | awk '{print $4}' | cut -d: -f2 | head -1)
    echo -e "${GREEN}✓ Service listening on port $PORT${NC}"
else
    echo -e "${YELLOW}⚠ Port not immediately visible, checking process...${NC}"
    ps aux | grep gunicorn | grep -v grep || echo "⚠ No gunicorn process found"
fi
echo ""

echo -e "${YELLOW}📍 Step 10: Test API endpoint${NC}"
sleep 2
RESPONSE=$(curl -s http://localhost:5001/ 2>/dev/null | head -c 200)
if echo "$RESPONSE" | grep -q "0brix\|Leads premium para contratistas"; then
    echo -e "${GREEN}✓ Public homepage is responding${NC}"
    echo "Response snippet: ${RESPONSE:0:50}..."
else
    echo -e "${YELLOW}⚠ Homepage not responding to localhost test${NC}"
    echo "Response: $RESPONSE"
fi
echo ""

echo "=========================================="
echo -e "${GREEN}✅ Deployment completed!${NC}"
echo "=========================================="
echo ""
echo "Dashboard URL:"
echo "  http://$(hostname -I | awk '{print $1}'):5001"
echo ""
echo "Service status:"
systemctl status $SERVICE_NAME --no-pager | head -5
echo ""
echo "Recent logs:"
journalctl -u $SERVICE_NAME -n 5 --no-pager
