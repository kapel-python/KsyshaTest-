import re

with open("test_version_ux.py", "r") as f:
    content = f.read()

fake_db_code = """
class FakeDBContext:
    def __init__(self, commit_val="target_c"):
        self.commit_val = commit_val
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, query, params=None):
        class Result:
            def __init__(self, commit_val):
                self.commit_val = commit_val
            def fetchone(self):
                return {"git_commit": self.commit_val, "version": "1.0.8", "description": "desc", "created_at": "2026-05-29 12:00:00", "status": "stable"}
        return Result(self.commit_val)
"""

content = content.replace('import asyncio', 'import asyncio\n' + fake_db_code)

content = content.replace("patch.object(db, 'get_version_by_name', return_value={\"git_commit\": \"target_c\"}), \\", "patch.object(db, '_get_connection', return_value=FakeDBContext('target_c')), \\")

with open("test_version_ux.py", "w") as f:
    f.write(content)
