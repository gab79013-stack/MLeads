"""
utils/cslb_verifier.py
🔍 CSLB License Verifier — Verificación de licencias de contratistas CA

Implementa la verificación real de licencias CSLB usando el portal público:
  https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/LicenseDetail.aspx

Métodos:
  1. Direct URL lookup (número de licencia conocido)
  2. Search by name (búsqueda por nombre de negocio)
  3. Batch verification (verificar múltiples licencias)

Datos extraídos:
  - Status (Active, Expired, Suspended, Revoked, Inactive)
  - Classification codes (C-39, B, A, etc.)
  - Issue & expiration dates
  - Bond information
  - Workers' compensation insurance
  - Disciplinary actions
  - Business address & phone

Rate limiting: ~1 request/2 seconds (respectful scraping)
Cache: 7 days in SQLite (property_dna_cache table)

NOTA: Esto usa web scraping del portal público de CSLB.
Los datos son públicos pero el scraping debe ser respetuoso:
  - No más de 1 request por segundo
  - Cache agresivo para minimizar requests
  - User-agent identificable
  - Cumplir con robots.txt

Para uso en producción con alto volumen, contactar CSLB directamente
para acceso a datos bulk: https://www.cslb.ca.gov/Resources/OpenData/
"""

import os
import re
import json
import time
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
CSLB_DETAIL_URL = "https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/LicenseDetail.aspx"
CSLB_SEARCH_URL = "https://www.cslb.ca.gov/OnlineServices/CheckLicenseII/SearchResult.aspx"
CSLB_BASE_URL = "https://www.cslb.ca.gov"
CACHE_DAYS = 7
MIN_REQUEST_INTERVAL = 2.0  # seconds between requests

_last_request_time = 0.0


def _rate_limit():
    """Enforce minimum interval between requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


def _get_session() -> requests.Session:
    """Create a session with appropriate headers."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "MLeads/1.0 (Construction Lead Platform — license verification)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


# ── Primary Method: Direct License Lookup ──────────────────────────────────

def verify_license(license_number: str) -> Optional[Dict]:
    """
    Verify a CSLB license by number.
    
    Args:
        license_number: CSLB license number (e.g., "123456")
    
    Returns:
        Dict with license details, or None if not found.
        {
            "license_number": str,
            "status": str,           # ACTIVE, EXPIRED, SUSPENDED, etc.
            "is_active": bool,
            "business_name": str,
            "classification_codes": list[str],  # ["C-39", "B"]
            "classification_desc": list[str],   # ["Roofing", "General Building"]
            "issue_date": str,
            "expire_date": str,
            "bond_amount": float,
            "bond_effective": str,
            "workers_comp": bool,
            "workers_comp_effective": str,
            "workers_comp_expiration": str,
            "disciplinary_actions": int,
            "address": str,
            "city": str,
            "state": str,
            "zip": str,
            "phone": str,
            "entity_type": str,      # Sole Owner, Partnership, Corporation
            "source": "cslb",
            "verified_at": str,
        }
    """
    # Clean input
    lic = re.sub(r'[^0-9]', '', str(license_number))
    if not lic or len(lic) < 4:
        logger.debug(f"[CSLB] Invalid license number: {license_number}")
        return None

    # Check cache
    cached = _get_cached(lic)
    if cached:
        return cached

    # Rate limit
    _rate_limit()

    try:
        session = _get_session()
        
        # Step 1: Load the detail page (ASP.NET requires ViewState)
        resp = session.get(
            CSLB_DETAIL_URL,
            params={"LicNum": lic},
            timeout=20,
        )
        
        if resp.status_code != 200:
            logger.debug(f"[CSLB] HTTP {resp.status_code} for license {lic}")
            return None

        # Step 2: Parse the response
        data = _parse_license_page(resp.text, lic)
        
        if data:
            _cache(lic, data)
        
        return data

    except requests.Timeout:
        logger.warning(f"[CSLB] Timeout verifying license {lic}")
        return None
    except Exception as e:
        logger.debug(f"[CSLB] Error verifying license {lic}: {e}")
        return None


def search_by_name(business_name: str, city: str = "") -> List[Dict]:
    """
    Search for contractors by business name.
    
    Returns list of matching licenses (up to 10).
    """
    if not business_name or len(business_name) < 3:
        return []

    _rate_limit()

    try:
        session = _get_session()
        
        # Load search page to get ViewState
        resp = session.get(CSLB_SEARCH_URL, timeout=20)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Extract ASP.NET form fields
        viewstate = soup.find("input", {"name": "__VIEWSTATE"})
        viewstategenerator = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})
        eventvalidation = soup.find("input", {"name": "__EVENTVALIDATION"})
        
        if not viewstate:
            # Try alternative approach — direct URL search
            return _search_by_name_direct(business_name, city)

        # Submit search form
        form_data = {
            "__VIEWSTATE": viewstate.get("value", ""),
            "__VIEWSTATEGENERATOR": viewstategenerator.get("value", "") if viewstategenerator else "",
            "__EVENTVALIDATION": eventvalidation.get("value", "") if eventvalidation else "",
            "ctl00$MainContent$txtSearchType": "BusinessName",
            "ctl00$MainContent$txtBusinessName": business_name[:50],
            "ctl00$MainContent$btnSearch": "Search",
        }
        
        if city:
            form_data["ctl00$MainContent$txtCity"] = city

        _rate_limit()
        resp = session.post(CSLB_SEARCH_URL, data=form_data, timeout=20)
        
        if resp.status_code != 200:
            return []

        return _parse_search_results(resp.text)

    except Exception as e:
        logger.debug(f"[CSLB] Search error for '{business_name}': {e}")
        return []


def _search_by_name_direct(business_name: str, city: str = "") -> List[Dict]:
    """
    Alternative search method using CSLB's public search endpoint.
    Fallback when ASP.NET form submission fails.
    """
    try:
        session = _get_session()
        resp = session.get(
            CSLB_SEARCH_URL,
            params={
                "SearchType": "BusinessName",
                "BusinessName": business_name[:50],
                "City": city,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            return _parse_search_results(resp.text)
    except Exception as e:
        logger.debug(f"[CSLB] Direct search error: {e}")
    return []


# ── Parsing ────────────────────────────────────────────────────────────────

def _parse_license_page(html: str, license_number: str) -> Optional[Dict]:
    """Parse the CSLB license detail page."""
    soup = BeautifulSoup(html, "html.parser")
    
    # Check if we got a "license not found" page
    if "No license found" in html or "not found" in html.lower():
        return None

    data = {
        "license_number": license_number,
        "source": "cslb",
        "verified_at": datetime.utcnow().isoformat(),
    }

    # ── Business Name ─────────────────────────────────────────
    # Usually in a heading or specific div
    name_elem = (
        soup.find("span", id="MainContent_lblBusName") or
        soup.find("div", class_="license-header") or
        soup.find("h1") or
        soup.find("td", string=re.compile(r"Business Name", re.I))
    )
    if name_elem:
        data["business_name"] = _clean_text(name_elem.get_text())

    # ── Status ────────────────────────────────────────────────
    status_elem = (
        soup.find("span", id="MainContent_lblLicenseStatus") or
        soup.find("span", id="MainContent_lblStatus") or
        soup.find("td", string=re.compile(r"License Status", re.I))
    )
    if status_elem:
        status_text = _clean_text(status_elem.get_text())
        # Extract just the status value, not the label
        for valid_status in ["Active", "Expired", "Suspended", "Revoked", "Inactive", 
                             "Cancelled", "Bond Exempt Active", "Active-NFA"]:
            if valid_status.upper() in status_text.upper():
                data["status"] = valid_status.upper()
                break
        if "status" not in data:
            data["status"] = status_text.upper()

    data["is_active"] = data.get("status", "") in (
        "ACTIVE", "BOND EXEMPT ACTIVE", "ACTIVE-NFA"
    )

    # ── Classification ────────────────────────────────────────
    class_elem = (
        soup.find("span", id="MainContent_lblClassDesc") or
        soup.find("table", id="MainContent_grdClassifications") or
        soup.find("div", id="MainContent_pnlClass")
    )
    classifications = []
    class_descriptions = []
    if class_elem:
        # Parse classification table rows
        for row in class_elem.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                code = _clean_text(cells[0].get_text())
                desc = _clean_text(cells[1].get_text())
                if re.match(r'^[A]-\d+|[B]|[A-Z]{1,2}-\d+$', code):
                    classifications.append(code)
                    class_descriptions.append(desc)

    # Fallback: try to extract from text
    if not classifications:
        class_match = re.findall(r'([A-Z]-\d{1,2}|[AB])\s*[-–]\s*([A-Za-z\s]+)', html)
        for code, desc in class_match[:5]:
            if code not in classifications:
                classifications.append(code.strip())
                class_descriptions.append(desc.strip())

    data["classification_codes"] = classifications
    data["classification_desc"] = class_descriptions

    # ── Dates ─────────────────────────────────────────────────
    date_patterns = [
        (r"Issue\s*Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", "issue_date"),
        (r"Expire\s*Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", "expire_date"),
        (r"Effective\s*Date[:\s]+(\d{1,2}/\d{1,2}/\d{4})", "effective_date"),
    ]
    for pattern, key in date_patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            try:
                dt = datetime.strptime(match.group(1), "%m/%d/%Y")
                data[key] = dt.strftime("%Y-%m-%d")
            except ValueError:
                data[key] = match.group(1)

    # ── Bond ──────────────────────────────────────────────────
    bond_match = re.search(r"Bond\s*Amount[:\s]*\$?([\d,]+)", html, re.IGNORECASE)
    if bond_match:
        data["bond_amount"] = float(bond_match.group(1).replace(",", ""))

    bond_eff = re.search(r"Bond\s*Effective[:\s]+(\d{1,2}/\d{1,2}/\d{4})", html, re.IGNORECASE)
    if bond_eff:
        data["bond_effective"] = bond_eff.group(1)

    # ── Workers' Compensation ─────────────────────────────────
    wc_match = re.search(r"Workers['']?\s*Comp", html, re.IGNORECASE)
    if wc_match:
        data["workers_comp"] = True
        wc_eff = re.search(
            r"Workers['']?\s*Comp.*?Effective[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
            html, re.IGNORECASE | re.DOTALL
        )
        if wc_eff:
            data["workers_comp_effective"] = wc_eff.group(1)
        wc_exp = re.search(
            r"Workers['']?\s*Comp.*?Expire[sd]?[:\s]+(\d{1,2}/\d{1,2}/\d{4})",
            html, re.IGNORECASE | re.DOTALL
        )
        if wc_exp:
            data["workers_comp_expiration"] = wc_exp.group(1)
    else:
        data["workers_comp"] = False

    # ── Disciplinary Actions ──────────────────────────────────
    disc_elem = soup.find("a", id="MainContent_lnkDisciplinary")
    if disc_elem:
        disc_text = disc_elem.get_text()
        disc_match = re.search(r'(\d+)', disc_text)
        data["disciplinary_actions"] = int(disc_match.group(1)) if disc_match else 1
    else:
        # Check if there's a disciplinary section
        disc_section = soup.find("div", id="MainContent_pnlDisciplinary")
        data["disciplinary_actions"] = 1 if disc_section and disc_section.get_text(strip=True) else 0

    # ── Address ───────────────────────────────────────────────
    addr_elem = (
        soup.find("span", id="MainContent_lblBusAddr") or
        soup.find("span", id="MainContent_lblAddress")
    )
    if addr_elem:
        addr_text = _clean_text(addr_elem.get_text())
        # Parse: "123 MAIN ST, ANYTOWN, CA, 90001"
        parts = [p.strip() for p in addr_text.split(",")]
        if len(parts) >= 1:
            data["address"] = parts[0]
        if len(parts) >= 2:
            data["city"] = parts[1]
        if len(parts) >= 3:
            data["state"] = parts[2].strip()
        if len(parts) >= 4:
            data["zip"] = parts[3].strip()

    # ── Phone ─────────────────────────────────────────────────
    phone_elem = (
        soup.find("span", id="MainContent_lblPhone") or
        soup.find("a", href=re.compile(r"tel:"))
    )
    if phone_elem:
        phone_text = phone_elem.get_text()
        phone_clean = re.sub(r'[^\d]', '', phone_text)
        if len(phone_clean) >= 10:
            data["phone"] = f"({phone_clean[:3]}) {phone_clean[3:6]}-{phone_clean[6:10]}"

    # ── Entity Type ───────────────────────────────────────────
    entity_elem = soup.find("span", id="MainContent_lblEntityType")
    if entity_elem:
        data["entity_type"] = _clean_text(entity_elem.get_text())

    # If we got at least a business name, consider it a success
    if not data.get("business_name") and not data.get("status"):
        logger.debug(f"[CSLB] Could not parse license page for {license_number}")
        # Still return what we have — might be useful
        if len(data) <= 3:  # Only has license_number, source, verified_at
            return None

    return data


def _parse_search_results(html: str) -> List[Dict]:
    """Parse CSLB search results page."""
    results = []
    soup = BeautifulSoup(html, "html.parser")

    # Find result table rows
    table = soup.find("table", id="MainContent_grdSearchResults")
    if not table:
        # Try alternative: look for result links
        for link in soup.find_all("a", href=re.compile(r"LicNum=(\d+)")):
            lic = re.search(r"LicNum=(\d+)", link.get("href", ""))
            if lic:
                name = _clean_text(link.get_text())
                results.append({
                    "license_number": lic.group(1),
                    "business_name": name,
                    "source": "cslb_search",
                })
        return results[:10]

    for row in table.find_all("tr")[1:]:  # Skip header
        cells = row.find_all("td")
        if len(cells) >= 3:
            lic_link = cells[0].find("a")
            lic_num = _clean_text(cells[0].get_text())
            name = _clean_text(cells[1].get_text())
            status = _clean_text(cells[2].get_text()) if len(cells) > 2 else ""

            results.append({
                "license_number": re.sub(r'[^0-9]', '', lic_num),
                "business_name": name,
                "status": status.upper(),
                "source": "cslb_search",
            })

    return results[:10]


# ── Batch Verification ────────────────────────────────────────────────────

def batch_verify(license_numbers: List[str], delay: float = 2.5) -> Dict[str, Dict]:
    """
    Verify multiple CSLB licenses.
    
    Args:
        license_numbers: List of license number strings
        delay: Delay between requests (seconds) — default 2.5s for rate limiting
    
    Returns:
        Dict mapping license_number → verification result
    """
    results = {}
    
    for i, lic in enumerate(license_numbers):
        lic_clean = re.sub(r'[^0-9]', '', str(lic))
        if not lic_clean:
            continue
        
        result = verify_license(lic_clean)
        results[lic_clean] = result
        
        # Rate limiting between requests
        if i < len(license_numbers) - 1 and not _get_cached(lic_clean):
            time.sleep(delay)
    
    return results


def verify_subcontractor_profile(license_number: str, claimed_specialties: List[str]) -> Dict:
    """
    Verify a subcontractor's license against their claimed specialties.
    
    Args:
        license_number: CSLB license number
        claimed_specialties: List of trades they claim (e.g., ["ROOFING", "DRYWALL"])
    
    Returns:
        {
            "license": dict from verify_license,
            "specialty_matches": [{"trade": "ROOFING", "match": True, "classification": "C-39"}],
            "all_verified": bool,
            "unverified_trades": list[str],
            "risk_level": str,
        }
    """
    # Trade → CSLB classification mapping
    TRADE_TO_CSLB = {
        "DEMOLITION": ["C-21", "A", "B"],
        "PAINTING": ["C-33", "B"],
        "ROOFING": ["C-39", "B"],
        "INSULATION": ["C-2", "B"],
        "FRAMING": ["C-5", "B"],
        "CONCRETE": ["C-8", "A", "B"],
        "DRYWALL": ["C-9", "B"],
        "ELECTRICAL": ["C-10", "C-46"],
        "FLOORING": ["C-15", "B"],
        "WINDOWS": ["C-17", "B"],
        "HVAC": ["C-20", "B"],
        "LANDSCAPING": ["C-27"],
        "PLUMBING": ["C-36", "B"],
        "GENERAL": ["A", "B"],
        "SOLAR": ["C-10", "C-46", "B"],
    }

    result = {
        "license_number": license_number,
        "specialty_matches": [],
        "all_verified": False,
        "unverified_trades": [],
        "risk_level": "UNKNOWN",
    }

    # Verify license
    license_data = verify_license(license_number)
    result["license"] = license_data

    if not license_data:
        result["risk_level"] = "HIGH"
        result["unverified_trades"] = claimed_specialties
        return result

    # Check each claimed specialty
    lic_classes = license_data.get("classification_codes", [])
    all_match = True

    for trade in (claimed_specialties or []):
        valid_classes = TRADE_TO_CSLB.get(trade.upper(), ["A", "B"])
        match = any(cls in lic_classes for cls in valid_classes)
        
        matched_class = None
        for cls in valid_classes:
            if cls in lic_classes:
                matched_class = cls
                break

        result["specialty_matches"].append({
            "trade": trade,
            "match": match,
            "classification": matched_class,
        })

        if not match:
            all_match = False
            result["unverified_trades"].append(trade)

    result["all_verified"] = all_match

    # Calculate risk
    if not license_data.get("is_active"):
        result["risk_level"] = "CRITICAL"
    elif not all_match:
        result["risk_level"] = "HIGH"
    elif license_data.get("disciplinary_actions", 0) > 0:
        result["risk_level"] = "MEDIUM"
    else:
        result["risk_level"] = "LOW"

    return result


# ── Cache Management ──────────────────────────────────────────────────────

def _get_cached(license_number: str) -> Optional[Dict]:
    """Check cache for previously verified license."""
    try:
        import sqlite3
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        
        cutoff = (datetime.utcnow() - timedelta(days=CACHE_DAYS)).isoformat()
        c.execute("""
            SELECT result_data FROM cslb_cache
            WHERE license_number = ? AND cached_at > ?
        """, (license_number, cutoff))
        
        row = c.fetchone()
        conn.close()
        
        if row:
            return json.loads(row["result_data"])
    except Exception:
        pass
    return None


def _cache(license_number: str, data: Dict):
    """Cache verification result."""
    try:
        import sqlite3
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
        c = conn.cursor()
        
        c.execute("""
            CREATE TABLE IF NOT EXISTS cslb_cache (
                license_number TEXT PRIMARY KEY,
                result_data TEXT NOT NULL,
                cached_at TEXT NOT NULL
            )
        """)
        
        c.execute("""
            INSERT OR REPLACE INTO cslb_cache (license_number, result_data, cached_at)
            VALUES (?, ?, ?)
        """, (license_number, json.dumps(data, default=str), datetime.utcnow().isoformat()))
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[CSLB/Cache] {e}")


# ── Utilities ──────────────────────────────────────────────────────────────

def _clean_text(text: str) -> str:
    """Clean extracted text."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


# ── Integration with existing fraud_detector.py ───────────────────────────

def cslb_lookup(license_num: str = "", contractor_name: str = "") -> Dict:
    """
    Drop-in replacement for utils.lead_enrichment._cslb_lookup()
    Returns the same format expected by fraud_detector.py.
    """
    if license_num:
        result = verify_license(license_num)
        if result:
            return result

    if contractor_name:
        results = search_by_name(contractor_name)
        if results:
            # Verify the first match in detail
            first = results[0]
            if first.get("license_number"):
                result = verify_license(first["license_number"])
                if result:
                    return result
            # Return search result if verification fails
            return first

    return {}
