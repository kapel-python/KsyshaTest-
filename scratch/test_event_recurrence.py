import os
import sys
from datetime import datetime, timezone, timedelta

# Добавляем путь к проекту в sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import Database, db
from utils import calculate_next_occurrence, is_scheduled_event_expired

def run_test():
    print("=== STARTING EVENT RECURRENCE TEST ===")
    
    # 1. Добавляем тестовых пользователей
    user_id = 999999
    with db._get_connection() as conn:
        conn.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, "test_user"))
        conn.commit()

    # Задаем таймзону пользователя UTC+3
    db.set_user_setting(user_id, "timezone", "Europe/Moscow")

    # 2. Создаем события с датой в прошлом
    # Допустим, событие было запланировано на 2025-05-31 18:53:00
    past_date = "2025-05-31 18:53:00"
    
    # Создаем разовое событие (is_recurring = 0)
    one_time_id = db.add_scheduled_event(
        user_id=user_id,
        title="One-Time Event Test",
        description="This event should only fire once.",
        event_datetime=past_date,
        is_recurring=0
    )
    
    # Создаем ежегодное событие (is_recurring = 1)
    recurring_id = db.add_scheduled_event(
        user_id=user_id,
        title="Recurring Event Test",
        description="This event should repeat every year.",
        event_datetime=past_date,
        is_recurring=1
    )
    
    print(f"Created One-time Event #{one_time_id} and Recurring Event #{recurring_id}")

    # 3. Извлекаем их и проверяем даты наступления
    ev_one_time = db.get_scheduled_event(one_time_id)
    ev_recurring = db.get_scheduled_event(recurring_id)

    print(f"One-Time Event: database={past_date}, runtime={ev_one_time.event_datetime}")
    print(f"Recurring Event: database={past_date}, runtime={ev_recurring.event_datetime}")

    # Для разового дата не должна меняться:
    assert ev_one_time.event_datetime == past_date, "One-time event rolled forward incorrectly!"
    # Для ежегодного дата должна стать будущей (в 2026 или позже):
    assert ev_recurring.event_datetime > past_date, "Recurring event did not roll forward!"

    # 4. Проверяем логику уведомлений
    # Пометим, что оба были уведомлены в прошлом году (2025-05-31 18:55:00 UTC)
    past_notif_time = datetime(2025, 5, 31, 18, 55, tzinfo=timezone.utc)
    db.mark_event_notified_for_user(one_time_id, user_id, past_notif_time)
    db.mark_event_notified_for_user(recurring_id, user_id, past_notif_time)

    # Проверяем, считается ли уведомление отправленным
    # Для разового: уже было уведомлено, повторно не шлем -> is_event_notified_for_user = True
    notified_one_time = db.is_event_notified_for_user(one_time_id, user_id)
    # Для ежегодного: наступил новый год/новый цикл, нужно уведомить заново -> is_event_notified_for_user = False
    notified_recurring = db.is_event_notified_for_user(recurring_id, user_id)

    print(f"Notification status: One-time={notified_one_time} (Expected: True), Recurring={notified_recurring} (Expected: False)")

    assert notified_one_time is True, "One-time event was not marked as notified!"
    assert notified_recurring is False, "Recurring event should need a new notification!"

    # Очищаем за собой тестовые данные
    with db._get_connection() as conn:
        conn.execute("DELETE FROM event_notifications WHERE event_id IN (?, ?)", (one_time_id, recurring_id))
        conn.execute("DELETE FROM scheduled_events WHERE id IN (?, ?)", (one_time_id, recurring_id))
        conn.commit()

    print("=== ALL TESTS PASSED SUCCESSFULLY! ===")

if __name__ == "__main__":
    run_test()
