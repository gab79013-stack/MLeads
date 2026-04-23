"""
utils/marketing_db.py
━━━━━━━━━━━━━━━━━━━━
Schema SQLite para el equipo de Marketing Agents.

Tablas:
  - marketing_content     contenido generado (blog, social, ads, PR)
  - seo_keywords          tracking de posición y métricas por keyword
  - seo_sitemap           URLs para sitemap.xml auto-generado
  - social_posts          cola y log de publicaciones en redes sociales
  - email_campaigns       definición de secuencias de email
  - email_sends           log de envíos individuales con open/click tracking
  - ads_campaigns         métricas de campañas Google Ads + Facebook Ads
  - ad_copy_variants      variantes de copy sugeridas por Claude (A/B)
  - analytics_snapshots   snapshot diario de GA4
  - analytics_top_pages   top páginas por fecha
  - pr_items              pipeline de PR (press releases, pitches, reviews)
  - content_calendar      calendario editorial 4 semanas
  - marketing_roi         resumen semanal de ROI

Se inicializa via init_marketing_db(), llamada desde utils/web_db.py:init_web_db()
"""

import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/leads.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_marketing_db():
    """Create all marketing tables. Safe to call multiple times (IF NOT EXISTS)."""
    conn = _get_conn()
    c = conn.cursor()

    # ── Content library ──────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS marketing_content (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            content_type    TEXT NOT NULL,
            title           TEXT,
            body            TEXT NOT NULL,
            meta_description TEXT,
            keywords        TEXT,
            platform        TEXT,
            status          TEXT DEFAULT 'draft',
            scheduled_at    TIMESTAMP,
            published_at    TIMESTAMP,
            external_url    TEXT,
            external_id     TEXT,
            agent_key       TEXT NOT NULL,
            ai_source       TEXT DEFAULT 'claude',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_mktcontent_type   ON marketing_content(content_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mktcontent_status ON marketing_content(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mktcontent_agent  ON marketing_content(agent_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mktcontent_sched  ON marketing_content(scheduled_at)")

    # ── SEO keywords ──────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS seo_keywords (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword             TEXT NOT NULL UNIQUE,
            target_url          TEXT,
            current_position    INTEGER,
            previous_position   INTEGER,
            search_volume       INTEGER,
            competition         TEXT,
            clicks              INTEGER DEFAULT 0,
            impressions         INTEGER DEFAULT 0,
            ctr                 REAL DEFAULT 0.0,
            last_checked        TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_seokw_position ON seo_keywords(current_position)")

    # ── SEO sitemap ───────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS seo_sitemap (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL UNIQUE,
            priority    REAL DEFAULT 0.5,
            changefreq  TEXT DEFAULT 'weekly',
            last_mod    DATE,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Social posts ──────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS social_posts (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id       INTEGER,
            platform         TEXT NOT NULL,
            post_text        TEXT NOT NULL,
            image_url        TEXT,
            scheduled_at     TIMESTAMP,
            posted_at        TIMESTAMP,
            external_post_id TEXT,
            likes            INTEGER DEFAULT 0,
            comments         INTEGER DEFAULT 0,
            shares           INTEGER DEFAULT 0,
            impressions      INTEGER DEFAULT 0,
            status           TEXT DEFAULT 'queued',
            error_message    TEXT,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(content_id) REFERENCES marketing_content(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_socialposts_platform  ON social_posts(platform)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_socialposts_status    ON social_posts(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_socialposts_scheduled ON social_posts(scheduled_at)")

    # ── Email campaigns ───────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            campaign_type   TEXT NOT NULL,
            subject         TEXT NOT NULL,
            html_body       TEXT NOT NULL,
            text_body       TEXT,
            from_name       TEXT DEFAULT 'MLeads',
            from_email      TEXT,
            status          TEXT DEFAULT 'active',
            sequence_day    INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Email sends log ───────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS email_sends (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id       INTEGER,
            recipient_email   TEXT NOT NULL,
            recipient_user_id INTEGER,
            sent_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            opened_at         TIMESTAMP,
            clicked_at        TIMESTAMP,
            bounced           INTEGER DEFAULT 0,
            unsubscribed      INTEGER DEFAULT 0,
            sendgrid_msg_id   TEXT,
            FOREIGN KEY(campaign_id) REFERENCES email_campaigns(id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_emailsends_campaign   ON email_sends(campaign_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_emailsends_recipient  ON email_sends(recipient_email)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_emailsends_sent       ON email_sends(sent_at)")

    # ── Paid ads campaigns ────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS ads_campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            platform        TEXT NOT NULL,
            campaign_id     TEXT NOT NULL,
            campaign_name   TEXT NOT NULL,
            status          TEXT,
            budget_daily    REAL,
            impressions     INTEGER DEFAULT 0,
            clicks          INTEGER DEFAULT 0,
            conversions     INTEGER DEFAULT 0,
            spend           REAL DEFAULT 0.0,
            ctr             REAL DEFAULT 0.0,
            cpc             REAL DEFAULT 0.0,
            roas            REAL DEFAULT 0.0,
            quality_score   INTEGER,
            fetched_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(platform, campaign_id)
        )
    """)

    # ── Ad copy variants (A/B) ────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS ad_copy_variants (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id       INTEGER,
            headline          TEXT NOT NULL,
            description       TEXT NOT NULL,
            cta               TEXT,
            ai_source         TEXT DEFAULT 'claude',
            performance_score REAL DEFAULT 0.0,
            status            TEXT DEFAULT 'suggested',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(campaign_id) REFERENCES ads_campaigns(id)
        )
    """)

    # ── Analytics snapshots (GA4) ─────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date    DATE NOT NULL,
            sessions         INTEGER DEFAULT 0,
            users            INTEGER DEFAULT 0,
            new_users        INTEGER DEFAULT 0,
            pageviews        INTEGER DEFAULT 0,
            bounce_rate      REAL DEFAULT 0.0,
            avg_session_dur  REAL DEFAULT 0.0,
            conversions      INTEGER DEFAULT 0,
            conversion_rate  REAL DEFAULT 0.0,
            organic_traffic  INTEGER DEFAULT 0,
            paid_traffic     INTEGER DEFAULT 0,
            direct_traffic   INTEGER DEFAULT 0,
            social_traffic   INTEGER DEFAULT 0,
            source_data      TEXT,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(snapshot_date)
        )
    """)

    # ── Top pages per snapshot ────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS analytics_top_pages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date DATE NOT NULL,
            page_path     TEXT NOT NULL,
            sessions      INTEGER DEFAULT 0,
            pageviews     INTEGER DEFAULT 0,
            bounce_rate   REAL,
            avg_time      REAL,
            UNIQUE(snapshot_date, page_path)
        )
    """)

    # ── PR & Reputation ───────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS pr_items (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            item_type        TEXT NOT NULL,
            title            TEXT NOT NULL,
            body             TEXT,
            publication      TEXT,
            publication_url  TEXT,
            status           TEXT DEFAULT 'draft',
            review_platform  TEXT,
            review_rating    REAL,
            sentiment        TEXT,
            ai_response      TEXT,
            published_at     TIMESTAMP,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_pritems_type   ON pr_items(item_type)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pritems_status ON pr_items(status)")

    # ── Content calendar ──────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS content_calendar (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_date  DATE NOT NULL,
            content_type    TEXT NOT NULL,
            title           TEXT,
            topic           TEXT,
            target_keyword  TEXT,
            assigned_agent  TEXT,
            status          TEXT DEFAULT 'planned',
            content_id      INTEGER,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(content_id) REFERENCES marketing_content(id)
        )
    """)

    # ── Marketing ROI (weekly rollup) ─────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS marketing_roi (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start       DATE NOT NULL UNIQUE,
            total_spend      REAL DEFAULT 0.0,
            signups          INTEGER DEFAULT 0,
            trial_starts     INTEGER DEFAULT 0,
            paid_conversions INTEGER DEFAULT 0,
            mrr_added        REAL DEFAULT 0.0,
            organic_sessions INTEGER DEFAULT 0,
            social_reach     INTEGER DEFAULT 0,
            content_pieces   INTEGER DEFAULT 0,
            cpa              REAL DEFAULT 0.0,
            ltv_estimate     REAL DEFAULT 0.0,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    logger.info("[marketing_db] All marketing tables initialized")


# ── Helper functions ──────────────────────────────────────────────────

def save_content(content_type: str, title: str, body: str, agent_key: str,
                 platform: str = None, keywords: list = None,
                 meta_description: str = None, ai_source: str = "claude",
                 status: str = "draft") -> int:
    """Insert a content record. Returns the new row id."""
    import json
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO marketing_content
            (content_type, title, body, agent_key, platform, keywords,
             meta_description, ai_source, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        content_type, title, body, agent_key, platform,
        json.dumps(keywords or []), meta_description, ai_source, status,
    ))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def queue_social_post(platform: str, post_text: str, scheduled_at=None,
                      content_id: int = None, image_url: str = None) -> int:
    """Queue a social post for dispatch. Returns row id."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO social_posts (platform, post_text, scheduled_at, content_id, image_url)
        VALUES (?, ?, ?, ?, ?)
    """, (platform, post_text, scheduled_at, content_id, image_url))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_queued_posts(platform: str = None) -> list:
    """Return social posts ready to dispatch (status=queued, scheduled_at <= now)."""
    conn = _get_conn()
    c = conn.cursor()
    if platform:
        c.execute("""
            SELECT * FROM social_posts
            WHERE status = 'queued'
              AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
              AND platform = ?
            ORDER BY scheduled_at ASC
        """, (platform,))
    else:
        c.execute("""
            SELECT * FROM social_posts
            WHERE status = 'queued'
              AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
            ORDER BY scheduled_at ASC
        """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def mark_post_sent(post_id: int, external_post_id: str = None):
    conn = _get_conn()
    conn.execute("""
        UPDATE social_posts
        SET status = 'posted', posted_at = datetime('now'), external_post_id = ?
        WHERE id = ?
    """, (external_post_id, post_id))
    conn.commit()
    conn.close()


def mark_post_failed(post_id: int, error: str):
    conn = _get_conn()
    conn.execute("""
        UPDATE social_posts
        SET status = 'failed', error_message = ?
        WHERE id = ?
    """, (error[:500], post_id))
    conn.commit()
    conn.close()


def log_email_send(campaign_id: int, recipient_email: str,
                   recipient_user_id: int = None, sendgrid_msg_id: str = None) -> int:
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO email_sends (campaign_id, recipient_email, recipient_user_id, sendgrid_msg_id)
        VALUES (?, ?, ?, ?)
    """, (campaign_id, recipient_email, recipient_user_id, sendgrid_msg_id))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_email_send_day(user_id: int, campaign_type: str) -> int:
    """Return how many emails of this campaign_type have been sent to this user."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*) FROM email_sends es
        JOIN email_campaigns ec ON ec.id = es.campaign_id
        WHERE es.recipient_user_id = ? AND ec.campaign_type = ?
    """, (user_id, campaign_type))
    count = c.fetchone()[0]
    conn.close()
    return count


def save_analytics_snapshot(date: str, data: dict):
    """Upsert a daily GA4 snapshot."""
    conn = _get_conn()
    conn.execute("""
        INSERT INTO analytics_snapshots
            (snapshot_date, sessions, users, new_users, pageviews,
             bounce_rate, avg_session_dur, conversions, conversion_rate,
             organic_traffic, paid_traffic, direct_traffic, social_traffic, source_data)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(snapshot_date) DO UPDATE SET
            sessions        = excluded.sessions,
            users           = excluded.users,
            new_users       = excluded.new_users,
            pageviews       = excluded.pageviews,
            bounce_rate     = excluded.bounce_rate,
            avg_session_dur = excluded.avg_session_dur,
            conversions     = excluded.conversions,
            conversion_rate = excluded.conversion_rate,
            organic_traffic = excluded.organic_traffic,
            paid_traffic    = excluded.paid_traffic,
            direct_traffic  = excluded.direct_traffic,
            social_traffic  = excluded.social_traffic,
            source_data     = excluded.source_data
    """, (
        date,
        data.get("sessions", 0), data.get("users", 0), data.get("new_users", 0),
        data.get("pageviews", 0), data.get("bounce_rate", 0.0),
        data.get("avg_session_dur", 0.0), data.get("conversions", 0),
        data.get("conversion_rate", 0.0), data.get("organic_traffic", 0),
        data.get("paid_traffic", 0), data.get("direct_traffic", 0),
        data.get("social_traffic", 0),
        __import__("json").dumps(data.get("source_data", {})),
    ))
    conn.commit()
    conn.close()


def get_last_n_snapshots(n: int = 14) -> list:
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM analytics_snapshots
        ORDER BY snapshot_date DESC LIMIT ?
    """, (n,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def save_pr_item(item_type: str, title: str, body: str = None,
                 publication: str = None, status: str = "draft",
                 review_platform: str = None, review_rating: float = None,
                 ai_response: str = None) -> int:
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO pr_items
            (item_type, title, body, publication, status,
             review_platform, review_rating, ai_response)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (item_type, title, body, publication, status,
          review_platform, review_rating, ai_response))
    row_id = c.lastrowid
    conn.commit()
    conn.close()
    return row_id


def upsert_seo_keyword(keyword: str, position: int = None,
                       clicks: int = 0, impressions: int = 0,
                       ctr: float = 0.0, target_url: str = None):
    conn = _get_conn()
    conn.execute("""
        INSERT INTO seo_keywords (keyword, current_position, clicks, impressions, ctr, target_url, last_checked)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(keyword) DO UPDATE SET
            previous_position = current_position,
            current_position  = excluded.current_position,
            clicks            = excluded.clicks,
            impressions       = excluded.impressions,
            ctr               = excluded.ctr,
            last_checked      = excluded.last_checked
    """, (keyword, position, clicks, impressions, ctr, target_url))
    conn.commit()
    conn.close()


def get_marketing_overview() -> dict:
    """Return KPI summary for the marketing dashboard."""
    conn = _get_conn()
    c = conn.cursor()

    # Content stats
    c.execute("SELECT COUNT(*) FROM marketing_content WHERE status = 'published'")
    published_content = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM marketing_content WHERE status = 'draft'")
    draft_content = c.fetchone()[0]

    # Social reach (last 30 days)
    c.execute("""
        SELECT COALESCE(SUM(impressions), 0), COALESCE(SUM(likes + shares), 0)
        FROM social_posts WHERE posted_at >= date('now', '-30 days')
    """)
    row = c.fetchone()
    social_impressions = row[0]
    social_engagement = row[1]

    # Email stats
    c.execute("SELECT COUNT(*) FROM email_sends WHERE sent_at >= date('now', '-30 days')")
    emails_sent = c.fetchone()[0]

    # Latest analytics snapshot
    c.execute("SELECT * FROM analytics_snapshots ORDER BY snapshot_date DESC LIMIT 1")
    snap = c.fetchone()
    latest_sessions = dict(snap)["sessions"] if snap else 0
    latest_conversions = dict(snap)["conversions"] if snap else 0

    # Ads spend
    c.execute("SELECT COALESCE(SUM(spend), 0) FROM ads_campaigns WHERE fetched_at >= date('now', '-30 days')")
    total_spend = c.fetchone()[0]

    # PR items
    c.execute("SELECT COUNT(*) FROM pr_items WHERE status = 'published'")
    pr_published = c.fetchone()[0]

    conn.close()
    return {
        "content": {"published": published_content, "drafts": draft_content},
        "social":  {"impressions_30d": social_impressions, "engagement_30d": social_engagement},
        "email":   {"sent_30d": emails_sent},
        "analytics": {"sessions": latest_sessions, "conversions": latest_conversions},
        "ads":     {"spend_30d": round(total_spend, 2)},
        "pr":      {"published": pr_published},
    }
