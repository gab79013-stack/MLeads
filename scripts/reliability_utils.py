#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_dir() -> Path:
    return Path(os.environ.get("STRUCTURED_LOG_DIR", "/workspace/logs"))


def append_structured_log(
    filename: str, event_type: str, payload: dict[str, Any]
) -> Path:
    target_dir = log_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    line = {
        "timestamp": utc_now(),
        "event_type": event_type,
        **payload,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def stable_payload_hash(payload: Any) -> str:
    rendered = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(rendered.encode()).hexdigest()


def record_audit(
    conn: sqlite3.Connection,
    actor_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    status: str = "success",
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor_id, action, entity_type, entity_id, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            actor_id,
            action,
            entity_type,
            entity_id,
            status,
            json.dumps(metadata or {}, ensure_ascii=False),
            utc_now(),
        ),
    )


def begin_idempotent(
    conn: sqlite3.Connection,
    scope: str,
    idempotency_key: str,
    payload: Any,
) -> dict[str, Any]:
    payload_hash = stable_payload_hash(payload)
    row = conn.execute(
        "SELECT payload_hash, status, response FROM idempotency_keys WHERE scope = ? AND idempotency_key = ?",
        (scope, idempotency_key),
    ).fetchone()
    if row:
        existing_hash, status, response = row
        if existing_hash != payload_hash:
            raise ValueError("Idempotency key reuse with different payload")
        if status == "completed" and response:
            return {
                "replayed": True,
                "payload_hash": payload_hash,
                "response": json.loads(response),
            }
        return {"replayed": False, "payload_hash": payload_hash, "response": None}

    conn.execute(
        "INSERT INTO idempotency_keys (scope, idempotency_key, payload_hash, status, created_at, updated_at) VALUES (?, ?, ?, 'processing', ?, ?)",
        (scope, idempotency_key, payload_hash, utc_now(), utc_now()),
    )
    return {"replayed": False, "payload_hash": payload_hash, "response": None}


def complete_idempotent(
    conn: sqlite3.Connection,
    scope: str,
    idempotency_key: str,
    response: Any,
    status: str = "completed",
) -> None:
    conn.execute(
        "UPDATE idempotency_keys SET status = ?, response = ?, updated_at = ? WHERE scope = ? AND idempotency_key = ?",
        (
            status,
            json.dumps(response, ensure_ascii=False),
            utc_now(),
            scope,
            idempotency_key,
        ),
    )


def fail_idempotent(conn: sqlite3.Connection, scope: str, idempotency_key: str) -> None:
    conn.execute(
        "UPDATE idempotency_keys SET status = 'failed', updated_at = ? WHERE scope = ? AND idempotency_key = ?",
        (utc_now(), scope, idempotency_key),
    )


def acquire_lock(
    conn: sqlite3.Connection,
    resource_type: str,
    resource_id: str,
    owner_id: str,
    ttl_seconds: int = 300,
) -> bool:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    ).isoformat()
    row = conn.execute(
        "SELECT owner_id, expires_at FROM processing_locks WHERE resource_type = ? AND resource_id = ?",
        (resource_type, resource_id),
    ).fetchone()
    if row and row[1] > utc_now() and row[0] != owner_id:
        return False
    conn.execute(
        "INSERT OR REPLACE INTO processing_locks (resource_type, resource_id, owner_id, expires_at, created_at, updated_at) VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM processing_locks WHERE resource_type = ? AND resource_id = ?), ?), ?)",
        (
            resource_type,
            resource_id,
            owner_id,
            expires_at,
            resource_type,
            resource_id,
            utc_now(),
            utc_now(),
        ),
    )
    return True


def release_lock(
    conn: sqlite3.Connection, resource_type: str, resource_id: str, owner_id: str
) -> None:
    conn.execute(
        "DELETE FROM processing_locks WHERE resource_type = ? AND resource_id = ? AND owner_id = ?",
        (resource_type, resource_id, owner_id),
    )


def normalize_phone(phone: str | None) -> str:
    if not phone:
        return ""
    return "".join(ch for ch in phone if ch.isdigit())


def normalize_company(company: str | None) -> str:
    if not company:
        return ""
    return "".join(ch.lower() for ch in company if ch.isalnum())


def ensure_metrics_snapshot(
    conn: sqlite3.Connection, snapshot_type: str, payload: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO metrics_snapshots (snapshot_type, payload, created_at) VALUES (?, ?, ?)",
        (snapshot_type, json.dumps(payload, ensure_ascii=False), utc_now()),
    )
