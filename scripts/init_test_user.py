#!/usr/bin/env python3
"""
Initialize test user for development/demo purposes
Usage: python scripts/init_test_user.py [username] [password] [email]
"""

import sys
import sqlite3
import hashlib
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.web_db import get_db_connection, init_web_db
from web.auth import hash_password

def create_test_user(username='admin', password='admin123', email='admin@mleads.local', full_name='Admin User'):
    """Create a test admin user in the database."""

    # Initialize DB schema
    init_web_db()

    conn = get_db_connection()
    c = conn.cursor()

    try:
        # Check if user exists
        c.execute("SELECT id FROM users WHERE username = ?", (username,))
        existing = c.fetchone()

        if existing:
            print(f"✓ User '{username}' already exists (ID: {existing[0]})")
            conn.close()
            return existing[0]

        # Create user
        password_hash = hash_password(password)
        c.execute("""
            INSERT INTO users (username, email, password_hash, full_name, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (username, email, password_hash, full_name))

        user_id = c.lastrowid

        # Assign admin role
        c.execute("SELECT id FROM roles WHERE name = 'admin'")
        admin_role = c.fetchone()

        if admin_role:
            c.execute("""
                INSERT OR IGNORE INTO user_roles (user_id, role_id)
                VALUES (?, ?)
            """, (user_id, admin_role[0]))

        # Grant all cities and agents access (for demo)
        c.execute("SELECT id FROM cities")
        cities = [row[0] for row in c.fetchall()]
        for city_id in cities:
            c.execute("""
                INSERT OR IGNORE INTO user_city_access (user_id, city_id)
                VALUES (?, ?)
            """, (user_id, city_id))

        c.execute("SELECT id FROM agents")
        agents = [row[0] for row in c.fetchall()]
        for agent_id in agents:
            c.execute("""
                INSERT OR IGNORE INTO user_agent_access (user_id, agent_id)
                VALUES (?, ?)
            """, (user_id, agent_id))

        conn.commit()
        conn.close()

        print(f"✓ Created user '{username}' (ID: {user_id})")
        print(f"  Email: {email}")
        print(f"  Password: {password}")
        print(f"  Role: admin")
        print(f"\nLogin at: http://localhost:5001/login.html")

        return user_id

    except Exception as e:
        conn.close()
        print(f"✗ Error creating user: {e}")
        return None

if __name__ == '__main__':
    username = sys.argv[1] if len(sys.argv) > 1 else 'admin'
    password = sys.argv[2] if len(sys.argv) > 2 else 'admin123'
    email = sys.argv[3] if len(sys.argv) > 3 else 'admin@mleads.local'

    create_test_user(username, password, email)
