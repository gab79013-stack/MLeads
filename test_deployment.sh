#!/bin/bash
# Local testing script to verify MLeads implementation

set -e

PROJECT_DIR="/home/user/MLeads"
cd "$PROJECT_DIR"

echo "=========================================="
echo "MLeads Implementation Test Suite"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASS=0
FAIL=0

# Test function
test_file() {
    local file=$1
    local description=$2
    
    if [ -f "$file" ]; then
        echo -e "${GREEN}✓${NC} $description"
        ((PASS++))
        return 0
    else
        echo -e "${RED}✗${NC} $description (FILE NOT FOUND: $file)"
        ((FAIL++))
        return 1
    fi
}

# Test content function
test_content() {
    local file=$1
    local pattern=$2
    local description=$3
    
    if [ ! -f "$file" ]; then
        echo -e "${RED}✗${NC} $description (FILE NOT FOUND: $file)"
        ((FAIL++))
        return 1
    fi
    
    if grep -q "$pattern" "$file"; then
        echo -e "${GREEN}✓${NC} $description"
        ((PASS++))
        return 0
    else
        echo -e "${RED}✗${NC} $description (PATTERN NOT FOUND)"
        ((FAIL++))
        return 1
    fi
}

# File existence tests
echo -e "${BLUE}━ File Structure Tests${NC}"
test_file "web/app.py" "Flask app exists"
test_file "web_server.py" "Web server entry point exists"
test_file "web/templates/index.html" "Dashboard HTML exists"
test_file "workers/inspection_scheduler.py" "Inspection scheduler exists"
test_file "utils/inspection_calendar_fetchers.py" "Calendar fetchers exist"
test_file "utils/web_db.py" "Database utilities exist"
test_file "agents/construction_agent.py" "Construction agent exists"
test_file "requirements.txt" "Python requirements exist"
echo ""

# Content validation tests
echo -e "${BLUE}━ Content Validation Tests${NC}"
test_content "web/app.py" "create_app" "Flask app factory function"
test_content "web/app.py" "start_inspection_scheduler" "Scheduler initialization in app"
test_content "web/templates/index.html" "MLeads" "Dashboard title is MLeads"
test_content "web/templates/index.html" "Salesforce" "Salesforce-style UI elements"
test_content "workers/inspection_scheduler.py" "BackgroundScheduler" "APScheduler integration"
test_content "utils/inspection_calendar_fetchers.py" "ContraCostaFetcher" "Contra Costa fetcher"
test_content "utils/inspection_calendar_fetchers.py" "BerkeleyFetcher" "Berkeley fetcher"
test_content "utils/inspection_calendar_fetchers.py" "SanJoseFetcher" "San Jose fetcher"
echo ""

# Git tests
echo -e "${BLUE}━ Git Status Tests${NC}"
CURRENT_BRANCH=$(git branch --show-current)
if [ "$CURRENT_BRANCH" == "claude/check-lead-calendar-integration-K0AOx" ]; then
    echo -e "${GREEN}✓${NC} On correct deployment branch"
    ((PASS++))
else
    echo -e "${RED}✗${NC} On wrong branch (current: $CURRENT_BRANCH)"
    ((FAIL++))
fi

if git status | grep -q "nothing to commit"; then
    echo -e "${GREEN}✓${NC} All changes committed"
    ((PASS++))
else
    echo -e "${YELLOW}⚠${NC} Uncommitted changes exist"
    git status --short
fi

if git log --oneline -1 | grep -q "Salesforce\|dashboard\|inspection"; then
    echo -e "${GREEN}✓${NC} Recent commits include feature work"
    ((PASS++))
    echo "   Latest: $(git log --oneline -1)"
else
    echo -e "${YELLOW}⚠${NC} Recent commits may not include latest work"
fi
echo ""

# Python syntax tests
echo -e "${BLUE}━ Python Syntax Tests${NC}"
python_files=(
    "web/app.py"
    "web_server.py"
    "workers/inspection_scheduler.py"
    "utils/inspection_calendar_fetchers.py"
)

for file in "${python_files[@]}"; do
    if [ -f "$file" ]; then
        if python3 -m py_compile "$file" 2>/dev/null; then
            echo -e "${GREEN}✓${NC} $file (syntax OK)"
            ((PASS++))
        else
            echo -e "${RED}✗${NC} $file (syntax error)"
            ((FAIL++))
        fi
    fi
done
echo ""

# Summary
echo "=========================================="
if [ $FAIL -eq 0 ]; then
    echo -e "${GREEN}✅ ALL TESTS PASSED (${PASS}/${PASS})${NC}"
    echo "=========================================="
    echo ""
    echo "Implementation Status: READY FOR DEPLOYMENT"
    echo ""
    echo "Next steps:"
    echo "1. Copy deploy.sh to droplet:"
    echo "   scp deploy.sh root@159.223.199.152:/home/mleads/MLeads/"
    echo ""
    echo "2. On droplet, run:"
    echo "   cd /home/mleads/MLeads"
    echo "   bash deploy.sh"
    echo ""
    exit 0
else
    echo -e "${RED}❌ TESTS FAILED (${FAIL} failures, ${PASS} passes)${NC}"
    echo "=========================================="
    exit 1
fi
