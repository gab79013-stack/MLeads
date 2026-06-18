"""
leads_routes.py — Leads API routes
Extracted from app.py by refactor_extract4.py
"""
import logging
import os

from flask import Blueprint, request, jsonify, g

bp = Blueprint('leads_routes', __name__)
logger = logging.getLogger('mleads')


def _get_app_const(name, default=None):
    """Get a constant/function from web.app without requiring eager imports."""
    try:
        import web.app as _app_mod
        return getattr(_app_mod, name, default)
    except Exception:
        return default

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

def get_lead_views():
    """Get user's saved lead filter views."""
    from utils.web_db import get_user_lead_views

    user_id = g.user_id
    views = get_user_lead_views(user_id)

    return jsonify(views), 200

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

def get_preferences():
    """Get user preferences."""
    from utils.web_db import get_user_preferences

    user_id = g.user_id
    prefs = get_user_preferences(user_id)

    return jsonify(prefs), 200

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

def create_payment_checkout():
    """
    Create a Stripe Checkout session for the authenticated web user.
    Body: {"tier": "pro" | "quality" | "premium" | "elite"}
    Returns: {"checkout_url": "https://checkout.stripe.com/..."}
    Requires STRIPE_API_KEY and STRIPE_PRICE_ID_* in env.
    """
    data = request.get_json(silent=True) or {}
    tier = (data.get('tier') or 'pro').lower()
    if tier not in ('pro', 'quality', 'premium', 'elite'):
        return jsonify({"error": "Tier must be 'pro', 'quality', 'premium' or 'elite'"}), 400

    elite_gate, checkout_context = _elite_checkout_guard(tier, data)
    if elite_gate:
        return elite_gate

    stripe_key = os.getenv('STRIPE_API_KEY', '')
    specific_key = f'STRIPE_PRICE_ID_{tier.upper()}'
    curated_tiers = {'quality', 'elite'}
    price_id = (
        os.getenv(specific_key, '')
        if tier in curated_tiers else
        (os.getenv(specific_key) or os.getenv('STRIPE_PRICE_ID') or '')
    )
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

        base_url = _checkout_base_url()
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


def _checkout_base_url() -> str:
    """Resolve Stripe return URLs from production config or the active request host."""
    configured = (os.getenv('BASE_URL') or '').strip()
    if configured:
        return configured.rstrip('/')
    return request.host_url.rstrip('/')


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

    proof_fn = _get_app_const("_elite_sales_proof_payload")
    if not callable(proof_fn):
        return (jsonify({
            "error": "Elite requiere validación de inventario antes de cobrar. Intenta de nuevo en unos minutos.",
            "code": "elite_readiness_unavailable",
            "checkout_allowed": False,
        }), 503), context

    try:
        proof = proof_fn(city, service)
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
    record_fn = _get_app_const("_record_elite_pilot_request")
    if callable(record_fn):
        return bool(record_fn(user_id, city, service, readiness_status, recommended_price, proof))
    return False


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
        # Check if this event is for a web user (has user_id in metadata)
        data_obj  = (event.get('data') or {}).get('object') or {}
        meta      = data_obj.get('metadata') or {}
        web_user_id = meta.get('user_id') or data_obj.get('client_reference_id')

        if web_user_id:
            handled = _handle_web_user_stripe_event(event, web_user_id)
        else:
            handled = billing.handle_event(event)

        return jsonify({"received": True, "handled": handled}), 200
    except Exception as e:
        logger.exception(f"Stripe webhook handler error: {e}")
        return jsonify({"error": "Internal server error"}), 500

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
