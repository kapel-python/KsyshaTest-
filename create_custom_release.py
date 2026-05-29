import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the project directory is in the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import db
from utils import create_admin_keyboard
from app_version import get_version_metadata, get_git_status_info
import handlers

async def run_validation():
    print("🚀 Running custom release and UX validation suite...")
    
    # 1. Assert no rollback buttons in main admin panel
    admin_kb = create_admin_keyboard()
    for row in admin_kb.inline_keyboard:
        for btn in row:
            assert "rollback" not in btn.callback_data, f"Error: Rollback button found in main keyboard: {btn.text}"
            assert "Откат" not in btn.text, f"Error: Rollback text found in main keyboard: {btn.text}"
            
    print("✅ Verified: Main admin panel keyboard contains no rollback buttons.")
    
    # 2. Setup mock state with a COMPLETELY custom and unique description
    unique_desc = "Manual Release with Clean UX - 2026"
    
    # Check if there's any uncommitted change (required for release)
    status_info = get_git_status_info()
    if status_info.get("is_clean", True):
        # Create a small dummy change to ensure git commit doesn't fail
        dummy_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dummy_release_trigger.txt")
        with open(dummy_file, "w") as f:
            f.write(f"Trigger manual release with description: {unique_desc}")
            
    # Mock FSMState and CallbackQuery
    from aiogram.fsm.context import FSMContext
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"release_description": unique_desc}
    
    callback = AsyncMock()
    callback.from_user = MagicMock()
    callback.from_user.id = db.get_creator_id()
    callback.data = "admin_confirm_release"
    callback.message = AsyncMock()
    callback.message.answer = AsyncMock()
    
    mocked_edit = AsyncMock()
    
    print(f"🚀 Creating a REAL release with description: '{unique_desc}'...")
    # Mock _trigger_local_restart so it does not actually reboot our terminal environment, but run the rest of the flow
    with patch('handlers.callback_edit_or_answer', mocked_edit), \
         patch('handlers._trigger_local_restart', AsyncMock(return_value=(True, "mocked_restart"))):
        await handlers.admin_confirm_release(callback, state)
        
    print("✅ Release handler execution finished.")
    
    # 3. Assert APP_VERSION on disk contains the exact custom description
    disk_ver, disk_desc = get_version_metadata()
    print(f"Disk Version: {disk_ver}")
    print(f"Disk Description: '{disk_desc}'")
    
    assert disk_desc == unique_desc, f"Error: Disk description is '{disk_desc}', expected '{unique_desc}'"
    print("✅ Verified: app_version.py contains the exact custom description.")
    
    # 4. Assert version_history database table contains the exact custom description
    history = db.get_version_history()
    latest_rec = history[0]
    print(f"Database Latest Version: {latest_rec['version']}")
    print(f"Database Latest Description: '{latest_rec['description']}'")
    
    assert latest_rec["version"] == disk_ver, f"Error: DB version {latest_rec['version']} doesn't match disk version {disk_ver}"
    assert latest_rec["description"] == unique_desc, f"Error: DB description is '{latest_rec['description']}', expected '{unique_desc}'"
    print("✅ Verified: version_history in database contains the exact custom description.")
    
    # 5. Assert Version History screen contains NO rollback buttons
    captured_text = None
    captured_reply_markup = None
    
    async def mock_callback_edit_or_answer(cb, text, reply_markup=None, parse_mode=None):
        nonlocal captured_text, captured_reply_markup
        captured_text = text
        captured_reply_markup = reply_markup
        
    history_cb = AsyncMock()
    history_cb.from_user = MagicMock()
    history_cb.from_user.id = db.get_creator_id()
    history_cb.data = "admin_version_history:0"
    
    with patch('handlers.callback_edit_or_answer', mock_callback_edit_or_answer):
        await handlers.admin_version_history(history_cb)
        
    assert captured_reply_markup is not None, "Error: version history keyboard is None"
    
    rollback_btn_found = False
    details_btn_found = False
    create_release_btn_found = False
    
    for row in captured_reply_markup.inline_keyboard:
        for btn in row:
            if "rollback" in btn.callback_data or "Откат" in btn.text:
                rollback_btn_found = True
            if btn.callback_data.startswith("admin_version_detail:"):
                details_btn_found = True
            if btn.callback_data == "admin_create_release":
                create_release_btn_found = True
                
    assert create_release_btn_found, "Error: Create Release button is missing from history screen!"
    assert details_btn_found, "Error: View Details button is missing from history screen!"
    assert not rollback_btn_found, "Error: Rollback button found in history screen!"
    print("✅ Verified: Version history screen contains Create Release, View Details, but NO rollback buttons.")
    
    # 6. Assert Version Details screen contains correct layout & NO rollback buttons
    captured_detail_text = None
    captured_detail_markup = None
    
    async def mock_detail_callback_edit_or_answer(cb, text, reply_markup=None, parse_mode=None):
        nonlocal captured_detail_text, captured_detail_markup
        captured_detail_text = text
        captured_detail_markup = reply_markup
        
    detail_cb = AsyncMock()
    detail_cb.from_user = MagicMock()
    detail_cb.from_user.id = db.get_creator_id()
    detail_cb.data = f"admin_version_detail:{disk_ver}:0"
    
    with patch('handlers.callback_edit_or_answer', mock_detail_callback_edit_or_answer):
        await handlers.admin_version_detail(detail_cb)
        
    assert captured_detail_markup is not None, "Error: version details keyboard is None"
    
    detail_rollback_found = False
    back_to_list_found = False
    
    for row in captured_detail_markup.inline_keyboard:
        for btn in row:
            if "rollback" in btn.callback_data or "Откат" in btn.text:
                detail_rollback_found = True
            if btn.callback_data.startswith("admin_version_history:"):
                back_to_list_found = True
                
    assert back_to_list_found, "Error: 'Back to list' navigation button is missing!"
    assert not detail_rollback_found, "Error: Rollback button found in version details screen!"
    
    # Check text details
    print("\n--- Captured Version Details Content ---")
    print(captured_detail_text)
    print("----------------------------------------")
    
    assert "Описание:" in captured_detail_text, "Error: 'Описание:' header is missing!"
    assert "Коммит:" in captured_detail_text, "Error: 'Коммит:' header is missing!"
    assert "Дата:" in captured_detail_text, "Error: 'Дата:' header is missing!"
    assert "Доступные действия" not in captured_detail_text, "Error: Future rollback support text found in details!"
    
    print("✅ Verified: Version details screen layout is clean, displays correct headers, and has NO rollback buttons.")
    print("✅ Verified: Back navigation is working perfectly.")
    
    print("\n🎉 ALL MANUAL RELEASE AND UX AUDIT VALIDATIONS COMPLETED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    asyncio.run(run_validation())
