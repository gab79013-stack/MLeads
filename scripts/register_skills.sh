#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${KORTIX_SOURCE_SKILLS_DIR:-/workspace/skills}"
TARGET_DIR="${KORTIX_SKILLS_DIR:-/workspace/.opencode/skills}"

mkdir -p "$TARGET_DIR/lead-scoring"
cp "$SOURCE_DIR/lead-scoring/SKILL.md" "$TARGET_DIR/lead-scoring/SKILL.md"

printf 'Registered skills in %s\n' "$TARGET_DIR"
