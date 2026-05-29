import asyncio
import sys
import html
from pathlib import Path

# Add workspace to path
sys.path.insert(0, "/root/KsyshaTest")

from unittest.mock import AsyncMock, MagicMock
from handlers import (
    admin_version_history,
    admin_version_detail,
)
from database import db

# Mock CallbackQuery
def make_mock_callback(data: str, user_id: int = 12345) -> AsyncMock:
    cb = AsyncMock()
    cb.data = data
    cb.from_user.id = user_id
    cb.message = AsyncMock()
    cb.message.chat.id = 11111
    cb.message.message_id = 22222
    cb.bot = AsyncMock()
    return cb


async def run_tests():
    print("--------------------------------------------------")
    print("RUNNING SELECTABLE VERSION HISTORY UI TEST SUITE")
    print("--------------------------------------------------")

    creator_id = db.get_creator_id()
    print(f"Creator ID: {creator_id}")

    # Test 1: List and Pagination View
    print("\n[TEST 1] Testing Version History List View & Select Buttons...")
    cb_list = make_mock_callback("admin_version_history:0", user_id=creator_id)
    await admin_version_history(cb_list)
    
    # Verify the message text and keyboard
    text_list = cb_list.bot.edit_message_text.call_args[1]["text"]
    markup_list = cb_list.bot.edit_message_text.call_args[1]["reply_markup"]
    
    print("✓ Received list text view:")
    print(text_list)
    
    assert "📦 История версий" in text_list
    assert "Страница 1" in text_list
    
    # Check that we have a button for each version on page
    buttons_flat = [btn.text for row in markup_list.inline_keyboard for btn in row]
    print(f"Buttons flat list: {buttons_flat}")
    
    assert any("Версия 1.0.2" in btn for btn in buttons_flat), "Missing select button for Version 1.0.2"
    assert any("Версия 1.0.1" in btn for btn in buttons_flat), "Missing select button for Version 1.0.1"
    assert any("Версия 1.0.0" in btn for btn in buttons_flat), "Missing select button for Version 1.0.0"
    print("✓ Verified select buttons are rendered vertically under the list")

    # Test 2: Version Details View Selection
    print("\n[TEST 2] Testing Version Selection & Details View...")
    cb_detail = make_mock_callback("admin_version_detail:1.0.1:0", user_id=creator_id)
    await admin_version_detail(cb_detail)
    
    text_detail = cb_detail.bot.edit_message_text.call_args[1]["text"]
    markup_detail = cb_detail.bot.edit_message_text.call_args[1]["reply_markup"]
    
    print("✓ Received version details view:")
    print(text_detail)
    
    assert "📦 <b>Версия 1.0.1</b>" in text_detail
    assert "Описание:" in text_detail
    assert "Version history browsing in admin panel" in text_detail
    assert "Коммит:" in text_detail
    assert "cfc0c4d" in text_detail
    assert "Дата:" in text_detail
    assert "Действия:" in text_detail
    assert "(rollback functionality will be implemented later)" in text_detail
    print("✓ Verified all requested details and formatting are correct")
    
    # Test 3: Back Navigation Verification
    print("\n[TEST 3] Testing Back Navigation...")
    back_button = markup_detail.inline_keyboard[0][0]
    print(f"Back button: text={back_button.text}, callback_data={back_button.callback_data}")
    assert back_button.text == "⬅️ Назад к списку"
    assert back_button.callback_data == "admin_version_history:0"
    print("✓ Verified back-navigation button returns to correct page index")

    print("\n--------------------------------------------------")
    print("ALL SELECTABLE UI TESTS PASSED SUCCESSFULLY!")
    print("--------------------------------------------------")


if __name__ == "__main__":
    asyncio.run(run_tests())
