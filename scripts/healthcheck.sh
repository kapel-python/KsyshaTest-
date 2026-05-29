#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/root/KsyshaTest"
LOG_DIR="$ROOT_DIR/backups"
LOG_FILE="$LOG_DIR/healthcheck.log"
MAX_BACKUP_AGE_HOURS="${MAX_BACKUP_AGE_HOURS:-30}"
MIN_FREE_GB="${MIN_FREE_GB:-2}"

mkdir -p "$LOG_DIR"

fail() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] FAIL: $1" | tee -a "$LOG_FILE"
  exit 1
}

ok() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] OK: $1" | tee -a "$LOG_FILE"
}

# 1) Containers
for c in ksysha-bot ksysha-cloudflared; do
  status=$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null || true)
  [[ "$status" == "true" ]] || fail "container $c is not running"
done
ok "all containers running"

# 2) API health
code=$(curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:25086/stats' || true)
[[ "$code" == "200" || "$code" == "302" ]] || fail "http /stats returned $code"
ok "http /stats=$code"

# 3) Backup freshness
latest_backup=$(ls -1t "$ROOT_DIR"/backups/memories-*.sqlite3.gz 2>/dev/null | head -n1 || true)
[[ -n "$latest_backup" ]] || fail "no backup files found"

now_ts=$(date -u +%s)
backup_ts=$(stat -c %Y "$latest_backup")
age_hours=$(( (now_ts - backup_ts) / 3600 ))
(( age_hours <= MAX_BACKUP_AGE_HOURS )) || fail "latest backup too old: ${age_hours}h > ${MAX_BACKUP_AGE_HOURS}h"
ok "backup freshness ${age_hours}h"

# 4) Disk free space
avail_kb=$(df -Pk "$ROOT_DIR" | awk 'NR==2 {print $4}')
avail_gb=$(( avail_kb / 1024 / 1024 ))
(( avail_gb >= MIN_FREE_GB )) || fail "low disk: ${avail_gb}GB < ${MIN_FREE_GB}GB"
ok "disk free ${avail_gb}GB"

exit 0
