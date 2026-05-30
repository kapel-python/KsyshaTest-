import asyncio
import json
from aiohttp import web
import database
import http_api

# Setup a mock DB connection
database.db.init_database(":memory:")

# Helper to mock requests
class MockRequest:
    def __init__(self, data):
        self.data = data
        self.remote = "127.0.0.1"
        self.headers = {"User-Agent": "Test"}

    async def json(self):
        return self.data

async def run_tests():
    db = database.db
    # Create two users
    db.upsert_user(111, "UserA", "userA_first")
    db.upsert_user(222, "UserB", "userB_first")

    print("--- Test 1: Logged out user + valid token ---")
    t1 = db.issue_user_login_token(111)
    req1 = MockRequest({"token": t1})
    resp1 = await http_api.token_auth(req1)
    print(f"Auth Response (Status {resp1.status}):", json.loads(resp1.text))
    # Check if consumed
    print("Token consumed?", db.peek_user_login_token(t1) is None)

    print("\n--- Test 2: Logged out user + invalid token ---")
    req2 = MockRequest({"token": "invalid_xyz"})
    resp2 = await http_api.token_auth(req2)
    print(f"Auth Response (Status {resp2.status}):", json.loads(resp2.text))

    print("\n--- Test 3: Logged in user A + token of user A ---")
    t3 = db.issue_user_login_token(111)
    # token_check
    req3_check = MockRequest({"token": t3, "current_visitor_id": "111"})
    resp3_check = await http_api.token_check(req3_check)
    print("Check Response:", json.loads(resp3_check.text))
    # auth
    req3_auth = MockRequest({"token": t3})
    resp3_auth = await http_api.token_auth(req3_auth)
    print("Auth Response:", json.loads(resp3_auth.text))
    print("Token consumed?", db.peek_user_login_token(t3) is None)

    print("\n--- Test 4: Logged in user A + token of user B + Cancel ---")
    t4 = db.issue_user_login_token(222)
    req4_check = MockRequest({"token": t4, "current_visitor_id": "111"})
    resp4_check = await http_api.token_check(req4_check)
    print("Check Response:", json.loads(resp4_check.text))
    print("Token consumed after check?", db.peek_user_login_token(t4) is None)
    # Cancel means no token_auth is called
    print("User canceled. Token remains valid.", db.peek_user_login_token(t4) is not None)

    print("\n--- Test 5: Logged in user A + token of user B + Switch ---")
    # Using the same token t4
    print("User clicked switch!")
    req5_auth = MockRequest({"token": t4})
    resp5_auth = await http_api.token_auth(req5_auth)
    print("Auth Response:", json.loads(resp5_auth.text))
    print("Token consumed?", db.peek_user_login_token(t4) is None)

    print("\n--- Test 6: Expired token ---")
    # We will artificially expire it in the DB
    t6 = db.issue_user_login_token(111)
    token_hash = db._hash_login_token(t6)
    with db._get_connection() as conn:
        conn.execute("UPDATE user_login_tokens SET expires_at_utc = '2000-01-01 00:00:00' WHERE token_hash=?", (token_hash,))
        conn.commit()
    req6_check = MockRequest({"token": t6})
    resp6_check = await http_api.token_check(req6_check)
    print("Check Response (expired):", json.loads(resp6_check.text))

    print("\n--- Test 7: Already-consumed token ---")
    t7 = db.issue_user_login_token(111)
    # Consume it
    await http_api.token_auth(MockRequest({"token": t7}))
    # Try again
    req7_auth = MockRequest({"token": t7})
    resp7_auth = await http_api.token_auth(req7_auth)
    print("Auth Response (consumed):", json.loads(resp7_auth.text))

asyncio.run(run_tests())
