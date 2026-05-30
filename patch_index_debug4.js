const fs = require('fs');
let code = fs.readFileSync('index.html', 'utf8');

const targetStr = `        if (_urlToken) {
          const existingRoleBeforeToken = getCurrentRole();
          const existingVidBeforeToken = getCookie('visitor_id');
          let shouldConsumeToken = false;`;

const newStr = `        if (_urlToken) {
          const existingRoleBeforeToken = getCurrentRole();
          const existingVidBeforeToken = getCookie('visitor_id');
          let shouldConsumeToken = false;
          
          const authDebugId = Math.random().toString(36).substring(2, 8);
          console.log(\`[auth-debug:\${authDebugId}] Token detected:\`, _urlToken.substring(0, 4) + '...');
          console.log(\`[auth-debug:\${authDebugId}] existingRoleBeforeToken:\`, existingRoleBeforeToken);
          console.log(\`[auth-debug:\${authDebugId}] existingVidBeforeToken:\`, existingVidBeforeToken);`;

let pos = code.indexOf(targetStr);
if (pos === -1) {
  console.log("NOT FOUND 1");
  process.exit(1);
}
code = code.substring(0, pos) + newStr + code.substring(pos + targetStr.length);


const targetStr2 = `          if (existingRoleBeforeToken && existingVidBeforeToken) {
            try {
              const checkResp = await fetch('/api/token_check', {
                method: 'POST',
                headers: withOptionalApiKey({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ token: _urlToken, current_visitor_id: existingVidBeforeToken })
              });
              if (checkResp.ok) {
                const checkData = await checkResp.json();
                if (checkData.ok) {
                  if (checkData.match) {
                    shouldConsumeToken = true;
                  } else {
                    shouldConsumeToken = await new Promise((resolve) => {`;

const newStr2 = `          if (existingRoleBeforeToken && existingVidBeforeToken) {
            console.log(\`[auth-debug:\${authDebugId}] Both cookies present, calling /api/token_check\`);
            try {
              const checkResp = await fetch('/api/token_check', {
                method: 'POST',
                headers: withOptionalApiKey({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ token: _urlToken, current_visitor_id: existingVidBeforeToken, debug_id: authDebugId })
              });
              console.log(\`[auth-debug:\${authDebugId}] token_check HTTP status:\`, checkResp.status);
              if (checkResp.ok) {
                const checkData = await checkResp.json();
                console.log(\`[auth-debug:\${authDebugId}] token_check response:\`, checkData);
                if (checkData.ok) {
                  if (checkData.match) {
                    console.log(\`[auth-debug:\${authDebugId}] token_check returned match=true, setting shouldConsumeToken = true\`);
                    shouldConsumeToken = true;
                  } else {
                    console.log(\`[auth-debug:\${authDebugId}] token_check returned match=false, executing modal branch\`);
                    shouldConsumeToken = await new Promise((resolve) => {`;

pos = code.indexOf(targetStr2);
if (pos === -1) {
  console.log("NOT FOUND 2");
  process.exit(1);
}
code = code.substring(0, pos) + newStr2 + code.substring(pos + targetStr2.length);


const targetStr3 = `                      <div style="background:var(--bg-secondary, #f5f5f5); padding:16px; border-radius:12px; margin-bottom:16px; text-align:left;">
                        <div style="font-size:14px; opacity:0.7; margin-bottom:4px;">Current session:</div>
                        <div style="font-weight:bold; font-size:18px;">\${checkData.current_user.name}</div>
                      </div>
                      <div style="background:rgba(0, 123, 255, 0.1); padding:16px; border-radius:12px; margin-bottom:24px; text-align:left;">
                        <div style="font-size:14px; color:#007bff; margin-bottom:4px;">Link belongs to:</div>
                        <div style="font-weight:bold; font-size:18px; color:#007bff;">\${checkData.token_user.name}</div>
                      </div>`;

const newStr3 = `                      <div style="background:var(--bg-secondary, #f5f5f5); padding:16px; border-radius:12px; margin-bottom:16px; text-align:left;">
                        <div style="font-size:14px; opacity:0.7; margin-bottom:4px;">Current session:</div>
                        <div style="font-weight:bold; font-size:18px;">\${checkData.current_user?.name || 'Unknown'}</div>
                      </div>
                      <div style="background:rgba(0, 123, 255, 0.1); padding:16px; border-radius:12px; margin-bottom:24px; text-align:left;">
                        <div style="font-size:14px; color:#007bff; margin-bottom:4px;">Link belongs to:</div>
                        <div style="font-weight:bold; font-size:18px; color:#007bff;">\${checkData.token_user?.name || 'Unknown'}</div>
                      </div>`;

pos = code.indexOf(targetStr3);
if (pos !== -1) {
    code = code.substring(0, pos) + newStr3 + code.substring(pos + targetStr3.length);
}

const targetStr4 = `                    overlay.appendChild(modal);
                    document.body.appendChild(overlay);
                    
                    document.getElementById('cancelTokenSwitch').onclick = () => {
                      document.body.removeChild(overlay);
                      resolve(false);
                    };
                    document.getElementById('confirmTokenSwitch').onclick = () => {
                      document.body.removeChild(overlay);
                      resolve(true);
                    };
                  });
                  }
                } else {
                  console.warn('[auth] Token check returned invalid');
                }
              } else {
                console.error('[auth] Token check HTTP error', checkResp.status);
              }
            } catch(e) {
              console.error('[auth] Failed to check token', e);
            }
          } else {
            shouldConsumeToken = true;
          }`;

const newStr4 = `                    overlay.appendChild(modal);
                    document.body.appendChild(overlay);
                    console.log(\`[auth-debug:\${authDebugId}] Modal DOM element inserted into body\`);
                    
                    document.getElementById('cancelTokenSwitch').onclick = () => {
                      console.log(\`[auth-debug:\${authDebugId}] User clicked Cancel in modal\`);
                      document.body.removeChild(overlay);
                      resolve(false);
                    };
                    document.getElementById('confirmTokenSwitch').onclick = () => {
                      console.log(\`[auth-debug:\${authDebugId}] User clicked Switch in modal\`);
                      document.body.removeChild(overlay);
                      resolve(true);
                    };
                  });
                  console.log(\`[auth-debug:\${authDebugId}] Modal promise resolved, shouldConsumeToken:\`, shouldConsumeToken);
                  }
                } else {
                  console.warn('[auth] Token check returned invalid');
                  console.log(\`[auth-debug:\${authDebugId}] checkData.ok is false, shouldConsumeToken remains false\`);
                }
              } else {
                console.error('[auth] Token check HTTP error', checkResp.status);
              }
            } catch(e) {
              console.error('[auth] Failed to check token', e);
              console.log(\`[auth-debug:\${authDebugId}] Caught exception during token_check, shouldConsumeToken remains false\`);
            }
          } else {
            console.log(\`[auth-debug:\${authDebugId}] Missing cookies (entered logged out branch), setting shouldConsumeToken = true\`);
            shouldConsumeToken = true;
          }`;

pos = code.indexOf(targetStr4);
if (pos === -1) {
  console.log("NOT FOUND 4");
  process.exit(1);
}
code = code.substring(0, pos) + newStr4 + code.substring(pos + targetStr4.length);


const targetStr5 = `          if (shouldConsumeToken) {
            try {
              const _authResp = await fetch('/api/token_auth', {
                method: 'POST',
                headers: withOptionalApiKey({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ token: _urlToken, tz: getUserTimezone() || '' }),
              });
              if (!_authResp.ok) {
                throw new Error('HTTP ' + _authResp.status);
              }
              const _authData = await _authResp.json();
              if (_authData && _authData.ok) {
                const _vid = String(_authData.user_id);
                const _role = String(_authData.role || '');
                setCookie(ROLE_COOKIE_NAME, _role, 365);
                setCookie(AUTH_COOKIE_NAME, 'ok', 365);
                setCookie('visitor_id', _vid, 365);
                window.__visitorId = _vid;
                _setAIAuth(_authData.ai_session || '', _authData.visitor_sig || '');
              }
            } catch (e) {
              console.warn('[auth] Не удалось войти по токену:', e);
            }
          }`;

const newStr5 = `          console.log(\`[auth-debug:\${authDebugId}] Immediately before if (shouldConsumeToken), value is:\`, shouldConsumeToken);

          if (shouldConsumeToken) {
            console.log(\`[auth-debug:\${authDebugId}] Executing /api/token_auth\`);
            try {
              const _authResp = await fetch('/api/token_auth', {
                method: 'POST',
                headers: withOptionalApiKey({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ token: _urlToken, tz: getUserTimezone() || '', debug_id: authDebugId }),
              });
              console.log(\`[auth-debug:\${authDebugId}] token_auth HTTP status:\`, _authResp.status);
              if (!_authResp.ok) {
                throw new Error('HTTP ' + _authResp.status);
              }
              const _authData = await _authResp.json();
              if (_authData && _authData.ok) {
                console.log(\`[auth-debug:\${authDebugId}] token_auth success, setting cookies for user_id:\`, _authData.user_id);
                const _vid = String(_authData.user_id);
                const _role = String(_authData.role || '');
                setCookie(ROLE_COOKIE_NAME, _role, 365);
                setCookie(AUTH_COOKIE_NAME, 'ok', 365);
                setCookie('visitor_id', _vid, 365);
                window.__visitorId = _vid;
                _setAIAuth(_authData.ai_session || '', _authData.visitor_sig || '');
              } else {
                console.log(\`[auth-debug:\${authDebugId}] token_auth returned ok=false\`);
              }
            } catch (e) {
              console.warn('[auth] Не удалось войти по токену:', e);
              console.log(\`[auth-debug:\${authDebugId}] Exception during token_auth\`);
            }
          }`;

pos = code.indexOf(targetStr5);
if (pos === -1) {
  console.log("NOT FOUND 5");
  process.exit(1);
}
code = code.substring(0, pos) + newStr5 + code.substring(pos + targetStr5.length);

fs.writeFileSync('index.html', code);
