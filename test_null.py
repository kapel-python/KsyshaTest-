import sqlite3
conn = sqlite3.connect('/app/data/memories.db')
try:
    conn.execute("UPDATE couples SET user1_id = NULL LIMIT 1")
    print("SUCCESS")
except Exception as e:
    print("ERROR:", e)
