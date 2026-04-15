#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def ts(hours_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


LEADS = [
    {
        "id": "lead_phase3_001",
        "first_name": "Lucia",
        "last_name": "Mora",
        "email": "lucia.mora@techcorp.com",
        "phone": "+34-600-100-001",
        "company": "TechCorp",
        "source": "referral",
        "score": 92,
        "status": "qualified",
        "notes": "Ready for proposal",
    },
    {
        "id": "lead_phase3_002",
        "first_name": "Nico",
        "last_name": "Paz",
        "email": "nico@growth.io",
        "phone": "+34-600-100-002",
        "company": "Growth.io",
        "source": "linkedin",
        "score": 61,
        "status": "contacted",
        "notes": "Waiting for follow-up",
    },
    {
        "id": "lead_phase3_003",
        "first_name": "Eva",
        "last_name": "Ruiz",
        "email": "eva.ruiz@gmail.com",
        "phone": None,
        "company": None,
        "source": "ads",
        "score": 15,
        "status": "new",
        "notes": "Top-of-funnel only",
    },
]


def fingerprint(email: str) -> str:
    return hashlib.md5(email.strip().lower().encode()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed phase 3 demo records")
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        for idx, lead in enumerate(LEADS, start=1):
            conn.execute(
                """
                INSERT OR REPLACE INTO leads (
                  id, first_name, last_name, email, phone, company, source, score, status,
                  tags, notes, metadata, assigned_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, '{}', NULL, ?, ?)
                """,
                (
                    lead["id"],
                    lead["first_name"],
                    lead["last_name"],
                    lead["email"],
                    lead["phone"],
                    lead["company"],
                    lead["source"],
                    lead["score"],
                    lead["status"],
                    lead["notes"],
                    ts(10 * idx),
                    ts(10 * idx),
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO dedup_pool (fingerprint, lead_id, potential_matches, resolved, created_at) VALUES (?, ?, '[]', 0, ?)",
                (fingerprint(lead["email"]), lead["id"], ts(idx)),
            )

        conn.execute(
            "INSERT INTO lead_interactions (lead_id, type, content, agent_id, metadata, created_at) VALUES (?, 'meeting', 'Proposal review scheduled', 'seed', '{}', ?)",
            ("lead_phase3_001", ts(1)),
        )
        conn.execute(
            "UPDATE subscriptions SET current_leads = 850, leads_limit = 1000, updated_at = ? WHERE user_id = 'user_local_admin'",
            (ts(0),),
        )
        conn.commit()


if __name__ == "__main__":
    main()
