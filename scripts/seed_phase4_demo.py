#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from auth_utils import hash_password


def ts(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def fingerprint(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed phase 4 reliability demo records"
    )
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO app_users (id, email, password_hash, role, status, display_name) VALUES ('user_ops_001', 'ops@kortix.local', ?, 'ops', 'active', 'Ops User')",
            (hash_password("OpsPass123!"),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO leads (id, first_name, last_name, email, phone, company, source, score, status, tags, notes, metadata, assigned_agent, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, '{}', ?, ?, ?)",
            (
                "lead_phase4_001",
                "Lucia",
                "Mora",
                "lucia.mora@techcorp.com",
                "+34 600 111 222",
                "TechCorp",
                "referral",
                0,
                "new",
                "Primary record",
                "agent-a",
                ts(5),
                ts(5),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO leads (id, first_name, last_name, email, phone, company, source, score, status, tags, notes, metadata, assigned_agent, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, '{}', ?, ?, ?)",
            (
                "lead_phase4_002",
                "Lucia",
                "Mora",
                "lmora@techcorp.com",
                "+34 600 111 222",
                "TechCorp",
                "linkedin",
                0,
                "contacted",
                "Possible duplicate",
                "agent-b",
                ts(4),
                ts(4),
            ),
        )
        for lead_id, email in [
            ("lead_phase4_001", "lucia.mora@techcorp.com"),
            ("lead_phase4_002", "lmora@techcorp.com"),
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO dedup_pool (fingerprint, lead_id, potential_matches, resolved, created_at) VALUES (?, ?, '[]', 0, ?)",
                (fingerprint(email), lead_id, ts(1)),
            )
        conn.execute(
            "INSERT INTO lead_interactions (lead_id, type, content, agent_id, metadata, created_at) VALUES ('lead_phase4_001', 'email', 'Requested pricing details', 'seed', '{}', ?)",
            (ts(1),),
        )
        conn.commit()


if __name__ == "__main__":
    main()
