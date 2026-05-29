#!/usr/bin/env python3
import argparse
import gzip
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile


def verify_backup(backup_file: Path) -> None:
    with tempfile.NamedTemporaryFile(prefix="restore-check-", suffix=".sqlite3", delete=False) as tmp:
        restored = Path(tmp.name)
    try:
        with gzip.open(backup_file, "rb") as gz, restored.open("wb") as out:
            shutil.copyfileobj(gz, out)
        conn = sqlite3.connect(str(restored))
        try:
            row = conn.execute("PRAGMA integrity_check;").fetchone()
            if not row or row[0].lower() != "ok":
                raise RuntimeError(f"integrity_check failed: {row}")
            conn.execute("SELECT COUNT(1) FROM sqlite_master;").fetchone()
        finally:
            conn.close()
    finally:
        restored.unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backup-dir", default="/root/KsyshaTest/backups")
    args = p.parse_args()

    backup_dir = Path(args.backup_dir)
    latest = sorted(backup_dir.glob("memories-*.sqlite3.gz"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not latest:
        print("ERROR: no backups found", file=sys.stderr)
        return 2
    target = latest[0]
    verify_backup(target)
    print(f"OK: restore-check latest backup: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
