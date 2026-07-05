#!/usr/bin/env python3
"""Interactively configure Stripe billing variables in .env.production."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
from pathlib import Path


SECRET_KEYS = {"STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET"}
REQUIRED_KEYS = [
    "STRIPE_API_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_ID_PRO",
    "STRIPE_PRICE_ID_QUALITY",
    "STRIPE_PRICE_ID_PREMIUM",
    "STRIPE_PRICE_ID_ELITE",
    "BASE_URL",
]


def parse_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def prompt_value(key: str, current: str, non_interactive: bool) -> str:
    env_value = os.getenv(key)
    if env_value is not None:
        return env_value.strip()
    if non_interactive:
        return current

    label = f"{key}"
    if current:
        label += " [configured, press Enter to keep]"
    label += ": "
    if key in SECRET_KEYS:
        value = getpass.getpass(label)
    else:
        value = input(label)
    return value.strip() or current


def render_env(lines: list[str], updates: dict[str, str]) -> str:
    seen = set()
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                rendered.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        rendered.append(line)

    missing = [key for key in REQUIRED_KEYS if key not in seen]
    if missing:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append("# Stripe billing for web subscriptions")
        for key in missing:
            rendered.append(f"{key}={updates.get(key, '')}")

    return "\n".join(rendered).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure Stripe billing env vars for Docker production.")
    parser.add_argument("--env-file", default=".env.production", help="Path to env file to update.")
    parser.add_argument("--non-interactive", action="store_true", help="Read values from current environment only.")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    lines, current = parse_env(env_path)
    updates = {key: prompt_value(key, current.get(key, ""), args.non_interactive) for key in REQUIRED_KEYS}

    missing = [key for key in REQUIRED_KEYS if not updates.get(key)]
    if missing:
        print("Missing required billing values:")
        for key in missing:
            print(f"  - {key}")
        print("No changes written.")
        return 2

    if env_path.exists():
        backup = env_path.with_suffix(env_path.suffix + ".bak")
        shutil.copy2(env_path, backup)
        print(f"Backup written: {backup}")

    env_path.write_text(render_env(lines, updates), encoding="utf-8")
    env_path.chmod(0o600)
    print(f"Updated {env_path}")
    print("Next: docker compose up -d --build web")
    print("Then check the admin Billing readiness card.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
