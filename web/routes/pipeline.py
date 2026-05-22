"""
pipeline_routes.py — Pipeline API routes
Extracted from app.py by refactor_extract4.py
"""
from flask import Blueprint, request, jsonify

bp = Blueprint('pipeline_routes', __name__)

def api_pipeline():
    """Get user's pipeline leads grouped by status."""
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({s: [] for s in PIPELINE_STATUSES})

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT p.lead_id, p.status, p.notes, p.created_at, p.updated_at,
               l.address, l.city, l.primary_service_type, l.lead_data
        FROM lead_pipeline p
        LEFT JOIN consolidated_leads l ON l.address_key = p.lead_id
        WHERE p.user_id = ?
        ORDER BY p.updated_at DESC
    """, (uid,))

    columns = {s: [] for s in PIPELINE_STATUSES}
    for row in c.fetchall():
        ld = json.loads(row["lead_data"] or "{}")
        lead = {
            "id": row["lead_id"],
            "address": row["address"] or "",
            "city": row["city"] or "",
            
            "status": row["status"],
            "notes": row["notes"] or "",
            "created_at": row["created_at"] or "",
            "updated_at": row["updated_at"] or "",
            "service_type": row["primary_service_type"] or "",
            "trade": ld.get("_trade", ""),
            "contractor": ld.get("contractor", ""),
            "phone": ld.get("contact_phone", ""),
            "value": ld.get("value_float", 0),
            "score": ld.get("_scoring", {}).get("score", 0) if isinstance(ld.get("_scoring"), dict) else 0,
            "urgency": ld.get("_urgency", ""),
            "pain_point": ld.get("_key_pain_point", ""),
            "upsell": ld.get("_upsell_opportunity", ""),
            "best_time": ld.get("_best_contact_time", ""),
            "project_scope": ld.get("_project_scope", ""),
            "decision_maker": ld.get("_decision_maker", ""),
            "ai_summary": ld.get("_ai_summary", ""),
        }
        status = row["status"] if row["status"] in columns else "Nuevo"
        columns[status].append(lead)

    conn.close()
    return jsonify(columns)

def api_pipeline_move():
    """Move a lead to a different pipeline status."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    new_status = data.get("status")

    if not lead_id or not new_status:
        return jsonify({"error": "lead_id and status required"}), 400
    if new_status not in PIPELINE_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(PIPELINE_STATUSES)}"}), 400

    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE lead_pipeline SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ? AND user_id = ?",
        (new_status, lead_id, uid)
    )
    conn.commit()
    ok = c.rowcount > 0
    conn.close()
    return jsonify({"ok": ok})

def api_pipeline_notes():
    """Update notes on a pipeline lead."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    notes = data.get("notes", "")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401

    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE lead_pipeline SET notes = ?, updated_at = CURRENT_TIMESTAMP WHERE lead_id = ? AND user_id = ?",
        (notes, lead_id, uid)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def api_pipeline_remove():
    """Remove a lead from pipeline."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")

    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400

    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM lead_pipeline WHERE lead_id = ? AND user_id = ?", (lead_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def api_pipeline_contact():
    """Log a contact attempt (call, sms, email, in-person)."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    contact_type = data.get("contact_type", "call")
    outcome = data.get("outcome", "")
    notes = data.get("notes", "")
    if not lead_id or contact_type not in ("call", "sms", "email", "visit", "other"):
        return jsonify({"error": "lead_id and valid contact_type required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO lead_contacts_log (lead_id, user_id, contact_type, outcome, notes) VALUES (?, ?, ?, ?, ?)",
              (lead_id, uid, contact_type, outcome, notes))
    # Auto-advance to "Contactado" if still "Nuevo"
    c.execute("UPDATE lead_pipeline SET status='Contactado', updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=? AND status='Nuevo'",
              (lead_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def api_pipeline_followup():
    """Schedule a follow-up reminder."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    due_at = data.get("due_at")  # ISO timestamp
    followup_type = data.get("followup_type", "call")
    notes = data.get("notes", "")
    if not lead_id or not due_at:
        return jsonify({"error": "lead_id and due_at required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO lead_followups (lead_id, user_id, due_at, followup_type, notes) VALUES (?, ?, ?, ?, ?)",
              (lead_id, uid, due_at, followup_type, notes))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def api_pipeline_followups():
    """Get pending follow-ups for user."""
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT f.id, f.lead_id, f.due_at, f.followup_type, f.notes, f.completed,
               l.address, l.city
        FROM lead_followups f
        LEFT JOIN consolidated_leads l ON l.address_key = f.lead_id
        WHERE f.user_id = ? AND f.completed = 0
        ORDER BY f.due_at ASC
    """, (uid,))
    results = []
    for row in c.fetchall():
        results.append({
            "id": row[0], "lead_id": row[1], "due_at": row[2],
            "followup_type": row[3], "notes": row[4], "completed": row[5],
            "address": row[6], "city": row[7],
        })
    conn.close()
    return jsonify(results)

def api_pipeline_followup_complete(fid):
    """Mark a follow-up as completed."""
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE lead_followups SET completed=1, completed_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
              (fid, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

def api_pipeline_estimate():
    """Create an estimate for a lead."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    amount = data.get("amount", 0)
    description = data.get("description", "")
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    # Registration required to send estimates
    if not user_id:
        return jsonify({"error": "registration_required", "message": "Regístrate para enviar estimados"}), 401
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO lead_estimates (lead_id, user_id, amount, description) VALUES (?, ?, ?, ?)",
              (lead_id, uid, float(amount), description))
    # Auto-advance to "Propuesta"
    c.execute("UPDATE lead_pipeline SET status='Propuesta', updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=? AND status IN ('Nuevo','Contactado')",
              (lead_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "amount": float(amount)})

def api_pipeline_close():
    """Close a lead as won or lost with a reason."""
    data = request.get_json() or {}
    lead_id = data.get("lead_id")
    won = data.get("won", False)
    reason = data.get("reason", "")
    if not lead_id:
        return jsonify({"error": "lead_id required"}), 400
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify({"error": "Not authenticated"}), 401
    status = "Ganado" if won else "Perdido"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE lead_pipeline SET status=?, close_reason=?, closed_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE lead_id=? AND user_id=?",
              (status, reason, lead_id, uid))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": status})

def api_pipeline_activity(lead_id):
    """Get full activity timeline for a lead."""
    user_id, anon_id = _resolve_swipe_identity()
    uid = user_id or anon_id
    if not uid:
        return jsonify([])
    conn = get_db_connection()
    c = conn.cursor()
    timeline = []
    # Contact logs
    c.execute("SELECT contact_type, outcome, notes, created_at FROM lead_contacts_log WHERE lead_id=? AND user_id=? ORDER BY created_at DESC", (lead_id, uid))
    for row in c.fetchall():
        timeline.append({"type": "contact", "contact_type": row[0], "outcome": row[1], "notes": row[2], "date": row[3]})
    # Follow-ups
    c.execute("SELECT followup_type, notes, due_at, completed FROM lead_followups WHERE lead_id=? AND user_id=? ORDER BY due_at DESC", (lead_id, uid))
    for row in c.fetchall():
        timeline.append({"type": "followup", "followup_type": row[0], "notes": row[1], "due_at": row[2], "completed": row[3]})
    # Estimates
    c.execute("SELECT amount, description, status, created_at FROM lead_estimates WHERE lead_id=? AND user_id=? ORDER BY created_at DESC", (lead_id, uid))
    for row in c.fetchall():
        timeline.append({"type": "estimate", "amount": row[0], "description": row[1], "estimate_status": row[2], "date": row[3]})
    # Sort by date
    timeline.sort(key=lambda x: x.get("date") or x.get("due_at") or "", reverse=True)
    conn.close()
    return jsonify(timeline)
