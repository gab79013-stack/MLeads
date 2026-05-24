"""
scripts/enrich_contacts.py
━━━━━━━━━━━━━━━━━━━━━━━━
Enrich leads that don't have phone or email.
Runs in background with rate limiting.
"""

import os, sys, json, sqlite3, time, logging, signal

sys.path.insert(0, "/opt/MLeads")
os.chdir("/opt/MLeads")
from dotenv import load_dotenv
load_dotenv()

from agents.contact_enrichment_agent import enrich_lead

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DB = "/opt/MLeads/data/leads.db"
BATCH_SIZE = 50
DELAY = 2.0  # seconds between enrichments (rate limit)
MAX_PER_RUN = 500

_running = True

def _signal_handler(sig, frame):
    global _running
    logger.info("Signal received, stopping gracefully...")
    _running = False

signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)

def main():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Find leads that need enrichment (active, no phone AND no email)
    c.execute("""
        SELECT address_key, lead_data, contractor, address, city
        FROM consolidated_leads
        WHERE has_phone = 0
        AND COALESCE(is_dead_lead, 0) = 0
        AND contractor IS NOT NULL
        AND contractor != ''
        AND contractor != 'NONE'
        AND (lead_data NOT LIKE '%contact_email%' OR json_extract(lead_data, '$.contact_email') = '' OR json_extract(lead_data, '$.contact_email') IS NULL)
        AND lead_data NOT LIKE '%_enrichment_source%contact_enrichment%'
        ORDER BY json_extract(lead_data, '$._scoring.score') DESC
        LIMIT ?
    """, (MAX_PER_RUN,))

    rows = c.fetchall()
    logger.info(f"Leads to enrich: {len(rows)}")

    enriched = 0
    found_phone = 0
    found_email = 0

    for i, row in enumerate(rows):
        if not _running:
            logger.info(f"Stopping at {i}/{len(rows)}")
            break

        lead_data = json.loads(row["lead_data"])
        contractor = row["contractor"]
        city = row["city"]
        had_phone = bool(lead_data.get("contact_phone"))
        had_email = bool(lead_data.get("contact_email"))

        logger.info(f"[{i+1}/{len(rows)}] Enriching: {contractor} ({city})")

        try:
            enriched_data = enrich_lead(lead_data)
        except Exception as e:
            logger.warning(f"  Failed: {e}")
            time.sleep(DELAY)
            continue

        # Check what we found
        new_phone = bool(enriched_data.get("contact_phone")) and not had_phone
        new_email = bool(enriched_data.get("contact_email")) and not had_email

        if new_phone:
            found_phone += 1
            logger.info(f"  📞 Found phone: {enriched_data.get('contact_phone')}")
        if new_email:
            found_email += 1
            logger.info(f"  📧 Found email: {enriched_data.get('contact_email')}")
        if enriched_data.get("key_contacts"):
            logger.info(f"  👥 Found {len(enriched_data['key_contacts'])} key contacts")

        # Update DB
        c.execute(
            "UPDATE consolidated_leads SET lead_data = ? WHERE address_key = ?",
            (json.dumps(enriched_data, default=str), row["address_key"])
        )

        # Update has_phone flag
        if new_phone:
            c.execute(
                "UPDATE consolidated_leads SET has_phone = 1 WHERE address_key = ?",
                (row["address_key"],)
            )

        enriched += 1

        # Commit every 10
        if (i + 1) % 10 == 0:
            conn.commit()
            logger.info(f"  Progress: {enriched} enriched, {found_phone} phones, {found_email} emails")

        time.sleep(DELAY)

    conn.commit()
    conn.close()

    logger.info(f"✅ Done! Enriched {enriched} leads")
    logger.info(f"   📞 New phones: {found_phone}")
    logger.info(f"   📧 New emails: {found_email}")
    logger.info(f"   📊 Success rate: {((found_phone + found_email) / max(enriched, 1) * 100):.1f}%")


if __name__ == "__main__":
    main()
