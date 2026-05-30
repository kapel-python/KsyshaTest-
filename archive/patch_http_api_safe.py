import re
with open("/root/KsyshaTest/http_api.py", "r", encoding="utf-8") as f:
    content = f.read()

index_block = """async def index(request: web.Request) -> web.StreamResponse:

    \"\"\"Отдаёт главную страницу или страницу технического перерыва (если включена тест версия).\"\"\"
    try:
        with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
            f.write(f"SERVER: GET / requested from {request.headers.get('User-Agent', '')}\\n")
    except:
        pass
"""
content = re.sub(
    r'async def index\(request: web\.Request\) -> web\.StreamResponse:\s*"""Отдаёт главную страницу или страницу технического перерыва \(если включена тест версия\)\."""',
    index_block,
    content
)

nav_debug_block = """    async def nav_debug(request):
        try:
            data = await request.json()
            with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
                f.write(f"NAV_DEBUG: {data}\\n")
        except:
            pass
        return web.Response(text="ok")
    app.router.add_post("/api/nav_debug", nav_debug)

    app.router.add_post("/api/log", client_log)"""
content = content.replace('    app.router.add_post("/api/log", client_log)', nav_debug_block)

with open("/root/KsyshaTest/http_api.py", "w", encoding="utf-8") as f:
    f.write(content)
