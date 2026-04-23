"""
agents/marketing/seo_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SEO Agent — blog post generation, keyword tracking, sitemap, content calendar.

Agent key: mkt_seo
Interval:  10,080 minutes (weekly)

fetch_leads() → tasks: generate_blog, keyword_check, sitemap_update, seed_calendar
notify(task)  → generates blog post via Claude, queues social companions, updates DB
"""

import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta, date

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH      = os.getenv("DB_PATH", "data/leads.db")
MKT_SITE_URL = os.getenv("MKT_SITE_BASE_URL", "https://mleads.com")
BLOG_PATH    = os.getenv("MKT_BLOG_BASE_PATH", "/blog")
APPROVAL     = os.getenv("MKT_CONTENT_APPROVAL_MODE", "manual")


class SEOAgent(BaseMarketingAgent):
    name      = "SEO Agent"
    emoji     = "🔍"
    agent_key = "mkt_seo"

    TARGET_KEYWORDS = [
        "construction leads Bay Area",
        "roofing leads San Jose",
        "roofing leads San Francisco",
        "electrical contractor leads California",
        "HVAC leads Bay Area",
        "plumbing subcontractor leads",
        "construction permit data California",
        "find construction projects near me",
        "building permit leads Bay Area",
        "contractor lead generation software",
        "construction lead generation California",
        "roofing contractor marketing",
        "how to get more construction jobs",
        "subcontractor leads California",
        "Bay Area construction opportunities",
    ]

    _claude_system_prompt = (
        "You are an expert SEO content strategist for MLeads, a Bay Area construction lead "
        "generation platform serving 54 cities including San Jose, San Francisco, Oakland, "
        "Fremont, Santa Clara, Berkeley, Richmond, Hayward, and Sunnyvale. "
        "You write 600-word blog posts that rank for terms Bay Area contractors search. "
        "Tone: authoritative, practical, data-driven. Include real examples. Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = []

        # Seed calendar if empty
        if not self._calendar_has_content():
            tasks.append({"type": "seed_calendar"})

        # Generate blog posts for keywords lacking recent content (max 2 per run)
        blog_count = 0
        for kw in self.TARGET_KEYWORDS:
            if blog_count >= 2:
                break
            if not self._keyword_has_recent_post(kw):
                tasks.append({"type": "generate_blog", "keyword": kw})
                blog_count += 1

        # Always check keywords and update sitemap
        tasks.append({"type": "keyword_check"})
        tasks.append({"type": "sitemap_update"})

        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "generate_blog":
            self._run_generate_blog(task["keyword"])
        elif t == "keyword_check":
            self._run_keyword_check()
        elif t == "sitemap_update":
            self._run_sitemap_update()
        elif t == "seed_calendar":
            self._seed_content_calendar()
        else:
            logger.warning(f"[{self.agent_key}] Unknown task: {t}")

    # ── Blog generation ───────────────────────────────────────────────

    def _run_generate_blog(self, keyword: str):
        city  = self._city_from_keyword(keyword)
        trade = self._trade_from_keyword(keyword)
        post  = self._gen_blog(keyword, city, trade)

        status = "published" if APPROVAL == "auto" else "draft"
        content_id = self._store_content(
            content_type="blog_post",
            title=post["title"],
            body=post["body"],
            keywords=[keyword],
            meta_description=post.get("meta", "")[:155],
            ai_source=post.get("ai_source", "template"),
            status=status,
        )

        if content_id > 0 and status == "published":
            self._upsert_sitemap(f"{MKT_SITE_URL}{BLOG_PATH}/{self._slugify(post['title'])}")

        # Queue companion social posts
        self._gen_social_companions(post["title"], keyword, post["body"][:200], content_id)

        self._send_report(
            f"🔍 *SEO Blog Post {'Published' if status == 'published' else 'Drafted'}*\n"
            f"📝 {post['title']}\n"
            f"🔑 Keyword: _{keyword}_\n"
            f"🤖 Source: {post.get('ai_source', 'template')}"
        )
        logger.info(f"[{self.agent_key}] Blog '{post['title']}' saved (id={content_id})")

    def _gen_blog(self, keyword: str, city: str, trade: str) -> dict:
        prompt = (
            f"Write a 600-word SEO blog post targeting the keyword: \"{keyword}\".\n"
            f"City focus: {city}. Trade focus: {trade or 'general construction'}.\n"
            f"Structure: H1 title, meta description (max 155 chars), 4 H2 sections "
            f"(each 2-3 sentences), and a CTA to sign up for MLeads free trial at {MKT_SITE_URL}.\n"
            f"JSON schema: {{\"title\": \"...\", \"meta\": \"...\", \"body\": \"...\", \"cta\": \"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=1200)
        if result and "title" in result and "body" in result:
            result["ai_source"] = "claude"
            return result
        return self._blog_template(keyword, city, trade)

    def _blog_template(self, keyword: str, city: str, trade: str) -> dict:
        trade_label = trade or "construction"
        return {
            "title": f"How to Find {trade_label.title()} Leads in {city}: A Complete Guide",
            "meta": f"Discover how Bay Area {trade_label} contractors find high-value leads using permit data. MLeads monitors 54 cities in real time.",
            "body": (
                f"<h1>How to Find {trade_label.title()} Leads in {city}</h1>\n\n"
                f"<p>Finding quality {trade_label} leads in {city} requires more than word-of-mouth. "
                f"The most successful contractors use permit data to identify projects before the competition even knows they exist.</p>\n\n"
                f"<h2>Why Permit Data Changes Everything</h2>\n"
                f"<p>Every construction project in {city} starts with a permit filing. "
                f"That filing is public record — and it's available 30 days before work begins. "
                f"MLeads monitors all 54 Bay Area cities in real time, so you get notified the moment a relevant permit hits.</p>\n\n"
                f"<h2>The Types of Leads Available in {city}</h2>\n"
                f"<p>MLeads sources leads from permits, solar installs, demolition projects, flood damage reports, and real estate transactions. "
                f"For {trade_label} contractors specifically, the highest-value signals are ADU permits, re-roof applications, and inspection-pending jobs.</p>\n\n"
                f"<h2>How to Act on Leads Before Competitors</h2>\n"
                f"<p>Speed matters. The first contractor to reach a property owner or GC wins the quote. "
                f"MLeads sends you AI-generated outreach messages — SMS, email, and phone scripts — so you can contact leads in under 60 seconds.</p>\n\n"
                f"<h2>Getting Started</h2>\n"
                f"<p>MLeads offers a free trial with access to all 54 Bay Area cities. "
                f"Most contractors find 5-10 warm leads in their first day.</p>\n\n"
                f"<p><strong>Ready to stop chasing leads and start getting called?</strong></p>"
            ),
            "cta": f"Start your free trial at {MKT_SITE_URL}",
            "ai_source": "template",
        }

    # ── Social companions ─────────────────────────────────────────────

    def _gen_social_companions(self, title: str, keyword: str,
                               body_preview: str, content_id: int):
        blog_url = f"{MKT_SITE_URL}{BLOG_PATH}/{self._slugify(title)}"

        # LinkedIn
        li_text = (
            f"New on the MLeads blog: \"{title}\"\n\n"
            f"{body_preview}...\n\n"
            f"Read the full guide → {blog_url}\n\n"
            f"#construction #leads #BayArea #contractor"
        )
        self._queue_social_post("linkedin", li_text, content_id=content_id)

        # Twitter
        tw_text = (
            f"How to find {keyword} — new guide on the MLeads blog 🔨\n"
            f"{blog_url}"
        )[:280]
        self._queue_social_post("twitter", tw_text, content_id=content_id)

    # ── Keyword check ─────────────────────────────────────────────────

    def _run_keyword_check(self):
        try:
            from utils.search_console_client import get_keyword_performance
            kw_data = get_keyword_performance(days=28)
            if kw_data:
                from utils.marketing_db import upsert_seo_keyword
                for kw in kw_data:
                    upsert_seo_keyword(
                        keyword=kw.get("keyword", ""),
                        position=int(kw.get("position", 0)),
                        clicks=kw.get("clicks", 0),
                        impressions=kw.get("impressions", 0),
                        ctr=kw.get("ctr", 0.0),
                    )
                logger.info(f"[{self.agent_key}] Updated {len(kw_data)} keywords from Search Console")
                self._send_report(
                    f"🔍 *Keyword Check*\n"
                    f"📊 {len(kw_data)} keywords updated from Search Console"
                )
        except ImportError:
            logger.debug(f"[{self.agent_key}] Search Console client not available")
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Keyword check error: {e}")

    # ── Sitemap update ────────────────────────────────────────────────

    def _run_sitemap_update(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT title FROM marketing_content
                WHERE content_type = 'blog_post' AND status = 'published'
            """)
            titles = [row[0] for row in c.fetchall() if row[0]]
            conn.close()

            for title in titles:
                self._upsert_sitemap(f"{MKT_SITE_URL}{BLOG_PATH}/{self._slugify(title)}")

            logger.info(f"[{self.agent_key}] Sitemap updated with {len(titles)} URLs")
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Sitemap update error: {e}")

    def _upsert_sitemap(self, url: str):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            conn.execute("""
                INSERT INTO seo_sitemap (url, priority, changefreq, last_mod)
                VALUES (?, 0.7, 'weekly', date('now'))
                ON CONFLICT(url) DO UPDATE SET last_mod = date('now')
            """, (url,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[{self.agent_key}] Sitemap upsert error: {e}")

    # ── Content calendar ──────────────────────────────────────────────

    def _seed_content_calendar(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            today = date.today()
            for i, kw in enumerate(self.TARGET_KEYWORDS[:4]):
                sched = today + timedelta(weeks=i)
                c.execute("""
                    INSERT OR IGNORE INTO content_calendar
                        (scheduled_date, content_type, topic, target_keyword, assigned_agent, status)
                    VALUES (?, 'blog_post', ?, ?, 'mkt_seo', 'planned')
                """, (sched.isoformat(), f"Blog: {kw}", kw))
            conn.commit()
            conn.close()
            logger.info(f"[{self.agent_key}] Content calendar seeded for 4 weeks")
        except Exception as e:
            logger.warning(f"[{self.agent_key}] Calendar seed error: {e}")

    # ── Utility helpers ───────────────────────────────────────────────

    def _keyword_has_recent_post(self, keyword: str) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM marketing_content
                WHERE content_type = 'blog_post'
                  AND (title LIKE ? OR keywords LIKE ?)
                  AND created_at >= date('now', '-30 days')
            """, (f"%{keyword[:30]}%", f"%{keyword[:30]}%"))
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False

    def _calendar_has_content(self) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM content_calendar")
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return True

    def _city_from_keyword(self, keyword: str) -> str:
        cities = ["San Jose", "San Francisco", "Oakland", "Fremont",
                  "Santa Clara", "Berkeley", "Hayward", "Sunnyvale"]
        kw_lower = keyword.lower()
        for city in cities:
            if city.lower() in kw_lower:
                return city
        return "Bay Area"

    def _trade_from_keyword(self, keyword: str) -> str:
        trades = {
            "roof": "roofing", "solar": "solar", "electric": "electrical",
            "hvac": "HVAC", "plumb": "plumbing", "demo": "demolition",
            "constr": "construction", "contractor": "contracting",
        }
        kw_lower = keyword.lower()
        for k, v in trades.items():
            if k in kw_lower:
                return v
        return "construction"

    @staticmethod
    def _slugify(text: str) -> str:
        import re
        slug = text.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s_]+", "-", slug)
        slug = re.sub(r"-+", "-", slug)
        return slug[:80]
