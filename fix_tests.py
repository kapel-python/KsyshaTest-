import re

with open("test_rollback_fallback.py", "r") as f:
    content = f.read()

content = content.replace('FAKE_VER = "1.0.0-test"', 'FAKE_VER = "1.0.15"')

# Define the query-specific mock class
fake_db_code = """
class FakeDBContext:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, query, params=None):
        class Result:
            def fetchone(self):
                if "SELECT git_commit" in query:
                    return {"git_commit": FAKE_COMMIT}
                if "SELECT value" in query:
                    return {"value": None}
                if "SELECT status" in query:
                    return {"status": "stable"}
                return {"git_commit": FAKE_COMMIT, "value": None, "status": "stable"}
        return Result()
"""

# Insert it after FAKE_COMMIT_SHORT
content = content.replace('FAKE_COMMIT_SHORT = FAKE_COMMIT[:7]', 'FAKE_COMMIT_SHORT = FAKE_COMMIT[:7]\n' + fake_db_code)

# Replace db_row = ... in Test 1
content = re.sub(r'    db_row = \{"git_commit".*?\n', '', content)

# Replace the mock setup in Test 1 and 5
content = re.sub(
    r'        mock_ctx = MagicMock\(\)\n.*?mock_conn\.return_value = mock_ctx\n',
    '        mock_conn.return_value = FakeDBContext()\n',
    content,
    flags=re.DOTALL
)

# For tests 2, 4, 6, we need to add patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
# Let's just find `patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \`
# and replace it.
content = content.replace(
    'patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \\',
    'patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \\\n         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \\'
)

with open("test_rollback_fallback.py", "w") as f:
    f.write(content)

