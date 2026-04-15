#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str, iterations: int = 120000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), int(iterations)
    ).hex()
    return hmac.compare_digest(candidate, digest)


def create_session(conn: sqlite3.Connection, user_id: str, ttl_hours: int = 24) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    conn.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, expires_at, utc_now()),
    )
    conn.execute(
        "UPDATE app_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
        (utc_now(), utc_now(), user_id),
    )
    conn.commit()
    return token


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    conn.commit()


def get_user_by_session(conn: sqlite3.Connection, token: str):
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT u.id, u.email, u.role, u.status, u.display_name, s.expires_at
        FROM auth_sessions s
        JOIN app_users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ? AND u.status = 'active'
        """,
        (token, utc_now()),
    ).fetchone()
    return dict(row) if row else None


def default_admin_email() -> str:
    return os.environ.get("KORTIX_ADMIN_EMAIL", "admin@kortix.local")


def default_admin_password() -> str:
    return os.environ.get("KORTIX_ADMIN_PASSWORD", "ChangeMe123!")
