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
