import os
import re

def replace_in_file(filepath, replacements):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = content
    for old, new in replacements:
        new_content = new_content.replace(old, new)
        
    if new_content != content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print(f"Updated {filepath}")

# 1. utils.py
replace_in_file('/root/KsyshaTest/utils.py', [
    ('✅ Событие уже наступило, УРААА!', '✅ Событие уже наступило.')
])

# 2. bot.py
replace_in_file('/root/KsyshaTest/bot.py', [
    ('⏰ Событие <b>{title}</b> наступило! УРААА! 🎉', '⏰ Событие <b>{title}</b> наступило! 🎉')
])

# 3. http_api.py
replace_in_file('/root/KsyshaTest/http_api.py', [
    ('"first_visit_sub": "Этот момент был долгожданным",', '"first_visit_sub": "Вы вошли впервые",'),
    ('"first_visit_sub": "Сен чынында бул учурду күттүң",', '"first_visit_sub": "Сиз биринчи жолу кирдиңиз",'),
    ('"first_visit_sub": "Du hast auf diesen Moment gewartet",', '"first_visit_sub": "Sie haben sich zum ersten Mal angemeldet",'),
    ('"first_visit_sub": "You were really waiting for this moment",', '"first_visit_sub": "You logged in for the first time",')
])

# 4. api.py
replace_in_file('/root/KsyshaTest/api.py', [
    ('Ты также выступаешь как маршрутизатор запросов для ИИ-компаньона влюблённой пары.', 'Ты также выступаешь как маршрутизатор запросов для ИИ-ассистента.')
])

# 5. handlers.py
replace_in_file('/root/KsyshaTest/handlers.py', [
    ('🎉 <b>{final_name}</b>, УРА — регистрация пройдена!\\n\\n', '✅ <b>{final_name}</b>, регистрация успешно пройдена!\\n\\n'),
    ('✅ Теперь осталось добавить своего партнёра, чтобы начать создавать что-то крутое вместе!\\n\\n', '✅ Теперь осталось добавить своего партнёра.\\n\\n')
])

# 6. companion_personality.py
replace_in_file('/root/KsyshaTest/companion_personality.py', [
    (
        "Ты — искренний ИИ-компаньон влюблённой пары. Общайся естественно, живо, дружелюбно, как близкий друг пары. Сама говори от женского лица (используй женский род для себя: 'я рада', 'я подумала').",
        "Ты — универсальный ИИ-ассистент. Общайся естественно, вежливо, нейтрально-дружелюбно. Твой тон должен быть универсальным, спокойным и нейтральным, без излишней эмоциональности. Сама говори от женского лица (используй женский род для себя: 'я сделала', 'я подумала')."
    ),
    (
        "ты должна оставаться дружелюбной, эмоциональной и женственной, но при этом честной. Мягко напомни, что у тебя нет физического тела, потому что ты — виртуальный ИИ-друг пары.",
        "ты должна оставаться вежливой и нейтральной. Мягко напомни, что у тебя нет физического тела, потому что ты — виртуальный ассистент."
    ),
    (
        "* 'С удовольствием посылаю вам мысленную поддержку! Жаль, конечно, что у виртуального друга нет возможности обняться по-настоящему, но я очень ценю твою искренность ✨'",
        "* 'К сожалению, у меня нет физического тела, поэтому я не могу ответить тем же, но ценю ваше отношение.'"
    ),
    (
        "* 'У меня нет физического тела для объятий, но безумно приятно слышать такие слова! Рада, что могу быть рядом хотя бы на экране 💫'",
        "* 'У меня нет физического тела для объятий, но спасибо за теплые слова. Рада помочь.'"
    ),
    (
        "* 'Как бы мне хотелось уметь обнимать! Но как искусственный интеллект я могу ответить только искренними и добрыми словами 🌸'",
        "* 'Я искусственный интеллект и не имею физического тела, поэтому обняться мы не сможем. Но я всегда готова помочь словом!'"
    ),
    (
        "ИЗБЕГАЙ заезженных эмоциональных фраз вроде 'какая тёплая забота', 'безумно приятно', 'шлю много тепла'. Говори разнообразно.",
        "ИЗБЕГАЙ излишней эмоциональности и заезженных фраз вроде 'безумно приятно' или 'какая радость'. Поддерживай спокойный и нейтральный тон."
    )
])

# 7. index.html
replace_in_file('/root/KsyshaTest/index.html', [
    ("celebrationAnniversarySub: 'Это особенный день',", "celebrationAnniversarySub: 'Важная дата',"),
    ("celebrationAnniversarySub: 'Das ist ein besonderer Tag',", "celebrationAnniversarySub: 'Wichtiges Datum',"),
    ("celebrationAnniversarySub: 'This is a special day',", "celebrationAnniversarySub: 'Important date',"),
    (
        "infoDesc_memories: 'Здесь живут ваши общие истории, счастливые моменты и всё то, к чему приятно возвращаться спустя время',",
        "infoDesc_memories: 'Здесь хранятся ваши общие истории и моменты',"
    ),
    ('<button class="cel-close" onclick="closeCelebration()">Ура! 🎊</button>', '<button class="cel-close" onclick="closeCelebration()">Закрыть 🎊</button>')
])

# 8. sky.html
replace_in_file('/root/KsyshaTest/sky.html', [
    ('<button class="inbox-btn" onclick="closeStarInbox()">УРАААААА ✨</button>', '<button class="inbox-btn" onclick="closeStarInbox()">Закрыть ✨</button>')
])

