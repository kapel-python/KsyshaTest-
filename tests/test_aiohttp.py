import asyncio
from aiohttp import web

async def handle(request):
    response = web.Response(text="Hello")
    response.set_cookie("visitor_id", "123")
    return response

app = web.Application()
app.router.add_get('/api/token_auth', handle)

async def test():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8081)
    await site.start()
    
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get('http://localhost:8081/api/token_auth') as resp:
            print("Set-Cookie header:", resp.headers.getall("Set-Cookie"))
    
    await runner.cleanup()

asyncio.run(test())
