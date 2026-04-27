"""
agents/marketing/social_media_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Social Media Agent — LinkedIn, Twitter/X, Instagram auto-posting.

Agent key: mkt_social
Interval:  120 minutes (dispatch queued posts + generate if queue low)

fetch_leads() → dispatch_queued task + generate_posts if queue is low
notify(task)  → dispatches pending posts OR generates new content queue
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta
import random

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH      = os.getenv("DB_PATH", "data/leads.db")
MKT_SITE_URL = os.getenv("MKT_SITE_BASE_URL", "https://mleads.com")

_ANGLES = [
    "stat_highlight",
    "feature_highlight",
    "social_proof",
    "urgency",
    "educational",
]

_PLATFORMS = ["linkedin", "twitter"]
_MIN_QUEUE  = 3   # generate new posts when queued < this per platform


class SocialMediaAgent(BaseMarketingAgent):
    name      = "Social Media Agent"
    emoji     = "📱"
    agent_key = "mkt_social"

    _claude_system_prompt = (
        "You are the social media voice of MLeads — Bay Area construction lead gen SaaS. "
        "Content mix: industry insights (30%), behind-the-scenes (25%), educational (25%), "
        "founder story (15%), promotional (5%). "
        "Hook formulas: curiosity ('I was wrong about permit data...'), "
        "story ('Last week a roofer in San Jose found 8 leads before competitors knew'), "
        "value ('How to find construction jobs before your competitors'). "
        "LinkedIn: professional, data-driven, 1-3 paragraphs. "
        "Twitter/X: punchy, one strong opinion or number, under 200 chars. "
        "Use real Bay Area permit stats when provided. No corporate speak. "
        "Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = [{"type": "dispatch_queued"}]

        stats = self._get_lead_stats()
        for platform in _PLATFORMS:
            queued = self._count_queued(platform)
            if queued < _MIN_QUEUE:
                tasks.append({
                    "type":     "generate_posts",
                    "platform": platform,
                    "stats":    stats,
                })

        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "dispatch_queued":
            self._dispatch_queued()
        elif t == "generate_posts":
            self._generate_and_queue(task["platform"], task.get("stats", {}))
        else:
            logger.warning(f"[{self.agent_key}] Unknown task: {t}")

    # ── Dispatch queue ────────────────────────────────────────────────

    def _dispatch_queued(self):
        try:
            from utils.marketing_db import get_queued_posts, mark_post_sent, mark_post_failed
        except ImportError:
            logger.warning(f"[{self.agent_key}] marketing_db not available")
            return

        posts = get_queued_posts()
        if not posts:
            return

        sent = failed = 0
        for post in posts:
            try:
                from utils.social_poster import dispatch_post
                result = dispatch_post(post["platform"], post["post_text"])
                if result.get("success"):
                    mark_post_sent(post["id"], result.get("post_id"))
                    sent += 1
                else:
                    mark_post_failed(post["id"], result.get("error", "unknown"))
                    failed += 1
            except ImportError:
                # No social_poster configured — log only
                logger.info(
                    f"[{self.agent_key}] [DRY RUN] {post['platform']}: "
                    f"{post['post_text'][:80]}..."
                )
                try:
                    mark_post_sent(post["id"])
                except Exception:
                    pass
                sent += 1
            except Exception as e:
                logger.warning(f"[{self.agent_key}] Dispatch error: {e}")
                try:
                    mark_post_failed(post["id"], str(e)[:200])
                except Exception:
                    pass
                failed += 1

        if sent or failed:
            logger.info(f"[{self.agent_key}] Dispatched: {sent} sent, {failed} failed")

    # ── Generate and queue posts ──────────────────────────────────────

    def _generate_and_queue(self, platform: str, stats: dict):
        posts = self._gen_posts(platform, stats)
        now = datetime.utcnow()
        for i, post in enumerate(posts):
            # Spread posts over next 7 days naturally
            offset_days = (i * 2) + random.randint(0, 1)
            offset_hours = random.choice([8, 10, 12, 14, 17])
            scheduled = (now + timedelta(days=offset_days)).replace(
                hour=offset_hours, minute=0, second=0, microsecond=0
            )
            self._queue_social_post(platform, post["text"], scheduled_at=scheduled)

        logger.info(f"[{self.agent_key}] Queued {len(posts)} {platform} posts")

    def _gen_posts(self, platform: str, stats: dict) -> list:
        prompt = self._build_posts_prompt(platform, stats)
        result = self._generate_json(prompt, max_tokens=1200)

        if result and isinstance(result, list):
            return [{"text": p.get("text", p.get("post", ""))[:500]} for p in result if p.get("text") or p.get("post")]

        # Fallback: template posts
        return self._template_posts(platform, stats)

    def _build_posts_prompt(self, platform: str, stats: dict) -> str:
        char_limit = 280 if platform == "twitter" else 700
        hashtags   = "" if platform == "twitter" else " #construction #contractor #BayArea #leads"
        permit_count = stats.get("total_leads", 0)
        top_city     = stats.get("top_city", "San Jose")
        top_trade    = stats.get("top_trade", "Roofing").lower()

        return (
            f"Write 5 {platform} posts for MLeads. "
            f"Bay Area context: {permit_count} new construction leads found this month, "
            f"top city: {top_city}, top trade: {top_trade}.\n"
            f"Use these 5 different angles (one post each):\n"
            f"1. stat_highlight: lead with the number {permit_count}\n"
            f"2. feature_highlight: MLeads gives 30-day head start on permit data\n"
            f"3. social_proof: Bay Area contractors winning bids with early data\n"
            f"4. urgency: 'your competitor saw that permit this morning'\n"
            f"5. educational: one tip about {top_trade} lead generation in {top_city}\n"
            f"Max {char_limit} chars per post.{hashtags}\n"
            f"JSON: [{{\"text\": \"...\"}}, ...]"
        )

    def _template_posts(self, platform: str, stats: dict) -> list:
        count    = stats.get("total_leads", 0)
        city     = stats.get("top_city", "San Jose")
        trade    = stats.get("top_trade", "Roofing").lower()
        site_url = MKT_SITE_URL

        ht = "" if platform == "twitter" else "\n\n#construction #contractor #BayArea"

        templates = [
            {"text": f"{count} new construction leads found across Bay Area this month. Your competitors are seeing the same permits — are you seeing them first? {site_url}{ht}"},
            {"text": f"Every construction permit in {city} is public record. MLeads delivers them to your phone the moment they're filed — 30 days before work begins. {site_url}{ht}"},
            {"text": f"Bay Area {trade} contractors using permit data close 2x more jobs than those waiting for referrals. The data is public. The advantage isn't. {site_url}{ht}"},
            {"text": f"A {city} contractor saw a $180k re-roof permit this morning at 7am. Called by 8am. Won the job by noon. That's what MLeads does. {site_url}{ht}"},
            {"text": f"3 tips for {trade} leads in {city}:\n1. Check new permits daily\n2. Target ADU projects ($200k+ avg)\n3. Call within 24h of filing\n\nMLeads automates all 3. {site_url}{ht}"},
        ]
        # Trim to platform char limit
        limit = 280 if platform == "twitter" else 700
        for t in templates:
            if len(t["text"]) > limit:
                t["text"] = t["text"][:limit - 3] + "..."
        return templates

    # ── DB helpers ────────────────────────────────────────────────────

    def _count_queued(self, platform: str) -> int:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM social_posts
                WHERE platform = ? AND status = 'queued'
            """, (platform,))
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _get_lead_stats(self) -> dict:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days')
            """)
            total = c.fetchone()[0]
            c.execute("""
                SELECT _trade, COUNT(*) cnt FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days') AND _trade IS NOT NULL
                GROUP BY _trade ORDER BY cnt DESC LIMIT 1
            """)
            row = c.fetchone()
            top_trade = row[0].title() if row else "Roofing"
            c.execute("""
                SELECT city, COUNT(*) cnt FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days') AND city IS NOT NULL
                GROUP BY city ORDER BY cnt DESC LIMIT 1
            """)
            row = c.fetchone()
            top_city = row[0] if row else "San Jose"
            conn.close()
            return {"total_leads": total, "top_trade": top_trade, "top_city": top_city}
        except Exception:
            return {"total_leads": 0, "top_trade": "Roofing", "top_city": "San Jose"}
