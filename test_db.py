import sqlite3
import os

db_path = '/workspace/data/memories.db'

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT created_at FROM security_events WHERE type='admin_brute' ORDER BY created_at DESC LIMIT 5;")
for row in cur.fetchall():
    print(repr(row[0]))
