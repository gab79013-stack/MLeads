#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${1:-/workspace/.kortix/kortix.db}"
BACKUP_DIR="${2:-/workspace/backups/phase1}"

mkdir -p "$BACKUP_DIR"

STAMP="$(date +%Y%m%d_%H%M%S)"
TARGET="$BACKUP_DIR/kortix_${STAMP}.db"

sqlite3 "$DB_PATH" ".backup '$TARGET'"
printf '%s\n' "$TARGET"
