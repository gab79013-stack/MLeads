"""
utils/cslb_zip_search.py
🔍 CSLB ZIP Code Search — Busca contratistas con licencia por ZIP code

Usa la búsqueda pública de CSLB por ZIP code para encontrar contratistas
activos en el Bay Area. Luego los verifica y push a Huly.

URL: https://www.cslb.ca.gov/onlineservices/checklicenseII/ZipCodeSearch.aspx

Flujo:
  1. Para cada ZIP code del Bay Area
  2. Busca contratistas activos en CSLB
  3. Filtra por clasificación relevante (C-39, B, C-9, etc.)
  4. Verifica la licencia en detalle
  5. Push a Huly CRM
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime
from typing import List, Optional, Dict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CSLB_ZIP_URL = "https://www.cslb.ca.gov/onlineservices/checklicenseII/ZipCodeSearch.aspx"
CSLB_DETAIL_URL = "https://www.cslb.ca.gov/onlineservices/checklicenseII/LicenseDetail.aspx"

# Relevant CSLB classifications for MLeads
RELEVANT_CLASSES = {
    "C-39": "Roofing",
    "B": "General Building",
    "C-9": "Drywall",
    "C-33": "Painting",
    "C-10": "Electrical",
    "C-27": "Landscaping",
    "C-20": "HVAC",
    "C-36": "Plumbing",
    "C-8": "Concrete",
    "C-21": "Demolition",
    "C-2": "Insulation",
    "C-5": "Framing",
    "A": "Engineering",
}

# Bay Area ZIP codes (selected high-value areas)
BAY_AREA_ZIPS = [
    # Oakland / Alameda
    "94601", "94602", "94606", "94607", "94608", "94609", "94610", "94611", "94612",
    # Berkeley
    "94702", "94703", "94704", "94705", "94706", "94707", "94708", "94709", "94710",
    # San Francisco
    "94102", "94103", "94105", "94107", "94109", "94110", "94112", "94114", "94115", "94117", "94121", "94122", "94131", "94133", "94134",
    # San Jose
    "95110", "95112", "95113", "95116", "95117", "95118", "95121", "95122", "95123", "95125", "95126", "95128", "95131", "95132", "95134",
    # Concord / Walnut Creek
    "94518", "94519", "94520", "94521", "94523", "94524", "94526", "94530", "94595", "94596", "94597", "94598",
    # Richmond
    "94801", "94804", "94805", "94806",
    # Fremont / Hayward
    "94536", "94537", "94538", "94539", "94541", "94542", "94544", "94545", "94546", "94552", "94555", "94560", "94577", "94578", "94583", "94586", "94587",
    # Palo Alto / Mountain View / Sunnyvale
    "94040", "94041", "94043", "94085", "94086", "94087", "94088", "94301", "94302", "94303", "94304", "94305", "94306",
    # Daly City / San Mateo
    "94014", "94015", "94018", "94021", "94025", "94027", "94030", "94033", "94037", "94038", "94044", "94061", "94062", "94063", "94065", "94066", "94070", "94080",
    # Napa / Vallejo / Fairfield
    "94503", "94505", "94507", "94508", "94510", "94511", "94512", "94513", "94530", "94533", "94534", "94535", "94558", "94559", "94564", "94568", "94571", "94585", "94589", "94590", "94591", "94592",
    # Novato / San Rafael
    "94901", "94903", "94912", "94913", "94914", "94915", "94920", "94923", "94941", "94945", "94947", "94949", "94952", "94954", "94957", "94960", "94965",
    # Petaluma / Santa Rosa / Sonoma
    "94952", "94954", "95401", "95403", "95404", "95405", "95407", "95409", "95412", "95431", "95442", "95444", "95452", "95472", "95476",
]

# Limit for testing
MAX_ZIPS = int(os.getenv("CSLB_MAX_ZIPS", "5"))
REQUEST_DELAY = 3.0  # seconds between requests


class CSLBZipSearch:
    """Search for licensed contractors by ZIP code on CSLB."""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
    
    def search_zip(self, zip_code: str, classification: str = "") -> List[Dict]:
        """
        Search for contractors by ZIP code.
        Returns list of contractor dicts with license info.
        """
        results = []
        
        try:
            # Load the search page to get form fields
            resp = self.session.get(CSLB_ZIP_URL, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"[CSLB/ZIP/{zip_code}] Page load failed: {resp.status_code}")
                return []
            
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Extract ASP.NET form fields
            viewstate = soup.find("input", {"name": "__VIEWSTATE"})
            viewstategenerator = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
            eventvalidation = soup.find("input", {"name": "__EVENTVALIDATION"})
            
            if not viewstate:
                logger.debug(f"[CSLB/ZIP/{zip_code}] No form found")
                return []
            
            # Submit search
            form_data = {
                "__VIEWSTATE": viewstate.get("value", ""),
                "__VIEWSTATEGENERATOR": viewstategenerator.get("value", "") if viewstategenerator else "",
                "__EVENTVALIDATION": eventvalidation.get("value", "") if eventvalidation else "",
                "ctl00$MainContent$txtZipCode": zip_code,
                "ctl00$MainContent$btnSearch": "Search",
            }
            
            if classification:
                form_data["ctl00$MainContent$ddlClassification"] = classification
            
            time.sleep(REQUEST_DELAY)
            resp = self.session.post(CSLB_ZIP_URL, data=form_data, timeout=20)
            
            if resp.status_code != 200:
                return []
            
            # Parse results
            results = self._parse_results(resp.text, zip_code)
            
        except Exception as e:
            logger.debug(f"[CSLB/ZIP/{zip_code}] Error: {e}")
        
        return results
    
    def _parse_results(self, html: str, zip_code: str) -> List[Dict]:
        """Parse the search results page."""
        results = []
        soup = BeautifulSoup(html, "html.parser")
        
        # Find the results table
        table = soup.find("table", {"id": "ctl00_MainContent_grdResults"})
        if not table:
            # Try alternative
            table = soup.find("table", class_="results")
        if not table:
            # Look for any table with license data
            for t in soup.find_all("table"):
                if t.find("a", href=re.compile(r"LicNum")):
                    table = t
                    break
        
        if not table:
            return []
        
        for row in table.find_all("tr")[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            
            # Extract license number from link
            lic_link = cells[0].find("a")
            if lic_link:
                lic_num = re.sub(r'[^0-9]', '', lic_link.get_text())
                href = lic_link.get("href", "")
                if "LicNum=" in href:
                    lic_num = re.search(r"LicNum=(\d+)", href)
                    lic_num = lic_num.group(1) if lic_num else ""
            else:
                lic_num = re.sub(r'[^0-9]', '', cells[0].get_text())
            
            if not lic_num or len(lic_num) < 4:
                continue
            
            business_name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            city = cells[2].get_text(strip=True) if len(cells) > 2 else zip_code
            status = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            classification = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            
            results.append({
                "license_number": lic_num,
                "business_name": business_name,
                "city": city,
                "status": status.upper(),
                "classification": classification,
                "zip_code": zip_code,
                "source": "cslb_zip_search",
            })
        
        return results[:50]  # Max 50 per ZIP
    
    def search_bay_area(self, max_zips: int = 5, classifications: List[str] = None) -> List[Dict]:
        """
        Search for contractors across Bay Area ZIP codes.
        
        Args:
            max_zips: Maximum ZIP codes to search (for rate limiting)
            classifications: CSLB classifications to filter (e.g., ["C-39", "B"])
        """
        all_results = []
        zips = BAY_AREA_ZIPS[:max_zips]
        
        # Target classifications for construction leads
        if not classifications:
            classifications = list(RELEVANT_CLASSES.keys())
        
        for i, zip_code in enumerate(zips):
            logger.info(f"[{i+1}/{len(zips)}] Searching ZIP {zip_code}...")
            
            for cls_code in classifications[:3]:  # Limit to 3 classifications per ZIP
                results = self.search_zip(zip_code, cls_code)
                
                # Filter to only active licenses
                active = [r for r in results if r.get("status") in ("ACTIVE", "BOND EXEMPT ACTIVE", "")]
                all_results.extend(active)
                
                if results:
                    logger.info(f"  {cls_code}: {len(results)} results ({len(active)} active)")
            
            # Deduplicate by license number
            seen = set()
            unique = []
            for r in all_results:
                if r["license_number"] not in seen:
                    seen.add(r["license_number"])
                    unique.append(r)
            all_results = unique
        
        logger.info(f"Total unique contractors found: {len(all_results)}")
        return all_results
    
    def verify_and_push(self, contractors: List[Dict], push_huly: bool = True) -> Dict:
        """
        Verify contractors via CSLB and optionally push to Huly.
        """
        from utils.cslb_verifier import verify_license
        
        stats = {
            "total": len(contractors),
            "verified": 0,
            "active": 0,
            "pushed_huly": 0,
            "results": [],
        }
        
        for i, contractor in enumerate(contractors):
            lic = contractor.get("license_number", "")
            name = contractor.get("business_name", "")
            
            logger.info(f"[{i+1}/{len(contractors)}] Verifying {name[:40]} (lic={lic})")
            
            result = {**contractor, "verified_at": datetime.utcnow().isoformat()}
            
            # Verify license
            if lic:
                cslb_data = verify_license(lic)
                if cslb_data:
                    result["cslb_data"] = cslb_data
                    result["is_active"] = cslb_data.get("is_active", False)
                    result["risk_level"] = "LOW" if cslb_data.get("is_active") else "CRITICAL"
                    result["classification_codes"] = cslb_data.get("classification_codes", [])
                    result["workers_comp"] = cslb_data.get("workers_comp", False)
                    result["bond_amount"] = cslb_data.get("bond_amount", 0)
                    stats["verified"] += 1
                    if cslb_data.get("is_active"):
                        stats["active"] += 1
                else:
                    result["is_active"] = False
                    result["risk_level"] = "HIGH"
                    result["cslb_data"] = None
            
            # Push to Huly
            if push_huly and result.get("is_active"):
                try:
                    from utils.huly_crm import push_lead_to_crm
                    lead = {
                        "id": f"cslb_{lic}",
                        "address": "",
                        "city": contractor.get("city", "Bay Area"),
                        "contractor": name,
                        "contact_phone": "",
                        "description": f"CSLB Verified — {', '.join(result.get('classification_codes', []))}",
                        "value_float": 0,
                        "_trade": self._class_to_trade(result.get("classification_codes", [])),
                        "agent_sources": "cslb_verify",
                        "_agent_key": "cslb_verify",
                        "_tripartite": {
                            "subcontractor_score": 80,
                            "gc_score": 65,
                            "insurance_score": 50 if result.get("workers_comp") else 20,
                        },
                    }
                    push_result = push_lead_to_crm(lead, lead["_tripartite"])
                    if push_result and push_result.get("status") == "pushed":
                        stats["pushed_huly"] += 1
                        result["huly_pushed"] = True
                except Exception as e:
                    logger.debug(f"Huly push error: {e}")
            
            stats["results"].append(result)
            
            # Rate limit between verifications
            if i < len(contractors) - 1:
                time.sleep(2.5)
        
        return stats
    
    def _class_to_trade(self, codes: list) -> str:
        """Map CSLB classification codes to trade name."""
        code_to_trade = {
            "C-39": "ROOFING", "C-9": "DRYWALL", "C-33": "PAINTING",
            "C-10": "ELECTRICAL", "C-27": "LANDSCAPING", "C-20": "HVAC",
            "C-36": "PLUMBING", "C-8": "CONCRETE", "C-21": "DEMOLITION",
            "C-2": "INSULATION", "C-5": "FRAMING", "B": "GENERAL", "A": "ENGINEERING",
        }
        for code in codes:
            if code in code_to_trade:
                return code_to_trade[code]
        return "GENERAL"


def run_cslb_batch(max_zips: int = 5, push_huly: bool = True) -> Dict:
    """Run the full CSLB batch search + verify + push pipeline."""
    searcher = CSLBZipSearch()
    
    logger.info("Step 1: Searching ZIP codes...")
    contractors = searcher.search_bay_area(max_zips=max_zips)
    
    if not contractors:
        logger.warning("No contractors found")
        return {"total": 0, "verified": 0, "active": 0, "pushed_huly": 0}
    
    logger.info(f"Step 2: Verifying {len(contractors)} contractors...")
    stats = searcher.verify_and_push(contractors, push_huly=push_huly)
    
    return stats
