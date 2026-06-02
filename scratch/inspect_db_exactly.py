import sqlite3
import json

db_path = "/workspace/data/memories.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

print("=== TABLES ===")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(tables)

for t in tables:
    count = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    print(f"Table '{t}': {count} rows")

print("\n=== SAMPLE USERS ===")
if "users" in tables:
    for r in conn.execute("SELECT * FROM users").fetchall():
        print(dict(r))

print("\n=== SAMPLE MEMORIES ===")
if "memories" in tables:
    for r in conn.execute("SELECT * FROM memories LIMIT 3").fetchall():
        print(dict(r))

conn.close()
