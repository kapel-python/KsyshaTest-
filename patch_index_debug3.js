const fs = require('fs');
let code = fs.readFileSync('index.html', 'utf8');

// Insert authDebugId definition
code = code.replace(
    /let shouldConsumeToken = false;\s*console\.log\('\[auth-debug\]/g,
    "let shouldConsumeToken = false;\n          const authDebugId = Math.random().toString(36).substring(2, 8);\n          console.log(`[auth-debug:${authDebugId}]"
);

// Replace remaining console.log('[auth-debug] ...') with console.log(`[auth-debug:${authDebugId}] ...`)
// Using a regex to match console.log('[auth-debug] ', arg) -> console.log(`[auth-debug:${authDebugId}]`, arg)
code = code.replace(/console\.log\('\[auth-debug\]/g, "console.log(`[auth-debug:${authDebugId}]` + ' ' +");
code = code.replace(/console\.warn\('\[auth-debug\]/g, "console.warn(`[auth-debug:${authDebugId}]` + ' ' +");
code = code.replace(/console\.error\('\[auth-debug\]/g, "console.error(`[auth-debug:${authDebugId}]` + ' ' +");

// We still need to fix the first one we replaced in the first regex:
// "let shouldConsumeToken = false;\n          const authDebugId = Math.random().toString(36).substring(2, 8);\n          console.log(`[auth-debug:${authDebugId}]"
// Wait, the first regex replaced the first `console.log('[auth-debug]`, so we need to fix it. Let's just revert index.html again inside the script.
