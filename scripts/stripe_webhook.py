#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from reliability_utils import (
    append_structured_log,
    begin_idempotent,
    complete_idempotent,
    record_audit,
)


PLAN_LIMITS = {
    "free": 100,
    "starter": 1000,
    "pro": 10000,
    "enterprise": 100000,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def verify_signature(payload: bytes, signature_header: str, secret: str | None) -> bool:
    if not secret:
        return True
    parts = dict(
        part.split("=", 1) for part in signature_header.split(",") if "=" in part
    )
    timestamp = parts.get("t")
    signature = parts.get("v1")
    if not timestamp or not signature:
        return False
    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_event(conn: sqlite3.Connection, event: dict) -> None:
    event_id = event.get("id")
    event_type = event.get("type")
    if not event_id or not event_type:
        return

    state = begin_idempotent(conn, "stripe_webhook", event_id, event)
    if state["replayed"]:
        record_audit(
            conn,
            "stripe",
            "webhook_replayed",
            "stripe_event",
            event_id,
            status="replayed",
            metadata={"event_type": event_type},
        )
        conn.commit()
        append_structured_log(
            "stripe_webhook.jsonl",
            "stripe.webhook.replayed",
            {"event_id": event_id, "event_type": event_type},
        )
        return

    already = conn.execute(
        "SELECT 1 FROM webhook_events WHERE provider = 'stripe' AND event_id = ?",
        (event_id,),
    ).fetchone()
    if already:
        complete_idempotent(
            conn,
            "stripe_webhook",
            event_id,
            {"event_id": event_id, "status": "already_recorded"},
        )
        conn.commit()
        return

    conn.execute(
        "INSERT INTO webhook_events (provider, event_id, event_type, status, payload, created_at) VALUES ('stripe', ?, ?, 'received', ?, ?)",
        (event_id, event_type, json.dumps(event, ensure_ascii=False), utc_now()),
    )

    obj = event.get("data", {}).get("object", {})
    if event_type == "checkout.session.completed":
        user_id = obj.get("client_reference_id") or "user_local_admin"
        plan = obj.get("metadata", {}).get("plan", "starter")
        customer = obj.get("customer") or f"cus_{user_id}"
        subscription = obj.get("subscription") or f"sub_{user_id}"
        conn.execute(
            """
            INSERT INTO subscriptions (id, user_id, stripe_customer_id, stripe_subscription_id, plan, status, leads_limit, current_leads, features, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, 0, '["dashboard","billing","notifications"]', ?, ?)
            ON CONFLICT(id) DO UPDATE SET stripe_customer_id = excluded.stripe_customer_id, stripe_subscription_id = excluded.stripe_subscription_id, plan = excluded.plan, status = 'active', leads_limit = excluded.leads_limit, updated_at = excluded.updated_at
            """,
            (
                subscription,
                user_id,
                customer,
                subscription,
                plan,
                PLAN_LIMITS.get(plan, 100),
                utc_now(),
                utc_now(),
            ),
        )
    elif event_type == "invoice.paid":
        customer = obj.get("customer")
        invoice_id = obj.get("id")
        amount = (obj.get("amount_paid") or 0) / 100
        sub = conn.execute(
            "SELECT id FROM subscriptions WHERE stripe_customer_id = ? ORDER BY updated_at DESC LIMIT 1",
            (customer,),
        ).fetchone()
        if sub:
            conn.execute(
                "INSERT OR REPLACE INTO payments (id, subscription_id, stripe_payment_id, amount, currency, status, metadata, created_at) VALUES (?, ?, ?, ?, ?, 'paid', ?, ?)",
                (
                    f"pay_{invoice_id}",
                    sub[0],
                    obj.get("payment_intent"),
                    amount,
                    obj.get("currency", "usd"),
                    json.dumps(obj, ensure_ascii=False),
                    utc_now(),
                ),
            )
            conn.execute(
                "UPDATE subscriptions SET status = 'active', updated_at = ? WHERE id = ?",
                (utc_now(), sub[0]),
            )
    elif event_type == "customer.subscription.deleted":
        subscription = obj.get("id")
        conn.execute(
            "UPDATE subscriptions SET status = 'cancelled', plan = 'free', leads_limit = 100, updated_at = ? WHERE id = ?",
            (utc_now(), subscription),
        )

    conn.execute(
        "UPDATE webhook_events SET status = 'processed', processed_at = ? WHERE provider = 'stripe' AND event_id = ?",
        (utc_now(), event_id),
    )
    record_audit(
        conn,
        "stripe",
        "webhook_processed",
        "stripe_event",
        event_id,
        metadata={"event_type": event_type},
    )
    complete_idempotent(
        conn,
        "stripe_webhook",
        event_id,
        {"event_id": event_id, "event_type": event_type, "status": "processed"},
    )
    conn.commit()
    append_structured_log(
        "stripe_webhook.jsonl",
        "stripe.webhook.processed",
        {"event_id": event_id, "event_type": event_type},
    )


class StripeHandler(BaseHTTPRequestHandler):
    @property
    def db_path(self) -> str:
        return self.server.db_path  # type: ignore[attr-defined]

    @property
    def webhook_secret(self) -> str | None:
        return self.server.webhook_secret  # type: ignore[attr-defined]

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/stripe/webhook":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        signature = self.headers.get("Stripe-Signature", "")
        if not verify_signature(payload, signature, self.webhook_secret):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"error":"invalid_signature"}')
            return
        event = json.loads(payload.decode())
        with sqlite3.connect(self.db_path) as conn:
            handle_event(conn, event)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received":true}')


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Stripe webhook server")
    parser.add_argument(
        "--db", default=os.environ.get("KORTIX_DB_PATH", "/workspace/.kortix/kortix.db")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("STRIPE_WEBHOOK_PORT", "43124"))
    )
    parser.add_argument(
        "--webhook-secret", default=os.environ.get("STRIPE_WEBHOOK_SECRET")
    )
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), StripeHandler)
    server.db_path = args.db  # type: ignore[attr-defined]
    server.webhook_secret = args.webhook_secret  # type: ignore[attr-defined]
    print(f"Stripe webhook listening on http://127.0.0.1:{args.port}/stripe/webhook")
    server.serve_forever()


if __name__ == "__main__":
    main()
