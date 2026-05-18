"""
scripts/contacts_to_huly.py
📤 Push contractor contacts from CSVs to Huly CRM

Lee los CSVs en /contacts, enriquece con datos básicos,
y push a Huly como Contact + Deal.

No requiere verificación CSLB — usa los datos que ya tenemos.
La verificación CSLB se puede hacer después como paso separado.

Uso:
    python scripts/contacts_to_huly.py
    python scripts/contacts_to_huly.py --limit 50
    python scripts/contacts_to_huly.py --csv "contacts/C-39 ROOFING - CSLBSearchData.csv"
"""

import os
import csv
import re
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("contacts_to_huly")

# Classification mapping from filename
FILENAME_TO_TRADE = {
    "C-39": "ROOFING",
    "C-2": "INSULATION",
    "C-16": "FIRE_PROTECTION",
    "C-38": "REFRIGERATION",
    "C-53": "SWIMMING_POOL",
    "C-61": "SPECIALTY",
    "B-2": "RESIDENTIAL_REMODELING",
    "B CONTACTS": "GENERAL",
    "GC": "GENERAL",
    "ARCHITECT": "ARCHITECT",
    "PROPERTY_MANAGER": "PROPERTY_MANAGEMENT",
    "PROFESIONAL-SERVICES": "PROFESSIONAL_SERVICES",
    "HEALTHY_NAICS": "HEALTHCARE",
    "STORAGE": "STORAGE",
    "REAL_STATE": "REAL_ESTATE",
}


def detect_fields(headers: list) -> dict:
    """Auto-detect CSV column names."""
    result = {"name": None, "phone": None, "email": None, "city": None, "classification": None, "license": None}
    
    mappings = {
        "name": ["BusinessName", "Business Name", "Name", "Company", "ContractorName", "Full Name", "Organization"],
        "phone": ["PhoneNumber", "Phone", "phone", "Tel", "Telephone", "Mobile"],
        "email": ["Email", "email", "E-mail", "EmailAddress"],
        "city": ["City", "city", "Location", "Area"],
        "classification": ["Classification", "Class", "Type", "Category", "Trade"],
        "license": ["License", "LicenseNumber", "Lic#", "LicNum"],
    }
    
    for key, patterns in mappings.items():
        for h in headers:
            h_clean = h.strip()
            for p in patterns:
                if p.lower() == h_clean.lower():
                    result[key] = h_clean
                    break
    
    return result


def infer_trade_from_filename(filename: str) -> str:
    """Infer the trade from the CSV filename."""
    filename_upper = filename.upper()
    for key, trade in FILENAME_TO_TRADE.items():
        if key.upper() in filename_upper:
            return trade
    return "GENERAL"


def read_csv(csv_path: str) -> list:
    """Read contacts from a CSV file."""
    contacts = []
    
    for encoding in ['utf-8', 'latin-1', 'cp1252']:
        try:
            with open(csv_path, 'r', encoding=encoding) as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    continue
                
                fields = detect_fields(reader.fieldnames)
                
                for row in reader:
                    name = row.get(fields["name"], "") if fields["name"] else ""
                    phone = row.get(fields["phone"], "") if fields["phone"] else ""
                    email = row.get(fields["email"], "") if fields["email"] else ""
                    city = row.get(fields["city"], "") if fields["city"] else ""
                    classification = row.get(fields["classification"], "") if fields["classification"] else ""
                    license_num = row.get(fields["license"], "") if fields["license"] else ""
                    
                    # Clean phone
                    if phone:
                        phone = re.sub(r'[^\d\(\)\-\+\s]', '', str(phone)).strip()
                    
                    # Clean license
                    if license_num:
                        license_num = re.sub(r'[^0-9]', '', str(license_num))
                    
                    if not name and not phone and not email:
                        continue
                    
                    contacts.append({
                        "business_name": name.strip(),
                        "phone": phone.strip(),
                        "email": email.strip(),
                        "city": city.strip(),
                        "classification": classification.strip(),
                        "license_number": license_num.strip(),
                        "source_file": os.path.basename(csv_path),
                        "trade": infer_trade_from_filename(csv_path),
                    })
            break
        except UnicodeDecodeError:
            continue
    
    return contacts


def push_to_huly(contacts: list) -> dict:
    """Push contacts to Huly CRM via the bridge."""
    from utils.huly_crm import push_lead_to_crm
    
    stats = {"total": len(contacts), "pushed": 0, "skipped": 0, "errors": 0}
    
    for i, contact in enumerate(contacts):
        name = contact.get("business_name", "Unknown")
        phone = contact.get("phone", "")
        email = contact.get("email", "")
        city = contact.get("city", "Bay Area")
        trade = contact.get("trade", "GENERAL")
        lic = contact.get("license_number", "")
        
        if not name or name == "Unknown":
            stats["skipped"] += 1
            continue
        
        lead = {
            "id": f"contact_{lic or hash(name) % 100000}",
            "address": "",
            "city": city,
            "contractor": name,
            "contact_phone": phone,
            "contact_email": email,
            "description": f"Contractor — {trade}" + (f" — License: {lic}" if lic else ""),
            "value_float": 0,
            "_trade": trade,
            "agent_sources": "csv_import",
            "_agent_key": "csv_import",
            "_tripartite": {
                "subcontractor_score": 70 if lic else 50,
                "gc_score": 55 if lic else 35,
                "insurance_score": 30,
            },
        }
        
        try:
            result = push_lead_to_crm(lead, lead["_tripartite"])
            if result and result.get("status") == "pushed":
                stats["pushed"] += 1
                logger.info(f"[{i+1}/{len(contacts)}] ✅ {name[:40]} ({city})")
            else:
                stats["skipped"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.debug(f"[{i+1}] Error: {e}")
    
    return stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Push contractor CSVs to Huly CRM")
    parser.add_argument("--csv", help="Specific CSV file", default=None)
    parser.add_argument("--limit", type=int, default=0, help="Max contacts to push")
    parser.add_argument("--dry-run", action="store_true", help="Don't push, just count")
    parser.add_argument("--json", action="store_true", help="Output JSON to stdout for batch_insert.js")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("Contractor CSVs → Huly CRM")
    logger.info("=" * 60)
    
    all_contacts = []
    
    if args.csv:
        contacts = read_csv(args.csv)
        logger.info(f"Loaded {len(contacts)} contacts from {args.csv}")
        all_contacts.extend(contacts)
    else:
        contacts_dir = PROJECT_ROOT / "contacts"
        for csv_file in sorted(contacts_dir.glob("*.csv")):
            contacts = read_csv(str(csv_file))
            if contacts:
                trade = infer_trade_from_filename(csv_file.name)
                logger.info(f"  {csv_file.name}: {len(contacts)} contacts ({trade})")
                all_contacts.extend(contacts)
    
    # Deduplicate by name+phone
    seen = set()
    unique = []
    for c in all_contacts:
        key = f"{c['business_name']}|{c['phone']}"
        if key not in seen:
            seen.add(key)
            unique.append(c)
    all_contacts = unique
    
    logger.info(f"\nTotal unique contacts: {len(all_contacts)}")
    
    if args.limit > 0:
        all_contacts = all_contacts[:args.limit]
        logger.info(f"Limited to {args.limit}")
    
    if args.dry_run:
        logger.info("Dry run — not pushing")
        return
    
    if args.json:
        # Output JSON for batch_insert.js
        print(json.dumps(all_contacts))
        return
    
    logger.info(f"\n📤 Pushing to Huly CRM...\n")
    stats = push_to_huly(all_contacts)
    
    logger.info(f"\n{'='*60}")
    logger.info(f"RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"Total: {stats['total']}")
    logger.info(f"Pushed: {stats['pushed']}")
    logger.info(f"Skipped: {stats['skipped']}")
    logger.info(f"Errors: {stats['errors']}")


if __name__ == "__main__":
    main()
