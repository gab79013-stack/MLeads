"""
agents/marketing/paid_ads_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Paid Ads Agent — Google Ads + Facebook Ads monitoring, copy generation.

Agent key: mkt_ads
Interval:  360 minutes (6 hours)

fetch_leads() → fetch metrics, generate copy on Mondays, check performance alerts
notify(task)  → fetches campaign data, saves to ads_campaigns, generates copy variants
"""

import os
import sqlite3
import logging
from datetime import datetime

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH      = os.getenv("DB_PATH", "data/leads.db")
BUDGET_ALERT = float(os.getenv("ADS_DAILY_BUDGET_ALERT_PCT", "1.2"))
CTR_DROP_PCT = float(os.getenv("ADS_CTR_DROP_ALERT_PCT", "0.20"))

_TRADES = ["roofing", "electrical", "HVAC", "plumbing", "demolition", "general contracting"]
_GEO    = "Bay Area, California"


class PaidAdsAgent(BaseMarketingAgent):
    name      = "Paid Ads Agent"
    emoji     = "💰"
    agent_key = "mkt_ads"

    _claude_system_prompt = (
        "You are a paid ads specialist for MLeads — construction lead gen SaaS for Bay Area contractors. "
        "For each ad request: define the angle first (pain point / outcome / social proof / "
        "curiosity / urgency), then write 3 variations with different specificity and tone. "
        "Google RSA: headlines max 30 chars each, descriptions max 90 chars. "
        "Meta: primary text max 125 chars visible, headline max 40 chars. "
        "Use numbers when possible ('423 new permits this month in San Jose'). "
        "Benefits over features. Active voice. No vague claims like 'grow your business'. "
        "Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = [
            {"type": "fetch_google_metrics"},
            {"type": "fetch_facebook_metrics"},
            {"type": "performance_alert_check"},
        ]
        # Generate ad copy on Mondays or if no variants this week
        if datetime.utcnow().weekday() == 0 or not self._has_recent_copy():
            tasks.append({"type": "generate_ad_copy"})
        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "fetch_google_metrics":
            self._fetch_google()
        elif t == "fetch_facebook_metrics":
            self._fetch_facebook()
        elif t == "generate_ad_copy":
            self._run_generate_copy()
        elif t == "performance_alert_check":
            self._check_alerts()
        else:
            logger.warning(f"[{self.agent_key}] Unknown task: {t}")

    # ── Google Ads metrics ────────────────────────────────────────────

    def _fetch_google(self):
        try:
            from utils.google_ads_client import get_campaign_performance
            campaigns = get_campaign_performance()
            for camp in campaigns:
                self._upsert_campaign("google", camp)
            logger.info(f"[{self.agent_key}] Google: {len(campaigns)} campaigns fetched")
        except ImportError:
            logger.debug(f"[{self.agent_key}] google_ads_client not available")
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Google fetch error: {e}")

    # ── Facebook Ads metrics ──────────────────────────────────────────

    def _fetch_facebook(self):
        try:
            from utils.facebook_ads_client import get_campaign_performance
            campaigns = get_campaign_performance()
            for camp in campaigns:
                self._upsert_campaign("facebook", camp)
            logger.info(f"[{self.agent_key}] Facebook: {len(campaigns)} campaigns fetched")
        except ImportError:
            logger.debug(f"[{self.agent_key}] facebook_ads_client not available")
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Facebook fetch error: {e}")

    # ── Ad copy generation ────────────────────────────────────────────

    def _run_generate_copy(self):
        total = 0
        for trade in _TRADES[:3]:   # top 3 trades per run to limit API usage
            google_variants = self._gen_google_copy(trade, _GEO)
            self._save_copy_variants("google", google_variants, trade)
            total += len(google_variants)

            fb_variants = self._gen_facebook_copy(trade)
            self._save_copy_variants("facebook", fb_variants, trade)
            total += len(fb_variants)

        self._send_report(
            f"💰 *Ad Copy Generated*\n"
            f"📝 {total} variants for {len(_TRADES[:3])} trades\n"
            f"Status: 'suggested' — approve in dashboard"
        )
        logger.info(f"[{self.agent_key}] Generated {total} ad copy variants")

    def _gen_google_copy(self, trade: str, geography: str) -> list:
        prompt = (
            f"Write 3 Google Ads ad copy variants for MLeads targeting {trade} contractors "
            f"in {geography}.\n"
            f"USP: 'Get construction leads 30 days before competitors. 54 Bay Area cities.'\n"
            f"Vary angles: urgency / benefit / social proof.\n"
            f"Strict limits: headline1 max 30 chars, headline2 max 30 chars, "
            f"description max 90 chars.\n"
            f"JSON: [{{\"headline1\":\"...\",\"headline2\":\"...\","
            f"\"description\":\"...\"}}, ...]"
        )
        result = self._generate_json(prompt, max_tokens=600)
        if result and isinstance(result, list):
            return result
        return [
            {"headline1": "Bay Area Leads Daily", "headline2": "See Permits First",
             "description": f"MLeads delivers {trade} leads 30 days early. Try free today."},
            {"headline1": "Beat Competitors Today", "headline2": "54 Bay Area Cities",
             "description": f"Find {trade} projects before they call anyone else. Free trial."},
            {"headline1": "More Jobs, Less Chasing", "headline2": "Permit Data Live",
             "description": f"Get {trade} leads from permits the day they're filed. Sign up free."},
        ]

    def _gen_facebook_copy(self, trade: str) -> list:
        prompt = (
            f"Write 3 Facebook Ads copy variants for MLeads targeting {trade} contractors "
            f"in Bay Area.\n"
            f"Strict limits: headline max 40 chars, primary_text max 125 chars.\n"
            f"JSON: [{{\"headline\":\"...\",\"primary_text\":\"...\","
            f"\"description\":\"...\"}}, ...]"
        )
        result = self._generate_json(prompt, max_tokens=600)
        if result and isinstance(result, list):
            return result
        return [
            {"headline": "Get More Construction Jobs", "description": "Free Trial",
             "primary_text": f"Bay Area {trade} contractors — find leads before competitors. "
                             f"MLeads monitors 54 cities in real time."},
            {"headline": "See Permits Before Anyone", "description": "54 Bay Area Cities",
             "primary_text": f"Stop chasing referrals. MLeads delivers {trade} leads "
                             f"from permit filings the same day they're filed."},
            {"headline": "First Contractor Wins", "description": "Try MLeads Free",
             "primary_text": f"80% of {trade} jobs go to the first contractor who calls. "
                             f"MLeads gets you there first."},
        ]

    def _save_copy_variants(self, platform: str, variants: list, trade: str):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            for v in variants:
                if platform == "google":
                    headline  = f"{v.get('headline1', '')} | {v.get('headline2', '')}"
                    desc      = v.get("description", "")
                else:
                    headline  = v.get("headline", "")
                    desc      = v.get("primary_text", v.get("description", ""))
                conn.execute("""
                    INSERT INTO ad_copy_variants (headline, description, cta, ai_source, status)
                    VALUES (?, ?, ?, 'claude', 'suggested')
                """, (headline[:200], desc[:500], f"Try MLeads Free — {trade}"))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Save copy error: {e}")

    # ── Performance alerts ────────────────────────────────────────────

    def _check_alerts(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT platform, campaign_name, ctr, spend, budget_daily
                FROM ads_campaigns
                WHERE fetched_at >= date('now', '-7 days')
            """)
            rows = c.fetchall()
            conn.close()
        except Exception:
            return

        alerts = []
        for platform, name, ctr, spend, budget in rows:
            if budget and spend and spend > budget * BUDGET_ALERT:
                alerts.append(f"⚠️ {platform}/{name}: spend ${spend:.2f} exceeds budget ${budget:.2f}")
            if ctr and ctr < 0.5 and ctr > 0:
                alerts.append(f"📉 {platform}/{name}: CTR {ctr:.2f}% is low")

        if alerts:
            self._send_report("💰 *Ads Alert*\n" + "\n".join(alerts))
            logger.warning(f"[{self.agent_key}] {len(alerts)} performance alerts")

    # ── DB helpers ────────────────────────────────────────────────────

    def _upsert_campaign(self, platform: str, data: dict):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("""
                INSERT INTO ads_campaigns
                    (platform, campaign_id, campaign_name, status, budget_daily,
                     impressions, clicks, conversions, spend, ctr, cpc, roas, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                ON CONFLICT(platform, campaign_id) DO UPDATE SET
                    campaign_name = excluded.campaign_name,
                    status        = excluded.status,
                    budget_daily  = excluded.budget_daily,
                    impressions   = excluded.impressions,
                    clicks        = excluded.clicks,
                    conversions   = excluded.conversions,
                    spend         = excluded.spend,
                    ctr           = excluded.ctr,
                    cpc           = excluded.cpc,
                    roas          = excluded.roas,
                    fetched_at    = excluded.fetched_at
            """, (
                platform,
                data.get("campaign_id", "unknown"),
                data.get("campaign_name", ""),
                data.get("status", ""),
                data.get("budget_daily", 0),
                data.get("impressions", 0),
                data.get("clicks", 0),
                data.get("conversions", 0),
                data.get("spend", 0.0),
                data.get("ctr", 0.0),
                data.get("cpc", 0.0),
                data.get("roas", 0.0),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Upsert campaign error: {e}")

    def _has_recent_copy(self) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM ad_copy_variants
                WHERE created_at >= date('now', '-7 days')
            """)
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False
