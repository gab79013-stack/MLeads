"""
agents/marketing/content_marketing_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Content Marketing Agent — long-form articles, case studies, landing page variants.

Agent key: mkt_content
Interval:  10,080 minutes (weekly)

fetch_leads() → tasks: case_study, long_form_article, landing_page_variant
notify(task)  → generates content via Claude, stores in marketing_content, Telegram alert
"""

import os
import sqlite3
import logging
from datetime import datetime

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH      = os.getenv("DB_PATH", "data/leads.db")
MKT_SITE_URL = os.getenv("MKT_SITE_BASE_URL", "https://mleads.com")
APPROVAL     = os.getenv("MKT_CONTENT_APPROVAL_MODE", "manual")

_ARTICLE_TOPICS = [
    "How Permit Data Gives Contractors a 30-Day Head Start",
    "The Ultimate Guide to Finding Subcontractor Opportunities in the Bay Area",
    "Why 80% of Construction Leads Go to the First Contractor Who Calls",
    "Bay Area Construction Boom: What the Numbers Say",
    "From Permit to Project: How to Turn Data Into Contracts",
]


class ContentMarketingAgent(BaseMarketingAgent):
    name      = "Content Marketing Agent"
    emoji     = "✍️"
    agent_key = "mkt_content"

    _claude_system_prompt = (
        "You are a B2B content marketer for MLeads, a Bay Area construction lead generation SaaS "
        "for contractors. You write data-backed articles and case studies that convert skeptical "
        "contractors into paying customers. Real numbers from permit databases make your content "
        "credible. Always include an ROI angle. Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = []
        now = datetime.utcnow()

        if not self._content_exists_in_days("case_study", 14):
            tasks.append({"type": "case_study"})

        week_num = now.isocalendar()[1]
        topic = _ARTICLE_TOPICS[week_num % len(_ARTICLE_TOPICS)]
        if not self._content_exists_in_days("article", 7):
            tasks.append({"type": "long_form_article", "topic": topic})

        if now.day <= 7 and not self._content_exists_in_days("landing_page", 28):
            tasks.append({"type": "landing_page_variant"})

        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "case_study":
            self._run_case_study()
        elif t == "long_form_article":
            self._run_article(task.get("topic", _ARTICLE_TOPICS[0]))
        elif t == "landing_page_variant":
            self._run_landing_page()
        else:
            logger.warning(f"[{self.agent_key}] Unknown task: {t}")

    # ── Case study ────────────────────────────────────────────────────

    def _run_case_study(self):
        stats = self._get_lead_stats()
        trade = stats.get("top_trade", "ROOFING").title()
        city  = stats.get("top_city", "San Jose")
        count = stats.get("total_leads", 50)
        value = stats.get("total_value", 2_500_000)

        post = self._gen_case_study(trade, city, count, value)
        status = "published" if APPROVAL == "auto" else "draft"
        content_id = self._store_content(
            content_type="case_study",
            title=post["headline"],
            body=f"{post['subheadline']}\n\n{post['body']}\n\n{post['cta']}",
            ai_source=post.get("ai_source", "template"),
            status=status,
        )

        self._queue_social_post(
            "linkedin",
            f"Case Study: {post['headline']}\n\n{post['subheadline']}\n\n"
            f"Read more → {MKT_SITE_URL}/case-studies\n#construction #contractor #BayArea",
            content_id=content_id,
        )

        self._send_report(
            f"✍️ *Case Study {'Published' if status == 'published' else 'Drafted'}*\n"
            f"📝 {post['headline']}\n"
            f"📊 {trade} · {city} · {count} leads · ${value:,.0f} value"
        )

    def _gen_case_study(self, trade: str, city: str, lead_count: int,
                        total_value: float) -> dict:
        prompt = (
            f"Create a 400-word case study for MLeads using this anonymized data:\n"
            f"Trade: {trade} contractor in {city}.\n"
            f"Leads found in 30 days: {lead_count}.\n"
            f"Estimated total project value of those leads: ${total_value:,.0f}.\n"
            f"Frame as: 'How a {city} {trade} contractor added {lead_count} warm leads "
            f"to their pipeline using permit data.'\n"
            f"Include ROI calculation. End with CTA to try MLeads free.\n"
            f"JSON: {{\"headline\":\"...\",\"subheadline\":\"...\","
            f"\"body\":\"...\",\"cta\":\"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=900)
        if result and "headline" in result:
            result["ai_source"] = "claude"
            return result
        return {
            "headline": f"How a {city} {trade} Contractor Found {lead_count} Leads in 30 Days",
            "subheadline": f"Using MLeads permit data, one {city} contractor identified "
                           f"${total_value:,.0f} in potential project value before competitors.",
            "body": (
                f"When a {city}-based {trade.lower()} contractor signed up for MLeads, "
                f"they weren't sure what to expect. Within the first month, they identified "
                f"{lead_count} projects across {city} and neighboring areas — all before "
                f"a single competitor had made contact.\n\n"
                f"The secret? Permit data. Every permit filed in the Bay Area is public record. "
                f"MLeads monitors 54 cities in real time and delivers leads the same day "
                f"they're filed — 30 days before work begins.\n\n"
                f"The contractor estimates the pipeline value at ${total_value:,.0f}. "
                f"Even winning 10% of those jobs would represent significant revenue growth."
            ),
            "cta": f"Start your free trial at {MKT_SITE_URL}",
            "ai_source": "template",
        }

    # ── Long-form article ─────────────────────────────────────────────

    def _run_article(self, topic: str):
        post = self._gen_article(topic)
        status = "published" if APPROVAL == "auto" else "draft"
        content_id = self._store_content(
            content_type="article",
            title=post["title"],
            body=post["body"],
            meta_description=post.get("meta", "")[:155],
            ai_source=post.get("ai_source", "template"),
            status=status,
        )

        # Queue 3 social posts for article
        for platform in ["linkedin", "twitter"]:
            text = (
                f"New article: \"{post['title']}\"\n\n{post.get('meta','')[:150]}\n\n"
                f"Read → {MKT_SITE_URL}/blog\n#construction #leads #BayArea"
            )[:280]
            self._queue_social_post(platform, text, content_id=content_id)

        self._send_report(
            f"✍️ *Article {'Published' if status == 'published' else 'Drafted'}*\n"
            f"📝 {post['title']}\n"
            f"🤖 Source: {post.get('ai_source', 'template')}"
        )

    def _gen_article(self, topic: str) -> dict:
        prompt = (
            f"Write a 1200-word B2B article for MLeads titled: \"{topic}\".\n"
            f"Structure: compelling intro, 5 H2 sections with specific data/examples, "
            f"conclusion with CTA to sign up free at {MKT_SITE_URL}.\n"
            f"Bay Area construction focus. Include real-sounding statistics.\n"
            f"JSON: {{\"title\":\"...\",\"meta\":\"...\",\"body\":\"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=1800)
        if result and "title" in result:
            result["ai_source"] = "claude"
            return result
        return {
            "title": topic,
            "meta": f"Learn how Bay Area contractors use permit data to win more jobs. "
                    f"MLeads monitors 54 cities in real time.",
            "body": (
                f"<h1>{topic}</h1>\n\n"
                f"<p>The Bay Area construction market moves fast. Permits are filed, "
                f"projects start, and contractors who show up first win the job. "
                f"Here's how the best contractors stay ahead.</p>\n\n"
                f"<h2>The 30-Day Advantage</h2>\n"
                f"<p>Construction permits are public record and typically filed 3-6 weeks "
                f"before work begins. That's your window. MLeads monitors all permit filings "
                f"across 54 Bay Area cities and delivers them to you the same day.</p>\n\n"
                f"<h2>Types of Leads That Matter Most</h2>\n"
                f"<p>Not all leads are equal. The highest-value signals: ADU permits "
                f"($200k+ average), re-roof applications, solar installs requiring electrical "
                f"work, and demolition permits that precede new construction.</p>\n\n"
                f"<h2>Speed as a Competitive Advantage</h2>\n"
                f"<p>Studies show 80% of leads go to the first contractor who responds. "
                f"MLeads includes AI-generated outreach messages — SMS, email, phone script — "
                f"so you can contact a lead in under 60 seconds.</p>\n\n"
                f"<h2>Hot Zones: Neighborhood-Level Opportunities</h2>\n"
                f"<p>When 3+ leads cluster within 500 meters, MLeads sends a Hot Zone alert. "
                f"This means a whole neighborhood is building — one relationship can turn into "
                f"multiple contracts.</p>\n\n"
                f"<h2>Getting Started</h2>\n"
                f"<p>MLeads offers a free trial with full access to all 54 cities. "
                f"Most contractors find 5-10 warm leads on day one.</p>\n\n"
                f"<p><a href=\"{MKT_SITE_URL}\">Start your free trial today →</a></p>"
            ),
            "ai_source": "template",
        }

    # ── Landing page variant ──────────────────────────────────────────

    def _run_landing_page(self):
        prompt = (
            "Write an A/B test landing page variant for MLeads (Bay Area construction lead "
            "generation SaaS). Focus on a different angle than the typical 'find leads faster' — "
            "try 'stop losing jobs to contractors who see permits before you do'. "
            "Include: headline, subheadline, 3 bullet benefits, CTA button text, social proof line. "
            f"JSON: {{\"headline\":\"...\",\"subheadline\":\"...\","
            f"\"bullets\":[...],\"cta\":\"...\",\"social_proof\":\"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=600)
        if not result:
            result = {
                "headline": "Your Competitors Are Seeing That Permit Right Now",
                "subheadline": "MLeads monitors permit filings across 54 Bay Area cities "
                               "and delivers leads to you the moment they're filed.",
                "bullets": [
                    "Get leads 30 days before work starts",
                    "AI-generated outreach messages ready to send",
                    "Hot Zone alerts for neighborhood-level opportunities",
                ],
                "cta": "Start Free Trial — No Credit Card",
                "social_proof": "Trusted by Bay Area contractors from San Jose to San Francisco",
            }
            result["ai_source"] = "template"
        else:
            result["ai_source"] = "claude"

        body = (
            f"HEADLINE: {result.get('headline','')}\n"
            f"SUBHEADLINE: {result.get('subheadline','')}\n"
            f"BULLETS: {result.get('bullets',[])}\n"
            f"CTA: {result.get('cta','')}\n"
            f"SOCIAL PROOF: {result.get('social_proof','')}"
        )
        self._store_content(
            content_type="landing_page",
            title=result.get("headline", "Landing Page Variant"),
            body=body,
            ai_source=result.get("ai_source", "template"),
            status="draft",
        )
        self._send_report(f"✍️ *Landing Page Variant Drafted*\n📝 {result.get('headline','')}")

    # ── DB helpers ────────────────────────────────────────────────────

    def _content_exists_in_days(self, content_type: str, days: int) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM marketing_content
                WHERE content_type = ? AND created_at >= date('now', ?)
            """, (content_type, f"-{days} days"))
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False

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
            top_trade = row[0] if row else "ROOFING"
            c.execute("""
                SELECT city, COUNT(*) cnt FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days') AND city IS NOT NULL
                GROUP BY city ORDER BY cnt DESC LIMIT 1
            """)
            row = c.fetchone()
            top_city = row[0] if row else "San Jose"
            c.execute("""
                SELECT COALESCE(SUM(value_float), 0) FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days')
            """)
            total_value = c.fetchone()[0] or 0
            conn.close()
            return {
                "total_leads": total,
                "top_trade": top_trade,
                "top_city": top_city,
                "total_value": float(total_value),
            }
        except Exception:
            return {"total_leads": 50, "top_trade": "ROOFING",
                    "top_city": "San Jose", "total_value": 2_500_000.0}
