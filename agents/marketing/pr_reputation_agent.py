"""
agents/marketing/pr_reputation_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PR & Reputation Agent — press releases, review management, PR pipeline.

Agent key: mkt_pr
Interval:  10,080 minutes (weekly)

fetch_leads() → check_milestones, check_reviews, weekly_pr_report
notify(task)  → generates press releases at milestones, AI review responses, PR report
"""

import os
import sqlite3
import logging
from datetime import datetime

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH      = os.getenv("DB_PATH", "data/leads.db")
COMPANY_NAME = os.getenv("PR_COMPANY_NAME", "MLeads")
PR_EMAIL     = os.getenv("PR_CONTACT_EMAIL", "press@mleads.com")
BOILERPLATE  = os.getenv(
    "PR_COMPANY_BOILERPLATE",
    "MLeads is a Bay Area construction lead generation platform monitoring 54 cities "
    "in real time. Contractors use MLeads to find high-value projects 30 days before "
    "competitors through permit data, solar installs, and demolition filings.",
)
G2_API_KEY       = os.getenv("G2_API_KEY", "")
CAPTERRA_API_KEY = os.getenv("CAPTERRA_API_KEY", "")

MILESTONE_THRESHOLDS = [1_000, 5_000, 10_000, 25_000, 50_000, 100_000]

_PUBLICATIONS = [
    "Construction Dive",
    "Engineering News-Record (ENR)",
    "Remodeling Magazine",
    "Professional Remodeler",
    "EC&M Magazine",
    "Plumbing & Mechanical Magazine",
]


class PRReputationAgent(BaseMarketingAgent):
    name      = "PR & Reputation Agent"
    emoji     = "📰"
    agent_key = "mkt_pr"

    _claude_system_prompt = (
        f"You are the PR and communications strategist for {COMPANY_NAME}, a Bay Area "
        "construction lead generation SaaS. You write press releases and industry pitches "
        "for publications like Construction Dive, ENR, Remodeling Magazine, and "
        "Professional Remodeler. Tone: authoritative, data-driven, newsworthy. "
        "Always lead with the 'why it matters for contractors' angle. Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = [
            {"type": "check_milestones"},
            {"type": "check_reviews"},
            {"type": "weekly_pr_report"},
        ]

        # Check if a new milestone was just crossed
        lead_count = self._get_total_leads()
        for threshold in MILESTONE_THRESHOLDS:
            if lead_count >= threshold and not self._milestone_pr_exists(threshold):
                tasks.append({
                    "type":       "generate_press_release",
                    "milestone":  threshold,
                    "lead_count": lead_count,
                })
                break  # one press release per run

        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "check_milestones":
            pass   # handled in fetch_leads for this run
        elif t == "generate_press_release":
            self._run_press_release(task["milestone"], task["lead_count"])
        elif t == "check_reviews":
            self._run_check_reviews()
        elif t == "weekly_pr_report":
            self._run_pr_report()
        else:
            logger.warning(f"[{self.agent_key}] Unknown task: {t}")

    # ── Press release ─────────────────────────────────────────────────

    def _run_press_release(self, milestone: int, lead_count: int):
        pr = self._gen_press_release(milestone, lead_count)
        from utils.marketing_db import save_pr_item
        item_id = save_pr_item(
            item_type="press_release",
            title=pr["headline"],
            body=f"{pr['body']}\n\n### About {COMPANY_NAME}\n{pr['boilerplate']}",
            status="draft",
        )
        self._send_report(
            f"📰 *Press Release Drafted*\n"
            f"📝 {pr['headline']}\n"
            f"🎯 Milestone: {milestone:,} leads\n"
            f"🤖 Source: {pr.get('ai_source', 'template')}"
        )
        logger.info(f"[{self.agent_key}] Press release drafted for {milestone:,} milestone (id={item_id})")

    def _gen_press_release(self, milestone: int, lead_count: int) -> dict:
        prompt = (
            f"Write a press release for {COMPANY_NAME}.\n"
            f"Milestone: Surpassed {milestone:,} construction leads across Bay Area.\n"
            f"Current count: {lead_count:,} leads, 54 cities, real-time permit monitoring.\n"
            f"Contact: {PR_EMAIL}\n"
            f"Include: FOR IMMEDIATE RELEASE header, compelling headline, dateline "
            f"(San Jose, CA, {datetime.utcnow().strftime('%B %d, %Y')}), "
            f"3 body paragraphs, realistic founder quote from 'CEO of {COMPANY_NAME}', "
            f"standard boilerplate, press contact.\n"
            f"JSON: {{\"headline\":\"...\",\"body\":\"...\",\"boilerplate\":\"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=1000)
        if result and "headline" in result:
            result["ai_source"] = "claude"
            return result

        return {
            "headline": f"{COMPANY_NAME} Surpasses {milestone:,} Construction Leads "
                        f"Across Bay Area, Empowering Local Contractors",
            "body": (
                f"FOR IMMEDIATE RELEASE\n\n"
                f"SAN JOSE, CA, {datetime.utcnow().strftime('%B %d, %Y')} — "
                f"{COMPANY_NAME}, a Bay Area construction lead generation platform, "
                f"today announced it has surpassed {milestone:,} construction leads "
                f"monitored across 54 cities in the greater Bay Area.\n\n"
                f"The platform aggregates real-time permit filings, solar installations, "
                f"demolition projects, and property transactions to deliver actionable "
                f"leads to contractors before their competitors.\n\n"
                f"'Reaching {milestone:,} leads is a testament to how much untapped "
                f"opportunity exists in public permit data,' said the CEO of {COMPANY_NAME}. "
                f"'Our contractors are consistently the first call on new projects because "
                f"they see the data before anyone else.'\n\n"
                f"For more information, contact {PR_EMAIL}."
            ),
            "boilerplate": BOILERPLATE,
            "ai_source": "template",
        }

    # ── Review monitoring ─────────────────────────────────────────────

    def _run_check_reviews(self):
        reviews_found = 0

        # G2
        if G2_API_KEY:
            try:
                reviews_found += self._fetch_g2_reviews()
            except Exception as e:
                logger.warning(f"[{self.agent_key}] G2 error: {e}")
        # Capterra
        if CAPTERRA_API_KEY:
            try:
                reviews_found += self._fetch_capterra_reviews()
            except Exception as e:
                logger.warning(f"[{self.agent_key}] Capterra error: {e}")

        if not G2_API_KEY and not CAPTERRA_API_KEY:
            logger.info(f"[{self.agent_key}] No review API keys — skipping review check")

        if reviews_found:
            logger.info(f"[{self.agent_key}] Processed {reviews_found} reviews")

    def _fetch_g2_reviews(self) -> int:
        import requests
        resp = requests.get(
            "https://data.g2.com/api/v1/reviews",
            headers={"Authorization": f"Token token={G2_API_KEY}"},
            params={"filter[product_id]": os.getenv("G2_PRODUCT_ID", ""),
                    "sort": "-created_at", "page[size]": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            return 0
        reviews = resp.json().get("data", [])
        for review in reviews:
            attrs = review.get("attributes", {})
            rating = attrs.get("star_rating", 5)
            text   = attrs.get("title", "") + " " + attrs.get("comment_answers", {}).get("love", "")
            if rating < 3:
                self._handle_negative_review(text, rating, "G2")
        return len(reviews)

    def _fetch_capterra_reviews(self) -> int:
        # Capterra has a limited public API; using basic product reviews endpoint
        import requests
        resp = requests.get(
            f"https://api.capterra.com/v2/reviews",
            headers={"Authorization": f"Bearer {CAPTERRA_API_KEY}"},
            params={"product_id": os.getenv("CAPTERRA_PRODUCT_ID", ""),
                    "sort": "-created_at", "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            return 0
        reviews = resp.json().get("reviews", [])
        for review in reviews:
            rating = review.get("overall_rating", 5)
            text   = review.get("review_title", "") + " " + review.get("pros_cons", "")
            if rating < 3:
                self._handle_negative_review(text, rating, "Capterra")
        return len(reviews)

    def _handle_negative_review(self, review_text: str, rating: float, platform: str):
        response = self._gen_review_response(review_text, rating, platform)
        from utils.marketing_db import save_pr_item
        save_pr_item(
            item_type="review",
            title=f"Negative review on {platform} ({rating}/5)",
            body=review_text[:1000],
            review_platform=platform.lower(),
            review_rating=rating,
            sentiment="negative",
            ai_response=response,
            status="draft",
        )
        self._send_report(
            f"⚠️ *Negative Review ({platform})*\n"
            f"⭐ Rating: {rating}/5\n"
            f"💬 Response drafted — review in dashboard"
        )

    def _gen_review_response(self, review_text: str, rating: float, platform: str) -> str:
        prompt = (
            f"Write a professional response to this negative review on {platform}:\n"
            f"Review: \"{review_text[:300]}\"\n"
            f"Rating: {rating}/5\n"
            f"Company: {COMPANY_NAME} — Bay Area construction lead generation SaaS.\n"
            f"Tone: empathetic, not defensive. Offer to resolve offline. Under 100 words.\n"
            f"JSON: {{\"response\": \"...\"}}"
        )
        result = self._generate_json(prompt, max_tokens=300)
        if result and "response" in result:
            return result["response"]
        if rating <= 1:
            return (
                f"We're sorry to hear about your experience with {COMPANY_NAME}. "
                "This isn't the standard we hold ourselves to and we'd love to make it right. "
                f"Please reach out directly at {PR_EMAIL} and we'll personally resolve this."
            )
        return (
            f"Thank you for your feedback. We're always working to improve {COMPANY_NAME} "
            "and your input helps us do that. "
            f"Please contact us at {PR_EMAIL} so we can address your concerns directly."
        )

    # ── Weekly PR report ──────────────────────────────────────────────

    def _run_pr_report(self):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("SELECT status, COUNT(*) FROM pr_items GROUP BY status")
            status_counts = dict(c.fetchall())
            c.execute("""
                SELECT COUNT(*) FROM pr_items
                WHERE created_at >= date('now', '-7 days')
            """)
            new_this_week = c.fetchone()[0]
            conn.close()
        except Exception:
            status_counts = {}
            new_this_week = 0

        lead_count  = self._get_total_leads()
        next_mile   = next((t for t in MILESTONE_THRESHOLDS if t > lead_count), None)
        pct_to_next = f"{(lead_count / next_mile * 100):.0f}%" if next_mile else "100%"

        self._send_report(
            f"📰 *Weekly PR Report*\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Pipeline:\n"
            f"  Draft: {status_counts.get('draft', 0)}\n"
            f"  Sent: {status_counts.get('sent', 0)}\n"
            f"  Published: {status_counts.get('published', 0)}\n"
            f"  New this week: {new_this_week}\n\n"
            f"🎯 Milestone Progress:\n"
            f"  Total leads: {lead_count:,}\n"
            f"  Next milestone: {next_mile:,} ({pct_to_next} there)\n\n"
            f"📰 Target Publications:\n"
            + "\n".join(f"  • {p}" for p in _PUBLICATIONS[:3])
        )

    # ── DB helpers ────────────────────────────────────────────────────

    def _get_total_leads(self) -> int:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM consolidated_leads")
            count = c.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def _milestone_pr_exists(self, threshold: int) -> bool:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM pr_items
                WHERE item_type = 'press_release'
                  AND title LIKE ?
            """, (f"%{threshold:,}%",))
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False
