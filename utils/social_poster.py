"""
social_poster.py – API wrappers for social media posting.
All public functions return {"success": bool, "post_id": str, "error": str}.

Environment variables read:
    BUFFER_ACCESS_TOKEN
    BUFFER_PROFILE_IDS          (comma-separated)
    TWITTER_BEARER_TOKEN
    TWITTER_API_KEY
    TWITTER_API_SECRET
    TWITTER_ACCESS_TOKEN
    TWITTER_ACCESS_SECRET
    LINKEDIN_ACCESS_TOKEN
    LINKEDIN_ORGANIZATION_ID
"""

import os
import json
import logging

import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
BUFFER_ACCESS_TOKEN = os.getenv("BUFFER_ACCESS_TOKEN", "")
BUFFER_PROFILE_IDS_RAW = os.getenv("BUFFER_PROFILE_IDS", "")

TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.getenv("TWITTER_ACCESS_SECRET", "")

LINKEDIN_ACCESS_TOKEN = os.getenv("LINKEDIN_ACCESS_TOKEN", "")
LINKEDIN_ORGANIZATION_ID = os.getenv("LINKEDIN_ORGANIZATION_ID", "")

_EMPTY_RESULT = {"success": False, "post_id": "", "error": ""}


def _result(success: bool, post_id: str = "", error: str = "") -> dict:
    return {"success": success, "post_id": str(post_id), "error": error}


# ---------------------------------------------------------------------------
# Buffer
# ---------------------------------------------------------------------------

def _get_buffer_profile_ids() -> list:
    """Return profile IDs from env, or auto-discover via Buffer API."""
    if BUFFER_PROFILE_IDS_RAW:
        return [pid.strip() for pid in BUFFER_PROFILE_IDS_RAW.split(",") if pid.strip()]

    if not BUFFER_ACCESS_TOKEN:
        return []

    try:
        resp = requests.get(
            "https://api.bufferapp.com/1/profiles.json",
            params={"access_token": BUFFER_ACCESS_TOKEN},
            timeout=10,
        )
        if resp.status_code == 200:
            profiles = resp.json()
            return [p["id"] for p in profiles if isinstance(p, dict) and "id" in p]
    except Exception as exc:
        logger.warning("Buffer profile auto-discover failed: %s", exc)
    return []


def post_to_buffer(text: str, profile_ids: list = None) -> dict:
    """Post via Buffer API to all configured profiles."""
    if not BUFFER_ACCESS_TOKEN:
        logger.warning("Buffer not configured: BUFFER_ACCESS_TOKEN is missing.")
        return _result(False, error="Buffer not configured")

    ids = profile_ids or _get_buffer_profile_ids()
    if not ids:
        logger.warning("Buffer: no profile IDs available.")
        return _result(False, error="No Buffer profile IDs configured")

    payload = {
        "text": text,
        "access_token": BUFFER_ACCESS_TOKEN,
        "profile_ids[]": ids,
    }

    try:
        resp = requests.post(
            "https://api.bufferapp.com/1/updates/create.json",
            data=payload,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code in (200, 201) and data.get("success"):
            updates = data.get("updates", [{}])
            post_id = updates[0].get("id", "") if updates else ""
            return _result(True, post_id=post_id)
        error_msg = data.get("message", resp.text[:200])
        logger.error("Buffer post failed (%s): %s", resp.status_code, error_msg)
        return _result(False, error=error_msg)
    except Exception as exc:
        logger.error("Buffer post exception: %s", exc)
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------

def _twitter_oauth1_auth():
    """Return requests_oauthlib OAuth1 object, or None if not available."""
    try:
        from requests_oauthlib import OAuth1
        return OAuth1(
            TWITTER_API_KEY,
            TWITTER_API_SECRET,
            TWITTER_ACCESS_TOKEN,
            TWITTER_ACCESS_SECRET,
        )
    except ImportError:
        return None


def post_to_twitter(text: str) -> dict:
    """Post tweet via Twitter API v2."""
    if not all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET]):
        logger.warning("Twitter not configured: one or more TWITTER_* env vars missing.")
        return _result(False, error="Twitter not configured")

    # Prefer tweepy if installed
    try:
        import tweepy
        client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_SECRET,
        )
        response = client.create_tweet(text=text)
        tweet_id = str(response.data.get("id", "")) if response.data else ""
        return _result(True, post_id=tweet_id)
    except ImportError:
        pass
    except Exception as exc:
        logger.error("tweepy post failed: %s", exc)
        return _result(False, error=str(exc))

    # Fallback: direct requests + OAuth1
    auth = _twitter_oauth1_auth()
    if auth is None:
        logger.warning("requests_oauthlib not installed; cannot authenticate to Twitter.")
        return _result(False, error="requests_oauthlib not installed")

    try:
        resp = requests.post(
            "https://api.twitter.com/2/tweets",
            json={"text": text},
            auth=auth,
            timeout=15,
        )
        data = resp.json()
        if resp.status_code in (200, 201):
            tweet_id = str(data.get("data", {}).get("id", ""))
            return _result(True, post_id=tweet_id)
        error_msg = data.get("detail", resp.text[:200])
        logger.error("Twitter post failed (%s): %s", resp.status_code, error_msg)
        return _result(False, error=error_msg)
    except Exception as exc:
        logger.error("Twitter post exception: %s", exc)
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# LinkedIn
# ---------------------------------------------------------------------------

def post_to_linkedin(text: str, organization_id: str = None) -> dict:
    """Post to LinkedIn organization page via LinkedIn API v2."""
    if not LINKEDIN_ACCESS_TOKEN:
        logger.warning("LinkedIn not configured: LINKEDIN_ACCESS_TOKEN missing.")
        return _result(False, error="LinkedIn not configured")

    org_id = organization_id or LINKEDIN_ORGANIZATION_ID
    if not org_id:
        logger.warning("LinkedIn: LINKEDIN_ORGANIZATION_ID not set.")
        return _result(False, error="LinkedIn organization ID not configured")

    author = f"urn:li:organization:{org_id}"
    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    headers = {
        "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    try:
        resp = requests.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers=headers,
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            post_id = resp.headers.get("X-RestLi-Id", "")
            if not post_id:
                try:
                    post_id = str(resp.json().get("id", ""))
                except Exception:
                    post_id = ""
            return _result(True, post_id=post_id)
        error_msg = resp.text[:200]
        try:
            error_msg = resp.json().get("message", error_msg)
        except Exception:
            pass
        logger.error("LinkedIn post failed (%s): %s", resp.status_code, error_msg)
        return _result(False, error=error_msg)
    except Exception as exc:
        logger.error("LinkedIn post exception: %s", exc)
        return _result(False, error=str(exc))


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def dispatch_post(platform: str, text: str) -> dict:
    """Route to the correct posting function based on platform string."""
    platform_lower = platform.lower().strip()
    if platform_lower == "twitter":
        return post_to_twitter(text)
    if platform_lower == "linkedin":
        return post_to_linkedin(text)
    if platform_lower in ("instagram", "buffer"):
        return post_to_buffer(text)
    logger.warning("dispatch_post: unknown platform '%s'", platform)
    return _result(False, error=f"Unknown platform: {platform}")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def get_post_metrics(platform: str, post_id: str) -> dict:
    """
    Fetch engagement metrics for a published post.
    Returns {"likes": int, "shares": int, "impressions": int}
    """
    base = {"likes": 0, "shares": 0, "impressions": 0}
    platform_lower = platform.lower().strip()

    if platform_lower == "twitter":
        if not TWITTER_BEARER_TOKEN:
            base["note"] = "TWITTER_BEARER_TOKEN not configured"
            return base
        try:
            headers = {"Authorization": f"Bearer {TWITTER_BEARER_TOKEN}"}
            params = {
                "tweet.fields": "public_metrics",
                "expansions": "author_id",
            }
            resp = requests.get(
                f"https://api.twitter.com/2/tweets/{post_id}",
                headers=headers,
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                metrics = resp.json().get("data", {}).get("public_metrics", {})
                return {
                    "likes": metrics.get("like_count", 0),
                    "shares": metrics.get("retweet_count", 0),
                    "impressions": metrics.get("impression_count", 0),
                }
            base["note"] = f"Twitter API error {resp.status_code}"
        except Exception as exc:
            base["note"] = str(exc)
        return base

    if platform_lower == "linkedin":
        if not LINKEDIN_ACCESS_TOKEN or not LINKEDIN_ORGANIZATION_ID:
            base["note"] = "LinkedIn not configured"
            return base
        try:
            headers = {
                "Authorization": f"Bearer {LINKEDIN_ACCESS_TOKEN}",
                "X-Restli-Protocol-Version": "2.0.0",
            }
            params = {
                "q": "organizationalEntity",
                "organizationalEntity": f"urn:li:organization:{LINKEDIN_ORGANIZATION_ID}",
                "ugcPosts": f"urn:li:ugcPost:{post_id}",
            }
            resp = requests.get(
                "https://api.linkedin.com/v2/organizationalEntityShareStatistics",
                headers=headers,
                params=params,
                timeout=10,
            )
            if resp.status_code == 200:
                elements = resp.json().get("elements", [{}])
                stats = elements[0].get("totalShareStatistics", {}) if elements else {}
                return {
                    "likes": stats.get("likeCount", 0),
                    "shares": stats.get("shareCount", 0),
                    "impressions": stats.get("impressionCount", 0),
                }
            base["note"] = f"LinkedIn API error {resp.status_code}"
        except Exception as exc:
            base["note"] = str(exc)
        return base

    base["note"] = f"Metrics not supported for platform: {platform}"
    return base


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    TEST_TEXT = (
        "MLeads just surfaced 50 new roofing leads in the Bay Area this week. "
        "Sign up free → https://mleads.com"
    )

    print("=== dispatch_post: twitter (no keys) ===")
    print(dispatch_post("twitter", TEST_TEXT))

    print("\n=== dispatch_post: linkedin (no keys) ===")
    print(dispatch_post("linkedin", TEST_TEXT))

    print("\n=== dispatch_post: buffer (no keys) ===")
    print(dispatch_post("buffer", TEST_TEXT))

    print("\n=== dispatch_post: instagram (no keys) ===")
    print(dispatch_post("instagram", TEST_TEXT))

    print("\n=== dispatch_post: unknown platform ===")
    print(dispatch_post("tiktok", TEST_TEXT))

    print("\n=== get_post_metrics: twitter (no keys) ===")
    print(get_post_metrics("twitter", "1234567890"))

    print("\n=== get_post_metrics: linkedin (no keys) ===")
    print(get_post_metrics("linkedin", "9876543210"))

    print("\n=== get_post_metrics: unsupported platform ===")
    print(get_post_metrics("instagram", "abc123"))
