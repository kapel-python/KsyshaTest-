import sqlite3
db_path = "/app/data/memories.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
print("=== INVITE CODES ===")
rows = conn.execute("SELECT * FROM invite_codes").fetchall()
for r in rows:
    print(dict(r))
conn.close()
