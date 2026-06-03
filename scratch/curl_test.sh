#!/bin/bash
BASE="http://localhost:25086"
paths=(
    "/something-that-does-not-exist"
    "/api/memories/999999"
    "/api/admin/notifications"
    "/api/media/../../../etc/passwd"
    "/api/media/%2e%2e/config.py"
    "/api/media/..%2Fdatabase.py"
    "/media/../../../etc/passwd"
    "/media/%2e%2e/config.py"
    "/media/..%2Fdatabase.py"
)

for p in "${paths[@]}"; do
    echo "=== Path: $p ==="
    # -I fetches headers. -s hides progress.
    # We follow redirects with -L to see the final status? No, let's just get the first status code, and if redirected, the final one.
    # Actually, we want to know what the client gets if they just do a GET.
    # Aiohttp's client in test.py follows redirects! By default, aiohttp ClientSession.get(allow_redirects=True).
    STATUS=$(curl -L -s -o /dev/null -w "%{http_code}" --path-as-is "$BASE$p")
    BODY=$(curl -L -s --path-as-is "$BASE$p" | head -n 3)
    echo "Final Status: $STATUS"
    echo "First 3 lines of Body:"
    echo "$BODY"
    echo ""
done
