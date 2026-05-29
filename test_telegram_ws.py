import asyncio
from aiohttp import ClientSession
from database import db

async def main():
    creator_id = db.get_creator_id()
    if not creator_id:
        db.register_user(111, "test_creator", "Test", "Creator")
        db.set_setting("creator_id", "111")
        creator_id = 111

    c = db.get_couple_by_user(creator_id)
    if not c:
        db.create_couple(creator_id, 222, "2024-01-01")

    # Get valid session sig (mock one is needed? No, we don't have it. We need to fetch it from /api/collect_site_data if we login?)
    # Wait, we can bypass the websocket test and just inspect the server logs!
    import os
    os.system("tail -n 100 bot.log > bot_recent.log")

if __name__ == "__main__":
    asyncio.run(main())
