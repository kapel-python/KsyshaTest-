import asyncio
from aiohttp import ClientSession

async def main():
    async with ClientSession() as session:
        print("Logging in as creator...")
        async with session.post(
            "http://127.0.0.1:25086/api/admin_login",
            headers={"Origin": "http://127.0.0.1:25086"},
            json={"password": "1"} # Or whatever password. Wait, I can just mock the cookie in http_api or use visitor_id bypassing in test?
        ) as resp:
            print("Login resp:", resp.status)
            cookies = session.cookie_jar.filter_cookies("http://127.0.0.1:25086")
            print("Cookies:", cookies)
            if not cookies:
                print("Failed to get cookies. We can bypass auth by hacking http_api.py temporarily.")
