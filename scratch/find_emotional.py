import os
import re

words = ['рада', 'приятно', 'чудесн', 'замечательн', 'прекрасн', 'крутое вместе', 'ура', 'влюблён', 'эмоциональ', 'особенный', 'долгожданн', 'скучаю', 'безумно', 'искренн']

pattern = re.compile(f"({'|'.join(words)})", re.IGNORECASE)

for root, dirs, files in os.walk('/root/KsyshaTest'):
    if '.git' in root or '__pycache__' in root or 'venv' in root or 'scratch' in root:
        continue
    for file in files:
        if file.endswith('.py') or file.endswith('.html') or file.endswith('.js'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if pattern.search(line):
                    print(f"{path}:{i+1}:{line.strip()}")
