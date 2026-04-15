#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REQUIRED_TABLES = [
    "audit_logs",
    "idempotency_keys",
    "processing_locks",
    "lead_duplicate_matches",
    "metrics_snapshots",
]


def sqlite_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row[0] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 healthcheck")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    tables = sqlite_names(args.db) if args.db.exists() else set()
    report = {
        "db_exists": args.db.exists(),
        "missing_tables": sorted(set(REQUIRED_TABLES) - tables),
        "ok": args.db.exists() and not (set(REQUIRED_TABLES) - tables),
    }
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
