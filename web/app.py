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
import hashlib
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
from web.helpers.service_filter import build_service_category_filter
from web.helpers.gc_interest import (
    build_gc_insight,
    build_gc_interest_sql_filter,
    build_public_real_lead_sql_filter,
    is_gc_interesting_lead,
    is_placeholder_or_demo_lead,
)

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
# Use Redis if available (shared across gunicorn workers), else fall back to
# memory per-worker. In production with multiple workers use:
#   RATELIMIT_STORAGE_URI=redis://localhost:6379/0
_rl_uri = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],          # no global limit — set per-route
    storage_uri=_rl_uri,
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


def _clean_text(value, max_len=300):
    """Normalize user-submitted text into a bounded plain string."""
    return " ".join(str(value or "").strip().split())[:max_len]


def _normalize_phone(value):
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _homeowner_budget_value(budget_range):
    values = {
        "under_25k": 15000,
        "25_75k": 50000,
        "75_150k": 110000,
        "150_300k": 225000,
        "300k_plus": 375000,
        "unsure": 0,
    }
    return values.get((budget_range or "").strip(), 0)


def _homeowner_lead_score(has_email, budget_value, timeline, description):
    score = 82
    reasons = [
        "Homeowner directo pide contacto antes de buscar GC.",
        "Teléfono validado por formulario de intención.",
    ]
    if has_email:
        score += 3
        reasons.append("Incluye email para seguimiento.")
    if budget_value >= 75000:
        score += 6
        reasons.append("Presupuesto indicado sugiere proyecto vendible para GC.")
    if timeline in {"asap", "0_30", "1_3_months"}:
        score += 5
        reasons.append("Timeline cercano aumenta probabilidad de cierre.")
    if len(description or "") >= 80:
        score += 3
        reasons.append("Descripción suficiente para calificar alcance inicial.")
    score = min(score, 98)
    return {
        "score": score,
        "grade": "HOT" if score >= 90 else "WARM",
        "reasons": reasons,
    }


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


@app.route('/homeowner-intake', methods=['GET'])
@app.route('/homeowner-intake.html', methods=['GET'])
def homeowner_intake_page():
    """Serve the public homeowner project intake page."""
    intake_path = os.path.join(os.path.dirname(__file__), 'templates', 'homeowner_intake.html')
    try:
        with open(intake_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Homeowner intake page not found"}), 404


@app.route('/pipeline', methods=['GET'])
@app.route('/pipeline.html', methods=['GET'])
def pipeline_page():
    """Serve the Kanban pipeline page for swiped/right leads."""
    pipeline_path = os.path.join(os.path.dirname(__file__), 'templates', 'pipeline.html')
    try:
        with open(pipeline_path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return jsonify({"error": "Pipeline page not found"}), 404


@app.route('/api/homeowner-intake', methods=['POST'])
@limiter.limit("12 per minute")
def homeowner_intake_submit():
    """
    Capture a direct homeowner project request and publish it as a GC-ready lead.

    This channel targets owners who are still planning an addition/remodel and
    have not yet selected a GC, making the lead valuable before permits appear.
    """
    data = request.get_json(silent=True) or {}
    full_name = _clean_text(data.get("full_name"), 120)
    phone = _normalize_phone(data.get("phone"))
    email = _clean_text(data.get("email"), 160).lower()
    address = _clean_text(data.get("address"), 180)
    city = _clean_text(data.get("city"), 80)
    state = _clean_text(data.get("state"), 20).upper()
    zip_code = _clean_text(data.get("zip"), 20)
    project_type = _clean_text(data.get("project_type"), 80)
    timeline = _clean_text(data.get("timeline"), 40)
    budget_range = _clean_text(data.get("budget_range"), 40)
    best_time = _clean_text(data.get("best_time"), 120)
    description = _clean_text(data.get("description"), 1200)

    missing = []
    for field_name, value in [
        ("full_name", full_name),
        ("phone", phone),
        ("address", address),
        ("city", city),
        ("project_type", project_type),
        ("timeline", timeline),
        ("description", description),
    ]:
        if not value:
            missing.append(field_name)
    if missing:
        return jsonify({"error": "missing_required_fields", "fields": missing}), 400

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    identity = "|".join([address.lower(), city.lower(), state.lower(), phone, email])
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
    lead_id = f"homeowner_{digest}"
    source_base = os.getenv('BASE_URL', request.host_url).rstrip('/')
    source_url = f"{source_base}/homeowner-intake"
    budget_value = _homeowner_budget_value(budget_range)
    scoring = _homeowner_lead_score(bool(email), budget_value, timeline, description)
    project_label = project_type.replace("_", " ")
    summary = (
        f"Homeowner planea {project_label} y quiere hablar con un GC antes de iniciar permisos. "
        f"Timeline: {timeline}. Presupuesto: {budget_range or 'unsure'}."
    )

    lead_data = {
        "id": lead_id,
        "source": "homeowner_intake",
        "source_label": "Homeowner request",
        "source_url": source_url,
        "primary_service_type": "remodel",
        "service_type": "remodel",
        "address": address,
        "city": city,
        "state": state,
        "zip": zip_code,
        "owner": full_name,
        "homeowner_name": full_name,
        "contact_phone": phone,
        "contact_email": email,
        "contractor": "NONE",
        "permit_type": "Homeowner GC request",
        "description": f"Homeowner is planning {project_label}. {description}",
        "project_type": project_type,
        "timeline": timeline,
        "budget_range": budget_range,
        "best_time": best_time,
        "value_float": budget_value,
        "_scoring": scoring,
        "_trade": "GENERAL_CONTRACTOR",
        "_urgency": "HIGH" if timeline in {"asap", "0_30", "1_3_months"} else "MEDIUM",
        "_ai_summary": summary,
        "_is_residential": True,
        "_project_phase": "planning",
        "_decision_maker": "homeowner",
        "_owner_type": "HOMEOWNER",
        "_services": [project_type],
        "_key_pain_point": "Needs GC estimate before permitting",
        "_upsell_opportunity": "Design-build, permit help, addition/remodel package",
        "_lead_channel": "homeowner_intake",
    }

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO homeowner_project_intakes (
            lead_id, full_name, phone, email, address, city, state, zip_code,
            project_type, timeline, budget_range, description, best_time, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?)
    """, (
        lead_id, full_name, phone, email, address, city, state, zip_code,
        project_type, timeline, budget_range, description, best_time, now,
    ))
    c.execute("""
        INSERT OR REPLACE INTO consolidated_leads (
            address_key, address, city, agent_sources, first_seen, last_updated,
            lead_data, notified, primary_service_type, has_contact, has_phone, is_dead_lead
        ) VALUES (
            ?, ?, ?, 'homeowner_intake',
            COALESCE((SELECT first_seen FROM consolidated_leads WHERE address_key = ?), ?),
            ?, ?, 0, 'remodel', 1, 1, 0
        )
    """, (
        lead_id, address, city, lead_id, now, now, json.dumps(lead_data, ensure_ascii=False),
    ))
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "lead_id": lead_id,
        "score": scoring["score"],
        "grade": scoring["grade"],
        "message": "project_request_received",
    }), 201


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


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit("5 per minute")
def register():
    """Public registration for the swipe / web app."""
    data = request.get_json(silent=True) or {}
    email    = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    full_name = (data.get('full_name') or data.get('name') or '').strip()

    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({"error": "Email válido requerido"}), 400
    if len(password) < 6:
        return jsonify({"error": "La contraseña debe tener al menos 6 caracteres"}), 400

    # Derive username from email prefix, ensure uniqueness
    base_uname = email.split('@')[0][:32].lower()
    base_uname = ''.join(c for c in base_uname if c.isalnum() or c in ('_', '-')) or 'user'
    username = base_uname

    conn = get_db_connection()
    c = conn.cursor()
    credit_granted = False
    replacement_credits = 0
    try:
        c.execute("SELECT id FROM users WHERE email = ?", (email,))
        if c.fetchone():
            return jsonify({"error": "Este email ya está registrado"}), 409

        suffix = 1
        while True:
            c.execute("SELECT id FROM users WHERE username = ?", (username,))
            if not c.fetchone():
                break
            username = f"{base_uname}{suffix}"
            suffix += 1

        password_hash = hash_password(password)
        c.execute("""
            INSERT INTO users (username, email, password_hash, full_name, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (username, email, password_hash, full_name or username))
        user_id = c.lastrowid
        conn.commit()
    finally:
        conn.close()

    access_token, refresh_token = generate_tokens(user_id)
    return jsonify({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "user": {
            "id":        user_id,
            "email":     email,
            "full_name": full_name or username,
            "provider":  "email",
        },
    }), 201


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


@app.route('/api/admin/elite-pilot-requests', methods=['GET'])
@require_admin
def admin_elite_pilot_requests():
    """Show captured Elite demand in markets that were not ready for $500 checkout."""
    status = (request.args.get("status") or "open").strip().lower()
    if status not in {"open", "contacted", "closed", "all"}:
        return jsonify({"error": "Invalid status"}), 400

    conn = get_db_connection()
    c = conn.cursor()
    try:
        where = "" if status == "all" else "WHERE epr.status = ?"
        params = [] if status == "all" else [status]
        c.execute(f"""
            SELECT epr.id, epr.user_id, u.email, u.full_name,
                   epr.city, epr.service, epr.readiness_status,
                   epr.recommended_price, epr.requested_price,
                   epr.proof_json, epr.source, epr.status,
                   epr.created_at, epr.updated_at
              FROM elite_pilot_requests epr
              LEFT JOIN users u ON u.id = epr.user_id
              {where}
             ORDER BY epr.updated_at DESC
             LIMIT 200
        """, params)
        requests = []
        for row in c.fetchall():
            item = dict(row)
            try:
                item["proof"] = json.loads(item.pop("proof_json") or "{}")
            except Exception:
                item["proof"] = {}
            requests.append(item)

        c.execute(f"""
            SELECT COALESCE(NULLIF(city, ''), 'Unspecified') AS city,
                   COALESCE(NULLIF(service, ''), 'all') AS service,
                   readiness_status,
                   COUNT(*) AS requests,
                   MAX(updated_at) AS last_requested_at
              FROM elite_pilot_requests epr
              {where}
             GROUP BY city, service, readiness_status
             ORDER BY requests DESC, last_requested_at DESC
             LIMIT 50
        """, params)
        markets = [dict(row) for row in c.fetchall()]
    finally:
        conn.close()

    return jsonify({
        "requests": requests,
        "markets": markets,
        "summary": {
            "total_requests": len(requests),
            "markets": len(markets),
            "status": status,
        },
    }), 200


@app.route('/api/admin/elite-pilot-requests/<int:request_id>', methods=['PATCH'])
@require_admin
def admin_update_elite_pilot_request(request_id):
    """Update sales follow-up status for captured Elite pilot demand."""
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in {"open", "contacted", "closed"}:
        return jsonify({"error": "Status must be open, contacted or closed"}), 400

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE elite_pilot_requests
               SET status = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
        """, (status, request_id))
        if c.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Elite pilot request not found"}), 404
        conn.commit()
        c.execute("""
            SELECT epr.id, epr.user_id, u.email, u.full_name,
                   epr.city, epr.service, epr.readiness_status,
                   epr.recommended_price, epr.requested_price,
                   epr.source, epr.status, epr.created_at, epr.updated_at
              FROM elite_pilot_requests epr
              LEFT JOIN users u ON u.id = epr.user_id
             WHERE epr.id = ?
        """, (request_id,))
        row = c.fetchone()
        return jsonify({"ok": True, "request": dict(row) if row else {"id": request_id, "status": status}}), 200
    finally:
        conn.close()


@app.route('/api/admin/elite-claims', methods=['GET'])
@require_admin
def admin_elite_claims():
    """Audit Elite lead exclusivity reservations by contractor and lead."""
    status = (request.args.get("status") or "active").strip().lower()
    if status not in {"active", "reported", "expired", "all"}:
        return jsonify({"error": "Invalid status"}), 400

    params: list = []
    if status == "active":
        where = "WHERE c.status = 'active' AND datetime(c.expires_at) > datetime('now')"
    elif status == "expired":
        where = "WHERE c.status = 'active' AND datetime(c.expires_at) <= datetime('now')"
    elif status == "reported":
        where = "WHERE c.status = 'reported'"
    else:
        where = ""

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(f"""
            SELECT c.lead_id, c.user_id, u.email, u.full_name,
                   c.claim_type, c.status, c.claimed_at, c.expires_at,
                   l.address, l.city, l.primary_service_type
              FROM elite_lead_claims c
              LEFT JOIN users u ON u.id = c.user_id
              LEFT JOIN consolidated_leads l ON l.address_key = c.lead_id
              {where}
             ORDER BY datetime(c.expires_at) ASC, datetime(c.claimed_at) DESC
             LIMIT 200
        """, params)
        claims = [dict(row) for row in c.fetchall()]

        c.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN status = 'active' AND datetime(expires_at) > datetime('now') THEN 1 ELSE 0 END), 0) AS active_claims,
                COALESCE(SUM(CASE WHEN status = 'reported' THEN 1 ELSE 0 END), 0) AS reported_claims,
                COALESCE(SUM(CASE WHEN status = 'active' AND datetime(expires_at) <= datetime('now') THEN 1 ELSE 0 END), 0) AS expired_claims,
                COUNT(DISTINCT CASE WHEN status = 'active' AND datetime(expires_at) > datetime('now') THEN user_id END) AS active_contractors
            FROM elite_lead_claims
        """)
        summary = dict(c.fetchone())
    finally:
        conn.close()

    return jsonify({
        "claims": claims,
        "summary": summary,
        "status": status,
    }), 200


# ─────────────────────────────────────────────────────────
# Stripe — checkout + webhook
# ─────────────────────────────────────────────────────────

@app.route('/api/payment/checkout', methods=['POST'])
@require_auth
@limiter.limit("10 per minute")
def create_payment_checkout():
    """
    Create a Stripe Checkout session for the authenticated web user.
    Body: {"tier": "pro" | "premium" | "elite"}
    Returns: {"checkout_url": "https://checkout.stripe.com/..."}
    Requires STRIPE_API_KEY and STRIPE_PRICE_ID_PRO / STRIPE_PRICE_ID_PREMIUM / STRIPE_PRICE_ID_ELITE in env.
    """
    data = request.get_json(silent=True) or {}
    tier = (data.get('tier') or 'pro').lower()
    if tier not in ('pro', 'premium', 'elite'):
        return jsonify({"error": "Tier must be 'pro', 'premium' or 'elite'"}), 400

    elite_gate, checkout_context = _elite_checkout_guard(tier, data)
    if elite_gate:
        return elite_gate

    stripe_key = os.getenv('STRIPE_API_KEY', '')
    price_id   = os.getenv(f'STRIPE_PRICE_ID_{tier.upper()}', os.getenv('STRIPE_PRICE_ID', ''))
    if not stripe_key or not price_id:
        return jsonify({"error": "Pago no configurado. Contacta a soporte.", "code": "stripe_not_configured"}), 503

    try:
        import stripe as _stripe
        _stripe.api_key = stripe_key

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT email, full_name FROM users WHERE id = ?", (g.user_id,))
        user = c.fetchone()
        conn.close()

        base_url = os.getenv('BASE_URL', 'http://104.42.252.241:5000')
        checkout_metadata = {'user_id': str(g.user_id), 'tier': tier, **checkout_context}
        session = _stripe.checkout.Session.create(
            mode='subscription',
            line_items=[{'price': price_id, 'quantity': 1}],
            success_url=f"{base_url}/swipe?payment=success",
            cancel_url=f"{base_url}/swipe?payment=cancel",
            customer_email=user['email'] if user else None,
            client_reference_id=str(g.user_id),
            metadata=checkout_metadata,
            subscription_data={'metadata': checkout_metadata},
        )
        return jsonify({"checkout_url": session.url, "session_id": session.id}), 200
    except Exception as e:
        logger.exception(f"Stripe checkout creation failed: {e}")
        return jsonify({"error": "Error al procesar el pago. Intenta de nuevo."}), 500


def _checkout_filter(value) -> str:
    """Keep checkout metadata/filter values compact and Stripe-safe."""
    if isinstance(value, (list, tuple, set)):
        value = ",".join(str(v) for v in value if str(v).strip())
    return str(value or "").strip()[:120]


def _elite_checkout_guard(tier: str, data: dict):
    """Only sell the $500 Elite plan where inventory is actually ready."""
    city = _checkout_filter(data.get('city'))
    service = _checkout_filter(data.get('service') or data.get('service_cats'))
    context = {}
    if city:
        context['market_city'] = city
    if service:
        context['market_service'] = service

    if tier != 'elite':
        return None, context

    try:
        proof = _elite_sales_proof_payload(city, service)
    except Exception as exc:
        logger.warning(f"Elite checkout readiness unavailable: {exc}")
        return (jsonify({
            "error": "Elite requiere validación de inventario antes de cobrar. Intenta de nuevo en unos minutos.",
            "code": "elite_readiness_unavailable",
            "checkout_allowed": False,
        }), 503), context

    status = proof.get("status")
    recommended_price = int(proof.get("recommended_price") or 0)
    market = proof.get("market") or {}
    if status != "ready_for_elite" or recommended_price < 500:
        saved_request = _record_elite_pilot_request(
            int(g.user_id), city, service, status or "needs_inventory", recommended_price, proof
        )
        return (jsonify({
            "error": "Elite todavía no está listo para venderse en este mercado/filtro. Usa Premium o solicita piloto.",
            "code": "elite_market_not_ready",
            "checkout_allowed": False,
            "pilot_request_saved": saved_request,
            "status": status or "needs_inventory",
            "recommended_price": recommended_price,
            "market": market,
            "proof_points": proof.get("proof_points", []),
        }), 409), context

    context.update({
        "elite_market_status": status,
        "elite_recommended_price": str(recommended_price),
    })
    if market.get("city"):
        context["elite_market_city"] = _checkout_filter(market.get("city"))
    return None, context


def _record_elite_pilot_request(
    user_id: int,
    city: str,
    service: str,
    readiness_status: str,
    recommended_price: int,
    proof: dict,
) -> bool:
    """Capture demand when a contractor wants Elite before the market is ready."""
    try:
        proof_snapshot = {
            "status": readiness_status,
            "recommended_price": recommended_price,
            "market": proof.get("market"),
            "proof_points": proof.get("proof_points", [])[:5],
        }
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO elite_pilot_requests (
                user_id, city, service, readiness_status, recommended_price,
                requested_price, proof_json, source, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, 500, ?, 'checkout_block', 'open', CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, city, service) DO UPDATE SET
                readiness_status = excluded.readiness_status,
                recommended_price = excluded.recommended_price,
                proof_json = excluded.proof_json,
                source = excluded.source,
                status = 'open',
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id,
            city,
            service,
            readiness_status,
            recommended_price,
            json.dumps(proof_snapshot, ensure_ascii=False),
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        logger.warning(f"Could not record Elite pilot request: {exc}")
        return False


@app.route('/api/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """
    Receive Stripe webhook events. Handles both bot_users (Telegram) and
    web app users (swipe app) based on metadata fields.
    """
    payload   = request.get_data()
    signature = request.headers.get('Stripe-Signature', '')
    event     = billing.verify_webhook(payload, signature)
    if not event:
        return jsonify({"error": "invalid signature or billing not configured"}), 400
    try:
        # Check if this event is for a web user (metadata first, stored Stripe ids second).
        data_obj  = (event.get('data') or {}).get('object') or {}
        web_user_id = _resolve_web_user_id_from_stripe_object(data_obj)

        if web_user_id:
            handled = _handle_web_user_stripe_event(event, web_user_id)
        else:
            handled = billing.handle_event(event)

        return jsonify({"received": True, "handled": handled}), 200
    except Exception as e:
        logger.exception(f"Stripe webhook handler error: {e}")
        return jsonify({"error": "Internal server error"}), 500


def _handle_web_user_stripe_event(event: dict, web_user_id: str) -> bool:
    """Apply a Stripe event to the web app users table."""
    event_type = event.get('type', '')
    data_obj   = (event.get('data') or {}).get('object') or {}

    conn = get_db_connection()
    c = conn.cursor()
    try:
        if event_type in ('checkout.session.completed', 'invoice.paid', 'invoice.payment_succeeded'):
            stripe_customer = data_obj.get('customer')
            stripe_subscription = data_obj.get('subscription') or data_obj.get('id')
            metadata = data_obj.get('metadata') or {}
            tier = (metadata.get('tier') or _get_existing_subscription_tier(web_user_id) or 'premium').lower()
            # Get period end from invoice lines if available
            period_end = None
            try:
                lines = data_obj.get('lines', {}).get('data', [])
                if lines:
                    period_end = lines[0].get('period', {}).get('end')
            except Exception:
                pass
            paid_until = (
                datetime.utcfromtimestamp(int(period_end)).strftime('%Y-%m-%d %H:%M:%S')
                if period_end else
                (datetime.utcnow() + timedelta(days=31)).strftime('%Y-%m-%d %H:%M:%S')
            )
            c.execute("""
               UPDATE users
                   SET is_paid = 1,
                       subscription_tier = ?,
                       paid_until = ?,
                       stripe_customer_id = COALESCE(?, stripe_customer_id),
                       stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                       paid_since = COALESCE(paid_since, CURRENT_TIMESTAMP),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
            """, (tier, paid_until, stripe_customer, stripe_subscription, int(web_user_id)))
            conn.commit()
            logger.info(f"[webhook] web user {web_user_id} marked paid until {paid_until}")
            return True

        if event_type in ('customer.subscription.deleted', 'customer.subscription.paused'):
            stripe_customer = data_obj.get('customer')
            stripe_subscription = data_obj.get('id') or data_obj.get('subscription')
            c.execute("""
               UPDATE users
                   SET is_paid = 0,
                       subscription_tier = 'free',
                       paid_until = CURRENT_TIMESTAMP,
                       stripe_customer_id = COALESCE(?, stripe_customer_id),
                       stripe_subscription_id = COALESCE(?, stripe_subscription_id),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
            """, (stripe_customer, stripe_subscription, int(web_user_id)))
            conn.commit()
            logger.info(f"[webhook] web user {web_user_id} subscription ended")
            return True
    finally:
        conn.close()

    return False


def _resolve_web_user_id_from_stripe_object(data_obj: dict) -> str | None:
    meta = data_obj.get('metadata') or {}
    web_user_id = meta.get('user_id') or data_obj.get('client_reference_id')
    if web_user_id:
        return str(web_user_id)

    stripe_customer = data_obj.get('customer')
    stripe_subscription = data_obj.get('subscription') or data_obj.get('id')
    if not stripe_customer and not stripe_subscription:
        return None

    conn = get_db_connection()
    c = conn.cursor()
    try:
        if stripe_subscription:
            c.execute("SELECT id FROM users WHERE stripe_subscription_id = ? LIMIT 1", (stripe_subscription,))
            row = c.fetchone()
            if row:
                return str(row[0])
        if stripe_customer:
            c.execute("SELECT id FROM users WHERE stripe_customer_id = ? LIMIT 1", (stripe_customer,))
            row = c.fetchone()
            if row:
                return str(row[0])
    finally:
        conn.close()
    return None


def _get_existing_subscription_tier(web_user_id: str) -> str | None:
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT subscription_tier FROM users WHERE id = ?", (int(web_user_id),))
        row = c.fetchone()
        tier = (row[0] if row else None) or None
        return str(tier).lower() if tier and tier != 'free' else None
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────
# Public Swipe Endpoints (Tinder-style UX)
# ─────────────────────────────────────────────────────────

# Anonymous visitors can view up to this many leads before being asked
# to log in with Google or Facebook.
ANON_LEAD_LIMIT = int(os.getenv("SWIPE_ANON_LIMIT", "10"))
FREE_USER_LEAD_LIMIT = int(os.getenv("SWIPE_FREE_LIMIT", "40"))
REQUIRE_CONTACT = os.getenv("SWIPE_REQUIRE_CONTACT", "false").lower() in ("true", "1", "yes")
PRO_LEAD_LIMIT = int(os.getenv("SWIPE_PRO_LIMIT", "200"))   # $29/mo tier
ELITE_LEAD_LIMIT = int(os.getenv("SWIPE_ELITE_LIMIT", "80")) # $500/mo curated tier
ELITE_CLAIM_DAYS = int(os.getenv("SWIPE_ELITE_CLAIM_DAYS", "14"))
# PREMIUM = is_paid flag + no limit ($99/mo)


def _get_web_subscription(user_id) -> tuple[bool, str]:
    if not user_id:
        return False, "free"
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT COALESCE(is_paid, 0), COALESCE(subscription_tier, 'free'), paid_until FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()
        is_paid = bool(row and row[0])
        tier = str((row[1] if row and len(row) > 1 else "free") or "free").lower()
        paid_until = row[2] if row and len(row) > 2 else None
        if is_paid and paid_until:
            try:
                is_paid = datetime.strptime(str(paid_until)[:19], "%Y-%m-%d %H:%M:%S") > datetime.utcnow()
            except Exception:
                pass
        if is_paid and tier == "free":
            tier = "premium"
        if not is_paid:
            tier = "free"
        return is_paid, tier
    finally:
        conn.close()


def _tier_lead_limit(tier: str, is_paid: bool) -> int | None:
    if not is_paid:
        return FREE_USER_LEAD_LIMIT
    if tier == "pro":
        return PRO_LEAD_LIMIT
    if tier == "elite":
        return ELITE_LEAD_LIMIT
    return None


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
    """Count every viewed/swiped lead for quota and counter consistency."""
    conn = get_db_connection()
    c = conn.cursor()
    if user_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE user_id = ?",
            (user_id,),
        )
    elif anon_id:
        c.execute(
            "SELECT COUNT(*) FROM swipe_actions WHERE anon_id = ?",
            (anon_id,),
        )
    else:
        conn.close()
        return 0
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 0


def _elite_replacement_credit_count(user_id) -> int:
    if not user_id:
        return 0
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT COUNT(*)
            FROM elite_replacement_credits
            WHERE user_id = ? AND status = 'open'
        """, (int(user_id),))
        row = c.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def _grant_elite_replacement_credit(c, user_id: int, lead_id: str, reason: str, notes: str = "") -> bool:
    c.execute("""
        INSERT OR IGNORE INTO elite_replacement_credits (user_id, lead_id, reason, status, notes)
        VALUES (?, ?, ?, 'open', ?)
    """, (int(user_id), lead_id, reason, notes[:500]))
    return c.rowcount > 0


def _redeem_elite_replacement_credit(c, user_id: int) -> bool:
    c.execute("""
        UPDATE elite_replacement_credits
           SET status = 'redeemed',
               redeemed_at = CURRENT_TIMESTAMP
         WHERE id = (
               SELECT id
                 FROM elite_replacement_credits
                WHERE user_id = ? AND status = 'open'
                ORDER BY granted_at ASC
                LIMIT 1
         )
    """, (int(user_id),))
    return c.rowcount > 0


def _lead_age_days(lead_data: dict, fallback_date: str = "") -> int | None:
    dates: list[datetime] = []
    for field in ("issued_date", "issue_date", "event_date", "created_at", "last_updated", "_first_seen"):
        raw = (lead_data.get(field) or "").strip() if isinstance(lead_data.get(field), str) else lead_data.get(field)
        if not raw:
            continue
        try:
            text = str(raw).strip().replace("Z", "").replace("T", " ")
            if len(text) >= 19:
                dt = datetime.fromisoformat(text[:19])
            else:
                dt = datetime.fromisoformat(text[:10])
            dates.append(dt)
        except Exception:
            continue
    if fallback_date:
        raw_fallback = str(fallback_date).strip().replace("Z", "").replace("T", " ")
        try:
            if len(raw_fallback) >= 19:
                dates.append(datetime.fromisoformat(raw_fallback[:19]))
            else:
                dates.append(datetime.fromisoformat(raw_fallback[:10]))
        except Exception:
            pass
    if dates:
        return max((datetime.utcnow() - max(dates)).days, 0)
    return None


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


def _premium_quality(lead_data: dict, gc_insight: dict, service_type: str, scoring: dict, inspection_date: str = "", first_seen: str = "") -> tuple[int, list[str], bool]:
    """Score evidence that can justify high-ticket curated leads."""
    score = int(scoring.get("score") or 0)
    service = str(service_type or "").lower()
    has_source = bool(gc_insight.get("source_url"))
    has_phone = bool((lead_data.get("contact_phone") or "").strip())
    has_value = bool(lead_data.get("value_float"))
    age_days = _lead_age_days(lead_data, first_seen)
    has_action_window = bool(inspection_date)
    has_direct_owner_intent = str(lead_data.get("_lead_channel") or "") == "homeowner_intake"
    fresh_limit_days = 21 if service in {"weather", "flood", "disaster"} else 45
    has_recent_signal = has_direct_owner_intent or has_action_window or (age_days is not None and age_days <= fresh_limit_days)
    points = 0
    checks: list[str] = []
    if gc_insight.get("confidence") == "verified":
        points += 30
        checks.append("Fuente oficial verificada")
    if has_source:
        points += 15
        checks.append("Link de fuente auditable")
    if has_phone:
        points += 20
        checks.append("Teléfono disponible")
    if score >= 90:
        points += 15
        checks.append("Score HOT 90+")
    if has_value:
        points += 10
        checks.append("Valor de proyecto detectado")
    if inspection_date:
        points += 10
        checks.append("Ventana de visita/inspección")
    if service in {"weather", "flood", "disaster"}:
        checks.append("Oportunidad sensible al tiempo")
    if has_direct_owner_intent:
        checks.append("Homeowner pidió GC directamente")
    if age_days is not None and age_days <= fresh_limit_days:
        checks.append(f"Señal fresca ({age_days} días)")
    is_elite = (
        points >= 70
        and has_source
        and has_phone
        and score >= 85
        and (has_value or has_action_window or has_direct_owner_intent)
        and has_recent_signal
    )
    if not is_elite and not has_phone:
        checks.append("No Elite: falta teléfono")
    if not is_elite and not has_recent_signal:
        checks.append("No Elite: señal vieja o sin fecha")
    return min(points, 100), checks[:5], is_elite


def _elite_certificate(
    lead_data: dict,
    gc_insight: dict,
    q_score: int,
    q_checks: list[str],
    is_elite: bool,
    inspection_date: str = "",
    first_seen: str = "",
    claimed_by_me: bool = False,
    claim_expires_at: str = "",
) -> dict:
    """Structured buyer-facing proof for why an Elite lead is worth paying for."""
    age_days = _lead_age_days(lead_data, first_seen)
    evidence = []
    if gc_insight.get("source_url"):
        evidence.append({
            "label": "Fuente auditable",
            "value": gc_insight.get("source_label") or "Fuente oficial",
            "status": "verified" if gc_insight.get("confidence") == "verified" else "present",
        })
    if (lead_data.get("contact_phone") or "").strip():
        evidence.append({"label": "Contacto directo", "value": "Teléfono disponible", "status": "verified"})
    if lead_data.get("value_float"):
        try:
            value = f"${float(lead_data.get('value_float') or 0):,.0f}"
        except Exception:
            value = "Detectado"
        evidence.append({"label": "Valor del proyecto", "value": value, "status": "verified"})
    if inspection_date:
        evidence.append({"label": "Ventana de acción", "value": str(inspection_date)[:10], "status": "timely"})
    elif age_days is not None:
        evidence.append({"label": "Frescura", "value": f"{age_days} días", "status": "fresh" if age_days <= 45 else "aging"})
    if claimed_by_me:
        evidence.append({"label": "Exclusividad", "value": f"Reservado hasta {claim_expires_at[:10]}", "status": "reserved"})
    elif is_elite:
        evidence.append({"label": "Exclusividad", "value": "Disponible para reservar", "status": "available"})

    return {
        "certified": bool(is_elite),
        "quality_score": int(q_score or 0),
        "headline": "Certificado Elite" if is_elite else "Evidencia de calidad",
        "checks": q_checks[:5],
        "evidence": evidence[:6],
    }


def _is_elite_lead_record(row_dict: dict, lead_data: dict | None = None) -> tuple[bool, int, list[str]]:
    try:
        lead_data = lead_data if lead_data is not None else json.loads(row_dict.get("lead_data") or "{}")
    except Exception:
        lead_data = {}
    service_type = (row_dict.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
    if not service_type or not is_gc_interesting_lead(lead_data, service_type):
        return False, 0, []
    gc_insight = build_gc_insight(lead_data, service_type)
    if not gc_insight.get("source_url"):
        return False, 0, []
    scoring = lead_data.get("_scoring", {}) or {}
    inspection_date = (
        lead_data.get("inspection_date")
        or lead_data.get("next_inspection_date")
        or lead_data.get("next_scheduled_inspection_date")
        or ""
    )
    q_score, q_checks, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, str(inspection_date).strip()[:10], row_dict.get("first_seen", ""))
    return is_elite, q_score, q_checks


def _active_elite_claim(c, lead_id: str):
    c.execute("""
        SELECT user_id, expires_at
        FROM elite_lead_claims
        WHERE lead_id = ?
          AND status = 'active'
          AND datetime(expires_at) > datetime('now')
        LIMIT 1
    """, (lead_id,))
    return c.fetchone()


def _claim_elite_lead(conn, c, user_id: int, lead_id: str, claim_type: str = "like") -> dict:
    claim = _active_elite_claim(c, lead_id)
    if claim and int(claim["user_id"]) != int(user_id):
        return {"claimed": False, "blocked": True, "expires_at": claim["expires_at"]}

    expires_at = (datetime.utcnow() + timedelta(days=ELITE_CLAIM_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO elite_lead_claims (lead_id, user_id, claim_type, status, claimed_at, expires_at)
        VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP, ?)
        ON CONFLICT(lead_id) DO UPDATE SET
            user_id = excluded.user_id,
            claim_type = excluded.claim_type,
            status = 'active',
            claimed_at = CURRENT_TIMESTAMP,
            expires_at = excluded.expires_at
    """, (lead_id, int(user_id), claim_type, expires_at))
    conn.commit()
    return {"claimed": True, "blocked": False, "expires_at": expires_at}


def _elite_inventory_payload(city_filter: str = "", service_filter: str = "") -> dict:
    """Return non-sensitive inventory counts for selling/operating Elite."""
    conn = get_db_connection()
    c = conn.cursor()
    conditions = [
        build_public_real_lead_sql_filter(),
        build_gc_interest_sql_filter(),
    ]
    params: list = []
    if city_filter:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")
    if service_filter:
        cats = [x.strip().lower() for x in service_filter.split(",") if x.strip()]
        service_sql, service_params = build_service_category_filter(cats, _TRADE_SERVICE_TO_AI, _SERVICE_TYPE_CATS)
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = "WHERE " + " AND ".join(conditions)
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 2500
    """, params)
    rows = c.fetchall()
    conn.close()

    by_city: dict[str, int] = {}
    by_service: dict[str, int] = {}
    samples: list[dict] = []
    total = 0
    quality_sum = 0

    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type or not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            continue
        scoring = lead_data.get("_scoring", {}) or {}
        q_score, q_checks, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, "", rd.get("first_seen", ""))
        if not is_elite:
            continue
        total += 1
        quality_sum += q_score
        city = rd.get("city") or "Unknown"
        by_city[city] = by_city.get(city, 0) + 1
        by_service[service_type] = by_service.get(service_type, 0) + 1
        if len(samples) < 8:
            samples.append({
                "city": city,
                "service_type": service_type,
                "score": int(scoring.get("score") or 0),
                "quality_score": q_score,
                "checks": q_checks,
                "value": lead_data.get("value_float") or 0,
                "source_label": gc_insight.get("source_label", ""),
            })

    return {
        "total_elite_leads": total,
        "average_quality_score": round(quality_sum / total, 1) if total else 0,
        "by_city": dict(sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:25]),
        "by_service": dict(sorted(by_service.items(), key=lambda kv: (-kv[1], kv[0]))),
        "top_markets": [
            {"city": city, "elite_leads": count}
            for city, count in sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
        ],
        "samples": samples,
        "filters": {"city": city_filter, "service": service_filter},
    }


def _elite_quality_report_payload(city_filter: str = "", service_filter: str = "") -> dict:
    """Operational QA report for deciding whether Elite inventory is sellable."""
    conn = get_db_connection()
    c = conn.cursor()
    conditions = [
        build_public_real_lead_sql_filter(),
        build_gc_interest_sql_filter(),
    ]
    params: list = []
    if city_filter:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")
    if service_filter:
        cats = [x.strip().lower() for x in service_filter.split(",") if x.strip()]
        service_sql, service_params = build_service_category_filter(cats, _TRADE_SERVICE_TO_AI, _SERVICE_TYPE_CATS)
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = "WHERE " + " AND ".join(conditions)
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 2500
    """, params)
    rows = c.fetchall()
    conn.close()

    report = {
        "filters": {"city": city_filter, "service": service_filter},
        "candidate_leads": 0,
        "elite_leads": 0,
        "average_quality_score": 0,
        "coverage": {
            "official_source": 0,
            "phone": 0,
            "project_value": 0,
            "hot_score_90": 0,
            "inspection_window": 0,
            "fresh_signal": 0,
        },
        "rejection_reasons": {
            "not_gc_relevant": 0,
            "missing_source_url": 0,
            "below_elite_threshold": 0,
            "invalid_json": 0,
        },
        "top_markets": [],
        "top_services": [],
        "audit_samples": [],
        "sellability": "needs_inventory",
        "alerts": [],
    }
    by_city: dict[str, int] = {}
    by_service: dict[str, int] = {}
    quality_sum = 0

    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            report["rejection_reasons"]["invalid_json"] += 1
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type or not is_gc_interesting_lead(lead_data, service_type):
            report["rejection_reasons"]["not_gc_relevant"] += 1
            continue
        report["candidate_leads"] += 1
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            report["rejection_reasons"]["missing_source_url"] += 1
            continue
        scoring = lead_data.get("_scoring", {}) or {}
        inspection_date = (lead_data.get("inspection_date") or lead_data.get("next_inspection_date") or "").strip()[:10]
        q_score, q_checks, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, inspection_date, rd.get("first_seen", ""))
        if not is_elite:
            report["rejection_reasons"]["below_elite_threshold"] += 1
            continue

        report["elite_leads"] += 1
        quality_sum += q_score
        city = rd.get("city") or "Unknown"
        by_city[city] = by_city.get(city, 0) + 1
        by_service[service_type] = by_service.get(service_type, 0) + 1
        if gc_insight.get("source_url"):
            report["coverage"]["official_source"] += 1
        if (lead_data.get("contact_phone") or "").strip():
            report["coverage"]["phone"] += 1
        if lead_data.get("value_float"):
            report["coverage"]["project_value"] += 1
        if int(scoring.get("score") or 0) >= 90:
            report["coverage"]["hot_score_90"] += 1
        if inspection_date:
            report["coverage"]["inspection_window"] += 1
        report["coverage"]["fresh_signal"] += 1
        if len(report["audit_samples"]) < 12:
            report["audit_samples"].append({
                "lead_id": rd.get("address_key"),
                "city": city,
                "service_type": service_type,
                "score": int(scoring.get("score") or 0),
                "quality_score": q_score,
                "checks": q_checks,
                "source_label": gc_insight.get("source_label", ""),
                "first_seen": rd.get("first_seen", ""),
                "has_phone": bool((lead_data.get("contact_phone") or "").strip()),
                "has_value": bool(lead_data.get("value_float")),
            })

    elite_total = report["elite_leads"]
    if elite_total:
        report["average_quality_score"] = round(quality_sum / elite_total, 1)
        for key, value in list(report["coverage"].items()):
            report["coverage"][key] = {
                "count": value,
                "pct": round(value * 100 / elite_total, 1),
            }
    report["top_markets"] = [
        {"city": city, "elite_leads": count}
        for city, count in sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]
    report["top_services"] = [
        {"service_type": service_type, "elite_leads": count}
        for service_type, count in sorted(by_service.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    ]

    if elite_total >= 50 and report["average_quality_score"] >= 80:
        report["sellability"] = "ready_for_elite"
    elif elite_total >= 15:
        report["sellability"] = "pilot_market"
    if elite_total < 15:
        report["alerts"].append("Menos de 15 leads Elite: vender como piloto o ampliar mercado.")
    if elite_total and isinstance(report["coverage"]["phone"], dict) and report["coverage"]["phone"]["pct"] < 70:
        report["alerts"].append("Cobertura de teléfono bajo 70%: reforzar enriquecimiento antes de vender a $500.")
    if elite_total and isinstance(report["coverage"]["project_value"], dict) and report["coverage"]["project_value"]["pct"] < 50:
        report["alerts"].append("Valor de proyecto bajo 50%: mostrar ROI puede ser más difícil.")

    return report


def _elite_market_action_plan(
    elite: int,
    avg_quality: float,
    phone_pct: float,
    value_pct: float,
    hot_pct: float,
    candidate_leads: int,
    status: str,
) -> dict:
    """Return sales-ops actions needed to make a market sellable at Elite price."""
    ready_elite_min = 50
    ready_quality_min = 80
    ready_phone_min = 90
    pilot_elite_min = 15
    pilot_phone_min = 80

    gap_to_elite = {
        "elite_leads": max(ready_elite_min - elite, 0),
        "average_quality_score": max(round(ready_quality_min - avg_quality, 1), 0),
        "phone_pct": max(round(ready_phone_min - phone_pct, 1), 0),
        "project_value_pct": max(round(50 - value_pct, 1), 0),
        "hot_score_pct": max(round(70 - hot_pct, 1), 0),
    }
    next_actions: list[str] = []
    if gap_to_elite["elite_leads"]:
        next_actions.append(
            f"Add {gap_to_elite['elite_leads']} more Elite-qualified leads from homeowner intake, permits and storm signals."
        )
    if gap_to_elite["phone_pct"]:
        next_actions.append(
            f"Enrich/verify phone coverage by {gap_to_elite['phone_pct']} pts before selling at $500/month."
        )
    if gap_to_elite["average_quality_score"]:
        next_actions.append(
            f"Raise average quality by {gap_to_elite['average_quality_score']} pts with fresher source, value and contact evidence."
        )
    if gap_to_elite["project_value_pct"]:
        next_actions.append(
            f"Capture project value/budget for {gap_to_elite['project_value_pct']} pts more leads to make ROI proof stronger."
        )
    if not next_actions:
        next_actions.append("Ready for $500/month Elite positioning; monitor reports and replacement credits weekly.")

    if status == "ready_for_elite":
        priority = "sell_now"
    elif elite >= pilot_elite_min and phone_pct >= pilot_phone_min:
        priority = "pilot_and_enrich"
    elif candidate_leads >= ready_elite_min:
        priority = "enrich_existing_inventory"
    else:
        priority = "source_more_inventory"

    return {
        "priority": priority,
        "gap_to_elite": gap_to_elite,
        "next_actions": next_actions[:4],
    }


def _elite_market_readiness_payload(city_filter: str = "", service_filter: str = "") -> dict:
    """Public-safe market readiness summary for selling Elite subscriptions."""
    conn = get_db_connection()
    c = conn.cursor()
    conditions = [
        build_public_real_lead_sql_filter(),
        build_gc_interest_sql_filter(),
    ]
    params: list = []
    if city_filter:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")
    if service_filter:
        cats = [x.strip().lower() for x in service_filter.split(",") if x.strip()]
        service_sql, service_params = build_service_category_filter(cats, _TRADE_SERVICE_TO_AI, _SERVICE_TYPE_CATS)
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = "WHERE " + " AND ".join(conditions)
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 5000
    """, params)
    rows = c.fetchall()
    conn.close()

    markets: dict[str, dict] = {}
    total_candidates = 0
    total_elite = 0
    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type or not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            continue
        total_candidates += 1
        city = rd.get("city") or "Unknown"
        market = markets.setdefault(city, {
            "city": city,
            "candidate_leads": 0,
            "elite_leads": 0,
            "quality_sum": 0,
            "phone_count": 0,
            "value_count": 0,
            "hot_count": 0,
            "services": {},
        })
        market["candidate_leads"] += 1
        scoring = lead_data.get("_scoring", {}) or {}
        q_score, _, is_elite = _premium_quality(
            lead_data,
            gc_insight,
            service_type,
            scoring,
            str(lead_data.get("inspection_date") or lead_data.get("next_inspection_date") or "")[:10],
            rd.get("first_seen", ""),
        )
        if not is_elite:
            continue
        total_elite += 1
        market["elite_leads"] += 1
        market["quality_sum"] += q_score
        market["services"][service_type] = market["services"].get(service_type, 0) + 1
        if (lead_data.get("contact_phone") or "").strip():
            market["phone_count"] += 1
        if lead_data.get("value_float"):
            market["value_count"] += 1
        if int(scoring.get("score") or 0) >= 90:
            market["hot_count"] += 1

    readiness = []
    for city, market in markets.items():
        elite = int(market["elite_leads"])
        avg_quality = round(market["quality_sum"] / elite, 1) if elite else 0
        phone_pct = round(market["phone_count"] * 100 / elite, 1) if elite else 0
        value_pct = round(market["value_count"] * 100 / elite, 1) if elite else 0
        hot_pct = round(market["hot_count"] * 100 / elite, 1) if elite else 0
        if elite >= 50 and avg_quality >= 80 and phone_pct >= 90:
            status = "ready_for_elite"
            recommended_price = 500
        elif elite >= 15 and phone_pct >= 80:
            status = "pilot_market"
            recommended_price = 250
        else:
            status = "needs_inventory"
            recommended_price = 0
        action_plan = _elite_market_action_plan(
            elite,
            avg_quality,
            phone_pct,
            value_pct,
            hot_pct,
            int(market["candidate_leads"]),
            status,
        )
        readiness.append({
            "city": city,
            "status": status,
            "recommended_price": recommended_price,
            "candidate_leads": int(market["candidate_leads"]),
            "elite_leads": elite,
            "average_quality_score": avg_quality,
            "priority": action_plan["priority"],
            "gap_to_elite": action_plan["gap_to_elite"],
            "next_actions": action_plan["next_actions"],
            "coverage": {
                "phone_pct": phone_pct,
                "project_value_pct": value_pct,
                "hot_score_pct": hot_pct,
                "fresh_signal_pct": 100 if elite else 0,
            },
            "top_services": [
                {"service_type": service, "elite_leads": count}
                for service, count in sorted(market["services"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            ],
        })

    readiness.sort(key=lambda m: (m["status"] != "ready_for_elite", m["status"] != "pilot_market", -m["elite_leads"], m["city"]))
    summary = {
        "ready_markets": sum(1 for m in readiness if m["status"] == "ready_for_elite"),
        "pilot_markets": sum(1 for m in readiness if m["status"] == "pilot_market"),
        "needs_inventory_markets": sum(1 for m in readiness if m["status"] == "needs_inventory"),
        "total_candidate_leads": total_candidates,
        "total_elite_leads": total_elite,
    }
    return {
        "summary": summary,
        "markets": readiness[:50],
        "filters": {"city": city_filter, "service": service_filter},
        "thresholds": {
            "ready_for_elite": {"elite_leads": 50, "average_quality_score": 80, "phone_pct": 90, "price": 500},
            "pilot_market": {"elite_leads": 15, "phone_pct": 80, "price": 250},
        },
    }


def _elite_uplift_missing_requirements(lead_data: dict, gc_insight: dict, service_type: str, scoring: dict, inspection_date: str, first_seen: str) -> list[str]:
    """Explain what a near-Elite lead still needs before it can be sold."""
    missing: list[str] = []
    score = int(scoring.get("score") or 0)
    has_phone = bool((lead_data.get("contact_phone") or "").strip())
    has_value = bool(lead_data.get("value_float"))
    has_action_window = bool(inspection_date)
    has_direct_owner_intent = str(lead_data.get("_lead_channel") or "") == "homeowner_intake"
    age_days = _lead_age_days(lead_data, first_seen)
    fresh_limit_days = 21 if str(service_type or "").lower() in {"weather", "flood", "disaster"} else 45
    has_recent_signal = has_direct_owner_intent or has_action_window or (age_days is not None and age_days <= fresh_limit_days)

    if not gc_insight.get("source_url"):
        missing.append("official_source_url")
    if gc_insight.get("confidence") != "verified":
        missing.append("verified_source_confidence")
    if not has_phone:
        missing.append("phone")
    if score < 85:
        missing.append("score_85_plus")
    if not (has_value or has_action_window or has_direct_owner_intent):
        missing.append("project_value_or_action_window")
    if not has_recent_signal:
        missing.append("fresh_signal")
    return missing


def _elite_uplift_candidates_payload(city_filter: str = "", service_filter: str = "", limit: int = 30) -> dict:
    """Admin queue of non-Elite leads closest to becoming sellable Elite inventory."""
    limit = max(1, min(int(limit or 30), 100))
    conn = get_db_connection()
    c = conn.cursor()
    conditions = [
        build_public_real_lead_sql_filter(),
        build_gc_interest_sql_filter(),
    ]
    params: list = []
    if city_filter:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")
    if service_filter:
        cats = [x.strip().lower() for x in service_filter.split(",") if x.strip()]
        service_sql, service_params = build_service_category_filter(cats, _TRADE_SERVICE_TO_AI, _SERVICE_TYPE_CATS)
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = "WHERE " + " AND ".join(conditions)
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 5000
    """, params)
    rows = c.fetchall()
    conn.close()

    candidates: list[dict] = []
    missing_counts: dict[str, int] = {}
    scanned = 0
    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type or not is_gc_interesting_lead(lead_data, service_type):
            continue
        scanned += 1
        gc_insight = build_gc_insight(lead_data, service_type)
        scoring = lead_data.get("_scoring", {}) or {}
        inspection_date = str(lead_data.get("inspection_date") or lead_data.get("next_inspection_date") or "").strip()[:10]
        q_score, q_checks, is_elite = _premium_quality(
            lead_data,
            gc_insight,
            service_type,
            scoring,
            inspection_date,
            rd.get("first_seen", ""),
        )
        if is_elite:
            continue
        missing = _elite_uplift_missing_requirements(lead_data, gc_insight, service_type, scoring, inspection_date, rd.get("first_seen", ""))
        if not missing:
            missing = ["quality_score_70_plus"]
        for item in missing:
            missing_counts[item] = missing_counts.get(item, 0) + 1
        uplift_score = q_score - (len(missing) * 6)
        candidates.append({
            "lead_id": rd.get("address_key"),
            "address": rd.get("address") or rd.get("address_key") or "",
            "city": rd.get("city") or "Unknown",
            "service_type": service_type,
            "quality_score": q_score,
            "uplift_score": uplift_score,
            "score": int(scoring.get("score") or 0),
            "missing_requirements": missing,
            "next_action": _elite_uplift_next_action(missing),
            "source_label": gc_insight.get("source_label", ""),
            "source_url": gc_insight.get("source_url", ""),
            "has_phone": bool((lead_data.get("contact_phone") or "").strip()),
            "value": lead_data.get("value_float") or 0,
            "first_seen": rd.get("first_seen", ""),
            "checks": q_checks,
        })

    candidates.sort(key=lambda item: (-int(item["uplift_score"]), len(item["missing_requirements"]), item["city"], item["lead_id"]))
    return {
        "filters": {"city": city_filter, "service": service_filter},
        "scanned_candidates": scanned,
        "returned": min(len(candidates), limit),
        "missing_counts": dict(sorted(missing_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "candidates": candidates[:limit],
    }


def _elite_uplift_next_action(missing: list[str]) -> str:
    if "phone" in missing:
        return "Verify owner/decision-maker phone before selling as Elite."
    if "official_source_url" in missing or "verified_source_confidence" in missing:
        return "Attach an auditable public source URL and mark source confidence verified."
    if "fresh_signal" in missing:
        return "Refresh permit, inspection, storm or homeowner signal before certification."
    if "project_value_or_action_window" in missing:
        return "Capture budget, permit value, inspection window or homeowner intent."
    if "score_85_plus" in missing:
        return "Enrich project context until lead score reaches 85+."
    return "Review manually; lead is close to Elite but below quality threshold."


def _elite_sales_proof_payload(city_filter: str = "", service_filter: str = "") -> dict:
    """Public-safe proof points for explaining Elite pricing to contractors."""
    readiness = _elite_market_readiness_payload(city_filter, service_filter)
    markets = readiness.get("markets") or []
    target = None
    for market in markets:
        if market.get("status") == "ready_for_elite":
            target = market
            break
    if target is None and markets:
        target = markets[0]

    if not target:
        return {
            "status": "needs_inventory",
            "recommended_price": 0,
            "headline": "Elite no está listo para vender en este mercado todavía.",
            "proof_points": ["Aumentar inventario y cobertura de teléfono antes de vender."],
            "market": None,
            "readiness": readiness,
        }

    price = int(target.get("recommended_price") or 0)
    elite_count = int(target.get("elite_leads") or 0)
    coverage = target.get("coverage") or {}
    avg_quality = float(target.get("average_quality_score") or 0)
    avg_project_value = 0
    try:
        inventory = _elite_inventory_payload(target.get("city") or city_filter, service_filter)
        sample_values = [
            float(sample.get("value") or 0)
            for sample in inventory.get("samples", [])
            if float(sample.get("value") or 0) > 0
        ]
        avg_project_value = round(sum(sample_values) / len(sample_values)) if sample_values else 0
    except Exception:
        inventory = {}

    conservative_close_rate = 0.05
    expected_jobs = round(elite_count * conservative_close_rate, 1)
    estimated_pipeline_value = round(avg_project_value * expected_jobs) if avg_project_value else 0
    break_even_months = round(avg_project_value / price, 1) if price and avg_project_value else 0
    headline = (
        f"{target.get('city')} está listo para Elite a ${price}/mes."
        if target.get("status") == "ready_for_elite"
        else f"{target.get('city')} conviene venderlo como piloto antes de Elite."
    )
    proof_points = [
        f"{elite_count} leads Elite disponibles con calidad promedio {avg_quality}/100.",
        f"{coverage.get('phone_pct', 0)}% con teléfono y {coverage.get('project_value_pct', 0)}% con valor de proyecto.",
        f"Señales frescas: {coverage.get('fresh_signal_pct', 0)}% del inventario Elite.",
    ]
    if avg_project_value:
        proof_points.append(
            f"Valor promedio de muestra: ${avg_project_value:,.0f}; un cierre puede cubrir {break_even_months} meses de Elite."
        )
    if estimated_pipeline_value:
        proof_points.append(
            f"Con cierre conservador de 5%, pipeline estimado: ${estimated_pipeline_value:,.0f}."
        )

    return {
        "status": target.get("status"),
        "recommended_price": price,
        "headline": headline,
        "proof_points": proof_points,
        "roi": {
            "average_sample_project_value": avg_project_value,
            "conservative_close_rate": conservative_close_rate,
            "estimated_jobs": expected_jobs,
            "estimated_pipeline_value": estimated_pipeline_value,
            "break_even_months_per_close": break_even_months,
        },
        "market": target,
        "readiness": readiness,
        "inventory_sample_count": len((inventory or {}).get("samples", [])),
    }


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
    # Keywords are matched against description, permit_type, and AI _trade fields only
    "roofing":     ["roof", "roofing", "re-roof", "reroof", "shingle", "tile roof", "torch down", "tpo", "flat roof", "gutter", "fascia", "reroofing"],
    "drywall":     ["drywall", "sheetrock", "gypsum board", "taping", "texturing", "wallboard", "wall board", "partition wall", "plaster", "drywall repair", "interior wall", "drywall install"],
    "paint":       ["paint", "painting", "repaint", "painter", "stucco", "primer", "exterior paint", "interior paint", "paint coat"],
    "electrical":  ["electrical", "panel upgrade", "ev charger", "200 amp", "rewire", "sub panel", "wiring", "low voltage", "circuit breaker", "outlet install", "service upgrade", "electric", "meter socket"],
    "plumbing":    ["plumb", "sewer", "water heater", "gas line", "backflow", "repipe", "water main", "gas pipe", "water pipe", "drain line", "water line"],
    "hvac":        ["hvac", "heating", "cooling", "air condition", "furnace", "ductwork", "mini split", "heat pump", "ventilation", "ac unit", "air handler", "mechanical"],
    "flooring":    ["flooring", "hardwood floor", "tile floor", "floor tile", "laminate", "vinyl plank", "carpet", "epoxy floor", "subfloor", "ceramic tile", "floor install", "wood floor"],
    "concrete":    ["concrete", "cement", "foundation", "slab", "sidewalk", "driveway", "flatwork", "footing", "stem wall", "curb", "concrete work", "slab on grade"],
    "framing":     ["framing", "structural", "load bearing", "beam", "joist", "truss", "stud wall", "rough framing", "wood frame", "framer", "timber"],
    "windows":     ["window", "window install", "window replace", "sliding door", "patio door", "skylight", "glass replacement", "glazing", "storefront", "french door", "fenestration", "door install", "door replace"],
    "landscaping": ["landscap", "hardscape", "irrigation", "sprinkler", "retaining wall", "paver", "turf", "grading", "tree removal", "landscape install"],
    "remodel":     ["remodel", "renovation", "kitchen remodel", "bathroom remodel", "addition", "adu", "accessory dwelling", "tenant improvement", "interior alteration", "room addition"],
}
# These map directly to primary_service_type column
_SERVICE_TYPE_CATS = {"solar", "permits", "construction", "realestate", "flood", "weather", "disaster", "energy", "rodents", "deconstruction", "remodel", "crossdata"}

# Subcontractor categories must use the post-classification opportunity trade,
# not raw permit keywords. Example: a REROOF permit pulled by a CCC roofer is
# reclassified to DRYWALL/PAINT/INSULATION; it must not appear for roofing users
# just because the description still says "REROOF".
_TRADE_SERVICE_TO_AI = {
    "roofing": "ROOFING",
    "drywall": "DRYWALL",
    "paint": "PAINTING",
    "electrical": "ELECTRICAL",
    "plumbing": "PLUMBING",
    "hvac": "HVAC",
    "flooring": "FLOORING",
    "concrete": "CONCRETE",
    "framing": "FRAMING",
    "windows": "WINDOWS",
    "landscaping": "LANDSCAPING",
    "deconstruction": "DEMOLITION",
    "insulation": "INSULATION",
}

_SWIPE_FILTER_CATEGORY_LABELS = {
    "weather": "Daño por tormenta",
    "permits": "Permisos listos",
    "construction": "Proyecto sin GC confirmado",
    "remodel": "Remodelación / reparación",
    "deconstruction": "Demolición / rebuild",
    "realestate": "Venta de propiedad",
    "crossdata": "Cross-data verificado",
}

_SWIPE_FILTER_SERVICE_ALIASES = {
    "weather": {"weather", "flood", "disaster"},
    "permits": {"permits"},
    "construction": {"construction"},
    "remodel": {"remodel"},
    "deconstruction": {"deconstruction"},
    "realestate": {"realestate"},
    "crossdata": {"crossdata"},
}


def _service_count_keys(service_type: str) -> list[str]:
    service = (service_type or "").strip().lower()
    keys = [service] if service else []
    for category, aliases in _SWIPE_FILTER_SERVICE_ALIASES.items():
        if service in aliases and category not in keys:
            keys.append(category)
    return keys


def _add_service_count(counts: dict[str, int], service_type: str) -> None:
    for key in _service_count_keys(service_type):
        counts[key] = counts.get(key, 0) + 1


def _matches_city_radius(row_dict: dict, lead_data: dict, city_filter: str, radius_miles: float) -> bool:
    city_filter = (city_filter or "").strip()
    if not city_filter:
        return True
    lead_city = (row_dict.get("city") or lead_data.get("city") or "").strip()
    if radius_miles <= 0:
        return city_filter.lower() in lead_city.lower()

    origin = _city_coords(city_filter)
    if not origin:
        return city_filter.lower() in lead_city.lower()

    lead_lat = lead_data.get("lat")
    lead_lon = lead_data.get("lon")
    try:
        if lead_lat and lead_lon:
            return _haversine_miles(origin[0], origin[1], float(lead_lat), float(lead_lon)) <= radius_miles
    except (TypeError, ValueError):
        pass

    lead_coords = _city_coords(lead_city)
    if lead_coords:
        return _haversine_miles(origin[0], origin[1], lead_coords[0], lead_coords[1]) <= radius_miles
    return city_filter.lower() in lead_city.lower()


def _parse_swipe_filter_args(args) -> dict:
    hot_only = args.get("hot_only", "0") == "1"
    try:
        min_score = int(args.get("min_score", 0))
    except (TypeError, ValueError):
        min_score = 0
    if hot_only:
        min_score = max(min_score, 90)
    try:
        min_value = float(args.get("min_value", 0))
    except (TypeError, ValueError):
        min_value = 0.0
    try:
        max_value = float(args.get("max_value", 0))
    except (TypeError, ValueError):
        max_value = 0.0
    try:
        radius_miles = float(args.get("radius_miles", 0))
    except (TypeError, ValueError):
        radius_miles = 0.0
    return {
        "hot_only": hot_only,
        "min_score": min_score,
        "min_value": min_value,
        "max_value": max_value,
        "city": (args.get("city") or "").strip(),
        "radius_miles": radius_miles,
        "elite_only": args.get("elite_only", "0") == "1",
    }


def _swipe_filter_options_payload(args) -> dict:
    filters = _parse_swipe_filter_args(args)
    city_filter = filters["city"]
    radius_miles = filters["radius_miles"]
    do_radius = bool(city_filter and radius_miles > 0)

    conditions = [
        "(has_phone = 1 OR primary_service_type IN ('weather', 'flood', 'disaster'))",
        "COALESCE(is_dead_lead, 0) = 0",
        build_public_real_lead_sql_filter(),
        build_gc_interest_sql_filter(),
    ]
    params: list = []

    if filters["min_score"] > 0:
        conditions.append("CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) >= ?")
        params.append(filters["min_score"])
    if filters["min_value"] > 0:
        conditions.append(
            "(primary_service_type IN ('weather', 'flood', 'disaster') OR "
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) >= ?)"
        )
        params.append(filters["min_value"])
    if filters["max_value"] > 0:
        conditions.append("CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) <= ?")
        params.append(filters["max_value"])
    if city_filter and not do_radius:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")

    where_sql = "WHERE " + " AND ".join(conditions)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(f"""
        SELECT address_key, address, city, lead_data, primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY first_seen DESC
        LIMIT 5000
    """, params)
    rows = c.fetchall()
    conn.close()

    service_counts: dict[str, int] = {}
    raw_service_counts: dict[str, int] = {}
    by_city: dict[str, int] = {}
    score_buckets = {"hot_90": 0, "warm_70": 0, "all": 0}
    value_buckets = {"under_100k": 0, "100k_500k": 0, "over_500k": 0, "unknown": 0}
    total = 0

    for row in rows:
        rd = dict(row)
        try:
            lead_data = json.loads(rd.get("lead_data") or "{}")
        except Exception:
            continue
        if is_placeholder_or_demo_lead(lead_data, rd.get("address_key")):
            continue
        service_type = (rd.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if not service_type:
            continue
        if service_type not in {"weather", "flood", "disaster"} and not (lead_data.get("contact_phone") or lead_data.get("phone")):
            continue
        if not _matches_city_radius(rd, lead_data, city_filter, radius_miles):
            continue
        if not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        if not gc_insight.get("source_url"):
            continue

        scoring = lead_data.get("_scoring", {}) or {}
        q_score, _, is_elite = _premium_quality(lead_data, gc_insight, service_type, scoring, "", rd.get("first_seen", ""))
        if filters["elite_only"] and not is_elite:
            continue

        total += 1
        raw_service_counts[service_type] = raw_service_counts.get(service_type, 0) + 1
        _add_service_count(service_counts, service_type)
        city = rd.get("city") or "Unknown"
        by_city[city] = by_city.get(city, 0) + 1

        score = int(scoring.get("score") or 0)
        score_buckets["all"] += 1
        if score >= 90:
            score_buckets["hot_90"] += 1
        if score >= 70:
            score_buckets["warm_70"] += 1

        value = float(lead_data.get("value_float") or 0)
        if value <= 0:
            value_buckets["unknown"] += 1
        elif value < 100000:
            value_buckets["under_100k"] += 1
        elif value <= 500000:
            value_buckets["100k_500k"] += 1
        else:
            value_buckets["over_500k"] += 1

    categories = [
        {
            "id": category,
            "label": label,
            "count": int(service_counts.get(category, 0)),
            "available": int(service_counts.get(category, 0)) > 0,
        }
        for category, label in _SWIPE_FILTER_CATEGORY_LABELS.items()
    ]

    return {
        "total_available": total,
        "available_service_counts": service_counts,
        "raw_service_counts": raw_service_counts,
        "filter_categories": categories,
        "available_service_types": sorted(k for k, v in service_counts.items() if v > 0),
        "top_cities": [
            {"city": city, "count": count}
            for city, count in sorted(by_city.items(), key=lambda kv: (-kv[1], kv[0]))[:25]
        ],
        "score_buckets": score_buckets,
        "value_buckets": value_buckets,
        "filters": filters,
    }


@app.route('/api/swipe/filter-options', methods=['GET'])
@limiter.limit("60 per minute")
def swipe_filter_options():
    """Return live inventory counts for the Swipe filter drawer."""
    return jsonify(_swipe_filter_options_payload(request.args)), 200


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
      - service_cats:  comma-separated GC opportunity categories:
                       weather/flood, permits, remodel, deconstruction, realestate,
                       crossdata, construction (construction is shown only when
                       pre-award / no GC is confirmed)

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
    elite_only = request.args.get("elite_only", "0") == "1"

    # Pre-compute origin coords for radius filtering
    origin_coords = _city_coords(city_filter) if (city_filter and radius_miles > 0) else None
    do_radius = origin_coords is not None or (city_filter and radius_miles > 0)

    already_swiped = _already_swiped_ids(user_id, anon_id)
    swipes_count = len(already_swiped)

    remaining = None
    if not user_id:
        if not anon_id:
            return jsonify({"error": "anon_id required for anonymous browsing"}), 400
        if elite_only:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "register",
                "required_tier": "elite",
                "anon_limit":   ANON_LEAD_LIMIT,
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200

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
        is_paid, subscription_tier = _get_web_subscription(user_id)
        if elite_only and subscription_tier != "elite":
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "required_tier": "elite",
                "swipes_count": swipes_count,
                "remaining":    0,
            }), 200
        if subscription_tier == "elite":
            elite_only = True
        tier_limit = _tier_lead_limit(subscription_tier, is_paid)
        replacement_credits = _elite_replacement_credit_count(user_id) if subscription_tier == "elite" else 0
        billable_swipes_count = max(swipes_count - replacement_credits, 0)
        if tier_limit is not None and billable_swipes_count >= tier_limit:
            return jsonify({
                "leads":        [],
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   FREE_USER_LEAD_LIMIT,
                "tier_limit":   tier_limit,
                "tier":         subscription_tier,
                "swipes_count": swipes_count,
                "billable_swipes_count": billable_swipes_count,
                "replacement_credits": replacement_credits,
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
        # Storm/disaster leads are verified impact-zone/property candidates. Many
        # do not have a project value yet, so the default permit-value floor must
        # not hide them or make the storm-damage pill look unavailable.
        conditions.append(
            "(primary_service_type IN ('weather', 'flood', 'disaster') OR "
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) >= ?)"
        )
        params.append(min_value)

    if max_value > 0:
        conditions.append(
            "CAST(COALESCE(json_extract(lead_data, '$.value_float'), 0) AS REAL) <= ?"
        )
        params.append(max_value)

    # Phone filter — direct-contact leads need phone, but storm/disaster leads
    # are sold as verified impact-zone/property candidates before owner-contact
    # enrichment. Do not fabricate phones just to make them visible.
    conditions.append("(has_phone = 1 OR primary_service_type IN ('weather', 'flood', 'disaster'))")

    # Dead-lead filter — exclude GC self-pull leads (contractor pulling own permit).
    # is_dead_lead is a pre-computed indexed column set by gc_detector.py via base.py.
    conditions.append("COALESCE(is_dead_lead, 0) = 0")

    # Public integrity filter — never show synthetic/demo/placeholders in the
    # public feed. Python validation below is authoritative for stale rows.
    conditions.append(build_public_real_lead_sql_filter())

    # GC buyer-intent filter — the public swipe feed is now GC-only. Exclude
    # already-awarded jobs and non-GC opportunity types before fetching a pool.
    conditions.append(build_gc_interest_sql_filter())

    # City filter: without radius → simple LIKE; with radius → post-process
    if city_filter and not do_radius:
        conditions.append("LOWER(city) LIKE LOWER(?)")
        params.append(f"%{city_filter}%")

    # Capture the verified, GC-eligible inventory before applying the selected
    # service filter so the UI can disable zero-inventory filter pills instead of
    # letting users get trapped in an empty state.
    availability_conditions = list(conditions)
    availability_params = list(params)

    # ── Service category filter ────────────────────────────────────────────────
    # For subcontractor trades, match the CURRENT opportunity trade only.
    # Do not match raw description/permit_type here: a taken REROOF permit still
    # says "roof" in the description, but after self-pull detection it is no
    # longer a roofing opportunity.
    if selected_cats:
        service_sql, service_params = build_service_category_filter(
            selected_cats, _TRADE_SERVICE_TO_AI, _SERVICE_TYPE_CATS
        )
        if service_sql:
            conditions.append(service_sql)
            params.extend(service_params)

    where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    # Fetch a large pool for diversity, then apply city round-robin
    # This prevents any single city from monopolizing the feed
    fetch_limit = max(limit * 50, 500) if not do_radius else limit * 20

    query = f"""
        SELECT address_key, address, city, agent_sources, lead_data,
               primary_service_type, first_seen
        FROM consolidated_leads
        {where_sql}
        ORDER BY CAST(json_extract(lead_data, '$._scoring.score') AS INTEGER) / 25 DESC,
                 RANDOM()
        LIMIT ?
    """
    params.append(fetch_limit)
    c.execute(query, params)
    raw_rows = c.fetchall()

    # ── City round-robin: pick from each city in rotation ─────────────────────
    # Groups leads by city and interleaves them so no single city dominates.
    # Within each city, leads are already ordered best-first (score/25 DESC).
    if not city_filter and not do_radius and len(raw_rows) > limit:
        from collections import defaultdict
        city_buckets: dict = defaultdict(list)
        for r in raw_rows:
            city_buckets[dict(r).get("city", "")].append(r)
        # Sort cities by number of leads (more leads = more variety)
        ordered_cities = sorted(city_buckets.keys(), key=lambda c: -len(city_buckets[c]))
        diversified = []
        i = 0
        while len(diversified) < fetch_limit and any(city_buckets.values()):
            city = ordered_cities[i % len(ordered_cities)]
            if city_buckets[city]:
                diversified.append(city_buckets[city].pop(0))
            i += 1
            if i > len(ordered_cities) * 1000:
                break
        rows = diversified
    else:
        rows = list(raw_rows)

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

    available_service_counts: dict[str, int] = {}
    try:
        availability_where = (
            "WHERE " + " AND ".join(availability_conditions)
            if availability_conditions else ""
        )
        c.execute(f"""
            SELECT primary_service_type, lead_data
            FROM consolidated_leads
            {availability_where}
            LIMIT 1000
        """, availability_params)
        for ar in c.fetchall():
            ard = dict(ar)
            try:
                ald = json.loads(ard.get("lead_data") or "{}")
            except Exception:
                ald = {}
            ast = (ard.get("primary_service_type") or ald.get("primary_service_type") or "").strip().lower()
            if not ast:
                continue
            if is_placeholder_or_demo_lead(ald):
                continue
            if ast not in {"weather", "flood", "disaster"} and not (ald.get("contact_phone") or ald.get("phone")):
                continue
            if not _matches_city_radius(ard, ald, city_filter, radius_miles):
                continue
            if not is_gc_interesting_lead(ald, ast):
                continue
            if not build_gc_insight(ald, ast).get("source_url"):
                continue
            _add_service_count(available_service_counts, ast)
    except Exception as ae:
        logger.debug(f"Availability lookup failed: {ae}")

    leads = []
    for row in rows:
        row_dict = dict(row)
        try:
            lead_data = json.loads(row_dict.get("lead_data") or "{}")
        except Exception:
            lead_data = {}

        # ── Public data integrity guard ───────────────────────────────────────
        # Skip synthetic/demo placeholders even if they are already in a local DB.
        if is_placeholder_or_demo_lead(lead_data, row_dict.get("address_key")):
            continue

        # ── Phone guard (belt-and-suspenders after DB filter) ─────────────────
        # Direct-contact leads need a phone. Storm/disaster rows are allowed as
        # impact-zone/property candidates without fabricated contact data.
        phone_check = (lead_data.get("contact_phone") or "").strip()
        service_type_for_phone = (row_dict.get("primary_service_type") or lead_data.get("primary_service_type") or "").strip().lower()
        if service_type_for_phone not in {"weather", "flood", "disaster"} and not phone_check:
            continue

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
        if not is_gc_interesting_lead(lead_data, service_type):
            continue
        gc_insight = build_gc_insight(lead_data, service_type)
        # User-facing swipe leads must be independently verifiable. If we cannot
        # build or store a working official source URL, do not show the row.
        if not gc_insight.get("source_url"):
            continue
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
        state       = (lead_data.get("state") or lead_data.get("property_state") or lead_data.get("site_state") or "").strip()
        zip_code    = (lead_data.get("zip") or lead_data.get("zipcode") or lead_data.get("postal_code") or lead_data.get("site_zip") or "").strip()

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
        premium_quality_score, premium_quality_checks, is_elite_quality = _premium_quality(
            lead_data, gc_insight, service_type, scoring, inspection_date, row_dict.get("first_seen", "")
        )
        if elite_only and not is_elite_quality:
            continue
        elite_claimed_by_me = False
        elite_claim_expires_at = ""
        if is_elite_quality:
            claim = _active_elite_claim(c, row_dict["address_key"])
            if claim:
                if not user_id or int(claim["user_id"]) != int(user_id):
                    continue
                elite_claimed_by_me = True
                elite_claim_expires_at = claim["expires_at"] or ""

        leads.append({
            "id":               row_dict["address_key"],
            "address":          row_dict["address"],
            "city":             row_dict["city"],
            "state":            state,
            "zip":              zip_code,
            "description":      desc,
            "value":            lead_data.get("value_float") or 0,
            "score":            scoring.get("score", 0),
            "grade":            scoring.get("grade", ""),
            "grade_emoji":      scoring.get("grade_emoji", ""),
            "reasons":          scoring.get("reasons", [])[:4],
            "service_type":     service_type,
            "service_label":    service_info.get("label", ""),
            "service_emoji":    service_info.get("emoji", ""),
            "gc_insight":       gc_insight,
            "gc_confidence":    gc_insight.get("confidence", ""),
            "gc_badges":        gc_insight.get("badges", []),
            "source_url":       gc_insight.get("source_url", ""),
            "source_label":     gc_insight.get("source_label", ""),
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
            "premium_quality_score": premium_quality_score,
            "premium_quality_checks": premium_quality_checks,
            "is_elite_quality": is_elite_quality,
            "elite_claimed_by_me": elite_claimed_by_me,
            "elite_claim_expires_at": elite_claim_expires_at,
            "elite_certificate": _elite_certificate(
                lead_data,
                gc_insight,
                premium_quality_score,
                premium_quality_checks,
                is_elite_quality,
                inspection_date,
                row_dict.get("first_seen", ""),
                elite_claimed_by_me,
                elite_claim_expires_at,
            ),
            "created_at":       row_dict.get("first_seen", ""),
            # AI classification fields (Qwen)
            "ai_trade":         lead_data.get("_trade", ""),
            "ai_urgency":       lead_data.get("_urgency", ""),
            "ai_summary":       lead_data.get("_ai_summary", ""),
            "ai_budget_min":    lead_data.get("_budget_min"),
            "ai_budget_max":    lead_data.get("_budget_max"),
            "ai_services":      lead_data.get("_services", []),
            "ai_is_residential":lead_data.get("_is_residential", False),
            "ai_is_commercial": lead_data.get("_is_commercial", False),
            # GC self-pull detection (gc_detector.py)
            "is_gc_self_pull":  lead_data.get("_is_gc_self_pull", False),
            "gc_pull_reason":   lead_data.get("_gc_pull_reason", ""),
        })

        if len(leads) >= limit:
            break

    conn.close()

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
        return -(int(ld.get("score", 0)) + int(ld.get("premium_quality_score", 0)) + urgency)
    leads.sort(key=_sort_key)

    response = {
        "leads":         leads,
        "auth_required": False,
        "anon_limit":    ANON_LEAD_LIMIT,
        "free_limit":    FREE_USER_LEAD_LIMIT,
        "pro_limit":     PRO_LEAD_LIMIT,
        "elite_limit":   ELITE_LEAD_LIMIT,
        "swipes_count":  swipes_count,
        "is_paid":       locals().get('is_paid', False) if user_id else None,
        "tier":          locals().get('subscription_tier', "free") if user_id else "anon",
        "elite_only":    elite_only,
        "billable_swipes_count": locals().get("billable_swipes_count", swipes_count),
        "replacement_credits": locals().get("replacement_credits", 0),
        "available_service_counts": available_service_counts,
        "available_service_types": sorted(available_service_counts.keys()),
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

    redeem_replacement_after_swipe = False
    replacement_credit_redeemed = False
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
        # Check quota by subscription tier.
        _is_paid, _tier = _get_web_subscription(user_id)
        _current = _count_swipes(user_id, None)
        _replacement_credits = _elite_replacement_credit_count(user_id) if _tier == "elite" else 0
        _billable_current = max(_current - _replacement_credits, 0)
        _limit = _tier_lead_limit(_tier, _is_paid)
        if _limit is not None and _billable_current >= _limit:
            return jsonify({
                "ok": False,
                "auth_required": True,
                "auth_mode":    "upgrade",
                "free_limit":   FREE_USER_LEAD_LIMIT,
                "tier_limit":   _limit,
                "tier":         _tier,
                "swipes_count": _current,
                "billable_swipes_count": _billable_current,
                "replacement_credits": _replacement_credits,
                "remaining":    0,
            }), 200
        redeem_replacement_after_swipe = (
            _tier == "elite"
            and _limit is not None
            and _current >= _limit
            and _replacement_credits > 0
        )

    conn = get_db_connection()
    c = conn.cursor()
    claim_result = None
    try:
        if action == 'like' and user_id:
            c.execute("""
                SELECT address_key, lead_data, primary_service_type, first_seen
                FROM consolidated_leads
                WHERE address_key = ?
                LIMIT 1
            """, (lead_id,))
            lead_row = c.fetchone()
            if lead_row:
                is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
                if is_elite_lead:
                    _, user_tier = _get_web_subscription(user_id)
                    if user_tier != "elite":
                        conn.close()
                        return jsonify({
                            "ok": False,
                            "auth_required": True,
                            "auth_mode": "upgrade",
                            "required_tier": "elite",
                            "message": "Este lead Elite requiere plan Elite para reservarlo.",
                        }), 200
                    claim_result = _claim_elite_lead(conn, c, int(user_id), lead_id, "like")
                    if claim_result.get("blocked"):
                        conn.close()
                        return jsonify({
                            "ok": False,
                            "exclusive_unavailable": True,
                            "message": "Este lead Elite ya fue reservado por otro contratista.",
                            "expires_at": claim_result.get("expires_at", ""),
                        }), 409

        c.execute("""
            INSERT INTO swipe_actions (user_id, anon_id, lead_id, action)
            VALUES (?, ?, ?, ?)
        """, (user_id, anon_id, lead_id, action))
        # If a caller likes a lead, put it in the lightweight Kanban pipeline.
        # Anonymous likes can be reviewed in the browser; registered likes can
        # continue into contact/estimate/invoice preparation.
        if action == 'like':
            pipeline_uid = str(user_id or anon_id)
            if pipeline_uid:
                c.execute("""
                    INSERT OR IGNORE INTO lead_pipeline (user_id, lead_id, status, notes)
                    VALUES (?, ?, 'Nuevo', '')
                """, (pipeline_uid, lead_id))
        # If an authenticated user liked the lead, also log it as a contact
        if action == 'like' and user_id:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
                    VALUES (?, ?, 'swipe_like', '')
                """, (user_id, lead_id))
            except Exception as log_err:
                logger.debug(f"lead_contacts log failed: {log_err}")
        if redeem_replacement_after_swipe and user_id:
            replacement_credit_redeemed = _redeem_elite_replacement_credit(c, int(user_id))
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
        billable_swipes_count = swipes_count
        replacement_credits = 0
    else:
        _is_paid, _tier = _get_web_subscription(user_id)
        replacement_credits = _elite_replacement_credit_count(user_id) if _tier == "elite" else 0
        billable_swipes_count = max(swipes_count - replacement_credits, 0)
        _limit = _tier_lead_limit(_tier, _is_paid)
        if _limit is not None:
            remaining = max(_limit - billable_swipes_count, 0)

    return jsonify({
        "ok":            True,
        "auth_required": auth_required,
        "anon_limit":    ANON_LEAD_LIMIT,
        "swipes_count":  swipes_count,
        "billable_swipes_count": billable_swipes_count,
        "replacement_credits": replacement_credits,
        "replacement_credit_redeemed": replacement_credit_redeemed,
        "remaining":     remaining,
        "elite_claim":    claim_result,
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
    is_paid, tier = _get_web_subscription(user_id)
    swipes = _count_swipes(user_id, None)
    tier_limit = _tier_lead_limit(str(tier).lower(), is_paid)
    replacement_credits = _elite_replacement_credit_count(user_id) if str(tier).lower() == "elite" else 0
    billable_swipes = max(swipes - replacement_credits, 0)
    return jsonify({
        "is_paid":     is_paid,
        "tier":        tier,
        "swipes":      swipes,
        "billable_swipes": billable_swipes,
        "replacement_credits": replacement_credits,
        "free_limit":  FREE_USER_LEAD_LIMIT,
        "pro_limit":   PRO_LEAD_LIMIT,
        "elite_limit": ELITE_LEAD_LIMIT,
        "remaining":   None if tier_limit is None else max(tier_limit - billable_swipes, 0),
        "tiers": [
            {"id": "pro",     "price": 29,  "limit": PRO_LEAD_LIMIT, "label": "Pro"},
            {"id": "premium", "price": 99,  "limit": None,           "label": "Premium"},
            {"id": "elite",   "price": 500, "limit": ELITE_LEAD_LIMIT, "label": "Elite", "curated": True},
        ],
    }), 200


@app.route('/api/swipe/elite-inventory', methods=['GET'])
def swipe_elite_inventory():
    """Non-sensitive inventory counts for Elite plan sales/ops."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    return jsonify(_elite_inventory_payload(city, service)), 200


@app.route('/api/swipe/market-readiness', methods=['GET'])
def swipe_market_readiness():
    """Public-safe market readiness summary for selling Elite."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    return jsonify(_elite_market_readiness_payload(city, service)), 200


@app.route('/api/swipe/elite-sales-proof', methods=['GET'])
def swipe_elite_sales_proof():
    """Public-safe proof points for selling the Elite tier."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    return jsonify(_elite_sales_proof_payload(city, service)), 200


@app.route('/api/admin/elite-quality-report', methods=['GET'])
@require_admin
def admin_elite_quality_report():
    """Admin QA report for deciding where Elite is ready to sell."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    return jsonify(_elite_quality_report_payload(city, service)), 200


@app.route('/api/admin/elite-uplift-candidates', methods=['GET'])
@require_admin
def admin_elite_uplift_candidates():
    """Return near-Elite candidates that ops can enrich into sellable inventory."""
    city = (request.args.get("city") or "").strip()
    service = (request.args.get("service") or request.args.get("service_cats") or "").strip()
    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    return jsonify(_elite_uplift_candidates_payload(city, service, limit)), 200


PIPELINE_STATUSES = ["Nuevo", "Contactado", "Propuesta", "Negociación", "Ganado", "Perdido"]


def _pipeline_uid(user_id, anon_id):
    return str(user_id or anon_id) if (user_id or anon_id) else None


def _pipeline_lead_payload(row):
    rd = dict(row)
    try:
        ld = json.loads(rd.get('lead_data') or '{}')
    except Exception:
        ld = {}
    scoring = ld.get('_scoring', {}) or {}
    return {
        "id": rd.get("lead_id"),
        "address": rd.get("address") or rd.get("lead_id") or "",
        "city": rd.get("city") or "",
        "status": rd.get("status") or "Nuevo",
        "notes": rd.get("notes") or "",
        "created_at": rd.get("created_at") or "",
        "updated_at": rd.get("updated_at") or "",
        "service_type": rd.get("primary_service_type") or ld.get("primary_service_type") or "",
        "trade": ld.get("_trade") or ld.get("trade") or "",
        "contractor": ld.get("contractor") or ld.get("gc_name") or "",
        "phone": (ld.get("contact_phone") or "").strip(),
        "email": (ld.get("contact_email") or "").strip(),
        "value": ld.get("value_float") or ld.get("permit_value") or 0,
        "score": scoring.get("score") or ld.get("score") or 0,
        "urgency": ld.get("_urgency") or "",
        "pain_point": ld.get("_key_pain_point") or "",
        "upsell": ld.get("_upsell_opportunity") or "",
        "best_time": ld.get("_best_contact_time") or "",
        "project_scope": ld.get("_project_scope") or "",
        "decision_maker": ld.get("_decision_maker") or "",
        "ai_summary": ld.get("_ai_summary") or "",
    }


@app.route('/api/pipeline', methods=['GET'])
def api_pipeline():
    """Return liked leads grouped by Kanban status for the current user/session."""
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    columns = {s: [] for s in PIPELINE_STATUSES}
    if not uid:
        return jsonify(columns), 200

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT p.lead_id, p.status, p.notes, p.created_at, p.updated_at,
                   l.address, l.city, l.primary_service_type, l.lead_data
            FROM lead_pipeline p
            LEFT JOIN consolidated_leads l ON l.address_key = p.lead_id
            WHERE p.user_id = ?
            ORDER BY p.updated_at DESC
        """, (uid,))
        for row in c.fetchall():
            lead = _pipeline_lead_payload(row)
            status = lead["status"] if lead["status"] in columns else "Nuevo"
            columns[status].append(lead)
    finally:
        conn.close()
    return jsonify(columns), 200


@app.route('/api/pipeline/move', methods=['POST'])
def api_pipeline_move():
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    new_status = (data.get("status") or "").strip()
    if not lead_id or new_status not in PIPELINE_STATUSES:
        return jsonify({"error": "lead_id and valid status required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("UPDATE lead_pipeline SET status=?, updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=?", (new_status, lead_id, uid))
    conn.commit(); ok = c.rowcount > 0; conn.close()
    return jsonify({"ok": ok, "status": new_status}), 200


@app.route('/api/pipeline/notes', methods=['POST'])
def api_pipeline_notes():
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    notes = (data.get("notes") or "")[:4000]
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("UPDATE lead_pipeline SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=?", (notes, lead_id, uid))
    conn.commit(); conn.close()
    return jsonify({"ok": True}), 200


@app.route('/api/pipeline/remove', methods=['POST'])
def api_pipeline_remove():
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("DELETE FROM lead_pipeline WHERE lead_id=? AND user_id=?", (lead_id, uid))
    conn.commit(); conn.close()
    return jsonify({"ok": True}), 200


@app.route('/api/pipeline/contact', methods=['POST'])
def api_pipeline_contact():
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    contact_type = data.get("contact_type", "call")
    outcome = (data.get("outcome") or "")[:500]
    notes = (data.get("notes") or "")[:2000]
    if not lead_id or contact_type not in ("call", "sms", "email", "visit", "other"):
        return jsonify({"error": "lead_id and valid contact_type required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO lead_contacts_log (lead_id, user_id, contact_type, outcome, notes) VALUES (?, ?, ?, ?, ?)", (lead_id, uid, contact_type, outcome, notes))
    c.execute("UPDATE lead_pipeline SET status='Contactado', updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=? AND status='Nuevo'", (lead_id, uid))
    conn.commit(); conn.close()
    return jsonify({"ok": True}), 200


@app.route('/api/pipeline/estimate', methods=['POST'])
def api_pipeline_estimate():
    """Create a draft estimate and advance the lead to Propuesta."""
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    amount = float(data.get("amount") or 0)
    description = (data.get("description") or "")[:4000]
    user_id, _ = _resolve_swipe_identity()
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if not user_id:
        return jsonify({"error": "registration_required", "message": "Regístrate para preparar estimados/invoices"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO lead_estimates (lead_id, user_id, amount, description, status) VALUES (?, ?, ?, ?, 'draft')", (lead_id, user_id, amount, description))
    estimate_id = c.lastrowid
    c.execute("INSERT OR IGNORE INTO lead_pipeline (user_id, lead_id, status, notes) VALUES (?, ?, 'Nuevo', '')", (str(user_id), lead_id))
    c.execute("UPDATE lead_pipeline SET status='Propuesta', updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=?", (lead_id, str(user_id)))
    conn.commit(); conn.close()
    return jsonify({"ok": True, "estimate_id": estimate_id, "amount": amount, "status": "draft"}), 200


@app.route('/api/pipeline/invoice', methods=['POST'])
def api_pipeline_invoice():
    """Prepare a draft invoice for a liked lead; this does not charge the customer."""
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    amount = float(data.get("amount") or 0)
    description = (data.get("description") or "")[:4000]
    user_id, _ = _resolve_swipe_identity()
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if not user_id:
        return jsonify({"error": "registration_required", "message": "Regístrate para preparar invoices"}), 401
    conn = get_db_connection(); c = conn.cursor()
    c.execute("INSERT INTO lead_invoices (lead_id, user_id, amount, description, status) VALUES (?, ?, ?, ?, 'draft')", (lead_id, user_id, amount, description))
    invoice_id = c.lastrowid
    c.execute("INSERT OR IGNORE INTO lead_pipeline (user_id, lead_id, status, notes) VALUES (?, ?, 'Nuevo', '')", (str(user_id), lead_id))
    c.execute("UPDATE lead_pipeline SET status='Propuesta', updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=?", (lead_id, str(user_id)))
    conn.commit(); conn.close()
    return jsonify({"ok": True, "invoice_id": invoice_id, "amount": amount, "status": "draft", "prepared": True}), 200


@app.route('/api/pipeline/activity/<path:lead_id>', methods=['GET'])
def api_pipeline_activity(lead_id):
    user_id, anon_id = _resolve_swipe_identity()
    uid = _pipeline_uid(user_id, anon_id)
    if not uid:
        return jsonify([]), 200
    conn = get_db_connection(); c = conn.cursor(); timeline = []
    c.execute("SELECT contact_type, outcome, notes, created_at FROM lead_contacts_log WHERE lead_id=? AND user_id=? ORDER BY created_at DESC", (lead_id, uid))
    for row in c.fetchall():
        timeline.append({"type": "contact", "contact_type": row[0], "outcome": row[1], "notes": row[2], "date": row[3]})
    if user_id:
        c.execute("SELECT amount, description, status, created_at FROM lead_estimates WHERE lead_id=? AND user_id=? ORDER BY created_at DESC", (lead_id, user_id))
        for row in c.fetchall():
            timeline.append({"type": "estimate", "amount": row[0], "description": row[1], "estimate_status": row[2], "date": row[3]})
        c.execute("SELECT amount, description, status, prepared_at FROM lead_invoices WHERE lead_id=? AND user_id=? ORDER BY prepared_at DESC", (lead_id, user_id))
        for row in c.fetchall():
            timeline.append({"type": "invoice", "amount": row[0], "description": row[1], "invoice_status": row[2], "date": row[3]})
    conn.close()
    timeline.sort(key=lambda x: x.get("date") or "", reverse=True)
    return jsonify(timeline), 200


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
    for an 0brix JWT.

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
    0brix JWT.

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
    """Return known city names from live inventory plus geocode fallbacks."""
    q = (request.args.get('q') or '').strip().lower()
    city_set = set(_CITY_COORDS.keys())
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if q:
            c.execute("""
                SELECT city, COUNT(*) AS n
                FROM consolidated_leads
                WHERE TRIM(COALESCE(city, '')) != ''
                  AND LOWER(city) LIKE LOWER(?)
                GROUP BY city
                ORDER BY n DESC, city ASC
                LIMIT 80
            """, (f"%{q}%",))
        else:
            c.execute("""
                SELECT city, COUNT(*) AS n
                FROM consolidated_leads
                WHERE TRIM(COALESCE(city, '')) != ''
                GROUP BY city
                ORDER BY n DESC, city ASC
                LIMIT 80
            """)
        city_set.update(str(row[0]).strip().lower() for row in c.fetchall() if row[0])
        conn.close()
    except Exception as e:
        logger.debug(f"City autocomplete DB lookup failed: {e}")

    cities = sorted(city_set)
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


@app.route('/api/swipe/report-lead', methods=['POST'])
@require_auth
def swipe_report_lead_quality():
    """Let users report bad leads so Elite inventory can be replaced/improved."""
    user_id = g.user_id
    data = request.get_json(silent=True) or {}
    lead_id = (data.get("lead_id") or "").strip()
    reason = (data.get("reason") or "other").strip().lower()
    details = (data.get("details") or "").strip()[:1000]
    allowed_reasons = {"no_contact", "wrong_number", "already_taken", "not_interested", "bad_source", "duplicate", "other"}
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    if reason not in allowed_reasons:
        reason = "other"

    conn = get_db_connection()
    c = conn.cursor()
    credit_granted = False
    replacement_credits = 0
    try:
        c.execute("""
            SELECT address_key, lead_data, primary_service_type, first_seen
            FROM consolidated_leads
            WHERE address_key = ?
            LIMIT 1
        """, (lead_id,))
        lead_row = c.fetchone()
        if not lead_row:
            return jsonify({"error": "lead not found"}), 404
        is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
        c.execute("""
            INSERT INTO lead_quality_reports (lead_id, user_id, reason, details, is_elite, status)
            VALUES (?, ?, ?, ?, ?, 'open')
            ON CONFLICT(user_id, lead_id, reason) DO UPDATE SET
                details = excluded.details,
                is_elite = excluded.is_elite,
                status = 'open',
                updated_at = CURRENT_TIMESTAMP
        """, (lead_id, int(user_id), reason, details, 1 if is_elite_lead else 0))
        if is_elite_lead:
            c.execute("""
                UPDATE elite_lead_claims
                   SET status = 'reported',
                       expires_at = CURRENT_TIMESTAMP
                 WHERE lead_id = ? AND user_id = ?
            """, (lead_id, int(user_id)))
            credit_granted = _grant_elite_replacement_credit(
                c,
                int(user_id),
                lead_id,
                reason,
                "Auto-granted from Elite lead quality report",
            )
            c.execute("""
                SELECT COUNT(*)
                FROM elite_replacement_credits
                WHERE user_id = ? AND status = 'open'
            """, (int(user_id),))
            row = c.fetchone()
            replacement_credits = int(row[0]) if row else 0
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning(f"lead quality report failed: {e}")
        return jsonify({"error": "failed to report lead"}), 500
    finally:
        conn.close()

    return jsonify({
        "ok": True,
        "is_elite": is_elite_lead,
        "replacement_review": bool(is_elite_lead),
        "replacement_credit_granted": credit_granted,
        "replacement_credits": replacement_credits,
        "message": "Gracias. Revisaremos este lead para reemplazo/mejora de calidad.",
    }), 200


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
               cl.address, cl.city, cl.lead_data, cl.primary_service_type, cl.first_seen
        FROM swipe_actions sa
        JOIN consolidated_leads cl ON cl.address_key = sa.lead_id
        WHERE sa.user_id = ? AND sa.action = 'like'
        GROUP BY sa.lead_id
        ORDER BY contacted_at DESC
        LIMIT 100
    """, (user_id,))
    rows = c.fetchall()

    contacts = []
    try:
        for row in rows:
            rd = dict(row)
            try:
                ld = json.loads(rd.get('lead_data') or '{}')
            except Exception:
                ld = {}
            scoring = ld.get('_scoring', {}) or {}
            service_type = (rd.get('primary_service_type') or ld.get('primary_service_type') or '').strip().lower()
            gc_insight = build_gc_insight(ld, service_type) if service_type else {}
            is_elite_quality = False
            premium_quality_score = 0
            premium_quality_checks = []
            if service_type:
                is_elite_quality, premium_quality_score, premium_quality_checks = _is_elite_lead_record(rd, ld)
            claim = _active_elite_claim(c, rd['lead_id']) if is_elite_quality else None
            elite_claimed_by_me = bool(claim and int(claim['user_id']) == int(user_id))
            elite_claim_expires_at = claim['expires_at'] if elite_claimed_by_me else ''
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
                'service_type':  service_type,
                'source_url':    gc_insight.get('source_url', ''),
                'source_label':  gc_insight.get('source_label', ''),
                'is_elite_quality': is_elite_quality,
                'premium_quality_score': premium_quality_score,
                'elite_claimed_by_me': elite_claimed_by_me,
                'elite_claim_expires_at': elite_claim_expires_at,
                'elite_certificate': _elite_certificate(
                    ld,
                    gc_insight,
                    premium_quality_score,
                    premium_quality_checks,
                    is_elite_quality,
                    '',
                    rd.get('first_seen', ''),
                    elite_claimed_by_me,
                    elite_claim_expires_at,
                ),
            })
    finally:
        conn.close()
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
    claim_result = None
    try:
        c.execute("""
            SELECT address_key, lead_data, primary_service_type, first_seen
            FROM consolidated_leads
            WHERE address_key = ?
            LIMIT 1
        """, (lead_id,))
        lead_row = c.fetchone()
        if lead_row:
            is_elite_lead, _, _ = _is_elite_lead_record(dict(lead_row))
            if is_elite_lead:
                _, user_tier = _get_web_subscription(user_id)
                if user_tier != "elite":
                    conn.close()
                    return jsonify({"ok": False, "auth_required": True, "auth_mode": "upgrade", "required_tier": "elite"}), 200
                claim_result = _claim_elite_lead(conn, c, int(user_id), lead_id, contact_type)
                if claim_result.get("blocked"):
                    conn.close()
                    return jsonify({"ok": False, "exclusive_unavailable": True, "expires_at": claim_result.get("expires_at", "")}), 409
        c.execute("""
            INSERT OR IGNORE INTO lead_contacts (user_id, lead_id, contact_type, notes)
            VALUES (?, ?, ?, '')
        """, (user_id, lead_id, contact_type))
        conn.commit()
    except Exception as e:
        logger.debug(f"swipe_log_contact failed: {e}")
    conn.close()
    return jsonify({"ok": True, "elite_claim": claim_result}), 200


@app.route('/api/swipe/pulse', methods=['GET'])
def swipe_pulse():
    """
    Lightweight polling endpoint for real-time sync.
    Returns the count of leads newer than `since` (ISO timestamp).
    The swipe UI polls this every 30s and refreshes the deck when new leads arrive.
    """
    since = request.args.get("since", "")
    conn = get_db_connection()
    c = conn.cursor()
    try:
        if since:
            c.execute(
                "SELECT COUNT(*) FROM consolidated_leads WHERE last_updated > ?",
                (since,),
            )
        else:
            c.execute("SELECT COUNT(*) FROM consolidated_leads")
        total_new = c.fetchone()[0]

        c.execute("SELECT MAX(last_updated) FROM consolidated_leads")
        latest = c.fetchone()[0] or ""
    finally:
        conn.close()

    return jsonify({
        "new_since": total_new,
        "latest_update": latest,
        "server_time": datetime.utcnow().isoformat(),
    }), 200


@app.route('/api/admin/lead-quality-reports', methods=['GET'])
@require_admin
def admin_lead_quality_reports():
    """List user-reported lead quality issues for Elite QA operations."""
    status = (request.args.get("status") or "open").strip().lower()
    if status not in {"open", "reviewing", "resolved", "dismissed", "all"}:
        status = "open"
    params: list = []
    where = ""
    if status != "all":
        where = "WHERE r.status = ?"
        params.append(status)
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute(f"SELECT COUNT(*) FROM lead_quality_reports r {where}", params)
        total = c.fetchone()[0]
        c.execute(f"""
            SELECT r.id, r.lead_id, r.user_id, u.email, u.full_name,
                   r.reason, r.details, r.is_elite, r.status, r.resolution,
                   r.created_at, r.updated_at,
                   l.address, l.city, l.primary_service_type,
                   erc.status AS replacement_credit_status,
                   erc.granted_at AS replacement_credit_granted_at
            FROM lead_quality_reports r
            LEFT JOIN users u ON u.id = r.user_id
            LEFT JOIN consolidated_leads l ON l.address_key = r.lead_id
            LEFT JOIN elite_replacement_credits erc
              ON erc.user_id = r.user_id
             AND erc.lead_id = r.lead_id
             AND erc.reason = r.reason
            {where}
            ORDER BY r.created_at DESC
            LIMIT 100
        """, params)
        reports = [dict(row) for row in c.fetchall()]
        c.execute("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                SUM(CASE WHEN is_elite = 1 THEN 1 ELSE 0 END) AS elite_count
            FROM lead_quality_reports
        """)
        summary = dict(c.fetchone())
        c.execute("""
            SELECT
                COUNT(*) AS open_replacement_credits,
                COUNT(DISTINCT user_id) AS users_with_open_replacements
            FROM elite_replacement_credits
            WHERE status = 'open'
        """)
        summary.update(dict(c.fetchone()))
    finally:
        conn.close()
    return jsonify({"reports": reports, "total": total, "summary": summary, "status": status}), 200


@app.route('/api/admin/lead-quality-reports/<int:report_id>', methods=['PATCH'])
@require_admin
def admin_update_lead_quality_report(report_id):
    """Update QA report status after admin review."""
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    resolution = (data.get("resolution") or "").strip()[:1000]
    if status not in {"open", "reviewing", "resolved", "dismissed"}:
        return jsonify({"error": "Status must be open, reviewing, resolved or dismissed"}), 400

    if status in {"resolved", "dismissed"} and not resolution:
        resolution = "Closed by admin review"
    elif status == "reviewing" and not resolution:
        resolution = "Reviewing lead quality report"

    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("""
            UPDATE lead_quality_reports
               SET status = ?,
                   resolution = ?,
                   updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
        """, (status, resolution, report_id))
        if c.rowcount == 0:
            conn.rollback()
            return jsonify({"error": "Lead quality report not found"}), 404
        conn.commit()
        c.execute("""
            SELECT id, lead_id, user_id, reason, details, is_elite,
                   status, resolution, created_at, updated_at
              FROM lead_quality_reports
             WHERE id = ?
        """, (report_id,))
        row = c.fetchone()
        return jsonify({"ok": True, "report": dict(row) if row else {"id": report_id, "status": status}}), 200
    finally:
        conn.close()


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

    # NYC 311 Service Requests blueprint
    from web.nyc311 import bp as nyc311_bp
    app.register_blueprint(nyc311_bp)

    # Construction & Demolition Permits blueprint
    from web.permits import bp as permits_bp
    app.register_blueprint(permits_bp)

    # Marketing Dashboard blueprint (optional — graceful if not present)
    try:
        from web.marketing_routes import marketing_bp
        app.register_blueprint(marketing_bp, url_prefix="/api/marketing")
        logger.info("Marketing routes registered at /api/marketing/*")
    except Exception as _mkt_bp_err:
        logger.warning(f"Marketing routes not loaded: {_mkt_bp_err}")

    return app


# ── CrossData Prediction endpoints ────────────────────────────────────────────

@app.route('/api/ai/classify', methods=['POST'])
@require_auth
def ai_classify_lead():
    """
    Clasifica un lead con Qwen.
    Body: { "lead_id": "..." } o { "lead": { lead dict } }
    """
    body = request.get_json(silent=True) or {}
    lead_id = body.get("lead_id")
    lead_dict = body.get("lead")

    if lead_id:
        conn = get_db_connection()
        row = conn.execute(
            "SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Lead not found"}), 404
        try:
            lead_dict = json.loads(row["lead_data"])
        except Exception:
            return jsonify({"error": "Invalid lead_data"}), 500

    if not lead_dict:
        return jsonify({"error": "Provide lead_id or lead"}), 400

    try:
        from utils.ai_classifier import enrich_lead_with_classification, get_cache_stats
        enriched = enrich_lead_with_classification(dict(lead_dict))
        return jsonify({
            "trade":       enriched.get("_trade"),
            "urgency":     enriched.get("_urgency"),
            "budget_min":  enriched.get("_budget_min"),
            "budget_max":  enriched.get("_budget_max"),
            "services":    enriched.get("_services", []),
            "summary":     enriched.get("_ai_summary"),
            "is_residential": enriched.get("_is_residential"),
            "is_commercial":  enriched.get("_is_commercial"),
            "owner_type":     enriched.get("_owner_type"),
            "source":         enriched.get("_classifier_source"),
            "cache_stats":    get_cache_stats(),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/ai/classify-batch', methods=['POST'])
@require_auth
def ai_classify_batch():
    """
    Clasifica los N leads más recientes sin clasificar con Qwen.
    Body: { "limit": 100 }
    """
    body = request.get_json(silent=True) or {}
    limit = min(int(body.get("limit", 50)), 500)

    conn = get_db_connection()
    rows = conn.execute("""
        SELECT address_key, lead_data FROM consolidated_leads
        WHERE lead_data NOT LIKE '%_classifier_source%'
          AND (lead_data LIKE '%description%' OR lead_data LIKE '%permit_type%')
        ORDER BY last_updated DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    if not rows:
        return jsonify({"status": "nothing_to_classify", "count": 0})

    import threading
    def _batch():
        try:
            from utils.ai_classifier import enrich_lead_with_classification
            classified = 0
            CHUNK = 20  # commit every 20 records to avoid long DB locks
            for i in range(0, len(rows), CHUNK):
                chunk = rows[i:i + CHUNK]
                db = get_db_connection()
                try:
                    for row in chunk:
                        try:
                            ld = json.loads(row["lead_data"] or "{}")
                            enriched = enrich_lead_with_classification(ld)
                            db.execute(
                                "UPDATE consolidated_leads SET lead_data=?, last_updated=? WHERE address_key=?",
                                (json.dumps(enriched, default=str), datetime.utcnow().isoformat(), row["address_key"])
                            )
                            classified += 1
                        except Exception as e:
                            logger.warning(f"batch classify error: {e}")
                    db.commit()
                except Exception as e:
                    logger.error(f"[AI Batch] chunk commit failed: {e}")
                finally:
                    db.close()
            logger.info(f"[AI Batch] classified {classified} leads with Qwen")
        except Exception as e:
            logger.error(f"[AI Batch] failed: {e}")

    threading.Thread(target=_batch, daemon=True).start()
    return jsonify({"status": "started", "leads_queued": len(rows)}), 202


@app.route('/api/crossdata/run', methods=['POST'])
@require_auth
def crossdata_run():
    """Dispara manualmente un ciclo de predicción cross-data."""
    import threading
    def _run():
        try:
            from agents.crossdata_agent import run_cross_prediction
            run_cross_prediction()
        except Exception as e:
            logger.error(f"crossdata manual run error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "message": "Cross-data prediction running in background"}), 202


@app.route('/api/crossdata/stats', methods=['GET'])
@require_auth
def crossdata_stats():
    """Estadísticas del agente cross-data."""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM consolidated_leads WHERE lead_data LIKE '%_cross_prediction%'")
        cross_leads = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM consolidated_leads WHERE agent_sources LIKE '%,%'")
        multi_agent = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM scheduled_inspections")
        inspections = c.fetchone()[0]

        c.execute("""
            SELECT json_extract(lead_data, '$._cross_prediction.combo_matched') as combo, COUNT(*) as cnt
            FROM consolidated_leads
            WHERE lead_data LIKE '%combo_matched%'
            GROUP BY combo ORDER BY cnt DESC LIMIT 10
        """)
        combos = [{"combo": r[0], "count": r[1]} for r in c.fetchall()]

        c.execute("""
            SELECT json_extract(lead_data, '$._cross_prediction.urgency') as urgency, COUNT(*) as cnt
            FROM consolidated_leads
            WHERE lead_data LIKE '%_cross_prediction%'
            GROUP BY urgency ORDER BY cnt DESC
        """)
        by_urgency = {r[0]: r[1] for r in c.fetchall()}

        c.execute("SELECT COUNT(*) FROM property_signals WHERE agent_key='crossdata'")
        crossdata_signals = c.fetchone()[0]

    finally:
        conn.close()

    return jsonify({
        "cross_predicted_leads": cross_leads,
        "multi_agent_leads": multi_agent,
        "scheduled_inspections": inspections,
        "crossdata_signals": crossdata_signals,
        "by_urgency": by_urgency,
        "top_combos": combos,
    })


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)


# ═══════════════════════════════════════════════════════════════════
# Disaster Intelligence Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/disasters/active', methods=['GET'])
@require_auth
def get_active_disasters():
    """Get currently active disaster events (requires PostgreSQL)."""
    use_pg = os.getenv("USE_POSTGRES", "").lower() in ("1", "true")
    if not use_pg:
        return jsonify({"error": "Disaster Intelligence requires PostgreSQL (USE_POSTGRES=1)"}), 501
    try:
        from db_postgres import get_conn, put_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, event_type, source, severity, description,
                       affected_cities, started_at, ended_at
                FROM disaster_events
                WHERE ended_at IS NULL OR ended_at > NOW()
                ORDER BY started_at DESC LIMIT 50
            """)
            disasters = []
            for row in cur.fetchall():
                r = dict(row)
                r['started_at'] = str(r.get('started_at', ''))
                r['ended_at'] = str(r.get('ended_at', ''))
                disasters.append(r)
        put_conn(conn)
        return jsonify({"disasters": disasters, "count": len(disasters)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/disasters/run', methods=['POST'])
@require_auth
def run_disaster_check():
    """Manually trigger disaster intelligence scan."""
    import threading
    def _run():
        try:
            from agents.disaster_agent import DisasterAgent
            agent = DisasterAgent()
            leads = agent.fetch_leads()
            if leads:
                agent.send_batch(leads)
        except Exception as e:
            logger.error(f"Disaster manual run error: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"}), 202


# ═══════════════════════════════════════════════════════════════════
# Property DNA Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/property-dna/<path:lead_id>', methods=['GET'])
@require_auth
def get_property_dna(lead_id):
    """Get Property DNA data for a specific lead."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    ld = json.loads(row["lead_data"] or "{}") if row["lead_data"] else {}
    return jsonify({
        "lead_id": lead_id,
        "property_year_built": ld.get("property_year_built"),
        "property_roof_material": ld.get("property_roof_material"),
        "property_value": ld.get("property_value"),
        "property_sqft": ld.get("property_sqft"),
        "flood_zone": ld.get("flood_zone"),
        "source": ld.get("_property_dna_source"),
    }), 200


@app.route('/api/property-dna/enrich', methods=['POST'])
@require_auth
def enrich_property_dna():
    """Batch enrich leads with Property DNA."""
    data = request.get_json(silent=True) or {}
    limit = min(int(data.get("limit", 20)), 100)
    import threading
    def _enrich():
        try:
            from utils.property_dna import get_property_dna
            import sqlite3
            dna = get_property_dna()
            conn2 = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn2.row_factory = sqlite3.Row
            rows = conn2.execute("""
                SELECT address_key, address, city, lead_data FROM consolidated_leads
                WHERE lead_data NOT LIKE '%property_year_built%' LIMIT ?
            """, (limit,)).fetchall()
            conn2.close()
            enriched = 0
            for row in rows:
                try:
                    lead = {"address": row["address"], "city": row["city"]}
                    lead = dna.enrich_lead(lead)
                    ld = json.loads(row["lead_data"] or "{}")
                    for k in ["property_year_built", "property_roof_material",
                              "property_value", "property_sqft", "flood_zone",
                              "_property_dna_source"]:
                        if k in lead:
                            ld[k] = lead[k]
                    db = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
                    db.execute("UPDATE consolidated_leads SET lead_data=? WHERE address_key=?",
                              (json.dumps(ld, default=str), row["address_key"]))
                    db.commit()
                    db.close()
                    enriched += 1
                except Exception as e:
                    logger.debug(f"Property DNA enrich error: {e}")
            logger.info(f"[PropertyDNA] Enriched {enriched}/{len(rows)} leads")
        except Exception as e:
            logger.error(f"Property DNA batch error: {e}")
    threading.Thread(target=_enrich, daemon=True).start()
    return jsonify({"status": "started", "limit": limit}), 202


# ═══════════════════════════════════════════════════════════════════
# Tripartite Scoring Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/scoring/tripartite/<path:lead_id>', methods=['GET'])
@require_auth
def get_tripartite_score(lead_id):
    """Get tripartite scores (sub/gc/insurance) for a lead."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    ld = json.loads(row["lead_data"] or "{}") if row["lead_data"] else {}
    if "_tripartite" in ld:
        return jsonify({"lead_id": lead_id, **ld["_tripartite"]}), 200
    try:
        from utils.tripartite_scoring import calculate_tripartite_scores
        scores = calculate_tripartite_scores(ld)
        return jsonify({"lead_id": lead_id, **scores}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/scoring/tripartite-batch', methods=['POST'])
@require_auth
def batch_tripartite_scoring():
    """Calculate tripartite scores for all leads missing them."""
    data = request.get_json(silent=True) or {}
    limit = min(int(data.get("limit", 50)), 500)
    import threading
    def _score():
        try:
            from utils.tripartite_scoring import calculate_tripartite_scores
            import sqlite3
            conn2 = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
            conn2.row_factory = sqlite3.Row
            rows = conn2.execute("""
                SELECT address_key, lead_data FROM consolidated_leads
                WHERE lead_data NOT LIKE '%_tripartite%' LIMIT ?
            """, (limit,)).fetchall()
            conn2.close()
            scored = 0
            for row in rows:
                try:
                    ld = json.loads(row["lead_data"] or "{}")
                    scores = calculate_tripartite_scores(ld)
                    ld["_tripartite"] = scores
                    db = sqlite3.connect(os.getenv("DB_PATH", "data/leads.db"))
                    db.execute("UPDATE consolidated_leads SET lead_data=? WHERE address_key=?",
                              (json.dumps(ld, default=str), row["address_key"]))
                    db.commit()
                    db.close()
                    scored += 1
                except Exception as e:
                    logger.debug(f"Tripartite score error: {e}")
            logger.info(f"[Tripartite] Scored {scored}/{len(rows)} leads")
        except Exception as e:
            logger.error(f"Tripartite batch error: {e}")
    threading.Thread(target=_score, daemon=True).start()
    return jsonify({"status": "started", "limit": limit}), 202


# ═══════════════════════════════════════════════════════════════════
# Multi-Tenant Lead Assignment Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/leads/<path:lead_id>/assign', methods=['POST'])
@require_auth
def assign_lead_to_user(lead_id):
    """Manually assign a lead to a GC or Sub."""
    data = request.get_json() or {}
    gc_id = data.get('gc_id')
    sub_id = data.get('sub_id')
    if not gc_id and not sub_id:
        return jsonify({"error": "Provide gc_id or sub_id"}), 400
    try:
        from utils.lead_router import get_lead_router
        router = get_lead_router()
        router.assign_lead(lead_id, gc_id=gc_id, sub_id=sub_id)
        return jsonify({"status": "assigned", "lead_id": lead_id}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/leads/assigned', methods=['GET'])
@require_auth
def get_assigned_leads():
    """Get leads assigned to the current user (GC or Sub)."""
    user_id = g.user_id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE id = ?", (user_id,))
    user_row = c.fetchone()
    if not user_row:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    role = user_row[0] if isinstance(user_row[0], str) else 'user'
    if role == 'subcontractor':
        c.execute("""
            SELECT address_key, address, city, agent_sources, lead_data, assigned_at
            FROM consolidated_leads WHERE assigned_to_sub = ? AND is_dead_lead = 0
            ORDER BY assigned_at DESC
        """, (user_id,))
    elif role == 'gc':
        c.execute("""
            SELECT address_key, address, city, agent_sources, lead_data, assigned_at
            FROM consolidated_leads WHERE assigned_to_gc = ? AND is_dead_lead = 0
            ORDER BY assigned_at DESC
        """, (user_id,))
    else:
        conn.close()
        return jsonify({"leads": [], "count": 0}), 200
    leads = []
    for row in c.fetchall():
        rd = dict(row)
        ld = json.loads(rd.get("lead_data", "{}") or "{}") if rd.get("lead_data") else {}
        leads.append({
            "id": rd["address_key"], "address": rd["address"],
            "city": rd["city"], "source": rd["agent_sources"],
            "assigned_at": rd.get("assigned_at"),
            "description": (ld.get("description") or "")[:200],
        })
    conn.close()
    return jsonify({"leads": leads, "count": len(leads), "role": role}), 200


# ═══════════════════════════════════════════════════════════════════
# CSLB License Verification Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/cslb/verify/<license_number>', methods=['GET'])
@require_auth
def verify_cslb_license(license_number):
    """Verify a CSLB license by number."""
    try:
        from utils.cslb_verifier import verify_license
        result = verify_license(license_number)
        if result:
            return jsonify(result), 200
        return jsonify({"error": "License not found", "license_number": license_number}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/cslb/search', methods=['GET'])
@require_auth
def search_cslb_license():
    """Search CSLB by business name."""
    business_name = request.args.get('name', '')
    city = request.args.get('city', '')
    if not business_name or len(business_name) < 3:
        return jsonify({"error": "Name must be at least 3 characters"}), 400
    try:
        from utils.cslb_verifier import search_by_name
        results = search_by_name(business_name, city)
        return jsonify({"results": results, "count": len(results)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/cslb/verify-sub', methods=['POST'])
@require_auth
def verify_subcontractor_cslb():
    """Verify a subcontractor's license against claimed specialties."""
    data = request.get_json() or {}
    license_number = data.get('license_number', '')
    specialties = data.get('specialties', [])
    if not license_number:
        return jsonify({"error": "license_number required"}), 400
    try:
        from utils.cslb_verifier import verify_subcontractor_profile
        result = verify_subcontractor_profile(license_number, specialties)
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/cslb/batch', methods=['POST'])
@require_auth
@limiter.limit("5 per minute")
def batch_verify_cslb():
    """Batch verify multiple CSLB licenses."""
    data = request.get_json() or {}
    licenses = data.get('licenses', [])
    if not licenses or len(licenses) > 20:
        return jsonify({"error": "Provide 1-20 license numbers"}), 400
    import threading
    def _batch():
        try:
            from utils.cslb_verifier import batch_verify
            results = batch_verify(licenses)
            verified = sum(1 for v in results.values() if v and v.get("is_active"))
            logger.info(f"[CSLB] Batch verified {verified}/{len(licenses)} licenses")
        except Exception as e:
            logger.error(f"[CSLB] Batch verify error: {e}")
    threading.Thread(target=_batch, daemon=True).start()
    return jsonify({"status": "started", "count": len(licenses)}), 202


# ═══════════════════════════════════════════════════════════════════
# Huly CRM Integration Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/crm/deals', methods=['GET'])
@require_auth
def get_crm_deals():
    """Get deals from Huly CRM."""
    stage = request.args.get('stage')
    limit = min(int(request.args.get('limit', 50)), 200)
    try:
        from utils.huly_crm import get_huly_crm
        crm = get_huly_crm()
        deals = crm.get_deals(stage=stage, limit=limit)
        return jsonify({"deals": deals, "count": len(deals)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/crm/contacts', methods=['GET'])
@require_auth
def get_crm_contacts():
    """Get contacts from Huly CRM."""
    limit = min(int(request.args.get('limit', 50)), 200)
    try:
        from utils.huly_crm import get_huly_crm
        crm = get_huly_crm()
        contacts = crm.get_contacts(limit=limit)
        return jsonify({"contacts": contacts, "count": len(contacts)}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/crm/push/<path:lead_id>', methods=['POST'])
@require_auth
def push_lead_to_crm(lead_id):
    """Manually push a lead to Huly CRM."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT lead_data FROM consolidated_leads WHERE address_key = ?", (lead_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Lead not found"}), 404
    ld = json.loads(row["lead_data"] or "{}") if row["lead_data"] else {}
    ld["id"] = lead_id
    try:
        from utils.huly_crm import push_lead_to_crm
        from utils.tripartite_scoring import calculate_tripartite_scores
        scores = ld.get("_tripartite") or calculate_tripartite_scores(ld)
        result = push_lead_to_crm(ld, scores)
        if result:
            return jsonify({"status": "pushed", **result}), 200
        return jsonify({"status": "skipped", "reason": "below thresholds or not configured"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════
# CSLB Batch Verification Endpoint
# ═══════════════════════════════════════════════════════════════════

@app.route('/api/cslb/batch-verify', methods=['POST'])
@require_auth
@limiter.limit("2 per minute")
def trigger_cslb_batch():
    """Trigger batch CSLB verification of all contractor CSVs."""
    data = request.get_json(silent=True) or {}
    limit = min(int(data.get("limit", 20)), 100)
    push_huly = data.get("push_huly", False)
    import threading
    def _batch():
        try:
            cmd = [sys.executable, "scripts/cslb_batch_verify.py", "--limit", str(limit), "--delay", "2.5"]
            if push_huly:
                cmd.append("--push-huly")
            import subprocess
            result = subprocess.run(cmd, capture_output=True, text=True, cwd="/opt/MLeads", timeout=600)
            logger.info(f"[CSLB Batch] exit={result.returncode}")
            if result.stderr:
                logger.warning(f"[CSLB Batch] stderr: {result.stderr[:500]}")
        except Exception as e:
            logger.error(f"[CSLB Batch] error: {e}")
    import sys
    threading.Thread(target=_batch, daemon=True).start()
    return jsonify({"status": "started", "limit": limit, "push_huly": push_huly}), 202
