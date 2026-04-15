#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from auth_utils import default_admin_email, default_admin_password, hash_password


def apply_schema(db_path: Path, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)


def seed_demo_user(conn: sqlite3.Connection) -> dict:
    user_id = "user_local_admin"
    email = default_admin_email()
    password = default_admin_password()
    conn.execute(
        """
        INSERT OR IGNORE INTO app_users (id, email, password_hash, role, status, display_name)
        VALUES (?, ?, ?, 'admin', 'active', 'Local Admin')
        """,
        (user_id, email, hash_password(password)),
    )
    conn.commit()
    return {"user_id": user_id, "email": email, "password": password}


def seed_demo_subscription(conn: sqlite3.Connection, user_id: str) -> dict:
    sub_id = f"sub_{user_id}"
    conn.execute(
        """
        INSERT OR IGNORE INTO subscriptions (
          id, user_id, stripe_customer_id, stripe_subscription_id, plan, status,
          leads_limit, current_leads, features
        ) VALUES (?, ?, ?, ?, 'starter', 'active', 1000, 12, '["dashboard","billing","notifications"]')
        """,
        (sub_id, user_id, "cus_local_admin", "sub_local_admin"),
    )
    conn.commit()
    return {"subscription_id": sub_id, "plan": "starter"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize Phase 3 auth, billing, and notification tables"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--schema", type=Path, default=Path("/workspace/scripts/init_phase3_schema.sql")
    )
    parser.add_argument("--seed-demo-user", action="store_true")
    parser.add_argument("--seed-demo-subscription", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    apply_schema(args.db, args.schema)

    report: dict = {
        "db": str(args.db),
        "seeded_user": None,
        "seeded_subscription": None,
    }
    with sqlite3.connect(args.db) as conn:
        if args.seed_demo_user:
            report["seeded_user"] = seed_demo_user(conn)
        if args.seed_demo_subscription:
            user_info = report["seeded_user"] or {"user_id": "user_local_admin"}
            report["seeded_subscription"] = seed_demo_subscription(
                conn, user_info["user_id"]
            )

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
