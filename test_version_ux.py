import asyncio

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
                return {"git_commit": self.commit_val, "version": "1.1.0", "description": "desc", "created_at": "2026-05-29 12:00:00", "status": "stable"}
        return Result(self.commit_val)

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project directory is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from utils import create_admin_keyboard
import handlers

async def test_version_ux_workflow():
    print("🧪 Starting Version Management UX Verification Suite...")
    
    # 1. Verify release button is NOT in the main admin keyboard
    admin_kb = create_admin_keyboard()
    button_callbacks = []
    for row in admin_kb.inline_keyboard:
        for button in row:
            button_callbacks.append(button.callback_data)
            
    assert "admin_create_release" not in button_callbacks, "ERROR: Release creation button is still in the main admin keyboard!"
    print("✅ Verified: Release creation button removed from the main admin keyboard.")
    
    # 2. Mock database and handler elements to verify the Version History screen
    # Setup FSM state mock
    from aiogram.fsm.context import FSMContext
    state = AsyncMock(spec=FSMContext)
    
    # Mock CallbackQuery for version history
    callback = AsyncMock()
    callback.from_user = MagicMock()
    callback.from_user.id = db.get_creator_id()
    callback.data = "admin_version_history:0"
    
    # We will capture the arguments passed to callback_edit_or_answer
    captured_text = None
    captured_reply_markup = None
    
    async def mock_callback_edit_or_answer(cb, text, reply_markup=None, parse_mode=None):
        nonlocal captured_text, captured_reply_markup
        captured_text = text
        captured_reply_markup = reply_markup
        
    print("🧪 Running admin_version_history handler...")
    with patch('handlers.callback_edit_or_answer', mock_callback_edit_or_answer):
        await handlers.admin_version_history(callback)
        
    assert captured_reply_markup is not None, "ERROR: admin_version_history did not set inline keyboard!"
    
    # Extract all callbacks from version history keyboard
    history_callbacks = []
    history_buttons_by_text = {}
    for row in captured_reply_markup.inline_keyboard:
        for btn in row:
            history_callbacks.append(btn.callback_data)
            history_buttons_by_text[btn.text] = btn.callback_data
            
    # Check that Create Release is at the top (first row)
    first_row_callbacks = [btn.callback_data for btn in captured_reply_markup.inline_keyboard[0]]
    assert "admin_create_release" in first_row_callbacks, "ERROR: Release creation button is not at the top of the version history keyboard!"
    print("✅ Verified: Release creation button appears at the top of the version history screen.")
    
    # 3. Verify Version Details UI
    detail_found = False
    for cb in history_callbacks:
        if cb.startswith("admin_version_detail:"):
            detail_found = True
            break
            
    assert detail_found, "ERROR: Version detail links are missing from history!"
    print("✅ Verified: 'View Details' action is present for version records.")
    
    # Test detail view renders rollback actions
    detail_cb = AsyncMock()
    detail_cb.from_user = MagicMock()
    detail_cb.from_user.id = db.get_creator_id()
    detail_cb.data = "admin_version_detail:1.1.0:0"
    
    captured_detail_markup = None
    async def mock_detail_edit_answer(cb, text, reply_markup=None, parse_mode=None):
        nonlocal captured_detail_markup
        captured_detail_markup = reply_markup
        
    # Test Rollback Undo state
    with patch('handlers.callback_edit_or_answer', mock_detail_edit_answer), \
         patch('app_version.get_git_commit', return_value="running_c"), \
         patch.object(db, '_get_connection', return_value=FakeDBContext('target_c')), \
         patch.object(db, 'get_setting', side_effect=lambda k: "1" if k == "rollback_active" else "target_c"):
         
        await handlers.admin_version_detail(detail_cb)
        
    assert captured_detail_markup is not None
    detail_callbacks = [btn.callback_data for row in captured_detail_markup.inline_keyboard for btn in row]
    assert "admin_undo_rollback" in detail_callbacks, "ERROR: Undo rollback button is missing from details view!"
    print("✅ Verified: 'Undo Rollback' action is present in version details when rollback is active.")
    
    # Test Rollback Trigger state
    with patch('handlers.callback_edit_or_answer', mock_detail_edit_answer), \
         patch('app_version.get_git_commit', return_value="running_c"), \
         patch.object(db, '_get_connection', return_value=FakeDBContext('target_c')), \
         patch.object(db, 'get_setting', return_value=None):
         
        await handlers.admin_version_detail(detail_cb)
        
    detail_callbacks = [btn.callback_data for row in captured_detail_markup.inline_keyboard for btn in row]
    assert "admin_rollback_trigger:1.1.0" in detail_callbacks, "ERROR: Rollback button is missing from details view!"
    print("✅ Verified: 'Rollback' action is present in version details for valid target versions.")

    print("\n🎉 ALL UX VERIFICATION TESTS PASSED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    asyncio.run(test_version_ux_workflow())
