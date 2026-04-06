"""
app.py — Flask API server for multi-user lead dashboard

REST API endpoints for:
- Authentication (login, refresh, logout)
- Lead retrieval and filtering
- User stats and audit logs
- Admin user/role management
"""

import os
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g
from flask_cors import CORS

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

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
CORS(app)

logger = logging.getLogger("web_api")


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
# Health Check
# ─────────────────────────────────────────────────────────

@app.route('/api/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat()
    })


# ─────────────────────────────────────────────────────────
# Authentication Endpoints
# ─────────────────────────────────────────────────────────

@app.route('/api/auth/login', methods=['POST'])
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
    status = request.args.get('status', 'all')  # all, new, contacted, pending
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

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Get total count
    c.execute(f"SELECT COUNT(*) FROM consolidated_leads l WHERE {where_sql}", params)
    total = c.fetchone()[0]

    # Get paginated results
    offset = (page - 1) * per_page
    c.execute(f"""
        SELECT l.address_key, l.address, l.city, l.agent_sources,
               l.first_seen, l.last_updated, l.lead_data
        FROM consolidated_leads l
        WHERE {where_sql}
        ORDER BY l.last_updated DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset])

    import json as json_mod
    leads = []
    for row in c.fetchall():
        row_dict = dict(row)
        # Parse lead_data JSON for display fields
        lead_data = {}
        try:
            lead_data = json_mod.loads(row_dict.get('lead_data', '{}') or '{}')
        except Exception:
            pass

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
        }

        # Check if user has contacted this lead
        c.execute("""
            SELECT COUNT(*) FROM lead_contacts
            WHERE lead_id = ? AND user_id = ?
        """, (lead['id'], user_id))
        lead['contacted'] = c.fetchone()[0] > 0

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

    import json as json_mod
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT address_key, address, city, agent_sources, first_seen, last_updated, lead_data
        FROM consolidated_leads
        WHERE address_key = ?
    """, (lead_id,))

    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"error": "Lead not found"}), 404

    row_dict = dict(row)
    lead_data = {}
    try:
        lead_data = json_mod.loads(row_dict.get('lead_data', '{}') or '{}')
    except Exception:
        pass

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
    }

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
        from datetime import timedelta
        expiration = (datetime.utcnow() + timedelta(hours=int(expires_in_hours))).strftime("%Y-%m-%d %H:%M:%S")
    elif expires_at:
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

        # Assign city access
        for city_id in city_ids:
            c.execute("""
                INSERT INTO user_city_access (user_id, city_id)
                VALUES (?, ?)
            """, (user_id, city_id))

        # Assign agent access
        for agent_id in agent_ids:
            c.execute("""
                INSERT INTO user_agent_access (user_id, agent_id)
                VALUES (?, ?)
            """, (user_id, agent_id))

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
        conn.close()
        return jsonify({"error": str(e)}), 400


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
        expiration = (datetime.utcnow() + timedelta(hours=int(expires_in_hours))).strftime("%Y-%m-%d %H:%M:%S")
    elif expires_at:
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


@app.route('/api/admin/users/<int:user_id>/access', methods=['PUT'])
@require_admin
def update_user_access(user_id):
    """Update user's city/agent access (admin only)."""
    data = request.get_json() or {}
    city_ids = data.get('city_ids', [])
    agent_ids = data.get('agent_ids', [])

    conn = get_db_connection()
    c = conn.cursor()

    # Clear existing access
    c.execute("DELETE FROM user_city_access WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM user_agent_access WHERE user_id = ?", (user_id,))

    # Set new access
    for city_id in city_ids:
        c.execute("""
            INSERT INTO user_city_access (user_id, city_id)
            VALUES (?, ?)
        """, (user_id, city_id))

    for agent_id in agent_ids:
        c.execute("""
            INSERT INTO user_agent_access (user_id, agent_id)
            VALUES (?, ?)
        """, (user_id, agent_id))

    conn.commit()
    conn.close()

    return jsonify({"status": "access updated"}), 200


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


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
        "inspection_type": "FOUNDATION|FRAMING|ROUGH_MEP|INSULATION|DRYWALL|FINAL",
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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


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
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────
# App Initialization
# ─────────────────────────────────────────────────────────

def create_app():
    """Application factory."""
    init_web_db()
    seed_cities_and_agents()

    # Start the inspection scheduler for automatic calendar updates
    try:
        start_inspection_scheduler()
    except Exception as e:
        logger.warning(f"Failed to start inspection scheduler: {e}")

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)
