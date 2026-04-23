"""
ga4_client.py – Google Analytics 4 Data API client for MLeads.

Environment variables:
    GA4_PROPERTY_ID            – GA4 numeric property ID (e.g. "123456789")
    GOOGLE_SERVICE_ACCOUNT_JSON – absolute path to a service-account JSON key file

Gracefully degrades to zero-value dicts when the google-analytics-data library
is not installed or credentials are not configured.
"""

import os
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")

# ---------------------------------------------------------------------------
# Lazy client
# ---------------------------------------------------------------------------
_client = None


def _get_client():
    """Return a BetaAnalyticsDataClient or None if not available/configured."""
    global _client
    if _client is not None:
        return _client

    if not GA4_PROPERTY_ID:
        logger.warning("ga4_client: GA4_PROPERTY_ID not set.")
        return None

    if not GOOGLE_SERVICE_ACCOUNT_JSON or not os.path.exists(GOOGLE_SERVICE_ACCOUNT_JSON):
        logger.warning(
            "ga4_client: GOOGLE_SERVICE_ACCOUNT_JSON not set or file not found: %s",
            GOOGLE_SERVICE_ACCOUNT_JSON,
        )
        return None

    try:
        from google.analytics.data.v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"],
        )
        _client = BetaAnalyticsDataClient(credentials=credentials)
        return _client
    except ImportError:
        logger.warning(
            "ga4_client: google-analytics-data library not installed. "
            "Run: pip install google-analytics-data"
        )
        return None
    except Exception as exc:
        logger.error("ga4_client: failed to build client: %s", exc)
        return None


def _property() -> str:
    return f"properties/{GA4_PROPERTY_ID}"


def _zero_daily() -> dict:
    return {
        "sessions": 0,
        "users": 0,
        "new_users": 0,
        "pageviews": 0,
        "bounce_rate": 0.0,
        "avg_session_dur": 0.0,
        "conversions": 0,
        "conversion_rate": 0.0,
        "organic_traffic": 0,
        "paid_traffic": 0,
        "direct_traffic": 0,
        "social_traffic": 0,
        "source_data": {"note": "GA4 not configured"},
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_daily_metrics(date_str: str = None) -> dict:
    """
    Fetch key metrics for a single day from GA4.

    Args:
        date_str: YYYY-MM-DD string. Defaults to yesterday.

    Returns:
        {
            "sessions": int,
            "users": int,
            "new_users": int,
            "pageviews": int,
            "bounce_rate": float,
            "avg_session_dur": float,
            "conversions": int,
            "conversion_rate": float,
            "organic_traffic": int,
            "paid_traffic": int,
            "direct_traffic": int,
            "social_traffic": int,
            "source_data": dict,
        }
    """
    if date_str is None:
        date_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    client = _get_client()
    if client is None:
        return _zero_daily()

    try:
        from google.analytics.data.v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            RunReportRequest,
            FilterExpression,
            Filter,
        )

        # ---- Core session/user metrics ----
        req = RunReportRequest(
            property=_property(),
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            metrics=[
                Metric(name="sessions"),
                Metric(name="totalUsers"),
                Metric(name="newUsers"),
                Metric(name="screenPageViews"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
                Metric(name="conversions"),
                Metric(name="sessionConversionRate"),
            ],
        )
        resp = client.run_report(req)

        row = resp.rows[0].metric_values if resp.rows else None
        sessions = int(row[0].value) if row else 0
        users = int(row[1].value) if row else 0
        new_users = int(row[2].value) if row else 0
        pageviews = int(row[3].value) if row else 0
        bounce_rate = float(row[4].value) if row else 0.0
        avg_session_dur = float(row[5].value) if row else 0.0
        conversions = int(float(row[6].value)) if row else 0
        conversion_rate = float(row[7].value) if row else 0.0

        # ---- Traffic by channel ----
        channel_req = RunReportRequest(
            property=_property(),
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            dimensions=[Dimension(name="sessionDefaultChannelGrouping")],
            metrics=[Metric(name="sessions")],
        )
        channel_resp = client.run_report(channel_req)

        channel_map = {}
        for ch_row in channel_resp.rows:
            channel_name = ch_row.dimension_values[0].value.lower()
            ch_sessions = int(ch_row.metric_values[0].value)
            channel_map[channel_name] = ch_sessions

        organic_traffic = channel_map.get("organic search", 0)
        paid_traffic = channel_map.get("paid search", 0)
        direct_traffic = channel_map.get("direct", 0)
        social_traffic = channel_map.get("organic social", 0) + channel_map.get("paid social", 0)

        return {
            "sessions": sessions,
            "users": users,
            "new_users": new_users,
            "pageviews": pageviews,
            "bounce_rate": round(bounce_rate, 4),
            "avg_session_dur": round(avg_session_dur, 2),
            "conversions": conversions,
            "conversion_rate": round(conversion_rate, 4),
            "organic_traffic": organic_traffic,
            "paid_traffic": paid_traffic,
            "direct_traffic": direct_traffic,
            "social_traffic": social_traffic,
            "source_data": channel_map,
        }

    except Exception as exc:
        logger.error("ga4_client.get_daily_metrics failed: %s", exc)
        result = _zero_daily()
        result["source_data"] = {"note": str(exc)}
        return result


def get_top_pages(date_str: str = None, limit: int = 10) -> list:
    """
    Return top pages by sessions for a given date.

    Args:
        date_str: YYYY-MM-DD (default: yesterday)
        limit:    number of rows to return (default 10)

    Returns:
        [
            {
                "page_path": str,
                "sessions": int,
                "pageviews": int,
                "bounce_rate": float,
                "avg_time": float,
            },
            ...
        ]
    """
    if date_str is None:
        date_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    client = _get_client()
    if client is None:
        return []

    try:
        from google.analytics.data.v1beta.types import (
            DateRange,
            Dimension,
            Metric,
            OrderBy,
            RunReportRequest,
        )

        req = RunReportRequest(
            property=_property(),
            date_ranges=[DateRange(start_date=date_str, end_date=date_str)],
            dimensions=[Dimension(name="pagePath")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="screenPageViews"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
            ],
            order_bys=[
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name="sessions"),
                    desc=True,
                )
            ],
            limit=limit,
        )
        resp = client.run_report(req)

        pages = []
        for row in resp.rows:
            pages.append(
                {
                    "page_path": row.dimension_values[0].value,
                    "sessions": int(row.metric_values[0].value),
                    "pageviews": int(row.metric_values[1].value),
                    "bounce_rate": round(float(row.metric_values[2].value), 4),
                    "avg_time": round(float(row.metric_values[3].value), 2),
                }
            )
        return pages

    except Exception as exc:
        logger.error("ga4_client.get_top_pages failed: %s", exc)
        return []


def get_weekly_comparison() -> dict:
    """
    Compare this week vs last week: sessions, users, conversions.

    Returns:
        {
            "this_week":  {"sessions": int, "users": int, "conversions": int},
            "last_week":  {"sessions": int, "users": int, "conversions": int},
            "change_pct": {"sessions": float, "users": float, "conversions": float},
        }
    """
    today = date.today()
    # This week: last 7 days (not including today)
    this_end = today - timedelta(days=1)
    this_start = today - timedelta(days=7)
    # Last week: 7 days before that
    last_end = today - timedelta(days=8)
    last_start = today - timedelta(days=14)

    def _fetch_week(start: date, end: date) -> dict:
        client = _get_client()
        if client is None:
            return {"sessions": 0, "users": 0, "conversions": 0}
        try:
            from google.analytics.data.v1beta.types import (
                DateRange,
                Metric,
                RunReportRequest,
            )

            req = RunReportRequest(
                property=_property(),
                date_ranges=[
                    DateRange(
                        start_date=start.strftime("%Y-%m-%d"),
                        end_date=end.strftime("%Y-%m-%d"),
                    )
                ],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="totalUsers"),
                    Metric(name="conversions"),
                ],
            )
            resp = client.run_report(req)
            row = resp.rows[0].metric_values if resp.rows else None
            return {
                "sessions": int(row[0].value) if row else 0,
                "users": int(row[1].value) if row else 0,
                "conversions": int(float(row[2].value)) if row else 0,
            }
        except Exception as exc:
            logger.error("ga4_client.get_weekly_comparison fetch failed: %s", exc)
            return {"sessions": 0, "users": 0, "conversions": 0}

    this_week = _fetch_week(this_start, this_end)
    last_week = _fetch_week(last_start, last_end)

    def _pct_change(new_val: int, old_val: int) -> float:
        if old_val == 0:
            return 0.0
        return round((new_val - old_val) / old_val * 100, 2)

    change_pct = {
        k: _pct_change(this_week[k], last_week[k]) for k in ("sessions", "users", "conversions")
    }

    return {
        "this_week": this_week,
        "last_week": last_week,
        "change_pct": change_pct,
    }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO)

    print("=== get_daily_metrics (yesterday) ===")
    metrics = get_daily_metrics()
    print(json.dumps(metrics, indent=2))

    print("\n=== get_top_pages (yesterday, limit=5) ===")
    pages = get_top_pages(limit=5)
    if pages:
        for p in pages:
            print(p)
    else:
        print("(no data – GA4 not configured or no traffic)")

    print("\n=== get_weekly_comparison ===")
    comparison = get_weekly_comparison()
    print(json.dumps(comparison, indent=2))
