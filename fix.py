import re

with open('index.html', 'r', encoding='utf-8') as f:
    text = f.read()

# Fix console.log(`[auth-debug:${authDebugId}] text', var);
text = re.sub(r'console\.log\(`\[auth-debug:\$\{authDebugId\}\]([^`\n]+)\',\s*', r'console.log(`[auth-debug:${authDebugId}]\1`, ', text)
# Fix console.log(`[auth-debug:${authDebugId}] text');
text = re.sub(r'console\.log\(`\[auth-debug:\$\{authDebugId\}\]([^`\n]+)\'\);', r'console.log(`[auth-debug:${authDebugId}]\1`);', text)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(text)

print("Fixed")
