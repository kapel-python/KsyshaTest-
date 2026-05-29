import requests
import sqlite3
from database import db

# 1. Add a wish directly
creator_id = db.get_creator_id()
if not creator_id:
    db.register_user(111, "test_creator", "Test", "Creator")
    db.set_setting("creator_id", "111")
    creator_id = 111

db.add_wish(creator_id, content="Test E2E wish")
wish = db.get_user_wishes(creator_id)[-1]
wish_id = wish.id

print(f"INITIAL DATABASE VALUE: {wish.status}")

statuses = ["created", "in_progress", "done"]

for s in statuses:
    print(f"\n--- Testing status: {s} ---")
    payload = {
        "wish_id": wish_id,
        "status": s,
        "visitor_id": "creator"
    }
    print(f"1. Exact HTTP request payload:\n{payload}")
    
    # 2. HTTP Request to running bot
    try:
        # http_api.py checks api secret or ignores it?
        resp = requests.post("http://127.0.0.1:25086/api/wish_update_status", json=payload)
        print(f"2. Exact HTTP response:\nHTTP {resp.status_code}\n{resp.text}")
    except Exception as e:
        print(f"2. Exact HTTP response: FAILED TO CONNECT - {e}")

    # 3. API Validation Result (we know from the source, but the HTTP response gives it away)
    print(f"3. Exact API validation result: {'Accepted' if getattr(resp, 'status_code', 500) == 200 else 'Rejected'}")
    
    # 4. Database update result (what does db.update_wish_status return?)
    db_update_res = db.update_wish_status(wish_id, s)
    print(f"4. Exact database update result (db.update_wish_status directly): {db_update_res}")
    
    # 5. Final DB value
    current_wish = db.get_wish(wish_id)
    print(f"5. Final database value after the request:\n{current_wish.status}")
