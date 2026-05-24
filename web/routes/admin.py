"""
admin_routes.py — Admin API routes
Extracted from app.py by refactor_extract4.py
"""
from flask import Blueprint, request, jsonify

bp = Blueprint('admin_routes', __name__)

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

def bot_users_stats():
    try:
        return jsonify(bu.get_stats()), 200
    except Exception as e:
        return jsonify({"error": "Internal server error"}), 500

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

def delete_feedback(fb_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM beta_feedback WHERE id = ?", (fb_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 200
