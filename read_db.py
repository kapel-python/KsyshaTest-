import sqlite3
import os

db_path = '/workspace/data/memories.db'
if not os.path.exists(db_path):
    print("DB not found")
    exit(1)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT * FROM security_events WHERE type='admin_brute' ORDER BY created_at DESC LIMIT 5;")
for row in cur.fetchall():
    print(row)
