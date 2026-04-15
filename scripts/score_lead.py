#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reliability_utils import (
    acquire_lock,
    append_structured_log,
    begin_idempotent,
    complete_idempotent,
    fail_idempotent,
    record_audit,
    release_lock,
    stable_payload_hash,
)


FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
}

SOURCE_WEIGHTS = {
    "referral": 90,
    "inbound": 88,
    "event": 82,
    "web_form": 75,
    "linkedin": 68,
    "partner": 80,
    "ads": 55,
    "cold_email": 45,
    "manual": 50,
    "import": 40,
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "factors": {
            "type": "object",
            "properties": {
                "domain_quality": {"type": "number", "minimum": 0, "maximum": 100},
                "engagement": {"type": "number", "minimum": 0, "maximum": 100},
                "company_fit": {"type": "number", "minimum": 0, "maximum": 100},
                "source_quality": {"type": "number", "minimum": 0, "maximum": 100},
                "completeness": {"type": "number", "minimum": 0, "maximum": 100},
            },
            "required": [
                "domain_quality",
                "engagement",
                "company_fit",
                "source_quality",
                "completeness",
            ],
            "additionalProperties": False,
        },
        "reasoning": {"type": "string"},
        "recommendation": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["new", "contacted", "qualified", "proposal", "won", "lost"],
        },
    },
    "required": [
        "score",
        "confidence",
        "factors",
        "reasoning",
        "recommendation",
        "status",
    ],
    "additionalProperties": False,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def load_json_file(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_lead(conn: sqlite3.Connection, lead_id: str) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return dict(row) if row else None


def fetch_interactions(conn: sqlite3.Connection, lead_id: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT type, content, agent_id, created_at FROM lead_interactions WHERE lead_id = ? ORDER BY created_at DESC LIMIT 10",
        (lead_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_scoring_history(
    conn: sqlite3.Connection, lead_id: str
) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT score, factors, model, created_at FROM lead_scoring_history WHERE lead_id = ? ORDER BY created_at DESC LIMIT 5",
        (lead_id,),
    ).fetchall()
    history = []
    for row in rows:
        item = dict(row)
        try:
            item["factors"] = json.loads(item.get("factors") or "{}")
        except json.JSONDecodeError:
            item["factors"] = {}
        history.append(item)
    return history


def resolve_duplicate_reference(
    conn: sqlite3.Connection, lead: dict[str, Any]
) -> str | None:
    email = normalize_email(lead["email"])
    fingerprint = hashlib.md5(email.encode()).hexdigest()
    existing = conn.execute(
        "SELECT lead_id FROM dedup_pool WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if existing and existing[0] != lead["id"]:
        return str(existing[0])

    match = conn.execute(
        "SELECT duplicate_lead_id FROM lead_duplicate_matches WHERE lead_id = ? AND status = 'open' ORDER BY match_score DESC LIMIT 1",
        (lead["id"],),
    ).fetchone()
    if match:
        return str(match[0])

    reverse = conn.execute(
        "SELECT lead_id FROM lead_duplicate_matches WHERE duplicate_lead_id = ? AND status = 'open' ORDER BY match_score DESC LIMIT 1",
        (lead["id"],),
    ).fetchone()
    if reverse:
        return str(reverse[0])

    conn.execute(
        "INSERT OR IGNORE INTO dedup_pool (fingerprint, lead_id, potential_matches, resolved) VALUES (?, ?, '[]', 0)",
        (fingerprint, lead["id"]),
    )
    return None


def heuristic_score(
    lead: dict[str, Any],
    interactions: list[dict[str, Any]],
    history: list[dict[str, Any]],
    shared_context: dict[str, Any],
    duplicate_of: str | None,
) -> dict[str, Any]:
    email = normalize_email(lead["email"])
    domain = email.split("@", 1)[1] if "@" in email else ""
    domain_quality = 25 if domain in FREE_EMAIL_DOMAINS else 88 if domain else 0
    completeness_fields = [
        lead.get("first_name"),
        lead.get("last_name"),
        lead.get("company"),
        lead.get("phone"),
        lead.get("source"),
        lead.get("notes"),
    ]
    completeness = round(
        sum(1 for item in completeness_fields if item) / len(completeness_fields) * 100
    )
    source_quality = SOURCE_WEIGHTS.get(str(lead.get("source") or "manual").lower(), 50)
    interaction_bonus = 30 + min(len(interactions) * 10, 40)
    if any(item["type"] == "meeting" for item in interactions):
        interaction_bonus += 10
    engagement = min(interaction_bonus, 100)
    company_fit = 40
    if lead.get("company"):
        company_fit += 25
    if domain and domain not in FREE_EMAIL_DOMAINS:
        company_fit += 20
    top_companies = {
        item["company"] for item in shared_context.get("top_companies", [])
    }
    if lead.get("company") in top_companies:
        company_fit += 5
    company_fit = min(company_fit, 100)
    previous_avg = (
        sum(float(item["score"]) for item in history) / len(history) if history else 0
    )
    weighted = (
        domain_quality * 0.25
        + engagement * 0.25
        + company_fit * 0.2
        + source_quality * 0.2
        + completeness * 0.1
    )
    score = round(min(max(weighted * 0.9 + previous_avg * 0.1, 0), 100), 2)
    if duplicate_of:
        score = round(max(score - 12, 0), 2)
    confidence = round(
        min(0.45 + completeness / 200 + len(interactions) * 0.03, 0.98), 2
    )
    status = "qualified" if score >= 80 else "contacted" if score >= 50 else "new"
    reasoning = f"Scoring heurístico basado en dominio={domain_quality}, engagement={engagement}, company_fit={company_fit}, source={source_quality}, completeness={completeness}."
    if duplicate_of:
        reasoning += f" Señalado como posible duplicado de {duplicate_of}."
    return {
        "score": score,
        "confidence": confidence,
        "factors": {
            "domain_quality": domain_quality,
            "engagement": engagement,
            "company_fit": company_fit,
            "source_quality": source_quality,
            "completeness": completeness,
        },
        "reasoning": reasoning,
        "recommendation": "Review duplicate before outreach"
        if duplicate_of
        else ("Promote to sales" if status == "qualified" else "Continue nurturing"),
        "status": status,
        "model": "heuristic-v2",
    }


def build_prompt(
    lead: dict[str, Any],
    interactions: list[dict[str, Any]],
    history: list[dict[str, Any]],
    shared_context: dict[str, Any],
    duplicate_of: str | None,
) -> str:
    payload = {
        "shared_context": shared_context,
        "lead": lead,
        "interactions": interactions,
        "history": history,
        "duplicate_of": duplicate_of,
    }
    return (
        "Eres un experto en calificación de leads B2B. Devuelve solo JSON válido con las llaves score, confidence, factors, reasoning, recommendation y status. El score debe ser 0-100.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def parse_openai_response(raw: str, model: str) -> dict[str, Any]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("OpenAI response did not contain JSON")
    parsed = json.loads(raw[start : end + 1])
    parsed["model"] = model
    return parsed


def extract_openai_text(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if (
                isinstance(content, dict)
                and content.get("type") == "output_text"
                and content.get("text")
            ):
                texts.append(content["text"])
    if texts:
        return "\n".join(texts)
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    raise ValueError("OpenAI response did not include text output")


def call_openai(prompt: str, model: str, max_output_tokens: int) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    body = json.dumps(
        {
            "model": model,
            "input": prompt,
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "lead_score",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                }
            },
        }
    ).encode()
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    return parse_openai_response(extract_openai_text(payload), model)


def persist_result(
    conn: sqlite3.Connection,
    lead_id: str,
    result: dict[str, Any],
    duplicate_of: str | None,
) -> None:
    status = result.get("status") or (
        "qualified"
        if result["score"] >= 80
        else "contacted"
        if result["score"] >= 50
        else "new"
    )
    conn.execute(
        "INSERT INTO lead_scoring_history (lead_id, score, factors, model, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            lead_id,
            float(result["score"]),
            json.dumps(
                {**result.get("factors", {}), "duplicate_of": duplicate_of},
                ensure_ascii=False,
            ),
            result.get("model", "unknown"),
            utc_now(),
        ),
    )
    conn.execute(
        "UPDATE leads SET score = ?, status = ?, updated_at = ? WHERE id = ?",
        (float(result["score"]), status, utc_now(), lead_id),
    )
    conn.execute(
        "INSERT INTO lead_interactions (lead_id, type, content, agent_id, metadata, created_at) VALUES (?, 'scoring', ?, 'lead-scoring', ?, ?)",
        (
            lead_id,
            f"score={result['score']} confidence={result.get('confidence')} status={status}",
            json.dumps(
                {"model": result.get("model"), "duplicate_of": duplicate_of},
                ensure_ascii=False,
            ),
            utc_now(),
        ),
    )


def score_lead_record(
    db_path: Path,
    lead_id: str,
    shared_context_path: Path | None = None,
    mode: str = "auto",
    actor_id: str = "lead-scoring",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    shared_context = load_json_file(shared_context_path)
    with sqlite3.connect(db_path) as conn:
        lock_owner = actor_id or "lead-scoring"
        if not acquire_lock(conn, "lead", lead_id, lock_owner, ttl_seconds=180):
            append_structured_log(
                "lead_scoring.jsonl",
                "lead.lock_busy",
                {"lead_id": lead_id, "actor_id": actor_id},
            )
            raise RuntimeError(f"Lead {lead_id} is locked by another worker")
        try:
            lead = fetch_lead(conn, lead_id)
            if not lead:
                raise ValueError(f"Lead not found: {lead_id}")
            interactions = fetch_interactions(conn, lead_id)
            history = fetch_scoring_history(conn, lead_id)
            duplicate_of = resolve_duplicate_reference(conn, lead)
            payload_basis = {
                "lead_id": lead_id,
                "mode": mode,
                "context_hash": stable_payload_hash(shared_context),
                "duplicate_of": duplicate_of,
            }
            if idempotency_key:
                state = begin_idempotent(
                    conn, "score_lead", idempotency_key, payload_basis
                )
                if state["replayed"]:
                    record_audit(
                        conn,
                        actor_id,
                        "score_lead_replayed",
                        "lead",
                        lead_id,
                        status="replayed",
                        metadata={"idempotency_key": idempotency_key},
                    )
                    conn.commit()
                    response = state["response"]
                    response["idempotent_replay"] = True
                    append_structured_log(
                        "lead_scoring.jsonl",
                        "lead.scoring.replayed",
                        {
                            "lead_id": lead_id,
                            "actor_id": actor_id,
                            "idempotency_key": idempotency_key,
                        },
                    )
                    return response

            model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
            max_tokens = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "1024"))
            selected_mode = (
                mode if mode != "auto" else os.environ.get("LEAD_SCORING_MODE", "auto")
            )
            if selected_mode == "openai" or (
                selected_mode == "auto" and os.environ.get("OPENAI_API_KEY")
            ):
                prompt = build_prompt(
                    lead, interactions, history, shared_context, duplicate_of
                )
                try:
                    result = call_openai(prompt, model, max_tokens)
                except (
                    RuntimeError,
                    urllib.error.URLError,
                    TimeoutError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    result = heuristic_score(
                        lead, interactions, history, shared_context, duplicate_of
                    )
                    result["reasoning"] += " Fallback a heurística por fallo de OpenAI."
            else:
                result = heuristic_score(
                    lead, interactions, history, shared_context, duplicate_of
                )

            result["lead_id"] = lead_id
            result["duplicate_of"] = duplicate_of
            persist_result(conn, lead_id, result, duplicate_of)
            record_audit(
                conn,
                actor_id,
                "score_lead",
                "lead",
                lead_id,
                metadata={
                    "score": result["score"],
                    "duplicate_of": duplicate_of,
                    "mode": selected_mode,
                },
            )
            if idempotency_key:
                complete_idempotent(conn, "score_lead", idempotency_key, result)
            conn.commit()
            append_structured_log(
                "lead_scoring.jsonl",
                "lead.scored",
                {
                    "lead_id": lead_id,
                    "actor_id": actor_id,
                    "score": result["score"],
                    "duplicate_of": duplicate_of,
                },
            )
            return result
        except Exception:
            if idempotency_key:
                fail_idempotent(conn, "score_lead", idempotency_key)
                conn.commit()
            append_structured_log(
                "lead_scoring.jsonl",
                "lead.scoring.failed",
                {"lead_id": lead_id, "actor_id": actor_id},
            )
            raise
        finally:
            release_lock(conn, "lead", lead_id, lock_owner)
            conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score a lead with OpenAI or heuristic fallback"
    )
    parser.add_argument("--lead-id", required=True)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("KORTIX_DB_PATH", "/workspace/.kortix/kortix.db")),
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=Path(
            os.environ.get(
                "KORTIX_SHARED_CONTEXT_PATH",
                "/workspace/.kortix/memory/shared-context.json",
            )
        ),
    )
    parser.add_argument(
        "--mode", choices=["auto", "openai", "heuristic"], default="auto"
    )
    parser.add_argument("--actor", default="lead-scoring-cli")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()
    result = score_lead_record(
        args.db,
        args.lead_id,
        args.context,
        args.mode,
        actor_id=args.actor,
        idempotency_key=args.idempotency_key,
    )
    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
