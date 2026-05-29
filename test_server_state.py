import asyncio
from aiohttp import web
from http_api import app, _site_ws_clients, broadcast_wish_status

async def handle(request):
    print("Clients:", _site_ws_clients)
    return web.Response(text=str(_site_ws_clients))

app.router.add_get('/_test_state', handle)
