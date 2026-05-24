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
from flask import Flask, request, jsonify, g, send_file, render_template
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

# ─────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Authentication Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# User Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Leads Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Dashboard Stats
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Audit Log
# ─────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────
# Scheduled Inspections Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Inspection Scheduler Admin Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Admin - Users Management Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Admin - Reference Data Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Leads - Notes & Contact History
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Saved Lead Views Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Settings & Preferences Endpoints
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Bot Users Admin API (Phase 3 — Telegram bot users)
# ─────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────
# Stripe — checkout + webhook
# ─────────────────────────────────────────────────────────

def _handle_web_user_stripe_event(event: dict, web_user_id: str) -> bool:
    """Apply a Stripe event to the web app users table."""
    event_type = event.get('type', '')
    data_obj   = (event.get('data') or {}).get('object') or {}

    conn = get_db_connection()
    c = conn.cursor()
    try:
        if event_type in ('checkout.session.completed', 'invoice.paid', 'invoice.payment_succeeded'):
            stripe_customer = data_obj.get('customer')
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
                       paid_since = COALESCE(paid_since, CURRENT_TIMESTAMP),
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
            """, (int(web_user_id),))
            conn.commit()
            logger.info(f"[webhook] web user {web_user_id} marked paid until {paid_until}")
            return True

        if event_type in ('customer.subscription.deleted', 'customer.subscription.paused'):
            c.execute("""
                UPDATE users
                   SET is_paid = 0,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
            """, (int(web_user_id),))
            conn.commit()
            logger.info(f"[webhook] web user {web_user_id} subscription ended")
            return True
    finally:
        conn.close()

    return False


# ─────────────────────────────────────────────────────────
# Public Swipe Endpoints (Tinder-style UX)
# ─────────────────────────────────────────────────────────

# Anonymous visitors can view up to this many leads before being asked
# to log in with Google or Facebook.
ANON_LEAD_LIMIT = int(os.getenv("SWIPE_ANON_LIMIT", "9999"))
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

def _geo_locate_ip(ip: str) -> tuple[float, float] | None:
    """Geolocate an IP address using ip-api.com (free, no key, 45 req/min)."""
    if not ip or ip in ("127.0.0.1", "localhost", "::1") or ip.startswith("10.") or ip.startswith("192.168."):
        return None
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://ip-api.com/json/{ip}?fields=status,lat,lon", timeout=3)
        data = json.loads(resp.read())
        if data.get("status") == "success":
            return (float(data["lat"]), float(data["lon"]))
    except Exception:
        pass
    return None

# Cache IP geolocations to avoid repeated API calls
_IP_GEO_CACHE: dict[str, tuple[float, float] | None] = {}

def _get_ip_geo(ip: str) -> tuple[float, float] | None:
    """Get IP geolocation with caching."""
    if ip in _IP_GEO_CACHE:
        return _IP_GEO_CACHE[ip]
    result = _geo_locate_ip(ip)
    _IP_GEO_CACHE[ip] = result
    # Evict cache if too large
    if len(_IP_GEO_CACHE) > 500:
        _IP_GEO_CACHE.pop(next(iter(_IP_GEO_CACHE)))
    return result



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
_SERVICE_TYPE_CATS = {"solar", "permits", "construction", "realestate", "flood", "energy", "rodents", "deconstruction", "remodel", "crossdata", "plumbing", "hvac", "paint", "flooring_concrete"}

# AI Trade classification map (DeepSeek V3.2)
_AI_TRADE_MAP = {
    "ROOFING": "roofing", "ELECTRICAL": "electrical", "DRYWALL": "drywall",
    "PAINTING": "paint", "LANDSCAPING": "landscaping", "HVAC": "hvac",
    "PLUMBING": "plumbing", "INSULATION": "insulation", "FRAMING": "framing",
    "CONCRETE": "concrete", "FLOORING": "flooring", "WINDOWS": "windows",
    "DEMOLITION": "demolition", "GENERAL": "general", "UNKNOWN": "unknown",
}


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


# ── Pipeline API ───────────────────────────────────────────────────────────────

PIPELINE_STATUSES = ["Nuevo", "Contactado", "Propuesta", "Negociación", "Ganado", "Perdido"]

# ── Pipeline v2 API — Contact Log, Follow-ups, Estimates ──────────────────────

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


    # ── Modular API Blueprints ────────────────────────────────
    from web.routes import auth, leads, swipe, pipeline, admin, ai, static_pages
    app.register_blueprint(auth.bp, url_prefix="/api")
    app.register_blueprint(leads.bp, url_prefix="/api")
    app.register_blueprint(swipe.bp, url_prefix="/api")
    app.register_blueprint(pipeline.bp, url_prefix="/api")
    app.register_blueprint(admin.bp, url_prefix="/api")
    app.register_blueprint(ai.bp, url_prefix="/api")
    app.register_blueprint(static_pages.bp)


    # -- Smart Outreach API (Vultr AI) --
    from web.routes.outreach import bp as outreach_bp
    app.register_blueprint(outreach_bp, url_prefix="/api")

    return app


# ── CrossData Prediction endpoints ────────────────────────────────────────────

if __name__ == '__main__':
    app = create_app()
    app.run(debug=True, host='0.0.0.0', port=5000)


# ═══════════════════════════════════════════════════════════════════
# Disaster Intelligence Endpoints
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# Property DNA Endpoints
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# Tripartite Scoring Endpoints
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# Multi-Tenant Lead Assignment Endpoints
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# CSLB License Verification Endpoints
# ═══════════════════════════════════════════════════════════════════

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

# ─────────────────────────────────────────────────────────
# 0brix Assistant Routes (Claude-like)
# ─────────────────────────────────────────────────────────

from assistant import Assistant

_assistant_instance = None

def get_assistant():
    global _assistant_instance
    if _assistant_instance is None:
        _assistant_instance = Assistant()
    return _assistant_instance

@app.route('/api/assistant/query', methods=['POST'])
def assistant_query():
    """Consulta al asistente con lenguaje natural."""
    from flask import request, jsonify
    
    data = request.json or {}
    query = data.get('query', '')
    user_id = data.get('user_id')
    filters = data.get('filters', {})
    
    if not query:
        return jsonify({"error": "Query is required"}), 400
    
    try:
        result = get_assistant().query(query, user_id, filters)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Assistant error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/assistant/stats', methods=['GET'])
def assistant_stats():
    """Estadísticas rápidas del dashboard."""
    from flask import request, jsonify
    user_id = request.args.get('user_id', type=int)
    stats = get_assistant().get_quick_stats(user_id)
    return jsonify(stats)

@app.route('/api/assistant/quick-actions', methods=['GET'])
def quick_actions():
    """Acciones rápidas predefinidas."""
    from flask import jsonify
    actions = [
        {"id": "pipeline", "label": "Resumen del Pipeline", "icon": "📋", "query": "¿Cómo está mi pipeline?"},
        {"id": "top_leads", "label": "Mejores Leads", "icon": "⭐", "query": "Muéstrame los mejores leads"},
        {"id": "trades", "label": "Análisis por Trade", "icon": "🏗️", "query": "¿Cómo están los leads por trade?"},
        {"id": "cities", "label": "Por Ciudad", "icon": "🏙️", "query": "¿Cuántos leads hay por ciudad?"},
        {"id": "recent", "label": "Leads Recientes", "icon": "🕐", "query": "¿Qué leads nuevos hay esta semana?"}
    ]
    return jsonify({"quick_actions": actions})
