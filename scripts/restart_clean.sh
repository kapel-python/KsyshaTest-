#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/root/KsyshaTest}"
if [[ ! -d "$ROOT_DIR" && -d "/workspace" ]]; then
  ROOT_DIR="/workspace"
fi
COMPOSE_FILE="$ROOT_DIR/docker-compose.yml"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-workspace}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
WAIT_TIMEOUT_SEC="${WAIT_TIMEOUT_SEC:-180}"
WAIT_STEP_SEC="${WAIT_STEP_SEC:-4}"
MEDIA_PROBE_PATH="${MEDIA_PROBE_PATH:-/media/20260526_114711_34909ca2.jpg}"
SKIP_MEDIA_PROBE="${SKIP_MEDIA_PROBE:-0}"
STRICT_MEDIA_PROBE="${STRICT_MEDIA_PROBE:-0}"
STATUS_FILE="${STATUS_FILE:-$ROOT_DIR/data/restart_status.json}"

log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*"
}

write_status() {
  local status="$1"
  local message="${2:-}"
  local attempt="${3:-0}"
  local now
  now="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p "$(dirname "$STATUS_FILE")"
  cat > "$STATUS_FILE" <<EOF
{"status":"$status","message":"$message","attempt":$attempt,"updated_at":"$now"}
EOF
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    log "ERROR: required command not found: $1"
    exit 1
  }
}

wait_healthy() {
  local container="$1"
  local waited=0
  while (( waited < WAIT_TIMEOUT_SEC )); do
    local state
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container" 2>/dev/null || true)"
    local running
    running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)"

    if [[ "$running" == "true" && ( "$state" == "healthy" || "$state" == "none" ) ]]; then
      log "OK: $container running (health=$state)"
      return 0
    fi

    sleep "$WAIT_STEP_SEC"
    waited=$((waited + WAIT_STEP_SEC))
  done

  log "ERROR: timeout waiting for $container to become healthy/running"
  docker ps --format '{{.Names}} {{.Status}}' | rg 'ksysha-(bot|cloudflared)' || true
  docker logs --tail 120 "$container" 2>/dev/null || true
  return 1
}

probe_media_block() {
  if [[ "$SKIP_MEDIA_PROBE" == "1" ]]; then
    log "INFO: media probe skipped (SKIP_MEDIA_PROBE=1)"
    return 0
  fi
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:25086${MEDIA_PROBE_PATH}" || true)"
  if [[ "$code" != "403" ]]; then
    if [[ "$STRICT_MEDIA_PROBE" == "1" ]]; then
      log "ERROR: media protection check failed, expected 403, got $code"
      return 1
    fi
    log "WARN: media protection check returned $code (strict disabled), continue"
    return 0
  fi
  log "OK: media protection check returned 403"
}

main() {
  require_cmd docker
  require_cmd curl
  require_cmd rg

  cd "$ROOT_DIR"
  export COMPOSE_PROJECT_NAME
  [[ -f "$COMPOSE_FILE" ]] || {
    write_status "failed" "compose_file_not_found" 0
    log "ERROR: compose file not found: $COMPOSE_FILE"
    exit 1
  }

  # Capture the current git HEAD so it is baked into the Docker image via the
  # GIT_COMMIT build-arg declared in docker-compose.yml.  Without this export,
  # compose receives an empty string and the Dockerfile falls back to "unknown".
  GIT_COMMIT="$(git -C "$ROOT_DIR" rev-parse HEAD 2>/dev/null || true)"
  if [[ -z "$GIT_COMMIT" ]]; then
    log "WARN: could not read git HEAD; GIT_COMMIT will be 'unknown' in image"
  else
    log "INFO: baking GIT_COMMIT=${GIT_COMMIT} into image"
  fi
  export GIT_COMMIT

  write_status "running" "restart_started" 0
  local attempt=1
  while (( attempt <= MAX_ATTEMPTS )); do
    write_status "running" "attempt_${attempt}" "$attempt"
    log "Attempt $attempt/$MAX_ATTEMPTS: clean recreate"

    docker compose down --remove-orphans || true
    docker rm -f ksysha-bot ksysha-cloudflared 2>/dev/null || true
    docker compose up -d --build --force-recreate

    if wait_healthy "ksysha-bot" && wait_healthy "ksysha-cloudflared" && probe_media_block; then
      write_status "success" "restart_verified" "$attempt"
      log "SUCCESS: stack is up and verified"
      docker compose ps
      exit 0
    fi

    log "WARN: attempt $attempt failed"
    attempt=$((attempt + 1))
    sleep 3
  done

  write_status "failed" "all_attempts_failed" "$MAX_ATTEMPTS"
  log "ERROR: all attempts failed"
  docker compose ps || true
  exit 1
}

main "$@"
