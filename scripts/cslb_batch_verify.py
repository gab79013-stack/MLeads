"""
scripts/cslb_batch_verify.py
🔍 Batch CSLB License Verification — Verifica todos los contractors de los CSVs

Lee los CSVs en /contacts, extrae números de licencia y nombres de negocio,
verifica cada uno contra el portal CSLB, y genera:
  1. Reporte CSV con status de cada licencia
  2. Push de contractors verificados a Huly CRM
  3. Actualización del cache CSLB

Uso:
    python scripts/cslb_batch_verify.py
    python scripts/cslb_batch_verify.py --csv contacts/C-39\ ROOFING\ -\ CSLBSearchData.csv
    python scripts/cslb_batch_verify.py --push-huly
    python scripts/cslb_batch_verify.py --limit 50
"""

import os
import csv
import re
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("cslb_batch")

# ── CSV Config ──────────────────────────────────────────────────────────

# Map CSV filenames to their CSLB classification codes
CSV_TO_CLASSIFICATION = {
    "C-39 ROOFING": "C-39",
    "C-2 INSULATION": "C-2",
    "C-16": "C-16",
    "C-21 Demolition": "C-21",
    "C-38 Refrigeration": "C-38",
    "C-53 Swimming Pool": "C-53",
    "C-61_D-34": "C-61",
    "B-2 RESIDENTIAL": "B",
    "B CONTACTS_GC": "B",
    "B_CONTACTS_GC": "B",
    "B CONTACTS_LISTA_B_GC": "B",
}

# Fields to look for license numbers and business names
LICENSE_FIELDS = ["LicenseNumber", "License", "license_number", "LicNum", "LicNo"]
NAME_FIELDS = ["BusinessName", "Business", "Name", "Company", "business_name", "ContractorName"]
PHONE_FIELDS = ["PhoneNumber", "Phone", "phone", "Tel"]
CITY_FIELDS = ["City", "city", "Location"]
CLASSIFICATION_FIELDS = ["Classification", "ClassCode", "license_class"]


def detect_csv_fields(headers: list) -> dict:
    """Auto-detect which columns contain license, name, phone, city."""
    result = {"license": None, "name": None, "phone": None, "city": None, "classification": None}
    
    for h in headers:
        h_clean = h.strip()
        for field_list, key in [
            (LICENSE_FIELDS, "license"),
            (NAME_FIELDS, "name"),
            (PHONE_FIELDS, "phone"),
            (CITY_FIELDS, "city"),
            (CLASSIFICATION_FIELDS, "classification"),
        ]:
            if result[key]:
                continue
            for pattern in field_list:
                if pattern.lower() in h_clean.lower():
                    result[key] = h_clean
                    break
    
    return result


def extract_license_from_name(business_name: str) -> str:
    """Try to extract a CSLB license number from a business name or data."""
    # CSLB license numbers are typically 6-8 digits
    match = re.search(r'\b(\d{6,8})\b', business_name)
    if match:
        return match.group(1)
    return ""


def read_csv_contacts(csv_path: str) -> list:
    """Read contacts from a CSV file."""
    contacts = []
    
    try:
        # Try different encodings
        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                with open(csv_path, 'r', encoding=encoding) as f:
                    reader = csv.DictReader(f)
                    headers = reader.fieldnames
                    if not headers:
                        continue
                    
                    fields = detect_csv_fields(headers)
                    
                    for row in reader:
                        contact = {
                            "source_file": os.path.basename(csv_path),
                            "business_name": row.get(fields["name"], "") if fields["name"] else "",
                            "license_number": row.get(fields["license"], "") if fields["license"] else "",
                            "phone": row.get(fields["phone"], "") if fields["phone"] else "",
                            "city": row.get(fields["city"], "") if fields["city"] else "",
                            "classification": row.get(fields["classification"], "") if fields["classification"] else "",
                            "raw": dict(row),
                        }
                        
                        # Clean license number
                        if contact["license_number"]:
                            contact["license_number"] = re.sub(r'[^0-9]', '', str(contact["license_number"]))
                        
                        # If no license field, try to extract from name
                        if not contact["license_number"] and contact["business_name"]:
                            contact["license_number"] = extract_license_from_name(contact["business_name"])
                        
                        # Skip if no useful data at all
                        if not contact["business_name"] and not contact["license_number"]:
                            continue
                        
                        # Infer classification from filename
                        if not contact["classification"]:
                            for key, cls_code in CSV_TO_CLASSIFICATION.items():
                                if key in csv_path:
                                    contact["classification"] = cls_code
                                    break
                        
                        contacts.append(contact)
                break  # Success, don't try other encodings
                
            except UnicodeDecodeError:
                continue
                
    except Exception as e:
        logger.error(f"Error reading {csv_path}: {e}")
    
    return contacts


def batch_verify(contacts: list, delay: float = 2.5) -> list:
    """Verify CSLB licenses for a batch of contacts."""
    from utils.cslb_verifier import verify_license, search_by_name
    
    results = []
    total = len(contacts)
    
    for i, contact in enumerate(contacts):
        lic = contact.get("license_number", "")
        name = contact.get("business_name", "")
        city = contact.get("city", "")
        
        logger.info(f"[{i+1}/{total}] Verifying: {name[:40]} (lic={lic or 'N/A'})")
        
        result = {
            **contact,
            "verified_at": datetime.utcnow().isoformat(),
            "cslb_status": "not_verified",
            "is_active": False,
            "risk_level": "UNKNOWN",
            "classification_codes": [],
            "bond_amount": 0,
            "workers_comp": False,
            "disciplinary_actions": 0,
        }
        
        try:
            # Try license number first
            if lic and len(lic) >= 4:
                cslb_data = verify_license(lic)
                if cslb_data:
                    result["cslb_status"] = cslb_data.get("status", "UNKNOWN")
                    result["is_active"] = cslb_data.get("is_active", False)
                    result["classification_codes"] = cslb_data.get("classification_codes", [])
                    result["bond_amount"] = cslb_data.get("bond_amount", 0)
                    result["workers_comp"] = cslb_data.get("workers_comp", False)
                    result["disciplinary_actions"] = cslb_data.get("disciplinary_actions", 0)
                    result["cslb_business_name"] = cslb_data.get("business_name", "")
                    result["expire_date"] = cslb_data.get("expire_date", "")
                    
                    # Calculate risk
                    if not result["is_active"]:
                        result["risk_level"] = "CRITICAL"
                    elif cslb_data.get("disciplinary_actions", 0) > 0:
                        result["risk_level"] = "MEDIUM"
                    else:
                        result["risk_level"] = "LOW"
                    
                    logger.info(f"  ✅ {result['cslb_status']} | Classes: {result['classification_codes']} | Risk: {result['risk_level']}")
                else:
                    result["cslb_status"] = "not_found"
                    result["risk_level"] = "HIGH"
                    logger.info(f"  ❌ License not found")
            
            # If no license, try searching by name
            elif name and len(name) >= 3:
                search_results = search_by_name(name, city)
                if search_results:
                    first = search_results[0]
                    result["cslb_status"] = first.get("status", "SEARCH_RESULT")
                    result["cslb_business_name"] = first.get("business_name", "")
                    result["found_license"] = first.get("license_number", "")
                    logger.info(f"  🔍 Found: {first.get('business_name', '')} (lic={first.get('license_number', '')})")
                else:
                    result["cslb_status"] = "not_found_name_search"
                    result["risk_level"] = "HIGH"
                    logger.info(f"  ❌ Not found by name")
            else:
                result["cslb_status"] = "no_data"
                result["risk_level"] = "UNKNOWN"
                logger.info(f"  ⚠️ No license or name to verify")
        
        except Exception as e:
            logger.error(f"  Error: {e}")
            result["cslb_status"] = "error"
            result["error"] = str(e)
        
        results.append(result)
        
        # Rate limiting
        if i < total - 1 and result["cslb_status"] != "not_verified":
            time.sleep(delay)
    
    return results


def save_report(results: list, output_path: str = None):
    """Save verification results to CSV report."""
    if not output_path:
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        output_path = str(PROJECT_ROOT / "reports" / f"cslb_verification_{timestamp}.csv")
    
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    if not results:
        logger.warning("No results to save")
        return
    
    # Get all keys
    all_keys = []
    for r in results:
        for k in r.keys():
            if k not in all_keys and k != "raw":
                all_keys.append(k)
    
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    
    logger.info(f"Report saved: {output_path}")


def push_verified_to_huly(results: list):
    """Push verified contractors to Huly CRM."""
    try:
        from utils.huly_crm import push_lead_to_crm
    except ImportError:
        logger.error("Huly CRM module not available")
        return 0
    
    pushed = 0
    for r in results:
        if not r.get("is_active"):
            continue
        
        lead = {
            "id": f"cslb_{r.get('license_number', r.get('found_license', 'unknown'))}",
            "address": "",
            "city": r.get("city", "Bay Area"),
            "contractor": r.get("cslb_business_name") or r.get("business_name", ""),
            "contact_phone": r.get("phone", ""),
            "description": f"CSLB Verified Contractor — {r.get('classification', '')} — Status: {r.get('cslb_status', '')}",
            "value_float": 0,
            "_trade": classification_to_trade(r.get("classification", "")),
            "agent_sources": "cslb_verify",
            "_agent_key": "cslb_verify",
            "property_year_built": None,
            "_tripartite": {
                "subcontractor_score": 85 if r.get("is_active") else 30,
                "gc_score": 70 if r.get("is_active") else 20,
                "insurance_score": 50 if r.get("workers_comp") else 20,
            },
        }
        
        result = push_lead_to_crm(lead, lead["_tripartite"])
        if result and result.get("status") == "pushed":
            pushed += 1
    
    return pushed


def classification_to_trade(cls: str) -> str:
    """Map CSLB classification to trade name."""
    mapping = {
        "C-39": "ROOFING", "C-2": "INSULATION", "C-9": "DRYWALL",
        "C-10": "ELECTRICAL", "C-16": "FIRE_PROTECTION", "C-20": "HVAC",
        "C-21": "DEMOLITION", "C-27": "LANDSCAPING", "C-33": "PAINTING",
        "C-36": "PLUMBING", "C-8": "CONCRETE", "C-5": "FRAMING",
        "C-15": "FLOORING", "C-17": "WINDOWS", "C-61": "SPECIALTY",
        "B": "GENERAL", "A": "ENGINEERING",
    }
    # Match partial
    cls_upper = cls.upper().strip()
    for key, trade in mapping.items():
        if key in cls_upper:
            return trade
    return "GENERAL"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Batch CSLB License Verification")
    parser.add_argument("--csv", help="Path to specific CSV file", default=None)
    parser.add_argument("--push-huly", action="store_true", help="Push verified to Huly CRM")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of contacts to verify")
    parser.add_argument("--delay", type=float, default=2.5, help="Delay between CSLB requests (seconds)")
    parser.add_argument("--report-only", action="store_true", help="Only generate report, no verification")
    args = parser.parse_args()
    
    logger.info("=" * 60)
    logger.info("CSLB Batch License Verification")
    logger.info("=" * 60)
    
    # Load contacts
    all_contacts = []
    
    if args.csv:
        # Single CSV
        contacts = read_csv_contacts(args.csv)
        logger.info(f"Loaded {len(contacts)} contacts from {args.csv}")
        all_contacts.extend(contacts)
    else:
        # All CSLB CSVs
        contacts_dir = PROJECT_ROOT / "contacts"
        for csv_file in sorted(contacts_dir.glob("*.csv")):
            filename = csv_file.name
            # Only process CSLB-related CSVs
            if any(key in filename for key in ["C-", "B CONTACTS", "B_CONTACTS", "ROOFING", "INSULATION"]):
                contacts = read_csv_contacts(str(csv_file))
                logger.info(f"  {filename}: {len(contacts)} contacts")
                all_contacts.extend(contacts)
    
    logger.info(f"\nTotal contacts to verify: {len(all_contacts)}")
    
    if not all_contacts:
        logger.error("No contacts found")
        return
    
    # Limit
    if args.limit > 0:
        all_contacts = all_contacts[:args.limit]
        logger.info(f"Limited to {args.limit} contacts")
    
    # Verify
    if not args.report_only:
        logger.info(f"\n🔍 Starting verification (delay={args.delay}s)...\n")
        results = batch_verify(all_contacts, delay=args.delay)
        
        # Stats
        verified = sum(1 for r in results if r.get("cslb_status") not in ["not_verified", "no_data"])
        active = sum(1 for r in results if r.get("is_active"))
        critical = sum(1 for r in results if r.get("risk_level") == "CRITICAL")
        
        logger.info(f"\n{'='*60}")
        logger.info(f"VERIFICATION COMPLETE")
        logger.info(f"{'='*60}")
        logger.info(f"Total: {len(results)}")
        logger.info(f"Verified: {verified}")
        logger.info(f"Active licenses: {active}")
        logger.info(f"Critical (inactive/expired): {critical}")
    else:
        results = all_contacts
    
    # Save report
    save_report(results)
    
    # Push to Huly
    if args.push_huly:
        logger.info(f"\n📤 Pushing verified contractors to Huly CRM...")
        pushed = push_verified_to_huly(results)
        logger.info(f"Pushed {pushed} verified contractors to Huly")


if __name__ == "__main__":
    main()
