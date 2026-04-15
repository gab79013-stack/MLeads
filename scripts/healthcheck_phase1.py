#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


REQUIRED_TABLES = [
    "leads",
    "lead_interactions",
    "lead_scoring_history",
    "dedup_pool",
]

REQUIRED_INDEXES = [
    "idx_leads_email",
    "idx_leads_status",
    "idx_leads_score",
    "idx_interactions_lead_id",
    "idx_scoring_lead_id",
]

REQUIRED_ENV_KEYS = [
    "KORTIX_HOME",
    "KORTIX_DB_PATH",
    "KORTIX_AGENTS_DIR",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "STRIPE_SECRET_KEY",
    "NEXTAUTH_SECRET",
    "AUTOWORK_INTERVAL_MINUTES",
]

AGENTS = ["lead-qualifier", "lead-nurturer", "lead-analyzer"]


def load_env_keys(path: Path) -> set[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip())
    return keys


def sqlite_names(db_path: Path, obj_type: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%'",
            (obj_type,),
        ).fetchall()
        return {row[0] for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 healthcheck")
    parser.add_argument("--db", type=Path, default=Path("/workspace/.kortix/kortix.db"))
    parser.add_argument(
        "--env-file", type=Path, default=Path("/workspace/.env.example")
    )
    parser.add_argument("--source-agents", type=Path, default=Path("/workspace/agents"))
    parser.add_argument(
        "--runtime-agents", type=Path, default=Path("/workspace/.opencode/agents")
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    tables = sqlite_names(args.db, "table") if args.db.exists() else set()
    indexes = sqlite_names(args.db, "index") if args.db.exists() else set()
    env_keys = load_env_keys(args.env_file) if args.env_file.exists() else set()

    report = {
        "db_exists": args.db.exists(),
        "integrity_check": None,
        "missing_tables": sorted(set(REQUIRED_TABLES) - tables),
        "missing_indexes": sorted(set(REQUIRED_INDEXES) - indexes),
        "missing_env_keys": sorted(set(REQUIRED_ENV_KEYS) - env_keys),
        "missing_source_agents": [],
        "missing_runtime_agents": [],
    }

    if args.db.exists():
        with sqlite3.connect(args.db) as conn:
            report["integrity_check"] = conn.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]

    for agent in AGENTS:
        if not (args.source_agents / agent / "agent.md").exists():
            report["missing_source_agents"].append(agent)
        if not (args.runtime_agents / agent / "agent.md").exists():
            report["missing_runtime_agents"].append(agent)

    report["ok"] = all(
        [
            report["db_exists"],
            report["integrity_check"] == "ok",
            not report["missing_tables"],
            not report["missing_indexes"],
            not report["missing_env_keys"],
            not report["missing_source_agents"],
            not report["missing_runtime_agents"],
        ]
    )

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
