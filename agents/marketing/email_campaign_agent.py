"""
agents/marketing/email_campaign_agent.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Email Campaign Agent — drip sequences, trial nudges, newsletter, re-engagement.

Agent key: mkt_email
Interval:  60 minutes (checks for users needing next email in sequence)

fetch_leads() → tasks for users pending onboarding, trial nudge, or newsletter
notify(task)  → generates email content + sends via SendGrid + logs to email_sends
"""

import os
import sqlite3
import logging
from datetime import datetime, timedelta

from agents.marketing.base_marketing_agent import BaseMarketingAgent

logger = logging.getLogger(__name__)

DB_PATH        = os.getenv("DB_PATH", "data/leads.db")
SENDGRID_FROM  = os.getenv("SENDGRID_FROM_EMAIL", "noreply@mleads.com")
FROM_NAME      = os.getenv("MKT_FROM_NAME", "Gabriel at MLeads")
MKT_SITE_URL   = os.getenv("MKT_SITE_BASE_URL", "https://mleads.com")
UNSUB_URL      = os.getenv("EMAIL_UNSUBSCRIBE_URL", f"{MKT_SITE_URL}/unsubscribe")
MAX_PER_RUN    = 50


class EmailCampaignAgent(BaseMarketingAgent):
    name      = "Email Campaign Agent"
    emoji     = "📧"
    agent_key = "mkt_email"

    DRIP_SCHEDULE = {
        "onboarding":  [0, 1, 3, 7, 14],
        "trial_nudge": [5, 6, 7],
    }

    _claude_system_prompt = (
        "You are the email copywriter for MLeads, a Bay Area construction lead generation platform "
        "that gives contractors early access to permit data, solar installs, and demolition projects "
        "across 54 cities. Write transactional and marketing emails to contractors in a trial. "
        "Tone: direct, helpful, like a human founder — not corporate. "
        "Personalize with the user's name when provided. Output valid JSON only."
    )

    # ── fetch_leads ───────────────────────────────────────────────────

    def fetch_leads(self) -> list:
        tasks = []
        try:
            conn = self._db()
            c = conn.cursor()
            c.execute("""
                SELECT id, email, username, full_name, created_at, expires_at
                FROM users
                WHERE is_active = 1 AND email IS NOT NULL
                ORDER BY created_at DESC
            """)
            users = [dict(zip([d[0] for d in c.description], row)) for row in c.fetchall()]
            conn.close()
        except Exception as e:
            logger.error(f"[{self.agent_key}] DB read error: {e}")
            return []

        now = datetime.utcnow()

        for user in users:
            if len(tasks) >= MAX_PER_RUN:
                break

            try:
                created = datetime.fromisoformat(user["created_at"].replace("Z", ""))
            except Exception:
                continue

            days_since = (now - created).days

            # Onboarding drip
            sends_done = self._email_sends_count(user["id"], "onboarding")
            for idx, day in enumerate(self.DRIP_SCHEDULE["onboarding"]):
                if idx < sends_done:
                    continue
                if days_since >= day:
                    tasks.append({
                        "type":      "onboarding",
                        "user_id":   user["id"],
                        "email":     user["email"],
                        "username":  user.get("username", ""),
                        "full_name": user.get("full_name", ""),
                        "day":       day,
                    })
                    break

        # Trial nudge
        try:
            conn = self._db()
            c = conn.cursor()
            c.execute("""
                SELECT id, email, username, full_name, expires_at
                FROM users
                WHERE is_active = 1 AND expires_at IS NOT NULL AND email IS NOT NULL
            """)
            trial_users = [dict(zip([d[0] for d in c.description], row)) for row in c.fetchall()]
            conn.close()
        except Exception as e:
            logger.error(f"[{self.agent_key}] Trial nudge DB error: {e}")
            trial_users = []

        for user in trial_users:
            if len(tasks) >= MAX_PER_RUN:
                break
            try:
                exp = datetime.fromisoformat(user["expires_at"].replace("Z", ""))
                days_left = (exp - now).days
                if 0 <= days_left <= 3:
                    sends = self._email_sends_count(user["id"], "trial_nudge")
                    if sends < 3:
                        tasks.append({
                            "type":      "trial_nudge",
                            "user_id":   user["id"],
                            "email":     user["email"],
                            "username":  user.get("username", ""),
                            "full_name": user.get("full_name", ""),
                            "days_left": days_left,
                        })
            except Exception:
                continue

        # Monthly newsletter (1st of month)
        if now.day == 1:
            if not self._newsletter_sent_this_month():
                tasks.append({"type": "newsletter", "month": now.strftime("%B %Y")})

        return tasks

    # ── notify ────────────────────────────────────────────────────────

    def notify(self, task: dict):
        t = task.get("type")
        if t == "onboarding":
            self._send_onboarding(task)
        elif t == "trial_nudge":
            self._send_trial_nudge(task)
        elif t == "newsletter":
            self._send_newsletter(task)
        else:
            logger.warning(f"[{self.agent_key}] Unknown task type: {t}")

    # ── Onboarding ────────────────────────────────────────────────────

    def _send_onboarding(self, task: dict):
        name    = task.get("full_name") or task.get("username") or "there"
        day     = task.get("day", 0)
        content = self._gen_onboarding(name, day)
        ok = self._send_email(task["email"], content["subject"], content["html"])
        if ok:
            self._log_send(task["user_id"], task["email"], "onboarding", content.get("campaign_id"))
            logger.info(f"[{self.agent_key}] Onboarding day-{day} sent to {task['email']}")

    def _gen_onboarding(self, name: str, day: int) -> dict:
        prompt = (
            f"Write a day-{day} onboarding email for '{name}', a contractor who just signed up "
            f"for a free trial of MLeads (Bay Area construction lead platform, 54 cities, "
            f"permit data, solar, demolition leads). "
            f"Day 0: warm welcome + how to find first lead. "
            f"Day 1: remind them of features, show what others do. "
            f"Day 3: 3 tips to maximize the platform. "
            f"Day 7: upgrade CTA — trial halfway done. "
            f"Day 14: last-chance trial ending. "
            f"Sign off as 'Gabriel, MLeads founder'. "
            f"JSON: {{\"subject\": \"...\", \"html\": \"<html with inline styles>\"}}"
        )
        result = self._generate_json(prompt, max_tokens=800)
        if result and "subject" in result and "html" in result:
            result["ai_source"] = "claude"
            return result
        return self._onboarding_template(name, day)

    def _onboarding_template(self, name: str, day: int) -> dict:
        templates = {
            0: {
                "subject": "Welcome to MLeads — here's your first lead",
                "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>Hey {name}, welcome to MLeads! 🔨</h2>
<p>You now have access to real-time construction leads across 54 Bay Area cities — pulled directly from permit filings, solar installs, demolition projects, and more.</p>
<p><strong>To find your first lead:</strong></p>
<ol>
<li>Log in at <a href="{MKT_SITE_URL}">{MKT_SITE_URL}</a></li>
<li>Use the Swipe Feed to browse leads by trade and city</li>
<li>Hit the fire emoji 🔥 on anything that looks good</li>
</ol>
<p>Most contractors find 3-5 warm leads in their first 10 minutes. Go see for yourself.</p>
<p>— Gabriel, MLeads founder</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
            },
            1: {
                "subject": "Did you find a lead yet? Here's what other contractors do",
                "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>Hey {name} 👋</h2>
<p>The contractors getting the most out of MLeads do one simple thing: they check the Swipe Feed every morning before their competitors wake up.</p>
<p>Here's a real example: a San Jose roofer found a permit for a $180k re-roof last Tuesday at 7am. He called by 8am. Won the job by noon.</p>
<p><a href="{MKT_SITE_URL}" style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:5px;text-decoration:none">Check today's leads →</a></p>
<p>— Gabriel</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
            },
            3: {
                "subject": "3 tips to get more from MLeads",
                "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>3 tips for MLeads power users</h2>
<p>Hey {name},</p>
<p>Here's how contractors squeeze the most leads out of the platform:</p>
<p><strong>1. Filter by trade.</strong> Don't scroll through everything — set your trade filter and only see relevant work.</p>
<p><strong>2. Enable HOT leads only.</strong> Leads scored 90+ are ones with a permit, active construction, AND an inspection coming up. Those are money.</p>
<p><strong>3. Check Hot Zones.</strong> When 3+ leads cluster within 500 meters, it means a whole neighborhood is building. One call gets you into the whole zone.</p>
<p><a href="{MKT_SITE_URL}">Log in and try these now →</a></p>
<p>— Gabriel</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
            },
            7: {
                "subject": "Your MLeads trial is halfway done",
                "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>Hey {name}, 7 days in — how's it going?</h2>
<p>You've got 7 days left on your free trial. If you've been finding leads, now is the time to lock in your access so you don't lose them when the trial ends.</p>
<p>For $99/month you get:</p>
<ul>
<li>Unlimited leads across 54 Bay Area cities</li>
<li>HOT leads the moment permits are filed</li>
<li>Hot Zone alerts for neighborhood-level activity</li>
<li>AI-generated outreach messages for each lead</li>
</ul>
<p><a href="{MKT_SITE_URL}/upgrade" style="background:#2563eb;color:#fff;padding:10px 20px;border-radius:5px;text-decoration:none">Upgrade now →</a></p>
<p>— Gabriel</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
            },
            14: {
                "subject": "Last chance — your MLeads trial ends today",
                "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>Hey {name} — your trial ends today</h2>
<p>This is your last chance to keep your access to MLeads before your trial expires.</p>
<p>Don't let your competitors get a 30-day head start on every permit filed in your city.</p>
<p><a href="{MKT_SITE_URL}/upgrade" style="background:#dc2626;color:#fff;padding:12px 24px;border-radius:5px;text-decoration:none;font-size:16px">Keep my access — $99/mo →</a></p>
<p>Questions? Just reply to this email.</p>
<p>— Gabriel</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
            },
        }
        return templates.get(day, templates[0])

    # ── Trial nudge ───────────────────────────────────────────────────

    def _send_trial_nudge(self, task: dict):
        name      = task.get("full_name") or task.get("username") or "there"
        days_left = task.get("days_left", 1)
        content   = self._gen_trial_nudge(name, days_left)
        ok = self._send_email(task["email"], content["subject"], content["html"])
        if ok:
            self._log_send(task["user_id"], task["email"], "trial_nudge")
            logger.info(f"[{self.agent_key}] Trial nudge ({days_left}d left) → {task['email']}")

    def _gen_trial_nudge(self, name: str, days_left: int) -> dict:
        urgency = {3: "3 days", 2: "2 days", 1: "tomorrow", 0: "today"}
        label = urgency.get(days_left, f"{days_left} days")
        return {
            "subject": f"Your MLeads trial expires {label}",
            "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2 style="color:#dc2626">Hey {name} — {label} left on your trial</h2>
<p>After your trial ends you lose access to all leads, hot zones, and AI outreach messages.</p>
<p>Upgrade now to keep your competitive edge:</p>
<p><a href="{MKT_SITE_URL}/upgrade" style="background:#2563eb;color:#fff;padding:12px 24px;border-radius:5px;text-decoration:none">Upgrade for $99/mo →</a></p>
<p>— Gabriel</p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
        }

    # ── Newsletter ────────────────────────────────────────────────────

    def _send_newsletter(self, task: dict):
        month = task.get("month", "")
        stats = self._get_lead_stats()
        content = self._gen_newsletter(month, stats)
        # Send to all active users
        conn = self._db()
        c = conn.cursor()
        c.execute("SELECT id, email FROM users WHERE is_active = 1 AND email IS NOT NULL")
        recipients = c.fetchall()
        conn.close()
        sent = 0
        for user_id, email in recipients:
            ok = self._send_email(email, content["subject"], content["html"])
            if ok:
                self._log_send(user_id, email, "newsletter")
                sent += 1
        self._send_report(f"📧 Newsletter '{month}' enviado a {sent} usuarios")
        logger.info(f"[{self.agent_key}] Newsletter sent to {sent} users")

    def _gen_newsletter(self, month: str, stats: dict) -> dict:
        prompt = (
            f"Write a monthly newsletter for MLeads contractors for {month}. "
            f"Platform stats: {stats}. "
            f"Tone: punchy, data-driven, like a founder update. "
            f"Include: 1 headline stat, 2 feature highlights, 1 Bay Area construction trend, CTA. "
            f"Max 350 words. JSON: {{\"subject\": \"...\", \"html\": \"<html>\"}}"
        )
        result = self._generate_json(prompt, max_tokens=900)
        if result and "subject" in result:
            return result
        lead_count = stats.get("total_leads", 0)
        return {
            "subject": f"MLeads {month} — {lead_count:,} new leads this month",
            "html": f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px">
<h2>MLeads {month} Update</h2>
<p>Here's what happened this month across the Bay Area construction market:</p>
<ul>
<li><strong>{lead_count:,} new leads</strong> across 54 cities</li>
<li>Top trade: {stats.get('top_trade', 'Roofing')}</li>
<li>Hottest city: {stats.get('top_city', 'San Jose')}</li>
</ul>
<p><a href="{MKT_SITE_URL}">See this month's leads →</a></p>
<hr><small><a href="{UNSUB_URL}">Unsubscribe</a></small>
</body></html>""",
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _email_sends_count(self, user_id: int, campaign_type: str) -> int:
        try:
            from utils.marketing_db import get_email_send_day
            return get_email_send_day(user_id, campaign_type)
        except Exception:
            return 0

    def _log_send(self, user_id: int, email: str, campaign_type: str,
                  campaign_id: int = None):
        try:
            from utils.marketing_db import log_email_send
            log_email_send(campaign_id, email, user_id)
        except Exception as e:
            logger.debug(f"[{self.agent_key}] log_send error: {e}")

    def _send_email(self, to: str, subject: str, html: str) -> bool:
        try:
            from utils.notifications import send_email
            return send_email(to, subject, html)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"[{self.agent_key}] send_email error: {e}")
            return False
        # Fallback: log only
        logger.info(f"[{self.agent_key}] [DRY RUN] Email to {to}: {subject}")
        return True

    def _newsletter_sent_this_month(self) -> bool:
        try:
            conn = self._db()
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM email_sends es
                JOIN email_campaigns ec ON ec.id = es.campaign_id
                WHERE ec.campaign_type = 'newsletter'
                  AND strftime('%Y-%m', es.sent_at) = strftime('%Y-%m', 'now')
            """)
            count = c.fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False

    def _get_lead_stats(self) -> dict:
        try:
            conn = self._db()
            c = conn.cursor()
            c.execute("""
                SELECT COUNT(*) FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days')
            """)
            total = c.fetchone()[0]
            c.execute("""
                SELECT _trade, COUNT(*) as cnt FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days') AND _trade IS NOT NULL
                GROUP BY _trade ORDER BY cnt DESC LIMIT 1
            """)
            row = c.fetchone()
            top_trade = row[0] if row else "ROOFING"
            c.execute("""
                SELECT city, COUNT(*) as cnt FROM consolidated_leads
                WHERE created_at >= date('now', '-30 days') AND city IS NOT NULL
                GROUP BY city ORDER BY cnt DESC LIMIT 1
            """)
            row = c.fetchone()
            top_city = row[0] if row else "San Jose"
            conn.close()
            return {"total_leads": total, "top_trade": top_trade, "top_city": top_city}
        except Exception:
            return {"total_leads": 0, "top_trade": "Roofing", "top_city": "San Jose"}
