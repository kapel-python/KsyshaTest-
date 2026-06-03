#!/bin/bash
BASE="http://localhost:25086"
paths=(
    "/something-that-does-not-exist"
    "/api/memories/999999"
    "/api/admin/notifications"
    "/api/media/../../../etc/passwd"
)

for p in "${paths[@]}"; do
    STATUS=$(curl -L -s -o /dev/null -w "%{http_code}" --path-as-is "$BASE$p")
    BODY=$(curl -L -s --path-as-is "$BASE$p" | head -n 3)
    echo "=== Path: $p ==="
    echo "Status: $STATUS"
    echo "First 3 lines of Body:"
    echo "$BODY"
    echo ""
done
