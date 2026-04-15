#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REQUIRED_TABLES = [
    "app_users",
    "auth_sessions",
    "subscriptions",
    "payments",
    "usage_logs",
    "notifications",
    "webhook_events",
]


def sqlite_names(db_path: Path, obj_type: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (obj_type,),
        ).fetchall()
        return {row[0] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 healthcheck")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--dashboard", type=Path, default=Path("/workspace/dashboard/server.py")
    )
    parser.add_argument(
        "--stripe", type=Path, default=Path("/workspace/scripts/stripe_webhook.py")
    )
    parser.add_argument(
        "--triggers", type=Path, default=Path("/workspace/.kortix/triggers.yaml")
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    tables = sqlite_names(args.db, "table") if args.db.exists() else set()
    report = {
        "db_exists": args.db.exists(),
        "missing_tables": sorted(set(REQUIRED_TABLES) - tables),
        "dashboard_exists": args.dashboard.exists(),
        "stripe_webhook_exists": args.stripe.exists(),
        "triggers_file_exists": args.triggers.exists(),
    }
    report["ok"] = all(
        [
            report["db_exists"],
            not report["missing_tables"],
            report["dashboard_exists"],
            report["stripe_webhook_exists"],
            report["triggers_file_exists"],
        ]
    )
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
