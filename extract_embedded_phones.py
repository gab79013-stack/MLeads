#!/usr/bin/env python3
"""
Extract phone numbers that are already IN the contractor/lead_data text.
Honolulu permits include phone in the contractor field like:
  "COMPANY  State Lic: CT1234 / ID: 1234567 / PH: (808) 123-4567"
"""
import sqlite3, json, re, sys

DB = '/opt/MLeads/data/leads.db'

PHONE_RE = re.compile(r'(?:PH:\s*)?(\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})')
TOLL = {'800','888','877','866','855','844','833'}

def extract_phones(text):
    """Extract and validate phone numbers from text."""
    found = []
    seen = set()
    for m in PHONE_RE.finditer(text):
        raw = m.group(1)
        digits = re.sub(r'\D', '', raw)
        if len(digits) == 11 and digits.startswith('1'):
            digits = digits[1:]
        if (len(digits) == 10 and digits[:3] not in TOLL 
            and digits not in seen and digits[0] in '23456789'):
            seen.add(digits)
            found.append(digits)
    return found

def main():
    dry = '--dry-run' in sys.argv
    
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get leads without phone that might have it in the text
    c.execute("""
        SELECT address_key, city, lead_data
        FROM consolidated_leads 
        WHERE (has_phone = 0 OR has_phone IS NULL)
        AND COALESCE(is_dead_lead, 0) = 0
    """)
    rows = c.fetchall()
    print(f"Scanning {len(rows)} leads for embedded phone numbers...")
    
    updated = 0
    for row in rows:
        ld = json.loads(row['lead_data'])
        
        # Check multiple fields for embedded phones
        contractor = ld.get('contractor', '')
        description = ld.get('description', '')
        detail = ld.get('detail', '')
        owner = ld.get('owner', '')
        applicant = ld.get('applicant_name', '')
        
        # Combine all text fields
        all_text = f"{contractor} {description} {detail} {owner} {applicant}"
        
        phones = extract_phones(all_text)
        
        # Only use the first phone if contractor name contains "PH:" pattern
        if phones and ('PH:' in contractor or 'PH:' in all_text[:500]):
            phone = phones[0]
            formatted = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
            
            if dry:
                print(f"  {row['address_key'][:40]} | {contractor[:50]} -> {formatted} (dry)")
            else:
                ld['contact_phone'] = formatted
                c.execute("UPDATE consolidated_leads SET lead_data = ?, has_phone = 1, has_contact = 1 WHERE address_key = ?",
                          (json.dumps(ld), row['address_key']))
                print(f"  ✅ {row['address_key'][:40]} | {formatted}")
            updated += 1
        elif phones and len(contractor) > 10:
            # Less confident - contractor has a business name and we found a phone
            phone = phones[0]
            formatted = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
            if dry:
                print(f"  ? {row['address_key'][:40]} | {contractor[:50]} -> {formatted} (low conf, dry)")
            # Skip low confidence for now
    
    if not dry:
        conn.commit()
    conn.close()
    
    print(f"\n=== {updated} leads with embedded phones extracted ===")

if __name__ == '__main__':
    main()
