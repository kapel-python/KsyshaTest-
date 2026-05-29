import asyncio
from http_api import _get_trusted_visitor_id, _verify_visitor_signature, _sign_payload
from aiohttp import web

# Simulate the token_auth logic
victim_user_id = "42"
victim_sig = _sign_payload(f"visitor:{victim_user_id}")

class DummyRequest:
    def __init__(self, cookies, headers, query):
        self.cookies = cookies
        self.headers = headers
        self.rel_url = type('URL', (), {'query': query})()

# 1. Normal state (Logged in)
req_logged_in = DummyRequest(
    cookies={"visitor_id": victim_user_id, "visitor_sig": victim_sig},
    headers={},
    query={}
)
print("Logged in:", _get_trusted_visitor_id(req_logged_in))

# 2. State after logout (visitor_id cookie deleted by JS, visitor_sig remains due to httponly)
req_logged_out = DummyRequest(
    cookies={"visitor_sig": victim_sig},
    headers={},
    query={}
)
print("Logged out:", _get_trusted_visitor_id(req_logged_out))

# 3. Attacker using header to bypass
req_attack = DummyRequest(
    cookies={"visitor_sig": victim_sig},
    headers={"X-Visitor-Id": victim_user_id},
    query={}
)
print("Attacker bypass:", _get_trusted_visitor_id(req_attack))

