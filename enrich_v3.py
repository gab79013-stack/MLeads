#!/usr/bin python3
"""
MLeads Phone Enrichment - DuckDuckGo search with contractor name matching
Simple, no browser-harness dependency, works with urllib only.
"""
import sqlite3, json, time, re, sys, urllib.request, urllib.parse

DB = '/opt/MLeads/data/leads.db'
SLEEP = 4  # seconds between requests (be nice to DuckDuckGo)

def get_leads(limit=50, cities=None):
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    sql = """
        SELECT address_key, city, lead_data
        FROM consolidated_leads 
        WHERE (has_phone = 0 OR has_phone IS NULL)
        AND COALESCE(is_dead_lead, 0) = 0
        AND length(json_extract(lead_data, '$.contractor')) > 5
    """
    params = []
    if cities:
        placeholders = ','.join('?' * len(cities))
        sql += f" AND city IN ({placeholders})"
        params.extend(cities)
    sql += " ORDER BY json_extract(lead_data, '$.value_float') DESC LIMIT ?"
    params.append(limit)
    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()
    return rows

def search_phone(name, city):
    """Search DuckDuckGo and extract phone numbers from results."""
    # Clean up name: "JOSE, REX L" -> "Rex Jose"
    clean_name = name
    if ',' in name:
        parts = [p.strip() for p in name.split(',')]
        if len(parts) >= 2:
            clean_name = f"{parts[1]} {parts[0]}"  # "REX L JOSE"
    
    # Remove LLC, INC etc for better search
    for suffix in [' LLC', ' INC', ' CORP', ' CO', ' LTD']:
        clean_name = clean_name.replace(suffix, '')
    clean_name = clean_name.strip()
    
    query = f'"{clean_name}" {city} contractor phone'
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
            'Accept': 'text/html',
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        
        # Check if contractor name appears in results (validation)
        name_parts = clean_name.lower().split()
        name_found = any(part in html.lower() for part in name_parts if len(part) > 3)
        
        if not name_found:
            return None  # Name not in results = low confidence
        
        # Extract US phone numbers
        phones = re.findall(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', html)
        toll_prefixes = {'800','888','877','866','855','844','833'}
        seen = set()
        for p in phones:
            digits = re.sub(r'\D', '', p)
            if len(digits) == 11 and digits.startswith('1'):
                digits = digits[1:]
            if (len(digits) == 10 and digits[:3] not in toll_prefixes 
                and digits not in seen and digits[0] in '23456789'):
                seen.add(digits)
        
        return list(seen)[:3]  # Return top 3 candidates
    except Exception as e:
        return None

def update_phone(address_key, phone):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (address_key,))
    row = c.fetchone()
    if row:
        ld = json.loads(row[0])
        formatted = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
        ld['contact_phone'] = formatted
        c.execute("UPDATE consolidated_leads SET lead_data = ?, has_phone = 1, has_contact = 1 WHERE address_key = ?",
                  (json.dumps(ld), address_key))
        conn.commit()
        r = formatted
    else:
        r = None
    conn.close()
    return r

if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    limit = 50
    for i, a in enumerate(sys.argv):
        if a == '--limit' and i+1 < len(sys.argv):
            limit = int(sys.argv[i+1])
    
    # Focus on our target cities
    cities = ['Chicago IL', 'Chicago (2018)', 'Chicago (2019)', 'Chicago (2020)', 'Chicago (2021)',
              'San Jose', 'Dallas', 'San Francisco', 'Honolulu']
    
    leads = get_leads(limit, cities)
    print(f"=== Phone Enrichment v3 (name-validated) ===")
    print(f"Mode: {'DRY RUN' if dry else 'LIVE'} | {len(leads)} leads in target cities")
    print()
    
    ok = 0
    for i, lead in enumerate(leads):
        ld = json.loads(lead['lead_data'])
        name = ld.get('contractor', '')
        city = lead['city']
        val = ld.get('value_float', 0)
        
        print(f"[{i+1}/{len(leads)}] ${val:,.0f} | {name} ({city})")
        
        phones = search_phone(name, city)
        if phones:
            phone = phones[0]
            fmt = f"({phone[:3]}) {phone[3:6]}-{phone[6:]}"
            if dry:
                print(f"  ✅ {fmt} (dry run)")
            else:
                saved = update_phone(lead['address_key'], phone)
                print(f"  ✅ Saved: {saved}")
            ok += 1
        else:
            print(f"  ❌ Not found / name not in results")
        
        time.sleep(SLEEP)
    
    print(f"\n=== {ok}/{len(leads)} enriched ===")
