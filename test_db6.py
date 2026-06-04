import sqlite3
import os

db_path = '/workspace/data/memories.db'

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT ip, created_at, detail FROM security_events WHERE type='admin_brute' ORDER BY created_at DESC LIMIT 20;")
for row in cur.fetchall():
    print(row)
