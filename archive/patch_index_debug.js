const fs = require('fs');
let code = fs.readFileSync('index.html', 'utf8');

code = code.replace(
    /let shouldConsumeToken = false;\s*console\.log\('\[auth-debug\]/g,
    "let shouldConsumeToken = false;\n          const authDebugId = Math.random().toString(36).substring(2, 8);\n          console.log(`[auth-debug:${authDebugId}]"
);

code = code.replace(/console\.log\('\[auth-debug\]/g, "console.log(`[auth-debug:${authDebugId}]");
code = code.replace(/body: JSON\.stringify\(\{ token: _urlToken, current_visitor_id: existingVidBeforeToken \}\)/g, "body: JSON.stringify({ token: _urlToken, current_visitor_id: existingVidBeforeToken, debug_id: authDebugId })");
code = code.replace(/body: JSON\.stringify\(\{ token: _urlToken, tz: getUserTimezone\(\) \|\| '' \}\)/g, "body: JSON.stringify({ token: _urlToken, tz: getUserTimezone() || '', debug_id: authDebugId })");

fs.writeFileSync('index.html', code);
