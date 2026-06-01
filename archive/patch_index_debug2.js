const fs = require('fs');
let code = fs.readFileSync('index.html', 'utf8');

// Fix syntax errors: `[auth-debug:${authDebugId}] Token detected:', -> `[auth-debug:${authDebugId}] Token detected:`,
code = code.replace(/console\.log\(`\[auth-debug:\$\{authDebugId\}\] ([^']+)',/g, "console.log(`[auth-debug:${authDebugId}] $1`,");
// Fix the remaining ones
code = code.replace(/console\.log\(`\[auth-debug:\$\{authDebugId\}\] ([^']+)';/g, "console.log(`[auth-debug:${authDebugId}] $1`);");

fs.writeFileSync('index.html', code);
