import asyncio
from handlers import db
from aiogram import Bot, Dispatcher
import config

async def main():
    # Call the exact code from handlers
    wish_id = 9
    new_status = "in_progress"
    
    ok = db.update_wish_status(wish_id, new_status)
    if not ok:
        print("Update failed")
        return
        
    try:
        from http_api import broadcast_wish_status
        print("Creating task...")
        task = asyncio.create_task(broadcast_wish_status(wish_id, new_status))
        await task
        print("Task done!")
    except Exception as e:
        print(f"Failed to broadcast wish status: {e}")

if __name__ == "__main__":
    asyncio.run(main())
