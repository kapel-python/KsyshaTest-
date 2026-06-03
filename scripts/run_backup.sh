#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/KsyshaTest"
LOG_DIR="$ROOT_DIR/backups"
LOG_FILE="$LOG_DIR/backup.log"
DB_PATH="/workspace/data/memories.db"

mkdir -p "$LOG_DIR"

{
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] START backup"
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/backup_db.py" --db "$DB_PATH" --backup-dir "$ROOT_DIR/backups" --keep-last 14
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] END backup"
} >> "$LOG_FILE" 2>&1
