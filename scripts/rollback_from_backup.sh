#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:?Usage: rollback_from_backup.sh <backup_dir> [db_path]}"
DB_PATH="${2:-/workspace/.kortix/kortix.db}"

if [ ! -f "$BACKUP_DIR/kortix.db" ]; then
  printf 'Missing backup DB: %s\n' "$BACKUP_DIR/kortix.db" >&2
  exit 1
fi

sqlite3 "$DB_PATH" ".restore '$BACKUP_DIR/kortix.db'"
sqlite3 "$DB_PATH" "PRAGMA integrity_check;"
