#!/bin/bash
BASE="http://localhost:25086"
URL="$BASE/api/$(python3 -c "print('a' * 2000)")"
curl -L -s -o /dev/null -w "%{http_code}" "$URL"
echo ""
curl -L -s "$URL" | head -n 10
