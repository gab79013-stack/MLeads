#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def apply_schema(db_path: Path, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(sql)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize phase 4 reliability schema"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--schema", type=Path, default=Path("/workspace/scripts/init_phase4_schema.sql")
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    apply_schema(args.db, args.schema)
    report = {"db": str(args.db), "schema": str(args.schema), "ok": True}
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
