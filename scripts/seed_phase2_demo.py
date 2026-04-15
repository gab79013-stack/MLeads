#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def ts(minutes_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


DEMO_LEADS = [
    {
        "id": "lead_demo_001",
        "first_name": "Ana",
        "last_name": "Lopez",
        "email": "ana.lopez@techcorp.com",
        "phone": "+34-600-000-001",
        "company": "TechCorp",
        "source": "referral",
        "score": 0,
        "status": "new",
        "notes": "Requested pricing after webinar",
        "created_at": ts(120),
        "updated_at": ts(120),
    },
    {
        "id": "lead_demo_002",
        "first_name": "Mario",
        "last_name": "Diaz",
        "email": "mario.diaz@gmail.com",
        "phone": None,
        "company": None,
        "source": "ads",
        "score": 0,
        "status": "new",
        "notes": "Downloaded ebook",
        "created_at": ts(90),
        "updated_at": ts(90),
    },
    {
        "id": "lead_demo_003",
        "first_name": "Sara",
        "last_name": "Navas",
        "email": "sara@growth.io",
        "phone": "+34-600-000-003",
        "company": "Growth.io",
        "source": "linkedin",
        "score": 65,
        "status": "contacted",
        "notes": "Asked for case studies",
        "created_at": ts(240),
        "updated_at": ts(240),
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed demo leads for Phase 2 verification"
    )
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        for lead in DEMO_LEADS:
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
                    lead["created_at"],
                    lead["updated_at"],
                ),
            )

        conn.execute(
            "INSERT INTO lead_interactions (lead_id, type, content, agent_id, metadata, created_at) VALUES (?, 'meeting', ?, 'seed', '{}', ?)",
            ("lead_demo_001", "Discovery call completed", ts(30)),
        )
        conn.execute(
            "INSERT INTO lead_interactions (lead_id, type, content, agent_id, metadata, created_at) VALUES (?, 'email', ?, 'seed', '{}', ?)",
            ("lead_demo_003", "Sent case studies", ts(20)),
        )
        conn.commit()


if __name__ == "__main__":
    main()
