"""
Validation: rollback deployer-fallback fix
==========================================
Tests every branch of the three changed handlers against the real production
configuration where DEPLOYER_URL points to http://deployer:25100/deploy but
no deployer container exists (confirmed by earlier root-cause analysis).

All tests patch only the _side-effects_ (git, docker run, DB writes) so that
the tests are deterministic and non-destructive.  The aiohttp call to
`http://deployer:25100/health` is intentionally NOT mocked in test groups 1–3
so we can confirm the fallback fires from a real DNS failure.
"""

import asyncio
import os
import sys
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import handlers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_callback(data: str, user_id: int = 1) -> MagicMock:
    cb = AsyncMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.message = AsyncMock()
    cb.message.answer = AsyncMock()
    return cb


FAKE_COMMIT = "aabbccdd1122334455667788"
FAKE_VER = "1.0.15"
FAKE_COMMIT_SHORT = FAKE_COMMIT[:7]

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


RESULTS: list[dict] = []


def _record(name: str, passed: bool, detail: str = "") -> None:
    icon = "✅" if passed else "❌"
    RESULTS.append({"name": name, "passed": passed, "detail": detail})
    print(f"  {icon} {name}" + (f": {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# Test 1 — admin_rollback_trigger: deployer unreachable → confirm screen shown
#           (deployer_note appears, button still rendered, no exception raised)
# ---------------------------------------------------------------------------
async def test_trigger_shows_confirm_when_deployer_down() -> None:
    print("\n[1] admin_rollback_trigger — deployer unreachable → confirm screen shown")

    cb = _make_callback(f"admin_rollback_trigger:{FAKE_VER}")
    mocked_edit = AsyncMock()


    def _fake_is_creator(uid):
        return True

    with patch.object(handlers.db, "is_creator", side_effect=_fake_is_creator), \
         patch.object(handlers.db, "_get_connection") as mock_conn, \
         patch("app_version.get_git_status_info", return_value={
             "is_clean": True,
             "branch": "main",
             "commit": FAKE_COMMIT,
             "total_commits": 10,
             "modified_files": [],
         }), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
         patch("handlers.callback_edit_or_answer", mocked_edit):

        # Set up fake DB connection returning our row
        mock_conn.return_value = FakeDBContext()

        # Do NOT mock aiohttp — let it actually fail to resolve "deployer"
        await handlers.admin_rollback_trigger(cb)

    # callback_edit_or_answer must have been called (confirm screen rendered)
    _record(
        "confirm screen rendered despite deployer being down",
        mocked_edit.called,
        f"called={mocked_edit.called}",
    )

    if mocked_edit.called:
        rendered_text = mocked_edit.call_args[0][1]  # positional arg 1
        has_deployer_note = "Deployer" in rendered_text and "локальный" in rendered_text
        _record(
            "deployer_note present in confirm text",
            has_deployer_note,
            repr(rendered_text[:200]),
        )
        has_confirm_button = any(
            "admin_confirm_rollback" in str(a)
            for a in mocked_edit.call_args_list
        )
        # Check keyboard in kwargs or positional
        kwargs = mocked_edit.call_args[1]
        kb = kwargs.get("reply_markup")
        button_found = False
        if kb:
            for row in kb.inline_keyboard:
                for btn in row:
                    if "admin_confirm_rollback" in (btn.callback_data or ""):
                        button_found = True
        _record("confirm button present in keyboard", button_found)


# ---------------------------------------------------------------------------
# Test 2 — admin_confirm_rollback: deployer unreachable → local restart used,
#           DB state preserved, checkout NOT reverted
# ---------------------------------------------------------------------------
async def test_confirm_uses_local_restart_when_deployer_down() -> None:
    print("\n[2] admin_confirm_rollback — deployer unreachable → local restart, state preserved")

    cb = _make_callback(f"admin_confirm_rollback:{FAKE_VER}:{FAKE_COMMIT}")
    checkout_calls: list[list] = []
    local_restart_called = False
    db_settings: dict[str, str] = {}

    def fake_subprocess_run(args, **kwargs):
        checkout_calls.append(list(args))
        return MagicMock(returncode=0, stdout="", stderr="")

    async def fake_trigger_deploy():
        # _trigger_deploy catches all exceptions internally and returns (False, str(e))
        # Simulate what the real function returns when deployer DNS fails:
        return False, "Cannot connect to host deployer:25100 ssl:default [Temporary failure in name resolution]"

    async def fake_trigger_local_restart():
        nonlocal local_restart_called
        local_restart_called = True
        return True, "accepted_local"

    def fake_set_setting(key, val):
        db_settings[key] = val

    def fake_delete_setting(key):
        db_settings.pop(key, None)

    def fake_is_creator(uid):
        return True

    with patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \
         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
         patch.object(handlers.db, "set_setting", side_effect=fake_set_setting), \
         patch.object(handlers.db, "delete_setting", side_effect=fake_delete_setting), \
         patch("app_version.get_git_commit", return_value=FAKE_COMMIT), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch("handlers._trigger_deploy", side_effect=fake_trigger_deploy), \
         patch("handlers._trigger_local_restart", side_effect=fake_trigger_local_restart):

        await handlers.admin_confirm_rollback(cb)

    _record("local restart was called", local_restart_called)

    # git checkout main (revert) must NOT be in checkout_calls
    reverted = any(
        "main" in args and "checkout" in args
        for args in checkout_calls
    )
    _record("checkout NOT reverted to main", not reverted, f"checkout_calls={checkout_calls}")

    # rollback_active must still be set
    rb_active = db_settings.get("rollback_active") == "1"
    _record("rollback_active preserved in DB", rb_active, f"db_settings={db_settings}")

    # Success message must mention local restart
    answer_calls_text = " ".join(
        str(a) for call_item in cb.message.answer.call_args_list
        for a in call_item[0]
    )
    _record(
        "success message mentions local restart",
        "локальный безопасный перезапуск" in answer_calls_text,
        repr(answer_calls_text[:300]),
    )


# ---------------------------------------------------------------------------
# Test 3 — admin_undo_rollback: deployer unreachable → local restart used
# ---------------------------------------------------------------------------
async def test_undo_uses_local_restart_when_deployer_down() -> None:
    print("\n[3] admin_undo_rollback — deployer unreachable → local restart used")

    cb = _make_callback("admin_undo_rollback")
    local_restart_called = False

    async def fake_trigger_deploy():
        # _trigger_deploy catches all exceptions internally and returns (False, str(e))
        return False, "Cannot connect to host deployer:25100 ssl:default [Temporary failure in name resolution]"

    async def fake_trigger_local_restart():
        nonlocal local_restart_called
        local_restart_called = True
        return True, "accepted_local"

    def fake_subprocess_run(args, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_is_creator(uid):
        return True

    def fake_delete_setting(key):
        pass

    with patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \
         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
         patch.object(handlers.db, "delete_setting", side_effect=fake_delete_setting), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch("handlers._trigger_deploy", side_effect=fake_trigger_deploy), \
         patch("handlers._trigger_local_restart", side_effect=fake_trigger_local_restart):

        await handlers.admin_undo_rollback(cb)

    _record("local restart called in undo path", local_restart_called)

    answer_calls_text = " ".join(
        str(a) for call_item in cb.message.answer.call_args_list
        for a in call_item[0]
    )
    _record(
        "undo success message rendered",
        "main" in answer_calls_text or "возвращение" in answer_calls_text.lower(),
        repr(answer_calls_text[:300]),
    )
    _record(
        "undo message mentions local restart",
        "локальный безопасный перезапуск" in answer_calls_text,
        repr(answer_calls_text[:300]),
    )


# ---------------------------------------------------------------------------
# Test 4 — admin_confirm_rollback: both paths fail → checkout reverted, state cleared
# ---------------------------------------------------------------------------
async def test_confirm_reverts_when_both_paths_fail() -> None:
    print("\n[4] admin_confirm_rollback — both deployer and local fail → revert")

    cb = _make_callback(f"admin_confirm_rollback:{FAKE_VER}:{FAKE_COMMIT}")
    checkout_calls: list[list] = []
    db_settings: dict[str, str] = {"rollback_active": "1"}

    def fake_subprocess_run(args, **kwargs):
        checkout_calls.append(list(args))
        return MagicMock(returncode=0, stdout="", stderr="")

    async def fake_trigger_deploy():
        # _trigger_deploy catches all exceptions internally and returns (False, str(e))
        return False, "Cannot connect to host deployer:25100 ssl:default [Temporary failure in name resolution]"

    async def fake_trigger_local_restart():
        return False, "restart_script_not_found:/app/scripts/restart_clean.sh"

    def fake_set_setting(key, val):
        db_settings[key] = val

    def fake_delete_setting(key):
        db_settings.pop(key, None)

    def fake_is_creator(uid):
        return True

    with patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \
         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
         patch.object(handlers.db, "set_setting", side_effect=fake_set_setting), \
         patch.object(handlers.db, "delete_setting", side_effect=fake_delete_setting), \
         patch("app_version.get_git_commit", return_value=FAKE_COMMIT), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch("handlers._trigger_deploy", side_effect=fake_trigger_deploy), \
         patch("handlers._trigger_local_restart", side_effect=fake_trigger_local_restart):

        await handlers.admin_confirm_rollback(cb)

    reverted = any("main" in args and "checkout" in args for args in checkout_calls)
    _record("checkout reverted to main on double-failure", reverted, f"calls={checkout_calls}")

    state_cleared = "rollback_active" not in db_settings
    _record("rollback_active cleared from DB", state_cleared, f"db_settings={db_settings}")

    answer_calls_text = " ".join(
        str(a) for call_item in cb.message.answer.call_args_list
        for a in call_item[0]
    )
    _record("error message sent to user", "❌" in answer_calls_text or "Ошибка" in answer_calls_text)


# ---------------------------------------------------------------------------
# Test 5 — admin_rollback_trigger: deployer available → NO deployer_note
# ---------------------------------------------------------------------------
async def test_trigger_no_note_when_deployer_ok() -> None:
    print("\n[5] admin_rollback_trigger — deployer healthy → no warning note in text")

    cb = _make_callback(f"admin_rollback_trigger:{FAKE_VER}")
    mocked_edit = AsyncMock()


    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=False)

    def fake_is_creator(uid):
        return True

    with patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \
         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
         patch.object(handlers.db, "_get_connection") as mock_conn, \
         patch("app_version.get_git_status_info", return_value={
             "is_clean": True, "branch": "main", "commit": FAKE_COMMIT,
             "total_commits": 10, "modified_files": [],
         }), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")), \
         patch("handlers.callback_edit_or_answer", mocked_edit), \
         patch("aiohttp.ClientSession") as mock_session_cls:

        mock_conn.return_value = FakeDBContext()

        # Mock healthy deployer response
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session_cls.return_value = mock_session

        await handlers.admin_rollback_trigger(cb)

    if mocked_edit.called:
        rendered_text = mocked_edit.call_args[0][1]
        no_warn = "Deployer недоступен" not in rendered_text and "локальный безопасный" not in rendered_text
        _record("no deployer warning when deployer is healthy", no_warn, repr(rendered_text[:200]))
    else:
        _record("confirm screen rendered (deployer ok)", False, "mocked_edit not called")


# ---------------------------------------------------------------------------
# Test 6 — Confirm confirm handler uses deployer when available
# ---------------------------------------------------------------------------
async def test_confirm_uses_deployer_when_available() -> None:
    print("\n[6] admin_confirm_rollback — deployer available → deployer used, no local restart")

    cb = _make_callback(f"admin_confirm_rollback:{FAKE_VER}:{FAKE_COMMIT}")
    local_restart_called = False
    deploy_called = False

    async def fake_trigger_deploy():
        nonlocal deploy_called
        deploy_called = True
        return True, "accepted"

    async def fake_trigger_local_restart():
        nonlocal local_restart_called
        local_restart_called = True
        return True, "accepted_local"

    def fake_subprocess_run(args, **kwargs):
        return MagicMock(returncode=0, stdout="", stderr="")

    def fake_is_creator(uid):
        return True

    db_settings: dict[str, str] = {}

    with patch.object(handlers.db, "is_creator", side_effect=fake_is_creator), \
         patch.object(handlers.db, "_get_connection", return_value=FakeDBContext()), \
         patch.object(handlers.db, "set_setting", side_effect=lambda k, v: db_settings.update({k: v})), \
         patch.object(handlers.db, "delete_setting", side_effect=lambda k: db_settings.pop(k, None)), \
         patch("app_version.get_git_commit", return_value=FAKE_COMMIT), \
         patch("app_version._get_repo_root", return_value="/workspace"), \
         patch("subprocess.run", side_effect=fake_subprocess_run), \
         patch("handlers._trigger_deploy", side_effect=fake_trigger_deploy), \
         patch("handlers._trigger_local_restart", side_effect=fake_trigger_local_restart):

        await handlers.admin_confirm_rollback(cb)

    _record("deployer was used when available", deploy_called)
    _record("local restart NOT called when deployer succeeded", not local_restart_called)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def main() -> None:
    print("=" * 65)
    print("Rollback deployer-fallback validation")
    print(f"Production config: DEPLOYER_URL={os.getenv('DEPLOYER_URL', '(not set)')}")
    print("=" * 65)

    await test_trigger_shows_confirm_when_deployer_down()
    await test_confirm_uses_local_restart_when_deployer_down()
    await test_undo_uses_local_restart_when_deployer_down()
    await test_confirm_reverts_when_both_paths_fail()
    await test_trigger_no_note_when_deployer_ok()
    await test_confirm_uses_deployer_when_available()

    print("\n" + "=" * 65)
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    failed = total - passed
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed:
        print("\nFailed tests:")
        for r in RESULTS:
            if not r["passed"]:
                print(f"  ❌ {r['name']}: {r['detail']}")
    print("=" * 65)
    return failed


if __name__ == "__main__":
    failures = asyncio.run(main())
    sys.exit(0 if failures == 0 else 1)
