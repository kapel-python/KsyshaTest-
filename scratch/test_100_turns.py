import os
import sys

# Ensure project root is in python path
sys.path.insert(0, '/root/KsyshaTest')

from api import ask_companion

def run_test():
    all_data = {
        "memories": [],
        "events": [],
        "wishes": {},
        "favorites": {},
        "user_settings": {},
        "users": {
            "creator": {"display": "Pit_DB", "first_name": "Peter", "username": "pit_user"},
            "partner": {"display": "Ksyusha", "first_name": "Ksyusha", "username": "ksyusha_user"}
        },
        "stats": {},
        "profile_stats": {}
    }
    
    extra = {
        "site_role": "creator",
        "timezone_id": "Europe/Moscow"
    }

    # Construct conversation history of 96 messages (48 turns)
    history = []

    # Turn 1: Real Name (48 turns ago)
    history.append({"role": "user", "content": "Меня зовут Артём."})
    history.append({"role": "assistant", "content": "Приятно познакомиться, Артём! Рада нашему общению ✨"})

    # Turn 2: Preferred Addressing (47 turns ago)
    history.append({"role": "user", "content": "Называй меня Pit."})
    history.append({"role": "assistant", "content": "Договорились, буду называть тебя Pit! 💫"})

    # Turn 3: Favorite things (46 turns ago)
    history.append({"role": "user", "content": "Я обожаю пиццу пепперони и фильмы Кристофера Нолана."})
    history.append({"role": "assistant", "content": "Запомнила! Пепперони и фильмы Нолана — отличный выбор 🍕🎬"})

    # Turn 4: Turn fact (45 turns ago)
    history.append({"role": "user", "content": "В прошлый вторник мы ходили в музей космонавтики."})
    history.append({"role": "assistant", "content": "Звучит невероятно захватывающе! Музей космонавтики — отличное место 🚀"})

    # Turns 5 to 48 (filler conversation to push early facts 30-50 turns back)
    for i in range(5, 49):
        history.append({"role": "user", "content": f"Вопрос про воспоминания, шаг {i}."})
        history.append({"role": "assistant", "content": f"Хорошо, давай обсудим воспоминания, шаг {i}."})

    print(f"Total history messages count: {len(history)}")

    # 1. Test: Preferred addressing
    print("\n--- TEST 1: Как ко мне обращаться? ---")
    ans1, _ = ask_companion("Как ко мне обращаться?", history, all_data, extra)
    print(f"AI response: {ans1}")

    # 2. Test: Real name
    print("\n--- TEST 2: А моё настоящее имя? ---")
    ans2, _ = ask_companion("А моё настоящее имя?", history, all_data, extra)
    print(f"AI response: {ans2}")

    # 3. Test: Favorite things
    print("\n--- TEST 3: Какое у меня любимое блюдо и фильмы? ---")
    ans3, _ = ask_companion("Какое у меня любимое блюдо и фильмы?", history, all_data, extra)
    print(f"AI response: {ans3}")

    # 4. Test: Fact mentioned 45 turns ago
    print("\n--- TEST 4: Что мы делали в прошлый вторник? ---")
    ans4, _ = ask_companion("Что мы делали в прошлый вторник?", history, all_data, extra)
    print(f"AI response: {ans4}")

if __name__ == '__main__':
    run_test()
