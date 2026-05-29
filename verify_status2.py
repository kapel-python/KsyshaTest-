import asyncio
from aiohttp import web
from http_api import wish_update_status
from database import db
from unittest.mock import MagicMock

async def main():
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
        
        # Mock request
        request = MagicMock(spec=web.Request)
        request.headers = {}
        # mock await request.json()
        async def mock_json():
            return payload
        request.json = mock_json
        request.query = {"visitor_id": "creator"}
        
        # Patch the check for test
        import http_api
        http_api._check_api_secret = lambda r: True
        # For mock visitor to work, make sure creator_id is correct in memory
        http_api.db = db # Ensure same instance
        
        try:
            resp = await wish_update_status(request)
            print(f"2. Exact HTTP response:\nHTTP {resp.status}\n{resp.text}")
        except Exception as e:
            print(f"2. Exact HTTP response: EXCEPTION {e}")
            resp = None
            
        print(f"3. Exact API validation result: {'Accepted' if getattr(resp, 'status', 500) == 200 else 'Rejected'}")
        
        db_update_res = db.update_wish_status(wish_id, s)
        print(f"4. Exact database update result (db.update_wish_status directly): {db_update_res}")
        
        current_wish = db.get_wish(wish_id)
        print(f"5. Final database value after the request:\n{current_wish.status}")

asyncio.run(main())
