import asyncio
from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from http_api import wish_update_status
from database import db

async def main():
    creator_id = db.get_creator_id()
    if not creator_id:
        db.register_user(111, "test_creator", "Test", "Creator")
        db.set_setting("creator_id", "111")
        creator_id = 111
        
    db.add_wish(creator_id, content="Test E2E wish")
    wish = db.get_user_wishes(creator_id)[-1]
    wish_id = wish.id
    
    # Also need couple for wish status update auth
    c = db.get_couple_by_user(creator_id)
    if not c:
        db.create_couple(creator_id, 222, "2024-01-01")
        
    db.update_wish_status(wish_id, "created")
    
    transitions = [
        "in_progress",
        "done",
        "created"
    ]
    
    for s in transitions:
        db_before = db.get_wish(wish_id).status
        print(f"\n--- Testing transition: {db_before} -> {s} ---")
        payload = {
            "wish_id": wish_id,
            "status": s,
            "visitor_id": "creator"
        }
        print(f"1. HTTP request payload: {payload}")
        
        request = make_mocked_request('POST', '/api/wish_update_status', headers={"Origin": "http://localhost"})
        request = request.clone(rel_url='/api/wish_update_status?visitor_id=creator')
        async def mock_json():
            return payload
        request.json = mock_json
        
        import http_api
        http_api._check_api_secret = lambda r: True
        http_api._get_trusted_visitor_id = lambda req, **kw: "creator"
        http_api.db = db
        
        try:
            resp = await wish_update_status(request)
            print(f"2. HTTP response: HTTP {resp.status} {resp.text}")
        except Exception as e:
            print(f"2. HTTP response: EXCEPTION {e}")
            resp = None
            
        print(f"3. DB value before: {db_before}")
        current_wish = db.get_wish(wish_id)
        print(f"4. DB value after: {current_wish.status}")

asyncio.run(main())
