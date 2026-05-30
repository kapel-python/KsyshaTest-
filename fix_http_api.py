import re

with open("/root/KsyshaTest/http_api.py", "r", encoding="utf-8") as f:
    c = f.read()

# 1. Remove literal \n
c = c.replace("\\n    project_root = Path(__file__).resolve().parent", "    project_root = Path(__file__).resolve().parent")

# 2. Remove the injected block
block = """    try:
        with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
            f.write(f"SERVER: GET / requested from {request.headers.get('User-Agent', '')}\\n")
    except:
        pass
"""
c = c.replace(block, "")

# 3. Remove nav_debug block
nav_debug_block = """    async def nav_debug(request):
        try:
            data = await request.json()
            with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
                f.write(f"NAV_DEBUG: {data}\\n")
        except:
            pass
        return web.Response(text="ok")
    app.router.add_post("/api/nav_debug", nav_debug)
"""
c = c.replace(nav_debug_block, "")

with open("/root/KsyshaTest/http_api.py", "w", encoding="utf-8") as f:
    f.write(c)
