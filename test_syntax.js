async function test() {
        const _urlParams = new URLSearchParams(window.location.search);
        const _urlToken = _urlParams.get('token');
        if (_urlToken) {
          const existingRoleBeforeToken = getCurrentRole();
          const existingVidBeforeToken = getCookie('visitor_id');
          let shouldConsumeToken = false;

          if (existingRoleBeforeToken && existingVidBeforeToken) {
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
                    shouldConsumeToken = await new Promise((resolve) => {
                    const overlay = document.createElement('div');
                    overlay.style.position = 'fixed';
                    overlay.style.top = '0'; overlay.style.left = '0';
                    overlay.style.width = '100vw'; overlay.style.height = '100vh';
                    overlay.style.backgroundColor = 'rgba(0,0,0,0.85)';
                    overlay.style.display = 'flex';
                    overlay.style.alignItems = 'center'; overlay.style.justifyContent = 'center';
                    overlay.style.zIndex = '999999';
                    overlay.style.fontFamily = 'sans-serif';
                    
                    const modal = document.createElement('div');
                    modal.style.backgroundColor = 'var(--bg-main, #ffffff)';
                    modal.style.color = 'var(--text-primary, #000000)';
                    modal.style.padding = '32px';
                    modal.style.borderRadius = '16px';
                    modal.style.maxWidth = '400px';
                    modal.style.textAlign = 'center';
                    modal.style.boxShadow = '0 10px 30px rgba(0,0,0,0.5)';
                    
                    modal.innerHTML = `
                      <h2 style="margin-top:0; font-size:22px; margin-bottom: 24px;">Account Switch</h2>
                      <div style="background:var(--bg-secondary, #f5f5f5); padding:16px; border-radius:12px; margin-bottom:16px; text-align:left;">
                        <div style="font-size:14px; opacity:0.7; margin-bottom:4px;">Current session:</div>
                        <div style="font-weight:bold; font-size:18px;">${checkData.current_user.name}</div>
                      </div>
                      <div style="background:rgba(0, 123, 255, 0.1); padding:16px; border-radius:12px; margin-bottom:24px; text-align:left;">
                        <div style="font-size:14px; color:#007bff; margin-bottom:4px;">Link belongs to:</div>
                        <div style="font-weight:bold; font-size:18px; color:#007bff;">${checkData.token_user.name}</div>
                      </div>
                      <p style="margin-bottom:24px; opacity:0.8; font-size:15px;">Continuing will replace your current session.</p>
                      <div style="display:flex; gap:12px; justify-content:center;">
                        <button id="cancelTokenSwitch" style="flex:1; padding:12px 20px; border-radius:10px; border:1px solid var(--border-color, #ccc); background:transparent; color:inherit; font-weight:bold; cursor:pointer; font-size:15px; transition:0.2s;">Cancel</button>
                        <button id="confirmTokenSwitch" style="flex:1; padding:12px 20px; border-radius:10px; border:none; background:#007bff; color:#fff; font-weight:bold; cursor:pointer; font-size:15px; transition:0.2s;">Switch</button>
                      </div>
                    `;
                    
                    overlay.appendChild(modal);
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
          }
        }
}
