# KsyshaTest: Stable Deploy and Recovery Runbook & Onboarding Guide

## Quick Start for New AI Agents
Welcome! If you only have 5 minutes to understand this project:
1. This is a Telegram bot + Web UI application for a personal project (memories/couples log). 
2. **Never modify `database.py` or `.env` blindly.** Schema changes are dangerous.
3. If asked to deploy or restart, use the bot's Admin Panel or the established scripts, don't just run `docker restart`.
4. Run syntax checks (`python3 -m py_compile <file>`) before deploying changes.
5. The version is strictly tracked in `app_version.py` (`version = 'X.Y.Z'`), NOT inside `config.py`.
6. The app relies on a `deployer` microservice running alongside the bot to handle safe container recreation.
7. "Safe restart" or "Safe rollback" is a priority - always verify everything via `docker ps` and `/health`!
8. **Prefer audits and evidence before redesigns.** Many past incidents were caused by making architectural assumptions before verifying runtime behavior.

---

## Project Overview
**What the project is:**
KsyshaTest is a personal Telegram bot and companion web app designed to securely store and display shared memories, events, and important dates for a couple.

**Main components:**
- **Telegram bot:** Built with `aiogram`, provides user interfaces, admin menus, notifications, and interactive management of records.
- **Web interface:** A static/Jinja-like HTML frontend served asynchronously by `aiohttp` in `http_api.py`. It acts as an interactive web viewer for memories.
- **Database:** SQLite (`memories.db`), encapsulated in `database.py`. Features hot-backup mechanisms.
- **Docker services:**
  - `ksysha-bot`: Main application container (Bot + Web Server).
  - `ksysha-deployer`: Sidecar microservice exposing an API (`/deploy` and `/health`) with access to the Docker socket to safely update `ksysha-bot` and `cloudflared`.
  - `ksysha-cloudflared`: Cloudflare Tunnel connector to securely expose the web interface.

---

## Known Stable Systems (Stable as of May 2026 validation)
Do not redesign these systems without a specific reproducible bug report:
- Release workflow is stable
- Rollback workflow is stable
- Restart workflow is stable
- Local safe restart fallback is stable
- Version detection is stable
- Telegram WebView navigation bug is resolved

---

## Architecture
**Key files:**
- `handlers.py`: Main bot logic, admin menus, routing, and restart/rollback UI formatting.
- `http_api.py`: Web server running inside the bot process.
- `database.py`: Core database operations. Do not change schemas without careful migration.
- `deployer_service.py`: The code for `ksysha-deployer` orchestrating Docker operations.
- `scripts/restart_clean.sh`: The local fallback restart script used when deployer is offline.
- `app_version.py`: Single source of truth for the application version.

**Startup flow:**
1. `docker-compose up` builds and starts containers.
2. `ksysha-deployer` binds to port 25100.
3. `ksysha-bot` runs `bot.py` or `http_api.py`, initializing DB, loading config, and starting `aiogram` polling & the web server on 25086.

**Workflow Overviews:**
- **Version system:** Semantic versioning tracked in `app_version.py`. Git commit hash is passed via `GIT_COMMIT` build arg or read via local `git rev-parse`.
- **Release workflow:** Creating a release modifies `app_version.py`, adds a release entry to the DB, and triggers a deploy.
- **Rollback workflow:** Reverts `app_version.py` back to an old state, restores database backups if necessary, and triggers a deploy.
- **Restart workflow:** Gracefully triggers `ksysha-deployer` to recreate containers. Falls back to a local script `restart_clean.sh` if deployer fails.

---

## Development Workflow
**How to run locally (without Docker):**
1. Set up a Python 3.12+ `.venv`.
2. Install dependencies: `pip install -r requirements.txt`.
3. Set required environment variables (e.g. `BOT_TOKEN`, `CREATOR_ID`).
4. Run `python bot.py` (or `python http_api.py`).

**How to run in Docker:**
1. Execute `docker compose up -d --build` in `/root/KsyshaTest`.

**How to validate changes:**
- Always run syntax checks: `python3 -m py_compile <changed_file.py>`.
- Run tests: `python3 tests.py` or `python3 full_test.py`.

**How to test safely:**
- Be mindful of `memories.db`. Use a local copy or test environment if making schema changes to prevent data corruption.

---

## Version Management
- **How releases are created:** Via the `create_custom_release.py` script or through the bot's admin panel. It updates `app_version.py` and creates a release record in the DB.
- **How version numbers are incremented:** Usually via AST manipulation or Regex in `app_version.py`.
- **Where version metadata is stored:** Code version exclusively in `app_version.py`; historical releases in the SQLite `releases` table.
- **How diagnostics determine version and commit:** Diagnostics import `app_version.py` directly, and read the commit hash from `os.environ.get("GIT_COMMIT")` or by executing `git rev-parse`.

---

## Rollback System
- **How rollback works:** Restores the codebase to a previous state, potentially restores a DB snapshot, and then triggers a container rebuild.
- **Important warnings:** Schema rollbacks are destructive. Always double-check if the database needs rollback.
- **Known pitfalls:** If the code rolls back but the database doesn't, schema mismatches will crash the bot.
- **Current architecture:** `ksysha-deployer` handles container replacement, while the bot code orchestrates file and database restoration.

---

## Restart System
- **Deployer usage:** Primary path. The bot sends a POST to `http://deployer:25100/deploy`. The deployer pulls, builds, recreates containers, and performs a self-restart.
- **Local safe restart fallback:** If the deployer is unreachable, `handlers.py` uses `subprocess` to trigger a temporary Docker container executing `scripts/restart_clean.sh`.
- **Healthcheck recovery:** If a restart seems "stuck" or "failed" but containers actually become healthy later, `_recover_restart_status_if_needed` fixes the UI status.
- **Restart status meanings:**
  - `restart_started_local`: Fallback triggered.
  - `restart_recovered_by_healthcheck`: Initial deploy error, but eventually Docker marked containers healthy.
  - `attempt_X`: Retry loops inside `restart_clean.sh`.

---

## Environment Variables
**(Note: Never commit real secrets. Use `.env` locally.)**
- `BOT_TOKEN`: The Telegram bot API token.
- `CREATOR_ID`: Telegram User ID of the admin.
- `KSUSHA_ID`: Telegram User ID of the partner.
- `BOT_SITE_URL` / `SITE_DIRECT_URL`: Public URLs for the web interface.
- `DEPLOYER_SECRET`: Secret token authenticating requests between bot and deployer.
- `MEDIA_ACCESS_TOKEN`: Token for secure media access.
- `DATABASE_PATH`: Custom path to the SQLite database.
- `HOT_BACKUP_ENABLED` / `HOT_BACKUP_PATH`: Backup configurations.

---

## AI Agent Notes
**Files that should not be modified lightly:**
- `database.py`: Changing schemas breaks active state and might break rollback.
- `deployer_service.py`: Easily breaks deployability. Test thoroughly.

**Historical issues already solved (Do not regress!):**
- **Telegram WebView navigation quirks:** DO NOT replace working button-based navigation with anchor-based navigation (`href`) without testing inside Telegram WebView. A bug existed where `/admin -> /` or `/stats -> /` failed in Telegram while working in Chrome. The fix was removing href-based navigation and using JavaScript navigation (similar to `/sky`).
- **Release and rollback system failures:** Several past issues involved Detached HEAD states, `rollback_active` locks, release creation deadlocks, and incorrect version detection. **Before redesigning: verify current behavior, prefer minimal changes, and do not rewrite without a clear bug.**
- **Version returning "unknown" in diagnostic screens:** Caused by looking for `PROJECT_VERSION` in `config.py`. Fixed by importing from `app_version.py`, which is the single source of truth.
- **Status texts showing internal codes:** (`restart_recovered_by_healthcheck` etc.) This is now mapped to human-readable strings.

**Safe debugging workflow:**
- Print logs inside containers instead of replacing logic. Use `docker logs ksysha-bot`.
- Always prefer audits and evidence before making architectural assumptions.

---

## Troubleshooting
- **Release failures:** Check if `app_version.py` is writable and not corrupted. Beware of `rollback_active` locks or detached HEAD.
- **Restart failures:** Use `docker logs ksysha-deployer` or check `data/restart_status.json`.
- **Healthcheck failures:** Verify `/health` on deployer or run `docker inspect --format='{{.State.Health.Status}}' ksysha-bot`.
- **Git state problems:** Make sure `git config --global --add safe.directory /workspace` is set if running inside containers.
- **Version detection issues:** If it reads `unknown`, check if `GIT_COMMIT` build-arg was populated and that `app_version.py` has no syntax errors. Do not look in `config.py`.

---
# KsyshaTest: Stable Deploy and Recovery Runbook (for AI agents)

This project runs as 3 Docker services:
- `ksysha-bot`
- `ksysha-deployer`
- `ksysha-cloudflared`

Main rule: do not trust a single "success" line. Always verify container IDs, status, and live health after deploy.

## 1) Standard Start (server reboot or first start)

```bash
cd /root/KsyshaTest
docker compose up -d --build
docker compose ps -a
```

Expected:
- all 3 services exist
- all required services are `Up`

## 2) Standard Deploy (from bot admin panel)

Admin panel triggers deployer endpoint:
- `POST http://deployer:25100/deploy`

Deployer does:
1. recreate `ksysha-bot` with build
2. recreate `ksysha-cloudflared`
3. schedule delayed self-restart of `ksysha-deployer`
4. persist deploy status and expose it via `/health`

After pressing restart in admin:
1. wait 5-15 seconds
2. press `🔄 Обновить статус`
3. confirm:
   - `Запущено контейнеров: 3 из 3`
   - `recreated: да` for bot update

## 3) Mandatory Post-Deploy Verification

```bash
docker ps --format '{{.Names}} {{.ID}} {{.Status}}'
```

Check:
- `ksysha-bot` ID changed after deploy
- `ksysha-cloudflared` is `Up` and recent
- `ksysha-deployer` is `Up` (it can have fresh uptime because self-restart)

Health check from running bot container:

```bash
docker exec ksysha-bot python -c "import json,urllib.request;print(json.dumps(json.loads(urllib.request.urlopen('http://deployer:25100/health',timeout=20).read().decode()), ensure_ascii=False))"
```

## 4) Known Docker Anomalies and Fixes

### A) Name conflict on recreate
Error:
- `The container name "/ksysha-bot" is already in use`
- `The container name "/ksysha-cloudflared" is already in use`

Fix:
```bash
docker rm -f ksysha-bot || true
docker rm -f ksysha-cloudflared || true
docker compose up -d --no-deps ksysha-bot cloudflared
```

### B) Bot still on old code after "restart"
Cause:
- container was not actually recreated or old container stayed bound

Fix:
```bash
cd /root/KsyshaTest
docker compose up -d --build --force-recreate --no-deps ksysha-bot
docker ps --format '{{.Names}} {{.ID}} {{.Status}}' | rg '^ksysha-bot '
```

Then validate code inside container:
```bash
docker exec ksysha-bot sh -lc "grep -n '_format_deploy_report\\|admin_restart_refresh' /app/handlers.py"
```

### C) Deployer code changed locally but behavior is old
Cause:
- `ksysha-deployer` was not rebuilt after file edits

Fix:
```bash
cd /root/KsyshaTest
docker compose up -d --build deployer
docker exec ksysha-deployer sh -lc "grep -n 'compose_recreate_with_retries\\|_consume_last_result' /app/deployer_service.py"
```

## 5) Test Count Drift (`290` vs old values like `306`)

Current canonical full suite value is in `test.py`:
- `FULL_RUN_EXPECTED_TOTAL = 290`

If UI shows old totals, most likely reasons:
1. old `ksysha-bot` container code
2. stale startup message from previous process state
3. partial deploy where bot recreate did not happen

Quick check:
```bash
docker exec ksysha-bot python - <<'PY'
from pathlib import Path
s = Path('/app/test.py').read_text(encoding='utf-8', errors='replace')
for line in s.splitlines():
    if 'FULL_RUN_EXPECTED_TOTAL' in line:
        print(line)
        break
PY
```

## 6) Hard Recovery (when state is inconsistent)

Use only when normal deploy path is unstable.

```bash
cd /root/KsyshaTest
docker compose down
docker rm -f ksysha-bot ksysha-deployer ksysha-cloudflared 2>/dev/null || true
docker compose up -d --build
docker compose ps -a
docker compose logs --since 15m ksysha-bot
docker compose logs --since 15m deployer
docker compose logs --since 15m cloudflared
```

Then refresh status in admin panel.

## 7) Agent Workflow Requirements (must follow)

1. Edit code in `/root/KsyshaTest`.
2. Run syntax check for changed Python files:
   - `python -m py_compile <files>`
3. Rebuild only affected service(s):
   - `deployer_service.py` changed -> rebuild `deployer`
   - `handlers.py`/`bot.py` changed -> rebuild `ksysha-bot`
4. Verify runtime inside containers (not only host files).
5. Verify `/health` and `docker ps` after deploy.
6. Report exact container IDs before/after when troubleshooting.

## 8) Important Notes

- Warning `git was not found in the system` during Docker build does not fail deploy by itself.
- The deploy status is persisted and consumed on deployer restart:
  - file is read at startup
  - loaded into memory cache
  - then file is removed
- If status says `0 из 0`, verify `/health` payload and deployer version first.
