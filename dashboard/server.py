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

        if parsed.path == "/login.html":
            content = (ROOT / "dashboard" / "login.html").read_bytes()
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
            if parsed.path == "/api/leads":
                limit = int(query.get("limit", ["100"])[0])
                offset = int(query.get("offset", ["0"])[0])
                city_id = query.get("city_id", [None])[0]
                state = query.get("state", [None])[0]
                service_type = query.get("service_type", [None])[0]
                status_filter = query.get("status", [None])[0]
                min_score = query.get("min_score", [None])[0]
                search = query.get("search", [None])[0]

                conditions = []
                params = []

                if city_id:
                    conditions.append("l.city_id = ?")
                    params.append(city_id)
                if state:
                    conditions.append("l.state = ?")
                    params.append(state)
                if service_type:
                    conditions.append("l.service_type = ?")
                    params.append(service_type)
                if status_filter:
                    conditions.append("l.status = ?")
                    params.append(status_filter)
                if min_score:
                    conditions.append("l.score >= ?")
                    params.append(float(min_score))
                if search:
                    conditions.append(
                        "(l.first_name LIKE ? OR l.last_name LIKE ? OR l.email LIKE ? OR l.company LIKE ? OR l.address LIKE ? OR l.city LIKE ?)"
                    )
                    like_search = f"%{search}%"
                    params.extend([like_search] * 6)

                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                params_limit = list(params) + [limit, offset]

                leads = [
                    dict(row)
                    for row in conn.execute(
                        f"""SELECT l.id, l.first_name, l.last_name, l.email, l.phone,
                            l.company, l.source, l.score, l.status, l.city, l.city_id,
                            l.state, l.zip, l.address, l.estimated_value, l.service_type,
                            l.agent_id, l.first_seen, l.next_inspection, l.created_at,
                            l.updated_at
                            FROM leads l{where}
                            ORDER BY l.score DESC, l.updated_at DESC
                            LIMIT ? OFFSET ?""",
                        params_limit,
                    ).fetchall()
                ]

                total = conn.execute(
                    f"SELECT COUNT(*) FROM leads l{where}", params
                ).fetchone()[0]

                self._json({"leads": leads, "count": len(leads), "total": total})
                return

            # GET /api/leads/:id
            if parsed.path.startswith("/api/leads/"):
                lead_id = parsed.path[len("/api/leads/") :]
                lead = conn.execute(
                    """SELECT l.id, l.first_name, l.last_name, l.email, l.phone,
                        l.company, l.source, l.score, l.status, l.city, l.city_id,
                        l.state, l.zip, l.address, l.estimated_value, l.service_type,
                        l.agent_id, l.first_seen, l.next_inspection, l.tags, l.notes,
                        l.metadata, l.assigned_agent, l.created_at, l.updated_at
                        FROM leads l WHERE l.id = ?""",
                    (lead_id,),
                ).fetchone()
                if not lead:
                    self._json({"error": "lead_not_found"}, 404)
                    return
                lead_dict = dict(lead)
                # Get interactions
                lead_dict["interactions"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, type, content, agent_id, metadata, created_at FROM lead_interactions WHERE lead_id = ? ORDER BY created_at DESC",
                        (lead_id,),
                    ).fetchall()
                ]
                # Get scoring history
                lead_dict["scoring_history"] = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT id, score, model, factors, created_at FROM lead_scoring_history WHERE lead_id = ? ORDER BY created_at DESC",
                        (lead_id,),
                    ).fetchall()
                ]
                self._json({"lead": lead_dict})
                return

            # GET /api/cities
            if parsed.path == "/api/cities":
                state_filter = query.get("state", [None])[0]
                active_only = query.get("active", ["1"])[0]
                conditions = []
                params = []
                if state_filter:
                    conditions.append("state = ?")
                    params.append(state_filter)
                if active_only == "1":
                    conditions.append("active = 1")
                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                cities = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT id, name, state, country, jurisdiction, population, avg_home_value, active, metadata FROM cities{where} ORDER BY name",
                        params,
                    ).fetchall()
                ]
                self._json({"cities": cities, "count": len(cities)})
                return

            # GET /api/cities/:id/stats (must be before /api/cities/:id)
            if parsed.path.endswith("/stats") and parsed.path.startswith(
                "/api/cities/"
            ):
                city_id = parsed.path[len("/api/cities/") : -len("/stats")]
                stats = {
                    "city_id": city_id,
                    "lead_count": conn.execute(
                        "SELECT COUNT(*) FROM leads WHERE city_id = ?", (city_id,)
                    ).fetchone()[0],
                    "avg_score": conn.execute(
                        "SELECT COALESCE(AVG(score), 0) FROM leads WHERE city_id = ?",
                        (city_id,),
                    ).fetchone()[0],
                    "by_status": [
                        dict(r)
                        for r in conn.execute(
                            "SELECT status, COUNT(*) as count FROM leads WHERE city_id = ? GROUP BY status",
                            (city_id,),
                        ).fetchall()
                    ],
                    "by_service_type": [
                        dict(r)
                        for r in conn.execute(
                            "SELECT service_type, COUNT(*) as count FROM leads WHERE city_id = ? AND service_type IS NOT NULL GROUP BY service_type",
                            (city_id,),
                        ).fetchall()
                    ],
                    "total_estimated_value": conn.execute(
                        "SELECT COALESCE(SUM(estimated_value), 0) FROM leads WHERE city_id = ?",
                        (city_id,),
                    ).fetchone()[0],
                }
                self._json({"stats": stats})
                return

            # GET /api/cities/:id
            if parsed.path.startswith("/api/cities/"):
                city_id = parsed.path[len("/api/cities/") :]
                city = conn.execute(
                    "SELECT * FROM cities WHERE id = ?", (city_id,)
                ).fetchone()
                if not city:
                    self._json({"error": "city_not_found"}, 404)
                    return
                city_dict = dict(city)
                city_dict["lead_count"] = conn.execute(
                    "SELECT COUNT(*) FROM leads WHERE city_id = ?", (city_id,)
                ).fetchone()[0]
                city_dict["avg_score"] = conn.execute(
                    "SELECT COALESCE(AVG(score), 0) FROM leads WHERE city_id = ?",
                    (city_id,),
                ).fetchone()[0]
                city_dict["total_value"] = conn.execute(
                    "SELECT COALESCE(SUM(estimated_value), 0) FROM leads WHERE city_id = ?",
                    (city_id,),
                ).fetchone()[0]
                self._json({"city": city_dict})
                return

            # GET /api/bot-users
            if parsed.path == "/api/bot-users":
                status_filter = query.get("status", [None])[0]
                city_filter = query.get("city_id", [None])[0]
                conditions = []
                params = []
                if status_filter:
                    conditions.append("status = ?")
                    params.append(status_filter)
                if city_filter:
                    conditions.append("city_id = ?")
                    params.append(city_filter)
                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                bot_users = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM bot_users{where} ORDER BY created_at DESC",
                        params,
                    ).fetchall()
                ]
                # Stats
                total = conn.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
                on_trial = conn.execute(
                    "SELECT COUNT(*) FROM bot_users WHERE status = 'trial'"
                ).fetchone()[0]
                paying = conn.execute(
                    "SELECT COUNT(*) FROM bot_users WHERE status = 'active'"
                ).fetchone()[0]
                est_mrr = conn.execute(
                    "SELECT COUNT(*) * 29 FROM bot_users WHERE status = 'active'"
                ).fetchone()[0]
                self._json(
                    {
                        "bot_users": bot_users,
                        "count": len(bot_users),
                        "stats": {
                            "total": total,
                            "on_trial": on_trial,
                            "paying": paying,
                            "est_mrr": est_mrr,
                        },
                    }
                )
                return

            # GET /api/inspections
            if parsed.path == "/api/inspections":
                days_filter = query.get("days", [None])[0]
                jurisdiction_filter = query.get("jurisdiction", [None])[0]
                conditions = []
                params = []
                if days_filter:
                    conditions.append(
                        "inspection_date <= date('now', '+' || ? || ' days')"
                    )
                    params.append(int(days_filter))
                if jurisdiction_filter:
                    conditions.append("jurisdiction = ?")
                    params.append(jurisdiction_filter)
                where = " WHERE " + " AND ".join(conditions) if conditions else ""
                inspections = [
                    dict(row)
                    for row in conn.execute(
                        f"SELECT * FROM inspections{where} ORDER BY inspection_date ASC",
                        params,
                    ).fetchall()
                ]
                self._json({"inspections": inspections, "count": len(inspections)})
                return

            # GET /api/users
            if parsed.path == "/api/users":
                if not self._require_role(user, {"admin"}):
                    return
                users = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, email, role, created_at FROM app_users ORDER BY created_at DESC"
                    ).fetchall()
                ]
                self._json({"users": users, "count": len(users)})
                return

            # GET /api/export/history
            if parsed.path == "/api/export/history":
                exports = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM export_history WHERE user_id = ? ORDER BY created_at DESC LIMIT 20",
                        (user["id"],),
                    ).fetchall()
                ]
                self._json({"exports": exports, "count": len(exports)})
                return

            # GET /api/feedback
            if parsed.path == "/api/feedback":
                if not self._require_role(user, {"admin"}):
                    return
                feedback = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM feedback ORDER BY created_at DESC LIMIT 50"
                    ).fetchall()
                ]
                self._json({"feedback": feedback, "count": len(feedback)})
                return

            # GET /api/stats (enhanced with city breakdown)
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
                by_city = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT l.city, COUNT(*) as count, COALESCE(AVG(l.score), 0) as avg_score
                           FROM leads l WHERE l.city IS NOT NULL
                           GROUP BY l.city ORDER BY count DESC LIMIT 10"""
                    ).fetchall()
                ]
                by_service_type = [
                    dict(row)
                    for row in conn.execute(
                        """SELECT l.service_type, COUNT(*) as count
                           FROM leads l WHERE l.service_type IS NOT NULL
                           GROUP BY l.service_type ORDER BY count DESC"""
                    ).fetchall()
                ]
                self._json(
                    {
                        "total": total,
                        "qualified": qualified,
                        "avg_score": avg_score,
                        "by_status": by_status,
                        "by_city": by_city,
                        "by_service_type": by_service_type,
                    }
                )
                return
            if parsed.path == "/api/notifications":
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
        if parsed.path == "/login.html":
            content = (ROOT / "dashboard" / "login.html").read_bytes()
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
                        """INSERT INTO leads (id, first_name, last_name, email, phone, company,
                           source, score, status, tags, notes, metadata, assigned_agent,
                           city, city_id, state, zip, address, country, estimated_value,
                           service_type, agent_id, first_seen, next_inspection,
                           created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'new', '[]', ?, ?, ?,
                                   ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   datetime('now'), datetime('now'))""",
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
                            str(payload.get("city") or "").strip() or None,
                            str(payload.get("city_id") or "").strip() or None,
                            str(payload.get("state") or "").strip() or None,
                            str(payload.get("zip") or "").strip() or None,
                            str(payload.get("address") or "").strip() or None,
                            str(payload.get("country") or "US").strip(),
                            float(payload["estimated_value"])
                            if payload.get("estimated_value")
                            else None,
                            str(payload.get("service_type") or "").strip() or None,
                            str(payload.get("agent_id") or "").strip() or None,
                            str(payload.get("first_seen") or "").strip() or None,
                            str(payload.get("next_inspection") or "").strip() or None,
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

        # POST /api/notifications/test
        if parsed.path == "/api/notifications/test":
            payload = self._read_json()
            with db_connect(self.db_path) as conn:
                record_audit(
                    conn, user.get("id"), "test_notification", "notification", None
                )
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.notification.test",
                {"user_id": user.get("id")},
            )
            self._json({"ok": True, "message": "Test notification sent"})
            return

        # POST /api/export
        if parsed.path == "/api/export":
            payload = self._read_json()
            export_id = f"export_{secrets.token_hex(8)}"
            fmt = payload.get("format", "csv")
            filters = payload.get("filters", {})
            with db_connect(self.db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
                conn.execute(
                    "INSERT INTO export_history (id, user_id, format, filters, row_count, status, created_at) VALUES (?, ?, ?, ?, ?, 'completed', datetime('now'))",
                    (export_id, user["id"], fmt, json.dumps(filters), count),
                )
                conn.commit()
            self._json({"export_id": export_id, "row_count": count, "format": fmt})
            return

        # POST /api/feedback
        if parsed.path == "/api/feedback":
            payload = self._read_json()
            feedback_id = f"fb_{secrets.token_hex(8)}"
            fb_type = payload.get("type", "general")
            message = payload.get("message", "")
            rating = payload.get("rating")
            if not message:
                self._json({"error": "message_required"}, 400)
                return
            with db_connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO feedback (id, user_id, type, rating, message, metadata, created_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                    (
                        feedback_id,
                        user["id"],
                        fb_type,
                        rating,
                        message,
                        json.dumps(payload.get("metadata", {})),
                    ),
                )
                conn.commit()
            self._json({"id": feedback_id, "ok": True}, 201)
            return

        self._json({"error": "not_found"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        user = self._require_auth()
        if not user:
            return

        payload = self._read_json()

        # PUT /api/leads/:id
        if parsed.path.startswith("/api/leads/"):
            if not self._require_role(user, {"admin", "ops", "sales"}):
                return
            lead_id = parsed.path[len("/api/leads/") :]
            with db_connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id FROM leads WHERE id = ?", (lead_id,)
                ).fetchone()
                if not existing:
                    self._json({"error": "lead_not_found"}, 404)
                    return

                fields = []
                values = []
                for key in [
                    "first_name",
                    "last_name",
                    "email",
                    "phone",
                    "company",
                    "city",
                    "city_id",
                    "state",
                    "zip",
                    "address",
                    "country",
                    "estimated_value",
                    "service_type",
                    "agent_id",
                    "score",
                    "status",
                    "notes",
                    "assigned_agent",
                    "first_seen",
                    "next_inspection",
                ]:
                    if key in payload:
                        fields.append(f"{key} = ?")
                        values.append(payload[key])
                fields.append("updated_at = datetime('now')")
                values.append(lead_id)

                conn.execute(
                    f"UPDATE leads SET {', '.join(fields)} WHERE id = ?", values
                )
                record_audit(
                    conn,
                    user.get("id"),
                    "update_lead",
                    "lead",
                    lead_id,
                    metadata={"fields": list(payload.keys())},
                )
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.lead.updated",
                {"user_id": user.get("id"), "lead_id": lead_id},
            )
            self._json({"ok": True, "lead_id": lead_id})
            return

        # PUT /api/users/:id
        if parsed.path.startswith("/api/users/") and not parsed.path.endswith(
            "/password"
        ):
            if not self._require_role(user, {"admin"}):
                return
            user_id = parsed.path[len("/api/users/") :]
            with db_connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id FROM app_users WHERE id = ?", (user_id,)
                ).fetchone()
                if not existing:
                    self._json({"error": "user_not_found"}, 404)
                    return
                for key in ["role", "email"]:
                    if key in payload:
                        conn.execute(
                            f"UPDATE app_users SET {key} = ? WHERE id = ?",
                            (payload[key], user_id),
                        )
                conn.commit()
            self._json({"ok": True, "user_id": user_id})
            return

        # PUT /api/users/:id/password
        if parsed.path.startswith("/api/users/") and parsed.path.endswith("/password"):
            user_id = parsed.path[len("/api/users/") : -len("/password")]
            new_password = payload.get("password", "")
            if not new_password or len(new_password) < 8:
                self._json({"error": "password_too_short"}, 400)
                return
            from scripts.auth_utils import hash_password

            password_hash = hash_password(new_password)
            with db_connect(self.db_path) as conn:
                # Admin can change any user's password, users can only change their own
                if user["role"] != "admin" and user["id"] != user_id:
                    self._json({"error": "forbidden"}, 403)
                    return
                conn.execute(
                    "UPDATE app_users SET password_hash = ? WHERE id = ?",
                    (password_hash, user_id),
                )
                conn.commit()
            self._json({"ok": True})
            return

        self._json({"error": "not_found"}, 404)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        user = self._require_auth()
        if not user:
            return

        # DELETE /api/leads/:id
        if parsed.path.startswith("/api/leads/"):
            if not self._require_role(user, {"admin"}):
                return
            lead_id = parsed.path[len("/api/leads/") :]
            with db_connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id FROM leads WHERE id = ?", (lead_id,)
                ).fetchone()
                if not existing:
                    self._json({"error": "lead_not_found"}, 404)
                    return
                conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
                conn.execute(
                    "DELETE FROM lead_interactions WHERE lead_id = ?", (lead_id,)
                )
                conn.execute(
                    "DELETE FROM lead_scoring_history WHERE lead_id = ?", (lead_id,)
                )
                record_audit(conn, user.get("id"), "delete_lead", "lead", lead_id)
                conn.commit()
            append_structured_log(
                "dashboard_access.jsonl",
                "dashboard.lead.deleted",
                {"user_id": user.get("id"), "lead_id": lead_id},
            )
            self._json({"ok": True, "lead_id": lead_id})
            return

        # DELETE /api/users/:id
        if parsed.path.startswith("/api/users/"):
            if not self._require_role(user, {"admin"}):
                return
            user_id = parsed.path[len("/api/users/") :]
            if user_id == user["id"]:
                self._json({"error": "cannot_delete_self"}, 400)
                return
            with db_connect(self.db_path) as conn:
                existing = conn.execute(
                    "SELECT id FROM app_users WHERE id = ?", (user_id,)
                ).fetchone()
                if not existing:
                    self._json({"error": "user_not_found"}, 404)
                    return
                conn.execute("DELETE FROM app_users WHERE id = ?", (user_id,))
                conn.commit()
            self._json({"ok": True, "user_id": user_id})
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
