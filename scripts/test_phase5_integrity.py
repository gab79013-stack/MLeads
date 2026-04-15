#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def query_count(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 5 integrity checks")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        exact_duplicates = conn.execute(
            "SELECT email, COUNT(*) AS c FROM leads GROUP BY email HAVING c > 1"
        ).fetchall()
        orphan_interactions = query_count(
            conn,
            "SELECT COUNT(*) FROM lead_interactions WHERE lead_id NOT IN (SELECT id FROM leads)",
        )
        orphan_notifications = query_count(
            conn,
            "SELECT COUNT(*) FROM notifications WHERE user_id NOT IN (SELECT id FROM app_users)",
        )
        scoring_duplicates = query_count(
            conn,
            "SELECT COUNT(*) FROM (SELECT lead_id, created_at, COUNT(*) AS c FROM lead_scoring_history GROUP BY lead_id, created_at HAVING c > 1)",
        )
        open_duplicate_matches = query_count(
            conn,
            "SELECT COUNT(*) FROM lead_duplicate_matches WHERE status = 'open'",
        )
        total_leads = query_count(conn, "SELECT COUNT(*) FROM leads")

    report = {
        "total_leads": total_leads,
        "exact_duplicate_emails": len(exact_duplicates),
        "orphan_interactions": orphan_interactions,
        "orphan_notifications": orphan_notifications,
        "duplicate_scoring_rows": scoring_duplicates,
        "open_duplicate_matches": open_duplicate_matches,
        "ok": len(exact_duplicates) == 0
        and orphan_interactions == 0
        and orphan_notifications == 0
        and scoring_duplicates == 0,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
