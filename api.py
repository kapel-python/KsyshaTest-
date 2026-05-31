import logging
import time
import json
import re
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Tuple, Optional
from zoneinfo import ZoneInfo

load_dotenv()

API_KEY = (os.getenv("GPTUNNEL_API_KEY", "") or "").strip()

API_CHAT_URL = "https://gptunnel.ru/v1/chat/completions"

API_BALANCE_URL = "https://gptunnel.ru/v1/balance"

MODEL = (os.getenv("GPTUNNEL_MODEL", "") or "").strip() or "qwen3-14b"
DATE_PARSER_MODEL = "qwen3-14b"
COMPANION_MODEL = "gpt-5.4-nano"

# ── Компактная сериализация данных для промпта ─────────────────────────────

# Поля которые ИИ реально использует в ответах
_MEMORY_AI_FIELDS  = {"id", "category", "title", "date", "content", "media_type", "created_at"}
_EVENT_AI_FIELDS   = {"id", "title", "description_html", "event_datetime", "event_datetime_human",
                      "is_passed", "media_type"}
_WISH_AI_FIELDS    = {"id", "content_html", "media_type", "created_at"}

def _slim(obj: dict, fields: set) -> dict:
    """Оставляет только нужные поля и убирает None/пустые строки."""
    return {k: v for k, v in obj.items() if k in fields and v is not None and v != ""}

def _slim_list(lst: list, fields: set) -> list:
    return [_slim(item, fields) for item in lst if isinstance(item, dict)]

def _compact_json(obj) -> str:
    """JSON без лишних пробелов."""
    import json as _j
    return _j.dumps(obj, ensure_ascii=False, separators=(",", ":"))



logger = logging.getLogger(__name__)
_COMPANION_GUIDE_CACHE = None

_COMPANION_TOOL_TO_ENDPOINT = {
    "memories": "api/memories",
    "memories_recent": "api/memories_recent",
    "events": "api/events",
    "events_recent": "api/events_recent",
    "wishes": "api/wishes",
    "wishes_recent": "api/wishes_recent",
    "favorites": "api/favorites",
    "user_stats": "api/user_stats",
    "user_settings": "api/user_settings",
    "profile_stats": "api/profile_stats",
    "all": "api/all",
    # legacy aliases
    "api/memories": "api/memories",
    "api/memories_recent": "api/memories_recent",
    "api/events": "api/events",
    "api/events_recent": "api/events_recent",
    "api/wishes": "api/wishes",
    "api/wishes_recent": "api/wishes_recent",
    "api/favorites": "api/favorites",
    "api/user_stats": "api/user_stats",
    "api/user_settings": "api/user_settings",
    "api/profile_stats": "api/profile_stats",
    "api/all": "api/all",
}

_COMPANION_ALLOWED_ENDPOINTS = {
    "api/memories",
    "api/memories_recent",
    "api/events",
    "api/events_recent",
    "api/wishes",
    "api/wishes_recent",
    "api/favorites",
    "api/user_stats",
    "api/user_settings",
    "api/profile_stats",
    "api/all",
}

_COMPANION_GUIDE_MAX_CHARS = 12000
_COMPANION_PROMPT_GUIDE_CHARS_BY_ENDPOINT = {
    "api/all": 6000,
    "api/memories": 5000,
    "api/memories_recent": 4500,
    "api/events": 5000,
    "api/events_recent": 4500,
    "api/wishes": 5000,
    "api/wishes_recent": 4500,
    "api/favorites": 3000,
    "api/user_stats": 3000,
    "api/user_settings": 3000,
    "api/profile_stats": 3000,
}
_COMPANION_PROMPT_LIMITS_BY_ENDPOINT = {
    "api/all": {"memories": 30, "events": 25, "wishes": 25},
    "api/memories": {"memories": 50, "events": 0, "wishes": 0},
    "api/memories_recent": {"memories": 20, "events": 0, "wishes": 0},
    "api/events": {"memories": 0, "events": 50, "wishes": 0},
    "api/events_recent": {"memories": 0, "events": 20, "wishes": 0},
    "api/wishes": {"memories": 0, "events": 0, "wishes": 50},
    "api/wishes_recent": {"memories": 0, "events": 0, "wishes": 20},
    "api/favorites": {"memories": 0, "events": 0, "wishes": 0},
    "api/user_stats": {"memories": 0, "events": 0, "wishes": 0},
    "api/user_settings": {"memories": 0, "events": 0, "wishes": 0},
    "api/profile_stats": {"memories": 0, "events": 0, "wishes": 0},
}


def _companion_valid_endpoint(ep: str) -> bool:
    if ep in _COMPANION_ALLOWED_ENDPOINTS:
        return True
    parts = ep.split("/")
    if len(parts) == 3 and parts[0] == "api" and parts[1] in ("memory", "event"):
        return parts[2].isdigit()
    return False


def _resolve_companion_endpoint(tool: str) -> str | None:
    tool_str = (tool or "").strip().lower()
    endpoint = _COMPANION_TOOL_TO_ENDPOINT.get(tool_str)
    if endpoint:
        return endpoint
    if tool_str.startswith("memory/") and tool_str.split("/")[-1].isdigit():
        return "api/" + tool_str
    if tool_str.startswith("event/") and tool_str.split("/")[-1].isdigit():
        return "api/" + tool_str
    if tool_str.startswith("api/memory/") and tool_str.split("/")[-1].isdigit():
        return tool_str
    if tool_str.startswith("api/event/") and tool_str.split("/")[-1].isdigit():
        return tool_str
    return None


def _load_companion_guide() -> str:
    global _COMPANION_GUIDE_CACHE
    if _COMPANION_GUIDE_CACHE is not None:
        return _COMPANION_GUIDE_CACHE
    guide_paths = [
        Path(__file__).resolve().parent / "COMPANION_GUIDE.txt",
        Path("/app/COMPANION_GUIDE.txt"),
    ]
    for p in guide_paths:
        try:
            if p.exists():
                txt = p.read_text(encoding="utf-8").strip()
                _COMPANION_GUIDE_CACHE = txt[:_COMPANION_GUIDE_MAX_CHARS]
                return _COMPANION_GUIDE_CACHE
        except Exception:
            continue
    _COMPANION_GUIDE_CACHE = ""
    return ""


def _limit_tail(items: list, limit: int) -> list:
    if not isinstance(items, list):
        return []
    if limit <= 0:
        return []
    if len(items) <= limit:
        return list(items)
    return list(items[-limit:])


def _endpoint_prompt_limits(endpoint: str) -> Dict[str, int]:
    return _COMPANION_PROMPT_LIMITS_BY_ENDPOINT.get(
        endpoint or "",
        _COMPANION_PROMPT_LIMITS_BY_ENDPOINT["api/all"],
    )


def _kb_for_prompt(endpoint: str) -> str:
    kb = _load_companion_guide()
    max_chars = _COMPANION_PROMPT_GUIDE_CHARS_BY_ENDPOINT.get(endpoint or "", 4000)
    if max_chars <= 0:
        return ""
    return kb[:max_chars]


def _auth_header_value() -> str:
    """
    GPTunnel принимает ключ в Authorization.
    Если в env уже положили `Bearer ...`, не дублируем префикс.
    """
    key = (API_KEY or "").strip()
    if key.lower().startswith("bearer "):
        return key
    return f"Bearer {key}"

def _send_messages(
    messages: List[Dict[str, str]],
    timeout_seconds: int = 40,
    retries_on_timeout: int = 0,
    model: str | None = None,
) -> str:

    """

    Базовый вызов GPTunnel с подробным логированием и таймаутом.

    """

    headers = {

        "Authorization": _auth_header_value(),

        "Content-Type": "application/json",

    }

    model_to_use = model or MODEL

    data = {

        "model": model_to_use,

        "messages": messages,

        "useWalletBalance": True,

        "thinking": {"type": "disabled"},

        "reasoning_effort": "low",

        "max_reasoning_tokens": 0,

    }

    logger.info(

        "AI request: model=%s, messages=%d", model_to_use, len(messages)

    )
    if not API_KEY:
        logger.error("AI request blocked: empty GPTUNNEL_API_KEY")
        raise RuntimeError("GPTUNNEL_API_KEY is empty")

    attempts = 1 + max(0, int(retries_on_timeout))
    last_timeout_exc = None
    for attempt in range(1, attempts + 1):
        started_at = time.monotonic()
        try:
            response = requests.post(
                API_CHAT_URL,
                json=data,
                headers=headers,
                timeout=timeout_seconds,
            )
            elapsed = time.monotonic() - started_at
            logger.info(
                "AI response received: status=%s, elapsed=%.3fs, attempt=%d/%d",
                response.status_code,
                elapsed,
                attempt,
                attempts,
            )
            response.raise_for_status()
            j = response.json()
            content = j["choices"][0]["message"]["content"]

            # Чтобы в логах не было всего текста, логируем только первые ~120 символов
            preview = (content or "").replace("\n", " ")[:120]
            logger.info("AI response content preview: %s", preview)
            return content
        except requests.exceptions.ReadTimeout as exc:
            elapsed = time.monotonic() - started_at
            logger.warning(
                "AI request timeout after %.3fs (ReadTimeout), attempt=%d/%d",
                elapsed,
                attempt,
                attempts,
            )
            last_timeout_exc = exc
            if attempt < attempts:
                continue
            logger.exception("AI request failed after timeout retries exhausted")
            raise
        except Exception:
            elapsed = time.monotonic() - started_at
            logger.exception("AI request failed after %.3fs", elapsed)
            raise

    if last_timeout_exc is not None:
        raise last_timeout_exc
    raise RuntimeError("AI request failed without explicit exception")

def _send_messages_stream(messages: List[Dict[str, str]], model: str | None = None):

    """

    Стриминговый вызов GPTunnel — возвращает генератор токенов.

    <think>...</think> блоки фильтруются на лету.

    """

    import json as _json

    headers = {

        "Authorization": _auth_header_value(),

        "Content-Type": "application/json",

    }

    model_to_use = model or MODEL

    data = {

        "model": model_to_use,

        "messages": messages,

        "useWalletBalance": True,

        "stream": True,

        "thinking": {"type": "disabled"},

        "reasoning_effort": "low",

        "max_reasoning_tokens": 0,

    }

    started_at = time.monotonic()

    logger.info("AI stream request: model=%s, messages=%d", model_to_use, len(messages))

    response = requests.post(

        API_CHAT_URL,

        json=data,

        headers=headers,

        timeout=60,

        stream=True,

    )

    response.raise_for_status()

    # Буфер для фильтрации <think>...</think> по кускам

    buf = ""

    in_think = False

    def filter_chunk(text: str) -> str:

        nonlocal buf, in_think

        buf += text

        result = ""

        while buf:

            if in_think:

                end = buf.find("</think>")

                if end == -1:

                    buf = buf[-8:] if len(buf) > 8 else buf

                    break

                else:

                    buf = buf[end + 8:]

                    in_think = False

            else:

                start = buf.find("<think>")

                if start == -1:

                    result += buf

                    buf = ""

                    break

                else:

                    result += buf[:start]

                    buf = buf[start + 7:]

                    in_think = True

        return result

    for raw in response.iter_lines():

        if not raw:

            continue

        line = raw.decode("utf-8") if isinstance(raw, bytes) else raw

        if line.startswith("data:"):

            line = line[5:].strip()

        if not line or line == "[DONE]":

            continue

        try:

            obj = _json.loads(line)

            # Поддерживаем оба формата: OpenAI delta и gptunnel delta

            delta = (

                obj.get("delta")

                or (obj.get("choices") or [{}])[0].get("delta", {}).get("content")

                or ""

            )

            finish = obj.get("finishReason") or (obj.get("choices") or [{}])[0].get("finish_reason") or ""

            if delta:

                clean = filter_chunk(delta)

                if clean:

                    yield clean

            if finish and finish not in ("", "null", "None"):

                break

        except Exception:

            continue

    elapsed = time.monotonic() - started_at

    logger.info("AI stream completed in %.3fs", elapsed)

def send_prompt(prompt: str, timeout_seconds: int = 40, model: str | None = None) -> str:

    """Старый интерфейс: один prompt без истории (используется для распознавания дат)."""

    messages = [

        {"role": "system", "content": ""},

        {"role": "user", "content": prompt},

    ]

    model_to_use = model or DATE_PARSER_MODEL

    return _send_messages(messages, timeout_seconds=timeout_seconds, model=model_to_use)

_RU_MONTH_VARIANTS = {
    "января": 1, "январь": 1,
    "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3,
    "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5,
    "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7,
    "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9,
    "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11,
    "декабря": 12, "декабрь": 12,
}

_RU_MONTH_FUZZY = {
    # частые опечатки/разговорные формы
    "янв": 1, "январ": 1, "янввря": 1,
    "фев": 2, "феврал": 2, "февоаля": 2,
    "мар": 3, "мартаа": 3,
    "апр": 4, "апрел": 4, "апреляя": 4,
    "майя": 5,
    "июн": 6, "июнья": 6,
    "июл": 7, "июлья": 7,
    "авг": 8, "авгус": 8,
    "сен": 9, "сент": 9, "сентебря": 9, "синтября": 9,
    "окт": 10, "октя": 10, "октябя": 10, "октяюря": 10,
    "ноя": 11, "нояб": 11, "ноябряя": 11,
    "дек": 12, "декаб": 12, "декаюря": 12,
}


def _resolve_ru_month(token: str) -> Optional[int]:
    t = (token or "").strip().lower().replace("ё", "е")
    if not t:
        return None
    if t in _RU_MONTH_VARIANTS:
        return _RU_MONTH_VARIANTS[t]
    if t in _RU_MONTH_FUZZY:
        return _RU_MONTH_FUZZY[t]
    # Последний шанс: отрезать хвосты и снова проверить
    t = re.sub(r"(е|я|й|ь|а|у|ю|ом|ем)$", "", t)
    if t in _RU_MONTH_FUZZY:
        return _RU_MONTH_FUZZY[t]
    return None


def _extract_today_from_context(datetime_context: str) -> Optional[date]:
    ctx = (datetime_context or "").lower()
    # Пример: "Сейчас у пользователя: 25 мая 2026, ..."
    m = re.search(r"сейчас у пользователя:\s*(\d{1,2})\s+([а-яё]+)\s+(\d{4})", ctx)
    if not m:
        return None
    d = int(m.group(1))
    mo = _resolve_ru_month(m.group(2))
    y = int(m.group(3))
    if not mo:
        return None
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _parse_date_local_ru(user_input: str, today: Optional[date] = None) -> str:
    """
    Локальный разбор частых форматов без ИИ:
    - 30.10.2025
    - 30/10/2025
    - 30-10-2025
    - 30 октября 2025
    Возвращает дату в формате D.M.YYYY или "".
    """
    s = (user_input or "").strip().lower()
    if not s:
        return ""
    today = today or date.today()

    # Вырезаем мусор вокруг даты и лишние суффиксы года.
    s = s.replace("года", "").replace("г.", "").replace("год", "")
    s = re.sub(r"\s+", " ", s).strip()

    # Относительные даты
    if re.search(r"\bсегодня\b", s):
        return f"{today.day}.{today.month}.{today.year}"
    if re.search(r"\bвчера\b", s):
        d = today - timedelta(days=1)
        return f"{d.day}.{d.month}.{d.year}"
    if re.search(r"\bпозавчера\b", s):
        d = today - timedelta(days=2)
        return f"{d.day}.{d.month}.{d.year}"
    if re.search(r"\b(неделю назад|7 дней назад)\b", s):
        d = today - timedelta(days=7)
        return f"{d.day}.{d.month}.{d.year}"
    if re.search(r"\b(месяц назад|30 дней назад)\b", s):
        d = today - timedelta(days=30)
        return f"{d.day}.{d.month}.{d.year}"
    if re.search(r"\bгод назад\b", s):
        try:
            d = date(today.year - 1, today.month, today.day)
        except ValueError:
            # 29 февраля -> 28 февраля невисокосного года
            d = date(today.year - 1, today.month, min(today.day, 28))
        return f"{d.day}.{d.month}.{d.year}"

    # Числовой формат: D.M.YYYY / D-M-YYYY / D/M/YYYY
    m_num = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", s)
    if m_num:
        d, m, y = int(m_num.group(1)), int(m_num.group(2)), int(m_num.group(3))
        if y < 100:
            y += 2000 if y < 50 else 1900
        try:
            parsed = date(y, m, d)
            if parsed > today:
                return ""
            return f"{parsed.day}.{parsed.month}.{parsed.year}"
        except ValueError:
            return ""

    # Числовой формат без года: D.M / D-M / D/M
    m_num_no_year = re.search(r"\b(\d{1,2})[./-](\d{1,2})\b", s)
    if m_num_no_year:
        d, m = int(m_num_no_year.group(1)), int(m_num_no_year.group(2))
        try:
            parsed = date(today.year, m, d)
            if parsed > today:
                parsed = date(today.year - 1, m, d)
            return f"{parsed.day}.{parsed.month}.{parsed.year}"
        except ValueError:
            return ""

    # Текстовый формат: D <месяц> YYYY
    m_txt = re.search(r"\b(\d{1,2})\s+([а-яё]+)\s+(\d{2,4})\b", s)
    if m_txt:
        d = int(m_txt.group(1))
        month_token = m_txt.group(2)
        y = int(m_txt.group(3))
        if y < 100:
            y += 2000 if y < 50 else 1900
        month_num = _resolve_ru_month(month_token)
        if not month_num:
            return ""
        try:
            parsed = date(y, month_num, d)
            if parsed > today:
                return ""
            return f"{parsed.day}.{parsed.month}.{parsed.year}"
        except ValueError:
            return ""

    # Текстовый формат без года: D <месяц>
    m_txt_no_year = re.search(r"\b(\d{1,2})\s+([а-яё]+)\b", s)
    if m_txt_no_year:
        d = int(m_txt_no_year.group(1))
        month_token = m_txt_no_year.group(2)
        month_num = _resolve_ru_month(month_token)
        if not month_num:
            return ""
        try:
            parsed = date(today.year, month_num, d)
            if parsed > today:
                parsed = date(today.year - 1, month_num, d)
            return f"{parsed.day}.{parsed.month}.{parsed.year}"
        except ValueError:
            return ""

    return ""


def _coerce_non_future_ai_date(result: str, today: Optional[date] = None) -> str:
    """Нормализует ответ ИИ и отбрасывает будущие даты."""
    s = (result or "").strip()
    if not s:
        return ""

    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s+(\d{1,2}):(\d{2}))?\s*$", s)
    if not m:
        return ""
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh, mm = m.group(4), m.group(5)
    try:
        parsed = date(y, mo, d)
    except ValueError:
        return ""
    today = today or date.today()
    if parsed > today:
        return ""
    if hh is not None and mm is not None:
        h, mi = int(hh), int(mm)
        if h < 0 or h > 23 or mi < 0 or mi > 59:
            return ""
        return f"{parsed.day}.{parsed.month}.{parsed.year} {h:02d}:{mi:02d}"
    return f"{parsed.day}.{parsed.month}.{parsed.year}"

def parse_date_with_ai(user_input: str, datetime_context: str) -> str:

    """

    Передаёт ИИ текст пользователя о дате и контекст (дата, время, часовой пояс).

    Возвращает дату в формате D.M.YYYY или D.M.YYYY HH:MM (если пользователь указал время).

    """

    prompt = f"""{datetime_context}

Пользователь написал о дате (и возможно времени): "{user_input}"

Определи точную дату и время, если пользователь его указал. Учти контекст выше для относительных выражений ("вчера", "сегодня", "30 октября", "30.10", "неделю назад", "в 15:00" и т.п.).

Правила:
1) Никогда не возвращай будущую дату/время.
2) Если год не указан, выбери наиболее вероятный прошлый вариант (обычно текущий год, но если такая дата в будущем — предыдущий год).
3) Если дата неоднозначна, выбери наиболее вероятную прошлую дату, а не пустой ответ.
4) Если пользователь указал время, верни его.

Ответь ТОЛЬКО датой в формате Д.М.ГГГГ (например: 25.1.2026) или датой и временем Д.М.ГГГГ ЧЧ:ММ (например: 25.1.2026 14:30). Без текста, без объяснений. Только дата (и время при необходимости)."""

    context_today = _extract_today_from_context(datetime_context)

    # Быстрый локальный разбор — чтобы не зависеть от внешнего API для базовых дат.
    parsed_local = _parse_date_local_ru(user_input, today=context_today)
    if parsed_local:
        return parsed_local

    try:

        # Для распознавания дат важнее быстрый ответ, чем долгий подвисший запрос.
        result = send_prompt(prompt, timeout_seconds=25, model=DATE_PARSER_MODEL)

        result = (result or "").strip()

        if result:
            normalized = _coerce_non_future_ai_date(result, today=context_today)
            if normalized:
                return normalized
            logger.warning("parse_date_with_ai: AI produced invalid/future date for input=%r result=%r", user_input, result)
        logger.warning("parse_date_with_ai: empty AI response for input=%r", user_input)

    except Exception as e:
        logger.exception("parse_date_with_ai: AI parse failed for input=%r: %s", user_input, e)

    return ""

def parse_timezone_with_ai_details(user_input: str) -> Dict[str, str]:
    """
    Возвращает словарь с распознанным часовым поясом:
    {
      "timezone": "Area/City",
      "display_name": "Удобная подпись для пользователя"
    }
    При неуспехе возвращает {}.
    """
    import json as _json
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    prompt = (
        f'Пользователь написал о своём часовом поясе или городе: "{user_input}"\n\n'
        "Твоя задача: максимально точно определить IANA-часовой пояс.\n"
        "Правила:\n"
        "1) Всегда сначала пытайся распознать конкретный город/регион по смыслу, языку, опечаткам, транслиту.\n"
        "2) Возвращай только валидный IANA timezone (например Europe/Moscow, Asia/Tokyo, America/New_York).\n"
        "3) Если пользователь дал только UTC/GMT смещение (например UTC+3), верни подходящий IANA:\n"
        "   - для целого часа можно использовать Etc/GMT±N,\n"
        "   - для дробного смещения подбери реальный регион (например Asia/Kolkata для UTC+5:30).\n"
        "4) Если есть несколько вариантов, выбери самый вероятный и распространённый.\n"
        "5) null допускается только когда вообще нет содержательного сигнала.\n"
        "6) Убедись, что зона реально существует в базе IANA. Например, для Санкт-Петербурга, Казани, Нижнего Новгорода нет отдельных зон — используй Europe/Moscow.\n\n"
        'Ответь строго JSON-объектом вида: {"timezone":"Area/City","display_name":"..."}.\n'
        "display_name должен быть коротким и понятным человеку (город/регион + при желании страна).\n"
        'Если определить совсем нельзя, ответь: {"timezone":null,"display_name":null}.\n'
        "Только JSON, без пояснений и markdown."
    )
    try:
        result = send_prompt(prompt, model=DATE_PARSER_MODEL)
        result = (result or "").strip()
        if result.startswith("```"):
            lines = result.splitlines()
            result = "\n".join(l for l in lines if not l.startswith("```")).strip()
        data = _json.loads(result)
        tz = data.get("timezone")
        display_name = data.get("display_name")
        if not tz or not isinstance(tz, str):
            return {}
        tz = tz.strip()
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, Exception):
            return {}
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = tz
        return {"timezone": tz, "display_name": display_name.strip()}
    except Exception as e:
        logger.warning("parse_timezone_with_ai_details: parse failed for input=%r: %s", user_input, e)
        return {}


def parse_timezone_with_ai(user_input: str) -> str:
    """
    Определяет IANA-идентификатор часового пояса по свободному тексту пользователя
    (например, «Москва», «Vladivostok», «New York», «минск»).

    Возвращает строку вида "Europe/Moscow" или "" если распознать не удалось.
    Результат всегда проверяется через zoneinfo.ZoneInfo — если ТЗ невалидна, вернёт "".
    """
    details = parse_timezone_with_ai_details(user_input)
    return details.get("timezone", "")


def get_balance() -> str:

    headers = {

        "Authorization": _auth_header_value(),

    }

    params = {

        "useWalletBalance": "true"

    }

    response = requests.get(API_BALANCE_URL, headers=headers, params=params, timeout=10)

    response.raise_for_status()

    data = response.json()

    balance_raw = data.get("balance", "0")

    try:

        return f"{float(balance_raw):.2f}"

    except Exception as e:
        logger.warning("get_balance: failed to parse balance=%r: %s", balance_raw, e)
        return "неизвестно"

from datetime import date as _date_type


def _fmt_date_met_lines(date_met_str: str | None, now) -> str:
    """Возвращает две строки промпта о дате знакомства.
    date_met_str — ISO строка 'YYYY-MM-DD' или None."""
    if not date_met_str:
        return "- Дата знакомства: не указана\n- Дней вместе на сегодня: неизвестно\n\n"
    try:
        dm = _date_type.fromisoformat(date_met_str)
        formatted = dm.strftime("%d.%m.%Y")
        days = (now.date() - dm).days
        return f"- Дата знакомства: {formatted}\n- Дней вместе на сегодня: {days}\n\n"
    except Exception as e:
        logger.debug("Не удалось форматировать date_met=%r: %s", date_met_str, e)
        return "- Дата знакомства: не указана\n- Дней вместе на сегодня: неизвестно\n\n"


def build_companion_system_prompt(

    all_data: Dict[str, Any],

    extra: Dict[str, Any] | None = None,

    endpoint: str | None = None,

) -> str:

    """

    Формирует system-prompt для ИИ-компаньона на основе данных /api/all и дополнительного контекста.

    """

    extra = extra or {}

    stats = all_data.get("stats") or {}

    # В зависимости от выбранного "эндпоинта" подаём в prompt только нужные данные.
    # ВНУТРИ КАЖДОЙ категории (memories, events, wishes, user_stats, favorites) отдаются ВСЕ доступные поля,
    # ничего дополнительно не обрезается и не придумывается.
    endpoint = (endpoint or "").strip().lower() or "api/all"

    raw_memories = all_data.get("memories") or []

    raw_events = all_data.get("events") or []

    wishes_block = (all_data.get("wishes") or {})
    raw_wishes = wishes_block.get("partner") or wishes_block.get("user") or []

    raw_favorites = (all_data.get("favorites") or {})

    raw_user_settings = (all_data.get("user_settings") or {})
    raw_users = (all_data.get("users") or {})
    raw_profile_stats = (all_data.get("profile_stats") or {})

    # Сколько элементов считать «недавно добавленными» для *_recent эндпоинтов.
    RECENT_LIMIT = 20

    # Параметры инструмента, которые ИИ мог указать на шаге router (фильтры, id и т.д.).
    tool_params = extra.get("tool_params") or {}

    # Сначала фильтруем по типу данных: каждый логический эндпоинт отдаёт ТОЛЬКО свою категорию.
    if endpoint == "api/memories":

        memories = raw_memories

        events = []

        wishes = []

    elif endpoint == "api/memories_recent":

        # Только недавно добавленные воспоминания (по факту — последние элементы списка).
        memories = list(raw_memories[-RECENT_LIMIT:])

        events = []

        wishes = []

    elif endpoint == "api/events":

        memories = []

        events = raw_events

        wishes = []

    elif endpoint == "api/events_recent":

        # Только недавно добавленные события (последние элементы списка).
        memories = []

        events = list(raw_events[-RECENT_LIMIT:])

        wishes = []

    elif endpoint == "api/wishes":

        memories = []

        events = []

        wishes = raw_wishes

    elif endpoint == "api/wishes_recent":

        # Только недавно добавленные желания Ксюши (последние элементы списка).
        memories = []

        events = []

        wishes = list(raw_wishes[-RECENT_LIMIT:])

    elif endpoint.startswith("api/memory/"):
        # Конкретное воспоминание по id
        try:
            target_id = int(endpoint.split("/")[-1])
            memories = [m for m in raw_memories if isinstance(m, dict) and m.get("id") == target_id]
        except (ValueError, TypeError):
            memories = []
        events = []
        wishes = []

    elif endpoint.startswith("api/event/"):
        # Конкретное событие по id
        try:
            target_id = int(endpoint.split("/")[-1])
            events = [e for e in raw_events if isinstance(e, dict) and e.get("id") == target_id]
        except (ValueError, TypeError):
            events = []
        memories = []
        wishes = []

    elif endpoint == "api/favorites":

        # Для избранного отдельная категория, сами списки сущностей здесь не нужны.
        memories = []

        events = []

        wishes = []

    elif endpoint == "api/user_stats":

        # Только статистика по пользователю, сами сущности не передаются.
        memories = []

        events = []

        wishes = []

    elif endpoint == "api/user_settings":

        # Только настройки текущего пользователя; сами сущности не передаются.
        memories = []

        events = []

        wishes = []

    elif endpoint == "api/profile_stats":

        memories = []

        events = []

        wishes = []

    else:

        # api/all или неизвестное — подаём все категории как есть.
        memories = raw_memories

        events = raw_events

        wishes = raw_wishes

    prompt_limits = _endpoint_prompt_limits(endpoint)
    memories = _limit_tail(memories, int(prompt_limits.get("memories", 0)))
    events = _limit_tail(events, int(prompt_limits.get("events", 0)))
    wishes = _limit_tail(wishes, int(prompt_limits.get("wishes", 0)))

    # Время и дата всегда считаются по ЧАСОВОМУ ПОЯСУ ПОЛЬЗОВАТЕЛЯ, а не сервера.
    timezone_id = extra.get("timezone_id") or "неизвестен"

    try:

        if timezone_id != "неизвестен":

            now = datetime.now(ZoneInfo(timezone_id))

        else:

            now = datetime.now()

    except Exception:

        # Фоллбэк, если передан некорректный идентификатор таймзоны.
        now = datetime.now()

    now_date = now.strftime("%d.%m.%Y")
    now_time = now.strftime("%H:%M")
    now_iso = now.isoformat(timespec="seconds")
    weekday = now.strftime("%A")

    site_role = (extra.get("site_role") or "").strip().lower()

    # Счётчики и статистика по пользователю. Статистика не должна содержать
    # данные об устройстве (ip, user_agent, browser, device и т.п.).
    total_memories = stats.get("total_memories", len(raw_memories))

    total_events = stats.get("total_events", len(raw_events))

    total_wishes = stats.get("total_wishes", len(raw_wishes))

    def _filter_stats_no_device(raw: Dict[str, Any]) -> Dict[str, Any]:

        if not isinstance(raw, dict):

            return {}

        blocked = ("device", "browser", "user_agent", "useragent", "ip", "platform")

        return {k: v for k, v in raw.items() if all(b not in k.lower() for b in blocked)}

    user_stats_creator = _filter_stats_no_device(stats.get("creator") or {})

    user_stats_ksyusha = _filter_stats_no_device(stats.get("partner") or stats.get("ksyusha") or {})

    favorites_creator = raw_favorites.get("creator") or {}

    favorites_ksyusha = raw_favorites.get("partner") or raw_favorites.get("ksyusha") or {}

    def _safe_user_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        blocked = (
            "ip",
            "token",
            "auth",
            "cookie",
            "session",
            "secret",
            "password",
            "user_agent",
            "browser",
            "device",
            "fingerprint",
        )
        return {k: v for k, v in raw.items() if not any(b in str(k).lower() for b in blocked)}

    user_settings_creator = _safe_user_settings(raw_user_settings.get("creator") or {})

    user_settings_ksyusha = _safe_user_settings(raw_user_settings.get("partner") or raw_user_settings.get("ksyusha") or {})
    users_creator = raw_users.get("creator") or {}
    users_ksyusha = raw_users.get("partner") or raw_users.get("ksyusha") or {}

    # Настройки текущего пользователя (по роли), могут содержать:
    # ip, часовой пояс (автоопределённый сайтом), часовой пояс, выбранный в боте,
    # включены ли уведомления, избранное и другие настройки.
    if site_role == "creator":

        current_user_settings = user_settings_creator

    elif site_role in ("ksyusha", "partner"):

        current_user_settings = user_settings_ksyusha

    else:

        current_user_settings = {}

    # Извлекаем данные для приоритетов имен
    if site_role == "creator":
        current_user_display = users_creator.get("display") or ""
        current_user_first_name = users_creator.get("first_name") or ""
        current_user_username = users_creator.get("username") or ""
        
        partner_role = "ksyusha"
        partner_user_display = users_ksyusha.get("display") or ""
        partner_user_first_name = users_ksyusha.get("first_name") or ""
        partner_user_username = users_ksyusha.get("username") or ""
    else:
        current_user_display = users_ksyusha.get("display") or ""
        current_user_first_name = users_ksyusha.get("first_name") or ""
        current_user_username = users_ksyusha.get("username") or ""
        
        partner_role = "creator"
        partner_user_display = users_creator.get("display") or ""
        partner_user_first_name = users_creator.get("first_name") or ""
        partner_user_username = users_creator.get("username") or ""

    from companion_personality import COMPANION_CORE_IDENTITY, COMPANION_SUGGESTION_RULES

    return (

        f"{COMPANION_CORE_IDENTITY}\n\n"
        
        "ИМЕНА ПОЛЬЗОВАТЕЛЕЙ И ПРИОРИТЕТЫ:\n"
        "Когда обращаешься к пользователю по имени или отвечаешь на вопрос 'как меня зовут', ты ДОЛЖНА строго соблюдать следующий приоритет имен:\n"
        "1. Имя, которое пользователь сам явно назвал тебе в текущей беседе (например, если он написал 'Меня зовут Артём', запомни это имя и используй его).\n"
        f"2. Имя/Никнейм из базы данных текущего пользователя: '{current_user_display}'\n"
        f"3. Имя Telegram (first_name) текущего пользователя: '{current_user_first_name}'\n"
        f"4. Username Telegram текущего пользователя: '@{current_user_username}' (это крайний случай, НИКОГДА не используй его, если есть имя в беседе, никнейм или first_name!)\n\n"
        
        "ДЕТАЛИ СОБЕСЕДНИКОВ:\n"
        f"- Текущий пользователь (ты общаешься с ним): Роль={site_role}, Никнейм в БД='{current_user_display}', Telegram first_name='{current_user_first_name}', Telegram username='@{current_user_username}'\n"
        f"- Его партнёр: Роль={partner_role}, Никнейм в БД='{partner_user_display}', Telegram first_name='{partner_user_first_name}', Telegram username='@{partner_user_username}'\n\n"

        f"Текущее время пользователя: {now_date} {now_time} ({weekday}), timezone={timezone_id}, iso={now_iso}.\n\n"

        f"ВАЖНЫЕ ДАТЫ ПАРЫ:\n"
        f"{_fmt_date_met_lines(all_data.get('date_met'), now)}"

        "ДАННЫЕ ПАРЫ (все поля внутри категорий полные, ничего не выдумывается моделью):\n"

        f"- Всего воспоминаний: {total_memories}\n"

        f"- Всего событий: {total_events}\n"

        f"- Всего желаний партнёра: {total_wishes}\n"

        f"- Статистика создателя (без данных об устройстве): {user_stats_creator}\n"

        f"- Статистика партнёра (без данных об устройстве): {user_stats_ksyusha}\n"

        f"- Избранное создателя: {favorites_creator}\n"

        f"- Избранное партнёра: {favorites_ksyusha}\n"

        f"- Настройки создателя: {user_settings_creator}\n"

        f"- Настройки партнёра: {user_settings_ksyusha}\n"

        f"- Настройки текущего пользователя (по роли): {current_user_settings}\n"
        f"- Никнейм создателя: {users_creator.get('display') or ''}\n"
        f"- Имя Telegram создателя: {users_creator.get('first_name') or ''}\n"
        f"- Username создателя в боте: @{(users_creator.get('username') or '')}\n"
        f"- Никнейм партнёра: {users_ksyusha.get('display') or ''}\n"
        f"- Имя Telegram партнёра: {users_ksyusha.get('first_name') or ''}\n"
        f"- Username партнёра в боте: @{(users_ksyusha.get('username') or '')}\n"
        f"- Базовая статистика текущего пользователя: {raw_profile_stats}\n"
        f"- Текущая роль пользователя: {(site_role or 'unknown')}\n\n"

        f"- memories: {_compact_json(_slim_list(memories, _MEMORY_AI_FIELDS))}\n"

        f"- events: {_compact_json(_slim_list(events, _EVENT_AI_FIELDS))}\n"

        f"- wishes: {_compact_json(_slim_list(wishes, _WISH_AI_FIELDS))}\n\n"

        f"Параметры инструмента (если были заданы на шаге router): {tool_params}\n\n"

        "НИКОГДА не придумывай не существующие в данных воспоминания, события, желания, статистику или любые другие факты. "

        "Отвечай только на основе реально переданных выше структур.\n\n"

        "ВАЖНО: в самом конце каждого ответа добавь блок подсказок СТРОГО в следующем XML-формате (напиши теги <suggestions> и </suggestions> на английском без перевода, внутри укажи 3 подсказки, каждая с новой строки):\n"

        "<suggestions>\nПодсказка 1\nПодсказка 2\nПодсказка 3\n</suggestions>\n"

        f"{COMPANION_SUGGESTION_RULES}\n\n"
        f"ВНУТРЕННЯЯ ИНСТРУКЦИЯ ПРОЕКТА (KB):\n{_kb_for_prompt(endpoint)}\n"

    )

def build_companion_router_prompt(extra: Dict[str, Any] | None = None) -> str:

    """

    System‑prompt для первого шага: ИИ решает, нужен ли доступ к данным,

    и если да, то к какому «эндпоинту».

    """

    extra = extra or {}

    timezone_id = extra.get("timezone_id") or "неизвестен"

    try:

        if timezone_id != "неизвестен":

            now = datetime.now(ZoneInfo(timezone_id))

        else:

            now = datetime.now()

    except Exception:

        now = datetime.now()

    now_date = now.strftime("%d.%m.%Y")

    now_time = now.strftime("%H:%M")

    now_iso = now.isoformat(timespec="seconds")

    from companion_personality import COMPANION_CORE_IDENTITY, COMPANION_SUGGESTION_RULES

    return (
        f"{COMPANION_CORE_IDENTITY}\n\n"
        "Ты также выступаешь как маршрутизатор запросов для ИИ-компаньона влюблённой пары.\n"
        "Твоя задача — по сообщению пользователя понять, нужны ли тебе данные с их сайта "
        "(воспоминания, события, желания, избранное и т.п.) или можно ответить без обращения к данным (tool=null).\n\n"
        f"Сейчас {now_date} {now_time}, часовой пояс пользователя: {timezone_id}, iso={now_iso}.\n\n"
        "Если вопрос про факты пары (дата знакомства, сколько дней вместе, ближайшее событие, желания, воспоминания, "
        "кто/когда/сколько/какое число) — ВСЕГДА выбирай tool с данными, а не tool=null.\n\n"
        "У тебя есть следующие внутренние API (это НЕ HTTP-запросы пользователя, а внутренние источники данных сервера):\n"
        "- memories         — все воспоминания пары\n"
        "- memories_recent  — только недавно добавленные воспоминания\n"
        "- memory/{id}      — ОДНО конкретное воспоминание по его id (используй если пользователь спрашивает про конкретный момент и ты знаешь его id из истории диалога)\n"
        "- events           — все запланированные события и важные даты\n"
        "- events_recent    — только недавно добавленные события\n"
        "- event/{id}       — ОДНО конкретное событие по его id\n"
        "- wishes           — все желания партнёра (полные объекты, с автором, названием, текстом и т.д.)\n"
        "- wishes_recent    — только недавно добавленные желания партнёра (последние элементы списка)\n"
        "- favorites        — избранные моменты/события/желания (отдельно для создателя и партнёра)\n"
        "- user_stats       — статистика текущего пользователя по его роли (без данных об устройстве)\n"
        "- user_settings    — настройки текущего пользователя (ip, часовые пояса, уведомления, избранное и прочее)\n"
        "- profile_stats    — базовая статистика профиля как на странице /stats (серия, награды, любимое время и т.п.)\n"
        "- all              — все данные сразу (воспоминания, события, желания, избранное, настройки, статистика и т.д.)\n\n"
        "НИ ПРИ КАКИХ ОБСТОЯТЕЛЬСТВАХ НЕ ПРИДУМЫВАЙ СВОИ СОБСТВЕННЫЕ НАЗВАНИЯ TOOLS ИЛИ ЭНДПОИНТОВ. "
        "МОЖНО ИСПОЛЬЗОВАТЬ ТОЛЬКО перечисленные выше варианты.\n\n"
        "Ты всегда отвечаешь СТРОГО ОДНОЙ СТРОКОЙ В ВИДЕ ВАЛИДНОГО JSON-БЛОКА, БЕЗ ПРЕДИСЛОВИЙ И КОММЕНТАРИЕВ.\n\n"
        f"{COMPANION_SUGGESTION_RULES}\n\n"
        "Форматы ответа:\n\n"
        "1) Если данные с сайта НЕ нужны (можно ответить сразу):\n"
        "{\n"
        '  \"tool\": null,\n'
        '  \"answer\": \"<короткий ответ пользователю на русском>\",\n'
        '  \"suggestions\": [\"подсказка 1\", \"подсказка 2\", \"подсказка 3\"],\n'
        '  \"params\": null\n'
        "}\n\n"
        "2) Если данные с сайта НУЖНЫ, но ты сам ответ пока не пишешь (только выбираешь источник данных и ПАРАМЕТРЫ для него):\n"
        "{\n"
        '  \"tool\": \"memories\" | \"memories_recent\" | \"events\" | \"events_recent\" | \"wishes\" | \"wishes_recent\" | \"favorites\" | \"user_stats\" | \"user_settings\" | \"profile_stats\" | \"all\",\n'
        '  \"answer\": null,\n'
        '  \"suggestions\": [],\n'
        '  \"params\": {\n'
        '    // необязательный объект с параметрами, как в боте;\n'
        '    // используй ТОЛЬКО простые ключи и значения (id, дата, тип события и т.п.)\n'
        "  }\n"
        "}\n\n"
        "Примеры params (НЕ выдумывай другие структуры, только подобные этим):\n"
        "- для memories:        {\"memory_id\": \"...\"} или {\"date_from\": \"2025-10-01\", \"date_to\": \"2025-10-31\"}\n"
        "- для memories_recent: {\"limit\": 10}\n"
        "- для events:          {\"only_future\": true} или {\"event_type\": \"anniversary\"}\n"
        "- для events_recent:   {\"limit\": 10}\n"
        "- для wishes:          {\"wish_id\": \"...\"} или {\"resolved\": false}\n"
        "- для wishes_recent:   {\"limit\": 10}\n"
        "- для user_stats:      {\"scope\": \"current_user\"}\n"
        "- для user_settings:   {\"scope\": \"current_user\"}\n\n"
        "- для profile_stats:   {\"scope\": \"current_user\"}\n\n"
        "Сайт может использовать params, чтобы подставить нужные данные перед вторым шагом. "
        "ТЫ НЕ ДОЛЖЕН придумывать параметры или поля, которых нет в этих примерах по смыслу.\n\n"
        "Никогда не пиши ничего, кроме JSON. Никакого текста до или после JSON, никаких комментариев.\n"
    )

def _extract_suggestions(text: str) -> Tuple[str, List[str]]:
    """
    Извлекает подсказки из текста ответа.
    Поддерживает XML-теги <suggestions>...</suggestions> и старый bracket-синтаксис [SUGGESTIONS:...].
    Возвращает очищенный текст и список подсказок (максимум 3).
    """
    import re
    suggestions = []
    
    # 1. Попытка извлечения через XML-теги <suggestions>...</suggestions>
    tag_idx = text.lower().find("<suggestions>")
    if tag_idx != -1:
        part = text[tag_idx + len("<suggestions>"):]
        clean_text = text[:tag_idx].strip()
        
        # Убираем закрывающий тег, если есть
        part_clean = re.sub(r"</?suggestions>", "", part, flags=re.IGNORECASE).strip()
        if part_clean:
            for line in re.split(r"[\n|]", part_clean):
                line = line.strip()
                # Убираем маркеры списков
                line = re.sub(r"^[-\*\d\.\s]+", "", line).strip()
                if line:
                    suggestions.append(line)
        return clean_text, suggestions[:3]
        
    # 2. Фолбек на старый синтаксис [SUGGESTIONS:...] с поддержкой кириллической С (U+0421) в СUGGESTIONS
    m = re.search(r"\[(?:SUGGESTIONS|СУПЕР|ПОДСКАЗКИ|ПРЕДЛОЖЕНИЯ|SUGGEST|СUGGESTIONS):\s*(.+?)\]", text, flags=re.IGNORECASE | re.DOTALL)
    if m:
        part = m.group(1)
        for s in part.split("|"):
            s = s.strip()
            if s:
                suggestions.append(s)
        clean_text = re.sub(r"\[(?:SUGGESTIONS|СУПЕР|ПОДСКАЗКИ|ПРЕДЛОЖЕНИЯ|SUGGEST|СUGGESTIONS):.*?\]", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        return clean_text, suggestions[:3]
        
    return text, []

def ask_companion(

    user_message: str,

    history: List[Dict[str, str]],

    all_data: Dict[str, Any],

    extra: Dict[str, Any] | None = None,

) -> Tuple[str, List[str]]:

    """

    Новый двухшаговый режим работы ИИ-компаньона.

    1) Первый запрос (router): ИИ решает, нужен ли доступ к данным (api/memories / api/events / api/wishes / api/all)

       или можно ответить без данных (формат \"None. ответ\").

    2) Если нужен один из api/..., сервер подставляет реальные данные и делает второй запрос к ИИ,

       который уже формулирует финальный ответ пользователю.

    history — список сообщений {'role': 'user'|'assistant', 'content': '...'} с фронта или из БД.

    Возвращает (ответ, список подсказок).

    """

    import re

    extra = extra or {}

    # Чтобы не раздувать контекст, берём только последние N сообщений истории.

    # Этого достаточно, чтобы ИИ видел текущий диалог.

    MAX_HISTORY_ITEMS = 10

    if history:

        history = list(history[-MAX_HISTORY_ITEMS:])

    user_text = (user_message or "").strip()

    pre_routing = extra.get("routing") if isinstance(extra, dict) else None
    if isinstance(pre_routing, dict):
        routing = pre_routing
        logger.info("AI-companion: using pre-routed decision")
    else:
        routing = route_companion_request(user_text, history, extra)
    suggestions: List[str] = routing.get("suggestions") or []
    endpoint: str | None = routing.get("endpoint")
    if not routing.get("needs_data"):
        return (routing.get("reply") or "Не смог ответить, попробуй ещё раз"), suggestions
    if _companion_valid_endpoint(endpoint or ""):

        logger.info("AI-companion router selected endpoint: %s", endpoint)

        # Если на шаге router были заданы параметры инструмента — передаём их дальше во второй шаг.
        tool_params = routing.get("tool_params") or {}
        if isinstance(tool_params, dict):
            extra = dict(extra)
            extra["tool_params"] = tool_params

        system_prompt = build_companion_system_prompt(all_data, extra, endpoint=endpoint)

        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

        for item in history or []:

            role = item.get("role")

            content = (item.get("content") or "").strip()

            if role in ("user", "assistant") and content:

                messages.append({"role": role, "content": content})

        if user_text:

            messages.append({"role": "user", "content": user_text})

        logger.info(

            "AI-companion data-step: sending second-step request with endpoint=%s", endpoint

        )

        raw_reply = _send_messages(messages, timeout_seconds=40, retries_on_timeout=1, model=COMPANION_MODEL)

        reply = (raw_reply or "").strip() or "Не смог ответить, попробуй ещё раз"

        # Подсказки во втором шаге имеют приоритет над подсказками от router-а

        reply, second_suggestions = _extract_suggestions(reply)
        if second_suggestions:
            suggestions = second_suggestions

        logger.info("AI-companion data-step completed successfully")

        return reply, suggestions

    # Фоллбэк: неожиданный формат — ведём себя как раньше, считаем, что raw_router это уже готовый ответ.

    raw_router = routing.get("raw_router") or ""
    logger.warning(
        "AI-companion router returned unexpected format, using it as direct answer: %s",
        raw_router,
    )
    reply = raw_router or "Не смог ответить, попробуй ещё раз"

    return reply, suggestions


def route_companion_request(
    user_message: str,
    history: List[Dict[str, str]],
    extra: Dict[str, Any] | None = None,
    router_timeout_seconds: int = 40,
) -> Dict[str, Any]:
    import re
    extra = extra or {}
    user_text = (user_message or "").strip()

    # Жёсткий фоллбэк для фактических вопросов: сначала данные, потом ответ.
    low = user_text.lower()
    if low and re.search(
        r"(когда.*познаком|когда.*познак|дата знакомств|дата.*знакомств|мы познаком|мы познак|"
        r"сколько.*дней вместе|годовщина|"
        r"какое.*сегодня|какой.*сегодня|текущее.*время|сейчас.*время|сколько.*времени|"
        r"мой.*статист|моя.*статист|награ|огон[её]к|серия.*дней)",
        low,
    ):
        return {
            "needs_data": True,
            "reply": "",
            "suggestions": [],
            "endpoint": "api/profile_stats" if re.search(r"(статист|награ|огон[её]к|серия.*дней)", low) else "api/all",
            "tool_params": {},
            "raw_router": "forced:fact_query",
        }

    router_prompt = build_companion_router_prompt(extra)
    router_messages: List[Dict[str, str]] = [{"role": "system", "content": router_prompt}]
    for item in history or []:
        role = item.get("role")
        content = (item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            router_messages.append({"role": role, "content": content})
    if user_text:
        router_messages.append({"role": "user", "content": user_text})
    logger.info("AI-companion router: sending first-step request")
    raw_router = (
        _send_messages(
            router_messages,
            timeout_seconds=router_timeout_seconds,
            retries_on_timeout=1,
            model=COMPANION_MODEL,
        )
        or ""
    ).strip()
    logger.info("AI-companion router raw reply: %s", raw_router.replace("\n", " ")[:200])

    suggestions: List[str] = []
    router_obj = None
    if raw_router.startswith("{") and raw_router.endswith("}"):
        try:
            router_obj = json.loads(raw_router)
        except Exception:
            router_obj = None
    if isinstance(router_obj, dict) and "tool" in router_obj:
        tool = router_obj.get("tool")
        tool_params = router_obj.get("params") if isinstance(router_obj.get("params"), dict) else {}
        raw_sug = router_obj.get("suggestions") or []
        if isinstance(raw_sug, list):
            suggestions = [str(s).strip() for s in raw_sug if str(s).strip()][:3]
        if tool is None:
            return {
                "needs_data": False,
                "reply": (router_obj.get("answer") or "").strip() or "Не смог ответить, попробуй ещё раз",
                "suggestions": suggestions,
                "endpoint": None,
                "tool_params": {},
                "raw_router": raw_router,
            }
        endpoint = _resolve_companion_endpoint(str(tool))
        if endpoint:
            return {
                "needs_data": True,
                "reply": "",
                "suggestions": suggestions,
                "endpoint": endpoint,
                "tool_params": tool_params,
                "raw_router": raw_router,
            }
        logger.warning("AI-companion router JSON returned unknown tool=%r", tool)

    raw_router, router_suggestions = _extract_suggestions(raw_router)
    if router_suggestions:
        suggestions = router_suggestions
    lower_router = raw_router.lower()
    if lower_router.startswith("none."):
        return {
            "needs_data": False,
            "reply": raw_router[5:].strip() or "Не смог ответить, попробуй ещё раз",
            "suggestions": suggestions,
            "endpoint": None,
            "tool_params": {},
            "raw_router": raw_router,
        }
    endpoint = (raw_router.split()[0] if raw_router else "").strip().lower()
    if _companion_valid_endpoint(endpoint):
        return {
            "needs_data": True,
            "reply": "",
            "suggestions": suggestions,
            "endpoint": endpoint,
            "tool_params": {},
            "raw_router": raw_router,
        }
    return {
        "needs_data": False,
        "reply": raw_router or "Не смог ответить, попробуй ещё раз",
        "suggestions": suggestions,
        "endpoint": None,
        "tool_params": {},
        "raw_router": raw_router,
    }

def ask_companion_stream(

    user_message: str,

    history: List[Dict[str, str]],

    all_data: Dict[str, Any],

    extra: Dict[str, Any] | None = None,

):

    """

    Стриминговая версия ask_companion.

    Шаг 1: роутер выбирает эндпоинт (включая memory/{id} и event/{id}).
    Шаг 2: стримим ответ только с нужными данными.

    Возвращает генератор токенов. Последний элемент — JSON с подсказками:

      {"__suggestions__": ["...", "...", "..."]}

    """

    import re as _re, json as _json

    extra = extra or {}

    MAX_HISTORY_ITEMS = 10

    if history:

        history = list(history[-MAX_HISTORY_ITEMS:])

    user_text = (user_message or "").strip()

    # --- Шаг 1: роутер ---
    endpoint = "api/all"
    tool_params_stream: dict = {}
    pre_routing = extra.get("routing") if isinstance(extra, dict) else None
    if isinstance(pre_routing, dict):
        if not pre_routing.get("needs_data"):
            reply_text = (pre_routing.get("reply") or "Не смог ответить, попробуй ещё раз").strip()
            sug_direct = pre_routing.get("suggestions") or []
            if not isinstance(sug_direct, list):
                sug_direct = []
            logger.info("AI-companion-stream: using pre-routed direct answer")
            yield reply_text
            yield _json.dumps({"__suggestions__": [str(s).strip() for s in sug_direct if str(s).strip()][:3]})
            return
        endpoint = (pre_routing.get("endpoint") or endpoint).strip().lower()
        tool_params_stream = pre_routing.get("tool_params") if isinstance(pre_routing.get("tool_params"), dict) else {}
    else:

        try:
            router_prompt = build_companion_router_prompt(extra)
            router_msgs: List[Dict[str, str]] = [{"role": "system", "content": router_prompt}]
            for item in history or []:
                r = item.get("role"); c = (item.get("content") or "").strip()
                if r in ("user", "assistant") and c:
                    router_msgs.append({"role": r, "content": c})
            if user_text:
                router_msgs.append({"role": "user", "content": user_text})

            raw_router = (
                _send_messages(router_msgs, timeout_seconds=40, retries_on_timeout=1, model=COMPANION_MODEL) or ""
            ).strip()
            router_obj = None
            if raw_router.startswith("{") and raw_router.endswith("}"):
                try:
                    router_obj = _json.loads(raw_router)
                except Exception:
                    pass

            if isinstance(router_obj, dict) and "tool" in router_obj:
                tool = router_obj.get("tool")
                tool_params_stream = router_obj.get("params") if isinstance(router_obj.get("params"), dict) else {}

                if tool is None:
                    # Данные не нужны — отдаём готовый ответ без стриминга
                    reply_text = (router_obj.get("answer") or "Не смог ответить, попробуй ещё раз").strip()
                    raw_sug = router_obj.get("suggestions") or []
                    sug_direct = [str(s).strip() for s in raw_sug if str(s).strip()][:3] if isinstance(raw_sug, list) else []
                    logger.info("AI-companion-stream: router no data needed, direct answer")
                    yield reply_text
                    yield _json.dumps({"__suggestions__": sug_direct})
                    return

                _tool_map = {
                    "memories": "api/memories", "memories_recent": "api/memories_recent",
                    "events": "api/events", "events_recent": "api/events_recent",
                    "wishes": "api/wishes", "wishes_recent": "api/wishes_recent",
                    "favorites": "api/favorites", "user_stats": "api/user_stats",
                    "user_settings": "api/user_settings", "profile_stats": "api/profile_stats", "all": "api/all",
                }
                ts = str(tool).strip().lower()
                ep = _tool_map.get(ts)
                if not ep:
                    if ts.startswith("memory/") and ts.split("/")[-1].isdigit():
                        ep = "api/" + ts
                    elif ts.startswith("event/") and ts.split("/")[-1].isdigit():
                        ep = "api/" + ts
                    elif ts.startswith("api/memory/") and ts.split("/")[-1].isdigit():
                        ep = ts
                    elif ts.startswith("api/event/") and ts.split("/")[-1].isdigit():
                        ep = ts
                if ep:
                    endpoint = ep
                    logger.info("AI-companion-stream: router selected endpoint=%s", endpoint)
        except Exception as _re_err:
            logger.warning("AI-companion-stream: router failed (%s), fallback to api/all", _re_err)

    if tool_params_stream:
        extra = dict(extra)
        extra["tool_params"] = tool_params_stream

    logger.info("AI-companion-stream: streaming with %s", endpoint)

    system_prompt = build_companion_system_prompt(all_data, extra, endpoint=endpoint)

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]

    for item in history or []:

        role = item.get("role")

        content = (item.get("content") or "").strip()

        if role in ("user", "assistant") and content:

            messages.append({"role": role, "content": content})

    if user_text:

        messages.append({"role": "user", "content": user_text})

    suggestions: List[str] = []
    full_reply = ""
    in_suggestions = False
    suggestions_buf = ""
    tail_buf = ""

    for chunk in _send_messages_stream(messages, model=COMPANION_MODEL):
        full_reply += chunk

        if in_suggestions:
            suggestions_buf += chunk
            continue

        tail_buf += chunk

        # 1. Проверяем XML-тег <suggestions>
        tag_idx = tail_buf.lower().find("<suggestions>")
        if tag_idx != -1:
            clean = tail_buf[:tag_idx]
            if clean:
                yield clean
            in_suggestions = True
            suggestions_buf = tail_buf[tag_idx + len("<suggestions>"):]
            tail_buf = ""
            continue

        # 2. Проверяем bracket-теги [SUGGESTIONS: или [СУПЕР: и т.д.
        m_bracket = _re.search(r"\[(?:SUGGESTIONS|СУПЕР|ПОДСКАЗКИ|ПРЕДЛОЖЕНИЯ|SUGGEST|СUGGESTIONS):.*?\]", tail_buf, flags=_re.IGNORECASE | _re.DOTALL)
        if m_bracket:
            clean = tail_buf[:m_bracket.start()]
            if clean:
                yield clean
            in_suggestions = True
            suggestions_buf = tail_buf[m_bracket.start() + len(m_bracket.group(0)):]
            tail_buf = ""
            continue

        # 3. Буферизируем потенциальные префиксы тегов во избежание утечки в поток
        suf_len = 0
        
        # Проверяем потенциальный префикс XML-тега
        last_xml_bracket = tail_buf.rfind("<")
        if last_xml_bracket != -1:
            suffix = tail_buf[last_xml_bracket:]
            if "<suggestions>".startswith(suffix.lower()):
                suf_len = len(suffix)

        # Проверяем потенциальный префикс bracket-тега
        if suf_len == 0:
            last_bracket = tail_buf[-20:].rfind("[")
            if last_bracket != -1:
                idx = len(tail_buf) - 20 + last_bracket
                suffix = tail_buf[idx:]
                if _re.match(r"^\[[A-Za-zА-ЯЁа-яё\s:]*$", suffix):
                    suf_len = len(suffix)

        if suf_len > 0:
            clean = tail_buf[:-suf_len]
            if clean:
                yield clean
            tail_buf = tail_buf[-suf_len:]
        else:
            yield tail_buf
            tail_buf = ""

    if tail_buf:
        tag_idx = tail_buf.lower().find("<suggestions>")
        if tag_idx != -1:
            clean = tail_buf[:tag_idx]
            if clean:
                yield clean
            suggestions_buf += tail_buf[tag_idx + len("<suggestions>"):]
        else:
            clean_tail = _re.sub(r"\[(?:SUGGESTIONS|СУПЕР|ПОДСКАЗКИ|ПРЕДЛОЖЕНИЯ|SUGGEST|СUGGESTIONS):.*?\]", "", tail_buf, flags=_re.IGNORECASE | _re.DOTALL)
            if clean_tail:
                yield clean_tail

    # Извлекаем подсказки из полного ответа централизованно и детерминированно
    _, suggestions = _extract_suggestions(full_reply)
    yield _json.dumps({"__suggestions__": suggestions})

    logger.info("AI-companion-stream: done")

def main():

    while True:

        prompt = input("Запрос: ")

        if not prompt.strip():

            break

        try:

            reply = send_prompt(prompt)

            print("Ответ:", reply)

            balance = get_balance()

            print("Остаток на счёте:", balance, "₽")

        except Exception as e:

            print("Ошибка:", e)

if __name__ == "__main__":

    main()
