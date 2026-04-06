# MLeads Dashboard - Deployment & Testing Guide

## Quick Start (Local Development)

### 1. Install Dependencies
```bash
pip install -r requirements.txt
python scripts/init_test_user.py
```

### 2. Run Development Server
```bash
python web_server.py
```

The dashboard is now available at `http://localhost:5001/`
- Login page: `http://localhost:5001/login.html`
- Default credentials: `admin` / `admin123`

## Production Deployment on DigitalOcean

### Prerequisites
- Fresh Ubuntu 20.04+ droplet (minimum 1GB RAM, 1 CPU recommended)
- SSH access to the droplet

### Automated Deployment

Run the deployment script from your local machine:

```bash
bash scripts/deploy_to_droplet.sh 159.223.199.152
```

This will:
1. Pull the latest code
2. Install dependencies
3. Restart the application
4. Verify the service is running

### Manual Deployment

SSH into the droplet:
```bash
ssh mleads@<droplet_ip>
```

Then run:
```bash
cd /home/mleads/MLeads
git fetch origin
git checkout claude/check-lead-calendar-integration-K0AOx
git pull origin claude/check-lead-calendar-integration-K0AOx
pip install --upgrade -r requirements.txt
python scripts/init_test_user.py
sudo systemctl restart mleads-web
```

## Testing the Dashboard

### 1. Access the Dashboard
Navigate to: `http://<droplet_ip>/`

### 2. Login
- Click "Login" in the top navigation
- Enter credentials (default: `admin` / `admin123`)
- You should see the main dashboard

### 3. Test Dashboard View
The Dashboard view shows:
- Total leads count
- Contacted leads count
- Recent leads table (top 5)
- Click on any lead to open the side panel

**Expected behavior:**
- Count should match the number of leads in the database
- Table should display recent leads with scores and inspection dates
- Click on a lead row to open side panel

### 4. Test Leads View
Click on "Leads" in the sidebar
- Filter by city (dropdown)
- Filter by agent (dropdown)
- Filter by minimum score (slider)
- Click "Search" to apply filters
- Click "Reset" to clear filters

**Expected behavior:**
- Table should display all leads matching filters
- Each row shows address, city, contact info, score, agent, and next inspection
- Click on any lead to open the side panel with full details

### 5. Test Lead Details (Side Panel)
Click on any lead in the Leads view
- Side panel should open on the right
- Display fields:
  - Address, City, Score, Estimated Value
  - Contractor, Phone, Email
  - Source, Next Inspection, Inspection Source

**Expected behavior:**
- All fields should populate with data from the API
- "Mark as Contacted" button should work
- Close button should close the panel

### 6. Test Users View
Click on "Users" in the sidebar
- Table shows all users with: username, email, full_name, roles, status
- "Add User" button opens modal

**Expected behavior:**
- Table should display all users in the system
- Add User modal should allow creating new users
- Edit button exists

### 7. Test Inspections View
Click on "Inspections" in the sidebar
- Table shows upcoming inspections for your city
- Displays: Address, Jurisdiction, Type, Date, GC Probability

**Expected behavior:**
- Table should populate with inspection calendar data for your city
- If no inspections, show "No inspections for [city]" message
- GC Probability shows as percentage

## Features Checklist - Phase 1 (Current)

- [x] User authentication with JWT tokens
- [x] Dashboard with lead counts and preview
- [x] Leads view with filtering
- [x] Lead detail side panel
- [x] Contact history display
- [x] Notes functionality
- [x] Scheduled inspections calendar view
- [x] Mark leads as contacted
- [x] User management (list/view)
- [ ] User management (edit/delete) - Phase 2
- [ ] Settings page - Phase 2
