"""
score.py — Lead scoring helper functions

Provides score_lead, scoreEmoji, scoreLabel, scoreGrade helpers
used by multiple blueprints (leads, swipe, pipeline).
"""

def scoreEmoji(grade: str) -> str:
    """Return emoji for a score grade."""
    return {
        "A+": "🏆", "A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴", "F": "💀"
    }.get(grade, "❓")


def scoreLabel(grade: str) -> str:
    """Return human label for a score grade."""
    return {
        "A+": "Hot Lead", "A": "Strong Lead", "B": "Good Lead",
        "C": "Average", "D": "Weak", "F": "Dead"
    }.get(grade, "Unknown")


def scoreGrade(score: int) -> str:
    """Convert numeric score (0-100) to letter grade."""
    if score >= 95: return "A+"
    if score >= 85: return "A"
    if score >= 70: return "B"
    if score >= 50: return "C"
    if score >= 30: return "D"
    return "F"


def score_lead(lead_data: dict) -> dict:
    """
    Compute a simple lead score from lead_data dict.
    Returns dict with score, grade, grade_emoji, reasons.
    """
    # If already scored, return existing
    existing = lead_data.get("_scoring")
    if isinstance(existing, dict) and "score" in existing:
        return existing

    score = 15  # default
    reasons = []

    # Basic heuristics
    if lead_data.get("contact_phone"):
        score += 10
        reasons.append("Has phone number")
    if lead_data.get("description"):
        score += 10
        reasons.append("Has description")
    value = lead_data.get("value_float", 0)
    if value and value > 0:
        if value >= 50000:
            score += 20
            reasons.append(f"High value (${value:,.0f})")
        elif value >= 10000:
            score += 10
            reasons.append(f"Medium value (${value:,.0f})")

    grade = scoreGrade(score)
    return {
        "score": score,
        "grade": grade,
        "grade_emoji": scoreEmoji(grade),
        "reasons": reasons[:5],
    }
