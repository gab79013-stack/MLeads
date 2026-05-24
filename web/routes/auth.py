"""
auth_routes.py — Auth API routes
Extracted from app.py by refactor_extract4.py
"""
from flask import Blueprint, request, jsonify

bp = Blueprint('auth_routes', __name__)

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

def logout():
    """Logout and revoke token."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    revoke_token(token)
    return jsonify({"status": "logged out"}), 200

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
