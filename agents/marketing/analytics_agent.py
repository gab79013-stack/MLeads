"""
agents/marketing/analytics_agent.py
Analytics Agent — GA4 daily snapshots, anomaly detection, weekly ROI report.

fetch_leads() → checks if today's snapshot is missing, or if weekly report is due
notify(task)  → executes: fetch GA4 snapshot OR send weekly report OR detect anomalies
"""

import logging
import os
import sqlite3
from datetime import date, datetime

from agents.marketing.base_marketing_agent import BaseMarketingAgent
from utils import marketing_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional GA4 client — graceful fallback if not installed / configured
# ---------------------------------------------------------------------------
try:
    from utils import ga4_client as _ga4_client

    _GA4_AVAILABLE = True
except ImportError:
    _ga4_client = None  # type: ignore[assignment]
    _GA4_AVAILABLE = False
    logger.info(
        "utils.ga4_client not found — Analytics Agent will use internal "
        "SQLite metrics as fallback."
    )


class AnalyticsAgent(BaseMarketingAgent):
    """
    Collects daily GA4 traffic snapshots (with an internal SQLite fallback),
    runs anomaly detection on session trends, and produces weekly ROI reports
    for MLeads marketing performance monitoring.

    Task types handled by notify():
        daily_snapshot  — fetch metrics for a given date and persist to DB
        anomaly_check   — alert on unusual session spikes or drops
        weekly_report   — build and send a full WoW performance summary
    """

    name = "Analytics Agent"
    emoji = "📊"
    agent_key = "mkt_analytics"

    _claude_system_prompt = (
        "You are a growth analytics expert for MLeads, a Bay Area "
        "construction lead generation SaaS. Given weekly traffic and "
        "conversion data, you write concise executive summaries with the "
        "most important insight, one concern, and one recommended action. "
        "Always compare to prior week. Keep responses under 200 words. "
        "Output plain text formatted for Telegram with emoji."
    )

    # ------------------------------------------------------------------
    # BaseMarketingAgent interface
    # ------------------------------------------------------------------

    def fetch_leads(self) -> list:
        """
        Return a list of task dicts describing work to be performed this cycle.

        Always emits:
            - ``{"type": "anomaly_check"}``

        Conditionally emits:
            - ``{"type": "daily_snapshot", "date": "YYYY-MM-DD"}``
              when today's snapshot is absent from the analytics_snapshots table.
            - ``{"type": "weekly_report"}``
              when today is Monday (datetime.now().weekday() == 0).

        Returns:
            list[dict]: Ordered list of task dicts ready for notify().
        """
        tasks = []
        today_str = date.today().isoformat()

        # ---- Check whether today's snapshot already exists ----
        try:
            recent_snapshots = marketing_db.get_last_n_snapshots(7)
            existing_dates = {s.get("date") for s in recent_snapshots}
        except Exception:
            logger.exception("Failed to query recent analytics snapshots.")
            existing_dates = set()

        if today_str not in existing_dates:
            tasks.append({"type": "daily_snapshot", "date": today_str})

        # ---- Anomaly check runs every cycle ----
        tasks.append({"type": "anomaly_check"})

        # ---- Weekly report on Mondays ----
        if datetime.now().weekday() == 0:
            tasks.append({"type": "weekly_report"})

        logger.debug(
            "Analytics Agent fetch_leads → %d task(s): %s",
            len(tasks),
            [t["type"] for t in tasks],
        )
        return tasks

    def notify(self, task: dict) -> None:
        """
        Execute a single task dispatched from the agent loop.

        Args:
            task: Dict with at minimum a ``type`` key. Recognised types:
                  ``daily_snapshot``  — requires ``date`` key (YYYY-MM-DD)
                  ``anomaly_check``   — no extra keys required
                  ``weekly_report``   — no extra keys required
        """
        task_type = task.get("type")

        if task_type == "daily_snapshot":
            date_str = task.get("date", date.today().isoformat())
            self._run_daily_snapshot(date_str)
        elif task_type == "anomaly_check":
            self._run_anomaly_check()
        elif task_type == "weekly_report":
            self._run_weekly_report()
        else:
            logger.warning(
                "AnalyticsAgent.notify() received unknown task type: %r", task_type
            )

    # ------------------------------------------------------------------
    # Public helper — called directly from the scheduler in main.py
    # ------------------------------------------------------------------

    def send_weekly_report(self) -> None:
        """
        Public entry point for manually triggering the weekly ROI report.

        Intended for use by the APScheduler cron job in main.py, e.g.:

            scheduler.add_job(analytics_agent.send_weekly_report, 'cron',
                              day_of_week='mon', hour=8)
        """
        self.notify({"type": "weekly_report"})

    # ------------------------------------------------------------------
    # Task implementations
    # ------------------------------------------------------------------

    def _run_daily_snapshot(self, date_str: str) -> None:
        """
        Fetch metrics for *date_str* and persist them as a daily snapshot.

        Tries GA4 first; falls back to _get_internal_metrics() if GA4 is not
        configured, returns empty data, or raises an exception.

        Args:
            date_str: ISO-8601 date string (YYYY-MM-DD).
        """
        logger.info("Analytics Agent: collecting daily snapshot for %s.", date_str)

        metrics = self._fetch_metrics_for_date(date_str)
        if not metrics:
            logger.warning(
                "No metrics returned for %s; skipping snapshot save.", date_str
            )
            return

        try:
            marketing_db.save_analytics_snapshot(date_str, metrics)
            logger.info("Saved analytics snapshot for %s.", date_str)
        except Exception:
            logger.exception("Failed to save analytics snapshot for %s.", date_str)

    def _run_anomaly_check(self) -> None:
        """
        Detect unusual session trends and alert via Telegram if thresholds are crossed.

        Compares the average daily sessions of the most-recent 7-day window against
        the prior 7-day window:
            - Spike:  current avg >= prior avg * 1.50  (+50 %)  → alert
            - Drop:   current avg <= prior avg * 0.70  (-30 %)  → alert

        Requires at least 2 snapshots in the DB; silently skips with a log entry
        when there is insufficient history.
        """
        logger.info("Analytics Agent: running anomaly check.")

        try:
            snapshots = marketing_db.get_last_n_snapshots(14)
        except Exception:
            logger.exception("Failed to fetch snapshots for anomaly check.")
            return

        if len(snapshots) < 2:
            logger.info(
                "Not enough snapshots for anomaly detection (have %d, need ≥2).",
                len(snapshots),
            )
            return

        # Sort ascending by date so slicing gives chronological windows
        snapshots_sorted = sorted(snapshots, key=lambda s: s.get("date", ""))

        recent_7 = snapshots_sorted[-7:]
        prior_7 = snapshots_sorted[-14:-7]

        if not prior_7:
            logger.info(
                "Fewer than 14 snapshots available (%d); skipping anomaly check.",
                len(snapshots),
            )
            return

        avg_recent = _avg_sessions(recent_7)
        avg_prior = _avg_sessions(prior_7)

        if avg_prior == 0:
            logger.info(
                "Prior-period average sessions is zero; skipping anomaly check."
            )
            return

        delta_pct = (avg_recent - avg_prior) / avg_prior * 100

        logger.info(
            "Anomaly check: recent_avg=%.1f, prior_avg=%.1f, delta=%.1f%%",
            avg_recent,
            avg_prior,
            delta_pct,
        )

        if delta_pct >= 50.0:
            message = (
                f"🚀 *Traffic Spike Detected!*\n\n"
                f"Sessions last 7 days avg: *{avg_recent:.0f}/day*\n"
                f"Sessions prior 7 days avg: *{avg_prior:.0f}/day*\n"
                f"Change: *+{delta_pct:.1f}%*\n\n"
                f"Investigate traffic sources to capitalise on this momentum. "
                f"Check paid campaigns, organic rankings, and referral activity."
            )
            logger.info("Sending traffic spike alert to Telegram.")
            self._send_report(message)

        elif delta_pct <= -30.0:
            message = (
                f"⚠️ *Traffic Drop Detected!*\n\n"
                f"Sessions last 7 days avg: *{avg_recent:.0f}/day*\n"
                f"Sessions prior 7 days avg: *{avg_prior:.0f}/day*\n"
                f"Change: *{delta_pct:.1f}%*\n\n"
                f"Review ad spend, SEO rankings, and site health immediately. "
                f"Check for broken tracking or campaign pauses."
            )
            logger.info("Sending traffic drop alert to Telegram.")
            self._send_report(message)

        else:
            logger.info("No anomaly detected (delta=%.1f%%).", delta_pct)

    def _run_weekly_report(self) -> None:
        """
        Build and deliver a weekly ROI performance summary.

        Workflow:
            1. Fetch the last 14 daily snapshots from the DB.
            2. Compute week-over-week session and conversion totals/deltas.
            3. Fetch the marketing KPI overview for supplemental context.
            4. Build a Claude prompt via _format_weekly_report_prompt().
            5. Generate an AI narrative via _generate_content().
            6. Assemble a structured Telegram message and send it.
            7. Store the AI narrative as a ``weekly_report`` marketing content entry.
            8. Save an ROI summary snapshot keyed as ``roi_YYYY-MM-DD``.
        """
        logger.info("Analytics Agent: generating weekly report.")
        report_date = date.today().isoformat()

        # ---- 1. Fetch snapshots ----
        try:
            snapshots = marketing_db.get_last_n_snapshots(14)
        except Exception:
            logger.exception("Failed to fetch snapshots for weekly report.")
            return

        if not snapshots:
            logger.warning("No snapshots available; skipping weekly report.")
            return

        # ---- 3. Fetch KPI overview ----
        try:
            overview = marketing_db.get_marketing_overview()
        except Exception:
            logger.exception("Failed to fetch marketing overview for weekly report.")
            overview = {}

        # ---- 4. Build prompt ----
        prompt = self._format_weekly_report_prompt(snapshots)

        # ---- 5. Generate AI narrative ----
        ai_summary = self._generate_content(prompt, max_tokens=300)
        if not ai_summary:
            ai_summary = (
                "📊 Weekly analytics summary temporarily unavailable — "
                "AI generation failed. Check logs for details."
            )
            logger.warning("AI summary generation failed; using fallback text.")

        # ---- 2 + 6. Compute stats and build Telegram message ----
        snapshots_sorted = sorted(snapshots, key=lambda s: s.get("date", ""))
        current_week = snapshots_sorted[-7:]
        prior_week = snapshots_sorted[-14:-7]

        report_lines = ["📊 *MLeads Weekly Analytics Report*\n"]

        if current_week:
            cur_sessions = sum(s.get("sessions", 0) for s in current_week)
            cur_conversions = sum(s.get("conversions", 0) for s in current_week)
            cur_new_users = sum(s.get("new_users", 0) for s in current_week)

            if prior_week:
                prior_sessions = sum(s.get("sessions", 0) for s in prior_week)
                prior_conversions = sum(s.get("conversions", 0) for s in prior_week)

                session_delta = _wow_delta_str(cur_sessions, prior_sessions)
                conversion_delta = _wow_delta_str(cur_conversions, prior_conversions)
            else:
                session_delta = "no prior data"
                conversion_delta = "no prior data"

            start_date = current_week[0].get("date", "?")
            end_date = current_week[-1].get("date", "?")
            report_lines.append(f"📅 *Week: {start_date} → {end_date}*\n")
            report_lines.append(f"🌐 Sessions: *{cur_sessions:,}* ({session_delta})")
            report_lines.append(f"🎯 Conversions: *{cur_conversions:,}* ({conversion_delta})")
            report_lines.append(f"👤 New users: *{cur_new_users:,}*")

        if overview:
            total_leads = overview.get("total_leads", 0)
            if total_leads:
                report_lines.append(f"📋 Total leads in DB: *{total_leads:,}*")

        report_lines.append(f"\n💡 *AI Insight*\n{ai_summary}")
        report_lines.append(f"\n_Report generated: {report_date}_")

        full_report = "\n".join(report_lines)
        self._send_report(full_report)
        logger.info("Weekly report sent to Telegram.")

        # ---- 7. Persist AI narrative as marketing content ----
        try:
            self._store_content(
                type="weekly_report",
                title=f"Weekly Analytics Report — {report_date}",
                body=ai_summary,
            )
        except Exception:
            logger.exception("Failed to store weekly report content in marketing_content.")

        # ---- 8. Save ROI summary snapshot ----
        try:
            if current_week:
                roi_data = {
                    "week_ending": report_date,
                    "sessions": sum(s.get("sessions", 0) for s in current_week),
                    "conversions": sum(s.get("conversions", 0) for s in current_week),
                    "new_users": sum(s.get("new_users", 0) for s in current_week),
                    "snapshot_count": len(current_week),
                }
                marketing_db.save_analytics_snapshot(f"roi_{report_date}", roi_data)
                logger.info("Saved weekly ROI snapshot for %s.", report_date)
        except Exception:
            logger.exception("Failed to save weekly ROI snapshot.")

    # ------------------------------------------------------------------
    # Metrics fetching — GA4 with internal SQLite fallback
    # ------------------------------------------------------------------

    def _fetch_metrics_for_date(self, date_str: str) -> dict:
        """
        Return a metrics dict for *date_str*, trying GA4 first.

        Falls back to _get_internal_metrics() if GA4 is not configured,
        returns empty/None data, or raises any exception.

        Args:
            date_str: ISO-8601 date string (YYYY-MM-DD).

        Returns:
            dict with at minimum ``date``, ``sessions``, ``new_users``,
            and ``conversions`` keys.
        """
        if _GA4_AVAILABLE:
            try:
                metrics = _ga4_client.get_daily_metrics(date_str)
                if metrics:
                    logger.debug("GA4 metrics fetched successfully for %s.", date_str)
                    return metrics
                logger.info(
                    "GA4 returned empty/None metrics for %s; falling back to internal.",
                    date_str,
                )
            except Exception:
                logger.exception(
                    "GA4 client raised an error for %s; falling back to internal metrics.",
                    date_str,
                )

        return self._get_internal_metrics(date_str)

    def _get_internal_metrics(self, date_str: str) -> dict:
        """
        Query the MLeads SQLite database directly to produce a minimal metrics dict.

        Runs two queries:
            SELECT COUNT(*) FROM users    WHERE date(created_at) = ?
            SELECT COUNT(*) FROM sessions WHERE date(created_at) = ?

        If either table does not exist, the corresponding count is recorded as 0
        and a warning is logged — the agent continues without raising an exception.

        New user registrations are used as a proxy for conversions when GA4 is
        not available.

        Args:
            date_str: ISO-8601 date string (YYYY-MM-DD).

        Returns:
            dict with keys:
                date        (str)   – The queried date
                sessions    (int)   – Row count from sessions table
                new_users   (int)   – Row count from users table
                conversions (int)   – Same as new_users (registration proxy)
                source      (str)   – "internal_sqlite"
        """
        new_users = 0
        session_count = 0
        db_path = self._get_db_path()

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row

            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) AS cnt FROM users WHERE date(created_at) = ?",
                    (date_str,),
                )
                row = cursor.fetchone()
                new_users = int(row["cnt"]) if row else 0
            except sqlite3.OperationalError:
                logger.warning(
                    "Table 'users' not found or query failed — new_users defaulting to 0."
                )

            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) AS cnt FROM sessions WHERE date(created_at) = ?",
                    (date_str,),
                )
                row = cursor.fetchone()
                session_count = int(row["cnt"]) if row else 0
            except sqlite3.OperationalError:
                logger.warning(
                    "Table 'sessions' not found or query failed — sessions defaulting to 0."
                )

            conn.close()

        except Exception:
            logger.exception(
                "Failed to open SQLite DB at '%s' for internal metrics.", db_path
            )

        metrics = {
            "date": date_str,
            "sessions": session_count,
            "new_users": new_users,
            "conversions": new_users,  # registrations as conversion proxy
            "source": "internal_sqlite",
        }
        logger.info(
            "Internal metrics for %s: sessions=%d, new_users=%d.",
            date_str,
            session_count,
            new_users,
        )
        return metrics

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    def _format_weekly_report_prompt(self, snapshots: list) -> str:
        """
        Build the Claude Haiku prompt from a list of daily snapshot dicts.

        Splits the sorted snapshot list into a current-week window (last 7 days)
        and a prior-week window (the 7 days before that), then formats totals and
        per-day averages for each window along with week-over-week delta strings.

        Args:
            snapshots: List of snapshot dicts, in any date order. Each dict is
                       expected to have at minimum: ``date``, ``sessions``,
                       ``new_users``, ``conversions``.

        Returns:
            Fully formatted prompt string ready for _generate_content().
        """
        snapshots_sorted = sorted(snapshots, key=lambda s: s.get("date", ""))
        current_week = snapshots_sorted[-7:]
        prior_week = snapshots_sorted[-14:-7] if len(snapshots_sorted) >= 14 else []

        def _week_block(week: list, label: str) -> str:
            if not week:
                return f"{label}: no data available."
            total_sessions = sum(s.get("sessions", 0) for s in week)
            total_users = sum(s.get("new_users", 0) for s in week)
            total_conversions = sum(s.get("conversions", 0) for s in week)
            avg_sessions = total_sessions / len(week)
            start = week[0].get("date", "?")
            end = week[-1].get("date", "?")
            return (
                f"{label} ({start} to {end}):\n"
                f"  Total sessions:       {total_sessions:,}  "
                f"(avg {avg_sessions:.1f}/day)\n"
                f"  New users:            {total_users:,}\n"
                f"  Conversions:          {total_conversions:,}"
            )

        current_block = _week_block(current_week, "Current week")
        prior_block = (
            _week_block(prior_week, "Prior week")
            if prior_week
            else "Prior week: insufficient historical data."
        )

        # Week-over-week delta block
        wow_block = ""
        if current_week and prior_week:
            cur_s = sum(s.get("sessions", 0) for s in current_week)
            pri_s = sum(s.get("sessions", 0) for s in prior_week)
            cur_c = sum(s.get("conversions", 0) for s in current_week)
            pri_c = sum(s.get("conversions", 0) for s in prior_week)
            cur_u = sum(s.get("new_users", 0) for s in current_week)
            pri_u = sum(s.get("new_users", 0) for s in prior_week)
            wow_block = (
                "\nWeek-over-week changes:\n"
                f"  Sessions:    {_delta_str(cur_s, pri_s)}\n"
                f"  New users:   {_delta_str(cur_u, pri_u)}\n"
                f"  Conversions: {_delta_str(cur_c, pri_c)}"
            )

        prompt = (
            "Here is the weekly analytics data for MLeads "
            "(Bay Area construction lead generation SaaS):\n\n"
            f"{current_block}\n\n"
            f"{prior_block}"
            f"{wow_block}\n\n"
            "Write a concise executive summary for this week's performance. "
            "Include: (1) the single most important insight, "
            "(2) one concern worth monitoring, "
            "(3) one specific recommended action. "
            "Always compare to the prior week. "
            "Use relevant emoji. Format for Telegram. "
            "Keep the total response under 200 words."
        )
        return prompt

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _get_db_path(self) -> str:
        """
        Resolve the SQLite database file path.

        Precedence:
            1. ``self.db_path`` — if set by BaseMarketingAgent or constructor
            2. Environment variable ``MLEADS_DB_PATH``
            3. Hardcoded fallback: ``<project_root>/instance/mleads.db``

        Returns:
            Absolute path string to the SQLite DB file.
        """
        if hasattr(self, "db_path") and self.db_path:
            return self.db_path

        env_path = os.environ.get("MLEADS_DB_PATH", "")
        if env_path:
            return env_path

        # Resolve project root: this file is at agents/marketing/analytics_agent.py,
        # so go up three levels to reach the project root.
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return os.path.join(base_dir, "instance", "mleads.db")


# ---------------------------------------------------------------------------
# Module-level pure-function helpers
# ---------------------------------------------------------------------------


def _avg_sessions(snapshots: list) -> float:
    """
    Compute the mean daily session count across *snapshots*.

    Args:
        snapshots: List of snapshot dicts, each with an optional ``sessions`` key.

    Returns:
        Mean session count as a float, or 0.0 for an empty list.
    """
    if not snapshots:
        return 0.0
    return sum(s.get("sessions", 0) for s in snapshots) / len(snapshots)


def _delta_str(current: float, prior: float) -> str:
    """
    Return a human-readable percentage delta string.

    Examples:
        _delta_str(110, 100) → "+10.0%"
        _delta_str(90, 100)  → "-10.0%"
        _delta_str(5, 0)     → "n/a"

    Args:
        current: Current-period value.
        prior:   Prior-period value.

    Returns:
        Formatted delta string, or ``"n/a"`` when prior is zero.
    """
    if prior == 0:
        return "n/a"
    pct = (current - prior) / prior * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}%"


def _wow_delta_str(current: float, prior: float) -> str:
    """
    Return a WoW delta string suitable for the Telegram report body.

    Examples:
        _wow_delta_str(110, 100) → "+10.0% WoW"
        _wow_delta_str(90, 100)  → "-10.0% WoW"
        _wow_delta_str(5, 0)     → "WoW: n/a"

    Args:
        current: Current-week total.
        prior:   Prior-week total.

    Returns:
        Formatted WoW delta string.
    """
    raw = _delta_str(current, prior)
    return "WoW: n/a" if raw == "n/a" else f"{raw} WoW"
