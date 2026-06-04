import re

with open("index.html", "r", encoding="utf-8") as f:
    idx = f.read()

# 1. Extract CSS
css_start = idx.find("        .memory {\n")
css_end = idx.find("        .memory-views-hint {\n")
if css_start == -1 or css_end == -1:
    print("CSS not found")
else:
    css_block = idx[css_start:css_end]
    # Remove from index.html
    idx = idx[:css_start] + idx[css_end:]
    # Prepend link to index.html
    link_tag = '    <link rel="stylesheet" href="/memory-card.css">\n'
    idx = idx.replace('    <link rel="stylesheet" href="/style.css">', link_tag + '    <link rel="stylesheet" href="/style.css">') # placeholder, let's find a good spot
    if link_tag not in idx:
        idx = idx.replace('</head>', link_tag + '</head>')
        
    with open("memory_card.css", "w", encoding="utf-8") as f:
        f.write("/* Extracted from index.html */\n" + css_block)

# 2. Extract JS
js_start = idx.find("      function createMemoryElement(m, categoriesMap) {\n")
js_end = idx.find("      function createEventElement(e) {\n")
if js_start == -1 or js_end == -1:
    print("JS not found")
else:
    js_block = idx[js_start:js_end]
    idx = idx[:js_start] + idx[js_end:]
    script_tag = '  <script src="/memory-card.js"></script>\n'
    idx = idx.replace('</body>', script_tag + '</body>')
    
    with open("memory_card.js", "w", encoding="utf-8") as f:
        f.write("/* Extracted from index.html */\n")
        # Expose it globally so index.html can use it
        f.write(js_block)
        f.write("\nwindow.createMemoryElement = createMemoryElement;\nwindow.MemoryCard = { createMemoryElement: createMemoryElement };\n")

with open("index.html", "w", encoding="utf-8") as f:
    f.write(idx)

print("Refactoring done")
