import os
from flask import Blueprint, request, jsonify, render_template
from datetime import datetime
import logging
from utils.web_db import get_db_connection
from workers.inspection_scheduler import get_scheduler_status
logger = logging.getLogger('mleads')
bp = Blueprint("static", __name__)
@bp.route('/', methods=['GET'])
def index():
    """Serve the main dashboard HTML."""
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Dashboard not found"}), 404




@bp.route('/login.html', methods=['GET'])
def login_page():
    """Serve the login page."""
    login_path = os.path.join(os.path.dirname(__file__), 'templates', 'login.html')
    try:
        with open(login_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Login page not found"}), 404




@bp.route('/swipe', methods=['GET'])
@bp.route('/swipe.html', methods=['GET'])
def swipe_page():
    """Serve the public Tinder-style swipe page (no auth required)."""
    swipe_path = os.path.join(os.path.dirname(__file__), 'templates', 'swipe.html')
    try:
        with open(swipe_path, 'r', encoding='utf-8') as f:
            html = f.read()
    except FileNotFoundError:
        return jsonify({"error": "Swipe page not found"}), 404
    # Inject OAuth client identifiers and API keys from environment
    html = html.replace('__GOOGLE_CLIENT_ID__', os.getenv('GOOGLE_CLIENT_ID', ''))
    html = html.replace('__FACEBOOK_APP_ID__', os.getenv('FACEBOOK_APP_ID', ''))
    html = html.replace('__GOOGLE_MAPS_API_KEY__', os.getenv('GOOGLE_MAPS_API_KEY', ''))
    from flask import make_response
    resp = make_response(html)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp




@bp.route('/<path:filename>', methods=['GET'])
def catch_all(filename):
    """Catch all routes and serve dashboard for SPA routing."""
    if filename.endswith('.json') or filename.startswith('api'):
        return jsonify({"error": "Not found"}), 404
    # Check if user has valid token before serving dashboard
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token and filename not in ['login.html', '']:
        # If no token and not login page, let JavaScript redirect to login
        pass
    return index()




@bp.route('/health', methods=['GET'])
def health():
    """Health check endpoint with full system status."""
    now = datetime.utcnow()
    status = "ok"
    details = {}

    # Database connectivity + lead count
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM consolidated_leads")
        leads_count = c.fetchone()[0]
        c.execute("""
            SELECT COUNT(*), MAX(created_at)
            FROM scheduled_inspections
            WHERE inspection_date >= date('now')
        """)
        row = c.fetchone()
        future_inspections = row[0]
        last_inspection_saved = row[1]
        conn.close()
        details["db"] = {
            "status": "ok",
            "leads_count": leads_count,
            "future_inspections": future_inspections,
            "last_inspection_saved": last_inspection_saved,
        }
    except Exception as e:
        status = "degraded"
        details["db"] = {"status": "error", "error": str(e)}

    # Scheduler status
    try:
        sched = get_scheduler_status()
        details["scheduler"] = sched
        if not sched.get("running"):
            status = "degraded"
    except Exception as e:
        status = "degraded"
        details["scheduler"] = {"status": "error", "error": str(e)}

    return jsonify({
        "status": status,
        "timestamp": now.isoformat() + "Z",
        **details,
    }), 200 if status == "ok" else 503




@bp.route('/pipeline')
def pipeline_page():
    """Pipeline Kanban page."""
    return render_template("pipeline.html")





