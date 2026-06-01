import sqlite3
db_path = "/workspace/memories.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
print("=== TABLES ===")
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print(tables)
if "users" in tables:
    rows = conn.execute("SELECT * FROM users").fetchall()
    for r in rows:
        print(dict(r))
conn.close()
