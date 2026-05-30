with open("/root/KsyshaTest/index.html", "r", encoding="utf-8") as f:
    content = f.read()

script = """
    <script>
    try {
        fetch('/api/nav_debug', {
            method: 'POST',
            body: JSON.stringify({msg: 'index.html loading started', page: '/'}),
            headers: {'Content-Type': 'application/json'},
            keepalive: true
        });
        window.addEventListener('error', function(e) {
            fetch('/api/nav_debug', {
                method: 'POST',
                body: JSON.stringify({msg: 'JS Error: ' + e.message, page: '/'}),
                headers: {'Content-Type': 'application/json'},
                keepalive: true
            });
        });
    } catch(e) {}
    </script>
"""

content = content.replace("<head>", "<head>\\n" + script)

with open("/root/KsyshaTest/index.html", "w", encoding="utf-8") as f:
    f.write(content)
