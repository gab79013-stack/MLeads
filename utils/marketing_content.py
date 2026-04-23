"""
marketing_content.py – Content generation utilities using Claude Haiku.
Mirrors the pattern from utils/ai_outreach.py:
  - ANTHROPIC_API_KEY from env
  - Ephemeral prompt caching for system prompts
  - In-memory MD5 cache
  - Graceful fallback to template strings
"""

import os
import json
import hashlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Anthropic SDK import
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic_sdk
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic SDK not installed – all content will use fallback templates.")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-haiku-4-5"

# ---------------------------------------------------------------------------
# In-memory response cache  { md5_key: parsed_result }
# ---------------------------------------------------------------------------
_cache: dict = {}


def _make_key(*parts: str) -> str:
    combined = "|".join(str(p) for p in parts)
    return hashlib.md5(combined.encode()).hexdigest()


def _get_client():
    if not _ANTHROPIC_AVAILABLE or not ANTHROPIC_API_KEY:
        return None
    return _anthropic_sdk.Anthropic(api_key=ANTHROPIC_API_KEY)


def _call_claude(system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> Optional[str]:
    """
    Call Claude Haiku with ephemeral cache_control on the system prompt.
    Returns the raw text response or None on failure.
    """
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc)
        return None


def _parse_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    # Strip markdown fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Could not parse JSON from Claude response.")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_blog_post(keyword: str, city: str = "Bay Area", trade: str = None) -> dict:
    """
    Returns {"title": str, "meta": str, "body": str, "cta": str, "ai_source": str}
    """
    trade_str = trade or "general construction"
    cache_key = _make_key("blog_post", keyword, city, trade_str)
    if cache_key in _cache:
        return _cache[cache_key]

    system_prompt = (
        "You are an expert SEO content strategist for MLeads, a Bay Area construction lead "
        "generation platform serving 54 cities. You write blog posts that rank for terms contractors "
        "search. Tone: authoritative, practical. Output valid JSON only."
    )
    user_prompt = (
        f"Write a 600-word SEO blog post targeting the keyword: '{keyword}'. "
        f"City focus: {city}. Trade focus: {trade_str}. "
        "Include: H1 title, meta description (max 155 chars), 4 H2 sections with content, "
        "and a CTA to sign up for MLeads free trial. "
        'JSON schema: {"title": "...", "meta": "...", "body": "...", "cta": "..."}'
    )

    raw = _call_claude(system_prompt, user_prompt, max_tokens=2000)
    result = _parse_json(raw)

    if result and all(k in result for k in ("title", "meta", "body", "cta")):
        result["ai_source"] = "claude-haiku"
    else:
        # Fallback template
        result = {
            "title": f"Find More {trade_str.title()} Leads in {city} | MLeads",
            "meta": (
                f"Discover how {city} {trade_str} contractors use MLeads to find verified "
                f"construction leads. Search '{keyword}' and start your free trial today."
            )[:155],
            "body": (
                f"## Why {city} Contractors Trust MLeads\n\n"
                f"Finding quality {trade_str} leads in {city} is harder than ever. MLeads aggregates "
                f"permit data, HOA filings, and real estate signals across 54 cities so you never "
                f"miss a project opportunity.\n\n"
                f"## How to Use '{keyword}' to Win More Jobs\n\n"
                f"Contractors who optimize for keywords like '{keyword}' see 3-5x more inbound "
                f"inquiries. MLeads surfaces these opportunities before your competitors even know "
                f"they exist.\n\n"
                f"## The MLeads Advantage in {city}\n\n"
                f"Our AI scores each lead by project value, timeline, and decision-maker contact "
                f"availability. That means less cold calling and more real conversations.\n\n"
                f"## Getting Started\n\n"
                f"Sign up for MLeads, select {city} and the {trade_str} trade category, and receive "
                f"your first batch of warm leads within minutes."
            ),
            "cta": (
                f"Ready to find {trade_str} leads in {city}? "
                "Start your free MLeads trial today — no credit card required."
            ),
            "ai_source": "fallback_template",
        }

    _cache[cache_key] = result
    return result


def generate_case_study(
    trade: str, city: str, lead_count: int, total_value: float
) -> dict:
    """
    Returns {"headline": str, "subheadline": str, "body": str, "cta": str, "ai_source": str}
    """
    cache_key = _make_key("case_study", trade, city, str(lead_count), str(total_value))
    if cache_key in _cache:
        return _cache[cache_key]

    system_prompt = (
        "You are a B2B content marketer for MLeads. You write data-backed case studies that "
        "convert skeptical contractors into paying customers. Real numbers make your content "
        "credible. Always include an ROI angle."
    )
    user_prompt = (
        f"Create a 400-word case study for MLeads using this anonymized data: "
        f"Trade: {trade} contractor in {city}. Leads found in 30 days: {lead_count}. "
        f"Estimated project value: ${total_value:,.0f}. "
        f"Frame as: 'How a {city} {trade} contractor added {lead_count} warm leads to pipeline.' "
        'JSON: {"headline": "...", "subheadline": "...", "body": "...", "cta": "..."}'
    )

    raw = _call_claude(system_prompt, user_prompt, max_tokens=1500)
    result = _parse_json(raw)

    if result and all(k in result for k in ("headline", "subheadline", "body", "cta")):
        result["ai_source"] = "claude-haiku"
    else:
        result = {
            "headline": (
                f"How a {city} {trade.title()} Contractor Added "
                f"{lead_count} Warm Leads to Their Pipeline"
            ),
            "subheadline": (
                f"In just 30 days, this {trade} pro unlocked ${total_value:,.0f} in potential "
                f"project value using MLeads."
            ),
            "body": (
                f"### The Challenge\n\n"
                f"A {trade} contractor operating in {city} was spending 10+ hours a week hunting "
                f"for new projects through word of mouth and outdated directories. Despite a strong "
                f"reputation, their pipeline was inconsistent.\n\n"
                f"### The Solution\n\n"
                f"After signing up for MLeads, the contractor set up a {city} alert for {trade} "
                f"permit activity. Within 24 hours, their dashboard populated with actionable leads "
                f"scored by estimated project value and contact readiness.\n\n"
                f"### The Results\n\n"
                f"Over 30 days they identified {lead_count} qualified leads with a combined "
                f"estimated project value of ${total_value:,.0f}. They converted 3 projects in the "
                f"first month alone — more than paying for a full year of MLeads.\n\n"
                f"### Key Takeaway\n\n"
                f"MLeads turns passive permit data into active sales opportunities. For {trade} "
                f"contractors in {city}, that means a measurable ROI within the first billing cycle."
            ),
            "cta": (
                f"See how many {trade} leads are waiting for you in {city}. "
                "Start your free MLeads trial — no credit card required."
            ),
            "ai_source": "fallback_template",
        }

    _cache[cache_key] = result
    return result


def generate_ad_copy(
    trade: str,
    geography: str,
    usp: str,
    current_best: str = "",
    ctr: float = 0.0,
) -> list:
    """
    Returns list of 3 dicts:
    [{"headline1": str, "headline2": str, "description": str}, ...]
    """
    cache_key = _make_key("ad_copy", trade, geography, usp, current_best, str(ctr))
    if cache_key in _cache:
        return _cache[cache_key]

    system_prompt = (
        "You are a Google Ads specialist for MLeads, a SaaS lead generation platform "
        "for Bay Area construction contractors. Headlines under 30 chars, descriptions under 90 chars."
    )
    user_prompt = (
        f"Generate 3 Google Ads variants for {trade} contractors in {geography}, "
        f"emphasizing USP: {usp}. "
        f'Current best headline: "{current_best}" (CTR: {ctr:.2f}%). '
        "Return a JSON array of 3 objects, each with keys: headline1, headline2, description. "
        "All headlines must be under 30 characters. All descriptions under 90 characters. "
        'Example: [{"headline1": "...", "headline2": "...", "description": "..."}]'
    )

    raw = _call_claude(system_prompt, user_prompt, max_tokens=800)
    if raw:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            result = json.loads(text)
            if isinstance(result, list) and len(result) == 3:
                for item in result:
                    item.setdefault("ai_source", "claude-haiku")
                _cache[cache_key] = result
                return result
        except json.JSONDecodeError:
            pass

    # Fallback: 3 hardcoded template variants
    result = [
        {
            "headline1": f"{trade.title()} Leads – {geography}"[:30],
            "headline2": "Sign Up Free Today"[:30],
            "description": (
                f"{usp}. MLeads finds verified {trade} projects near you. No credit card needed."
            )[:90],
            "ai_source": "fallback_template",
        },
        {
            "headline1": f"More {trade.title()} Projects"[:30],
            "headline2": f"MLeads – {geography}"[:30],
            "description": (
                f"Stop chasing leads. MLeads delivers warm {trade} opportunities in {geography} daily."
            )[:90],
            "ai_source": "fallback_template",
        },
        {
            "headline1": f"{trade.title()} Contractor Leads"[:30],
            "headline2": "Try MLeads Free"[:30],
            "description": (
                f"{usp}. Join {geography} contractors already winning jobs with MLeads."
            )[:90],
            "ai_source": "fallback_template",
        },
    ]

    _cache[cache_key] = result
    return result


def generate_newsletter(stats: dict, top_cities: list, features: list) -> dict:
    """
    Returns {"subject": str, "html": str, "preview_text": str, "ai_source": str}

    stats example:   {"total_leads": 1200, "new_users": 45, "avg_lead_value": 8500}
    top_cities:      ["San Jose", "Oakland", "Fremont"]
    features:        ["New permit filter", "CSV export"]
    """
    cache_key = _make_key(
        "newsletter",
        json.dumps(stats, sort_keys=True),
        ",".join(top_cities),
        ",".join(features),
    )
    if cache_key in _cache:
        return _cache[cache_key]

    system_prompt = (
        "You are the email copywriter for MLeads. Emails feel like they're from "
        "a human founder, not a corporation. Tone: direct, helpful, construction-industry-aware."
    )

    stats_str = json.dumps(stats)
    cities_str = ", ".join(top_cities)
    features_str = "\n".join(f"- {f}" for f in features)

    user_prompt = (
        "Write a monthly newsletter email for MLeads subscribers. "
        f"Platform stats this month: {stats_str}. "
        f"Top active cities: {cities_str}. "
        f"New features / updates:\n{features_str}\n\n"
        "Include a compelling subject line, short preview text, and full HTML body "
        "(simple inline styles, no external CSS). "
        'JSON: {"subject": "...", "preview_text": "...", "html": "..."}'
    )

    raw = _call_claude(system_prompt, user_prompt, max_tokens=2500)
    result = _parse_json(raw)

    if result and all(k in result for k in ("subject", "preview_text", "html")):
        result["ai_source"] = "claude-haiku"
    else:
        total_leads = stats.get("total_leads", 0)
        new_users = stats.get("new_users", 0)
        avg_value = stats.get("avg_lead_value", 0)
        cities_display = ", ".join(top_cities[:3])
        features_html = "".join(f"<li>{f}</li>" for f in features)

        result = {
            "subject": f"MLeads Update: {total_leads:,} leads found this month",
            "preview_text": (
                f"{new_users} new contractors joined. Top markets: {cities_display}."
            ),
            "html": f"""<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#222;">
  <h2 style="color:#e67e22;">MLeads Monthly Update</h2>
  <p>Hey,</p>
  <p>Here's what happened on MLeads this month:</p>
  <ul>
    <li><strong>{total_leads:,} leads</strong> surfaced across the Bay Area</li>
    <li><strong>{new_users} new contractors</strong> joined the platform</li>
    <li>Average estimated lead value: <strong>${avg_value:,.0f}</strong></li>
  </ul>
  <h3>Top Active Cities</h3>
  <p>{cities_display}</p>
  <h3>What's New</h3>
  <ul>{features_html}</ul>
  <p>
    <a href="https://mleads.com/dashboard"
       style="background:#e67e22;color:#fff;padding:10px 20px;
              border-radius:4px;text-decoration:none;display:inline-block;">
      View Your Leads
    </a>
  </p>
  <p style="font-size:12px;color:#888;">
    You're receiving this because you signed up for MLeads.
    <a href="https://mleads.com/unsubscribe">Unsubscribe</a>
  </p>
</body>
</html>""",
            "ai_source": "fallback_template",
        }

    _cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=== generate_blog_post ===")
    blog = generate_blog_post("roofing contractor leads San Jose", city="San Jose", trade="roofing")
    print(f"ai_source : {blog['ai_source']}")
    print(f"title     : {blog['title']}")
    print(f"meta      : {blog['meta'][:80]}...")

    print("\n=== generate_case_study ===")
    cs = generate_case_study("plumbing", "Oakland", 28, 142000)
    print(f"ai_source  : {cs['ai_source']}")
    print(f"headline   : {cs['headline']}")

    print("\n=== generate_ad_copy ===")
    ads = generate_ad_copy("HVAC", "Bay Area", "verified permit leads", "Find HVAC Jobs Fast", 3.2)
    for i, ad in enumerate(ads, 1):
        print(f"  Variant {i}: {ad['headline1']} | {ad['headline2']}")

    print("\n=== generate_newsletter ===")
    nl = generate_newsletter(
        stats={"total_leads": 1450, "new_users": 61, "avg_lead_value": 9200},
        top_cities=["San Jose", "Oakland", "Fremont"],
        features=["CSV export", "New permit filter", "Mobile app beta"],
    )
    print(f"ai_source    : {nl['ai_source']}")
    print(f"subject      : {nl['subject']}")
    print(f"preview_text : {nl['preview_text']}")
