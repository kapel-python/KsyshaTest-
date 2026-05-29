#!/usr/bin/env python3
import argparse
import datetime as dt
import gzip
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def backup_name(ts: dt.datetime) -> str:
    return f"memories-{ts.strftime('%Y%m%d-%H%M%S')}.sqlite3.gz"


def create_backup(db_path: Path, backup_dir: Path) -> Path:
    ts = now_utc()
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / backup_name(ts)

    with tempfile.NamedTemporaryFile(prefix="memories-backup-", suffix=".sqlite3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL);")
        except Exception:
            pass
        conn.close()

        shutil.copy2(db_path, tmp_path)
        with tmp_path.open("rb") as src, gzip.open(dest, "wb", compresslevel=6) as gz:
            shutil.copyfileobj(src, gz)
        return dest
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def verify_backup(backup_file: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix="memories-restore-check-", suffix=".sqlite3", delete=False) as tmp:
        restored_path = Path(tmp.name)
    try:
        with gzip.open(backup_file, "rb") as gz, restored_path.open("wb") as out:
            shutil.copyfileobj(gz, out)

        conn = sqlite3.connect(str(restored_path))
        try:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            if not row or row[0].lower() != "ok":
                raise RuntimeError(f"integrity_check failed: {row}")
            conn.execute("SELECT COUNT(1) FROM sqlite_master;").fetchone()
        finally:
            conn.close()
    finally:
        if restored_path.exists():
            restored_path.unlink()


def prune_backups(backup_dir: Path, keep_last: int) -> list[Path]:
    files = sorted(backup_dir.glob("memories-*.sqlite3.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = files[keep_last:]
    for f in to_delete:
        f.unlink(missing_ok=True)
    return to_delete


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup SQLite DB with retention and restore verification")
    parser.add_argument("--db", default="/root/KsyshaTest/data/memories.db", help="Path to SQLite DB")
    parser.add_argument("--backup-dir", default="/root/KsyshaTest/backups", help="Backup directory")
    parser.add_argument("--keep-last", type=int, default=14, help="How many latest backups to keep")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    args = parser.parse_args()

    db_path = Path(args.db)
    backup_dir = Path(args.backup_dir)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        return 2
    if args.keep_last < 1:
        print("ERROR: --keep-last must be >= 1", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"DRY RUN: db={db_path} backup_dir={backup_dir} keep_last={args.keep_last}")
        return 0

    backup_file = create_backup(db_path, backup_dir)
    verify_backup(backup_file)
    deleted = prune_backups(backup_dir, args.keep_last)

    print(f"OK: created={backup_file}")
    print(f"OK: verified={backup_file.name}")
    print(f"OK: pruned={len(deleted)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
