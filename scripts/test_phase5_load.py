#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def request_json(
    url: str,
    payload: dict | None = None,
    token: str | None = None,
    headers: dict | None = None,
) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=body, method="POST" if payload is not None else "GET"
    )
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def login(base_url: str, email: str, password: str) -> str:
    result = request_json(
        f"{base_url}/api/login", {"email": email, "password": password}
    )
    return result["token"]


def create_payload(index: int) -> dict:
    return {
        "first_name": f"Load{index}",
        "last_name": "Tester",
        "email": f"load{index}@example.com",
        "company": f"Company {index % 15}",
        "source": "load_test",
        "notes": "Phase 5 load test",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 5 load test against dashboard API"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--total", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    token = login(args.base_url, args.email, args.password)
    start = time.time()
    successes = 0
    failures = 0
    responses = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for index in range(args.total):
            payload = create_payload(index)
            futures.append(
                executor.submit(
                    request_json,
                    f"{args.base_url}/api/leads",
                    payload,
                    token,
                    {"X-Idempotency-Key": f"phase5-load-{index}"},
                )
            )
        for future in as_completed(futures):
            try:
                result = future.result()
                responses.append(result)
                if result.get("lead_id"):
                    successes += 1
                else:
                    failures += 1
            except urllib.error.URLError as exc:
                failures += 1
                responses.append({"error": str(exc)})

    elapsed = time.time() - start
    rate = successes / (elapsed / 3600) if elapsed else 0
    summary = {
        "total": args.total,
        "successes": successes,
        "failures": failures,
        "elapsed_seconds": round(elapsed, 2),
        "rate_per_hour": round(rate, 2),
        "target_met": rate >= 100,
    }
    payload = {"summary": summary, "responses_sample": responses[:10]}
    rendered = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
