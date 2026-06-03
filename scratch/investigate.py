import requests

BASE_URL = "http://localhost:25086"

tests = [
    ("GET nonexistent path", "/something-that-does-not-exist"),
    ("Very long URL", "/" + "A" * 8192),
    ("GET nonexistent memory", "/api/memories/999999999"),
    ("GET /api/admin/notifications without a key", "/api/admin/notifications"),
    ("Path traversal via ../../../etc/passwd", "/api/media/../../../etc/passwd"),
    ("Path traversal via %2e%2e", "/api/media/%2e%2e/config.py"),
    ("Path traversal via ..%2F", "/api/media/..%2Fdatabase.py"),
]

for name, path in tests:
    print(f"=== {name} ===")
    url = f"{BASE_URL}{path}"
    print(f"URL: {url}")
    try:
        r = requests.get(url, timeout=5)
        print(f"Status: {r.status_code}")
        print(f"Body: {r.text[:500]}")
    except Exception as e:
        print(f"Error: {e}")
    print("\n")
