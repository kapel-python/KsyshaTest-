import sqlite3
from datetime import datetime

db_path = '/workspace/data/memories.db'

conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Get one row to see format
cur.execute("SELECT created_at FROM security_events WHERE type='admin_brute' ORDER BY created_at DESC LIMIT 1;")
row = cur.fetchone()
print(f"Row created_at: {repr(row[0]) if row else None}")

# Insert a fake row now but change its string format slightly? 
# The issue: `since = (now - timedelta(hours=1)).isoformat()` produces string like `2026-06-04T07:56:21.925308`
# The database default created_at value is `CURRENT_TIMESTAMP`, which produces `2026-06-04 07:56:21` (without T)
# In SQLite, strings are compared lexicographically!
# '2026-06-04T07:56:21.925308' > '2026-06-04 08:55:34' is TRUE because 'T' > ' '!

print("'2026-06-04T07:56:21.925308' > '2026-06-04 08:55:34' is", '2026-06-04T07:56:21.925308' > '2026-06-04 08:55:34')
print("'2026-06-04 07:56:21.925308' < '2026-06-04 08:55:34' is", '2026-06-04 07:56:21.925308' < '2026-06-04 08:55:34')
