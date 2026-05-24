"""
Re-score ALL leads using the v2 scoring engine with AI fields.
"""
import os, sys, json, sqlite3, time

sys.path.insert(0, "/opt/MLeads")
os.chdir("/opt/MLeads")
from dotenv import load_dotenv
load_dotenv()
from utils.lead_scoring import score_lead

DB = "/opt/MLeads/data/leads.db"

conn = sqlite3.connect(DB, timeout=30)
conn.execute("PRAGMA journal_mode=WAL")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Get ALL active leads with phone
c.execute("""
    SELECT address_key, lead_data FROM consolidated_leads 
    WHERE has_phone = 1 AND COALESCE(is_dead_lead, 0) = 0
""")
rows = c.fetchall()
print(f"Re-scoring {len(rows)} leads...")

updated = 0
batch = 0
for row in rows:
    lead_data = json.loads(row["lead_data"])
    
    # Run scoring
    result = score_lead(lead_data)
    new_score = min(int(result["score"]), 100)
    
    # Update lead_data with scoring result
    lead_data["_scoring"] = {
        "score": new_score,
        "reasons": result.get("reasons", []),
        "tier": result.get("tier", "COLD"),
    }
    
    c.execute(
        "UPDATE consolidated_leads SET lead_data = ? WHERE address_key = ?",
        (json.dumps(lead_data, default=str), row["address_key"])
    )
    updated += 1
    
    batch += 1
    if batch % 50 == 0:
        conn.commit()
        print(f"  {updated}/{len(rows)} scored ({new_score} avg this batch)")

conn.commit()
conn.close()
print(f"✅ Done! Re-scored {updated} leads")
