# Lead Calendar Integration Documentation

## Overview

The MLeads system now has the capability to complement leads with information from public building inspection calendars and predictive models, allowing visibility into when GCs (General Contractors) will be on-site.

This integration covers **three jurisdictions with public calendar data** (Contra Costa, Berkeley, San Jose) and provides **predictive fallbacks** for all other supported cities.

---

## Data Sources

### ✅ Public Calendar Sources (Primary)

#### Contra Costa County
- **Source**: Daily PDF + Accela API
- **URL**: https://www.contracosta.ca.gov/DocumentCenter/View/25242/InspectionSchedule
- **Update Frequency**: Daily at 8:45 AM
- **Content**: permit_id, address, inspection date/time, inspector name
- **Access Method**: PDF download + parsing with `pdfplumber`

#### Berkeley
- **Source**: Daily PDF + Accela API
- **URL**: https://berkeleyca.gov/sites/default/files/documents/Scheduled%20Inspections%20Today.pdf
- **Update Frequency**: Daily
- **Content**: permit_id, address, inspection date/time, inspector name
- **Access Method**: PDF download + parsing with `pdfplumber`

#### San Jose
- **Source**: Open Data Portal (CKAN)
- **URL**: https://data.sanjoseca.gov/api/3/action/datastore_search
- **Dataset**: "Building Permits Under Inspection"
- **Update Frequency**: Daily
- **Content**: Permits with "under inspection" status
- **Access Method**: JSON API

### ⚠️ Fallback: Predictive Model

For jurisdictions without public calendar data (San Francisco, Oakland, Alameda, etc.):

- Uses construction phase sequence: `FOUNDATION → FRAMING → ROUGH_MEP → INSULATION → DRYWALL → FINAL`
- Estimates days between phases based on historical data (7-14 days typical)
- Provides confidence score (0.6 for predictions vs 0.85+ for public data)

---

## Architecture

### New Modules

#### `utils/inspection_calendar_fetchers.py`
Implements PDF/API fetchers for each jurisdiction:

```python
from utils.inspection_calendar_fetchers import (
    ContraCostaFetcher,
    BerkeleyFetcher, 
    SanJoseFetcher,
    ScheduledInspection
)

# Fetch inspections
fetcher = ContraCostaFetcher()
inspections: List[ScheduledInspection] = fetcher.fetch()

# Convert to database format
for inspection in inspections:
    data = inspection.to_dict()  # Ready for DB insertion
```

#### `utils/inspection_predictor.py`
Prediction logic for next inspections:

```python
from utils.inspection_predictor import (
    predict_next_inspection,
    estimate_gc_presence,
    get_next_inspection_date,
    is_inspection_soon
)

lead = {..., "phase": "framing", "phase_order": 2, ...}

# Predict next inspection
prediction = predict_next_inspection(lead)
# Returns: {
#   "inspection_type": "ROUGH_MEP",
#   "estimated_date": date(2024, 04, 25),
#   "confidence": 0.6,
#   "gc_probability": 0.75
# }

# Check if soon
if is_inspection_soon(lead, days=7):
    print("Inspection within 7 days!")
```

#### `workers/inspection_scheduler.py`
Background scheduler using APScheduler:

```python
from workers.inspection_scheduler import (
    start_inspection_scheduler,
    fetch_inspections_now,
    get_scheduler_status
)

# Start at app startup
start_inspection_scheduler()  # Runs daily at 9:00 AM

# Manual fetch (for admin requests)
count = fetch_inspections_now()  # Returns count of saved inspections

# Check status
status = get_scheduler_status()
# Returns: {
#   "running": true,
#   "jobs": [{"id": "fetch_inspections_daily", "next_run_time": "..."}]
# }
```

### Database

#### New Table: `scheduled_inspections`
```sql
CREATE TABLE scheduled_inspections (
    id INTEGER PRIMARY KEY,
    permit_id TEXT NOT NULL,
    address TEXT NOT NULL,
    address_key TEXT,  -- Links to lead address
    inspection_date DATE,
    inspection_type TEXT,  -- FOUNDATION, FRAMING, etc.
    time_window_start TEXT,
    time_window_end TEXT,
    inspector_name TEXT,
    inspector_id TEXT,
    jurisdiction TEXT,  -- "contra_costa", "berkeley", "san_jose"
    source_url TEXT,
    status TEXT,  -- SCHEDULED, COMPLETED, CANCELLED
    gc_presence_probability REAL,  -- 0.0-1.0
    created_at TIMESTAMP,
    fetched_at TIMESTAMP,
    UNIQUE(permit_id, inspection_date, jurisdiction)
);
```

**Indices for Performance:**
- `idx_scheduled_inspections_permit` - Fast permit lookup
- `idx_scheduled_inspections_address` - Fast address lookup
- `idx_scheduled_inspections_date` - Fast date range queries
- `idx_scheduled_inspections_jurisdiction` - Fast jurisdiction filtering

#### Extended Fields in `consolidated_leads`
- `next_scheduled_inspection_date` (DATE) - When GC will be on-site
- `next_inspection_type` (TEXT) - Type of inspection
- `gc_likely_on_site_date` (DATE) - Same as inspection date
- `inspection_source` (TEXT) - "public_calendar", "prediction", or "accela_api"

### Helper Functions in `web_db.py`
```python
# Insert/Update
insert_scheduled_inspection(data: dict) -> int

# Retrieve
get_upcoming_inspections(address_key: str, days: int = 30) -> list
get_inspections_by_jurisdiction(jurisdiction: str, start_date: str, end_date: str) -> list

# Maintenance
cleanup_old_inspections(older_than_days: int = 60) -> int
```

---

## Integration Points

### Construction Agent Enrichment
In `agents/construction_agent.py` (~line 726):

```python
# After GC contact lookup, enrich with inspection data
try:
    # Search for upcoming public calendar inspections
    upcoming = get_upcoming_inspections(address_key, days=30)
    
    if upcoming:
        # Use public calendar data (high confidence)
        lead['next_scheduled_inspection_date'] = upcoming[0]['inspection_date']
        lead['inspection_source'] = 'public_calendar'
        lead['_gc_presence_probability'] = 0.85
    else:
        # Fallback to prediction
        prediction = predict_next_inspection(lead)
        if prediction:
            lead['next_scheduled_inspection_date'] = prediction['estimated_date']
            lead['inspection_source'] = 'prediction'
            lead['_gc_presence_probability'] = 0.6
except Exception as e:
    logger.warning(f"Error enriching inspection data: {e}")
```

### Lead Scoring Boost
In `utils/lead_scoring.py` (~line 203):

```python
# Inspections < 7 days get significant boost
if lead.get('next_scheduled_inspection_date'):
    days_until = (insp_date - today).days
    
    if 0 <= days_until <= 7:
        total += 8  # 🔥 HOT tier boost
        reasons.append(f"Inspección en {days_until} días (GC en sitio)")
    elif days_until <= 14:
        total += 6
    elif days_until <= 30:
        total += 4
```

---

## API Endpoints

### Get Scheduled Inspections
```bash
# List by jurisdiction
GET /api/scheduled_inspections?jurisdiction=berkeley&start_date=2024-04-01

# Get lead-specific inspections
GET /api/leads/{lead_id}/scheduled_inspections?days=30

# Response
{
  "jurisdiction": "berkeley",
  "count": 12,
  "inspections": [
    {
      "id": 1,
      "permit_id": "BRK-2024-001",
      "address": "123 Main St",
      "inspection_date": "2024-04-08",
      "inspection_type": "FRAMING",
      "time_window_start": "9:00 AM",
      "time_window_end": "12:00 PM",
      "inspector_name": "Jane Smith",
      "gc_presence_probability": 0.85
    }
  ]
}
```

### Create Scheduled Inspection (Admin Only)
```bash
POST /api/scheduled_inspections
Authorization: Bearer <token>
Content-Type: application/json

{
  "permit_id": "SF-2024-999",
  "address": "456 Oak Ave, San Francisco, CA",
  "inspection_date": "2024-04-15",
  "inspection_type": "ROUGH_MEP",
  "jurisdiction": "sf",
  "inspector_name": "Bob Johnson",
  "time_window_start": "10:00 AM",
  "time_window_end": "1:00 PM"
}

# Response
{
  "id": 42,
  "status": "created",
  "inspection": {...}
}
```

### Admin Endpoints

#### Check Scheduler Status
```bash
GET /api/admin/scheduler/status
Authorization: Bearer <token>

{
  "running": true,
  "jobs": [
    {
      "id": "fetch_inspections_daily",
      "name": "Daily inspection fetch",
      "next_run_time": "2024-04-06 09:00:00"
    }
  ]
}
```

#### Trigger Manual Fetch
```bash
POST /api/admin/scheduler/fetch-now
Authorization: Bearer <token>

{
  "status": "completed",
  "inspections_saved": 342,
  "timestamp": "2024-04-05T14:30:00"
}
```

#### Cleanup Old Records
```bash
POST /api/admin/scheduler/cleanup
Authorization: Bearer <token>
Content-Type: application/json

{
  "older_than_days": 60
}

{
  "status": "completed",
  "deleted_records": 127,
  "timestamp": "2024-04-05T14:32:00"
}
```

---

## Usage Examples

### Basic Lead Enrichment
```python
from agents.construction_agent import ConstructionAgent
from utils.inspection_predictor import get_next_inspection_date

agent = ConstructionAgent()
leads = agent.query()

for lead in leads:
    # Lead now has inspection data
    if lead.get('next_scheduled_inspection_date'):
        next_date = lead['next_scheduled_inspection_date']
        source = lead['inspection_source']
        print(f"GC will be on-site: {next_date} ({source})")
```

### Finding Hot Leads with Nearby Inspections
```python
from utils.lead_scoring import score_lead
from utils.inspection_predictor import is_inspection_soon

hot_leads = []
for lead in all_leads:
    if is_inspection_soon(lead, days=7):
        scoring = score_lead(lead)
        if scoring['score'] >= 70:
            hot_leads.append({
                'lead': lead,
                'score': scoring['score'],
                'inspection_date': lead['next_scheduled_inspection_date']
            })

# Sort by earliest inspection date
hot_leads.sort(key=lambda x: x['inspection_date'])
```

### Tracking GC Availability
```python
from utils.web_db import get_inspections_by_jurisdiction
from datetime import date, timedelta

# Get all inspections for a city this week
this_week_start = date.today()
this_week_end = date.today() + timedelta(days=7)

inspections = get_inspections_by_jurisdiction(
    jurisdiction="berkeley",
    start_date=this_week_start.isoformat(),
    end_date=this_week_end.isoformat()
)

# Group by contractor to see activity
gc_activity = {}
for insp in inspections:
    # Could lookup permit to get GC name
    gc = insp.get('inspector_name', 'Unknown')
    if gc not in gc_activity:
        gc_activity[gc] = []
    gc_activity[gc].append(insp)

for gc, inspections_list in gc_activity.items():
    print(f"{gc}: {len(inspections_list)} inspections this week")
```

---

## Configuration

### Environment Variables
```bash
# Default timeout for API/PDF fetches (seconds)
SOURCE_TIMEOUT=45

# Scheduler job configuration
# Inspection fetch runs daily at 9:00 AM (UTC)
# Cleanup runs weekly on Monday at 2:00 AM (UTC)

# Database path
DB_PATH=data/leads.db
```

### Scheduler Customization
Edit `workers/inspection_scheduler.py`:

```python
# Change fetch time from 9:00 AM to 8:00 AM
scheduler.add_job(
    fetch_all_inspections,
    'cron',
    hour=8,  # Changed from 9
    minute=0
)

# Add additional fetcher
scheduler.add_job(
    fetch_accela_api_inspections,
    'cron',
    hour=10,
    minute=0
)
```

---

## Testing

Run unit tests:
```bash
pytest tests/test_inspection_calendar.py -v

# Test specific classes
pytest tests/test_inspection_calendar.py::TestInspectionPredictor -v
pytest tests/test_inspection_calendar.py::TestScheduledInspectionModel -v
```

Manual testing:
```bash
# Trigger fetch and check result
curl -X POST http://localhost:5000/api/admin/scheduler/fetch-now \
  -H "Authorization: Bearer <token>"

# Check scheduler status
curl http://localhost:5000/api/admin/scheduler/status \
  -H "Authorization: Bearer <token>"

# Get upcoming inspections for Berkeley
curl "http://localhost:5000/api/scheduled_inspections?jurisdiction=berkeley" \
  -H "Authorization: Bearer <token>"
```

---

## Troubleshooting

### PDF Parser Errors
If Contra Costa or Berkeley PDFs fail to parse:
- Check PDF URL is still valid
- Verify PDF structure hasn't changed (look at HTTP response)
- Check logs: `logger.error(f"Error parsing PDF: {e}")`

### CKAN API Errors
If San Jose data fetch fails:
- Verify dataset ID is still `ca355e55-c651-4e00-9bde-2c014f229486`
- Check CKAN API response with: `curl https://data.sanjoseca.gov/api/3/action/datastore_search?resource_id=...`

### Scheduler Not Running
- Check `get_scheduler_status()` returns `"running": true`
- Verify APScheduler is installed: `pip list | grep apscheduler`
- Check application logs for scheduler initialization

### Database Locks
If cleanup fails with "database is locked":
- Check no other processes have the database open
- Ensure `cleanup_old_inspections()` isn't called during a lead import

---

## Performance Considerations

### Query Performance
- Indices on `permit_id`, `address_key`, `inspection_date`, `jurisdiction`
- Typical query times: <100ms for daily fetch results
- Archive old records weekly to keep table size manageable

### PDF Parsing
- Contra Costa + Berkeley PDFs: ~100-300 records each, parse in <2s
- San Jose CKAN API: ~500-1000 records, download + parse in <5s

### Storage
- ~500 inspection records per week (all jurisdictions)
- 60-day retention = ~43,000 records
- Table size: ~5-10 MB (SQLite)

---

## Future Enhancements

1. **Accela API Integration** - Direct API access for authenticated jurisdictions
2. **Google Calendar Sync** - GCs can integrate their personal calendars
3. **Webhook Notifications** - Alert users when GC activity detected
4. **ML-based GC Matching** - Map permit names to GC records
5. **Multi-day Predictions** - Estimate full inspection cycle duration

---

## References

- [Plan Document](./Plans/calendar-integration.md)
- [Contra Costa County ePermits](https://epermits.cccounty.us)
- [Berkeley Citizen Access](https://aca.cityofberkeley.info/citizenaccess)
- [San Jose Open Data](https://data.sanjoseca.gov)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)
- [pdfplumber Documentation](https://github.com/jsvine/pdfplumber)

---

**Version**: 1.0  
**Last Updated**: 2024-04-05  
**Author**: Claude  
**Branch**: `claude/check-lead-calendar-integration-K0AOx`
