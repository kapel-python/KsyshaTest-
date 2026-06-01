import asyncio
import subprocess
from bot import on_startup
from database import db
from unittest.mock import MagicMock

async def test_hook():
    db.set_setting("rollback_active", "1")
    # Set to detached HEAD
    subprocess.run(["git", "checkout", "16922484f63fcc54e471c031995dc667221ba30a"])
    
    # Run hook
    mock_bot = MagicMock()
    await on_startup(mock_bot)
    
    # Check if branch is main or HEAD
    res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    branch = res.stdout.strip()
    
    print(f"Branch after hook: {branch}")
    if branch == "HEAD":
        print("Success! Hook did not restore main branch. Rollback is safe.")
    else:
        print("Failure! Hook restored main branch. Rollback is broken.")
    
    db.set_setting("rollback_active", "0")
    subprocess.run(["git", "checkout", "main"])

asyncio.run(test_hook())
