import sqlite3

db_path = '/workspace/data/memories.db'

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT datetime('now')")
print("now from sqlite:", cur.fetchone()[0])
