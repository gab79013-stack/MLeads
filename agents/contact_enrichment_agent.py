"""
agents/contact_enrichment_agent.py v5
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Contact Enrichment — uses DeepSeek AI (free via Vultr) to find contact info
from a company name, then validates by scraping the company website.

No blocked APIs — uses the same inference API already working for classification.
"""

import os, re, json, time, logging
from datetime import datetime
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://api.vultrinference.com/v1")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_PHONE_RE = re.compile(r'(?:\+1\s?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}')
JUNK = {"example.com","test.com","domain.com","sentry.io","wix.com","wordpress.org",
    "google.com","facebook.com","instagram.com","linkedin.com","yelp.com",
    "hotmail.com","gmail.com","yahoo.com","mailchimp.com","hubspot.com"}

def _clean_phone(raw: str) -> str:
    if not raw: return ""
    digits = re.sub(r'\D', '', raw)
    if len(digits) == 11 and digits.startswith('1'): digits = digits[1:]
    if len(digits) == 10: return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return raw.strip() if len(digits) >= 7 else ""

def _extract_emails(text: str) -> list[str]:
    return list(dict.fromkeys(e.lower() for e in _EMAIL_RE.findall(text)
        if not any(j in e.lower() for j in JUNK)
        and not e.startswith(("noreply","no-reply","abuse","admin@"))))[:5]

def _extract_phones(text: str) -> list[str]:
    return list(dict.fromkeys(_clean_phone(p) for p in _PHONE_RE.findall(text) if _clean_phone(p)))[:3]

def _get(url: str, timeout: int = 8) -> requests.Response | None:
    try:
        return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    except:
        return None


# ── Source 1: AI-powered contact search (DeepSeek via Vultr) ──────

def _ai_find_contacts(company: str, city: str = "", state: str = "") -> dict:
    """Use DeepSeek AI to find contact info for a construction company."""
    info = {"emails": [], "phones": [], "website": "", "key_contacts": [], "source": "ai"}
    
    prompt = f"""Find contact information for the construction company "{company}" in {city}, {state}. 

Return a JSON object with these fields:
- phone: the company's main phone number (US format like (555) 123-4567)
- email: the company's email address  
- website: the company website domain (e.g. example.com)
- owner: name of the owner/president/principal
- license: contractor license number if known

Only include information you are confident about. Use null for fields you cannot find.

Respond with ONLY the JSON, no other text."""

    try:
        resp = requests.post(
            f"{QWEN_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "DeepSeek-V3-0324",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
                "max_tokens": 300,
            },
            timeout=15,
        )
        
        if not resp.ok:
            logger.debug(f"AI search failed: {resp.status_code}")
            return info
        
        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Parse JSON from response
        # Try to find JSON in the response
        json_match = re.search(r'\{[^{}]+\}', content, re.DOTALL)
        if not json_match:
            return info
        
        result = json.loads(json_match.group())
        
        if result.get("phone"):
            cleaned = _clean_phone(str(result["phone"]))
            if cleaned: info["phones"].append(cleaned)
        
        if result.get("email"):
            email = str(result["email"]).strip().lower()
            if "@" in email and not any(j in email for j in JUNK):
                info["emails"].append(email)
        
        if result.get("website"):
            domain = str(result["website"]).strip().lower()
            domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").split("/")[0]
            if domain and "." in domain:
                info["website"] = domain
        
        if result.get("owner"):
            name = str(result["owner"]).strip()
            if len(name) > 3:
                email = ""
                if info["website"]:
                    parts = name.lower().split()
                    email = f"{parts[0]}.{parts[-1]}@{info['website']}" if len(parts) >= 2 else ""
                info["key_contacts"].append({
                    "name": name, "role": "Owner/Principal", "email": email, "source": "ai_search"
                })
        
        if result.get("license"):
            info["license"] = str(result["license"]).strip()
    
    except Exception as e:
        logger.debug(f"AI search failed: {e}")
    
    return info


# ── Source 2: Company website scraping ────────────────────────────

def _scrape_contact_page(domain: str) -> dict:
    """Scrape a company's website for contact info."""
    info = {"emails": [], "phones": []}
    if not domain: return info
    
    for path in ["", "/contact", "/about", "/contact-us"]:
        url = f"https://{domain}{path}"
        resp = _get(url, timeout=6)
        if not resp or not resp.ok or len(resp.text) < 100:
            continue
        
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        
        text = soup.get_text(separator=" ")
        info["emails"].extend(_extract_emails(text))
        info["phones"].extend(_extract_phones(text))
        
        # mailto: and tel: links
        for a in soup.find_all("a", href=True):
            if a["href"].startswith("mailto:"):
                e = a["href"].replace("mailto:", "").split("?")[0].strip()
                if e and "@" in e and not any(j in e.lower() for j in JUNK):
                    info["emails"].append(e.lower())
            if a["href"].startswith("tel:"):
                p = _clean_phone(a["href"].replace("tel:", ""))
                if p: info["phones"].append(p)
        
        if info["emails"] or info["phones"]:
            break
        time.sleep(0.3)
    
    info["emails"] = list(dict.fromkeys(info["emails"]))[:5]
    info["phones"] = list(dict.fromkeys(info["phones"]))[:3]
    return info


def _find_key_contacts(company: str, domain: str = "") -> list[dict]:
    """Find key contacts from website about/team page."""
    contacts = []
    if not domain: return contacts
    
    try:
        for path in ["/about", "/team", "/about-us"]:
            resp = _get(f"https://{domain}{path}", timeout=6)
            if not resp or not resp.ok: continue
            
            text = BeautifulSoup(resp.text, "lxml").get_text(separator=" ")
            for pat in [
                r'([A-Z][a-z]+ [A-Z][a-z]+)[,\s]+(?:Owner|President|Founder|CEO|Principal|VP|Estimator|Superintendent|Project Manager)',
                r'(?:Owner|President|Founder|CEO|Principal)[:\s]+([A-Z][a-z]+ [A-Z][a-z]+)',
            ]:
                for name in re.findall(pat, text)[:3]:
                    if len(name) > 4 and name not in [c["name"] for c in contacts]:
                        parts = name.lower().split()
                        email = f"{parts[0]}.{parts[-1]}@{domain}" if len(parts) >= 2 else ""
                        contacts.append({"name": name, "role": "Owner/Principal", "email": email, "source": "website"})
            if contacts: break
    except:
        pass
    
    return contacts[:5]


# ── Main ───────────────────────────────────────────────────────────

def enrich_lead(lead_data: dict) -> dict:
    """Enrich a lead with contact information using AI + web scraping."""
    company = (lead_data.get("contractor") or "").strip()
    city = (lead_data.get("city") or "").strip()
    state = (lead_data.get("state") or "").strip()
    existing_phone = (lead_data.get("contact_phone") or "").strip()
    existing_email = (lead_data.get("contact_email") or "").strip()
    
    if not company or company in ("NONE", "None", "N/A", ""):
        return lead_data
    
    if existing_phone and existing_email:
        return lead_data
    
    all_emails = []
    all_phones = []
    website = ""
    key_contacts = []
    license_num = ""
    
    # Step 1: AI-powered search (DeepSeek knows about real companies)
    ai = _ai_find_contacts(company, city, state)
    all_emails.extend(ai.get("emails", []))
    all_phones.extend(ai.get("phones", []))
    website = ai.get("website", "")
    key_contacts = ai.get("key_contacts", [])
    if ai.get("license"):
        license_num = ai["license"]
    
    # Step 2: Validate by scraping the actual website
    if website:
        domain = website.replace("www.", "")
        site = _scrape_contact_page(domain)
        all_emails.extend(site.get("emails", []))
        all_phones.extend(site.get("phones", []))
        key_contacts.extend(_find_key_contacts(company, domain))
    else:
        # Guess domain
        slug = re.sub(r'[^a-z0-9]+', '', company.lower())
        for suffix in [".com", "construction.com", "inc.com", "contracting.com", "builders.com"]:
            test = f"{slug}{suffix}"
            try:
                resp = requests.head(f"https://{test}", headers=HEADERS, timeout=3, allow_redirects=True)
                if resp.ok:
                    website = test
                    site = _scrape_contact_page(test)
                    all_emails.extend(site.get("emails", []))
                    all_phones.extend(site.get("phones", []))
                    key_contacts.extend(_find_key_contacts(company, test))
                    break
            except:
                continue
    
    # Deduplicate
    all_emails = list(dict.fromkeys(e.lower() for e in all_emails if e))[:5]
    all_phones = list(dict.fromkeys(p for p in all_phones if p))[:3]
    key_contacts = list({c["name"]: c for c in key_contacts}.values())[:5]
    
    # Update lead
    if not existing_email and all_emails:
        lead_data["contact_email"] = all_emails[0]
        lead_data["enrichment_emails"] = all_emails
    
    if not existing_phone and all_phones:
        lead_data["contact_phone"] = all_phones[0]
        lead_data["enrichment_phones"] = all_phones
    
    if website:
        lead_data["website"] = website
    
    if key_contacts:
        lead_data["key_contacts"] = key_contacts
    
    if license_num and not lead_data.get("license_number"):
        lead_data["license_number"] = license_num
    
    lead_data["_enrichment_source"] = "contact_enrichment_agent"
    lead_data["_enrichment_date"] = datetime.utcnow().isoformat()
    
    return lead_data


class ContactEnrichmentAgent:
    name = "contact_enrichment"
    description = "Enriches leads with emails, phones, and key contacts using AI (DeepSeek) + web scraping"
    
    def enrich_lead(self, lead_data: dict) -> dict:
        return enrich_lead(lead_data)
