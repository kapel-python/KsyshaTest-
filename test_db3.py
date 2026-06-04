from datetime import datetime, timedelta

now = datetime.utcnow()
since = (now - timedelta(hours=1)).isoformat()
print(f"now: {now}")
print(f"since: {since}")

# Let's compare since to what's in DB.
print(since < '2026-06-04 08:55:34')
print(since < '2026-06-04T08:55:34')
