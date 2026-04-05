"""
Predict next building inspections based on construction phase and historical data.
Used as fallback when public calendar data is not available.
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Optional, List

logger = logging.getLogger(__name__)

# Standard construction inspection sequence
INSPECTION_SEQUENCE = [
    "foundation",
    "framing",
    "rough_mep",
    "insulation",
    "drywall",
    "final",
]

# Typical days between inspections (based on historical data)
DAYS_BETWEEN_INSPECTIONS = {
    "foundation": 7,     # Foundation → Framing (usually 7 days)
    "framing": 14,       # Framing → Rough MEP (2 weeks)
    "rough_mep": 10,     # Rough MEP → Insulation (1-2 weeks)
    "insulation": 7,     # Insulation → Drywall (1 week)
    "drywall": 14,       # Drywall → Final (2 weeks)
    "final": None,       # No more inspections
}

# Map inspection types to phases
PHASE_KEYWORDS = {
    "foundation": ["FOUNDATION", "FOOTING", "CONCRETE"],
    "framing": ["FRAMING", "FRAME", "WOOD", "STRUCTURAL"],
    "rough_mep": ["MEP", "MECHANICAL", "ELECTRICAL", "PLUMBING", "ROUGH"],
    "insulation": ["INSULATION", "INSULATE"],
    "drywall": ["DRYWALL", "GYPSUM", "SHEETROCK"],
    "final": ["FINAL", "COMPLETION", "OCCUPANCY", "CO"],
}


def predict_next_inspection(lead: Dict) -> Optional[Dict]:
    """
    Predict the next inspection for a lead based on current phase and history.

    Args:
        lead: Lead dictionary with fields like 'phase', 'phase_order', 'date'

    Returns:
        Dictionary with predicted inspection info or None if no prediction can be made
    """
    try:
        current_phase = lead.get("phase", "").lower()
        phase_order = lead.get("phase_order", 0)
        last_inspection_date = lead.get("date")

        if not current_phase or phase_order < 0:
            return None

        # Find next phase in sequence
        try:
            current_idx = INSPECTION_SEQUENCE.index(current_phase)
        except ValueError:
            logger.warning(f"Unknown phase: {current_phase}")
            return None

        if current_idx >= len(INSPECTION_SEQUENCE) - 1:
            # Already at final phase
            return None

        next_phase = INSPECTION_SEQUENCE[current_idx + 1]
        days_until = DAYS_BETWEEN_INSPECTIONS.get(current_phase, 14)

        # Calculate estimated date
        if last_inspection_date:
            try:
                if isinstance(last_inspection_date, str):
                    last_date = datetime.strptime(last_inspection_date, "%Y-%m-%d").date()
                else:
                    last_date = last_inspection_date
                estimated_date = last_date + timedelta(days=days_until)
            except (ValueError, TypeError):
                # If date parsing fails, use today + days
                estimated_date = date.today() + timedelta(days=days_until)
        else:
            estimated_date = date.today() + timedelta(days=days_until)

        # Estimate GC presence probability (high for inspection date)
        gc_probability = estimate_gc_presence(lead, estimated_date, next_phase)

        return {
            "inspection_type": next_phase.upper(),
            "estimated_date": estimated_date,
            "confidence": 0.6,  # Prediction confidence (vs public calendar data)
            "gc_probability": gc_probability,
            "reason": f"Next expected inspection after {current_phase} phase",
        }

    except Exception as e:
        logger.error(f"Error predicting inspection: {e}")
        return None


def estimate_gc_presence(
    lead: Dict,
    inspection_date: date,
    inspection_type: Optional[str] = None,
) -> float:
    """
    Estimate probability that GC (General Contractor) will be on-site.

    Args:
        lead: Lead dictionary
        inspection_date: Expected inspection date
        inspection_type: Type of inspection

    Returns:
        Probability 0.0-1.0 that GC will be present
    """
    try:
        # GC is usually present for critical inspections
        if inspection_type:
            inspection_type = inspection_type.upper()

            # High probability for major phases
            high_probability_phases = [
                "FOUNDATION",
                "FRAMING",
                "ROUGH_MEP",
                "FINAL",
            ]

            if any(phase in inspection_type for phase in high_probability_phases):
                return 0.85
            elif "INSULATION" in inspection_type:
                return 0.70
            elif "DRYWALL" in inspection_type:
                return 0.65
            else:
                return 0.60

        # Default: medium-high probability
        return 0.75

    except Exception as e:
        logger.warning(f"Error estimating GC presence: {e}")
        return 0.5


def classify_phase(lead: Dict) -> Optional[str]:
    """
    Classify current construction phase based on inspection history.

    Args:
        lead: Lead dictionary with inspection data

    Returns:
        Phase string or None
    """
    try:
        # Check if phase already classified
        if "phase" in lead and lead["phase"]:
            return lead["phase"].lower()

        # Try to infer from description
        description = (lead.get("description") or "").upper()

        for phase, keywords in PHASE_KEYWORDS.items():
            if any(kw in description for kw in keywords):
                return phase

        return None

    except Exception as e:
        logger.warning(f"Error classifying phase: {e}")
        return None


def get_next_inspection_date(lead: Dict) -> Optional[date]:
    """
    Get the next inspection date for a lead (prediction or from public calendar).

    Args:
        lead: Lead dictionary

    Returns:
        Next inspection date or None
    """
    try:
        # If we already have a scheduled inspection date from public calendar
        if "next_scheduled_inspection_date" in lead:
            scheduled = lead["next_scheduled_inspection_date"]
            if scheduled:
                if isinstance(scheduled, str):
                    return datetime.strptime(scheduled, "%Y-%m-%d").date()
                return scheduled

        # Otherwise predict
        prediction = predict_next_inspection(lead)
        if prediction:
            return prediction["estimated_date"]

        return None

    except Exception as e:
        logger.error(f"Error getting next inspection date: {e}")
        return None


def calculate_days_until_inspection(lead: Dict) -> Optional[int]:
    """Calculate days until next inspection."""
    next_date = get_next_inspection_date(lead)
    if next_date:
        return (next_date - date.today()).days
    return None


def is_inspection_soon(lead: Dict, days: int = 7) -> bool:
    """Check if next inspection is within N days."""
    days_until = calculate_days_until_inspection(lead)
    if days_until is None:
        return False
    return 0 <= days_until <= days
