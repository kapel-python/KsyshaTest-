import asyncio
from aiohttp import web, ClientSession
from http_api import broadcast_wish_status, app, _site_ws_clients
from database import db
from aiohttp.test_utils import TestServer

async def main():
    creator_id = db.get_creator_id()
    if not creator_id:
        db.register_user(111, "test_creator", "Test", "Creator")
        db.set_setting("creator_id", "111")
        creator_id = 111

    c = db.get_couple_by_user(creator_id)
    if not c:
        db.create_couple(creator_id, 222, "2024-01-01")
        c = db.get_couple_by_user(creator_id)

    db.add_wish(creator_id, content="WS wish")
    wish = db.get_user_wishes(creator_id)[-1]
    
    server = TestServer(app)
    await server.start_server()
    
    print("Connecting WS...")
    async with ClientSession() as session:
        ws = await session.ws_connect(f"http://127.0.0.1:{server.port}/ws/site", headers={"Cookie": "visitor_id=creator"})
        
        print("WS connected. Waiting a bit.")
        await asyncio.sleep(0.5)
        
        print(f"Server tracked clients: {_site_ws_clients}")
        
        print(f"Triggering broadcast from backend for wish {wish.id}...")
        await broadcast_wish_status(wish.id, "in_progress")
        
        try:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            print(f"Received msg: {msg}")
        except asyncio.TimeoutError:
            print("NO MESSAGE RECEIVED!")
            
    await server.close()

if __name__ == "__main__":
    asyncio.run(main())
