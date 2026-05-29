import asyncio
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
    
    # Verify future compatibility buttons are present
    assert "admin_undo_rollback_soon" in first_row_callbacks, "ERROR: Undo rollback button is missing from the top row!"
    print("✅ Verified: 'Undo Rollback (soon)' placeholder is present in the top row.")
    
    # Verify rollback button is present for each version in the list
    rollback_soon_found = False
    details_found = False
    for cb in history_callbacks:
        if cb.startswith("admin_rollback_soon:"):
            rollback_soon_found = True
        if cb.startswith("admin_version_detail:"):
            details_found = True
            
    assert details_found, "ERROR: Version detail links are missing!"
    assert rollback_soon_found, "ERROR: Rollback placeholder links are missing!"
    print("✅ Verified: 'View Details' and 'Rollback' actions are present for version records.")
    
    # 3. Test future compatibility placeholder callbacks answer gracefully
    soon_callback = AsyncMock()
    soon_callback.from_user = MagicMock()
    soon_callback.from_user.id = db.get_creator_id()
    
    # Undo rollback
    await handlers.admin_undo_rollback_soon(soon_callback)
    soon_callback.answer.assert_called_with("↩️ Функция отмены отката версии будет доступна в следующем обновлении.", show_alert=True)
    print("✅ Verified: Undo rollback callback handler handles soon-to-be-available action gracefully.")
    
    # Rollback version
    soon_callback.data = "admin_rollback_soon:1.0.8"
    await handlers.admin_rollback_soon(soon_callback)
    soon_callback.answer.assert_called_with("⏪ Функция отката к версии 1.0.8 будет доступна в следующем обновлении.", show_alert=True)
    print("✅ Verified: Rollback callback handler handles soon-to-be-available action gracefully.")

    print("\n🎉 ALL UX VERIFICATION TESTS PASSED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    asyncio.run(test_version_ux_workflow())
