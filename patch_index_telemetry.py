import sys

with open('index.html', 'r', encoding='utf-8') as f:
    code = f.read()

target_1 = """        if (_urlToken) {
          const existingRoleBeforeToken = getCurrentRole();
          const existingVidBeforeToken = getCookie('visitor_id');
          let shouldConsumeToken = false;
          
          const authDebugId = Math.random().toString(36).substring(2, 8);"""

new_1 = """        if (_urlToken) {
          const existingRoleBeforeToken = getCurrentRole();
          const existingVidBeforeToken = getCookie('visitor_id');
          let shouldConsumeToken = false;
          
          const authDebugId = Math.random().toString(36).substring(2, 8);
          let debugState = {
            id: authDebugId,
            role_cookie_present: !!existingRoleBeforeToken,
            vid_cookie_present: !!existingVidBeforeToken,
            token_check_called: false,
            token_check_result: null,
            token_check_match: null,
            should_consume_token: false,
            modal_action: null,
            token_auth_called: false,
            token_auth_result: null,
            treated_as: (existingRoleBeforeToken && existingVidBeforeToken) ? "logged-in" : "logged-out"
          };"""

code = code.replace(target_1, new_1, 1)

code = code.replace("console.log(`[auth-debug:${authDebugId}] Both cookies present, calling /api/token_check`);", "console.log(`[auth-debug:${authDebugId}] Both cookies present, calling /api/token_check`);\n            debugState.token_check_called = true;")

code = code.replace("if (checkResp.ok) {\n                const checkData = await checkResp.json();", "if (checkResp.ok) {\n                const checkData = await checkResp.json();\n                debugState.token_check_result = checkData.ok;\n                debugState.token_check_match = checkData.match;")

code = code.replace("document.getElementById('cancelTokenSwitch').onclick = () => {\n                      console.log(`[auth-debug:${authDebugId}] User clicked Cancel in modal`);", "document.getElementById('cancelTokenSwitch').onclick = () => {\n                      console.log(`[auth-debug:${authDebugId}] User clicked Cancel in modal`);\n                      debugState.modal_action = 'cancel';")

code = code.replace("document.getElementById('confirmTokenSwitch').onclick = () => {\n                      console.log(`[auth-debug:${authDebugId}] User clicked Switch in modal`);", "document.getElementById('confirmTokenSwitch').onclick = () => {\n                      console.log(`[auth-debug:${authDebugId}] User clicked Switch in modal`);\n                      debugState.modal_action = 'switch';")

code = code.replace("if (shouldConsumeToken) {\n            console.log(`[auth-debug:${authDebugId}] Executing /api/token_auth`);", "if (shouldConsumeToken) {\n            console.log(`[auth-debug:${authDebugId}] Executing /api/token_auth`);\n            debugState.token_auth_called = true;")

code = code.replace("if (_authData && _authData.ok) {\n                console.log(`[auth-debug:${authDebugId}] token_auth success, setting cookies for user_id:`, _authData.user_id);", "if (_authData && _authData.ok) {\n                console.log(`[auth-debug:${authDebugId}] token_auth success, setting cookies for user_id:`, _authData.user_id);\n                debugState.token_auth_result = 'success';")
code = code.replace("} else {\n                console.log(`[auth-debug:${authDebugId}] token_auth returned ok=false`);", "} else {\n                console.log(`[auth-debug:${authDebugId}] token_auth returned ok=false`);\n                debugState.token_auth_result = 'failed';")

code = code.replace("console.warn('[auth] Не удалось войти по токену:', e);\n              console.log(`[auth-debug:${authDebugId}] Exception during token_auth`);", "console.warn('[auth] Не удалось войти по токену:', e);\n              console.log(`[auth-debug:${authDebugId}] Exception during token_auth`);\n              debugState.token_auth_result = 'exception';")


target_end = """          // Убираем токен из URL без перезагрузки страницы
          _urlParams.delete('token');"""

new_end = """          debugState.should_consume_token = shouldConsumeToken;
          try {
            await fetch('/api/auth_debug_log', {
              method: 'POST',
              headers: withOptionalApiKey({ 'Content-Type': 'application/json' }),
              body: JSON.stringify(debugState),
              keepalive: true
            });
          } catch(e) {}

          // Убираем токен из URL без перезагрузки страницы
          _urlParams.delete('token');"""

code = code.replace(target_end, new_end, 1)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(code)

print("SUCCESS")
