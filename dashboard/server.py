#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.auth_utils import (
    create_session,
    delete_session,
    get_user_by_session,
    verify_password,
)  # noqa: E402
from scripts.reliability_utils import (  # noqa: E402
    append_structured_log,
    begin_idempotent,
    complete_idempotent,
    fail_idempotent,
    record_audit,
)
from scripts.trigger_utils import evaluate_triggers, get_trigger_definitions  # noqa: E402


def db_connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "MLeadsDashboard/0.1"

    @property
    def db_path(self) -> str:
        return self.server.db_path  # type: ignore[attr-defined]

    def _json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _head(
        self,
        content_type: str = "text/html; charset=utf-8",
        content_length: int = 0,
        status: int = 200,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.end_headers()

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode())

    def _request_idempotency_key(self, payload: dict) -> str:
        header_key = self.headers.get("X-Idempotency-Key")
        if header_key:
            return header_key.strip()
        rendered = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(rendered.encode()).hexdigest()

    def _bearer_token(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header.split(" ", 1)[1].strip()
        return self.headers.get("X-Session-Token")

    def _require_auth(self):
        token = self._bearer_token()
        if not token:
            self._json({"error": "missing_token"}, 401)
            return None
        with db_connect(self.db_path) as conn:
            user = get_user_by_session(conn, token)
        if not user:
            self._json({"error": "invalid_session"}, 401)
            return None
        return user

    def _require_role(self, user: dict, roles: set[str]) -> bool:
        if user.get("role") not in roles:
            with db_connect(self.db_path) as conn:
                record_audit(
                    conn,
                    user.get("id"),
                    "access_denied",
                    "api",
                    self.path,
                    status="denied",
                    metadata={"required_roles": sorted(roles)},
                )
                conn.commit()
            self._json({"error": "forbidden"}, 403)
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            content = (ROOT / "dashboard" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if parsed.path == "/health":
            self._json({"ok": True, "db": self.db_path})
            return

        user = self._require_auth()
        if not user:
            return

        query = parse_qs(parsed.query)
        with db_connect(self.db_path) as conn:
            if parsed.path == "/api/me":
                self._json({"user": user})
                return
            if parsed.path == "/api/stats":
                total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
                qualified = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE score >= 80"
                ).fetchone()[0]
                avg_score = conn.execute(
                    "SELECT COALESCE(AVG(score), 0) FROM leads"
                ).fetchone()[0]
                by_status = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT status, COUNT(*) AS count FROM leads GROUP BY status ORDER BY count DESC"
                    ).fetchall()
                ]
                self._json(
                    {
                        "total": total,
                        "qualified": qualified,
                        "avg_score": avg_score,
                        "by_status": by_status,
                    }
                )
                return
            if parsed.path == "/api/leads":
                limit = int(query.get("limit", ["25"])[0])
                leads = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, first_name, last_name, email, company, source, score, status, updated_at FROM leads ORDER BY score DESC, updated_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                ]
                self._json({"leads": leads, "count": len(leads)})
                return
            if parsed.path == "/api/notifications":
                notifications = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, trigger_id, title, message, status, created_at FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                        (user["id"],),
                    ).fetchall()
                ]
                self._json(
                    {"notifications": notifications, "count": len(notifications)}
                )
                return
            if parsed.path == "/api/subscription":
                row = conn.execute(
                    "SELECT id, plan, status, leads_limit, current_leads, updated_at FROM subscriptions WHERE user_id = ? ORDER BY updated_at DESC LIMIT 1",
                    (user["id"],),
                ).fetchone()
                self._json({"subscription": dict(row) if row else None})
                return
            if parsed.path == "/api/triggers":
                self._json({"triggers": get_trigger_definitions()})
                return
            if parsed.path == "/api/audit-logs":
                if not self._require_role(user, {"admin"}):
                    return
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, actor_id, action, entity_type, entity_id, status, created_at FROM audit_logs ORDER BY created_at DESC LIMIT 50"
                    ).fetchall()
                ]
                self._json({"audit_logs": rows, "count": len(rows)})
                return
            if parsed.path == "/api/dedup-report":
                if not self._require_role(user, {"admin", "ops"}):
                    return
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT lead_id, duplicate_lead_id, match_reason, match_score, status, created_at FROM lead_duplicate_matches ORDER BY match_score DESC, created_at DESC LIMIT 50"
                    ).fetchall()
                ]
                self._json({"matches": rows, "count": len(rows)})
                return

        self._json({"error": "not_found"}, 404)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            content = (ROOT / "dashboard" / "index.html").read_bytes()
            self._head("text/html; charset=utf-8", len(content), 200)
            return
        if parsed.path == "/health":
            payload = json.dumps(
                {"ok": True, "db": self.db_path}, ensure_ascii=False
            ).encode()
            self._head("application/json; charset=utf-8", len(payload), 200)
            return
        self._head("application/json; charset=utf-8", 0, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            payload = self._read_json()
            email = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", ""))
            with db_connect(self.db_path) as conn:
                user = conn.execute(
                    "SELECT * FROM app_users WHERE email = ?", (email,)
                ).fetchone()
                if not user or not verify_password(password, user["password_hash"]):
                    record_audit(conn, email, "login", "user", email, status="failure")
                    conn.commit()
                    self._json({"error": "invalid_credentials"}, 401)
                    return
                token = create_session(conn, user["id"])
                record_audit(
                    conn,
                    user["id"],
                    "login",
                    "user",
                    user["id"],
                    metadata={"email": user["email"]},
                )
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.login",
                {"user_id": user["id"], "email": user["email"]},
            )
            self._json(
                {
                    "token": token,
                    "user": {
                        "id": user["id"],
                        "email": user["email"],
                        "role": user["role"],
                    },
                }
            )
            return

        user = self._require_auth()
        if not user:
            return

        if parsed.path == "/api/logout":
            token = self._bearer_token()
            if not token:
                self._json({"error": "missing_token"}, 401)
                return
            with db_connect(self.db_path) as conn:
                delete_session(conn, token)
                record_audit(conn, user.get("id"), "logout", "user", user.get("id"))
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.logout",
                {"user_id": user.get("id")},
            )
            self._json({"ok": True})
            return

        if parsed.path == "/api/leads":
            if not self._require_role(user, {"admin", "ops", "sales"}):
                return
            payload = self._read_json()
            email = str(payload.get("email", "")).strip().lower()
            first_name = str(payload.get("first_name", "")).strip()
            if not email or not first_name:
                self._json({"error": "email_and_first_name_required"}, 400)
                return
            idempotency_key = self._request_idempotency_key(payload)
            with db_connect(self.db_path) as conn:
                try:
                    state = begin_idempotent(
                        conn, "create_lead_api", idempotency_key, payload
                    )
                    if state["replayed"]:
                        self._json({**state["response"], "idempotent_replay": True})
                        return

                    existing = conn.execute(
                        "SELECT id, score, status FROM leads WHERE email = ?",
                        (email,),
                    ).fetchone()
                    if existing:
                        response = {
                            "lead_id": existing["id"],
                            "status": existing["status"],
                            "score": existing["score"],
                            "created": False,
                        }
                        complete_idempotent(
                            conn, "create_lead_api", idempotency_key, response
                        )
                        record_audit(
                            conn,
                            user.get("id"),
                            "create_lead_existing",
                            "lead",
                            existing["id"],
                            metadata={"email": email},
                        )
                        conn.commit()
                        append_structured_log(
                            "dashboard_access.jsonl",
                            "dashboard.lead.existing",
                            {
                                "user_id": user.get("id"),
                                "lead_id": existing["id"],
                                "email": email,
                            },
                        )
                        self._json(response)
                        return

                    lead_id = f"lead_{secrets.token_hex(8)}"
                    conn.execute(
                        "INSERT INTO leads (id, first_name, last_name, email, phone, company, source, score, status, tags, notes, metadata, assigned_agent, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'new', '[]', ?, ?, ?, datetime('now'), datetime('now'))",
                        (
                            lead_id,
                            first_name,
                            str(payload.get("last_name") or "").strip() or None,
                            email,
                            str(payload.get("phone") or "").strip() or None,
                            str(payload.get("company") or "").strip() or None,
                            str(payload.get("source") or "manual").strip() or "manual",
                            str(payload.get("notes") or "").strip(),
                            json.dumps(
                                payload.get("metadata") or {}, ensure_ascii=False
                            ),
                            user.get("id"),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO usage_logs (user_id, action, units, metadata, created_at) VALUES (?, 'lead_created', 1, ?, datetime('now'))",
                        (
                            user.get("id"),
                            json.dumps({"lead_id": lead_id}, ensure_ascii=False),
                        ),
                    )
                    conn.execute(
                        "UPDATE subscriptions SET current_leads = current_leads + 1, updated_at = datetime('now') WHERE user_id = ? AND status IN ('active', 'trialing')",
                        (user.get("id"),),
                    )
                    response = {
                        "lead_id": lead_id,
                        "status": "new",
                        "score": 0,
                        "created": True,
                    }
                    complete_idempotent(
                        conn, "create_lead_api", idempotency_key, response
                    )
                    record_audit(
                        conn,
                        user.get("id"),
                        "create_lead",
                        "lead",
                        lead_id,
                        metadata={
                            "email": email,
                            "source": payload.get("source", "manual"),
                        },
                    )
                    conn.commit()
                    append_structured_log(
                        "dashboard_access.jsonl",
                        "dashboard.lead.created",
                        {"user_id": user.get("id"), "lead_id": lead_id, "email": email},
                    )
                    self._json(response, 201)
                    return
                except Exception as exc:
                    fail_idempotent(conn, "create_lead_api", idempotency_key)
                    record_audit(
                        conn,
                        user.get("id"),
                        "create_lead",
                        "lead",
                        None,
                        status="failure",
                        metadata={"error": str(exc), "email": email},
                    )
                    conn.commit()
                    append_structured_log(
                        "dashboard_access.jsonl",
                        "dashboard.lead.failed",
                        {"user_id": user.get("id"), "email": email, "error": str(exc)},
                    )
                    self._json({"error": "create_lead_failed"}, 500)
                    return

        if parsed.path == "/api/triggers/evaluate":
            if not self._require_role(user, {"admin", "ops"}):
                return
            with db_connect(self.db_path) as conn:
                created = evaluate_triggers(conn)
                record_audit(
                    conn,
                    user.get("id"),
                    "evaluate_triggers",
                    "trigger",
                    None,
                    metadata={"count": len(created)},
                )
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.trigger_evaluate",
                {"user_id": user.get("id"), "created": len(created)},
            )
            self._json({"created": created, "count": len(created)})
            return

        self._json({"error": "not_found"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MLeads dashboard server")
    parser.add_argument(
        "--db", default=os.environ.get("KORTIX_DB_PATH", "/workspace/.kortix/kortix.db")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("DASHBOARD_PORT", "43123"))
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    server.db_path = args.db  # type: ignore[attr-defined]
    print(f"Dashboard running on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
