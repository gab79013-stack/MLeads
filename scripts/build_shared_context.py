#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def query_pairs(conn: sqlite3.Connection, sql: str) -> dict[str, int]:
    return {row[0]: int(row[1]) for row in conn.execute(sql).fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build shared Kortix context for lead scoring"
    )
    parser.add_argument("--db", type=Path, default=Path("/workspace/.kortix/kortix.db"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/workspace/.kortix/memory/shared-context.json"),
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(args.db) as conn:
        payload = {
            "generated_at": utc_now(),
            "db_path": str(args.db),
            "lead_count": int(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]),
            "avg_score": float(
                conn.execute("SELECT COALESCE(AVG(score), 0) FROM leads").fetchone()[0]
            ),
            "qualified_count": int(
                conn.execute("SELECT COUNT(*) FROM leads WHERE score >= 80").fetchone()[
                    0
                ]
            ),
            "status_counts": query_pairs(
                conn, "SELECT status, COUNT(*) FROM leads GROUP BY status"
            ),
            "source_counts": query_pairs(
                conn, "SELECT source, COUNT(*) FROM leads GROUP BY source"
            ),
            "recent_interactions": int(
                conn.execute(
                    "SELECT COUNT(*) FROM lead_interactions WHERE created_at >= datetime('now', '-7 day')"
                ).fetchone()[0]
            ),
            "recent_scoring_events": int(
                conn.execute(
                    "SELECT COUNT(*) FROM lead_scoring_history WHERE created_at >= datetime('now', '-7 day')"
                ).fetchone()[0]
            ),
            "top_companies": [
                {"company": row[0], "count": int(row[1])}
                for row in conn.execute(
                    "SELECT COALESCE(company, 'unknown'), COUNT(*) FROM leads GROUP BY COALESCE(company, 'unknown') ORDER BY COUNT(*) DESC LIMIT 5"
                ).fetchall()
            ],
        }

    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
