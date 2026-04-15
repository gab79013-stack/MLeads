#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from score_lead import score_lead_record  # noqa: E402
from reliability_utils import append_structured_log  # noqa: E402


def fetch_pending_leads(conn: sqlite3.Connection, batch_size: int) -> list[str]:
    rows = conn.execute(
        """
        SELECT id
        FROM leads
        WHERE status IN ('new', 'contacted')
          AND (score = 0 OR updated_at <= datetime('now', '-15 minute'))
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (batch_size,),
    ).fetchall()
    return [row[0] for row in rows]


async def process_one(
    db_path: Path,
    context_path: Path,
    lead_id: str,
    mode: str,
    semaphore: asyncio.Semaphore,
) -> dict:
    async with semaphore:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        return await asyncio.to_thread(
            score_lead_record,
            db_path,
            lead_id,
            context_path,
            mode,
            "autowork",
            f"autowork:{lead_id}:{stamp}",
        )


async def run_verification(
    db_path: Path,
    context_path: Path,
    batch_size: int,
    concurrency: int,
    mode: str,
) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        pending = fetch_pending_leads(conn, batch_size)

    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        process_one(db_path, context_path, lead_id, mode, semaphore)
        for lead_id in pending
    ]
    if not tasks:
        append_structured_log(
            "autowork.jsonl", "autowork.noop", {"batch_size": batch_size}
        )
        return []
    results = await asyncio.gather(*tasks, return_exceptions=True)
    normalized = []
    for item in results:
        if isinstance(item, Exception):
            normalized.append({"status": "error", "error": str(item)})
        else:
            normalized.append(item)
    append_structured_log(
        "autowork.jsonl",
        "autowork.completed",
        {
            "processed": len(normalized),
            "errors": sum(1 for item in normalized if item.get("status") == "error"),
        },
    )
    return normalized


def main() -> None:
    parser = argparse.ArgumentParser(description="Autowork verification loop for leads")
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
        "--batch-size",
        type=int,
        default=int(os.environ.get("AUTOWORK_BATCH_SIZE", "50")),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(os.environ.get("AUTOWORK_MAX_CONCURRENT", "5")),
    )
    parser.add_argument(
        "--mode", choices=["auto", "openai", "heuristic"], default="auto"
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    results = asyncio.run(
        run_verification(
            args.db, args.context, args.batch_size, args.concurrency, args.mode
        )
    )
    payload = json.dumps(
        {"processed": len(results), "results": results}, indent=2, ensure_ascii=False
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
