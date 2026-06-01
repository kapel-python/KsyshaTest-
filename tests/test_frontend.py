import asyncio
from playwright.async_api import async_playwright

async def main():
    print("No playwright installed. We can't do browser E2E easily.")

if __name__ == "__main__":
    asyncio.run(main())
