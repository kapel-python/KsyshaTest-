# Backups

## What was added
- `scripts/backup_db.py`: creates compressed SQLite backup (`.sqlite3.gz`), verifies restore with `PRAGMA integrity_check`, and applies retention.
- `scripts/run_backup.sh`: wrapper with logging to `backups/backup.log`.
- `scripts/cron.backup.example`: cron line example.
- `scripts/restore_check_latest.py`: verifies latest backup can be restored.
- `scripts/healthcheck.sh`: checks containers, API availability, backup age, and free disk.
- `scripts/cron.monitor.example`: unified cron examples for backup/health/restore-check.

## Manual run
```bash
cd /root/KsyshaTest
./scripts/run_backup.sh
```

## Dry run
```bash
cd /root/KsyshaTest
./.venv/bin/python ./scripts/backup_db.py --dry-run
```

## Install cron
```bash
crontab -e
```
Add:
```cron
40 3 * * * /root/KsyshaTest/scripts/run_backup.sh
```

## Retention
- Default: keep last `14` backups.
- Change by editing `--keep-last` in `scripts/run_backup.sh`.
