#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-/workspace/.kortix/kortix.db}"
BACKUP_ROOT="${2:-/workspace/backups/predeploy}"
STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET_DIR="$BACKUP_ROOT/$STAMP"

mkdir -p "$TARGET_DIR"
sqlite3 "$DB_PATH" ".backup '$TARGET_DIR/kortix.db'"
cp "/workspace/.env.example" "$TARGET_DIR/.env.example"
cp "/workspace/.kortix/triggers.yaml" "$TARGET_DIR/triggers.yaml"
printf '%s\n' "$TARGET_DIR"
