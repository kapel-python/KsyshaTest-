def replace_in_file(path, old, new):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    content = content.replace(old, new)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# admin.html
admin_script = """
    <script>
    function logNavDebug(msg) {
        try { fetch('/api/nav_debug', { method: 'POST', body: JSON.stringify({msg: msg, page: '/admin'}), headers: {'Content-Type': 'application/json'}, keepalive: true }); } catch(e) {}
    }
    </script>
"""
replace_in_file("/root/KsyshaTest/admin.html", "<head>", "<head>\\n" + admin_script)

old_admin = """<a href="/" onclick="window.location.href='/'; return false;" class="header-back">← Сайт</a>"""
new_admin = """<a href="#" onclick="logNavDebug('Click handler executed'); setTimeout(function(){ logNavDebug('Setting location.href'); window.location.href = '/'; }, 50); return false;" class="header-back">← Сайт</a>"""
replace_in_file("/root/KsyshaTest/admin.html", old_admin, new_admin)

# stats.html
stats_script = """
    <script>
    function logNavDebug(msg) {
        try { fetch('/api/nav_debug', { method: 'POST', body: JSON.stringify({msg: msg, page: '/stats'}), headers: {'Content-Type': 'application/json'}, keepalive: true }); } catch(e) {}
    }
    </script>
"""
replace_in_file("/root/KsyshaTest/stats.html", "<head>", "<head>\\n" + stats_script)

old_stats = """<a class="back-link" href="/" onclick="window.location.href='/'; return false;">"""
new_stats = """<a class="back-link" href="#" onclick="logNavDebug('Click handler executed'); setTimeout(function(){ logNavDebug('Setting location.href'); window.location.href = '/'; }, 50); return false;">"""
replace_in_file("/root/KsyshaTest/stats.html", old_stats, new_stats)

# sky.html
sky_script = """
    <script>
    function logNavDebug(msg) {
        try { fetch('/api/nav_debug', { method: 'POST', body: JSON.stringify({msg: msg, page: '/sky'}), headers: {'Content-Type': 'application/json'}, keepalive: true }); } catch(e) {}
    }
    </script>
"""
replace_in_file("/root/KsyshaTest/sky.html", "<head>", "<head>\\n" + sky_script)

old_sky = """<button class="sky-btn" id="btnClose" onclick="closeSky()">✕ закрыть</button>"""
new_sky = """<button class="sky-btn" id="btnClose" onclick="logNavDebug('Click handler executed'); setTimeout(function(){ logNavDebug('Setting location.href'); closeSky(); }, 50);">✕ закрыть</button>"""
replace_in_file("/root/KsyshaTest/sky.html", old_sky, new_sky)

