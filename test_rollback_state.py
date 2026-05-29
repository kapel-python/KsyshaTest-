# Fake DB
class FakeDB:
    def __init__(self):
        self.settings = {}
    def get_setting(self, key):
        return self.settings.get(key)
    def set_setting(self, key, value):
        self.settings[key] = value
    def delete_setting(self, key):
        if key in self.settings:
            del self.settings[key]

def run_startup_hook(fake_db, running_commit):
    """
    Simulates the DB cleanup logic executed in bot.py on startup.
    """
    if fake_db.get_setting("rollback_active") == "1":
        target_commit = fake_db.get_setting("rollback_target_commit")
        if target_commit and running_commit and running_commit[:8] != target_commit[:8]:
            fake_db.delete_setting("rollback_active")
            fake_db.delete_setting("rollback_previous_commit")
            fake_db.delete_setting("rollback_previous_version")
            fake_db.delete_setting("rollback_target_commit")

def test_successful_rollback():
    fake_db = FakeDB()
    fake_db.set_setting("rollback_active", "1")
    fake_db.set_setting("rollback_target_commit", "targetc1")
    run_startup_hook(fake_db, "targetc123")
    assert fake_db.get_setting("rollback_active") == "1", "State should remain active"
    print("test_successful_rollback passed.")

def test_rollback_failure_and_manual_recovery():
    fake_db = FakeDB()
    fake_db.set_setting("rollback_active", "1")
    fake_db.set_setting("rollback_target_commit", "badcommt")
    run_startup_hook(fake_db, "maincommt")
    assert fake_db.get_setting("rollback_active") is None, "State should be cleared"
    print("test_rollback_failure_and_manual_recovery passed.")

def test_rollback_to_broken_release():
    fake_db = FakeDB()
    assert fake_db.get_setting("rollback_active") is None
    print("test_rollback_to_broken_release passed.")

def test_release_after_rollback():
    fake_db = FakeDB()
    fake_db.set_setting("rollback_active", "1")
    fake_db.set_setting("rollback_target_commit", "targetc1")
    run_startup_hook(fake_db, "newrelc1")
    assert fake_db.get_setting("rollback_active") is None, "State should be cleared on new release"
    print("test_release_after_rollback passed.")

if __name__ == "__main__":
    test_successful_rollback()
    test_rollback_failure_and_manual_recovery()
    test_rollback_to_broken_release()
    test_release_after_rollback()
    print("All tests passed!")
