#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root/KsyshaTest}"
if [[ ! -d "$ROOT_DIR" && -d "/workspace" ]]; then
  ROOT_DIR="/workspace"
fi

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-workspace}"
cd "$ROOT_DIR"

docker compose down --remove-orphans || true
docker rm -f ksysha-bot ksysha-cloudflared 2>/dev/null || true
exec "$ROOT_DIR/scripts/restart_clean.sh"
