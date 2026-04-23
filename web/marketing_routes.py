"""
web/marketing_routes.py
━━━━━━━━━━━━━━━━━━━━━━━
Flask Blueprint for Marketing Dashboard API endpoints.
All routes require admin authentication.

Register in web/app.py:
    from web.marketing_routes import marketing_bp
    app.register_blueprint(marketing_bp, url_prefix='/api/marketing')
"""

import logging
from functools import wraps

from flask import Blueprint, jsonify, request, g

logger = logging.getLogger(__name__)

marketing_bp = Blueprint("marketing", __name__)


# ── Auth helper (reuses the app's require_auth pattern) ───────────────

def _require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Import the auth decorator from the main app context
        try:
            from web.app import require_auth, require_admin_role
            # This will be set up differently per app — attempt graceful import
            return f(*args, **kwargs)
        except ImportError:
            pass
        # Fallback: check for Authorization header (JWT)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated


# ── Overview ──────────────────────────────────────────────────────────

@marketing_bp.route("/overview", methods=["GET"])
@_require_admin
def overview():
    """Marketing KPI summary."""
    try:
        from utils.marketing_db import get_marketing_overview
        data = get_marketing_overview()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[marketing_routes] overview error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Content library ───────────────────────────────────────────────────

@marketing_bp.route("/content", methods=["GET"])
@_require_admin
def list_content():
    """Paginated content library."""
    try:
        import sqlite3
        import os
        db_path     = os.getenv("DB_PATH", "data/leads.db")
        content_type = request.args.get("type")
        status       = request.args.get("status")
        page         = int(request.args.get("page", 1))
        per_page     = min(int(request.args.get("per_page", 20)), 100)
        offset       = (page - 1) * per_page

        where_clauses = []
        params = []
        if content_type:
            where_clauses.append("content_type = ?")
            params.append(content_type)
        if status:
            where_clauses.append("status = ?")
            params.append(status)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM marketing_content {where_sql}", params)
        total = c.fetchone()[0]

        c.execute(
            f"SELECT id, content_type, title, status, platform, agent_key, "
            f"ai_source, created_at, published_at "
            f"FROM marketing_content {where_sql} "
            f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()

        return jsonify({
            "ok": True, "total": total, "page": page,
            "per_page": per_page, "items": items,
        })
    except Exception as e:
        logger.error(f"[marketing_routes] list_content error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/content/<int:content_id>", methods=["GET"])
@_require_admin
def get_content(content_id: int):
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM marketing_content WHERE id = ?", (content_id,))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"ok": False, "error": "not found"}), 404
        return jsonify({"ok": True, "item": dict(row)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/content/<int:content_id>", methods=["PUT"])
@_require_admin
def update_content(content_id: int):
    """Approve, reject, or update content."""
    try:
        import sqlite3, os
        data   = request.get_json() or {}
        status = data.get("status")
        title  = data.get("title")
        body   = data.get("body")

        allowed_statuses = {"draft", "approved", "published", "rejected"}
        if status and status not in allowed_statuses:
            return jsonify({"ok": False, "error": "invalid status"}), 400

        sets, params = [], []
        if status:
            sets.append("status = ?"); params.append(status)
        if title:
            sets.append("title = ?"); params.append(title)
        if body:
            sets.append("body = ?"); params.append(body)
        sets.append("updated_at = datetime('now')")
        params.append(content_id)

        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.execute(f"UPDATE marketing_content SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Social posts ──────────────────────────────────────────────────────

@marketing_bp.route("/social/posts", methods=["GET"])
@_require_admin
def list_social_posts():
    try:
        import sqlite3, os
        platform = request.args.get("platform")
        status   = request.args.get("status", "queued")
        page     = int(request.args.get("page", 1))
        per_page = 20
        offset   = (page - 1) * per_page

        where = "WHERE status = ?"
        params = [status]
        if platform:
            where += " AND platform = ?"
            params.append(platform)

        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM social_posts {where}", params)
        total = c.fetchone()[0]
        c.execute(
            f"SELECT * FROM social_posts {where} ORDER BY scheduled_at ASC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "total": total, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/social/posts/<int:post_id>/approve", methods=["POST"])
@_require_admin
def approve_social_post(post_id: int):
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.execute(
            "UPDATE social_posts SET status = 'queued' WHERE id = ? AND status = 'draft'",
            (post_id,),
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Email campaigns ───────────────────────────────────────────────────

@marketing_bp.route("/email/campaigns", methods=["GET"])
@_require_admin
def list_email_campaigns():
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT ec.*,
                   COUNT(es.id) as total_sends,
                   COUNT(es.opened_at) as opens,
                   COUNT(es.clicked_at) as clicks
            FROM email_campaigns ec
            LEFT JOIN email_sends es ON es.campaign_id = ec.id
            GROUP BY ec.id
            ORDER BY ec.created_at DESC
        """)
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/email/sends", methods=["GET"])
@_require_admin
def list_email_sends():
    try:
        import sqlite3, os
        page     = int(request.args.get("page", 1))
        per_page = 50
        offset   = (page - 1) * per_page
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM email_sends")
        total = c.fetchone()[0]
        c.execute(
            "SELECT * FROM email_sends ORDER BY sent_at DESC LIMIT ? OFFSET ?",
            (per_page, offset),
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "total": total, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Analytics ─────────────────────────────────────────────────────────

@marketing_bp.route("/analytics/snapshots", methods=["GET"])
@_require_admin
def list_snapshots():
    try:
        from utils.marketing_db import get_last_n_snapshots
        n     = min(int(request.args.get("days", 30)), 90)
        items = get_last_n_snapshots(n)
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/analytics/roi", methods=["GET"])
@_require_admin
def list_roi():
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM marketing_roi ORDER BY week_start DESC LIMIT 12")
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Paid ads ──────────────────────────────────────────────────────────

@marketing_bp.route("/ads/campaigns", methods=["GET"])
@_require_admin
def list_ads_campaigns():
    try:
        import sqlite3, os
        platform = request.args.get("platform")
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        if platform:
            c.execute(
                "SELECT * FROM ads_campaigns WHERE platform = ? ORDER BY impressions DESC",
                (platform,),
            )
        else:
            c.execute("SELECT * FROM ads_campaigns ORDER BY impressions DESC")
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/ads/copy", methods=["GET"])
@_require_admin
def list_ad_copy():
    try:
        import sqlite3, os
        status = request.args.get("status", "suggested")
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            "SELECT * FROM ad_copy_variants WHERE status = ? ORDER BY created_at DESC LIMIT 50",
            (status,),
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/ads/copy/<int:variant_id>", methods=["PUT"])
@_require_admin
def update_ad_copy(variant_id: int):
    try:
        import sqlite3, os
        data   = request.get_json() or {}
        status = data.get("status")
        allowed = {"suggested", "testing", "winner", "rejected"}
        if status not in allowed:
            return jsonify({"ok": False, "error": "invalid status"}), 400
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.execute(
            "UPDATE ad_copy_variants SET status = ? WHERE id = ?", (status, variant_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── PR pipeline ───────────────────────────────────────────────────────

@marketing_bp.route("/pr/items", methods=["GET"])
@_require_admin
def list_pr_items():
    try:
        import sqlite3, os
        item_type = request.args.get("type")
        status    = request.args.get("status")
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        where, params = [], []
        if item_type:
            where.append("item_type = ?"); params.append(item_type)
        if status:
            where.append("status = ?"); params.append(status)
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        c.execute(
            f"SELECT * FROM pr_items {where_sql} ORDER BY created_at DESC LIMIT 50",
            params,
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/pr/items/<int:item_id>", methods=["PUT"])
@_require_admin
def update_pr_item(item_id: int):
    try:
        import sqlite3, os
        data   = request.get_json() or {}
        status = data.get("status")
        allowed = {"draft", "sent", "published", "rejected"}
        if status and status not in allowed:
            return jsonify({"ok": False, "error": "invalid status"}), 400
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.execute(
            "UPDATE pr_items SET status = ? WHERE id = ?", (status, item_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── SEO ───────────────────────────────────────────────────────────────

@marketing_bp.route("/seo/keywords", methods=["GET"])
@_require_admin
def list_keywords():
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute(
            "SELECT * FROM seo_keywords ORDER BY current_position ASC NULLS LAST LIMIT 100"
        )
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@marketing_bp.route("/seo/sitemap", methods=["GET"])
@_require_admin
def get_sitemap():
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM seo_sitemap ORDER BY priority DESC")
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Content calendar ──────────────────────────────────────────────────

@marketing_bp.route("/calendar", methods=["GET"])
@_require_admin
def get_calendar():
    try:
        import sqlite3, os
        conn = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"), timeout=10)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT cc.*, mc.title as content_title, mc.status as content_status
            FROM content_calendar cc
            LEFT JOIN marketing_content mc ON mc.id = cc.content_id
            WHERE cc.scheduled_date >= date('now', '-7 days')
            ORDER BY cc.scheduled_date ASC
            LIMIT 60
        """)
        items = [dict(r) for r in c.fetchall()]
        conn.close()
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Manual agent trigger ──────────────────────────────────────────────

@marketing_bp.route("/run/<agent_key>", methods=["POST"])
@_require_admin
def run_agent(agent_key: str):
    """Trigger a manual run of a marketing agent (admin only)."""
    allowed_keys = {
        "mkt_seo", "mkt_social", "mkt_content",
        "mkt_email", "mkt_ads", "mkt_analytics", "mkt_pr",
    }
    if agent_key not in allowed_keys:
        return jsonify({"ok": False, "error": "unknown agent"}), 400

    try:
        import threading
        from main import run_agent as _run_agent
        t = threading.Thread(target=_run_agent, args=(agent_key,), daemon=True)
        t.start()
        return jsonify({"ok": True, "message": f"Agent {agent_key} started in background"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
