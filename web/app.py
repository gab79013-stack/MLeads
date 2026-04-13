"""
app.py — Flask API server for multi-user lead dashboard

REST API endpoints for:
- Authentication (login, refresh, logout)
- Lead retrieval and filtering
- User stats and audit logs
- Admin user/role management
"""

import os
import json
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, send_file
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from utils.web_db import (
    init_web_db, seed_cities_and_agents, get_db_connection,
    insert_scheduled_inspection, get_upcoming_inspections,
    get_inspections_by_jurisdiction, cleanup_old_inspections
)
from web.auth import (
    require_auth, generate_tokens, verify_password, hash_password,
    get_user_permissions, get_user_cities, get_user_agents,
    check_permission, revoke_token, AuthError
)
from workers.inspection_scheduler import (
    start_inspection_scheduler, get_scheduler_status, fetch_inspections_now
)
from workers.telegram_bot import start_bot_worker
from utils import bot_users as bu
from utils import billing

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# ─── CORS ────────────────────────────────────────────────
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*")
if _allowed_origins != "*":
    CORS(app, origins=[o.strip() for o in _allowed_origins.split(",")])
else:
    import warnings
    warnings.warn(
        "ALLOWED_ORIGINS is not set — CORS is open to all origins. "
        "Set ALLOWED_ORIGINS in .env for production (e.g., https://your-domain.com).",
        stacklevel=1,
    )
    CORS(app)

# ─── Rate Limiting ────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # no global limit — set per-route
    storage_uri="memory://",
)

logger = logging.getLogger("web_api")


# ─────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────

def log_audit(user_id, action, resource_type, resource_id, details=""):
    """Log an action to the audit log."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO audit_logs (user_id, action, resource_type, resource_id, details)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, action, resource_type, resource_id, details))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to log audit: {e}")


# ─────────────────────────────────────────────────────────
# Error Handlers
# ─────────────────────────────────────────────────────────

@app.errorhandler(400)
def bad_request(e):
    return jsonify({"error": "Bad request"}), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Server error"}), 500


# ─────────────────────────────────────────────────────────
# Dashboard & Static Files
# ─────────────────────────────────────────────────────────

@app.route('/', methods=['GET'])
def index():
    """Serve the main dashboard HTML."""
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'index.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Dashboard not found"}), 404


@app.route('/login.html', methods=['GET'])
def login_page():
    """Serve the login page."""
    login_path = os.path.join(os.path.dirname(__file__), 'templates', 'login.html')
    try:
        with open(login_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Login page not found"}), 404


@app.route('/swipe', methods=['GET'])
@app.route('/swipe.html', methods=['GET'])
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
    return html


@app.route('/<path:filename>', methods=['GET'])
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


# ─────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────

@app.route('/api/health', methods=['GET'])
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


# ─────────────────────────────────────────────────────────
# Authentication Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
@limiter.limit("10 per minute")
def login():
    """Login with username and password."""
    data = request.get_json() or {}

    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"error": "Missing username or password"}), 400

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, password_hash FROM users WHERE username = ? AND is_active = 1", (username,))
    user = c.fetchone()
    conn.close()

    if not user or not verify_password(password, user['password_hash']):
        return jsonify({"error": "Invalid credentials"}), 401

    access_token, refresh_token = generate_tokens(user['id'])

    return jsonify({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": int(os.getenv("JWT_ACCESS_EXPIRY", 3600))
    }), 200


@app.route('/api/auth/refresh', methods=['POST'])
def refresh():
    """Refresh access token using refresh token."""
    data = request.get_json() or {}
    refresh_token = data.get('refresh_token')

    if not refresh_token:
        return jsonify({"error": "Missing refresh token"}), 400

    try:
        from web.auth import verify_token
        payload = verify_token(refresh_token)

        if payload.get('type') != 'refresh':
            return jsonify({"error": "Invalid token type"}), 401

        # Generate new access token
        from web.auth import ACCESS_TOKEN_EXPIRY
        from datetime import timedelta
        now = datetime.utcnow()

        import jwt
        from web.auth import SECRET_KEY

        access_payload = {
            "user_id": payload["user_id"],
            "type": "access",
            "iat": now,
            "exp": now + timedelta(seconds=ACCESS_TOKEN_EXPIRY),
        }

        access_token = jwt.encode(access_payload, SECRET_KEY, algorithm="HS256")

        # Update session
        conn = get_db_connection()
        c = conn.cursor()
        expires_at = datetime.utcnow() + timedelta(seconds=ACCESS_TOKEN_EXPIRY)
        c.execute("""
            UPDATE sessions SET access_token = ?, expires_at = ?
            WHERE refresh_token = ?
        """, (access_token, expires_at, refresh_token))
        conn.commit()
        conn.close()

        return jsonify({
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": ACCESS_TOKEN_EXPIRY
        }), 200

    except AuthError as e:
        return jsonify({"error": str(e)}), 401


@app.route('/api/auth/logout', methods=['POST'])
@require_auth
def logout():
    """Logout and revoke token."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    revoke_token(token)
    return jsonify({"status": "logged out"}), 200


# ─────────────────────────────────────────────────────────
# User Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/user', methods=['GET'])
@require_auth
def get_current_user():
    """Get current logged-in user info."""
    user_id = g.user_id

    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT u.id, u.username, u.email, u.full_name, u.expires_at, u.created_at
        FROM users u WHERE u.id = ?
    """, (user_id,))
    user = dict(c.fetchone())

    # Get user's roles
    c.execute("""
        SELECT r.name FROM user_roles ur
        JOIN roles r ON ur.role_id = r.id
        WHERE ur.user_id = ?
    """, (user_id,))
    roles = [row[0] for row in c.fetchall()]

    # Get accessible cities and agents
    cities = get_user_cities(user_id)
    agents = get_user_agents(user_id)
    permissions = get_user_permissions(user_id)

    conn.close()

    user['roles'] = roles
    user['permissions'] = sorted(permissions)
    user['cities'] = cities
    user['agents'] = agents

    return jsonify(user), 200


# ─────────────────────────────────────────────────────────
# Leads Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/leads', methods=['GET'])
@require_auth
def list_leads():
    """List leads with filtering (city, agent, score, date range)."""
    user_id = g.user_id

    # Check permission
    if not check_permission(user_id, "leads", "view"):
        return jsonify({"error": "Permission denied"}), 403

    # Get filter parameters
    city_id = request.args.get('city_id', type=int)
    agent_name = request.args.get('agent')
    min_score = request.args.get('min_score', 0, type=int)
    min_value = request.args.get('min_value', 0, type=int)
    status = request.args.get('status', 'all')
    if status not in {'all', 'new', 'contacted', 'pending'}:
        return jsonify({"error": "Invalid status. Must be one of: all, new, contacted, pending"}), 400
    inspection_days = request.args.get('inspection_days', type=int)  # Filter leads with upcoming inspections within N days
    page = request.args.get('page', 1, type=int)
    per_page = 100

    # Get user's accessible cities and agents (by name)
    accessible_cities = get_user_cities(user_id)
    accessible_agents = get_user_agents(user_id)

    if not accessible_cities or not accessible_agents:
        return jsonify({"leads": [], "total": 0, "pages": 0}), 200

    city_names = [c['name'] for c in accessible_cities]
    agent_names = [a['name'] for a in accessible_agents]

    # Build query against consolidated_leads
    # Schema: address_key, address, city (text), agent_sources, first_seen, last_updated, lead_data (JSON), notified
    conn = get_db_connection()
    c = conn.cursor()

    where_clauses = []
    params = []

    # City filter (consolidated_leads.city is text name, not ID)
    if city_id:
        # Look up city name from ID
        c.execute("SELECT name FROM cities WHERE id = ?", (city_id,))
        city_row = c.fetchone()
        if city_row:
            where_clauses.append("l.city = ?")
            params.append(city_row[0])
    else:
        # Filter by accessible city names
        placeholders = ','.join('?' * len(city_names))
        where_clauses.append(f"l.city IN ({placeholders})")
        params.extend(city_names)

    # Agent filter (agent_sources is comma-separated agent keys)
    if agent_name and agent_name in agent_names:
        where_clauses.append("l.agent_sources LIKE ?")
        params.append(f"%{agent_name}%")
    elif agent_names:
        or_clauses = ' OR '.join(['l.agent_sources LIKE ?' for _ in agent_names])
        where_clauses.append(f"({or_clauses})")
        params.extend([f"%{a}%" for a in agent_names])

    # Score filter (extract from JSON)
    if min_score > 0:
        where_clauses.append("CAST(json_extract(l.lead_data, '$._scoring.score') AS INTEGER) >= ?")
        params.append(min_score)

    # Value filter (extract from JSON)
    if min_value > 0:
        where_clauses.append("CAST(COALESCE(json_extract(l.lead_data, '$.value_float'), 0) AS INTEGER) >= ?")
        params.append(min_value)

    # Status filter
    if status == 'contacted':
        where_clauses.append("EXISTS (SELECT 1 FROM lead_contacts WHERE lead_id = l.address_key AND user_id = ?)")
        params.append(user_id)
    elif status == 'new':
        where_clauses.append("NOT EXISTS (SELECT 1 FROM lead_contacts WHERE lead_id = l.address_key AND user_id = ?)")
        params.append(user_id)

    # Inspection days filter (leads with upcoming inspections within N days)
    if inspection_days and inspection_days > 0:
        where_clauses.append("""
            CAST(json_extract(l.lead_data, '$.next_scheduled_inspection_date') AS DATE)
            BETWEEN date('now') AND date('now', '+' || ? || ' days')
        """)
        params.append(inspection_days)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Get total count
    c.execute(f"SELECT COUNT(*) FROM consolidated_leads l WHERE {where_sql}", params)
    total = c.fetchone()[0]

    # Get paginated results
    offset = (page - 1) * per_page
    c.execute(f"""
        SELECT l.address_key, l.address, l.city, l.agent_sources,
               l.first_seen, l.last_updated, l.lead_data, l.primary_service_type
        FROM consolidated_leads l
        WHERE {where_sql}
        ORDER BY l.last_updated DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])


    # Fetch all rows
    rows = c.fetchall()

    # Get all contacted leads for this user in one query (fixes N+1 problem)
    lead_ids = [row['address_key'] for row in rows]
    contacted_leads = set()
    if lead_ids:
        placeholders = ','.join('?' * len(lead_ids))
        c.execute(f"""
            SELECT DISTINCT lead_id FROM lead_contacts
            WHERE user_id = ? AND lead_id IN ({placeholders})
        """, [user_id] + lead_ids)
        contacted_leads = {row[0] for row in c.fetchall()}

    # Fetch all service types in one query (fixes N+1 problem)
    c.execute("SELECT name, display_label, emoji FROM service_types")
    service_types_map = {row[0]: {'label': row[1], 'emoji': row[2]} for row in c.fetchall()}

    leads = []
    for row in rows:
        row_dict = dict(row)
        # Parse lead_data JSON for display fields
        lead_data = {}
        try:
            lead_data = json.loads(row_dict.get('lead_data', '{}') or '{}')
        except Exception:
            pass

        # Get service type information
        service_type = row_dict.get('primary_service_type') or (row_dict['agent_sources'].split(',')[0] if row_dict['agent_sources'] else None)
        service_info = service_types_map.get(service_type, {})

        scoring = lead_data.get('_scoring', {})
        lead = {
            'id': row_dict['address_key'],
            'address': row_dict['address'],
            'city': row_dict['city'],
            'score': scoring.get('score', 0),
            'grade': scoring.get('grade', ''),
            'grade_emoji': scoring.get('grade_emoji', ''),
            'scoring_reasons': (scoring.get('reasons') or [])[:3],
            'value': lead_data.get('value_float', 0),
            'source': row_dict['agent_sources'],
            'source_url': lead_data.get('source_url', ''),
            'description': (lead_data.get('description') or '')[:240],
            'created_at': row_dict['first_seen'],
            'last_updated': row_dict['last_updated'],
            'contractor': lead_data.get('contractor', ''),
            'contact_phone': lead_data.get('contact_phone', ''),
            'contact_email': lead_data.get('contact_email', ''),
            'owner': lead_data.get('owner', ''),
            'phase': lead_data.get('phase', ''),
            'permit_id': lead_data.get('permit_id', ''),
            'contacted': row_dict['address_key'] in contacted_leads,
            'service_type': service_type,
            'service_label': service_info.get('label', ''),
            'service_emoji': service_info.get('emoji', ''),
            'next_inspection_date': lead_data.get('next_scheduled_inspection_date', ''),
            'next_inspection_type': lead_data.get('next_inspection_type', ''),
            'inspection_source': lead_data.get('inspection_source', ''),
            'gc_presence_probability': lead_data.get('_gc_presence_probability', 0),
        }

        leads.append(lead)

    conn.close()

    return jsonify({
        "leads": leads,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page
    }), 200


@app.route('/api/leads/<path:lead_id>', methods=['GET'])
@require_auth
def get_lead(lead_id):
    """Get single lead detail."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "view"):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT address_key, address, city, agent_sources, first_seen, last_updated, lead_data, primary_service_type
        FROM consolidated_leads
        WHERE address_key = ?
    """, (lead_id,))

    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "Lead not found"}), 404

    row_dict = dict(row)
    lead_data = {}
    try:
        lead_data = json.loads(row_dict.get('lead_data', '{}') or '{}')
    except Exception:
        pass

    # Get service type information
    c.execute("SELECT display_label, emoji FROM service_types WHERE name = ?", (row_dict.get('primary_service_type'),))
    service_row = c.fetchone()
    service_label = service_row[0] if service_row else ''
    service_emoji = service_row[1] if service_row else ''
    if not (service_label and service_emoji) and row_dict['agent_sources']:
        # Fallback to first agent if primary_service_type not found
        first_agent = row_dict['agent_sources'].split(',')[0]
        c.execute("SELECT display_label, emoji FROM service_types WHERE name = ?", (first_agent,))
        service_row = c.fetchone()
        service_label = service_row[0] if service_row else ''
        service_emoji = service_row[1] if service_row else ''

    scoring = lead_data.get('_scoring', {})
    lead = {
        'id': row_dict['address_key'],
        'address': row_dict['address'],
        'city': row_dict['city'],
        'score': scoring.get('score', 0),
        'value': lead_data.get('value_float', 0),
        'source': row_dict['agent_sources'],
        'source_url': lead_data.get('source_url', ''),
        'description': lead_data.get('description', ''),
        'created_at': row_dict['first_seen'],
        'contractor': lead_data.get('contractor', ''),
        'contact_phone': lead_data.get('contact_phone', ''),
        'contact_email': lead_data.get('contact_email', ''),
        'owner': lead_data.get('owner', ''),
        'scoring_reasons': scoring.get('reasons', []),
        'next_inspection_date': lead_data.get('next_scheduled_inspection_date'),
        'inspection_source': lead_data.get('inspection_source', 'none'),
        'gc_presence_probability': lead_data.get('_gc_presence_probability', 0),
        'service_type': row_dict.get('primary_service_type'),
        'service_label': service_label,
        'service_emoji': service_emoji,
    }

    # Try to find upcoming inspection from public calendar
    try:
        c.execute("""
            SELECT inspection_date, inspection_type, jurisdiction, gc_presence_probability
            FROM scheduled_inspections
            WHERE address = ? AND inspection_date >= date('now')
            ORDER BY inspection_date ASC
            LIMIT 1
        """, (row_dict['address'],))
        insp_row = c.fetchone()
        if insp_row:
            lead['next_inspection_date'] = insp_row[0]
            lead['inspection_source'] = 'public_calendar'
            lead['gc_presence_probability'] = insp_row[3] if insp_row[3] else 0
    except Exception as e:
        logger.debug(f"Could not fetch scheduled inspection: {e}")

    conn.close()
    return jsonify(lead), 200


@app.route('/api/leads/<path:lead_id>/contact', methods=['POST'])
@require_auth
def log_lead_contact(lead_id):
    """Log user contact with a lead."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "contact"):
        return jsonify({"error": "Permission denied"}), 403

    data = request.get_json() or {}
    contact_type = data.get('type', 'view')
    notes = data.get('notes', '')

    valid_contact_types = {'view', 'phone_call', 'email', 'text', 'visit', 'other'}
    if contact_type not in valid_contact_types:
        contact_type = 'other'

    conn = get_db_connection()
    c = conn.cursor()

    # Verify lead exists
    c.execute("SELECT address_key FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "Lead not found"}), 404

    # Log contact
    c.execute("""
        INSERT INTO lead_contacts (user_id, lead_id, contact_type, notes)
        VALUES (?, ?, ?, ?)
    """, (user_id, lead_id, contact_type, notes))

    conn.commit()
    conn.close()

    return jsonify({"status": "contact logged"}), 201


# ─────────────────────────────────────────────────────────
# Dashboard Stats
# ─────────────────────────────────────────────────────────

@app.route('/api/stats', methods=['GET'])
@require_auth
def get_stats():
    """Get dashboard stats for current user."""
    user_id = g.user_id

    conn = get_db_connection()
    c = conn.cursor()

    # Get accessible cities and agents (by name)
    accessible_cities = get_user_cities(user_id)
    accessible_agents = get_user_agents(user_id)

    city_names = [c_item['name'] for c_item in accessible_cities]
    agent_names = [a['name'] for a in accessible_agents]

    if not city_names or not agent_names:
        return jsonify({
            "total_leads": 0,
            "new_leads": 0,
            "contacted_leads": 0,
            "by_agent": {},
            "by_city": {}
        }), 200

    # Build where clause (city is text name in consolidated_leads)
    placeholders_cities = ','.join('?' * len(city_names))
    or_agents = ' OR '.join(['agent_sources LIKE ?' for _ in agent_names])

    # Total leads
    c.execute(f"""
        SELECT COUNT(*) FROM consolidated_leads
        WHERE city IN ({placeholders_cities})
        AND ({or_agents})
    """, city_names + [f"%{a}%" for a in agent_names])
    total = c.fetchone()[0]

    # New leads (not contacted by user)
    c.execute(f"""
        SELECT COUNT(*) FROM consolidated_leads l
        WHERE city IN ({placeholders_cities})
        AND ({or_agents})
        AND NOT EXISTS (SELECT 1 FROM lead_contacts WHERE lead_id = l.address_key AND user_id = ?)
    """, city_names + [f"%{a}%" for a in agent_names] + [user_id])
    new = c.fetchone()[0]

    # Contacted leads
    c.execute(f"""
        SELECT COUNT(*) FROM lead_contacts
        WHERE user_id = ?
        AND lead_id IN (
            SELECT address_key FROM consolidated_leads
            WHERE city IN ({placeholders_cities})
            AND ({or_agents})
        )
    """, [user_id] + city_names + [f"%{a}%" for a in agent_names])
    contacted = c.fetchone()[0]

    # Leads by agent
    c.execute(f"""
        SELECT agent_sources, COUNT(*) as count
        FROM consolidated_leads
        WHERE city IN ({placeholders_cities})
        AND ({or_agents})
        GROUP BY agent_sources
    """, city_names + [f"%{a}%" for a in agent_names])
    by_agent = {row[0]: row[1] for row in c.fetchall()}

    # Leads by city
    c.execute(f"""
        SELECT city, COUNT(*) as count
        FROM consolidated_leads
        WHERE city IN ({placeholders_cities})
        AND ({or_agents})
        GROUP BY city
    """, city_names + [f"%{a}%" for a in agent_names])
    by_city = {row[0]: row[1] for row in c.fetchall()}

    conn.close()

    return jsonify({
        "total_leads": total,
        "new_leads": new,
        "contacted_leads": contacted,
        "by_agent": by_agent,
        "by_city": by_city
    }), 200


# ─────────────────────────────────────────────────────────
# Audit Log
# ─────────────────────────────────────────────────────────

@app.route('/api/audit-log', methods=['GET'])
@require_auth
def get_audit_log():
    """Get audit log for current user."""
    user_id = g.user_id
    page = request.args.get('page', 1, type=int)
    per_page = 50

    conn = get_db_connection()
    c = conn.cursor()

    # Get total count
    c.execute("SELECT COUNT(*) FROM audit_logs WHERE user_id = ?", (user_id,))
    total = c.fetchone()[0]

    # Get paginated logs
    offset = (page - 1) * per_page
    c.execute("""
        SELECT id, action, resource_type, resource_id, details, created_at
        FROM audit_logs
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, per_page, offset))

    logs = [dict(row) for row in c.fetchall()]
    conn.close()

    return jsonify({
        "logs": logs,
        "total": total,
        "page": page,
        "pages": (total + per_page - 1) // per_page
    }), 200


# ─────────────────────────────────────────────────────────
# Admin Endpoints (require admin role)
# ─────────────────────────────────────────────────────────

def require_admin(f):
    """Decorator to require admin role."""
    from functools import wraps

    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        user_id = g.user_id
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(*) FROM user_roles ur
            JOIN roles r ON ur.role_id = r.id
            WHERE ur.user_id = ? AND r.name = 'admin'
        """, (user_id,))
        is_admin = c.fetchone()[0] > 0
        conn.close()

        if not is_admin:
            return jsonify({"error": "Admin access required"}), 403

        return f(*args, **kwargs)

    return decorated


@app.route('/api/admin/users', methods=['POST'])
@require_admin
@limiter.limit("20 per minute")
def create_user():
    """Create a new user (admin only)."""
    data = request.get_json() or {}

    username = data.get('username')
    email = data.get('email')
    password = data.get('password')
    full_name = data.get('full_name', '')
    roles = data.get('roles', ['user'])
    city_ids = data.get('city_ids', [])
    agent_ids = data.get('agent_ids', [])
    # Time-limited access: accepts hours (e.g. 24) or ISO datetime string
    expires_in_hours = data.get('expires_in_hours')
    expires_at = data.get('expires_at')  # ISO format: "2026-04-06 15:00:00"

    if not username or not email or not password:
        return jsonify({"error": "Missing required fields"}), 400

    # Calculate expiration timestamp
    expiration = None
    if expires_in_hours:
        hours = int(expires_in_hours)
        if hours <= 0 or hours > 8760:  # max 1 year
            return jsonify({"error": "expires_in_hours must be between 1 and 8760"}), 400
        expiration = (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    elif expires_at:
        try:
            parsed_exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return jsonify({"error": "expires_at must be in format: YYYY-MM-DD HH:MM:SS"}), 400
        if parsed_exp <= datetime.utcnow():
            return jsonify({"error": "expires_at must be in the future"}), 400
        if parsed_exp > datetime.utcnow() + timedelta(days=3650):  # max 10 years
            return jsonify({"error": "expires_at cannot be more than 10 years in the future"}), 400
        expiration = expires_at

    password_hash = hash_password(password)

    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT INTO users (username, email, password_hash, full_name, expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (username, email, password_hash, full_name, expiration))

        user_id = c.lastrowid

        # Assign roles
        for role_name in roles:
            c.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
            role = c.fetchone()
            if role:
                c.execute("""
                    INSERT INTO user_roles (user_id, role_id)
                    VALUES (?, ?)
                """, (user_id, role[0]))

        # Assign city access (validate city_ids exist)
        for city_id in city_ids:
            c.execute("SELECT id FROM cities WHERE id = ?", (city_id,))
            if c.fetchone():
                c.execute("""
                    INSERT INTO user_city_access (user_id, city_id)
                    VALUES (?, ?)
                """, (user_id, city_id))
            else:
                logger.warning(f"City ID {city_id} does not exist, skipping")

        # Assign agent access (validate agent_ids exist)
        for agent_id in agent_ids:
            c.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
            if c.fetchone():
                c.execute("""
                    INSERT INTO user_agent_access (user_id, agent_id)
                    VALUES (?, ?)
                """, (user_id, agent_id))
            else:
                logger.warning(f"Agent ID {agent_id} does not exist, skipping")

        conn.commit()
        conn.close()

        result = {
            "id": user_id,
            "username": username,
            "email": email
        }
        if expiration:
            result["expires_at"] = expiration
            result["access_type"] = "temporary"
        else:
            result["access_type"] = "permanent"

        return jsonify(result), 201

    except Exception as e:
        logger.error(f"Error creating user: {e}", exc_info=True)
        conn.close()
        return jsonify({"error": "Failed to create user. Username or email may already exist."}), 400


@app.route('/api/admin/users/<int:user_id>/expiration', methods=['PUT'])
@require_admin
def update_user_expiration(user_id):
    """Update user's access expiration (admin only).

    Set expires_in_hours to extend from now, expires_at for exact date,
    or set both to null/omit to make access permanent.
    """
    data = request.get_json() or {}
    expires_in_hours = data.get('expires_in_hours')
    expires_at = data.get('expires_at')
    remove_expiration = data.get('permanent', False)

    conn = get_db_connection()
    c = conn.cursor()

    # Verify user exists
    c.execute("SELECT id, username FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404

    if remove_expiration:
        # Make access permanent
        c.execute("UPDATE users SET expires_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({
            "user_id": user_id,
            "username": user["username"],
            "expires_at": None,
            "access_type": "permanent"
        }), 200

    # Calculate new expiration
    expiration = None
    if expires_in_hours:
        hours = int(expires_in_hours)
        if hours <= 0 or hours > 8760:
            conn.close()
            return jsonify({"error": "expires_in_hours must be between 1 and 8760"}), 400
        expiration = (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    elif expires_at:
        try:
            parsed_exp = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            conn.close()
            return jsonify({"error": "expires_at must be in format: YYYY-MM-DD HH:MM:SS"}), 400
        if parsed_exp <= datetime.utcnow():
            conn.close()
            return jsonify({"error": "expires_at must be in the future"}), 400
        if parsed_exp > datetime.utcnow() + timedelta(days=3650):
            conn.close()
            return jsonify({"error": "expires_at cannot be more than 10 years in the future"}), 400
        expiration = expires_at
    else:
        conn.close()
        return jsonify({"error": "Provide expires_in_hours, expires_at, or permanent=true"}), 400

    c.execute("UPDATE users SET expires_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (expiration, user_id))
    conn.commit()
    conn.close()

    return jsonify({
        "user_id": user_id,
        "username": user["username"],
        "expires_at": expiration,
        "access_type": "temporary"
    }), 200


# ─────────────────────────────────────────────────────────
# Scheduled Inspections Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/scheduled_inspections', methods=['GET'])
@require_auth
def list_scheduled_inspections():
    """
    Get scheduled inspections filtered by jurisdiction and date range.

    Query params:
      - jurisdiction: Filter by jurisdiction (e.g., "berkeley", "contra_costa")
      - start_date: Start date YYYY-MM-DD (optional)
      - end_date: End date YYYY-MM-DD (optional)
      - limit: Max results (default 100)
    """
    jurisdiction = request.args.get('jurisdiction')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    limit = request.args.get('limit', 100, type=int)

    if not jurisdiction:
        return jsonify({"error": "jurisdiction parameter required"}), 400

    try:
        inspections = get_inspections_by_jurisdiction(jurisdiction, start_date, end_date)
        # Limit results
        inspections = inspections[:limit]

        return jsonify({
            "jurisdiction": jurisdiction,
            "count": len(inspections),
            "inspections": inspections
        }), 200

    except Exception as e:
        logger.error(f"Error listing inspections: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/<path:lead_id>/scheduled_inspections', methods=['GET'])
@require_auth
def get_lead_scheduled_inspections(lead_id):
    """
    Get upcoming scheduled inspections for a specific lead.

    Query params:
      - days: Look ahead N days (default 30)
    """
    days = request.args.get('days', 30, type=int)

    try:
        # lead_id typically is an address or address_key
        inspections = get_upcoming_inspections(lead_id, days=days)

        return jsonify({
            "lead_id": lead_id,
            "days": days,
            "count": len(inspections),
            "inspections": inspections
        }), 200

    except Exception as e:
        logger.error(f"Error getting lead inspections: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/scheduled_inspections', methods=['POST'])
@require_auth
def create_scheduled_inspection():
    """
    Create or update a scheduled inspection (admin only).

    Request body:
      {
        "permit_id": "string",
        "address": "string",
        "inspection_date": "YYYY-MM-DD",
        "inspection_type": "FOUNDATION|FRAMING|ELECTRICAL|ROOFING|DRYWALL|PAINT|LANDSCAPING|FINAL",
        "jurisdiction": "string",
        "inspector_name": "string (optional)",
        "time_window_start": "HH:MM (optional)",
        "time_window_end": "HH:MM (optional)"
      }
    """
    # Check admin permission
    if not check_permission(g.user_id, "inspections", "create"):
        return jsonify({"error": "Insufficient permissions"}), 403

    data = request.get_json() or {}

    # Validate required fields
    required = ["permit_id", "address", "inspection_date", "jurisdiction"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"Missing required field: {field}"}), 400

    try:
        # Prepare inspection data
        inspection_data = {
            "permit_id": data.get("permit_id"),
            "address": data.get("address"),
            "inspection_date": data.get("inspection_date"),
            "inspection_type": data.get("inspection_type", "INSPECTION"),
            "jurisdiction": data.get("jurisdiction"),
            "inspector_name": data.get("inspector_name"),
            "time_window_start": data.get("time_window_start"),
            "time_window_end": data.get("time_window_end"),
            "status": "SCHEDULED",
            "gc_presence_probability": data.get("gc_presence_probability", 0.8),
            "source_url": f"/api/scheduled_inspections (manual)",
        }

        row_id = insert_scheduled_inspection(inspection_data)

        return jsonify({
            "id": row_id,
            "status": "created",
            "inspection": inspection_data
        }), 201

    except Exception as e:
        logger.error(f"Error creating inspection: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Inspection Scheduler Admin Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/admin/scheduler/status', methods=['GET'])
@require_auth
def get_scheduler_status_endpoint():
    """Get status of the inspection scheduler (admin only)."""
    if not check_permission(g.user_id, "admin", "view"):
        return jsonify({"error": "Insufficient permissions"}), 403

    try:
        status = get_scheduler_status()
        return jsonify(status), 200
    except Exception as e:
        logger.error(f"Error getting scheduler status: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/scheduler/fetch-now', methods=['POST'])
@require_auth
def trigger_inspection_fetch():
    """Manually trigger inspection fetch now (admin only)."""
    if not check_permission(g.user_id, "admin", "manage"):
        return jsonify({"error": "Insufficient permissions"}), 403

    try:
        count = fetch_inspections_now()
        return jsonify({
            "status": "completed",
            "inspections_saved": count,
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Error triggering fetch: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/scheduler/cleanup', methods=['POST'])
@require_auth
def trigger_cleanup():
    """Cleanup old inspection records (admin only)."""
    if not check_permission(g.user_id, "admin", "manage"):
        return jsonify({"error": "Insufficient permissions"}), 403

    days = request.get_json().get('older_than_days', 60) if request.get_json() else 60

    try:
        count = cleanup_old_inspections(older_than_days=days)
        return jsonify({
            "status": "completed",
            "deleted_records": count,
            "older_than_days": days,
            "timestamp": datetime.utcnow().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Admin - Users Management Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/admin/users', methods=['GET'])
@require_admin
def list_all_users():
    """List all users with their roles and access (admin only)."""
    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Get all users
        c.execute("""
            SELECT u.id, u.username, u.email, u.full_name, u.is_active, u.expires_at, u.created_at
            FROM users u
            ORDER BY u.username
        """)

        users = []
        for row in c.fetchall():
            row_dict = dict(row)
            user_id = row_dict['id']

            # Get roles
            c.execute("""
                SELECT r.name FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = ?
            """, (user_id,))
            roles = [r[0] for r in c.fetchall()]

            # Get city access
            c.execute("""
                SELECT c.id, c.name FROM cities c
                JOIN user_city_access uca ON c.id = uca.city_id
                WHERE uca.user_id = ?
            """, (user_id,))
            cities = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

            # Get agent access
            c.execute("""
                SELECT a.id, a.name FROM agents a
                JOIN user_agent_access uaa ON a.id = uaa.agent_id
                WHERE uaa.user_id = ?
            """, (user_id,))
            agents = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

            users.append({
                "id": row_dict['id'],
                "username": row_dict['username'],
                "email": row_dict['email'],
                "full_name": row_dict['full_name'],
                "is_active": bool(row_dict['is_active']),
                "expires_at": row_dict['expires_at'],
                "created_at": row_dict['created_at'],
                "roles": roles,
                "cities": cities,
                "agents": agents
            })

        conn.close()
        return jsonify(users), 200

    except Exception as e:
        logger.error(f"Error listing users: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['GET'])
@require_admin
def get_user_detail(user_id):
    """Get detailed user information (admin only)."""
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            SELECT id, username, email, full_name, is_active, expires_at, created_at
            FROM users
            WHERE id = ?
        """, (user_id,))

        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "User not found"}), 404

        row_dict = dict(row)

        # Get roles
        c.execute("""
            SELECT r.id, r.name FROM roles r
            JOIN user_roles ur ON r.id = ur.role_id
            WHERE ur.user_id = ?
        """, (user_id,))
        roles = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

        # Get cities
        c.execute("""
            SELECT c.id, c.name FROM cities c
            JOIN user_city_access uca ON c.id = uca.city_id
            WHERE uca.user_id = ?
        """, (user_id,))
        cities = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

        # Get agents
        c.execute("""
            SELECT a.id, a.name FROM agents a
            JOIN user_agent_access uaa ON a.id = uaa.agent_id
            WHERE uaa.user_id = ?
        """, (user_id,))
        agents = [{"id": r[0], "name": r[1]} for r in c.fetchall()]

        conn.close()

        return jsonify({
            "id": row_dict['id'],
            "username": row_dict['username'],
            "email": row_dict['email'],
            "full_name": row_dict['full_name'],
            "is_active": bool(row_dict['is_active']),
            "expires_at": row_dict['expires_at'],
            "created_at": row_dict['created_at'],
            "roles": roles,
            "cities": cities,
            "agents": agents
        }), 200

    except Exception as e:
        logger.error(f"Error getting user: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['PUT'])
@require_admin
@limiter.limit("20 per minute")
def update_user(user_id):
    """Update user information (admin only)."""
    user_id_current = g.user_id
    data = request.get_json() or {}

    # Prevent self-modification (optional - may want to allow)
    # if user_id == user_id_current:
    #     return jsonify({"error": "Cannot modify own account this way"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify user exists
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not c.fetchone():
            conn.close()
            return jsonify({"error": "User not found"}), 404

        # Update fields
        updates = []
        values = []

        if 'full_name' in data:
            updates.append("full_name = ?")
            values.append(data['full_name'])

        if 'email' in data:
            updates.append("email = ?")
            values.append(data['email'])

        if 'is_active' in data:
            updates.append("is_active = ?")
            values.append(int(data['is_active']))

        if 'expires_at' in data:
            raw_exp = data['expires_at']
            if raw_exp is not None:
                try:
                    parsed_exp = datetime.strptime(raw_exp, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    conn.close()
                    return jsonify({"error": "expires_at must be in format: YYYY-MM-DD HH:MM:SS"}), 400
                if parsed_exp <= datetime.utcnow():
                    conn.close()
                    return jsonify({"error": "expires_at must be in the future"}), 400
            updates.append("expires_at = ?")
            values.append(raw_exp)

        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(user_id)
            query = f"UPDATE users SET {', '.join(updates)} WHERE id = ?"
            c.execute(query, values)

        conn.commit()

        # Log activity
        log_audit(user_id_current, "user_updated", str(user_id), "user",
                 f"Updated user {user_id}: {', '.join(updates)}")

        # Return updated user
        c.execute("""
            SELECT id, username, email, full_name, is_active, expires_at, created_at
            FROM users WHERE id = ?
        """, (user_id,))

        row_dict = dict(c.fetchone())
        c.execute("SELECT r.name FROM roles r JOIN user_roles ur ON r.id = ur.role_id WHERE ur.user_id = ?", (user_id,))
        roles = [r[0] for r in c.fetchall()]

        conn.close()

        return jsonify({
            "id": row_dict['id'],
            "username": row_dict['username'],
            "email": row_dict['email'],
            "full_name": row_dict['full_name'],
            "is_active": bool(row_dict['is_active']),
            "expires_at": row_dict['expires_at'],
            "created_at": row_dict['created_at'],
            "roles": roles
        }), 200

    except Exception as e:
        logger.error(f"Error updating user: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/users/<int:user_id>/roles', methods=['PUT'])
@require_admin
@limiter.limit("20 per minute")
def update_user_roles(user_id):
    """Update user roles (admin only)."""
    user_id_current = g.user_id
    data = request.get_json() or {}
    role_names = data.get('roles', [])

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify user exists
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not c.fetchone():
            conn.close()
            return jsonify({"error": "User not found"}), 404

        # Get role IDs
        role_ids = []
        for role_name in role_names:
            c.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
            role = c.fetchone()
            if role:
                role_ids.append(role[0])

        # Clear existing roles
        c.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))

        # Add new roles
        for role_id in role_ids:
            c.execute("INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
                     (user_id, role_id))

        conn.commit()

        # Log activity
        log_audit(user_id_current, "user_roles_updated", str(user_id), "user",
                 f"Updated roles to: {', '.join(role_names)}")

        conn.close()
        return jsonify({"user_id": user_id, "roles": role_names}), 200

    except Exception as e:
        logger.error(f"Error updating roles: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/users/<int:user_id>/access', methods=['PUT'])
@require_admin
@limiter.limit("20 per minute")
def update_user_access(user_id):
    """Update user city and agent access (admin only)."""
    user_id_current = g.user_id
    data = request.get_json() or {}
    city_ids = data.get('city_ids', [])
    agent_ids = data.get('agent_ids', [])

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify user exists
        c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if not c.fetchone():
            conn.close()
            return jsonify({"error": "User not found"}), 404

        # Clear existing city access
        c.execute("DELETE FROM user_city_access WHERE user_id = ?", (user_id,))

        # Add new city access
        for city_id in city_ids:
            c.execute("INSERT OR IGNORE INTO user_city_access (user_id, city_id) VALUES (?, ?)",
                     (user_id, city_id))

        # Clear existing agent access
        c.execute("DELETE FROM user_agent_access WHERE user_id = ?", (user_id,))

        # Add new agent access
        for agent_id in agent_ids:
            c.execute("INSERT OR IGNORE INTO user_agent_access (user_id, agent_id) VALUES (?, ?)",
                     (user_id, agent_id))

        conn.commit()

        # Log activity
        log_audit(user_id_current, "user_access_updated", str(user_id), "user",
                 f"Updated access: {len(city_ids)} cities, {len(agent_ids)} agents")

        conn.close()
        return jsonify({"user_id": user_id, "city_ids": city_ids, "agent_ids": agent_ids}), 200

    except Exception as e:
        logger.error(f"Error updating access: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@require_admin
@limiter.limit("10 per minute")
def delete_user(user_id):
    """Delete user (soft or hard delete) (admin only)."""
    user_id_current = g.user_id
    permanent = request.args.get('permanent', 'false').lower() == 'true'

    # Prevent self-deletion
    if user_id == user_id_current:
        return jsonify({"error": "Cannot delete your own account"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify user exists
        c.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        user = c.fetchone()
        if not user:
            conn.close()
            return jsonify({"error": "User not found"}), 404

        username = user[0]

        if permanent:
            # Hard delete: Remove all associated records
            c.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM user_city_access WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM user_agent_access WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM lead_contacts WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM lead_notes WHERE user_id = ?", (user_id,))
            c.execute("DELETE FROM users WHERE id = ?", (user_id,))

            log_audit(user_id_current, "user_deleted_permanent", str(user_id), "user",
                     f"Hard deleted user {username}")
        else:
            # Soft delete: Set is_active to false
            c.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user_id,))

            log_audit(user_id_current, "user_deleted_soft", str(user_id), "user",
                     f"Soft deleted user {username}")

        conn.commit()
        conn.close()

        return jsonify({
            "status": "deleted",
            "user_id": user_id,
            "permanent": permanent
        }), 200

    except Exception as e:
        logger.error(f"Error deleting user: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Admin - Reference Data Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/admin/cities', methods=['GET'])
@require_auth
def list_all_cities():
    """List all cities."""
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("SELECT id, name, state, county FROM cities ORDER BY name")
        cities = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(cities), 200
    except Exception as e:
        logger.error(f"Error listing cities: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/agents', methods=['GET'])
@require_auth
def list_all_agents():
    """List all agents."""
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("SELECT id, name FROM agents ORDER BY name")
        agents = [dict(row) for row in c.fetchall()]
        conn.close()
        return jsonify(agents), 200
    except Exception as e:
        logger.error(f"Error listing agents: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Leads - Notes & Contact History
# ─────────────────────────────────────────────────────────

@app.route('/api/leads/<path:lead_id>/contact-history', methods=['GET'])
@require_auth
def get_lead_contact_history(lead_id):
    """Get contact history for a lead."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "view"):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Get all contacts with user info
        c.execute("""
            SELECT lc.id, lc.contact_type, lc.notes, lc.created_at, u.username
            FROM lead_contacts lc
            JOIN users u ON lc.user_id = u.id
            WHERE lc.lead_id = ?
            ORDER BY lc.created_at DESC
        """, (lead_id,))

        history = [
            {
                "id": row[0],
                "contact_type": row[1],
                "notes": row[2],
                "created_at": row[3],
                "user": row[4]
            }
            for row in c.fetchall()
        ]

        conn.close()
        return jsonify(history), 200

    except Exception as e:
        logger.error(f"Error getting contact history: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/<path:lead_id>/notes', methods=['GET'])
@require_auth
def get_lead_notes(lead_id):
    """Get all notes for a lead."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "view"):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Check if notes table exists, if not return empty
        c.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='lead_notes'
        """)

        if not c.fetchone():
            conn.close()
            return jsonify([]), 200

        # Get notes (exclude soft-deleted)
        c.execute("""
            SELECT ln.id, ln.note, ln.created_at, ln.updated_at, u.username
            FROM lead_notes ln
            JOIN users u ON ln.user_id = u.id
            WHERE ln.lead_id = ? AND (ln.is_deleted = 0 OR ln.is_deleted IS NULL)
            ORDER BY ln.created_at DESC
        """, (lead_id,))

        notes = [
            {
                "id": row[0],
                "note": row[1],
                "created_at": row[2],
                "updated_at": row[3],
                "user": row[4]
            }
            for row in c.fetchall()
        ]

        conn.close()
        return jsonify(notes), 200

    except Exception as e:
        logger.error(f"Error getting notes: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/<path:lead_id>/notes', methods=['POST'])
@require_auth
def create_lead_note(lead_id):
    """Add a note to a lead."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "contact"):
        return jsonify({"error": "Permission denied"}), 403

    data = request.get_json() or {}
    note = data.get('note', '').strip()

    if not note:
        return jsonify({"error": "Note cannot be empty"}), 400

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Create table if not exists
        c.execute("""
            CREATE TABLE IF NOT EXISTS lead_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
        """)

        # Insert note
        c.execute("""
            INSERT INTO lead_notes (lead_id, user_id, note)
            VALUES (?, ?, ?)
        """, (lead_id, user_id, note))

        note_id = c.lastrowid
        conn.commit()
        conn.close()

        # Log action
        log_audit(user_id, "create_note", "lead", lead_id, f"Note: {note[:50]}")

        return jsonify({
            "id": note_id,
            "note": note,
            "created_at": datetime.utcnow().isoformat()
        }), 201

    except Exception as e:
        logger.error(f"Error creating note: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/<path:lead_id>/notes/<int:note_id>', methods=['PUT'])
@require_auth
def update_lead_note(lead_id, note_id):
    """Update a note on a lead."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "contact"):
        return jsonify({"error": "Permission denied"}), 403

    data = request.get_json() or {}
    note_text = data.get('note', '').strip()

    if not note_text:
        return jsonify({"error": "Note cannot be empty"}), 400

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify note exists and belongs to correct lead
        c.execute("""
            SELECT user_id FROM lead_notes
            WHERE id = ? AND lead_id = ?
        """, (note_id, lead_id))

        note_row = c.fetchone()
        if not note_row:
            conn.close()
            return jsonify({"error": "Note not found"}), 404

        # Update note
        c.execute("""
            UPDATE lead_notes
            SET note = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND lead_id = ?
        """, (note_text, note_id, lead_id))

        conn.commit()
        conn.close()

        # Log action
        log_audit(user_id, "update_note", "lead", lead_id, f"Updated note {note_id}")

        return jsonify({
            "id": note_id,
            "note": note_text,
            "updated_at": datetime.utcnow().isoformat()
        }), 200

    except Exception as e:
        logger.error(f"Error updating note: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/<path:lead_id>/notes/<int:note_id>', methods=['DELETE'])
@require_auth
def delete_lead_note(lead_id, note_id):
    """Delete a note from a lead (soft delete)."""
    user_id = g.user_id

    if not check_permission(user_id, "leads", "contact"):
        return jsonify({"error": "Permission denied"}), 403

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify note exists
        c.execute("""
            SELECT id FROM lead_notes
            WHERE id = ? AND lead_id = ?
        """, (note_id, lead_id))

        if not c.fetchone():
            conn.close()
            return jsonify({"error": "Note not found"}), 404

        # Soft delete
        c.execute("""
            UPDATE lead_notes
            SET is_deleted = 1, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND lead_id = ?
        """, (note_id, lead_id))

        conn.commit()
        conn.close()

        # Log action
        log_audit(user_id, "delete_note", "lead", lead_id, f"Deleted note {note_id}")

        return jsonify({"status": "deleted", "note_id": note_id}), 200

    except Exception as e:
        logger.error(f"Error deleting note: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Saved Lead Views Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/leads/views', methods=['GET'])
@require_auth
def get_lead_views():
    """Get user's saved lead filter views."""
    from utils.web_db import get_user_lead_views

    user_id = g.user_id
    views = get_user_lead_views(user_id)

    return jsonify(views), 200


@app.route('/api/leads/views', methods=['POST'])
@require_auth
def create_lead_view():
    """Create a new saved lead filter view."""
    from utils.web_db import save_lead_view

    user_id = g.user_id
    data = request.get_json() or {}

    name = data.get('name', '').strip()
    filters = data.get('filters', {})
    is_default = data.get('is_default', False)

    if not name:
        return jsonify({"error": "View name is required"}), 400

    view_id = save_lead_view(user_id, name, filters, is_default)

    if not view_id:
        return jsonify({"error": "View name already exists"}), 409

    # Log action
    log_audit(user_id, "create_view", "lead_view", str(view_id),
             f"Created view: {name}")

    return jsonify({
        "id": view_id,
        "name": name,
        "filters": filters,
        "is_default": is_default
    }), 201


@app.route('/api/leads/views/<int:view_id>', methods=['PUT'])
@require_auth
def update_lead_view(view_id):
    """Update a saved lead view."""
    user_id = g.user_id
    data = request.get_json() or {}

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Verify view exists and belongs to user
        c.execute("""
            SELECT name FROM lead_views
            WHERE id = ? AND user_id = ?
        """, (view_id, user_id))

        if not c.fetchone():
            conn.close()
            return jsonify({"error": "View not found"}), 404

        # Update fields
        updates = []
        values = []

        if 'name' in data:
            updates.append("name = ?")
            values.append(data['name'])

        if 'filters' in data:
            import json
            updates.append("filters = ?")
            values.append(json.dumps(data['filters']))

        if 'is_default' in data:
            updates.append("is_default = ?")
            values.append(int(data['is_default']))

        if updates:
            values.extend([view_id, user_id])
            query = f"UPDATE lead_views SET {', '.join(updates)} WHERE id = ? AND user_id = ?"
            c.execute(query, values)

        conn.commit()
        conn.close()

        # Log action
        log_audit(user_id, "update_view", "lead_view", str(view_id),
                 f"Updated view {view_id}")

        return jsonify({"status": "updated", "view_id": view_id}), 200

    except Exception as e:
        logger.error(f"Error updating view: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/leads/views/<int:view_id>', methods=['DELETE'])
@require_auth
def delete_lead_view(view_id):
    """Delete a saved lead view."""
    from utils.web_db import delete_lead_view

    user_id = g.user_id

    if delete_lead_view(view_id, user_id):
        # Log action
        log_audit(user_id, "delete_view", "lead_view", str(view_id),
                 f"Deleted view {view_id}")

        return jsonify({"status": "deleted", "view_id": view_id}), 200
    else:
        return jsonify({"error": "View not found"}), 404


# ─────────────────────────────────────────────────────────
# Settings & Preferences Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/settings/preferences', methods=['GET'])
@require_auth
def get_preferences():
    """Get user preferences."""
    from utils.web_db import get_user_preferences

    user_id = g.user_id
    prefs = get_user_preferences(user_id)

    return jsonify(prefs), 200


@app.route('/api/settings/preferences', methods=['PUT'])
@require_auth
def update_preferences():
    """Update user preferences."""
    from utils.web_db import update_user_preferences

    user_id = g.user_id
    data = request.get_json() or {}

    success = update_user_preferences(user_id, data)

    if success:
        # Log activity
        log_audit(user_id, "preferences_updated", str(user_id), "user",
                 f"Updated preferences: {', '.join(data.keys())}")

        return jsonify({"status": "updated"}), 200
    else:
        return jsonify({"error": "Failed to update preferences"}), 500


@app.route('/api/settings', methods=['GET'])
@require_auth
def get_all_settings():
    """Get all user settings and profile data."""
    from utils.web_db import get_user_preferences

    user_id = g.user_id
    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Get user profile
        c.execute("""
            SELECT id, username, email, full_name, created_at
            FROM users WHERE id = ?
        """, (user_id,))

        user_row = c.fetchone()
        if not user_row:
            conn.close()
            return jsonify({"error": "User not found"}), 404

        user = dict(user_row)

        # Get preferences
        prefs = get_user_preferences(user_id)

        # Get export history
        c.execute("""
            SELECT id, export_name, record_count, created_at FROM export_logs
            WHERE user_id = ? ORDER BY created_at DESC LIMIT 10
        """, (user_id,))

        exports = [dict(row) for row in c.fetchall()]

        # Get activity
        c.execute("""
            SELECT action_type, description, created_at FROM activity_feed
            WHERE user_id = ? ORDER BY created_at DESC LIMIT 20
        """, (user_id,))

        activities = [dict(row) for row in c.fetchall()]

        conn.close()

        return jsonify({
            "user": user,
            "preferences": prefs,
            "exports": exports,
            "activities": activities
        }), 200

    except Exception as e:
        logger.error(f"Error getting settings: {e}")
        conn.close()
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Bot Users Admin API (Phase 3 — Telegram bot users)
# ─────────────────────────────────────────────────────────

@app.route('/api/admin/bot-users', methods=['GET'])
@require_admin
def list_bot_users_endpoint():
    """List every bot_user with status, trial dates, services & city."""
    try:
        users = bu.list_bot_users(limit=1000)
        return jsonify({
            "users": users,
            "stats": bu.get_stats(),
            "trial_days": bu.TRIAL_DAYS,
            "price_usd": bu.SUBSCRIPTION_PRICE_USD,
        }), 200
    except Exception as e:
        logger.error(f"Error listing bot users: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/admin/bot-users/<int:bot_user_id>/trial', methods=['POST'])
@require_admin
def extend_bot_user_trial(bot_user_id):
    """Extend (or restart) a bot_user's trial by N days."""
    data = request.get_json() or {}
    days = int(data.get("days", bu.TRIAL_DAYS))
    user = bu.get_by_id(bot_user_id)
    if not user:
        return jsonify({"error": "Bot user not found"}), 404
    updated = bu.start_trial(user["chat_id"], days=days)
    log_audit(g.user_id, "bot_trial_extended", str(bot_user_id), "bot_user",
              f"Extended trial by {days} days")
    return jsonify(updated), 200


@app.route('/api/admin/bot-users/<int:bot_user_id>/activate', methods=['POST'])
@require_admin
def activate_bot_user(bot_user_id):
    """Manually mark a bot_user as paid for N days (useful for comps)."""
    data = request.get_json() or {}
    days = int(data.get("days", 30))
    user = bu.get_by_id(bot_user_id)
    if not user:
        return jsonify({"error": "Bot user not found"}), 404
    until = datetime.utcnow() + timedelta(days=days)
    bu.mark_paid(user["chat_id"], until)
    log_audit(g.user_id, "bot_user_activated", str(bot_user_id), "bot_user",
              f"Manual paid-status for {days} days")
    return jsonify(bu.get_by_id(bot_user_id)), 200


@app.route('/api/admin/bot-users/<int:bot_user_id>/suspend', methods=['POST'])
@require_admin
def suspend_bot_user(bot_user_id):
    """Suspend a bot_user so they stop receiving leads."""
    user = bu.get_by_id(bot_user_id)
    if not user:
        return jsonify({"error": "Bot user not found"}), 404
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE bot_users SET is_active = 0, state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (bu.STATE_SUSPENDED, bot_user_id),
    )
    conn.commit()
    conn.close()
    log_audit(g.user_id, "bot_user_suspended", str(bot_user_id), "bot_user", "")
    return jsonify(bu.get_by_id(bot_user_id)), 200


@app.route('/api/admin/bot-users/stats', methods=['GET'])
@require_admin
def bot_users_stats():
    try:
        return jsonify(bu.get_stats()), 200
    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Stripe webhook (Phase 4 — billing)
# ─────────────────────────────────────────────────────────

@app.route('/api/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """
    Receive Stripe webhook events. Verifies the signature and applies
    paid/expired status to the corresponding bot_user.
    """
    payload = request.get_data()
    signature = request.headers.get('Stripe-Signature', '')
    event = billing.verify_webhook(payload, signature)
    if not event:
        return jsonify({"error": "invalid signature or billing not configured"}), 400
    try:
        handled = billing.handle_event(event)
        return jsonify({"received": True, "handled": handled}), 200
    except Exception as e:
        logger.exception(f"Stripe webhook handler error: {e}")
        return jsonify({"error": "Internal server error"}), 500


# ─────────────────────────────────────────────────────────
# Public Swipe Endpoints (Tinder-style UX)
# ─────────────────────────────────────────────────────────

# Anonymous visitors can view up to this many leads before being asked
# to log in with Google or Facebook.
ANON_LEAD_LIMIT = int(os.getenv("SWIPE_ANON_LIMIT", "10"))
FREE_USER_LEAD_LIMIT = int(os.getenv("SWIPE_FREE_LIMIT", "40"))
REQUIRE_CONTACT = os.getenv("SWIPE_REQUIRE_CONTACT", "false").lower() in ("true", "1", "yes")
PRO_LEAD_LIMIT = int(os.getenv("SWIPE_PRO_LIMIT", "200"))   # $29/mo tier
# PREMIUM = is_paid flag + no limit ($99/mo)


def _resolve_swipe_identity():
    """
    Resolve the caller's identity for the swipe feed.

    Returns a tuple (user_id, anon_id) where exactly one is set.
    Authenticated users are identified by their JWT; anonymous users
    are identified by an ``anon_id`` query/body parameter that the
    client keeps in localStorage.
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            from web.auth import verify_token
            payload = verify_token(token)
            return payload.get("user_id"), None
        except Exception:
            pass

    anon_id = (
        request.args.get("anon_id")
        or (request.get_json(silent=True) or {}).get("anon_id")
        or request.headers.get("X-Anon-Id")
        or ""
    ).strip()
    return None, anon_id or None


def _count_swipes(user_id, anon_id) -> int:
    """Count only right-swipes (likes) — dislikes don't consume quota."""
    conn = get_db_connection()
    c = conn.cursor()
    if user_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE user_id = ? AND action = 'like'",
            (user_id,),
        )
    elif anon_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE anon_id = ? AND action = 'like'",
            (anon_id,),
        )
    else:
        conn.close()
        return 0
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 0


def _already_swiped_ids(user_id, anon_id) -> set:
    conn = get_db_connection()
    c = conn.cursor()
    if user_id:
        c.execute(
            "SELECT lead_id FROM swipe_actions WHERE user_id = ?",
            (user_id,),
        )
    elif anon_id:
        c.execute(
            "SELECT lead_id FROM swipe_actions WHERE anon_id = ?",
            (anon_id,),
        )
    else:
        conn.close()
        return set()
    ids = {row[0] for row in c.fetchall()}
    conn.close()
    return ids


# ── City coordinates for radius filtering ─────────────────────────────────────
import math as _math

_CITY_COORDS: dict[str, tuple[float, float]] = {
    # California – Bay Area
    "san francisco": (37.7749, -122.4194),
    "oakland": (37.8044, -122.2712),
    "berkeley": (37.8716, -122.2727),
    "san jose": (37.3382, -121.8863),
    "fremont": (37.5485, -121.9886),
    "hayward": (37.6688, -122.0808),
    "sunnyvale": (37.3688, -122.0363),
    "santa clara": (37.3541, -121.9552),
    "mountain view": (37.3861, -122.0839),
    "palo alto": (37.4419, -122.1430),
    "redwood city": (37.4852, -122.2364),
    "san mateo": (37.5630, -122.3255),
    "daly city": (37.6879, -122.4702),
    "richmond": (37.9358, -122.3477),
    "concord": (37.9780, -122.0311),
    "vallejo": (38.1041, -122.2566),
    "antioch": (37.9960, -121.8058),
    "richmond ca": (37.9358, -122.3477),
    "san leandro": (37.7249, -122.1561),
    "livermore": (37.6819, -121.7681),
    "pleasanton": (37.6624, -121.8747),
    "walnut creek": (37.9101, -122.0652),
    "san rafael": (37.9735, -122.5311),
    "napa": (38.2975, -122.2869),
    "santa rosa": (38.4404, -122.7141),
    "petaluma": (38.2324, -122.6367),
    "novato": (38.1074, -122.5697),
    "los angeles": (34.0522, -118.2437),
    "long beach": (33.7701, -118.1937),
    "anaheim": (33.8366, -117.9143),
    "santa ana": (33.7455, -117.8677),
    "irvine": (33.6846, -117.8265),
    "san diego": (32.7157, -117.1611),
    "sacramento": (38.5816, -121.4944),
    "fresno": (36.7378, -119.7871),
    "bakersfield": (35.3733, -119.0187),
    "stockton": (37.9577, -121.2908),
    "modesto": (37.6391, -120.9969),
    # Other major US cities
    "new york": (40.7128, -74.0060),
    "brooklyn": (40.6782, -73.9442),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "philadelphia": (39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "dallas": (32.7767, -96.7970),
    "austin": (30.2672, -97.7431),
    "jacksonville": (30.3322, -81.6557),
    "columbus": (39.9612, -82.9988),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "nashville": (36.1627, -86.7816),
    "portland": (45.5051, -122.6750),
    "las vegas": (36.1699, -115.1398),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "boston": (42.3601, -71.0589),
}


def _haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in miles between two lat/lon points."""
    R = 3958.8  # Earth radius in miles
    dlat = _math.radians(lat2 - lat1)
    dlon = _math.radians(lon2 - lon1)
    a = (_math.sin(dlat / 2) ** 2
         + _math.cos(_math.radians(lat1))
         * _math.cos(_math.radians(lat2))
         * _math.sin(dlon / 2) ** 2)
    return R * 2 * _math.asin(_math.sqrt(a))


def _city_coords(city_name: str) -> tuple[float, float] | None:
    """Return (lat, lon) for a city name, or None if unknown."""
    return _CITY_COORDS.get((city_name or "").strip().lower())


# ── Service category keyword mapping ──────────────────────────────────────────
_SERVICE_CAT_KEYWORDS: dict[str, list[str]] = {
    "roofing":     ["roof", "roofing", "re-roof", "reroof", "shingle", "tile roof", "torch down", "tpo", "flat roof", "gutter", "fascia"],
    "drywall":     ["drywall", "sheetrock", "gypsum", "taping", "texturing", "wall board", "partition", "plaster"],
    "paint":       ["paint", "painting", "repaint", "painter", "stucco paint", "primer", "coating"],
    "electrical":  ["electrical", "panel upgrade", "ev charger", "200 amp", "rewire", "sub panel", "wiring", "low voltage", "circuit"],
    "plumbing":    ["plumb", "plumbing", "pipe", "sewer", "drain", "water heater", "gas line", "backflow", "repipe", "fixture"],
    "hvac":        ["hvac", "heating", "cooling", "air condition", "furnace", "ductwork", "mini split", "heat pump", "ventilat", "duct"],
    "flooring":    ["floor", "flooring", "hardwood", "tile", "laminate", "vinyl plank", "carpet", "epoxy floor", "subfloor"],
    "concrete":    ["concrete", "cement", "foundation", "slab", "sidewalk", "driveway", "flatwork", "footer", "stem wall", "curb"],
    "framing":     ["framing", "framer", "structural", "load bearing", "beam", "joist", "truss", "stud wall", "lumber", "adu"],
    "windows":     ["window", "door", "sliding door", "patio door", "skylight", "glass", "glazing", "storefront", "french door", "fenestration"],
    "landscaping": ["landscap", "hardscape", "irrigation", "sprinkler", "retaining wall", "paver", "turf", "grading", "tree"],
    "remodel":     ["remodel", "renovation", "kitchen", "bathroom", "addition", "adu", "accessory dwelling", "tenant improvement", "interior alteration"],
}
# These map directly to primary_service_type column
_SERVICE_TYPE_CATS = {"solar", "permits", "construction", "realestate", "flood", "energy", "rodents", "deconstruction", "remodel"}


@app.route('/api/swipe/feed', methods=['GET'])
@limiter.limit("60 per minute")
def swipe_feed():
    """
    Public feed of leads for the Tinder-style swipe UI.

    Query params:
      - limit:         how many leads to return (default 10, max 20)
      - anon_id:       stable client-generated id for anonymous visitors
      - hot_only:      '1' to return only HOT leads (score >= 90)
      - min_score:     minimum score (0-100, default 0)
      - min_value:     minimum project value in USD (default 0)
      - max_value:     maximum project value in USD (0 = no limit)
      - city:          filter by city name (partial match)
      - radius_miles:  miles radius from city (requires city param)
      - service_cats:  comma-separated list of categories
                       subcontractor: roofing, drywall, paint, electrical,
                         plumbing, hvac, flooring, concrete, framing, windows, landscaping
                       lead type: solar, permits, construction, realestate,
                         flood, energy, rodents, deconstruction, remodel

    Anonymous visitors can view up to ANON_LEAD_LIMIT leads total.
    """
    user_id, anon_id = _resolve_swipe_identity()

    try:
        limit = min(int(request.args.get("limit", 10)), 20)
    except (TypeError, ValueError):
        limit = 10

    # ── Filter params ──────────────────────────────────────────────────────────
    hot_only = request.args.get("hot_only", "0") == "1"
    try:
        min_score = int(request.args.get("min_score", 0))
    except (TypeError, ValueError):
        min_score = 0
    if hot_only:
        min_score = max(min_score, 90)

    try:
        min_value = float(request.args.get("min_value", 0))
    except (TypeError, ValueError):
        min_value = 0.0
    try:
        max_value = float(request.args.get("max_value", 0))
    except (TypeError, ValueError):
        max_value = 0.0

    city_filter = (request.args.get("city") or "").strip()
    try:
        radius_miles = float(request.args.get("radius_miles", 0))
    except (TypeError, ValueError):
        radius_miles = 0.0

    raw_cats = (request.args.get("service_cats") or "").strip()
    selected_cats = [c.strip().lower() for c in raw_cats.split(",") if c.strip()] if raw_cats else []

    # Pre-compute origin coords for radius filtering
    origin_coords = _city_coords(city_filter) if (city_filter and radius_miles > 0) else None
    do_radius = origin_coords is not None or (city_filter and radius_miles > 0)

    already_swiped = _already_swiped_ids(user_id, anon_id)
    swipes_count = len(already_swiped)

    remaining = None
    if not user_id:
        if not anon_id:
            return jsonify({"error": "anon_id required for anonymous browsing"}), 400

        remaining = max(ANON_LEAD_LIMIT - swipes_count, 0)
        if remaining == 0:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "register",
                "anon_limit":   ANON_LEAD_LIMIT,
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200
        limit = min(limit, remaining)
    else:
        # Check free-tier quota for authenticated non-paid users
        conn2 = get_db_connection()
        c2 = conn2.cursor()
        c2.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
        row2 = c2.fetchone()
        conn2.close()
        is_paid = bool(row2 and row2[0])
        if not is_paid and swipes_count >= FREE_USER_LEAD_LIMIT:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   FREE_USER_LEAD_LIMIT,
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200

    conn = get_db_connection()
    c = conn.cursor()

    # ── Build WHERE clause ─────────────────────────────────────────────────────
    conditions: list[str] = []
    params: list = []

    if already_swiped:
        placeholders = ",".join("?" * len(already_swiped))
        conditions.append(f"address_key NOT IN ({placeholders})")
        params.extend(already_swiped)

    if min_score > 0:
        conditions.append(
            "CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) >= ?"
        )
        params.append(min_score)

    if min_value > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) >= ?"
        )
        params.append(min_value)

    if max_value > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) <= ?"
        )
        params.append(max_value)

    # Contact info filter — only show leads with phone or email when enabled
    # Uses the pre-computed has_contact column for index performance
    if REQUIRE_CONTACT:
        conditions.append("has_contact = 1")

    # City filter: without radius → simple LIKE; with radius → post-process
    if city_filter and not do_radius:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")

    # ── Service category filter ────────────────────────────────────────────────
    if selected_cats:
        cat_parts: list[str] = []
        for cat in selected_cats:
            if cat in _SERVICE_CAT_KEYWORDS:
                kws = _SERVICE_CAT_KEYWORDS[cat]
                kw_sql = " OR ".join(["LOWER(lead_data) LIKE ?" for _ in kws])
                cat_parts.append(f"({kw_sql})")
                params.extend(f"%{k}%" for k in kws)
            elif cat in _SERVICE_TYPE_CATS:
                cat_parts.append("primary_service_type = ?")
                params.append(cat)
        if cat_parts:
            conditions.append("(" + " OR ".join(cat_parts) + ")")

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Fetch extra rows when radius filtering (post-process will trim)
    fetch_limit = limit * 10 if do_radius else limit

    query = f"""
        SELECT address_key, address, city, agent_sources, lead_data,
               primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) DESC,
                 last_updated DESC
        LIMIT ?
    """
    params.append(fetch_limit)
    c.execute(query, params)

    rows = c.fetchall()

    c.execute("SELECT name, display_label, emoji FROM service_types")
    service_types_map = {
        row[0]: {"label": row[1], "emoji": row[2]} for row in c.fetchall()
    }

    # ── Batch-fetch upcoming scheduled inspections for all addresses ──────────
    all_addresses = [dict(r).get("address") for r in rows if dict(r).get("address")]
    insp_map: dict = {}
    if all_addresses:
        try:
            ph = ",".join("?" * len(all_addresses))
            c.execute(f"""
                SELECT si.address, si.inspection_date, si.inspection_type,
                       si.inspector_name, si.time_window_start, si.time_window_end,
                       si.gc_presence_probability
                FROM scheduled_inspections si
                INNER JOIN (
                    SELECT address, MIN(inspection_date) AS min_date
                    FROM scheduled_inspections
                    WHERE inspection_date >= date('now') AND address IN ({ph})
                    GROUP BY address
                ) best ON si.address = best.address AND si.inspection_date = best.min_date
            """, all_addresses)
            for r in c.fetchall():
                rd = dict(r)
                insp_map[rd["address"]] = rd
        except Exception as ie:
            logger.debug(f"Inspection batch lookup failed: {ie}")

    conn.close()

    leads = []
    for row in rows:
        row_dict = dict(row)
        try:
            lead_data = json.loads(row_dict.get("lead_data") or "{}")
        except Exception:
            lead_data = {}

        # ── Radius filter (post-process) ───────────────────────────────────────
        if do_radius and radius_miles > 0:
            # Prefer actual lat/lon stored in lead_data
            lead_lat = lead_data.get("lat")
            lead_lon = lead_data.get("lon")
            if lead_lat and lead_lon:
                try:
                    ref = origin_coords or _city_coords(city_filter)
                    if ref:
                        dist = _haversine_miles(ref[0], ref[1], float(lead_lat), float(lead_lon))
                        if dist > radius_miles:
                            continue
                except (TypeError, ValueError):
                    pass
            else:
                # Fall back to city-name lookup
                lead_city_coords = _city_coords(row_dict.get("city", ""))
                if lead_city_coords and origin_coords:
                    dist = _haversine_miles(
                        origin_coords[0], origin_coords[1],
                        lead_city_coords[0], lead_city_coords[1],
                    )
                    if dist > radius_miles:
                        continue
                elif city_filter:
                    # Unknown city — include only if city name matches
                    lead_city = (row_dict.get("city") or "").lower()
                    if city_filter.lower() not in lead_city:
                        continue

        scoring = lead_data.get("_scoring", {}) or {}
        service_type = (
            row_dict.get("primary_service_type")
            or (row_dict["agent_sources"].split(",")[0]
                if row_dict.get("agent_sources") else None)
        )
        service_info = service_types_map.get(service_type, {})

        desc = (lead_data.get("description") or lead_data.get("desc") or "")[:300]
        phone = (lead_data.get("contact_phone") or "").strip()
        email = (lead_data.get("contact_email") or "").strip()
        contractor = (lead_data.get("contractor") or "").strip()
        owner = (lead_data.get("owner") or "").strip()
        permit_type = (lead_data.get("permit_type") or "").strip()
        issued_date = (lead_data.get("issued_date") or lead_data.get("issue_date") or "").strip()[:10]
        lic_number  = (lead_data.get("lic_number") or lead_data.get("license") or "").strip()
        permit_id   = (lead_data.get("permit_id") or lead_data.get("id") or "").strip()

        # Inspection data: prefer calendar table, fall back to lead_data predictor
        insp = insp_map.get(row_dict.get("address"), {})
        inspection_date = (
            insp.get("inspection_date")
            or lead_data.get("next_scheduled_inspection_date")
            or ""
        )
        if inspection_date:
            inspection_date = str(inspection_date).strip()[:10]
        inspection_type   = (insp.get("inspection_type") or lead_data.get("next_inspection_type") or "").strip()
        inspector_name    = (insp.get("inspector_name") or "").strip()
        tw_start          = (insp.get("time_window_start") or "").strip()
        tw_end            = (insp.get("time_window_end") or "").strip()
        time_window       = f"{tw_start} – {tw_end}" if tw_start and tw_end else tw_start or tw_end
        inspection_source = (lead_data.get("inspection_source") or "").strip()
        gc_probability    = insp.get("gc_presence_probability") or lead_data.get("_gc_presence_probability") or 0

        leads.append({
            "id":               row_dict["address_key"],
            "address":          row_dict["address"],
            "city":             row_dict["city"],
            "description":      desc,
            "value":            lead_data.get("value_float") or 0,
            "score":            scoring.get("score", 0),
            "grade":            scoring.get("grade", ""),
            "grade_emoji":      scoring.get("grade_emoji", ""),
            "reasons":          scoring.get("reasons", [])[:4],
            "service_type":     service_type,
            "service_label":    service_info.get("label", ""),
            "service_emoji":    service_info.get("emoji", ""),
            "contractor":       contractor,
            "owner":            owner,
            "phone":            phone,
            "email":            email,
            "permit_type":      permit_type,
            "issued_date":      issued_date,
            "lic_number":       lic_number,
            "permit_id":        permit_id,
            "inspection_date":  inspection_date,
            "inspection_type":  inspection_type,
            "inspector_name":   inspector_name,
            "time_window":      time_window,
            "inspection_source":inspection_source,
            "gc_probability":   round(float(gc_probability or 0), 2),
            "created_at":       row_dict.get("first_seen", ""),
        })

        if len(leads) >= limit:
            break

    # Smart sort: leads with imminent inspections get priority over raw score
    from datetime import date as _date
    _today = _date.today()
    def _sort_key(ld):
        insp = ld.get("inspection_date", "")
        urgency = 0
        if insp:
            try:
                days = (_date.fromisoformat(insp[:10]) - _today).days
                if 0 <= days <= 1:   urgency = 300
                elif days <= 3:      urgency = 200
                elif days <= 7:      urgency = 100
            except Exception:
                pass
        return -(int(ld.get("score", 0)) + urgency)
    leads.sort(key=_sort_key)

    response = {
        "leads":         leads,
        "auth_required": False,
        "anon_limit":    ANON_LEAD_LIMIT,
        "free_limit":    FREE_USER_LEAD_LIMIT,
        "swipes_count":  swipes_count,
        "is_paid":       locals().get('is_paid', False) if user_id else None,
    }
    if remaining is not None:
        response["remaining"] = remaining
    return jsonify(response), 200


@app.route('/api/swipe/action', methods=['POST'])
@limiter.limit("30 per minute")
def swipe_action():
    """
    Record a like/dislike swipe.

    Body: {"lead_id": "...", "action": "like"|"dislike", "anon_id": "..."}

    Returns the updated swipe counters and whether the anonymous
    budget is exhausted (so the client can open the login wall).
    """
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    action = (data.get("action") or "").strip().lower()

    if not lead_id or action not in ("like", "dislike"):
        return jsonify({"error": "lead_id and valid action required"}), 400

    user_id, anon_id = _resolve_swipe_identity()
    if not user_id and not anon_id:
        return jsonify({"error": "anon_id or auth required"}), 400

    if not user_id:
        current = _count_swipes(None, anon_id)
        if current >= ANON_LEAD_LIMIT:
            return jsonify({
                "ok": False,
                "auth_required": True,
                "auth_mode":    "register",
                "anon_limit": ANON_LEAD_LIMIT,
                "swipes_count": current,
                "remaining": 0,
            }), 200
    else:
        # Check free-tier quota for authenticated non-paid users
        _conn = get_db_connection()
        _c = _conn.cursor()
        _c.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
        _row = _c.fetchone()
        _conn.close()
        _is_paid = bool(_row and _row[0])
        _current = _count_swipes(user_id, None)
        if not _is_paid and _current >= FREE_USER_LEAD_LIMIT:
            return jsonify({
                "ok": False,
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   FREE_USER_LEAD_LIMIT,
                "swipes_count": _current,
                "remaining":    0,
            }), 200

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO swipe_actions (user_id, anon_id, lead_id, action)
            VALUES (?, ?, ?, ?)
        """, (user_id, anon_id, lead_id, action))
        # If an authenticated user liked the lead, also log it as a contact
        if action == 'like' and user_id:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
                    VALUES (?, ?, 'swipe_like', '')
                """, (user_id, lead_id))
            except Exception as log_err:
                logger.debug(f"lead_contacts log failed: {log_err}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        logger.warning(f"swipe_action insert failed: {e}")
        return jsonify({"error": "failed to record swipe"}), 500
    conn.close()

    # ── Alert admin after 50 consecutive rejections ───────────────────────────
    if action == 'dislike':
        try:
            _check_and_alert_rejections(user_id, anon_id)
        except Exception as _alert_err:
            logger.debug(f"rejection alert check failed: {_alert_err}")

    swipes_count = _count_swipes(user_id, anon_id)
    remaining = None
    auth_required = False
    if not user_id:
        remaining = max(ANON_LEAD_LIMIT - swipes_count, 0)
        auth_required = remaining == 0

    return jsonify({
        "ok":            True,
        "auth_required": auth_required,
        "anon_limit":    ANON_LEAD_LIMIT,
        "swipes_count":  swipes_count,
        "remaining":     remaining,
    }), 200


def _check_and_alert_rejections(user_id, anon_id):
    """Send admin alert when a user hits 50 rejections (dislikes)."""
    REJECTION_ALERT_THRESHOLD = 50
    conn = get_db_connection()
    c = conn.cursor()
    if user_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE user_id = ? AND action = 'dislike'",
            (user_id,)
        )
    elif anon_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE anon_id = ? AND action = 'dislike'",
            (anon_id,)
        )
    else:
        conn.close()
        return
    count = c.fetchone()[0]
    conn.close()

    if count == REJECTION_ALERT_THRESHOLD:
        # Send Telegram notification to admin
        try:
            from utils.telegram import send_message
            identity = f"user_id={user_id}" if user_id else f"anon_id={anon_id}"
            send_message(
                f"⚠️ *Alerta de desinterés*\n"
                f"El usuario `{identity}` ha rechazado *{count} leads* consecutivos.\n"
                f"Puede necesitar ayuda para encontrar leads relevantes."
            )
        except Exception as e:
            logger.warning(f"Failed to send rejection alert: {e}")


@app.route('/api/swipe/upgrade-info', methods=['GET'])
def swipe_upgrade_info():
    """Return current user's quota status."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"anon": True, "limit": ANON_LEAD_LIMIT}), 200
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(is_paid, 0) FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    is_paid = bool(row and row[0])
    swipes = _count_swipes(user_id, None)
    return jsonify({
        "is_paid":     is_paid,
        "swipes":      swipes,
        "free_limit":  FREE_USER_LEAD_LIMIT,
        "pro_limit":   PRO_LEAD_LIMIT,
        "remaining":   None if is_paid else max(FREE_USER_LEAD_LIMIT - swipes, 0),
        "tiers": [
            {"id": "pro",     "price": 29,  "limit": PRO_LEAD_LIMIT, "label": "Pro"},
            {"id": "premium", "price": 99,  "limit": None,           "label": "Premium"},
        ],
    }), 200


# ─────────────────────────────────────────────────────────
# Social Login (Google / Facebook) for swipe app
# ─────────────────────────────────────────────────────────

def _upsert_oauth_user(provider: str, sub: str, email: str,
                       full_name: str, avatar_url: str) -> int:
    """
    Create or update a user for a given OAuth identity.
    Returns the user_id.
    """
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT id FROM users
        WHERE oauth_provider = ? AND oauth_sub = ?
    """, (provider, sub))
    row = c.fetchone()

    if row:
        user_id = row[0]
        c.execute("""
            UPDATE users
               SET full_name = COALESCE(?, full_name),
                   avatar_url = COALESCE(?, avatar_url),
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
        """, (full_name or None, avatar_url or None, user_id))
    else:
        # Fall back: try to match by email
        if email:
            c.execute("SELECT id FROM users WHERE email = ?", (email,))
            existing = c.fetchone()
        else:
            existing = None

        if existing:
            user_id = existing[0]
            c.execute("""
                UPDATE users
                   SET oauth_provider = ?,
                       oauth_sub = ?,
                       avatar_url = COALESCE(?, avatar_url),
                       full_name = COALESCE(?, full_name),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
            """, (provider, sub, avatar_url or None,
                  full_name or None, user_id))
        else:
            username = (email or f"{provider}_{sub}")[:64]
            safe_email = email or f"{provider}_{sub}@oauth.local"
            c.execute("""
                INSERT INTO users (username, email, password_hash, full_name,
                                   oauth_provider, oauth_sub, avatar_url,
                                   is_active)
                VALUES (?, ?, '', ?, ?, ?, ?, 1)
            """, (username, safe_email, full_name or username,
                  provider, sub, avatar_url or None))
            user_id = c.lastrowid

    conn.commit()
    conn.close()
    return user_id


def _verify_google_id_token(id_token: str) -> dict | None:
    """
    Verify a Google ID token via Google's tokeninfo endpoint.
    Returns the claims dict or None if invalid.
    """
    if not id_token:
        return None
    try:
        import requests
        resp = requests.get(
            "https://oauth2.googleapis.com/tokeninfo",
            params={"id_token": id_token},
            timeout=6,
        )
        if resp.status_code != 200:
            return None
        claims = resp.json() or {}
        if not claims.get("sub"):
            return None
        expected_aud = os.getenv("GOOGLE_CLIENT_ID", "")
        if expected_aud and claims.get("aud") != expected_aud:
            logger.warning("Google token aud mismatch")
            return None
        return claims
    except Exception as e:
        logger.warning(f"Google token verification failed: {e}")
        return None


def _verify_facebook_token(access_token: str) -> dict | None:
    """
    Verify a Facebook user access token by calling the Graph API.
    Returns the profile dict or None if invalid.
    """
    if not access_token:
        return None
    try:
        import requests
        resp = requests.get(
            "https://graph.facebook.com/me",
            params={
                "fields": "id,name,email,picture.type(large)",
                "access_token": access_token,
            },
            timeout=6,
        )
        if resp.status_code != 200:
            return None
        return resp.json() or None
    except Exception as e:
        logger.warning(f"Facebook token verification failed: {e}")
        return None


@app.route('/api/auth/oauth/google', methods=['POST'])
def oauth_google_login():
    """
    Exchange a Google ID token (from the JS Identity Services client)
    for an MLeads JWT.

    Body: {"credential": "<google-id-token>"}
    """
    data = request.get_json(silent=True) or {}
    id_token = data.get("credential") or data.get("id_token")
    claims = _verify_google_id_token(id_token)
    if not claims:
        return jsonify({"error": "Invalid Google credential"}), 401

    user_id = _upsert_oauth_user(
        provider="google",
        sub=str(claims.get("sub")),
        email=claims.get("email") or "",
        full_name=claims.get("name") or "",
        avatar_url=claims.get("picture") or "",
    )

    access_token, refresh_token = generate_tokens(user_id)
    return jsonify({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "user": {
            "id":         user_id,
            "email":      claims.get("email"),
            "full_name":  claims.get("name"),
            "avatar_url": claims.get("picture"),
            "provider":   "google",
        },
    }), 200


@app.route('/api/auth/oauth/facebook', methods=['POST'])
def oauth_facebook_login():
    """
    Exchange a Facebook user access token (from the JS SDK) for an
    MLeads JWT.

    Body: {"access_token": "<fb-access-token>"}
    """
    data = request.get_json(silent=True) or {}
    access_token_fb = data.get("access_token")
    profile = _verify_facebook_token(access_token_fb)
    if not profile or not profile.get("id"):
        return jsonify({"error": "Invalid Facebook token"}), 401

    avatar = ""
    picture = profile.get("picture") or {}
    if isinstance(picture, dict):
        avatar = (picture.get("data") or {}).get("url", "")

    user_id = _upsert_oauth_user(
        provider="facebook",
        sub=str(profile.get("id")),
        email=profile.get("email") or "",
        full_name=profile.get("name") or "",
        avatar_url=avatar,
    )

    access_token, refresh_token = generate_tokens(user_id)
    return jsonify({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "user": {
            "id":         user_id,
            "email":      profile.get("email"),
            "full_name":  profile.get("name"),
            "avatar_url": avatar,
            "provider":   "facebook",
        },
    }), 200


@app.route('/api/swipe/claim-anon', methods=['POST'])
@require_auth
def swipe_claim_anon():
    """
    After a successful OAuth login, migrate any anonymous swipes the
    user made (tracked by anon_id) onto their new user_id so their
    history carries over.

    Body: {"anon_id": "..."}
    """
    data = request.get_json(silent=True) or {}
    anon_id = (data.get("anon_id") or "").strip()
    if not anon_id:
        return jsonify({"ok": True, "migrated": 0}), 200

    conn = get_db_connection()
    c = conn.cursor()

    # Get anon likes before migrating (to create lead_contacts records)
    c.execute("""
        SELECT lead_id FROM swipe_actions
        WHERE anon_id = ? AND action = 'like' AND user_id IS NULL
          AND lead_id NOT IN (SELECT lead_id FROM swipe_actions WHERE user_id = ?)
    """, (anon_id, g.user_id))
    anon_like_ids = [row[0] for row in c.fetchall()]

    c.execute("""
        UPDATE swipe_actions
           SET user_id = ?, anon_id = NULL
         WHERE anon_id = ?
           AND user_id IS NULL
           AND lead_id NOT IN (
               SELECT lead_id FROM swipe_actions WHERE user_id = ?
           )
    """, (g.user_id, anon_id, g.user_id))
    migrated = c.rowcount

    # Also insert lead_contacts for every migrated like so history shows up in profile
    for lead_id in anon_like_ids:
        try:
            c.execute("""
                INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
                VALUES (?, ?, 'swipe_like', 'migrated from anonymous session')
            """, (g.user_id, lead_id))
        except Exception:
            pass

    # Drop any remaining anon rows for leads the user already swiped
    c.execute("DELETE FROM swipe_actions WHERE anon_id = ?", (anon_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "migrated": migrated}), 200


@app.route('/api/swipe/cities', methods=['GET'])
def swipe_cities():
    """Return a list of known city names for autocomplete (no auth required)."""
    q = (request.args.get('q') or '').strip().lower()
    cities = sorted(_CITY_COORDS.keys())
    if q:
        # Prefix matches first, then contains matches
        prefix = [c for c in cities if c.startswith(q)]
        contains = [c for c in cities if q in c and not c.startswith(q)]
        cities = (prefix + contains)[:20]
    else:
        cities = cities[:40]
    return jsonify([c.title() for c in cities]), 200


@app.route('/api/swipe/feedback', methods=['POST'])
@limiter.limit("5 per minute")
def swipe_feedback():
    """Store beta feedback from users (no auth required)."""
    data = request.get_json(silent=True) or {}
    message = (data.get('message') or '').strip()[:2000]
    if not message:
        return jsonify({"error": "message required"}), 400
    anon_id = (data.get('anon_id') or '').strip()[:64] or None
    user_id, _ = _resolve_swipe_identity()
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(
            "INSERT INTO beta_feedback (message, anon_id, user_id) VALUES (?, ?, ?)",
            (message, anon_id, user_id)
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"beta_feedback insert failed: {e}")
        conn.close()
        return jsonify({"error": "failed to save feedback"}), 500
    conn.close()
    return jsonify({"ok": True}), 200


@app.route('/api/swipe/my-contacts', methods=['GET'])
def swipe_my_contacts():
    """Return leads the authenticated user has swiped right on (liked)."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"error": "Authentication required"}), 401

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT sa.lead_id, MAX(sa.created_at) as contacted_at,
               cl.address, cl.city, cl.lead_data
        FROM swipe_actions sa
        JOIN consolidated_leads cl ON cl.address_key = sa.lead_id
        WHERE sa.user_id = ? AND sa.action = 'like'
        GROUP BY sa.lead_id
        ORDER BY contacted_at DESC
        LIMIT 100
    """, (user_id,))
    rows = c.fetchall()
    conn.close()

    contacts = []
    for row in rows:
        rd = dict(row)
        try:
            ld = json.loads(rd.get('lead_data') or '{}')
        except Exception:
            ld = {}
        scoring = ld.get('_scoring', {}) or {}
        contacts.append({
            'id':           rd['lead_id'],
            'address':      rd['address'],
            'city':         rd['city'],
            'contacted_at': rd['contacted_at'],
            'score':        scoring.get('score', 0),
            'grade':        scoring.get('grade', ''),
            'phone':        (ld.get('contact_phone') or '').strip(),
            'email':        (ld.get('contact_email') or '').strip(),
            'value':        ld.get('value_float', 0),
        })
    return jsonify({'contacts': contacts}), 200


@app.route('/api/swipe/log-contact', methods=['POST'])
def swipe_log_contact():
    """Log that an authenticated user clicked a phone/email contact on a lead."""
    user_id, _ = _resolve_swipe_identity()
    if not user_id:
        return jsonify({"ok": False}), 200  # silently ignore anon
    data = request.get_json(silent=True) or {}
    lead_id = (data.get('lead_id') or '').strip()
    contact_type = data.get('contact_type', 'phone')
    if contact_type not in {'phone', 'email', 'text', 'visit', 'other'}:
        contact_type = 'other'
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
            VALUES (?, ?, ?, '')
        """, (user_id, lead_id, contact_type))
        conn.commit()
    except Exception as e:
        logger.debug(f"swipe_log_contact failed: {e}")
    conn.close()
    return jsonify({"ok": True}), 200


@app.route('/api/admin/feedback', methods=['GET'])
@require_admin
def list_feedback():
    """List all beta feedback (admin only)."""
    user_id = g.user_id
    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM beta_feedback")
        total = c.fetchone()[0]
        c.execute("""
            SELECT id, message, anon_id, user_id, created_at
            FROM beta_feedback ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (per_page, offset))
        rows = [dict(r) for r in c.fetchall()]
    except Exception as e:
        conn.close()
        return jsonify({"error": "Internal server error"}), 500
    conn.close()
    return jsonify({"feedback": rows, "total": total, "page": page, "pages": (total + per_page - 1) // per_page}), 200


@app.route('/api/admin/feedback/<int:fb_id>', methods=['DELETE'])
@require_admin
def delete_feedback(fb_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM beta_feedback WHERE id = ?", (fb_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 200


# ─────────────────────────────────────────────────────────
# App Initialization
# ─────────────────────────────────────────────────────────

def create_app():
    """Application factory."""
    # Validate required secrets before accepting traffic
    jwt_secret = os.getenv("JWT_SECRET_KEY", "")
    if not jwt_secret or jwt_secret == "change-me-in-production":
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable is not set or is the default value. "
            "Set it to a long random string before starting the server."
        )

    init_web_db()
    seed_cities_and_agents()

    # Start the inspection scheduler for automatic calendar updates
    try:
        start_inspection_scheduler()
    except Exception as e:
        logger.warning(f"Failed to start inspection scheduler: {e}")

    # Start the Telegram bot worker (long polling). No-op if the token
    # isn't set or BOT_WORKER_ENABLED=false.
    try:
        start_bot_worker()
    except Exception as e:
        logger.warning(f"Failed to start Telegram bot worker: {e}")

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
