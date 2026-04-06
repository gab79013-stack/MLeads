"""
web_db.py — Database schema for multi-user dashboard

Extends the existing leads.db with tables for:
- User management (users, roles, permissions)
- City/agent access control
- Session management
- Audit logging
"""

import sqlite3
import os
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "data/leads.db")


def init_web_db():
    """Initialize web dashboard schema (runs once on app startup)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ─────────────────────────────────────────────────────
    # Users & Authentication
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT,
            is_active BOOLEAN DEFAULT 1,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Migration: add expires_at column if table already exists without it
    try:
        c.execute("SELECT expires_at FROM users LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE users ADD COLUMN expires_at TIMESTAMP")

    # ─────────────────────────────────────────────────────
    # Roles & Permissions
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            resource TEXT NOT NULL,
            action TEXT NOT NULL,
            description TEXT,
            UNIQUE(resource, action)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS role_permissions (
            role_id INTEGER NOT NULL,
            permission_id INTEGER NOT NULL,
            FOREIGN KEY(role_id) REFERENCES roles(id),
            FOREIGN KEY(permission_id) REFERENCES permissions(id),
            UNIQUE(role_id, permission_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_roles (
            user_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(role_id) REFERENCES roles(id),
            UNIQUE(user_id, role_id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # City & Agent Access Control
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            state TEXT DEFAULT 'CA',
            county TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_city_access (
            user_id INTEGER NOT NULL,
            city_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(city_id) REFERENCES cities(id),
            UNIQUE(user_id, city_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_access (
            user_id INTEGER NOT NULL,
            agent_id INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(agent_id) REFERENCES agents(id),
            UNIQUE(user_id, agent_id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Sessions & Auth Tokens
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            access_token TEXT NOT NULL UNIQUE,
            refresh_token TEXT UNIQUE,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Audit Logging
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            resource_type TEXT,
            resource_id TEXT,
            details TEXT,
            ip_address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Lead Contact Log (user interactions)
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS lead_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            lead_id TEXT NOT NULL,
            contact_type TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Lead Notes (internal notes on leads)
    # ─────────────────────────────────────────────────────
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

    # ─────────────────────────────────────────────────────
    # Scheduled Inspections (public calendar data & predictions)
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            permit_id TEXT NOT NULL,
            address TEXT NOT NULL,
            address_key TEXT,
            inspection_date DATE NOT NULL,
            inspection_type TEXT,
            time_window_start TEXT,
            time_window_end TEXT,
            inspector_name TEXT,
            inspector_id TEXT,
            jurisdiction TEXT NOT NULL,
            source_url TEXT,
            status TEXT DEFAULT 'SCHEDULED',
            gc_presence_probability REAL DEFAULT 0.8,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            fetched_at TIMESTAMP,
            UNIQUE(permit_id, inspection_date, jurisdiction)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Consolidated Leads & Property Signals
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS consolidated_leads (
            address_key TEXT PRIMARY KEY,
            address TEXT NOT NULL,
            city TEXT NOT NULL,
            agent_sources TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_updated TEXT NOT NULL,
            lead_data TEXT NOT NULL,
            notified INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS property_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            address_key TEXT NOT NULL,
            agent_key TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            signal_data TEXT,
            detected_at TEXT NOT NULL,
            UNIQUE(address_key, agent_key, signal_type)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Phase 2: User Preferences & Settings
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            theme TEXT DEFAULT 'light',
            notifications_enabled BOOLEAN DEFAULT 1,
            notify_new_leads BOOLEAN DEFAULT 1,
            notify_inspections BOOLEAN DEFAULT 0,
            notify_frequency TEXT DEFAULT 'daily',
            email_digest BOOLEAN DEFAULT 1,
            items_per_page INTEGER DEFAULT 100,
            default_sort TEXT DEFAULT 'last_updated',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Phase 2: Saved Lead Views (filter templates)
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS lead_views (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            filters TEXT NOT NULL,
            is_default BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Phase 2: Bulk Operations tracking
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS bulk_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            total_items INTEGER,
            processed_items INTEGER DEFAULT 0,
            payload TEXT NOT NULL,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Phase 2: Export logs
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS export_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            export_name TEXT,
            columns TEXT NOT NULL,
            filter_criteria TEXT,
            record_count INTEGER,
            file_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Phase 2: Activity Feed (comprehensive logging)
    # ─────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS activity_feed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_type TEXT NOT NULL,
            target_id TEXT,
            target_type TEXT,
            description TEXT,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    # ─────────────────────────────────────────────────────
    # Alter existing tables for Phase 2
    # ─────────────────────────────────────────────────────

    # Add last_login to users
    try:
        c.execute("SELECT last_login FROM users LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE users ADD COLUMN last_login TIMESTAMP")

    # Add updated_at and is_deleted to lead_notes
    try:
        c.execute("SELECT updated_at FROM lead_notes LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE lead_notes ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

    try:
        c.execute("SELECT is_deleted FROM lead_notes LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE lead_notes ADD COLUMN is_deleted BOOLEAN DEFAULT 0")

    # ─────────────────────────────────────────────────────
    # Create indexes for performance
    # ─────────────────────────────────────────────────────
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_roles ON user_roles(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_city ON user_city_access(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_agent ON user_agent_access(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(access_token)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lead_contacts_user ON lead_contacts(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_inspections_permit ON scheduled_inspections(permit_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_inspections_address ON scheduled_inspections(address_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_inspections_date ON scheduled_inspections(inspection_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_inspections_jurisdiction ON scheduled_inspections(jurisdiction)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_inspections_jurisdiction_date ON scheduled_inspections(jurisdiction, inspection_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_consolidated_leads_city ON consolidated_leads(city)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_property_signals_address ON property_signals(address_key)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_property_signals_agent ON property_signals(agent_key)")

    # Phase 2 indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_user_preferences_user_id ON user_preferences(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_lead_views_user_id ON lead_views(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bulk_operations_user_id ON bulk_operations(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_bulk_operations_status ON bulk_operations(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_feed_user_id ON activity_feed(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_activity_feed_created_at ON activity_feed(created_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_export_logs_user_id ON export_logs(user_id)")

    # ─────────────────────────────────────────────────────
    # Insert default roles
    # ─────────────────────────────────────────────────────
    default_roles = [
        ("admin", "Full access to all features and user management"),
        ("manager", "Can view all leads, manage team members"),
        ("user", "Can view leads filtered by city/agent, contact leads"),
        ("viewer", "Read-only access to leads"),
    ]

    for role_name, description in default_roles:
        c.execute(
            "INSERT OR IGNORE INTO roles (name, description) VALUES (?, ?)",
            (role_name, description),
        )

    # ─────────────────────────────────────────────────────
    # Insert default permissions
    # ─────────────────────────────────────────────────────
    default_permissions = [
        # Lead permissions
        ("leads", "view", "View leads"),
        ("leads", "filter", "Filter leads by city/agent"),
        ("leads", "contact", "Log contact with lead"),
        # User management
        ("users", "create", "Create new users"),
        ("users", "edit", "Edit users"),
        ("users", "delete", "Delete users"),
        ("users", "manage_roles", "Assign roles to users"),
        ("users", "manage_access", "Restrict city/agent access"),
        # Role management
        ("roles", "view", "View roles"),
        ("roles", "create", "Create roles"),
        ("roles", "edit", "Edit roles"),
        # Audit
        ("audit", "view", "View audit logs"),
    ]

    for resource, action, description in default_permissions:
        c.execute(
            "INSERT OR IGNORE INTO permissions (resource, action, description) VALUES (?, ?, ?)",
            (resource, action, description),
        )

    # ─────────────────────────────────────────────────────
    # Assign permissions to roles
    # ─────────────────────────────────────────────────────
    role_perm_map = {
        "admin": [
            ("leads", "view"),
            ("leads", "filter"),
            ("leads", "contact"),
            ("users", "create"),
            ("users", "edit"),
            ("users", "delete"),
            ("users", "manage_roles"),
            ("users", "manage_access"),
            ("roles", "view"),
            ("roles", "create"),
            ("roles", "edit"),
            ("audit", "view"),
        ],
        "manager": [
            ("leads", "view"),
            ("leads", "filter"),
            ("leads", "contact"),
            ("users", "view"),
            ("audit", "view"),
        ],
        "user": [
            ("leads", "view"),
            ("leads", "filter"),
            ("leads", "contact"),
        ],
        "viewer": [
            ("leads", "view"),
            ("leads", "filter"),
        ],
    }

    for role_name, perms in role_perm_map.items():
        c.execute("SELECT id FROM roles WHERE name = ?", (role_name,))
        role_id = c.fetchone()
        if role_id:
            role_id = role_id[0]
            for resource, action in perms:
                c.execute(
                    "SELECT id FROM permissions WHERE resource = ? AND action = ?",
                    (resource, action),
                )
                perm_id = c.fetchone()
                if perm_id:
                    perm_id = perm_id[0]
                    c.execute(
                        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?, ?)",
                        (role_id, perm_id),
                    )

    conn.commit()
    conn.close()


def seed_cities_and_agents():
    """Populate cities and agents from current configuration."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # All 54 Bay Area cities (from previous configuration)
    cities = [
        ("Alameda", "CA", "Alameda"),
        ("Albany", "CA", "Alameda"),
        ("Antioch", "CA", "Contra Costa"),
        ("Benicia", "CA", "Solano"),
        ("Berkeley", "CA", "Alameda"),
        ("Brentwood", "CA", "Contra Costa"),
        ("Campbell", "CA", "Santa Clara"),
        ("Clayton", "CA", "Contra Costa"),
        ("Concord", "CA", "Contra Costa"),
        ("Daly City", "CA", "San Mateo"),
        ("Danville", "CA", "Contra Costa"),
        ("Dublin", "CA", "Alameda"),
        ("East Palo Alto", "CA", "San Mateo"),
        ("Fairfield", "CA", "Solano"),
        ("Fremont", "CA", "Alameda"),
        ("Gilroy", "CA", "Santa Clara"),
        ("Hayward", "CA", "Alameda"),
        ("Hercules", "CA", "Contra Costa"),
        ("Hillsborough", "CA", "San Mateo"),
        ("Livermore", "CA", "Alameda"),
        ("Los Altos", "CA", "Santa Clara"),
        ("Los Gatos", "CA", "Santa Clara"),
        ("Martinez", "CA", "Contra Costa"),
        ("Menlo Park", "CA", "San Mateo"),
        ("Milpitas", "CA", "Santa Clara"),
        ("Moraga", "CA", "Contra Costa"),
        ("Morgan Hill", "CA", "Santa Clara"),
        ("Mountain View", "CA", "Santa Clara"),
        ("Napa", "CA", "Napa"),
        ("Newark", "CA", "Alameda"),
        ("Novato", "CA", "Marin"),
        ("Oakland", "CA", "Alameda"),
        ("Oakley", "CA", "Contra Costa"),
        ("Orinda", "CA", "Contra Costa"),
        ("Pacifica", "CA", "San Mateo"),
        ("Palo Alto", "CA", "Santa Clara"),
        ("Petaluma", "CA", "Sonoma"),
        ("Piedmont", "CA", "Alameda"),
        ("Pinole", "CA", "Contra Costa"),
        ("Pittsburg", "CA", "Contra Costa"),
        ("Pleasanton", "CA", "Alameda"),
        ("Redwood City", "CA", "San Mateo"),
        ("Richmond", "CA", "Contra Costa"),
        ("San Francisco", "CA", "San Francisco"),
        ("San Jose", "CA", "Santa Clara"),
        ("San Leandro", "CA", "Alameda"),
        ("San Mateo", "CA", "San Mateo"),
        ("San Rafael", "CA", "Marin"),
        ("Santa Clara", "CA", "Santa Clara"),
        ("Santa Cruz", "CA", "Santa Cruz"),
        ("Saratoga", "CA", "Santa Clara"),
        ("Sonoma", "CA", "Sonoma"),
        ("Sunnyvale", "CA", "Santa Clara"),
        ("Vacaville", "CA", "Solano"),
        ("Vallejo", "CA", "Solano"),
        ("Walnut Creek", "CA", "Contra Costa"),
    ]

    for city_name, state, county in cities:
        c.execute(
            "INSERT OR IGNORE INTO cities (name, state, county) VALUES (?, ?, ?)",
            (city_name, state, county),
        )

    # All 10 agents
    agents = [
        ("permits", "Building and demolition permits"),
        ("solar", "Solar installation leads"),
        ("rodents", "Pest control and rodent complaints"),
        ("flood", "Flood and water damage reports"),
        ("construction", "Active construction projects"),
        ("realestate", "Real estate sales and transfers"),
        ("energy", "Energy efficiency programs"),
        ("places", "Business licenses and permits"),
        ("yelp", "Business directory and reviews"),
        ("deconstruction", "Deconstruction and demolition projects"),
    ]

    for agent_name, description in agents:
        c.execute(
            "INSERT OR IGNORE INTO agents (name, description) VALUES (?, ?)",
            (agent_name, description),
        )

    conn.commit()
    conn.close()


def get_db_connection():
    """Get a database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ─────────────────────────────────────────────────────
# Scheduled Inspections Helper Functions
# ─────────────────────────────────────────────────────

def insert_scheduled_inspection(data: dict) -> int:
    """
    Insert a scheduled inspection into the database.

    Args:
        data: Dictionary with inspection fields

    Returns:
        Row ID of inserted inspection
    """
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT OR REPLACE INTO scheduled_inspections (
                permit_id, address, address_key, inspection_date, inspection_type,
                time_window_start, time_window_end, inspector_name, inspector_id,
                jurisdiction, source_url, status, gc_presence_probability, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('permit_id'),
            data.get('address'),
            data.get('address_key'),
            data.get('inspection_date'),
            data.get('inspection_type'),
            data.get('time_window_start'),
            data.get('time_window_end'),
            data.get('inspector_name'),
            data.get('inspector_id'),
            data.get('jurisdiction'),
            data.get('source_url'),
            data.get('status', 'SCHEDULED'),
            data.get('gc_presence_probability', 0.8),
            data.get('fetched_at', datetime.now()),
        ))

        conn.commit()
        return c.lastrowid

    finally:
        conn.close()


def get_upcoming_inspections(address_key: str, days: int = 30) -> list:
    """
    Get upcoming inspections for an address within N days.

    Args:
        address_key: Address key to search
        days: Number of days in future to search (default 30)

    Returns:
        List of inspection records
    """
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            SELECT * FROM scheduled_inspections
            WHERE address_key = ?
            AND inspection_date >= DATE('now')
            AND inspection_date <= DATE('now', '+' || ? || ' days')
            AND status = 'SCHEDULED'
            ORDER BY inspection_date ASC
        """, (address_key, days))

        return [dict(row) for row in c.fetchall()]

    finally:
        conn.close()


def get_inspections_by_jurisdiction(jurisdiction: str, start_date: str = None, end_date: str = None) -> list:
    """
    Get all scheduled inspections for a jurisdiction within a date range.

    Args:
        jurisdiction: Jurisdiction name
        start_date: YYYY-MM-DD format (optional)
        end_date: YYYY-MM-DD format (optional)

    Returns:
        List of inspection records
    """
    conn = get_db_connection()
    c = conn.cursor()

    try:
        if start_date is None:
            start_date = datetime.now().strftime("%Y-%m-%d")
        if end_date is None:
            end_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

        c.execute("""
            SELECT * FROM scheduled_inspections
            WHERE jurisdiction = ?
            AND inspection_date BETWEEN ? AND ?
            AND status = 'SCHEDULED'
            ORDER BY inspection_date ASC
        """, (jurisdiction, start_date, end_date))

        return [dict(row) for row in c.fetchall()]

    finally:
        conn.close()


def link_inspection_to_lead(inspection_id: int, lead_id: str) -> None:
    """
    Link a scheduled inspection to a lead (via address_key matching).
    This is typically done during lead enrichment.

    Args:
        inspection_id: Inspection record ID
        lead_id: Lead ID (usually address_key)
    """
    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Update inspection with address_key matching
        c.execute("""
            UPDATE scheduled_inspections
            SET address_key = ?
            WHERE id = ?
        """, (lead_id, inspection_id))

        conn.commit()

    finally:
        conn.close()


def cleanup_old_inspections(older_than_days: int = 60) -> int:
    """
    Delete old inspection records (older than N days).

    Args:
        older_than_days: Delete inspections older than this many days

    Returns:
        Number of deleted records
    """
    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Delete old inspections that are either completed/cancelled OR are old scheduled inspections
        c.execute("""
            DELETE FROM scheduled_inspections
            WHERE inspection_date < DATE('now', '-' || ? || ' days')
            AND (status IN ('COMPLETED', 'CANCELLED') OR status = 'SCHEDULED')
        """, (older_than_days,))

        conn.commit()
        return c.rowcount

    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════
# PHASE 2: USER PREFERENCES & SETTINGS
# ═════════════════════════════════════════════════════════════

def get_user_preferences(user_id: int) -> dict:
    """Get user preferences or create defaults."""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()

    if row:
        return dict(row)

    # Return defaults
    return {
        'theme': 'light',
        'notifications_enabled': True,
        'notify_new_leads': True,
        'notify_inspections': False,
        'notify_frequency': 'daily',
        'email_digest': True,
        'items_per_page': 100,
        'default_sort': 'last_updated',
    }


def update_user_preferences(user_id: int, preferences: dict) -> bool:
    """Update user preferences."""
    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Check if preferences exist
        c.execute("SELECT id FROM user_preferences WHERE user_id = ?", (user_id,))
        exists = c.fetchone()

        if exists:
            # Update
            updates = []
            values = []
            for key, value in preferences.items():
                if key not in ['id', 'user_id', 'created_at']:
                    updates.append(f"{key} = ?")
                    values.append(value)

            if updates:
                values.append(user_id)
                query = f"UPDATE user_preferences SET {', '.join(updates)}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?"
                c.execute(query, values)
        else:
            # Create new
            c.execute("""
                INSERT INTO user_preferences
                (user_id, theme, notifications_enabled, notify_new_leads, notify_inspections,
                 notify_frequency, email_digest, items_per_page, default_sort)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                preferences.get('theme', 'light'),
                preferences.get('notifications_enabled', True),
                preferences.get('notify_new_leads', True),
                preferences.get('notify_inspections', False),
                preferences.get('notify_frequency', 'daily'),
                preferences.get('email_digest', True),
                preferences.get('items_per_page', 100),
                preferences.get('default_sort', 'last_updated'),
            ))

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        return False
    finally:
        conn.close()


# ═════════════════════════════════════════════════════════════
# PHASE 2: SAVED LEAD VIEWS
# ═════════════════════════════════════════════════════════════

def save_lead_view(user_id: int, name: str, filters: dict, is_default: bool = False) -> int:
    """Save a filtered lead view."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT INTO lead_views (user_id, name, filters, is_default)
            VALUES (?, ?, ?, ?)
        """, (user_id, name, json.dumps(filters), is_default))

        view_id = c.lastrowid
        conn.commit()
        return view_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_lead_views(user_id: int) -> list:
    """Get all saved lead views for a user."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        SELECT id, name, filters, is_default, created_at
        FROM lead_views WHERE user_id = ? ORDER BY is_default DESC, created_at DESC
    """, (user_id,))

    views = []
    for row in c.fetchall():
        view = dict(row)
        try:
            view['filters'] = json.loads(view['filters'])
        except:
            view['filters'] = {}
        views.append(view)

    conn.close()
    return views


def delete_lead_view(view_id: int, user_id: int) -> bool:
    """Delete a lead view (verify ownership)."""
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("DELETE FROM lead_views WHERE id = ? AND user_id = ?", (view_id, user_id))
    conn.commit()
    deleted = c.rowcount > 0
    conn.close()

    return deleted


# ═════════════════════════════════════════════════════════════
# PHASE 2: ACTIVITY LOGGING
# ═════════════════════════════════════════════════════════════

def log_activity(user_id: int, action_type: str, target_id: str = None,
                 target_type: str = None, description: str = None, details: dict = None) -> bool:
    """Log an activity to the activity feed."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    try:
        c.execute("""
            INSERT INTO activity_feed
            (user_id, action_type, target_id, target_type, description, details)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (user_id, action_type, target_id, target_type, description,
              json.dumps(details) if details else None))

        conn.commit()
        return True
    except Exception as e:
        logger.warning(f"Failed to log activity: {e}")
        return False
    finally:
        conn.close()


def get_activity_feed(user_id: int = None, action_type: str = None,
                      days: int = 7, limit: int = 50, offset: int = 0) -> list:
    """Get activity feed with optional filters."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    query = "SELECT * FROM activity_feed WHERE created_at >= DATE('now', '-' || ? || ' days')"
    params = [days]

    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)

    if action_type:
        query += " AND action_type = ?"
        params.append(action_type)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    c.execute(query, params)

    activities = []
    for row in c.fetchall():
        activity = dict(row)
        try:
            if activity.get('details'):
                activity['details'] = json.loads(activity['details'])
        except:
            pass
        activities.append(activity)

    conn.close()
    return activities


# ═════════════════════════════════════════════════════════════
# PHASE 2: BULK OPERATIONS TRACKING
# ═════════════════════════════════════════════════════════════

def create_bulk_operation(user_id: int, operation_type: str, total_items: int,
                          payload: dict) -> int:
    """Create a bulk operation record."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("""
        INSERT INTO bulk_operations
        (user_id, operation_type, total_items, payload, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (user_id, operation_type, total_items, json.dumps(payload)))

    op_id = c.lastrowid
    conn.commit()
    conn.close()

    return op_id


def update_bulk_operation(operation_id: int, processed: int, status: str,
                          result: dict = None) -> bool:
    """Update progress of a bulk operation."""
    import json
    conn = get_db_connection()
    c = conn.cursor()

    completed_at = "CURRENT_TIMESTAMP" if status == 'completed' else "NULL"

    c.execute(f"""
        UPDATE bulk_operations
        SET processed_items = ?, status = ?, result = ?, completed_at = {completed_at}
        WHERE id = ?
    """, (processed, status, json.dumps(result) if result else None, operation_id))

    conn.commit()
    updated = c.rowcount > 0
    conn.close()

    return updated
