import asyncio
import aiohttp
from aiohttp import web, ClientSession
from database import db
import sqlite3

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
    
    print("Connecting WS...")
    async with ClientSession() as session:
        ws = await session.ws_connect(
            "ws://127.0.0.1:25086/ws/site",
            headers={"Cookie": "visitor_id=creator", "Origin": "http://127.0.0.1:25086"}
        )
        
        print("WS connected. Waiting a bit.")
        await asyncio.sleep(0.5)
        
        print(f"Triggering broadcast from backend for wish {wish.id}...")
        
        # We need to simulate telegram calling handlers by just directly updating DB and then broadcast?
        # No, we can just hit the API endpoint that changes the wish status!
        # Wait, the API endpoint broadcasts anyway. The bug is about TELEGRAM updates not reaching the client!
        # So we should call the Telegram handler or just run a script that imports http_api and calls broadcast.
        # But if the script runs in a separate process, broadcast_wish_status will send to its OWN _site_ws_clients, which is empty!
        # Ah! That's it!
        pass

if __name__ == "__main__":
    asyncio.run(main())
