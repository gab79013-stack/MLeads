#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${KORTIX_SOURCE_AGENTS_DIR:-/workspace/agents}"
TARGET_DIR="${KORTIX_AGENTS_DIR:-/workspace/.opencode/agents}"

mkdir -p "$TARGET_DIR"

for agent in lead-qualifier lead-nurturer lead-analyzer; do
  mkdir -p "$TARGET_DIR/$agent"
  cp "$SOURCE_DIR/$agent/agent.md" "$TARGET_DIR/$agent/agent.md"
done

printf 'Registered agents in %s\n' "$TARGET_DIR"
