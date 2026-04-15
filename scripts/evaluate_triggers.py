#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from trigger_utils import evaluate_triggers, get_trigger_definitions


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate phase 3 notifications and trigger rules"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    with sqlite3.connect(args.db) as conn:
        notifications = evaluate_triggers(conn)

    payload = json.dumps(
        {
            "triggers": get_trigger_definitions(),
            "notifications_created": notifications,
            "count": len(notifications),
        },
        indent=2,
        ensure_ascii=False,
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
