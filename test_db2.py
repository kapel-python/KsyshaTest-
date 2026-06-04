import sqlite3
from datetime import datetime, timedelta

now = datetime.utcnow()
since = (now - timedelta(hours=1)).isoformat()
print(f"now: {now}")
print(f"since: {since}")

# In test: row '2026-06-04 08:55:34' > since ?
dt_str = '2026-06-04 08:55:34'.replace('Z', '')
last_error_dt = datetime.fromisoformat(dt_str)
print(f"last_error_dt: {last_error_dt}")
