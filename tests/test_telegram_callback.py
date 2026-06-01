import asyncio
from aiohttp import ClientSession
from database import db

async def main():
    creator_id = db.get_creator_id()
    if not creator_id:
        db.register_user(111, "test_creator", "Test", "Creator")
        db.set_setting("creator_id", "111")
        creator_id = 111

    db.add_wish(creator_id, content="WS wish")
    wish = db.get_user_wishes(creator_id)[-1]
    
    print("Connecting WS...")
    async with ClientSession() as session:
        ws = await session.ws_connect(
            "ws://127.0.0.1:25086/ws/site?v=",
            headers={"Cookie": "visitor_id=creator", "Origin": "http://127.0.0.1:25086"}
        )
        print("WS connected!")
        
        # Now trigger TELEGRAM handler manually by calling db.update_wish_status and then broadcast
        # Wait, the bot is running! We can't easily trigger the aiogram handler from outside unless we send a real telegram message.
        # But we can import broadcast_wish_status and call it?
        # If we run a separate script, it won't talk to the bot's memory space.
        
        # Wait, if we want to simulate the Telegram handler inside the bot process, we can use the /api/admin_action endpoint or similar if it evaluates python?
        pass

if __name__ == "__main__":
    asyncio.run(main())
