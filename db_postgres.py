"""
db_postgres.py — PostgreSQL database layer for MLeads with multi-tenant support

Replaces SQLite for production. Provides:
- Connection pooling via psycopg2
- Multi-tenant roles (subcontractor, gc, insurance)
- JSONB profile_data per role
- Tripartite scoring (subcontractor_score, gc_score, insurance_score)
- Full schema init + migration from SQLite
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("db_postgres")

# ─── Configuration ────────────────────────────────────────────────────────────

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mleads:mleads@localhost:5432/mleads"
)

# Connection pool (thread-safe)
_pool: Optional[ThreadedConnectionPool] = None


def init_pool(min_conn=2, max_conn=20):
    """Initialize the connection pool."""
    global _pool
    if _pool is None:
        _pool = ThreadedConnectionPool(min_conn, max_conn, DATABASE_URL)
        logger.info(f"PostgreSQL connection pool initialized ({min_conn}-{max_conn} connections)")


def get_conn():
    """Get a connection from the pool."""
    global _pool
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    conn.autocommit = False
    return conn


def put_conn(conn):
    """Return a connection to the pool."""
    global _pool
    if _pool and conn:
        _pool.putconn(conn)


def get_db_connection():
    """SQLite-compatible interface: returns a connection with dict cursor.
    
    Callers using `conn.cursor()` will get a DictCursor by default.
    Close with put_conn() instead of conn.close() for pooling.
    """
    conn = get_conn()
    # Set default cursor factory for dict-like row access
    # Individual cursors can override this
    return conn


# ─── Schema Initialization ───────────────────────────────────────────────────

SCHEMA_SQL = """
-- ============================================================
-- EXTENSIONS
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for fuzzy text search

-- ============================================================
-- ENUMS
-- ============================================================
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('subcontractor', 'gc', 'insurance');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE lead_status AS ENUM ('new', 'contacted', 'pending', 'closed', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE subscription_tier AS ENUM ('free', 'pro', 'premium', 'enterprise');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================
-- USERS (Multi-tenant)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    username        TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    
    -- Multi-tenant role
    role            user_role DEFAULT 'gc',
    profile_data    JSONB DEFAULT '{}',
    
    -- OAuth
    oauth_provider  TEXT,
    oauth_sub       TEXT,
    avatar_url      TEXT,
    
    -- Subscription
    plan_tier       subscription_tier DEFAULT 'free',
    is_active       BOOLEAN DEFAULT TRUE,
    is_paid         BOOLEAN DEFAULT FALSE,
    paid_since      TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    last_login      TIMESTAMPTZ,
    
    -- Limits
    monthly_lead_limit  INTEGER DEFAULT 50,
    current_month_leads INTEGER DEFAULT 0,
    
    -- Notification preferences (JSONB for flexibility)
    notification_prefs JSONB DEFAULT '{
        "sms": true, "email": true, "telegram": true, "lob_postcard": false
    }',
    
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_plan ON users(plan_tier);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active);

-- ============================================================
-- RBAC: ROLES & PERMISSIONS (legacy, still useful for granular access)
-- ============================================================
CREATE TABLE IF NOT EXISTS roles (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS permissions (
    id          SERIAL PRIMARY KEY,
    resource    TEXT NOT NULL,
    action      TEXT NOT NULL,
    description TEXT,
    UNIQUE(resource, action)
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id       INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id INTEGER NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    UNIQUE(role_id, permission_id)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    UNIQUE(user_id, role_id)
);

-- ============================================================
-- CITIES
-- ============================================================
CREATE TABLE IF NOT EXISTS cities (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    state       TEXT DEFAULT 'CA',
    county      TEXT,
    tier_status TEXT DEFAULT 'Emerging',
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cities_state ON cities(state);
CREATE INDEX IF NOT EXISTS idx_cities_county ON cities(county);
CREATE INDEX IF NOT EXISTS idx_cities_tier ON cities(tier_status);

-- ============================================================
-- AGENTS
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id          SERIAL PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SERVICE TYPES
-- ============================================================
CREATE TABLE IF NOT EXISTS service_types (
    id            SERIAL PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    display_label TEXT NOT NULL,
    emoji         TEXT,
    description   TEXT,
    category      TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_types_name ON service_types(name);
CREATE INDEX IF NOT EXISTS idx_service_types_category ON service_types(category);

-- ============================================================
-- USER ACCESS CONTROL
-- ============================================================
CREATE TABLE IF NOT EXISTS user_city_access (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    city_id INTEGER NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
    UNIQUE(user_id, city_id)
);

CREATE TABLE IF NOT EXISTS user_agent_access (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    UNIQUE(user_id, agent_id)
);

-- ============================================================
-- SESSIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS sessions (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL UNIQUE,
    refresh_token TEXT UNIQUE,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(access_token);

-- ============================================================
-- CONSOLIDATED LEADS (with tripartite scoring)
-- ============================================================
CREATE TABLE IF NOT EXISTS consolidated_leads (
    address_key          TEXT PRIMARY KEY,
    address              TEXT NOT NULL,
    city                 TEXT NOT NULL,
    agent_sources        TEXT NOT NULL,
    first_seen           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lead_data            JSONB NOT NULL DEFAULT '{}',
    primary_service_type TEXT,
    has_contact          BOOLEAN DEFAULT FALSE,
    has_phone            BOOLEAN DEFAULT FALSE,
    is_dead_lead         BOOLEAN DEFAULT FALSE,
    notified             BOOLEAN DEFAULT FALSE,
    
    -- Multi-tenant assignment
    assigned_to_gc       INTEGER REFERENCES users(id),
    assigned_to_sub      INTEGER REFERENCES users(id),
    assigned_at          TIMESTAMPTZ,
    
    -- Tripartite scoring (stored separately for filtering/sorting)
    subcontractor_score  SMALLINT DEFAULT 0,
    gc_score             SMALLINT DEFAULT 0,
    insurance_score      SMALLINT DEFAULT 0,
    
    -- Property DNA (extracted from lead_data for indexing)
    property_year_built  SMALLINT,
    property_roof_material TEXT,
    property_value       NUMERIC(12,2),
    property_sqft        INTEGER,
    
    -- Geo
    lat                  DOUBLE PRECISION,
    lon                  DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_leads_city ON consolidated_leads(city);
CREATE INDEX IF NOT EXISTS idx_leads_service ON consolidated_leads(primary_service_type);
CREATE INDEX IF NOT EXISTS idx_leads_city_service ON consolidated_leads(city, primary_service_type);
CREATE INDEX IF NOT EXISTS idx_leads_has_phone ON consolidated_leads(has_phone);
CREATE INDEX IF NOT EXISTS idx_leads_dead ON consolidated_leads(is_dead_lead);
CREATE INDEX IF NOT EXISTS idx_leads_gc_score ON consolidated_leads(gc_score);
CREATE INDEX IF NOT EXISTS idx_leads_sub_score ON consolidated_leads(subcontractor_score);
CREATE INDEX IF NOT EXISTS idx_leads_insurance_score ON consolidated_leads(insurance_score);
CREATE INDEX IF NOT EXISTS idx_leads_assigned_gc ON consolidated_leads(assigned_to_gc);
CREATE INDEX IF NOT EXISTS idx_leads_assigned_sub ON consolidated_leads(assigned_to_sub);
CREATE INDEX IF NOT EXISTS idx_leads_property_year ON consolidated_leads(property_year_built);
CREATE INDEX IF NOT EXISTS idx_leads_latlon ON consolidated_leads(lat, lon) WHERE lat IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leads_data_gin ON consolidated_leads USING gin(lead_data);

-- ============================================================
-- PROPERTY SIGNALS
-- ============================================================
CREATE TABLE IF NOT EXISTS property_signals (
    id          SERIAL PRIMARY KEY,
    address_key TEXT NOT NULL,
    agent_key   TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_data JSONB,
    detected_at TIMESTAMPTZ NOT NULL,
    UNIQUE(address_key, agent_key, signal_type)
);

CREATE INDEX IF NOT EXISTS idx_signals_address ON property_signals(address_key);
CREATE INDEX IF NOT EXISTS idx_signals_agent ON property_signals(agent_key);

-- ============================================================
-- SCHEDULED INSPECTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS scheduled_inspections (
    id                      SERIAL PRIMARY KEY,
    permit_id               TEXT NOT NULL,
    address                 TEXT NOT NULL,
    address_key             TEXT,
    inspection_date         DATE NOT NULL,
    inspection_type         TEXT,
    time_window_start       TEXT,
    time_window_end         TEXT,
    inspector_name          TEXT,
    inspector_id            TEXT,
    jurisdiction            TEXT NOT NULL,
    source_url              TEXT,
    status                  TEXT DEFAULT 'SCHEDULED',
    gc_presence_probability REAL DEFAULT 0.8,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    fetched_at              TIMESTAMPTZ,
    UNIQUE(permit_id, inspection_date, jurisdiction)
);

CREATE INDEX IF NOT EXISTS idx_inspections_permit ON scheduled_inspections(permit_id);
CREATE INDEX IF NOT EXISTS idx_inspections_address ON scheduled_inspections(address_key);
CREATE INDEX IF NOT EXISTS idx_inspections_date ON scheduled_inspections(inspection_date);
CREATE INDEX IF NOT EXISTS idx_inspections_jurisdiction ON scheduled_inspections(jurisdiction);
CREATE INDEX IF NOT EXISTS idx_inspections_jur_date ON scheduled_inspections(jurisdiction, inspection_date);

-- ============================================================
-- LEAD CONTACTS & NOTES
-- ============================================================
CREATE TABLE IF NOT EXISTS lead_contacts (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lead_id      TEXT NOT NULL,
    contact_type TEXT,
    notes        TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lead_contacts_user ON lead_contacts(user_id);
CREATE INDEX IF NOT EXISTS idx_lead_contacts_lead ON lead_contacts(lead_id);

CREATE TABLE IF NOT EXISTS lead_notes (
    id         SERIAL PRIMARY KEY,
    lead_id    TEXT NOT NULL,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note       TEXT NOT NULL,
    is_deleted BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- AUDIT LOGS
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id             SERIAL PRIMARY KEY,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action         TEXT NOT NULL,
    resource_type  TEXT,
    resource_id    TEXT,
    details        TEXT,
    ip_address     INET,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at);

-- ============================================================
-- SWIPE ACTIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS swipe_actions (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,
    anon_id    TEXT,
    lead_id    TEXT NOT NULL,
    action     TEXT NOT NULL CHECK(action IN ('like','dislike')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_swipe_user ON swipe_actions(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_swipe_anon ON swipe_actions(anon_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_swipe_user_lead ON swipe_actions(user_id, lead_id) WHERE user_id IS NOT NULL;

-- ============================================================
-- BOT USERS (Telegram)
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_users (
    id                     SERIAL PRIMARY KEY,
    chat_id                TEXT UNIQUE NOT NULL,
    telegram_user_id       TEXT,
    username               TEXT,
    first_name             TEXT,
    last_name              TEXT,
    state                  TEXT DEFAULT 'new',
    services               JSONB DEFAULT '[]',
    city                   TEXT,
    latitude               DOUBLE PRECISION,
    longitude              DOUBLE PRECISION,
    radius_miles           INTEGER DEFAULT 35,
    subscription_status    TEXT DEFAULT 'none',
    trial_started_at       TIMESTAMPTZ,
    trial_ends_at          TIMESTAMPTZ,
    paid_until             TIMESTAMPTZ,
    stripe_customer_id     TEXT,
    stripe_subscription_id TEXT,
    joined_channel_at      TIMESTAMPTZ,
    is_active              BOOLEAN DEFAULT TRUE,
    leads_sent_count       INTEGER DEFAULT 0,
    last_lead_at           TIMESTAMPTZ,
    created_at             TIMESTAMPTZ DEFAULT NOW(),
    updated_at             TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_users_chat ON bot_users(chat_id);
CREATE INDEX IF NOT EXISTS idx_bot_users_state ON bot_users(state);
CREATE INDEX IF NOT EXISTS idx_bot_users_subscription ON bot_users(subscription_status);
CREATE INDEX IF NOT EXISTS idx_bot_users_active ON bot_users(is_active);

CREATE TABLE IF NOT EXISTS bot_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS bot_messages (
    id            SERIAL PRIMARY KEY,
    bot_user_id   INTEGER NOT NULL REFERENCES bot_users(id) ON DELETE CASCADE,
    chat_id       TEXT NOT NULL,
    direction     TEXT NOT NULL,
    message_type  TEXT,
    lead_id       TEXT,
    text          TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_messages_user ON bot_messages(bot_user_id);
CREATE INDEX IF NOT EXISTS idx_bot_messages_created ON bot_messages(created_at);

-- ============================================================
-- USER PREFERENCES & SETTINGS
-- ============================================================
CREATE TABLE IF NOT EXISTS user_preferences (
    id                   SERIAL PRIMARY KEY,
    user_id              INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    theme                TEXT DEFAULT 'light',
    notifications_enabled BOOLEAN DEFAULT TRUE,
    notify_new_leads     BOOLEAN DEFAULT TRUE,
    notify_inspections   BOOLEAN DEFAULT FALSE,
    notify_frequency     TEXT DEFAULT 'daily',
    email_digest         BOOLEAN DEFAULT TRUE,
    items_per_page       INTEGER DEFAULT 100,
    default_sort         TEXT DEFAULT 'last_updated',
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- SAVED LEAD VIEWS
-- ============================================================
CREATE TABLE IF NOT EXISTS lead_views (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    filters     JSONB NOT NULL,
    is_default  BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(user_id, name)
);

-- ============================================================
-- BULK OPERATIONS
-- ============================================================
CREATE TABLE IF NOT EXISTS bulk_operations (
    id               SERIAL PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    operation_type   TEXT NOT NULL,
    status           TEXT DEFAULT 'pending',
    total_items      INTEGER,
    processed_items  INTEGER DEFAULT 0,
    payload          JSONB NOT NULL,
    result           JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    completed_at     TIMESTAMPTZ
);

-- ============================================================
-- EXPORT LOGS
-- ============================================================
CREATE TABLE IF NOT EXISTS export_logs (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    export_name     TEXT,
    columns         JSONB NOT NULL,
    filter_criteria JSONB,
    record_count    INTEGER,
    file_path       TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- ACTIVITY FEED
-- ============================================================
CREATE TABLE IF NOT EXISTS activity_feed (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action_type  TEXT NOT NULL,
    target_id    TEXT,
    target_type  TEXT,
    description  TEXT,
    details      JSONB,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_feed(user_id);
CREATE INDEX IF NOT EXISTS idx_activity_created ON activity_feed(created_at);

-- ============================================================
-- BETA FEEDBACK
-- ============================================================
CREATE TABLE IF NOT EXISTS beta_feedback (
    id         SERIAL PRIMARY KEY,
    message    TEXT NOT NULL,
    anon_id    TEXT,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_beta_feedback_created ON beta_feedback(created_at);

-- ============================================================
-- DISASTER EVENTS (new — for Disaster Intelligence)
-- ============================================================
CREATE TABLE IF NOT EXISTS disaster_events (
    id              SERIAL PRIMARY KEY,
    event_type      TEXT NOT NULL,  -- hail, flood, wildfire, wind, tornado, earthquake
    source          TEXT NOT NULL,  -- noaa, fema, nasa_firms, open-meteo
    source_id       TEXT,           -- ID from source API
    severity        TEXT DEFAULT 'moderate',  -- minor, moderate, severe, catastrophic
    description     TEXT,
    affected_cities JSONB DEFAULT '[]',
    affected_zip_codes JSONB DEFAULT '[]',
    geometry        JSONB,          -- GeoJSON polygon or point
    started_at      TIMESTAMPTZ NOT NULL,
    ended_at        TIMESTAMPTZ,
    raw_data        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_disaster_type ON disaster_events(event_type);
CREATE INDEX IF NOT EXISTS idx_disaster_started ON disaster_events(started_at);
CREATE INDEX IF NOT EXISTS idx_disaster_source ON disaster_events(source, source_id);

-- ============================================================
-- LEAD-DISASTER LINKS (which leads are affected by which events)
-- ============================================================
CREATE TABLE IF NOT EXISTS lead_disaster_links (
    id              SERIAL PRIMARY KEY,
    lead_id         TEXT NOT NULL REFERENCES consolidated_leads(address_key) ON DELETE CASCADE,
    disaster_id     INTEGER NOT NULL REFERENCES disaster_events(id) ON DELETE CASCADE,
    impact_score    SMALLINT DEFAULT 50,  -- 0-100 how likely this lead needs work from this disaster
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(lead_id, disaster_id)
);

CREATE INDEX IF NOT EXISTS idx_ldl_lead ON lead_disaster_links(lead_id);
CREATE INDEX IF NOT EXISTS idx_ldl_disaster ON lead_disaster_links(disaster_id);
"""


def init_postgres_db():
    """Initialize the PostgreSQL schema. Safe to run multiple times (IF NOT EXISTS)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        conn.commit()
        logger.info("PostgreSQL schema initialized successfully")
        
        # Seed default data
        _seed_defaults(conn)
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to initialize PostgreSQL schema: {e}")
        raise
    finally:
        put_conn(conn)


def _seed_defaults(conn):
    """Insert default roles, permissions, and role-permission mappings."""
    with conn.cursor() as cur:
        # Default roles
        roles = [
            ('admin', 'Full access to all features and user management'),
            ('manager', 'Can view all leads, manage team members'),
            ('user', 'Can view leads filtered by city/agent, contact leads'),
            ('viewer', 'Read-only access to leads'),
        ]
        for name, desc in roles:
            cur.execute(
                "INSERT INTO roles (name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                (name, desc)
            )

        # Default permissions
        perms = [
            ('leads', 'view', 'View leads'),
            ('leads', 'filter', 'Filter leads by city/agent'),
            ('leads', 'contact', 'Log contact with lead'),
            ('users', 'create', 'Create new users'),
            ('users', 'edit', 'Edit users'),
            ('users', 'delete', 'Delete users'),
            ('users', 'manage_roles', 'Assign roles to users'),
            ('users', 'manage_access', 'Restrict city/agent access'),
            ('roles', 'view', 'View roles'),
            ('roles', 'create', 'Create roles'),
            ('roles', 'edit', 'Edit roles'),
            ('audit', 'view', 'View audit logs'),
        ]
        for resource, action, desc in perms:
            cur.execute(
                "INSERT INTO permissions (resource, action, description) VALUES (%s, %s, %s) ON CONFLICT (resource, action) DO NOTHING",
                (resource, action, desc)
            )

        # Role-permission mappings
        role_perms = {
            'admin': [('leads','view'),('leads','filter'),('leads','contact'),
                      ('users','create'),('users','edit'),('users','delete'),
                      ('users','manage_roles'),('users','manage_access'),
                      ('roles','view'),('roles','create'),('roles','edit'),('audit','view')],
            'manager': [('leads','view'),('leads','filter'),('leads','contact'),('audit','view')],
            'user': [('leads','view'),('leads','filter'),('leads','contact')],
            'viewer': [('leads','view'),('leads','filter')],
        }
        for role_name, perm_list in role_perms.items():
            cur.execute("SELECT id FROM roles WHERE name = %s", (role_name,))
            role_row = cur.fetchone()
            if not role_row:
                continue
            role_id = role_row[0]
            for resource, action in perm_list:
                cur.execute("SELECT id FROM permissions WHERE resource = %s AND action = %s", (resource, action))
                perm_row = cur.fetchone()
                if perm_row:
                    cur.execute(
                        "INSERT INTO role_permissions (role_id, permission_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (role_id, perm_row[0])
                    )


# ─── SQLite → PostgreSQL Migration ──────────────────────────────────────────

def migrate_from_sqlite(sqlite_path: str = "data/leads.db", batch_size: int = 500):
    """Migrate all data from SQLite to PostgreSQL.
    
    Run this ONCE to port existing data. The PostgreSQL schema must be initialized first.
    """
    import sqlite3
    
    if not os.path.exists(sqlite_path):
        logger.error(f"SQLite database not found: {sqlite_path}")
        return False
    
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    
    pg_conn = get_conn()
    migrated_tables = []
    
    try:
        with pg_conn.cursor() as cur:
            # ── Users ─────────────────────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM users").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO users (id, username, email, password_hash, full_name,
                            is_active, expires_at, oauth_provider, oauth_sub, avatar_url,
                            is_paid, paid_since, last_login, created_at, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username) DO NOTHING
                    """, (
                        r.get('id'), r.get('username'), r.get('email'),
                        r.get('password_hash'), r.get('full_name'),
                        bool(r.get('is_active', 1)), r.get('expires_at') or None,
                        r.get('oauth_provider') or None, r.get('oauth_sub') or None, r.get('avatar_url') or None,
                        bool(r.get('is_paid', 0)), r.get('paid_since') or None,
                        r.get('last_login') or None, r.get('created_at'), r.get('updated_at'),
                    ))
                migrated_tables.append(f"users ({len(rows)} rows)")
            except Exception as e:
                logger.warning(f"Skipping users migration: {e}")
            
            # Reset the PostgreSQL serial sequence
            cur.execute("SELECT setval('users_id_seq', (SELECT COALESCE(MAX(id), 1) FROM users))")
            
            # ── Cities ────────────────────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM cities").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO cities (id, name, state, county, tier_status, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (name) DO NOTHING
                    """, (r.get('id'), r.get('name'), r.get('state', 'CA'),
                          r.get('county'), r.get('tier_status', 'Emerging'), r.get('created_at')))
                migrated_tables.append(f"cities ({len(rows)} rows)")
                cur.execute("SELECT setval('cities_id_seq', (SELECT COALESCE(MAX(id), 1) FROM cities))")
            except Exception as e:
                logger.warning(f"Skipping cities migration: {e}")
            
            # ── Agents ────────────────────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM agents").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO agents (id, name, description, created_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (name) DO NOTHING
                    """, (r.get('id'), r.get('name'), r.get('description'), r.get('created_at')))
                migrated_tables.append(f"agents ({len(rows)} rows)")
                cur.execute("SELECT setval('agents_id_seq', (SELECT COALESCE(MAX(id), 1) FROM agents))")
            except Exception as e:
                logger.warning(f"Skipping agents migration: {e}")
            
            # ── Service Types ─────────────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM service_types").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO service_types (id, name, display_label, emoji, description, category, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (name) DO NOTHING
                    """, (r.get('id'), r.get('name'), r.get('display_label'),
                          r.get('emoji'), r.get('description'), r.get('category'), r.get('created_at')))
                migrated_tables.append(f"service_types ({len(rows)} rows)")
            except Exception as e:
                logger.warning(f"Skipping service_types migration: {e}")
            
            # ── Consolidated Leads (the big one — batched) ────────
            try:
                total = sqlite_conn.execute("SELECT COUNT(*) FROM consolidated_leads").fetchone()[0]
                logger.info(f"Migrating {total} consolidated leads in batches of {batch_size}...")
                offset = 0
                count = 0
                while offset < total:
                    rows = sqlite_conn.execute(
                        f"SELECT * FROM consolidated_leads LIMIT {batch_size} OFFSET {offset}"
                    ).fetchall()
                    for row in rows:
                        r = dict(row)
                        # Parse lead_data to extract scoring
                        lead_data = {}
                        try:
                            lead_data = json.loads(r.get('lead_data', '{}') or '{}')
                        except:
                            pass
                        scoring = lead_data.get('_scoring', {})
                        
                        cur.execute("""
                            INSERT INTO consolidated_leads (
                                address_key, address, city, agent_sources,
                                first_seen, last_updated, lead_data,
                                primary_service_type, has_contact, has_phone,
                                is_dead_lead, notified, lat, lon
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (address_key) DO NOTHING
                        """, (
                            r.get('address_key'), r.get('address'), r.get('city'),
                            r.get('agent_sources'), r.get('first_seen'), r.get('last_updated'),
                            json.dumps(lead_data),  # Store as JSONB
                            r.get('primary_service_type'),
                            bool(r.get('has_contact', 0)),
                            bool(r.get('has_phone', 0)),
                            bool(r.get('is_dead_lead', 0)),
                            bool(r.get('notified', 0)),
                            lead_data.get('lat'), lead_data.get('lon'),
                        ))
                        count += 1
                    offset += batch_size
                    logger.info(f"  Migrated {count}/{total} leads...")
                migrated_tables.append(f"consolidated_leads ({count} rows)")
            except Exception as e:
                logger.warning(f"Skipping consolidated_leads migration: {e}")
            
            # ── Scheduled Inspections ─────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM scheduled_inspections").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO scheduled_inspections (
                            permit_id, address, address_key, inspection_date,
                            inspection_type, time_window_start, time_window_end,
                            inspector_name, inspector_id, jurisdiction, source_url,
                            status, gc_presence_probability, created_at, updated_at, fetched_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (permit_id, inspection_date, jurisdiction) DO NOTHING
                    """, (
                        r.get('permit_id'), r.get('address'), r.get('address_key'),
                        r.get('inspection_date'), r.get('inspection_type'),
                        r.get('time_window_start'), r.get('time_window_end'),
                        r.get('inspector_name'), r.get('inspector_id'),
                        r.get('jurisdiction'), r.get('source_url'), r.get('status'),
                        r.get('gc_presence_probability', 0.8),
                        r.get('created_at'), r.get('updated_at'), r.get('fetched_at'),
                    ))
                migrated_tables.append(f"scheduled_inspections ({len(rows)} rows)")
            except Exception as e:
                logger.warning(f"Skipping scheduled_inspections migration: {e}")
            
            # ── Bot Users ─────────────────────────────────────────
            try:
                rows = sqlite_conn.execute("SELECT * FROM bot_users").fetchall()
                for row in rows:
                    r = dict(row)
                    cur.execute("""
                        INSERT INTO bot_users (
                            chat_id, telegram_user_id, username, first_name, last_name,
                            state, services, city, latitude, longitude, radius_miles,
                            subscription_status, trial_started_at, trial_ends_at, paid_until,
                            stripe_customer_id, stripe_subscription_id, joined_channel_at,
                            is_active, leads_sent_count, last_lead_at, created_at, updated_at
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (chat_id) DO NOTHING
                    """, (
                        r.get('chat_id'), r.get('telegram_user_id'), r.get('username'),
                        r.get('first_name'), r.get('last_name'), r.get('state'),
                        r.get('services', '[]'), r.get('city'),
                        r.get('latitude'), r.get('longitude'), r.get('radius_miles', 35),
                        r.get('subscription_status', 'none'),
                        r.get('trial_started_at'), r.get('trial_ends_at'), r.get('paid_until'),
                        r.get('stripe_customer_id'), r.get('stripe_subscription_id'),
                        r.get('joined_channel_at'),
                        bool(r.get('is_active', 1)), r.get('leads_sent_count', 0),
                        r.get('last_lead_at'), r.get('created_at'), r.get('updated_at'),
                    ))
                migrated_tables.append(f"bot_users ({len(rows)} rows)")
            except Exception as e:
                logger.warning(f"Skipping bot_users migration: {e}")
            
            # ── Remaining tables (generic batch) ──────────────────
            for table in ['roles', 'permissions', 'role_permissions', 'user_roles',
                          'user_city_access', 'user_agent_access', 'sessions',
                          'audit_logs', 'lead_contacts', 'lead_notes',
                          'property_signals', 'user_preferences', 'lead_views',
                          'bulk_operations', 'export_logs', 'activity_feed',
                          'bot_state', 'bot_messages', 'beta_feedback',
                          'swipe_actions']:
                try:
                    rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
                    if not rows:
                        continue
                    cols = rows[0].keys()
                    placeholders = ', '.join(['%s'] * len(cols))
                    col_names = ', '.join(cols)
                    for row in rows:
                        values = [row[k] for k in cols]
                        # Use ON CONFLICT DO NOTHING for safety
                        cur.execute(
                            f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                            values
                        )
                    migrated_tables.append(f"{table} ({len(rows)} rows)")
                except Exception as e:
                    logger.debug(f"Skipping {table} migration: {e}")
        
        pg_conn.commit()
        logger.info(f"Migration complete! Migrated: {', '.join(migrated_tables)}")
        return True
        
    except Exception as e:
        pg_conn.rollback()
        logger.error(f"Migration failed: {e}")
        raise
    finally:
        sqlite_conn.close()
        put_conn(pg_conn)


# ─── Compatibility Layer ─────────────────────────────────────────────────────
# These functions provide a SQLite-compatible interface so existing code
# (app.py, web_db.py callers) can work with minimal changes.

def get_db_connection_compat():
    """Return a connection that behaves like the old SQLite one.
    
    Returns a connection wrapped with a DictCursor and a custom .close()
    that returns the connection to the pool instead of closing it.
    """
    conn = get_conn()
    # Store original close
    _original_close = conn.close
    
    class CompatConnection:
        """Wrapper that provides sqlite3.Row-like dict access."""
        
        def __init__(self, pg_conn):
            self._conn = pg_conn
        
        def cursor(self, *args, **kwargs):
            kwargs.setdefault('cursor_factory', psycopg2.extras.RealDictCursor)
            return self._conn.cursor(*args, **kwargs)
        
        def commit(self):
            self._conn.commit()
        
        def rollback(self):
            self._conn.rollback()
        
        def close(self):
            # Return to pool instead of closing
            put_conn(self._conn)
        
        def execute(self, query, params=None):
            with self._conn.cursor() as cur:
                cur.execute(query, params)
        
        @property
        def row_factory(self):
            return None  # Not needed with RealDictCursor
        
        @row_factory.setter
        def row_factory(self, value):
            pass  # Ignore — RealDictCursor handles this