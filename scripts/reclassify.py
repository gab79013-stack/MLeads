"""
Reclassify all active leads with DeepSeek V3.2 via Vultr Inference (FREE)
Processes leads in batches, updates consolidated_leads.lead_data JSON.
"""
import os
import sys
import json
import sqlite3
import time
import logging

sys.path.insert(0, "/opt/MLeads")
os.chdir("/opt/MLeads")

from dotenv import load_dotenv
load_dotenv()

from utils.ai_classifier import classify_lead, _rate_limit_check, _get_client, _cache

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reclassify")

DB_PATH = "/opt/MLeads/data/leads.db"
MODEL = os.getenv("AI_CLASSIFIER_MODEL", "nvidia/DeepSeek-V3.2-NVFP4")

SYSTEM_PROMPT = """You are an expert construction lead analyst for a subcontractor platform. Given a building permit, extract JSON with these fields:
{"trade":"ROOFING|ELECTRICAL|DRYWALL|PAINTING|LANDSCAPING|HVAC|PLUMBING|INSULATION|FRAMING|CONCRETE|FLOORING|WINDOWS|DEMOLITION|GENERAL|UNKNOWN","sub_trades":["TRADE1","TRADE2"],"urgency":"HIGH|MEDIUM|LOW","urgency_reason":"brief reason","budget_min":null,"budget_max":null,"budget_confidence":"HIGH|MEDIUM|LOW","services":["service1","service2"],"is_residential":true,"is_commercial":true,"owner_type":"HOMEOWNER|INVESTOR|DEVELOPER|GC|GOVERNMENT|UNKNOWN","project_phase":"PLANNING|PERMITTING|ACTIVE|INSPECTION|COMPLETION","project_scope":"FULL|PARTIAL|REPAIR|EMERGENCY|MAINTENANCE","decision_maker":"HOMEOWNER|GC|PM|ARCHITECT|UNKNOWN","competing_subs":3,"best_contact_time":"MORNING|AFTERNOON|EVENING|ANY","key_pain_point":"main problem","upsell_opportunity":"additional service","summary":"2-sentence pitch","confidence":0.9}
Rules: trade=PRIMARY trade needed. sub_trades=OTHER trades likely needed. urgency HIGH=violation/emergency/active damage, MEDIUM=permit issued/new work, LOW=planning. Reply ONLY with the JSON object."""


def reclassify_batch(batch_size=20, max_leads=None, dry_run=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get active leads not yet AI-classified
    query = """
        SELECT address_key, lead_data 
        FROM consolidated_leads 
        WHERE has_phone = 1 
        AND COALESCE(is_dead_lead, 0) = 0
        AND json_extract(lead_data, '$._classifier_source') IS NULL
        OR json_extract(lead_data, '$._classifier_source') = 'rules'
        OR json_extract(lead_data, '$._classifier_source') = ''
    """
    c.execute(query)
    rows = c.fetchall()
    
    total = len(rows)
    if max_leads:
        rows = rows[:max_leads]
        total = len(rows)
    
    logger.info(f"Found {total} leads to reclassify (batch_size={batch_size})")
    
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
        
        # Build the lead dict for classifier
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
        
        # Classify with AI
        try:
            classification = classify_lead(lead)
        except Exception as e:
            logger.warning(f"Error classifying {addr_key}: {e}")
            errors += 1
            continue
        
        if classification.get("_source") != "qwen":
            # Fallback was used, skip (will retry later)
            errors += 1
            continue
        
        # Merge classification into lead_data
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
        
        if not dry_run:
            c.execute(
                "UPDATE consolidated_leads SET lead_data = ? WHERE address_key = ?",
                (json.dumps(lead_data, default=str), addr_key)
            )
        
        done += 1
        if done % 10 == 0:
            conn.commit()
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate / 60 if rate > 0 else 0
            logger.info(f"Progress: {done}/{total} classified, {errors} errors, {rate:.1f}/sec, ETA: {eta:.1f}min")
        
        # Rate limit: 30 calls/min
        time.sleep(0.5)
    
    conn.commit()
    conn.close()
    
    elapsed = time.time() - start
    logger.info(f"Done! {done} classified, {errors} errors, {elapsed:.0f}s elapsed")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    max_leads = None
    for arg in sys.argv:
        if arg.startswith("--max="):
            max_leads = int(arg.split("=")[1])
    
    reclassify_batch(batch_size=20, max_leads=max_leads, dry_run=dry)
