import sys

with open('http_api.py', 'r', encoding='utf-8') as f:
    code = f.read()

target_1 = """    token = _pstr(payload.get("token")).strip()
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    logger.info(f"[auth-debug] token_check called, current_visitor_id: {payload.get('current_visitor_id')}")

    token_data = db.peek_user_login_token(token)
    if not token_data:
        logger.info(f"[auth-debug] token_check invalid/used token")"""

new_1 = """    token = _pstr(payload.get("token")).strip()
    debug_id = _pstr(payload.get("debug_id")).strip() or "none"
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    logger.info(f"[auth-debug:{debug_id}] token_check called, current_visitor_id: {payload.get('current_visitor_id')}")

    token_data = db.peek_user_login_token(token)
    if not token_data:
        logger.info(f"[auth-debug:{debug_id}] token_check invalid/used token")"""

if target_1 not in code:
    print("FAILED 1")
    sys.exit(1)
code = code.replace(target_1, new_1, 1)

target_2 = """    token = _pstr(payload.get("token")).strip()
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    consumed_by = f"ip={request.remote or ''};ua={(request.headers.get('User-Agent') or '')[:120]}"
    token_data = db.consume_user_login_token(token, consumed_by=consumed_by)
    if not token_data:
        logger.info(f"[auth-debug] token_auth invalid/used token")
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid"}))
    
    logger.info(f"[auth-debug] token_auth executed and consumed token for user_id={token_data['user_id']}")"""

new_2 = """    token = _pstr(payload.get("token")).strip()
    debug_id = _pstr(payload.get("debug_id")).strip() or "none"
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    consumed_by = f"ip={request.remote or ''};ua={(request.headers.get('User-Agent') or '')[:120]}"
    token_data = db.consume_user_login_token(token, consumed_by=consumed_by)
    if not token_data:
        logger.info(f"[auth-debug:{debug_id}] token_auth invalid/used token")
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid"}))
    
    logger.info(f"[auth-debug:{debug_id}] token_auth executed and consumed token for user_id={token_data['user_id']}")"""

if target_2 not in code:
    print("FAILED 2")
    sys.exit(1)
code = code.replace(target_2, new_2, 1)

with open('http_api.py', 'w', encoding='utf-8') as f:
    f.write(code)

print("SUCCESS")
