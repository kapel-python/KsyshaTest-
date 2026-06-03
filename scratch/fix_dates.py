import os
import re

def main():
    root_dir = '/root/KsyshaTest'
    for root, dirs, files in os.walk(root_dir):
        if '.git' in root or '__pycache__' in root or 'venv' in root:
            continue
        for file in files:
            if not (file.endswith('.py') or file.endswith('.html') or file.endswith('.json')):
                continue
            
            filepath = os.path.join(root, file)
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            
            if file == 'index.html':
                # Fix AI companion day counter
                new_content = re.sub(
                    r'const met = new Date\(2025,\s*9,\s*30\);\s*//\s*30\.10\.2025',
                    r'const metRaw = (window.APP_DATA && window.APP_DATA.date_met) ? window.APP_DATA.date_met : null;\n          if (!metRaw) { el.textContent = "Дата не указана"; return; }\n          const met = new Date(metRaw);',
                    new_content
                )
                # Fix translation strings
                new_content = new_content.replace('15 мая — особенный день ❤️', 'Это особенный день ❤️')
                new_content = new_content.replace('Der 30. Oktober ist ein besonderer Tag ❤️', 'Das ist ein besonderer Tag ❤️')
                new_content = new_content.replace('October 30 is a special day ❤️', 'This is a special day ❤️')
                new_content = new_content.replace('15 мая 2026', '15 мая 2026')
            
            if file == 'maintenance.html':
                new_content = new_content.replace('15 мая 2024', '15 мая 2024')
                new_content = new_content.replace("'2025'", "'2024'", 1)
            
            # Replace examples everywhere
            new_content = new_content.replace('15 мая 2024', '15 мая 2024')
            new_content = new_content.replace('15.05.2024', '15.05.2024')
            new_content = new_content.replace('15 мая 2026', '15 мая 2026')
            new_content = new_content.replace('15.05.2026', '15.05.2026')
            new_content = new_content.replace('15 мая', '15 мая')
            new_content = new_content.replace('15 мая', '15 мая')
            new_content = new_content.replace('15.05', '15.05')
            new_content = new_content.replace('пятнадцатое мая', 'пятнадцатое мая')

            if new_content != content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"Updated {filepath}")

if __name__ == '__main__':
    main()
