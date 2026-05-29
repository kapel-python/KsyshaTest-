import re
with open("/root/KsyshaTest/http_api.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add nav_debug handler
nav_debug_code = """
    async def nav_debug(request):
        try:
            data = await request.json()
            with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
                f.write(f"NAV_DEBUG: {data}\\n")
        except:
            pass
        return web.Response(text="ok")
    app.router.add_post("/api/nav_debug", nav_debug)
"""
content = content.replace("app.router.add_get(\"/api/log\",", nav_debug_code + "\\n    app.router.add_get(\"/api/log\",")
# wait, actually let's just insert it before app.router.add_post("/api/log", client_log)
content = content.replace("app.router.add_post(\"/api/log\", client_log)", nav_debug_code + "\\n    app.router.add_post(\"/api/log\", client_log)")

# Add SERVER LOG to index
index_log = """
    try:
        with open("/root/KsyshaTest/frontend_debug.log", "a", encoding="utf-8") as f:
            f.write(f"SERVER: GET / requested from {request.headers.get('User-Agent', '')}\\n")
    except:
        pass
"""
content = content.replace("    project_root = Path(__file__).resolve().parent", index_log + "\\n    project_root = Path(__file__).resolve().parent")

with open("/root/KsyshaTest/http_api.py", "w", encoding="utf-8") as f:
    f.write(content)
