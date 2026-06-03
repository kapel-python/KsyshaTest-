#!/usr/bin/env python3
"""Migration v2: remove UNIQUE(user_id, wish_number) from wishes table.

Run once before deploying the new wish system:
    python migrate_wishes_v2.py

Safe to re-run: checks if migration is already applied.
"""
import sqlite3
import sys
import os

PRODUCTION_DB_PATH = "/workspace/data/memories.db"
DATABASE_PATH = os.environ.get("DATABASE_PATH") or PRODUCTION_DB_PATH


def _table_has_unique_on_wish_number(conn: sqlite3.Connection) -> bool:
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wishes'"
    ).fetchone()
    if not sql:
        return False
    return "UNIQUE(user_id, wish_number)" in (sql[0] or "")


def migrate(db_path: str) -> None:
    print(f"[migrate_wishes_v2] database: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")

        if not _table_has_unique_on_wish_number(conn):
            print("[migrate_wishes_v2] UNIQUE constraint not found — already migrated or table absent. Nothing to do.")
            return

        print("[migrate_wishes_v2] Recreating wishes table without UNIQUE(user_id, wish_number)...")

        conn.execute("BEGIN")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wishes_v2_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                wish_number INTEGER,
                content TEXT NOT NULL,
                media_type TEXT,
                media_file_id TEXT,
                media_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'created',
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        conn.execute("""
            INSERT INTO wishes_v2_new
                (id, user_id, wish_number, content,
                 media_type, media_file_id, media_path,
                 created_at, updated_at, status)
            SELECT
                id, user_id, wish_number, content,
                media_type, media_file_id, media_path,
                created_at, updated_at,
                COALESCE(status, 'created')
            FROM wishes
        """)

        rows = conn.execute("SELECT COUNT(*) FROM wishes").fetchone()[0]
        new_rows = conn.execute("SELECT COUNT(*) FROM wishes_v2_new").fetchone()[0]
        if rows != new_rows:
            conn.execute("ROLLBACK")
            raise RuntimeError(f"Row count mismatch: old={rows}, new={new_rows}")

        conn.execute("DROP TABLE wishes")
        conn.execute("ALTER TABLE wishes_v2_new RENAME TO wishes")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_wishes_user ON wishes(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wishes_created ON wishes(created_at)")

        conn.execute("COMMIT")
        conn.execute("PRAGMA foreign_keys=ON")
        print(f"[migrate_wishes_v2] Done. Migrated {new_rows} wishes.")
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        print(f"[migrate_wishes_v2] ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        DATABASE_PATH = sys.argv[1]
    migrate(DATABASE_PATH)
