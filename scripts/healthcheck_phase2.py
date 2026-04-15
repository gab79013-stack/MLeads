#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 2 healthcheck")
    parser.add_argument("--shared-context", type=Path, required=True)
    parser.add_argument(
        "--source-skill",
        type=Path,
        default=Path("/workspace/skills/lead-scoring/SKILL.md"),
    )
    parser.add_argument(
        "--runtime-skill",
        type=Path,
        default=Path("/workspace/.opencode/skills/lead-scoring/SKILL.md"),
    )
    parser.add_argument(
        "--score-script", type=Path, default=Path("/workspace/scripts/score_lead.py")
    )
    parser.add_argument(
        "--autowork-script",
        type=Path,
        default=Path("/workspace/scripts/autowork/verify_leads.py"),
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    context_ok = False
    context_payload = {}
    if args.shared_context.exists():
        context_payload = json.loads(args.shared_context.read_text(encoding="utf-8"))
        context_ok = (
            "lead_count" in context_payload and "status_counts" in context_payload
        )

    report = {
        "source_skill_exists": args.source_skill.exists(),
        "runtime_skill_exists": args.runtime_skill.exists(),
        "score_script_exists": args.score_script.exists(),
        "autowork_script_exists": args.autowork_script.exists(),
        "shared_context_exists": args.shared_context.exists(),
        "shared_context_valid": context_ok,
        "shared_context_summary": {
            "lead_count": context_payload.get("lead_count"),
            "qualified_count": context_payload.get("qualified_count"),
        },
    }
    report["ok"] = all(
        [
            report["source_skill_exists"],
            report["runtime_skill_exists"],
            report["score_script_exists"],
            report["autowork_script_exists"],
            report["shared_context_exists"],
            report["shared_context_valid"],
        ]
    )

    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
