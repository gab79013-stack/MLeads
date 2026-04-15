#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_trigger_definitions() -> list[dict]:
    return [
        {
            "id": "lead_qualified",
            "name": "Lead calificado",
            "description": "Notifica cuando un lead alcanza score >= 80",
        },
        {
            "id": "duplicate_pool_alert",
            "name": "Pool de duplicados elevado",
            "description": "Notifica cuando hay 3 o más fingerprints sin resolver",
        },
        {
            "id": "usage_limit_warning",
            "name": "Uso cercano al límite",
            "description": "Notifica cuando un usuario supera el 80% del plan",
        },
    ]


def _default_admin_user_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM app_users WHERE role = 'admin' AND status = 'active' ORDER BY created_at ASC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _notification_exists(
    conn: sqlite3.Connection,
    user_id: str,
    trigger_id: str,
    lead_id: str | None = None,
    hours: int = 24,
) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    row = conn.execute(
        """
        SELECT 1 FROM notifications
        WHERE user_id = ? AND trigger_id = ?
          AND COALESCE(lead_id, '') = COALESCE(?, '')
          AND created_at >= ?
        LIMIT 1
        """,
        (user_id, trigger_id, lead_id, cutoff),
    ).fetchone()
    return row is not None


def _insert_notification(
    conn: sqlite3.Connection,
    user_id: str,
    trigger_id: str,
    title: str,
    message: str,
    lead_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    payload = metadata or {}
    conn.execute(
        "INSERT INTO notifications (user_id, lead_id, trigger_id, title, message, channel, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, 'in_app', 'pending', ?, ?)",
        (
            user_id,
            lead_id,
            trigger_id,
            title,
            message,
            json.dumps(payload, ensure_ascii=False),
            utc_now(),
        ),
    )
    return {
        "user_id": user_id,
        "lead_id": lead_id,
        "trigger_id": trigger_id,
        "title": title,
        "message": message,
        "metadata": payload,
    }


def evaluate_triggers(conn: sqlite3.Connection) -> list[dict]:
    notifications: list[dict] = []
    admin_user_id = _default_admin_user_id(conn)
    if not admin_user_id:
        return notifications

    conn.row_factory = sqlite3.Row

    qualified_rows = conn.execute(
        "SELECT id, first_name, company, score FROM leads WHERE score >= 80 ORDER BY updated_at DESC"
    ).fetchall()
    for row in qualified_rows:
        if _notification_exists(conn, admin_user_id, "lead_qualified", row["id"]):
            continue
        notifications.append(
            _insert_notification(
                conn,
                admin_user_id,
                "lead_qualified",
                "Lead calificado",
                f"{row['first_name'] or 'Lead'} de {row['company'] or 'empresa desconocida'} alcanzó score {row['score']}",
                row["id"],
                {"score": row["score"]},
            )
        )

    unresolved = conn.execute(
        "SELECT COUNT(*) FROM dedup_pool WHERE resolved = 0"
    ).fetchone()[0]
    if unresolved >= 3 and not _notification_exists(
        conn, admin_user_id, "duplicate_pool_alert", None, hours=6
    ):
        notifications.append(
            _insert_notification(
                conn,
                admin_user_id,
                "duplicate_pool_alert",
                "Duplicados pendientes",
                f"Hay {unresolved} fingerprints sin resolver en el dedup pool.",
                metadata={"unresolved": unresolved},
            )
        )

    subscription_rows = conn.execute(
        "SELECT user_id, plan, current_leads, leads_limit FROM subscriptions WHERE status IN ('active', 'trialing')"
    ).fetchall()
    for row in subscription_rows:
        ratio = (row["current_leads"] / row["leads_limit"]) if row["leads_limit"] else 0
        if ratio < 0.8 or _notification_exists(
            conn, row["user_id"], "usage_limit_warning", None, hours=12
        ):
            continue
        notifications.append(
            _insert_notification(
                conn,
                row["user_id"],
                "usage_limit_warning",
                "Uso cercano al límite",
                f"Tu plan {row['plan']} está en {ratio:.0%} del límite de leads.",
                metadata={
                    "ratio": ratio,
                    "current_leads": row["current_leads"],
                    "leads_limit": row["leads_limit"],
                },
            )
        )

    conn.commit()
    return notifications
