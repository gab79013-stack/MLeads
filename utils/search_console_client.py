"""
search_console_client.py – Google Search Console API client for MLeads.

Environment variables:
    GOOGLE_SEARCH_CONSOLE_SITE   – verified site URL (e.g. "https://mleads.com/")
    GOOGLE_SERVICE_ACCOUNT_JSON  – absolute path to a service-account JSON key file

Gracefully degrades to empty lists when the google-api-python-client library
is not installed or credentials are not configured.
"""

import os
import logging
from datetime import date, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GOOGLE_SEARCH_CONSOLE_SITE = os.getenv("GOOGLE_SEARCH_CONSOLE_SITE", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# Scope required for Search Console read-only access
_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# ---------------------------------------------------------------------------
# Lazy service
# ---------------------------------------------------------------------------
_service = None


def _get_service():
    """
    Return a Google Search Console API service resource, or None if
    not installed/configured.
    """
    global _service
    if _service is not None:
        return _service

    if not GOOGLE_SEARCH_CONSOLE_SITE:
        logger.warning("search_console_client: GOOGLE_SEARCH_CONSOLE_SITE not set.")
        return None

    if not GOOGLE_SERVICE_ACCOUNT_JSON or not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        logger.warning(
            "search_console_client: GOOGLE_SERVICE_ACCOUNT_JSON not set or file not found: %s",
            GOOGLE_SERVICE_ACCOUNT_JSON,
        )
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON,
            scopes=_SCOPES,
        )
        _service = build("webmasters", "v3", credentials=credentials, cache_discovery=False)
        return _service
    except ImportError:
        logger.warning(
            "search_console_client: google-api-python-client or google-auth not installed. "
            "Run: pip install google-api-python-client google-auth"
        )
        return None
    except Exception as exc:
        logger.error("search_console_client: failed to build service: %s", exc)
        return None


def _date_range(days: int):
    """Return (start_date_str, end_date_str) for the last *days* days."""
    end = date.today() - timedelta(days=1)          # yesterday
    start = end - timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_keyword_performance(days: int = 28) -> list:
    """
    Fetch top queries by clicks from Search Console.

    Args:
        days: lookback window in days (default 28)

    Returns:
        [
            {
                "keyword": str,
                "clicks": int,
                "impressions": int,
                "ctr": float,
                "position": float,
            },
            ...
        ]
        Ordered by clicks descending. Limit 50 results.
        Returns empty list on failure/not configured.
    """
    service = _get_service()
    if service is None:
        return []

    start_date, end_date = _date_range(days)

    try:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["query"],
            "rowLimit": 50,
            "startRow": 0,
        }
        response = (
            service.searchanalytics()
            .query(siteUrl=GOOGLE_SEARCH_CONSOLE_SITE, body=body)
            .execute()
        )

        rows = response.get("rows", [])
        result = []
        for row in rows:
            keys = row.get("keys", [""])
            result.append(
                {
                    "keyword": keys[0] if keys else "",
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": round(float(row.get("ctr", 0.0)), 4),
                    "position": round(float(row.get("position", 0.0)), 2),
                }
            )

        # Sort by clicks descending (API usually returns sorted, but be explicit)
        result.sort(key=lambda x: x["clicks"], reverse=True)
        return result

    except Exception as exc:
        logger.warning("search_console_client.get_keyword_performance failed: %s", exc)
        return []


def get_page_performance(days: int = 28) -> list:
    """
    Fetch top pages by clicks from Search Console.

    Args:
        days: lookback window in days (default 28)

    Returns:
        [
            {
                "url": str,
                "clicks": int,
                "impressions": int,
                "ctr": float,
                "position": float,
            },
            ...
        ]
        Limit 20 results. Returns empty list on failure/not configured.
    """
    service = _get_service()
    if service is None:
        return []

    start_date, end_date = _date_range(days)

    try:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": ["page"],
            "rowLimit": 20,
            "startRow": 0,
        }
        response = (
            service.searchanalytics()
            .query(siteUrl=GOOGLE_SEARCH_CONSOLE_SITE, body=body)
            .execute()
        )

        rows = response.get("rows", [])
        result = []
        for row in rows:
            keys = row.get("keys", [""])
            result.append(
                {
                    "url": keys[0] if keys else "",
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": round(float(row.get("ctr", 0.0)), 4),
                    "position": round(float(row.get("position", 0.0)), 2),
                }
            )

        result.sort(key=lambda x: x["clicks"], reverse=True)
        return result

    except Exception as exc:
        logger.warning("search_console_client.get_page_performance failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("=== get_keyword_performance (last 28 days) ===")
    keywords = get_keyword_performance(days=28)
    if keywords:
        print(f"Top {min(5, len(keywords))} keywords:")
        for kw in keywords[:5]:
            print(f"  {kw['keyword']!r}: {kw['clicks']} clicks, pos {kw['position']}")
    else:
        print("(no data – Search Console not configured or no organic traffic)")

    print("\n=== get_page_performance (last 28 days) ===")
    pages = get_page_performance(days=28)
    if pages:
        print(f"Top {min(5, len(pages))} pages:")
        for pg in pages[:5]:
            print(f"  {pg['url']}: {pg['clicks']} clicks, CTR {pg['ctr']:.2%}")
    else:
        print("(no data – Search Console not configured or no organic traffic)")

    # Demonstrate fallback shapes when credentials are absent
    print("\n=== Fallback shapes (no credentials) ===")
    print("get_keyword_performance() ->", get_keyword_performance())
    print("get_page_performance()    ->", get_page_performance())
