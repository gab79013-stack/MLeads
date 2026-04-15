#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from reliability_utils import normalize_company, normalize_phone, utc_now


def load_leads(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, first_name, last_name, email, phone, company, source, status FROM leads ORDER BY created_at ASC"
    ).fetchall()
    return [dict(row) for row in rows]


def candidate_matches(leads: list[dict]) -> list[dict]:
    matches: list[dict] = []
    for index, lead in enumerate(leads):
        for other in leads[index + 1 :]:
            reasons = []
            score = 0.0
            if normalize_phone(lead.get("phone")) and normalize_phone(
                lead.get("phone")
            ) == normalize_phone(other.get("phone")):
                reasons.append("phone")
                score += 0.5
            if normalize_company(lead.get("company")) and normalize_company(
                lead.get("company")
            ) == normalize_company(other.get("company")):
                reasons.append("company")
                score += 0.25
            if (
                lead.get("first_name")
                and other.get("first_name")
                and str(lead["first_name"]).lower() == str(other["first_name"]).lower()
            ):
                reasons.append("first_name")
                score += 0.1
            if (
                lead.get("last_name")
                and other.get("last_name")
                and str(lead["last_name"]).lower() == str(other["last_name"]).lower()
            ):
                reasons.append("last_name")
                score += 0.1
            lead_email = str(lead.get("email") or "")
            other_email = str(other.get("email") or "")
            if (
                lead_email
                and other_email
                and lead_email.split("@", 1)[0] == other_email.split("@", 1)[0]
            ):
                reasons.append("email_local_part")
                score += 0.2

            if score >= 0.6:
                matches.append(
                    {
                        "lead_id": lead["id"],
                        "duplicate_lead_id": other["id"],
                        "match_reason": "+".join(reasons),
                        "match_score": round(min(score, 0.99), 2),
                    }
                )
    return matches


def persist_matches(conn: sqlite3.Connection, matches: list[dict]) -> None:
    conn.execute("DELETE FROM lead_duplicate_matches")
    for match in matches:
        conn.execute(
            "INSERT INTO lead_duplicate_matches (lead_id, duplicate_lead_id, match_reason, match_score, status, metadata, created_at, updated_at) VALUES (?, ?, ?, ?, 'open', '{}', ?, ?)",
            (
                match["lead_id"],
                match["duplicate_lead_id"],
                match["match_reason"],
                match["match_score"],
                utc_now(),
                utc_now(),
            ),
        )

    conn.row_factory = sqlite3.Row
    lead_ids = [row[0] for row in conn.execute("SELECT id FROM leads").fetchall()]
    for lead_id in lead_ids:
        related = conn.execute(
            "SELECT duplicate_lead_id FROM lead_duplicate_matches WHERE lead_id = ? UNION SELECT lead_id FROM lead_duplicate_matches WHERE duplicate_lead_id = ?",
            (lead_id, lead_id),
        ).fetchall()
        if not related:
            continue
        potential_matches = json.dumps(
            sorted({row[0] for row in related}), ensure_ascii=False
        )
        conn.execute(
            "UPDATE dedup_pool SET potential_matches = ?, resolved = 0 WHERE lead_id = ?",
            (potential_matches, lead_id),
        )
    conn.commit()


def summarize(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM lead_duplicate_matches").fetchone()[0]
    open_count = conn.execute(
        "SELECT COUNT(*) FROM lead_duplicate_matches WHERE status = 'open'"
    ).fetchone()[0]
    return {"total_matches": total, "open_matches": open_count}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile duplicate leads across agents"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        matches = candidate_matches(load_leads(conn))
        persist_matches(conn, matches)
        summary = summarize(conn)
        payload = {
            "matches": matches,
            "summary": summary,
        }

    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
