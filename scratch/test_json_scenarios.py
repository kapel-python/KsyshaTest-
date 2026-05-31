import sys
import os

# Include project directory
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api import _extract_json_block

def run_tests():
    # 1. Leading text before JSON
    t1 = 'Привет! Как дела?\n{"tool": null, "answer": "Всё отлично!", "suggestions": ["Хорошо", "А у тебя?"]}'
    left1, obj1 = _extract_json_block(t1)
    assert left1 == 'Привет! Как дела?', f"Expected 'Привет! Как дела?', got {repr(left1)}"
    assert obj1 is not None and obj1.get("answer") == "Всё отлично!"

    # 2. Trailing text after JSON
    t2 = '{"tool": null, "answer": "Всё отлично!", "suggestions": ["Хорошо"]}\nНапиши мне ещё.'
    left2, obj2 = _extract_json_block(t2)
    assert left2 == 'Напиши мне ещё.', f"Expected 'Напиши мне ещё.', got {repr(left2)}"
    assert obj2 is not None and obj2.get("answer") == "Всё отлично!"

    # 3. Markdown before JSON
    t3 = 'Вот ответ:\n```json\n{"tool": null, "answer": "Всё отлично!"}\n```'
    left3, obj3 = _extract_json_block(t3)
    assert left3 == 'Вот ответ:', f"Expected 'Вот ответ:', got {repr(left3)}"
    assert obj3 is not None and obj3.get("answer") == "Всё отлично!"

    # 4. Multiline outputs inside and outside JSON
    t4 = 'Первая строка\nВторая строка\n\n{"tool": null, "answer": "Линия 1\\nЛиния 2"}\n\nКонец.'
    left4, obj4 = _extract_json_block(t4)
    assert left4 == 'Первая строка\nВторая строка\nКонец.', f"Expected cleanup, got {repr(left4)}"
    assert obj4 is not None and obj4.get("answer") == "Линия 1\nЛиния 2"

    # 5. Long stories inside JSON
    t5 = 'История:\n{"tool": null, "answer": "' + 'Давным-давно... ' * 500 + '"}'
    left5, obj5 = _extract_json_block(t5)
    assert left5 == 'История:'
    assert obj5 is not None and len(obj5.get("answer")) > 5000

    # 6. Long emotional conversations with curly braces inside values
    t6 = 'Как грустно...\n{"tool": null, "answer": "Я понимаю твою боль {хотя это и тяжело}, но давай держаться вместе.", "suggestions": ["Спасибо", "Давай"]}'
    left6, obj6 = _extract_json_block(t6)
    assert left6 == 'Как грустно...'
    assert obj6 is not None and "{хотя это и тяжело}" in obj6.get("answer")

    print("✅ All phase 1 verification test scenarios passed successfully!")

if __name__ == "__main__":
    run_tests()
