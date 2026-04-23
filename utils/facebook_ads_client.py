"""
utils/facebook_ads_client.py
Facebook Marketing API client for MLeads marketing analytics.

Env vars:
    FACEBOOK_MARKETING_ACCESS_TOKEN  - Long-lived Marketing API access token
    FACEBOOK_AD_ACCOUNT_ID           - Ad account ID (with or without "act_" prefix)

Uses Facebook Marketing API v17.0 via direct REST calls — no SDK dependency.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FB_API_VERSION = "v17.0"
_FB_GRAPH_BASE = f"https://graph.facebook.com/{_FB_API_VERSION}"

# action_type values that count as a conversion / lead event
_CONVERSION_ACTION_TYPES = frozenset(
    {
        "lead",
        "complete_registration",
        "offsite_conversion.fb_pixel_lead",
        "offsite_conversion.lead",
    }
)

# Insights fields requested at campaign level
_CAMPAIGN_INSIGHT_FIELDS = "impressions,clicks,spend,ctr,cpc,actions"

# Insights fields requested at ad level
_AD_INSIGHT_FIELDS = "impressions,clicks,spend,ctr,cpc,actions"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_campaign_performance() -> list:
    """
    Fetch performance insights for all campaigns in the configured ad account.

    Calls:
        GET https://graph.facebook.com/v17.0/act_{account_id}/campaigns
            ?fields=id,name,status,daily_budget,
                    insights{impressions,clicks,spend,ctr,cpc,actions}
            &access_token={token}

    conversions = sum of actions where action_type is 'lead',
                  'complete_registration', or 'offsite_conversion.fb_pixel_lead'.

    Returns:
        list[dict]: Each dict contains:
            campaign_id   (str)   – Facebook campaign ID
            campaign_name (str)   – Human-readable campaign name
            status        (str)   – Campaign effective status
            budget_daily  (float) – Daily budget in USD (daily_budget is in cents)
            impressions   (int)   – Impressions in the default insight window
            clicks        (int)   – Link clicks
            conversions   (int)   – Lead/registration conversion actions
            spend         (float) – Spend in USD
            ctr           (float) – Click-through rate as a percentage (e.g. 2.5)
            cpc           (float) – Cost per click in USD
            roas          (float) – Return on ad spend (0.0; not available here)

    Returns an empty list (with a logged warning) when credentials are not
    configured or any API error occurs.
    """
    if not _credentials_present():
        logger.warning(
            "Facebook Ads credentials not configured — "
            "FACEBOOK_MARKETING_ACCESS_TOKEN and FACEBOOK_AD_ACCOUNT_ID must "
            "be set. Returning empty campaign list."
        )
        return []

    access_token = os.environ["FACEBOOK_MARKETING_ACCESS_TOKEN"]
    account_id = _normalise_account_id(os.environ["FACEBOOK_AD_ACCOUNT_ID"])

    url = f"{_FB_GRAPH_BASE}/act_{account_id}/campaigns"
    params = {
        "fields": f"id,name,status,daily_budget,insights{{{_CAMPAIGN_INSIGHT_FIELDS}}}",
        "access_token": access_token,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "Facebook Ads API HTTP error fetching campaigns: %s — %s",
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else str(exc),
        )
        return []
    except requests.exceptions.RequestException as exc:
        logger.warning("Facebook Ads API request failed: %s", exc)
        return []
    except ValueError as exc:
        logger.warning("Failed to parse Facebook Ads API response as JSON: %s", exc)
        return []

    raw_campaigns = data.get("data", [])
    campaigns = []

    for camp in raw_campaigns:
        # insights is a paged sub-object; use the first result row if available
        insights_data = camp.get("insights", {}).get("data", [])
        insights = insights_data[0] if insights_data else {}

        conversions = _extract_conversions(insights.get("actions", []))

        # Facebook returns daily_budget in cents (integer string)
        budget_cents = _safe_int(camp.get("daily_budget", 0))
        budget_usd = budget_cents / 100.0

        campaigns.append(
            {
                "campaign_id": str(camp.get("id", "")),
                "campaign_name": str(camp.get("name", "")),
                "status": str(camp.get("status", "")),
                "budget_daily": round(budget_usd, 2),
                "impressions": _safe_int(insights.get("impressions", 0)),
                "clicks": _safe_int(insights.get("clicks", 0)),
                "conversions": conversions,
                "spend": _safe_float(insights.get("spend", 0.0)),
                "ctr": _safe_float(insights.get("ctr", 0.0)),
                "cpc": _safe_float(insights.get("cpc", 0.0)),
                # ROAS requires purchase/revenue value data not returned here
                "roas": 0.0,
            }
        )

    logger.info(
        "Facebook Ads: fetched %d campaign(s) for account %s.",
        len(campaigns),
        account_id,
    )
    return campaigns


def get_ad_performance(campaign_id: str) -> list:
    """
    Fetch individual ad performance within a specific campaign.

    Calls:
        GET https://graph.facebook.com/v17.0/{campaign_id}/ads
            ?fields=id,name,status,
                    insights{impressions,clicks,spend,ctr,cpc,actions}
            &access_token={token}

    Args:
        campaign_id: The Facebook campaign ID string.

    Returns:
        list[dict]: Each dict contains:
            ad_id         (str)   – Facebook ad ID
            ad_name       (str)   – Human-readable ad name
            status        (str)   – Ad effective status
            campaign_id   (str)   – Parent campaign ID (echoed from input)
            impressions   (int)
            clicks        (int)
            conversions   (int)   – Lead/registration conversion actions
            spend         (float) – USD
            ctr           (float) – Percentage (e.g. 2.5 = 2.5 %)
            cpc           (float) – USD per click

    Returns an empty list with a logged warning on missing credentials or error.
    """
    if not _credentials_present():
        logger.warning(
            "Facebook Ads credentials not configured — "
            "FACEBOOK_MARKETING_ACCESS_TOKEN and FACEBOOK_AD_ACCOUNT_ID must "
            "be set. Returning empty ad list."
        )
        return []

    if not campaign_id:
        logger.warning("get_ad_performance called with empty campaign_id.")
        return []

    access_token = os.environ["FACEBOOK_MARKETING_ACCESS_TOKEN"]
    url = f"{_FB_GRAPH_BASE}/{campaign_id}/ads"
    params = {
        "fields": f"id,name,status,insights{{{_AD_INSIGHT_FIELDS}}}",
        "access_token": access_token,
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "Facebook Ads API HTTP error fetching ads for campaign %s: %s — %s",
            campaign_id,
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else str(exc),
        )
        return []
    except requests.exceptions.RequestException as exc:
        logger.warning(
            "Facebook Ads API request failed for campaign %s: %s", campaign_id, exc
        )
        return []
    except ValueError as exc:
        logger.warning(
            "Failed to parse Facebook Ads API ad response for campaign %s as JSON: %s",
            campaign_id,
            exc,
        )
        return []

    raw_ads = data.get("data", [])
    ads = []

    for ad in raw_ads:
        insights_data = ad.get("insights", {}).get("data", [])
        insights = insights_data[0] if insights_data else {}

        conversions = _extract_conversions(insights.get("actions", []))

        ads.append(
            {
                "ad_id": str(ad.get("id", "")),
                "ad_name": str(ad.get("name", "")),
                "status": str(ad.get("status", "")),
                "campaign_id": campaign_id,
                "impressions": _safe_int(insights.get("impressions", 0)),
                "clicks": _safe_int(insights.get("clicks", 0)),
                "conversions": conversions,
                "spend": _safe_float(insights.get("spend", 0.0)),
                "ctr": _safe_float(insights.get("ctr", 0.0)),
                "cpc": _safe_float(insights.get("cpc", 0.0)),
            }
        )

    logger.info(
        "Facebook Ads: fetched %d ad(s) for campaign %s.", len(ads), campaign_id
    )
    return ads


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _credentials_present() -> bool:
    """Return True only when both required env vars are non-empty strings."""
    return bool(
        os.environ.get("FACEBOOK_MARKETING_ACCESS_TOKEN", "")
        and os.environ.get("FACEBOOK_AD_ACCOUNT_ID", "")
    )


def _normalise_account_id(raw_id: str) -> str:
    """
    Strip any leading 'act_' prefix from a Facebook ad account ID.

    Facebook's URL format uses 'act_{id}', but the env var may include the
    prefix. We always add it back ourselves to avoid double-prefixing.
    """
    return raw_id.lstrip("act_")


def _extract_conversions(actions: list) -> int:
    """
    Sum action counts whose action_type is a known conversion/lead event.

    Args:
        actions: List of action dicts from the Facebook insights API.
                 Each dict has at least ``action_type`` and ``value`` keys.

    Returns:
        Total integer conversion count across all matching action types.
    """
    total = 0
    for action in actions:
        if action.get("action_type") in _CONVERSION_ACTION_TYPES:
            total += _safe_int(action.get("value", 0))
    return total


def _safe_int(value) -> int:
    """Coerce *value* to int, returning 0 on any failure."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value) -> float:
    """Coerce *value* to float, returning 0.0 on any failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
