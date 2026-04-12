"""
utils/fraud_detector.py  (license_validator)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Contractor License Validator via CSLB

Validates contractor licenses extracted from leads:
  - Verifies active status
  - Confirms classification matches claimed trade (C-21/C-33/C-39/etc.)
  - Checks for disciplinary actions
  - Verifies bond and insurance
  - Flags expired or suspended licenses

Uses the CSLB lookup functions from lead_enrichment.py.

Usage:
    from utils.fraud_detector import validate_contractor_license
    result = validate_contractor_license("123456", expected_trade="DEMOLITION")
"""

import logging
from typing import Dict, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


# ── Trade → expected CSLB classification codes ──────────────────────

TRADE_VALID_CLASSIFICATIONS = {
    "DEMOLITION":  ["C-21", "A", "B"],   # C-21 or General (A/B) can demo
    "PAINTING":    ["C-33", "B"],
    "ROOFING":     ["C-39", "B"],
    "INSULATION":  ["C-2", "B"],
    "FRAMING":     ["C-5", "B"],
    "CONCRETE":    ["C-8", "A", "B"],
    "DRYWALL":     ["C-9", "B"],
    "ELECTRICAL":  ["C-10", "B"],
    "FLOORING":    ["C-15", "B"],
    "WINDOWS":     ["C-17", "B"],
    "HVAC":        ["C-20", "B"],
    "LANDSCAPING": ["C-27"],
    "PLUMBING":    ["C-36", "B"],
    "GENERAL":     ["A", "B"],
}


class ValidationResult:
    """Result of a contractor license validation."""

    def __init__(self):
        self.is_valid: bool = False
        self.license_number: str = ""
        self.business_name: str = ""
        self.status: str = ""
        self.classification: str = ""
        self.trade_match: bool = False
        self.has_bond: bool = False
        self.has_insurance: bool = False
        self.disciplinary_actions: int = 0
        self.warnings: List[str] = []
        self.risk_level: str = "UNKNOWN"  # LOW, MEDIUM, HIGH, CRITICAL

    def to_dict(self) -> Dict:
        return {
            "is_valid": self.is_valid,
            "license_number": self.license_number,
            "business_name": self.business_name,
            "status": self.status,
            "classification": self.classification,
            "trade_match": self.trade_match,
            "has_bond": self.has_bond,
            "has_insurance": self.has_insurance,
            "disciplinary_actions": self.disciplinary_actions,
            "warnings": self.warnings,
            "risk_level": self.risk_level,
        }


def validate_contractor_license(
    license_num: str = "",
    contractor_name: str = "",
    expected_trade: str = "",
) -> ValidationResult:
    """
    Validate a contractor's CSLB license.

    Args:
        license_num: CSLB license number
        contractor_name: Business name (fallback if no license #)
        expected_trade: Expected trade (e.g. "DEMOLITION") to verify classification

    Returns:
        ValidationResult with status, warnings, and risk level
    """
    result = ValidationResult()
    result.license_number = license_num

    if not license_num and not contractor_name:
        result.warnings.append("No license number or contractor name provided")
        result.risk_level = "HIGH"
        return result

    # Look up via CSLB
    try:
        from utils.lead_enrichment import _cslb_lookup
        cslb_data = _cslb_lookup(license_num, contractor_name)
    except Exception as e:
        logger.debug(f"[LicenseValidator] CSLB lookup failed: {e}")
        result.warnings.append("CSLB lookup failed — cannot validate")
        result.risk_level = "MEDIUM"
        return result

    if not cslb_data:
        result.warnings.append("No CSLB record found")
        result.risk_level = "HIGH"
        return result

    # Parse CSLB response
    result.license_number = cslb_data.get("license_number", license_num)
    result.business_name = cslb_data.get("business_name", "")
    result.status = cslb_data.get("status", "UNKNOWN")
    result.classification = cslb_data.get("classification_code", "")
    result.has_bond = bool(cslb_data.get("bond_amount", 0))
    result.has_insurance = cslb_data.get("has_insurance", False)
    result.disciplinary_actions = cslb_data.get("disciplinary_actions", 0)

    # ── Status check ─────────────────────────────────────────────
    if cslb_data.get("is_active"):
        result.is_valid = True
    else:
        result.warnings.append(f"License status: {result.status} (not active)")

    # ── Expiration check ─────────────────────────────────────────
    expire_str = cslb_data.get("expire_date", "")
    if expire_str:
        try:
            expire_date = datetime.strptime(expire_str[:10], "%Y-%m-%d")
            if expire_date < datetime.utcnow():
                result.is_valid = False
                result.warnings.append(f"License expired: {expire_str[:10]}")
        except (ValueError, TypeError):
            pass

    # ── Classification match ─────────────────────────────────────
    if expected_trade:
        valid_classes = TRADE_VALID_CLASSIFICATIONS.get(
            expected_trade.upper(), ["A", "B"]
        )
        if result.classification in valid_classes:
            result.trade_match = True
        else:
            result.trade_match = False
            result.warnings.append(
                f"Classification mismatch: has {result.classification}, "
                f"expected one of {valid_classes} for {expected_trade}"
            )

    # ── Bond/Insurance check ─────────────────────────────────────
    if not result.has_bond:
        result.warnings.append("No bond on file")
    if not result.has_insurance:
        result.warnings.append("No insurance on file")

    # ── Disciplinary actions ─────────────────────────────────────
    if result.disciplinary_actions > 0:
        result.warnings.append(
            f"{result.disciplinary_actions} disciplinary action(s) on record"
        )

    # ── Risk level calculation ───────────────────────────────────
    risk_score = 0
    if not result.is_valid:
        risk_score += 3
    if not result.trade_match and expected_trade:
        risk_score += 2
    if result.disciplinary_actions > 0:
        risk_score += min(result.disciplinary_actions, 3)
    if not result.has_bond:
        risk_score += 1
    if not result.has_insurance:
        risk_score += 1

    if risk_score >= 5:
        result.risk_level = "CRITICAL"
    elif risk_score >= 3:
        result.risk_level = "HIGH"
    elif risk_score >= 1:
        result.risk_level = "MEDIUM"
    else:
        result.risk_level = "LOW"

    return result


def validate_lead_contractor(lead: dict) -> Optional[Dict]:
    """
    Validate the contractor listed on a lead.
    Enriches the lead dict with _license_validation data.

    Args:
        lead: Lead dict with contractor, lic, _trade fields

    Returns:
        Validation dict or None if no contractor info available
    """
    license_num = lead.get("lic") or lead.get("contractor_license") or ""
    contractor_name = lead.get("contractor") or ""
    expected_trade = lead.get("_trade", "")

    if not license_num and not contractor_name:
        return None

    result = validate_contractor_license(
        license_num=str(license_num).strip(),
        contractor_name=contractor_name,
        expected_trade=expected_trade,
    )

    # Enrich lead
    lead["_license_valid"] = result.is_valid
    lead["_license_risk"] = result.risk_level
    lead["_license_warnings"] = result.warnings

    return result.to_dict()


def format_validation_for_telegram(result: Dict) -> str:
    """Format license validation result for Telegram message."""
    risk_emoji = {
        "LOW": "🟢",
        "MEDIUM": "🟡",
        "HIGH": "🟠",
        "CRITICAL": "🔴",
        "UNKNOWN": "⚪",
    }

    lines = [
        f"{risk_emoji.get(result.get('risk_level', ''), '⚪')} "
        f"*License: {result.get('license_number', 'N/A')}*",
        f"Status: {result.get('status', 'Unknown')}",
        f"Class: {result.get('classification', 'N/A')}",
    ]

    if result.get("business_name"):
        lines.append(f"Business: {result['business_name']}")

    if result.get("warnings"):
        lines.append("⚠️ " + " | ".join(result["warnings"][:3]))

    return "\n".join(lines)
