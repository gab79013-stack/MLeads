# MLeads Dashboard - Deployment Guide

## Status

✅ **Implementation Complete**
- Salesforce-style dashboard UI with collapsible sidebar
- Inspection calendar integration (Contra Costa, Berkeley, San Jose)
- Multi-user authentication system
- Lead management and scoring
- Automated inspection scheduler

---

## Droplet Deployment (Quick Start)

### On Your Droplet (as root):

```bash
# 1. Navigate to project directory
cd /home/mleads/MLeads

# 2. Fetch latest code from git
git fetch origin claude/check-lead-calendar-integration-K0AOx
git checkout claude/check-lead-calendar-integration-K0AOx
git pull origin claude/check-lead-calendar-integration-K0AOx

# 3. Run automated deployment
bash deploy.sh
```

The `deploy.sh` script will:
- ✓ Verify all required files exist
- ✓ Install/update Python dependencies
- ✓ Stop the old web service
- ✓ Kill lingering processes
- ✓ Start the new service
- ✓ Verify the service is listening
- ✓ Test the dashboard endpoint

---

## Manual Deployment (Step by Step)

### Prerequisites
```bash
# Ensure you have Python 3 and pip installed
python3 --version
pip3 --version

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Database Setup
```bash
# Initialize database (if not exists)
python3 -c "
from utils.web_db import init_web_db, seed_cities_and_agents
init_web_db()
seed_cities_and_agents()
print('✓ Database initialized')
"
```

### Start the Web Server
```bash
# Option 1: Development server (for testing)
python3 web_server.py

# Option 2: Production with gunicorn (recommended)
gunicorn --workers 4 --bind 0.0.0.0:5001 --timeout 30 web_server:app
```

### Using systemd Service (Recommended)
```bash
# Service file should already exist at:
# /etc/systemd/system/mleads-web.service

# Start service
sudo systemctl start mleads-web
sudo systemctl enable mleads-web  # Auto-start on reboot

# Check status
sudo systemctl status mleads-web

# View logs
sudo journalctl -u mleads-web -f  # Follow logs in real-time
```

---

## Verification Checklist

### 1. Service Status
```bash
sudo systemctl status mleads-web
# Should show: ● mleads-web.service - MLeads Web Server
#              Loaded: loaded
#              Active: active (running)
```

### 2. Port Binding
```bash
sudo netstat -tuln | grep -E ":5001|:5000"
# Should show: tcp  0  0  0.0.0.0:5001  0.0.0.0:*  LISTEN
```

### 3. Dashboard Access
```bash
# From droplet
curl http://localhost:5001/

# Should return HTML containing: "MLeads Dashboard"
```

### 4. API Endpoints
```bash
# Get scheduler status
curl http://localhost:5001/api/scheduler/status

# Response should include:
# {
#   "running": true,
#   "jobs": [
#     {"id": "fetch_inspections_daily", "name": "Daily inspection fetch", ...},
#     {"id": "cleanup_inspections_weekly", "name": "Weekly cleanup", ...}
#   ]
# }
```

---

## Features Implemented

### Dashboard Features
- ✅ Salesforce-style UI with collapsible sidebar
- ✅ Multi-view navigation (Dashboard, Leads, Users, Inspections, Settings)
- ✅ Lead filtering and search
- ✅ Real-time lead scoring with visual indicators
- ✅ Contact information display
- ✅ Audit log tracking

### Inspection Calendar Integration
- ✅ **Contra Costa County** - PDF + Accela API
- ✅ **Berkeley** - PDF + Accela Citizen Access
- ✅ **San Jose** - Open Data CKAN API
- ✅ Automatic daily fetching (9:00 AM)
- ✅ Weekly cleanup of old records (Monday 2:00 AM)
- ✅ Fallback prediction model for unsupported jurisdictions

### Authentication & Authorization
- ✅ JWT token-based authentication
- ✅ User role management (admin, agent, supervisor)
- ✅ City and agent assignments per user
- ✅ Token refresh mechanism
- ✅ Audit trail logging

### Database
- ✅ SQLite database with proper schema
- ✅ `consolidated_leads` table with full lead information
- ✅ `scheduled_inspections` table for calendar data
- ✅ User authentication and role management
- ✅ Audit log table for tracking activity

---

## Troubleshooting

### Issue: Service fails to start
```bash
# Check logs
sudo journalctl -u mleads-web -n 50 --no-pager

# Common causes:
# - ModuleNotFoundError: Missing web/app.py (should be fixed by git pull)
# - Port already in use: Check what's using 5000/5001
#   sudo netstat -tuln | grep LISTEN
#   sudo lsof -i :5001
# - Database locked: Check if another process is using the DB
```

### Issue: Dashboard shows old UI
```bash
# Clear browser cache and hard refresh (Ctrl+Shift+R or Cmd+Shift+R)
# Or open in private/incognito window

# Verify correct file is being served:
curl http://localhost:5001/ | grep -o "MLeads Dashboard\|Insurleads Dashboard"
# Should show: MLeads Dashboard
```

### Issue: Inspection calendar not updating
```bash
# Check scheduler status
curl http://localhost:5001/api/scheduler/status

# Manually trigger fetch
curl -X POST http://localhost:5001/api/inspections/fetch_now

# Check logs for fetcher errors
sudo journalctl -u mleads-web | grep -i "fetch\|inspection"
```

### Issue: Port already in use
```bash
# Find what's using the port
sudo lsof -i :5001

# Kill any old processes
sudo pkill -f "gunicorn.*mleads"
sudo pkill -f "python.*web_server"

# Restart service
sudo systemctl restart mleads-web
```

---

## Environment Variables

Optional environment variables in `.env` file:

```bash
# Web server configuration
PORT=5001                          # Port to run on (default: 5001)
FLASK_DEBUG=false                  # Debug mode (default: false)
FLASK_ENV=production               # Environment (development/production)

# Database
DATABASE_PATH=/home/mleads/mleads.db   # SQLite database location

# Inspection calendar
INSPECTION_FETCH_HOUR=9            # Hour to fetch inspections (0-23)
INSPECTION_CLEANUP_DAYS=60         # Delete inspections older than N days

# Authentication
SECRET_KEY=your-secret-key         # Flask secret key (required in production)
JWT_EXPIRATION_HOURS=24            # JWT token expiration
```

---

## Performance Tuning

### For Production Deployment
```bash
# Use more workers based on CPU cores
gunicorn --workers 4 --worker-class sync --bind 0.0.0.0:5001 web_server:app

# Or with async workers
pip install gunicorn[gevent]
gunicorn --workers 4 --worker-class gevent --bind 0.0.0.0:5001 web_server:app

# Monitor resource usage
watch -n 1 "ps aux | grep gunicorn | grep -v grep"
```

### Database Optimization
```bash
# Run VACUUM to optimize database
sqlite3 /home/mleads/mleads.db VACUUM

# Create indexes for faster queries
sqlite3 /home/mleads/mleads.db << EOF
CREATE INDEX IF NOT EXISTS idx_leads_address ON consolidated_leads(address);
CREATE INDEX IF NOT EXISTS idx_leads_city ON consolidated_leads(city);
CREATE INDEX IF NOT EXISTS idx_inspections_date ON scheduled_inspections(inspection_date);
EOF
```

---

## Monitoring

### Real-time Log Monitoring
```bash
# Follow service logs
sudo journalctl -u mleads-web -f

# Filter by error level
sudo journalctl -u mleads-web -p err -f

# Show logs from last hour
sudo journalctl -u mleads-web --since "1 hour ago"
```

### Health Check Script
```bash
#!/bin/bash
echo "MLeads Service Health Check"
echo "============================="

# Check service status
if systemctl is-active --quiet mleads-web; then
    echo "✓ Service is running"
else
    echo "✗ Service is NOT running"
    exit 1
fi

# Check port
if netstat -tuln | grep -q ":5001"; then
    echo "✓ Port 5001 is listening"
else
    echo "✗ Port 5001 is NOT listening"
    exit 1
fi

# Check API response
if curl -s http://localhost:5001/ | grep -q "MLeads"; then
    echo "✓ Dashboard is responding"
else
    echo "✗ Dashboard is NOT responding"
    exit 1
fi

echo "All checks passed!"
```

---

## Rollback

If something breaks, you can rollback to the previous version:

```bash
# Check git history
git log --oneline -5

# Checkout previous commit
git checkout <previous-commit-sha>

# Restart service
sudo systemctl restart mleads-web

# Or switch to main branch
git checkout main
git pull origin main
sudo systemctl restart mleads-web
```

---

## Support

For issues or questions:
1. Check the logs: `sudo journalctl -u mleads-web`
2. Verify files exist: `ls -la web/app.py web_server.py`
3. Test database: `sqlite3 mleads.db ".tables"`
4. Verify branch: `git branch --show-current`

---

**Last Updated:** 2026-04-06
**Branch:** claude/check-lead-calendar-integration-K0AOx
**Status:** Ready for Production
