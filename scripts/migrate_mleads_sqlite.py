#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_TARGET = Path("/workspace/.kortix/kortix.db")
DEFAULT_SCHEMA = Path("/workspace/scripts/init_mleads_schema.sql")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def copy_backup(path: Path, backup_dir: Path, label: str) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = (
        backup_dir
        / f"{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix or '.db'}"
    )
    shutil.copy2(path, target)
    return target


def ensure_schema(target_db: Path, schema_path: Path) -> None:
    sql = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(target_db) as conn:
        conn.executescript(sql)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [row[1] for row in rows]


def detect_source_table(conn: sqlite3.Connection, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for table in list_tables(conn):
        cols = {col.lower() for col in get_columns(conn, table)}
        if "email" in cols:
            return table
    return None


ALIASES = {
    "id": ["id", "lead_id"],
    "first_name": ["first_name", "firstname", "given_name"],
    "last_name": ["last_name", "lastname", "surname", "family_name"],
    "email": ["email", "email_address"],
    "phone": ["phone", "phone_number", "mobile"],
    "company": ["company", "company_name", "organization"],
    "source": ["source", "channel", "acquisition_source"],
    "score": ["score", "lead_score"],
    "status": ["status", "stage", "lead_status"],
    "notes": ["notes", "note", "description"],
    "tags": ["tags", "labels"],
    "metadata": ["metadata", "meta", "payload"],
    "created_at": ["created_at", "createdon", "inserted_at"],
    "updated_at": ["updated_at", "modified_at", "last_updated"],
    "assigned_agent": ["assigned_agent", "owner", "agent_id"],
}


def pick(row: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        if alias in row and row[alias] not in (None, ""):
            return row[alias]
    return None


def normalize_json(value: Any, default: str) -> str:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize_status(value: Any) -> str:
    candidate = str(value or "new").strip().lower()
    allowed = {"new", "contacted", "qualified", "proposal", "won", "lost"}
    return candidate if candidate in allowed else "new"


def build_target_row(source_row: dict[str, Any]) -> dict[str, Any]:
    lowered = {str(key).lower(): value for key, value in source_row.items()}
    email = pick(lowered, *ALIASES["email"])
    if not email:
        raise ValueError("source row has no email")

    identifier = (
        pick(lowered, *ALIASES["id"])
        or f"lead_{hashlib.sha1(str(email).lower().encode()).hexdigest()[:12]}"
    )
    created_at = pick(lowered, *ALIASES["created_at"]) or utc_now()
    updated_at = pick(lowered, *ALIASES["updated_at"]) or created_at

    return {
        "id": str(identifier),
        "first_name": pick(lowered, *ALIASES["first_name"]),
        "last_name": pick(lowered, *ALIASES["last_name"]),
        "email": str(email).strip().lower(),
        "phone": pick(lowered, *ALIASES["phone"]),
        "company": pick(lowered, *ALIASES["company"]),
        "source": pick(lowered, *ALIASES["source"]) or "import",
        "score": float(pick(lowered, *ALIASES["score"]) or 0),
        "status": normalize_status(pick(lowered, *ALIASES["status"])),
        "tags": normalize_json(pick(lowered, *ALIASES["tags"]), "[]"),
        "notes": str(pick(lowered, *ALIASES["notes"]) or ""),
        "metadata": normalize_json(pick(lowered, *ALIASES["metadata"]), "{}"),
        "assigned_agent": pick(lowered, *ALIASES["assigned_agent"]),
        "created_at": str(created_at),
        "updated_at": str(updated_at),
    }


def import_rows(source_db: Path, target_db: Path, source_table: str) -> dict[str, Any]:
    with (
        sqlite3.connect(source_db) as source_conn,
        sqlite3.connect(target_db) as target_conn,
    ):
        source_conn.row_factory = sqlite3.Row
        rows = source_conn.execute(f"SELECT * FROM {source_table}").fetchall()
        imported = 0
        skipped = 0
        for row in rows:
            try:
                payload = build_target_row(dict(row))
            except Exception:
                skipped += 1
                continue

            target_conn.execute(
                """
                INSERT INTO leads (
                  id, first_name, last_name, email, phone, company, source, score,
                  status, tags, notes, metadata, assigned_agent, created_at, updated_at
                ) VALUES (
                  :id, :first_name, :last_name, :email, :phone, :company, :source, :score,
                  :status, :tags, :notes, :metadata, :assigned_agent, :created_at, :updated_at
                )
                ON CONFLICT(email) DO UPDATE SET
                  first_name=excluded.first_name,
                  last_name=excluded.last_name,
                  phone=excluded.phone,
                  company=excluded.company,
                  source=excluded.source,
                  score=excluded.score,
                  status=excluded.status,
                  tags=excluded.tags,
                  notes=excluded.notes,
                  metadata=excluded.metadata,
                  assigned_agent=excluded.assigned_agent,
                  updated_at=excluded.updated_at
                """,
                payload,
            )
            fingerprint = hashlib.md5(payload["email"].encode()).hexdigest()
            target_conn.execute(
                "INSERT OR IGNORE INTO dedup_pool (fingerprint, lead_id, potential_matches, resolved) VALUES (?, ?, '[]', 0)",
                (fingerprint, payload["id"]),
            )
            imported += 1

        target_conn.commit()

    return {"imported": imported, "skipped": skipped, "source_table": source_table}


def integrity_check(target_db: Path) -> str:
    with sqlite3.connect(target_db) as conn:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]


def count_leads(target_db: Path) -> int:
    with sqlite3.connect(target_db) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize and migrate MLeads SQLite data into Kortix."
    )
    parser.add_argument("--source", type=Path, help="Source MLeads SQLite database")
    parser.add_argument("--source-table", help="Explicit source table name")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--backup-dir", type=Path, default=Path("/workspace/backups/phase1")
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    args = parser.parse_args()

    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.backup_dir.mkdir(parents=True, exist_ok=True)

    backups: dict[str, str | None] = {
        "target": str(copy_backup(args.target, args.backup_dir, "kortix_pre_migration"))
        if args.target.exists()
        else None,
        "source": str(copy_backup(args.source, args.backup_dir, "mleads_source"))
        if args.source and args.source.exists()
        else None,
    }

    ensure_schema(args.target, args.schema)

    import_summary: dict[str, Any] = {"imported": 0, "skipped": 0, "source_table": None}
    if args.source:
        if not args.source.exists():
            raise FileNotFoundError(f"Source database not found: {args.source}")
        with sqlite3.connect(args.source) as source_conn:
            source_table = detect_source_table(source_conn, args.source_table)
        if source_table:
            import_summary = import_rows(args.source, args.target, source_table)

    summary = {
        "target_db": str(args.target),
        "integrity_check": integrity_check(args.target),
        "lead_count": count_leads(args.target),
        "backups": backups,
        "migration": import_summary,
        "completed_at": utc_now(),
    }

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
