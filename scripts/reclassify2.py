"""
Reclassify remaining leads - WAL mode version (non-blocking)
"""
import os, sys, json, sqlite3, time, logging

sys.path.insert(0, "/opt/MLeads")
os.chdir("/opt/MLeads")
from dotenv import load_dotenv
load_dotenv()
from utils.ai_classifier import classify_lead

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reclassify2")

DB_PATH = "/opt/MLeads/data/leads.db"

def reclassify(max_leads=None):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("""SELECT address_key, lead_data FROM consolidated_leads 
        WHERE has_phone = 1 AND COALESCE(is_dead_lead,0) = 0
        AND (json_extract(lead_data, '$._classifier_source') IS NULL 
             OR json_extract(lead_data, '$._classifier_source') = 'rules'
             OR json_extract(lead_data, '$._classifier_source') = '')""")
    rows = c.fetchall()
    
    total = len(rows)
    if max_leads:
        rows = rows[:max_leads]
        total = len(rows)
    
    logger.info(f"Leads to reclassify: {total}")
    done = 0
    errors = 0
    start = time.time()
    
    for i, row in enumerate(rows):
        addr_key = row["address_key"]
        try:
            lead_data = json.loads(row["lead_data"])
        except:
            errors += 1
            continue
        
        lead = {
            "description": lead_data.get("description", ""),
            "permit_type": lead_data.get("permit_type", ""),
            "value_float": lead_data.get("value_float", 0),
            "city": lead_data.get("city", ""),
            "contractor": lead_data.get("contractor", ""),
            "owner": lead_data.get("owner", ""),
            "contact_phone": lead_data.get("contact_phone", ""),
            "service_type": lead_data.get("service_type", lead_data.get("_agent_key", "")),
            "_agent_key": lead_data.get("_agent_key", ""),
        }
        
        desc = lead["description"] or ""
        if not desc and not lead["permit_type"]:
            errors += 1
            continue
        
        try:
            classification = classify_lead(lead)
        except Exception as e:
            logger.warning(f"Error: {e}")
            errors += 1
            time.sleep(2)
            continue
        
        if classification.get("_source") != "qwen":
            errors += 1
            time.sleep(2)
            continue
        
        lead_data["_trade"] = classification.get("trade", "GENERAL")
        lead_data["_sub_trades"] = classification.get("sub_trades", [])
        lead_data["_urgency"] = classification.get("urgency", "MEDIUM")
        lead_data["_urgency_reason"] = classification.get("urgency_reason", "")
        lead_data["_budget_min"] = classification.get("budget_min")
        lead_data["_budget_max"] = classification.get("budget_max")
        lead_data["_budget_confidence"] = classification.get("budget_confidence", "LOW")
        lead_data["_services"] = classification.get("services", [])
        lead_data["_ai_summary"] = classification.get("summary", "")
        lead_data["_is_residential"] = classification.get("is_residential", False)
        lead_data["_is_commercial"] = classification.get("is_commercial", False)
        lead_data["_owner_type"] = classification.get("owner_type", "UNKNOWN")
        lead_data["_classifier_source"] = "qwen"
        lead_data["_project_phase"] = classification.get("project_phase", "PERMITTING")
        lead_data["_project_scope"] = classification.get("project_scope", "PARTIAL")
        lead_data["_decision_maker"] = classification.get("decision_maker", "UNKNOWN")
        lead_data["_competing_subs"] = classification.get("competing_subs", 3)
        lead_data["_best_contact_time"] = classification.get("best_contact_time", "MORNING")
        lead_data["_key_pain_point"] = classification.get("key_pain_point", "")
        lead_data["_upsell_opportunity"] = classification.get("upsell_opportunity", "")
        lead_data["_ai_confidence"] = classification.get("confidence", 0.5)
        
        try:
            c.execute("UPDATE consolidated_leads SET lead_data = ? WHERE address_key = ?",
                      (json.dumps(lead_data, default=str), addr_key))
        except sqlite3.OperationalError as e:
            logger.warning(f"DB error: {e}, retrying...")
            time.sleep(1)
            c.execute("UPDATE consolidated_leads SET lead_data = ? WHERE address_key = ?",
                      (json.dumps(lead_data, default=str), addr_key))
        
        done += 1
        if done % 10 == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate / 60 if rate > 0 else 0
            logger.info(f"Progress: {done}/{total} ({rate:.1f}/s, ETA {eta:.0f}min)")
        
        time.sleep(0.8)  # rate limit safety
    
    conn.commit()
    conn.close()
    elapsed = time.time() - start
    logger.info(f"Done! {done} classified, {errors} errors, {elapsed:.0f}s")

if __name__ == "__main__":
    reclassify()
