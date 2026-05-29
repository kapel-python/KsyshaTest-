import asyncio
import aiohttp
from aiohttp import web
from http_api import app, _get_trusted_visitor_id
from database import db
from aiohttp.test_utils import TestClient, TestServer

async def main():
    # Setup test user and wish
    # Assume db is initialized since we deleted backups and it auto-inits
    creator_id = db.get_creator_id()
    if not creator_id:
        db.register_user(111, "test_creator", "Test", "Creator")
        db.set_setting("creator_id", "111")
        creator_id = 111
        
    db.add_wish(creator_id, content="Test wish")
    wish = db.get_user_wishes(creator_id)[0]
    wish_id = wish['id']
    
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    
    print("INITIAL WISH STATUS:", db.get_wish(wish_id).status)
    
    # Need auth headers
    headers = {"X-API-Key": "test_key"} # mock auth if needed? Wait, http_api uses signature or ignores if local?
    # Let's check test.py how it authenticates
    
    for status in ["created", "in_progress", "done"]:
        payload = {
            "wish_id": wish_id,
            "status": status,
            "visitor_id": "creator" # using creator so _visitor_to_user_id works
        }
        print(f"\n--- Testing status: {status} ---")
        print(f"1. Exact HTTP request payload: {payload}")
        
        # Test 1: Full E2E through HTTP
        resp = await client.post("/api/wish_update_status", json=payload)
        resp_json = await resp.json()
        print(f"2. Exact HTTP response: HTTP {resp.status} {resp_json}")
        
        # Check DB
        current_wish = db.get_wish(wish_id)
        print(f"5. Final database value after the request: {current_wish.status}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
