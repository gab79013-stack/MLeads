"""
utils/google_ads_client.py
Google Ads REST client for MLeads marketing analytics.

Env vars:
    GOOGLE_ADS_DEVELOPER_TOKEN  - Developer token from Google Ads API Center
    GOOGLE_ADS_CLIENT_ID        - OAuth2 client ID
    GOOGLE_ADS_CLIENT_SECRET    - OAuth2 client secret
    GOOGLE_ADS_REFRESH_TOKEN    - Long-lived OAuth2 refresh token
    GOOGLE_ADS_CUSTOMER_ID      - Google Ads customer ID (digits only, no dashes)

Uses direct REST API calls — no google-ads Python SDK dependency.
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_ADS_API_VERSION = "v14"
_SEARCH_STREAM_URL = (
    "https://googleads.googleapis.com/{version}/customers/{customer_id}"
    "/googleAds:searchStream"
)

_CAMPAIGN_GAQL = """
SELECT
    campaign.id,
    campaign.name,
    campaign.status,
    campaign_budget.amount_micros,
    metrics.impressions,
    metrics.clicks,
    metrics.conversions,
    metrics.cost_micros,
    metrics.ctr,
    metrics.average_cpc,
    metrics.search_impression_share
FROM campaign
WHERE campaign.status = 'ENABLED'
AND segments.date DURING LAST_30_DAYS
""".strip()

# Required env var names
_REQUIRED_ENV_VARS = [
    "GOOGLE_ADS_DEVELOPER_TOKEN",
    "GOOGLE_ADS_CLIENT_ID",
    "GOOGLE_ADS_CLIENT_SECRET",
    "GOOGLE_ADS_REFRESH_TOKEN",
    "GOOGLE_ADS_CUSTOMER_ID",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def refresh_oauth_token() -> "str | None":
    """
    Exchange the stored refresh token for a short-lived OAuth2 access token.

    Sends a POST to https://oauth2.googleapis.com/token using:
        GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN

    Returns:
        str: New access token on success.
        None: If credentials are missing or the HTTP request fails.
    """
    client_id = os.environ.get("GOOGLE_ADS_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_ADS_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "")

    if not all([client_id, client_secret, refresh_token]):
        logger.warning(
            "Cannot refresh Google Ads OAuth2 token — "
            "GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, and "
            "GOOGLE_ADS_REFRESH_TOKEN must all be set."
        )
        return None

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    try:
        response = requests.post(_OAUTH_TOKEN_URL, data=payload, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "Google Ads OAuth2 token refresh HTTP error: %s — %s",
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else str(exc),
        )
        return None
    except requests.exceptions.RequestException as exc:
        logger.warning("Google Ads OAuth2 token refresh request failed: %s", exc)
        return None
    except ValueError as exc:
        logger.warning("Failed to parse Google Ads OAuth2 token response as JSON: %s", exc)
        return None

    access_token = data.get("access_token")
    if not access_token:
        logger.warning(
            "Google Ads OAuth2 response did not contain an access_token: %s", data
        )
        return None

    logger.debug("Successfully refreshed Google Ads OAuth2 access token.")
    return access_token


def get_campaign_performance() -> list:
    """
    Fetch all active campaigns' 30-day performance via the Google Ads REST API.

    Authenticates with a fresh OAuth2 token, then posts a GAQL query via the
    searchStream endpoint for the configured customer account.

    The GAQL query retrieves per-campaign aggregates over LAST_30_DAYS for all
    ENABLED campaigns:
        campaign.id, campaign.name, campaign.status,
        campaign_budget.amount_micros,
        metrics.impressions, metrics.clicks, metrics.conversions,
        metrics.cost_micros, metrics.ctr, metrics.average_cpc,
        metrics.search_impression_share

    Returns:
        list[dict]: Each dict has the following keys:
            campaign_id   (str)   – Google Ads campaign ID
            campaign_name (str)   – Human-readable campaign name
            status        (str)   – Campaign status (ENABLED, PAUSED, etc.)
            budget_daily  (float) – Daily budget in USD (converted from micros)
            impressions   (int)   – Total impressions in the period
            clicks        (int)   – Total clicks in the period
            conversions   (int)   – Total conversion count (rounded)
            spend         (float) – Total spend in USD (converted from micros)
            ctr           (float) – Click-through rate (0.0–1.0)
            cpc           (float) – Average cost-per-click in USD
            quality_score (int)   – Derived from search_impression_share (0–10)

    Returns an empty list (with a logged warning) when credentials are not
    configured or any unrecoverable API error occurs.
    """
    if not _credentials_present():
        logger.warning(
            "Google Ads credentials not fully configured — all of %s must be set. "
            "Returning empty campaign list.",
            ", ".join(_REQUIRED_ENV_VARS),
        )
        return []

    customer_id = os.environ["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")

    access_token = refresh_oauth_token()
    if not access_token:
        logger.warning(
            "Could not obtain Google Ads access token. Returning empty campaign list."
        )
        return []

    url = _SEARCH_STREAM_URL.format(
        version=_GOOGLE_ADS_API_VERSION,
        customer_id=customer_id,
    )
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "Content-Type": "application/json",
    }
    payload = {"query": _CAMPAIGN_GAQL}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        logger.warning(
            "Google Ads API HTTP error fetching campaign performance: %s — %s",
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else str(exc),
        )
        return []
    except requests.exceptions.RequestException as exc:
        logger.warning("Google Ads API request failed: %s", exc)
        return []

    # searchStream returns a JSON array of batch objects, each with a "results" key.
    try:
        batches = response.json()
    except ValueError as exc:
        logger.warning("Failed to parse Google Ads searchStream response as JSON: %s", exc)
        return []

    campaigns = []
    for batch in batches:
        for result in batch.get("results", []):
            parsed = _parse_campaign_result(result)
            if parsed is not None:
                campaigns.append(parsed)

    logger.info(
        "Google Ads: fetched %d active campaign(s) for customer %s.",
        len(campaigns),
        customer_id,
    )
    return campaigns


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _credentials_present() -> bool:
    """Return True only when all required env vars are non-empty strings."""
    return all(os.environ.get(var, "") for var in _REQUIRED_ENV_VARS)


def _micros_to_usd(micros) -> float:
    """Convert a Google Ads micro-currency value to USD (divide by 1,000,000)."""
    try:
        return round(int(micros) / 1_000_000, 6)
    except (TypeError, ValueError):
        return 0.0


def _parse_campaign_result(result: dict) -> "dict | None":
    """
    Parse a single result row from the Google Ads searchStream response.

    Args:
        result: One element from the ``results`` array in an API batch.

    Returns:
        Normalised campaign performance dict, or None if the row is malformed.
    """
    try:
        campaign_row = result.get("campaign", {})
        budget_row = result.get("campaignBudget", {})
        metrics_row = result.get("metrics", {})

        budget_micros = int(budget_row.get("amountMicros", 0))
        cost_micros = int(metrics_row.get("costMicros", 0))
        avg_cpc_micros = int(metrics_row.get("averageCpc", 0))

        # search_impression_share is a decimal string like "0.42";
        # map it to a 0–10 quality score proxy (multiply by 10, round).
        sis_raw = metrics_row.get("searchImpressionShare", "0")
        try:
            quality_score = int(round(float(sis_raw) * 10))
        except (TypeError, ValueError):
            quality_score = 0

        return {
            "campaign_id": str(campaign_row.get("id", "")),
            "campaign_name": str(campaign_row.get("name", "")),
            "status": str(campaign_row.get("status", "")),
            "budget_daily": _micros_to_usd(budget_micros),
            "impressions": int(metrics_row.get("impressions", 0)),
            "clicks": int(metrics_row.get("clicks", 0)),
            # conversions comes back as a float from the API (e.g. "3.0")
            "conversions": int(float(metrics_row.get("conversions", 0))),
            "spend": _micros_to_usd(cost_micros),
            "ctr": float(metrics_row.get("ctr", 0.0)),
            "cpc": _micros_to_usd(avg_cpc_micros),
            "quality_score": quality_score,
        }
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "Could not parse Google Ads campaign result row: %s — error: %s",
            result,
            exc,
        )
        return None
