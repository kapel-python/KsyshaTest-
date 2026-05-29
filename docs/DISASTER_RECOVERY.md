# Disaster Recovery Runbook

## Scope
Проект: `/root/KsyshaTest`.
Цель: восстановить сервис при поломке контейнера/БД.

## Preconditions
- Есть свежий backup в `/root/KsyshaTest/backups/memories-*.sqlite3.gz`.
- Установлен `docker compose` v2.

## Routine controls
- `scripts/run_backup.sh` — ежедневный backup + verify + retention.
- `scripts/restore_check_latest.py` — проверка восстановления последнего backup.
- `scripts/healthcheck.sh` — проверка контейнеров, API, свежести backup, диска.
- Hot backup (моментальная копия после каждого commit):
  - active: `/root/KsyshaTest/data/memories.hotbackup.db`
  - prev: `/root/KsyshaTest/data/memories.hotbackup.db.prev`

## Recovery steps
1. Остановить стек:
```bash
cd /root/KsyshaTest
docker compose down
```
2. Сделать бекап текущей сломанной БД (на всякий случай):
```bash
cp /workspace/data/memories.db /workspace/data/memories.db.pre-restore.$(date +%Y%m%d-%H%M%S)
```

Восстановление данных (по приоритету):

1. Если есть свежий hot backup (и он не повреждён):
```bash
cp /workspace/data/memories.hotbackup.db /workspace/data/memories.db
```
2. Если hot backup отсутствует/битый — восстановить latest `.gz` backup:
```bash
LATEST=$(ls -1t /root/KsyshaTest/backups/memories-*.sqlite3.gz | head -n1)
gzip -dc "$LATEST" > /workspace/data/memories.db
```
5. Проверить целостность:
```bash
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/workspace/data/memories.db')
print(c.execute('pragma integrity_check').fetchone())
c.close()
PY
```
6. Восстановить код до стабильного коммита:
```bash
git checkout <known_good_commit_hash>
```
7. Поднять стек на свежей версии:
```bash
cd /root/KsyshaTest
docker compose up -d --build
```
7. Smoke-check:
```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:25086/stats
```

## Cron setup
Использовать `scripts/cron.monitor.example` через `crontab -e`.
