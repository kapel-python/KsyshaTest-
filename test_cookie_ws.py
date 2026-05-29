import asyncio
from aiohttp import ClientSession
import json

async def main():
    async with ClientSession() as session:
        # First send a dummy request to set the cookie in session or manually attach it
        print("Connecting WS...")
        ws = await session.ws_connect(
            "ws://127.0.0.1:25086/ws/site?v=",
            headers={"Cookie": "visitor_id=creator", "Origin": "http://127.0.0.1:25086"}
        )
        print("WS connected!")
        
        # Now trigger API
        async with session.post(
            "http://127.0.0.1:25086/api/wish_update_status",
            headers={"Cookie": "visitor_id=creator", "Content-Type": "application/json", "Origin": "http://127.0.0.1:25086"},
            json={"wish_id": 9, "status": "done", "visitor_id": None}
        ) as resp:
            print("API resp:", resp.status, await resp.text())
            
        # Wait for ws msg
        try:
            msg = await asyncio.wait_for(ws.receive_json(), timeout=2.0)
            print(f"Received msg: {msg}")
        except asyncio.TimeoutError:
            print("NO MESSAGE RECEIVED!")
            
        await ws.close()

if __name__ == "__main__":
    asyncio.run(main())
