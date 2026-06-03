import os
import re

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # Patterns to replace (removing ❤️ and 💛 when they are decorations)
    # We will be careful and target specific lines/phrases or do a global replace with exclusions.
    
    # 404.html
    # const symbols = ['💕', '❤️', '🌸', '💗', '✨', '💖', '🫶', '💝']; -> keep, not text addition.
    
    # admin.html
    content = content.replace('ничего страшного', 'ничего страшного')
    
    # config.py
    content = content.replace('Привет! С праздником! 🎉', 'Привет! С праздником! 🎉')
    content = content.replace('Привет ✨', 'Привет ✨')
    content = content.replace('Самые значимые события в нашей истории', 'Самые значимые события в нашей истории')
    content = content.replace('Все то что я вспомнил и что тебе было бы тоже хорошо напомнить', 'Все то что я вспомнил и что тебе было бы тоже хорошо напомнить')
    
    # companion_personality.py
    content = content.replace("'Посчитай дни вместе'", "'Посчитай дни вместе'")
    content = content.replace("желтым сердцем", "желтым сердцем")
    content = content.replace("Хочется тепла", "Хочется тепла")

    # handlers.py
    content = content.replace('Создай пару', 'Создай пару')
    
    # http_api.py
    content = content.replace('Этот момент был долгожданным', 'Этот момент был долгожданным')
    content = content.replace('По последнему входу', 'По последнему входу')
    content = content.replace('Сен чынында бул учурду күттүң', 'Сен чынында бул учурду күттүң')
    content = content.replace('Акыркы кириш боюнча', 'Акыркы кириш боюнча')
    content = content.replace('Du hast auf diesen Moment gewartet', 'Du hast auf diesen Moment gewartet')
    content = content.replace('Letzter Besuch', 'Letzter Besuch')
    content = content.replace('You were really waiting for this moment', 'You were really waiting for this moment')
    content = content.replace('By last visit', 'By last visit')
    content = content.replace('Увидимся совсем скоро!', 'Увидимся совсем скоро!')

    # index.html
    # aiWelcome
    content = content.replace('он тебе ответит', 'он тебе ответит')
    content = content.replace('sie antwortet dir', 'sie antwortet dir')
    content = content.replace('sie antwortet', 'sie antwortet')
    content = content.replace('it will answer', 'it will answer')

    # aiMessageForYou
    content = content.replace('Сообщение для тебя', 'Сообщение для тебя')
    content = content.replace('Nachricht für dich', 'Nachricht für dich')
    content = content.replace('Message for you', 'Message for you')

    # aiMessageOk, notificationOk, addUserOk
    content = content.replace('Хорошо', 'Хорошо')
    content = content.replace('Okay', 'Okay')

    # celebrationAnniversarySub
    content = content.replace('Это особенный день', 'Это особенный день')
    content = content.replace('Das ist ein besonderer Tag', 'Das ist ein besonderer Tag')
    content = content.replace('This is a special day', 'This is a special day')
    
    # passwordNeedAnswer
    content = content.replace('Нужно ввести ответ', 'Нужно ввести ответ')
    content = content.replace('Du musst eine Antwort eingeben', 'Du musst eine Antwort eingeben')
    content = content.replace('You need to enter an answer', 'You need to enter an answer')

    # togetherSuffix
    content = content.replace("вместе", "вместе")
    content = content.replace("zusammen", "zusammen")
    content = content.replace("together", "together")
    
    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated {filepath}")

for root, dirs, files in os.walk('/root/KsyshaTest'):
    if '.git' in root or '__pycache__' in root or 'venv' in root:
        continue
    for file in files:
        if file.endswith('.py') or file.endswith('.html') or file.endswith('.json') or file.endswith('.js'):
            process_file(os.path.join(root, file))
