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
2. Сохранить текущую БД (на случай частичного отката):
```bash
cp /root/KsyshaTest/data/memories.db /root/KsyshaTest/data/memories.db.pre-restore.$(date +%Y%m%d-%H%M%S)
```
3. Сначала попробовать восстановление из hot backup (если есть):
```bash
cp /root/KsyshaTest/data/memories.hotbackup.db /root/KsyshaTest/data/memories.db
```
4. Если hot backup отсутствует/битый — восстановить latest `.gz` backup:
```bash
LATEST=$(ls -1t /root/KsyshaTest/backups/memories-*.sqlite3.gz | head -n1)
gzip -dc "$LATEST" > /root/KsyshaTest/data/memories.db
```
5. Проверить целостность:
```bash
python3 - <<'PY'
import sqlite3
c=sqlite3.connect('/root/KsyshaTest/data/memories.db')
print(c.execute('pragma integrity_check').fetchone())
c.close()
PY
```
6. Поднять стек на свежей версии:
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
