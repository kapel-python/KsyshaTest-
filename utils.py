import calendar
import html as html_module
import os
import uuid
import re
import math
from datetime import datetime, date, timezone, timedelta
from typing import Optional, Dict, Any, List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.enums import MessageEntityType
from aiogram.utils.text_decorations import HtmlDecoration
import logging

from config import config
from constants import TITLE_PREVIEW_LENGTH
from database import Memory, Wish, ScheduledEvent

logger_utils = logging.getLogger(__name__)


async def safe_delete_message(message) -> None:
    """Безопасно удаляет сообщение. Игнорирует любые ошибки."""
    if message is None:
        return
    try:
        await message.delete()
    except Exception as e:
        logger_utils.debug("Не удалось удалить сообщение: %s", e)


def format_error_message_for_user(exception: BaseException, max_length: int = 400) -> str:
    """Формирует безопасный для показа пользователю текст ошибки (HTML-экранирование, обрезка)."""
    from constants import MSG_ERROR_TEMPLATE
    detail = str(exception).strip() or "неизвестная"
    detail = html_module.escape(detail)[:max_length]
    return MSG_ERROR_TEMPLATE.format(detail=detail)


class HtmlDecorationWithLinks(HtmlDecoration):
    """Как HtmlDecoration, но URL и MENTION превращаются в кликабельные ссылки."""

    def apply_entity(self, entity, text: str) -> str:
        if entity.type == MessageEntityType.URL:
            safe = html_module.escape(text, quote=True)
            return f'<a href="{safe}">{safe}</a>'
        if entity.type == MessageEntityType.MENTION:
            safe_text = html_module.escape(text, quote=True)
            link = f"https://t.me/{text.lstrip('@')}"
            safe_link = html_module.escape(link, quote=True)
            return f'<a href="{safe_link}">{safe_text}</a>'
        return super().apply_entity(entity, text)


_html_decoration = HtmlDecorationWithLinks()

logger = logging.getLogger(__name__)

def preserve_formatting(text: str) -> str:
    """
    Сохраняет форматирование текста, но экранирует опасные символы
    Поддерживает: жирный, курсив, подчёркнутый, зачёркнутый, моно, ссылки, цитаты
    """
    if not text:
        return ""
    
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    
    text = text.replace('&lt;b&gt;', '<b>').replace('&lt;/b&gt;', '</b>')
    text = text.replace('&lt;i&gt;', '<i>').replace('&lt;/i&gt;', '</i>')
    text = text.replace('&lt;u&gt;', '<u>').replace('&lt;/u&gt;', '</u>')
    text = text.replace('&lt;s&gt;', '<s>').replace('&lt;/s&gt;', '</s>')
    text = text.replace('&lt;code&gt;', '<code>').replace('&lt;/code&gt;', '</code>')
    text = text.replace('&lt;pre&gt;', '<pre>').replace('&lt;/pre&gt;', '</pre>')
    text = text.replace('&lt;a href=&quot;', '<a href="').replace('&quot;&gt;', '">')
    
    text = re.sub(
        r"&lt;a href='([^']+)'&gt;",
        r'<a href="\1">',
        text
    )
    text = text.replace('&lt;/a&gt;', '</a>')
    
    # Цитаты и скрытый текст (спойлеры)
    text = text.replace('&lt;blockquote&gt;', '<blockquote>').replace('&lt;/blockquote&gt;', '</blockquote>')
    text = text.replace('&lt;tg-spoiler&gt;', '<tg-spoiler>').replace('&lt;/tg-spoiler&gt;', '</tg-spoiler>')
    
    return text


# Теги, которые Telegram допускает в parse_mode=HTML
_ALLOWED_HTML_TAG_PATTERN = re.compile(
    r'<a\s+href="[^"]*">'                 # <a href="...">
    r'|</a>'                              # </a>
    r'|</?(?:b|i|u|s|code|pre)>'          # <b>, </b>, <i>, </i>, <u>, <s>, <code>, <pre>
    r'|</?tg-spoiler>'                    # <tg-spoiler>, </tg-spoiler> — спойлер
    r'|<blockquote(?:\s+[^>]*)?>'         # <blockquote> или <blockquote expandable>
    r'|</blockquote>',                    # </blockquote>
    re.IGNORECASE
)


def sanitize_html_for_telegram(text: str) -> str:
    """
    Делает HTML-текст безопасным для отправки в Telegram (parse_mode=HTML).
    - Экранирует любые < и >, которые не входят в разрешённые теги.
    - В конце добавляет недостающие закрывающие теги (если есть незакрытый <b> и т.д.).
    Использовать для content из БД перед подстановкой в сообщение.
    """
    if not text or not text.strip():
        return text
    placeholders: List[str] = []

    def replace_tag(match: re.Match) -> str:
        placeholders.append(match.group(0))
        return f"\x00T{len(placeholders) - 1}\x00"

    s = _ALLOWED_HTML_TAG_PATTERN.sub(replace_tag, text)
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    for i, ph in enumerate(placeholders):
        s = s.replace(f"\x00T{i}\x00", ph)

    # Добавляем недостающие закрывающие теги (только парные b, i, u, s, code, pre, blockquote, tg-spoiler)
    stack: List[str] = []
    for m in re.finditer(r"</?(b|i|u|s|code|pre|blockquote|tg-spoiler)\b[^>]*>", s, re.IGNORECASE):
        tag = m.group(1).lower()
        is_close = m.group(0).startswith("</")
        if is_close:
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(tag)
    for tag in reversed(stack):
        s += f"</{tag}>"
    return s


def text_and_entities_to_html(text: str, entities: Optional[List] = None) -> str:
    """
    Конвертирует текст и entities Telegram в HTML (для сохранения и отображения с parse_mode=HTML).
    Поддерживает жирный, курсив, URL и @mention как ссылки и т.д.
    """
    if not text and not (entities or []):
        return ""
    return _html_decoration.unparse(text or "", sorted(entities or [], key=lambda e: getattr(e, "offset", 0)))


TIMEZONE_OFFSETS = {
    "Europe/Moscow":       3,
    "Asia/Bishkek":        6,
    "Europe/Berlin":       1,
    "Europe/London":       0,
    "America/New_York":   -5,
    "America/Los_Angeles":-8,
    "Asia/Dubai":          4,
    "Asia/Tokyo":          9,
    "Asia/Almaty":         5,
}


def _tz_offset(tz_id: Optional[str], default: float = 0) -> float:
    """Возвращает текущее UTC-смещение (в часах) для любого IANA-часового пояса.
    Использует zoneinfo (Python 3.9+) с учётом DST (летнего времени).
    При неизвестном поясе — статический словарь или default.
    """
    if not tz_id:
        return default
    try:
        from zoneinfo import ZoneInfo
        now_utc = datetime.now(timezone.utc)
        offset = now_utc.astimezone(ZoneInfo(tz_id)).utcoffset()
        return offset.total_seconds() / 3600 if offset is not None else default
    except Exception:
        return float(TIMEZONE_OFFSETS.get(tz_id, default))

MONTH_RU = {
    "January": "января", "February": "февраля", "March": "марта",
    "April": "апреля", "May": "мая", "June": "июня",
    "July": "июля", "August": "августа", "September": "сентября",
    "October": "октября", "November": "ноября", "December": "декабря"
}

MONTH_NOMINATIVE_RU = {
    "January": "январь", "February": "февраль", "March": "март",
    "April": "апрель", "May": "май", "June": "июнь",
    "July": "июль", "August": "август", "September": "сентябрь",
    "October": "октябрь", "November": "ноябрь", "December": "декабрь"
}


def _format_datetime_parts(dt: datetime) -> str:
    """Форматирует datetime в «13 февраля 2026 в 12:14»."""
    date_part = dt.strftime("%d %B %Y")
    for eng, rus in MONTH_RU.items():
        date_part = date_part.replace(eng, rus)
    time_part = dt.strftime("%H:%M")
    return f"{date_part} в {time_part}"


def _format_date_russian(d: date) -> str:
    """Форматирует date в «14 февраля 2025»."""
    date_part = d.strftime("%d %B %Y")
    for eng, rus in MONTH_RU.items():
        date_part = date_part.replace(eng, rus)
    return date_part


MEMORY_PARAMS = {
    "days_together": "дней вместе (с даты знакомства)",
    "time_together": "сколько мы вместе в формате ... мес. .. д, например 3мес 15д",
    "days_until_anniversary": "дней до годовщины",
    "anniversary_date": "дата годовщины",
    "day_of_week": "день недели (понедельник, вторник...)",
    "season": "время года (хз по приколу сделал)",
    "time_greeting": "приветствие по времени суток (Доброе утро/день/вечер/ночь)",
    "month_name": "название текущего месяца(тоже а почему бы и нет)",
    "day_of_month": "число месяца (1-31)",
}

DAY_OF_WEEK_RU = {
    0: "понедельник", 1: "вторник", 2: "среда", 3: "четверг",
    4: "пятница", 5: "суббота", 6: "воскресенье",
}

SEASON_RU = {(12, 1, 2): "зима", (3, 4, 5): "весна", (6, 7, 8): "лето", (9, 10, 11): "осень"}


def get_user_datetime_context(user_id: Optional[int] = None) -> str:
    """
    Возвращает строку с текущей датой и временем пользователя (с учётом часового пояса)
    для передачи ИИ при распознавании даты.
    """
    utc_now = datetime.now(timezone.utc)
    if user_id:
        try:
            from database import db
            tz_id = db.get_user_setting(user_id, "timezone")
            offset_hours = _tz_offset(tz_id, 0)
        except Exception:
            offset_hours = 0
    else:
        offset_hours = 0
    user_now = utc_now + timedelta(hours=offset_hours)
    date_part = user_now.strftime("%d %B %Y")
    for eng, rus in MONTH_RU.items():
        date_part = date_part.replace(eng, rus)
    month_name = user_now.strftime("%B")
    for eng, rus in MONTH_RU.items():
        month_name = month_name.replace(eng, rus)
    dow = DAY_OF_WEEK_RU.get(user_now.weekday(), "")
    time_part = user_now.strftime("%H:%M")
    hour, minute = user_now.hour, user_now.minute
    return (
        f"Сейчас у пользователя: {date_part}, {dow}, {time_part} "
        f"(час {hour}, минута {minute}). Месяц: {month_name}, год: {user_now.year}."
    )


def _get_user_today(user_id: Optional[int] = None) -> date:
    """Возвращает «сегодня» в часовом поясе пользователя."""
    utc_now = datetime.now(timezone.utc)
    if user_id:
        try:
            from database import db
            tz_id = db.get_user_setting(user_id, "timezone")
            offset_hours = _tz_offset(tz_id, 0)
        except Exception:
            offset_hours = 0
    else:
        offset_hours = 0
    user_now = utc_now + timedelta(hours=offset_hours)
    return user_now.date()


def _date_diff_calendar(start: date, end: date) -> tuple:
    """Разница между датами в полных годах, месяцах и днях (календарно точно)."""
    if end < start:
        return (0, 0, 0)
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    from_date = date(start.year + years, start.month, start.day)
    if from_date >= end:
        return (years, 0, 0)
    months = 0
    d = from_date
    while d < end:
        if d.month == 12:
            next_d = date(d.year + 1, 1, min(d.day, 31))
        else:
            _, last = calendar.monthrange(d.year, d.month + 1)
            next_d = date(d.year, d.month + 1, min(d.day, last))
        if next_d > end:
            break
        months += 1
        d = next_d
    days = (end - d).days
    return (years, months, days)


def substitute_params(text: str, user_id: Optional[int] = None) -> str:
    """
    Подставляет параметры {...} в текст воспоминания.
    user_id — для учёта часового пояса при расчёте «сегодня».
    """
    if not text or "{" not in text:
        return text
    from database import db as _db

    date_met = None
    if user_id:
        try:
            date_met = _db.get_couple_met_date(user_id)
        except Exception:
            pass

    today = _get_user_today(user_id)

    try:
        from database import db
        lang = db.get_user_setting(user_id, "lang") or "ru" if user_id else "ru"
    except Exception:
        lang = "ru"

    date_met_params = {
        "days_together": "",
        "time_together": "",
        "days_until_anniversary": "",
        "anniversary_date": "",
        "relationship_age": "",
    }
    if date_met:
        try:
            delta = today - date_met
            delta_days = max(0, delta.days)
            next_anniv = date(today.year, date_met.month, date_met.day)
            if next_anniv < today:
                next_anniv = date(today.year + 1, date_met.month, date_met.day)
            days_until_anniv = (next_anniv - today).days

            if lang == "en":
                months_en = {
                    "January": "January", "February": "February", "March": "March", "April": "April",
                    "May": "May", "June": "June", "July": "July", "August": "August",
                    "September": "September", "October": "October", "November": "November", "December": "December"
                }
                m_en = months_en.get(date_met.strftime('%B'), date_met.strftime('%B'))
                anniv_str = f"{m_en} {date_met.day}, {next_anniv.year}"
            elif lang == "de":
                months_de = {
                    "January": "Januar", "February": "Februar", "March": "März", "April": "April",
                    "May": "Mai", "June": "Juni", "July": "Juli", "August": "August",
                    "September": "September", "October": "Oktober", "November": "November", "December": "Dezember"
                }
                m_de = months_de.get(date_met.strftime('%B'), date_met.strftime('%B'))
                anniv_str = f"{date_met.day}. {m_de} {next_anniv.year}"
            else:
                anniv_str = f"{date_met.day} {MONTH_RU.get(date_met.strftime('%B'), date_met.strftime('%B').lower())} {next_anniv.year}"

            years, months, days = _date_diff_calendar(date_met, today)
            if years > 0:
                time_together_str = f"{years}г. {months} мес {days}д"
            elif months > 0:
                time_together_str = f"{months} мес {days}д"
            else:
                time_together_str = f"{days}д"

            def _format_relationship_age(y: int, m: int, d: int, l: str) -> str:
                if l == "ru":
                    def plural_days(n):
                        if n % 10 == 1 and n % 100 != 11:
                            return f"{n} день"
                        elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                            return f"{n} дня"
                        else:
                            return f"{n} дней"
                    def plural_months(n):
                        if n % 10 == 1 and n % 100 != 11:
                            return f"{n} месяц"
                        elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                            return f"{n} месяца"
                        else:
                            return f"{n} месяцев"
                    def plural_years(n):
                        if n % 10 == 1 and n % 100 != 11:
                            return f"{n} год"
                        elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                            return f"{n} года"
                        else:
                            return f"{n} лет"
                elif l == "de":
                    def plural_days(n):
                        return f"{n} Tag" if n == 1 else f"{n} Tage"
                    def plural_months(n):
                        return f"{n} Monat" if n == 1 else f"{n} Monate"
                    def plural_years(n):
                        return f"{n} Jahr" if n == 1 else f"{n} Jahre"
                else: # en
                    def plural_days(n):
                        return f"{n} day" if n == 1 else f"{n} days"
                    def plural_months(n):
                        return f"{n} month" if n == 1 else f"{n} months"
                    def plural_years(n):
                        return f"{n} year" if n == 1 else f"{n} years"

                p_list = []
                if y > 0:
                    p_list.append(plural_years(y))
                if m > 0:
                    p_list.append(plural_months(m))
                if y == 0 and m == 0:
                    p_list.append(plural_days(d if d > 0 else 0))
                return " ".join(p_list)

            relationship_age_str = _format_relationship_age(years, months, days, lang)

            date_met_params = {
                "days_together": str(delta_days),
                "time_together": time_together_str,
                "days_until_anniversary": str(days_until_anniv),
                "anniversary_date": anniv_str,
                "relationship_age": relationship_age_str,
            }
        except Exception as e:
            logger_utils.warning("Error computing date_met params: %s", e)

    dow = today.weekday()
    DAY_OF_WEEK_EN = {
        0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
        4: "Friday", 5: "Saturday", 6: "Sunday"
    }
    DAY_OF_WEEK_DE = {
        0: "Montag", 1: "Dienstag", 2: "Mittwoch", 3: "Donnerstag",
        4: "Freitag", 5: "Samstag", 6: "Sonntag"
    }
    if lang == "en":
        day_of_week_str = DAY_OF_WEEK_EN.get(dow, "day")
    elif lang == "de":
        day_of_week_str = DAY_OF_WEEK_DE.get(dow, "Tag")
    else:
        day_of_week_str = DAY_OF_WEEK_RU.get(dow, "день")

    SEASON_EN = {(12, 1, 2): "Winter", (3, 4, 5): "Spring", (6, 7, 8): "Summer", (9, 10, 11): "Autumn"}
    SEASON_DE = {(12, 1, 2): "Winter", (3, 4, 5): "Frühling", (6, 7, 8): "Sommer", (9, 10, 11): "Herbst"}
    if lang == "en":
        season_str = "Winter"
        for months_tuple, s in SEASON_EN.items():
            if today.month in months_tuple:
                season_str = s
                break
    elif lang == "de":
        season_str = "Winter"
        for months_tuple, s in SEASON_DE.items():
            if today.month in months_tuple:
                season_str = s
                break
    else:
        season_str = "зима"
        for months_tuple, s in SEASON_RU.items():
            if today.month in months_tuple:
                season_str = s
                break

    if user_id:
        try:
            from database import db
            tz_id = db.get_user_setting(user_id, "timezone")
            offset_hours = _tz_offset(tz_id, 0)
        except Exception:
            offset_hours = 0
    else:
        offset_hours = 0
    user_hour = (datetime.now(timezone.utc) + timedelta(hours=offset_hours)).hour

    if lang == "en":
        if 5 <= user_hour < 12:
            time_greeting = "Good morning ❤️"
        elif 12 <= user_hour < 17:
            time_greeting = "Good afternoon ❤️"
        elif 17 <= user_hour < 23:
            time_greeting = "Good evening ❤️"
        else:
            time_greeting = "Sweet dreams ❤️"
    elif lang == "de":
        if 5 <= user_hour < 12:
            time_greeting = "Guten Morgen ❤️"
        elif 12 <= user_hour < 17:
            time_greeting = "Guten Tag ❤️"
        elif 17 <= user_hour < 23:
            time_greeting = "Guten Abend ❤️"
        else:
            time_greeting = "Süße Träume ❤️"
    else:
        if 5 <= user_hour < 12:
            time_greeting = "Доброе утречко ❤️"
        elif 12 <= user_hour < 17:
            time_greeting = "Добрый денечек)"
        elif 17 <= user_hour < 23:
            time_greeting = "Добрый вечер ❤️"
        else:
            time_greeting = "Сладких снов ❤️"

    if lang == "en":
        months_en_nom = {
            "January": "January", "February": "February", "March": "March", "April": "April",
            "May": "May", "June": "June", "July": "July", "August": "August",
            "September": "September", "October": "October", "November": "November", "December": "December"
        }
        month_name_str = months_en_nom.get(today.strftime("%B"), today.strftime("%B"))
    elif lang == "de":
        months_de_nom = {
            "January": "Januar", "February": "Februar", "March": "März", "April": "April",
            "May": "Mai", "June": "Juni", "July": "Juli", "August": "August",
            "September": "September", "October": "Oktober", "November": "November", "December": "Dezember"
        }
        month_name_str = months_de_nom.get(today.strftime("%B"), today.strftime("%B"))
    else:
        month_name_str = MONTH_NOMINATIVE_RU.get(today.strftime("%B"), today.strftime("%B").lower())

    if lang == "en":
        today_date_str = today.strftime("%B %d, %Y")
    elif lang == "de":
        months_de = {
            "January": "Januar", "February": "Februar", "March": "März", "April": "April",
            "May": "Mai", "June": "Juni", "July": "Juli", "August": "August",
            "September": "September", "October": "Oktober", "November": "November", "December": "Dezember"
        }
        month_de = months_de.get(today.strftime("%B"), today.strftime("%B"))
        today_date_str = f"{today.day}. {month_de} {today.year}"
    else:
        month_ru_gen = MONTH_RU.get(today.strftime("%B"), today.strftime("%B").lower())
        today_date_str = f"{today.day} {month_ru_gen} {today.year}"

    params = {
        "day_of_week": day_of_week_str,
        "season": season_str,
        "time_greeting": time_greeting,
        "month_name": month_name_str,
        "day_of_month": str(today.day),
        "today_date": today_date_str,
    }
    params.update(date_met_params)

    result = text
    for key, val in params.items():
        result = result.replace(f"{{{key}}}", val)
    return result


def get_params_help_text() -> str:
    """Краткая подсказка по параметрам для добавления воспоминания."""
    lines = ["Можно использовать параметры, они работают так что когда ты их пишешь то бот сам подставляет данные"]
    for param, desc in MEMORY_PARAMS.items():
        lines.append(f"• <code>{{{param}}}</code> — {desc}")
    return "\n".join(lines)


def format_datetime_russian(dt_str: str) -> str:
    """Форматирует дату-время из БД (YYYY-MM-DD HH:MM:SS) в «13 февраля 2026 в 12:14» (без учёта пояса)."""
    try:
        if len(dt_str) >= 19 and dt_str[10] == ' ':
            dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
        else:
            dt = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        date_part = dt.strftime("%d %B %Y")
        for eng, rus in MONTH_RU.items():
            date_part = date_part.replace(eng, rus)
        if len(dt_str) >= 19:
            time_part = dt.strftime("%H:%M")
            return f"{date_part} в {time_part}"
        return date_part
    except Exception:
        return dt_str[:10] if len(dt_str) >= 10 else dt_str


def format_datetime_for_user(dt_str: str, timezone_id: Optional[str]) -> str:
    """
    Форматирует дату-время из БД с учётом часового пояса пользователя.
    В БД предполагается UTC; Москва +3 ч, Бишкек +6 ч — прибавляются к времени для отображения.
    Поддерживает форматы: "YYYY-MM-DD HH:MM:SS", "YYYY-MM-DDTHH:MM:SS" (ISO), "YYYY-MM-DD".
    """
    if not dt_str or not dt_str.strip():
        return ""
    try:
        dt_str = dt_str.strip()
        dt_utc = None
        use_tz_offset = True
        if len(dt_str) >= 19 and dt_str[10] == ' ':
            dt_utc = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        elif len(dt_str) >= 19 and dt_str[10] == 'T':
            # ISO-формат: раньше сохраняли локальное время сервера — показываем как есть, без сдвига
            s = dt_str[:19].replace("T", " ")
            dt_utc = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            use_tz_offset = False
        else:
            # Если передана только дата (YYYY-MM-DD) или неполный формат — не применяем смещение часовых поясов,
            # чтобы не сдвинуть день (например, 2026-05-30 не должно стать 2026-05-29 20:00)
            d = datetime.strptime(dt_str[:10], "%Y-%m-%d").date()
            return _format_date_russian(d)
        
        offset_hours = _tz_offset(timezone_id, 0) if use_tz_offset else 0
        dt_local = dt_utc + timedelta(hours=offset_hours)
        return _format_datetime_parts(dt_local)
    except Exception:
        return format_datetime_russian(dt_str)


def format_memory_text(
    memory: Memory,
    user_id: Optional[int] = None,
    remaining_views: Optional[int] = None,
) -> str:
    """Форматирует текст воспоминания для отображения.
    user_id — для учёта часового пояса в дате добавления.
    remaining_views — оставшееся количество просмотров для текущего пользователя (если включено ограничение).
    """
    from database import db
    title = substitute_params((memory.title or "").strip(), user_id)
    mem_date = substitute_params((memory.date or "").strip(), user_id)
    content = substitute_params((memory.content or "").strip(), user_id)
    content = sanitize_html_for_telegram(content)
    text = title + "\n\n"
    text += "<i>Момент был " + mem_date + "</i>\n\n"
    text += "💬 Описание: " + content
    
    tz_id = db.get_user_setting(user_id, "timezone") if user_id else None
    formatted_datetime = format_datetime_for_user(memory.created_at or "", tz_id)
    
    display_name = db.get_display_name(memory.user_id) or memory.first_name or (f"@{memory.username}" if memory.username else f"ID {memory.user_id}")
    
    if memory.username:
        profile_url = f"https://t.me/{memory.username}"
    else:
        profile_url = f"tg://user?id={memory.user_id}"
    
    text += (
        f"\n\n👤 Добавил(а): "
        f"<a href=\"{profile_url}\">{preserve_formatting(display_name)}</a>"
    )
    text += f"\n📅 Добавлено: {formatted_datetime}"
    if memory.updated_at and (memory.updated_at or "") != (memory.created_at or ""):
        formatted_updated = format_datetime_for_user(memory.updated_at or "", tz_id)
        text += f" • отредактировано {formatted_updated}"
    
    if remaining_views is not None and user_id is not None and user_id != memory.user_id:
        if remaining_views > 0:
            text += f"\n\n⚠️ Ты можешь посмотреть этот момент ещё <b>{remaining_views}</b> раз(а)"
        else:
            text += "\n\n⚠️ Лимит просмотров этого момента для тебя исчерпан"

    if user_id is not None and user_id == memory.user_id:
        p_type = (getattr(memory, "privacy_type", None) or "").strip()
        if p_type:
            if p_type == "limited_views":
                limit = getattr(memory, "privacy_views_limit", None)
                if limit:
                    text += (
                        f"\n\n🔒 Приватность: ограниченное количество просмотров "
                        f"(<b>{limit}</b>) для остальных, но на тебя не действует так как ты создатель момента"
                    )
                else:
                    text += "\n\n🔒 Приватность: ограниченное количество просмотров"
            elif p_type == "password":
                text += "\n\n🔒 Приватность: доступ по паролю"
    
    return text


def parse_ai_date_to_db(ai_response: str) -> Optional[str]:
    """
    Преобразует ответ ИИ (например "25.1.2026" или "25.1.2026 14:30") в формат БД YYYY-MM-DD HH:MM:SS.
    """
    s = (ai_response or "").strip()
    time_part = None
    if ' ' in s and ':' in s.split()[-1]:
        parts_space = s.split(maxsplit=1)
        if len(parts_space) == 2:
            s, time_part = parts_space[0], parts_space[1]
    for sep in ('.', '/', '-'):
        parts = s.replace(',', ' ').split()
        for p in parts:
            if sep in p and len(p.split(sep)) >= 3:
                nums = p.split(sep)
                if len(nums) >= 3:
                    try:
                        d, m, y = int(nums[0]), int(nums[1]), int(nums[2])
                        if y < 100:
                            y += 2000 if y < 50 else 1900
                        if time_part:
                            # Парсим время: HH:MM или HH:MM:SS
                            tparts = time_part.replace(':', ' ').split()[:3]
                            h = int(tparts[0]) if len(tparts) >= 1 else 0
                            mn = int(tparts[1]) if len(tparts) >= 2 else 0
                            sec = int(tparts[2]) if len(tparts) >= 3 else 0
                            return datetime(y, m, d, h, mn, sec).strftime('%Y-%m-%d %H:%M:%S')
                        return date(y, m, d).strftime('%Y-%m-%d 00:00:00')
                    except (ValueError, IndexError):
                        pass
    try:
        if time_part:
            dt = datetime.strptime(s[:10] + ' ' + time_part[:8], '%Y-%m-%d %H:%M:%S')
        else:
            dt = datetime.strptime(s[:10], '%Y-%m-%d')
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    return None


def _event_datetime_to_utc(dt_str: str, creator_user_id: Optional[int]) -> Optional[datetime]:
    """
    Интерпретирует event_datetime (в часовом поясе создателя) и возвращает момент в UTC.
    """
    try:
        if len(dt_str) >= 19:
            event_dt = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
        else:
            event_dt = datetime.strptime(dt_str[:10], '%Y-%m-%d')
        creator_offset = 3  # по умолчанию Москва, если пояс не задан
        if creator_user_id:
            try:
                from database import db
                tz_id = db.get_user_setting(creator_user_id, "timezone")
                creator_offset = _tz_offset(tz_id, 3)
            except Exception:
                pass
        # event_dt — локальное время создателя; UTC = локальное - offset
        event_utc = event_dt - timedelta(hours=creator_offset)
        return event_utc.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def format_scheduled_event_datetime(dt_str: str, creator_user_id: Optional[int] = None,
                                   viewer_user_id: Optional[int] = None) -> str:
    """
    Форматирует дату события для просмотра в часовом поясе зрителя.
    creator_user_id — кто создал (event в его поясе); viewer_user_id — кому показываем.
    Если оба None — выводит как есть.
    """
    if not dt_str or not dt_str.strip():
        return ""
    if creator_user_id is None and viewer_user_id is None:
        return format_datetime_russian(dt_str)
    event_utc = _event_datetime_to_utc(dt_str, creator_user_id)
    if event_utc is None:
        return format_datetime_russian(dt_str)
    viewer_offset = 0
    if viewer_user_id:
        try:
            from database import db
            tz_id = db.get_user_setting(viewer_user_id, "timezone")
            viewer_offset = _tz_offset(tz_id, 3)
        except Exception:
            pass
    # UTC -> локальное время зрителя
    dt_local = event_utc + timedelta(hours=viewer_offset)
    return _format_datetime_parts(dt_local)


def format_scheduled_event_datetime_for_timezone(
    dt_str: str,
    creator_user_id: Optional[int],
    timezone_id: Optional[str],
) -> str:
    """
    Форматирует дату события для сайта/внешнего клиента, когда у нас нет user_id,
    но есть timezone_id из браузера (например "Europe/Moscow").
    """
    if not dt_str or not dt_str.strip():
        return ""
    event_utc = _event_datetime_to_utc(dt_str, creator_user_id)
    if event_utc is None:
        return format_datetime_russian(dt_str)
    offset = _tz_offset(timezone_id, 0)
    dt_local = event_utc + timedelta(hours=offset)
    return _format_datetime_parts(dt_local)


def is_scheduled_event_moment_passed(event_datetime_str: str, creator_user_id: Optional[int] = None) -> bool:
    """
    Проверяет, наступил ли уже момент события (один момент во времени).
    Используется для отображения «Событие уже наступило».
    """
    event_utc = _event_datetime_to_utc(event_datetime_str, creator_user_id)
    if event_utc is None:
        return False
    return event_utc <= datetime.now(timezone.utc)


def is_scheduled_event_expired(event_datetime_str: str, user_id: Optional[int] = None) -> bool:
    """
    Проверяет, истекло ли время события в часовом поясе пользователя (для уведомлений).
    «Floating»: каждый пользователь получает уведомление, когда наступит это время в его поясе.
    """
    try:
        if len(event_datetime_str) >= 19:
            event_dt = datetime.strptime(event_datetime_str[:19], '%Y-%m-%d %H:%M:%S')
        else:
            event_dt = datetime.strptime(event_datetime_str[:10], '%Y-%m-%d')
        now = datetime.now()
        if user_id:
            try:
                from database import db
                tz_id = db.get_user_setting(user_id, "timezone")
                offset_hours = _tz_offset(tz_id, 3)
                now = datetime.now(timezone.utc) + timedelta(hours=offset_hours)
                now = now.replace(tzinfo=None)
            except Exception:
                pass
        return event_dt <= now
    except Exception:
        return False


def format_time_remaining(event_datetime_str: str, creator_user_id: Optional[int] = None,
                         viewer_user_id: Optional[int] = None) -> str:
    """
    Возвращает оставшееся время до события (одна точка во времени).
    creator_user_id — в чьём поясе хранится event_datetime; viewer_user_id — для обратной совместимости (игнорируется).
    """
    try:
        event_utc = _event_datetime_to_utc(event_datetime_str, creator_user_id)
        if event_utc is None:
            return "—"
        now_utc = datetime.now(timezone.utc)
        if event_utc <= now_utc:
            return "0"

        import calendar
        # Calculate calendar difference precisely
        years = event_utc.year - now_utc.year
        try:
            temp = now_utc.replace(year=event_utc.year)
        except ValueError:
            temp = now_utc.replace(year=event_utc.year, day=28)

        if temp > event_utc:
            years -= 1
            y = event_utc.year - 1
            try:
                temp = now_utc.replace(year=y)
            except ValueError:
                temp = now_utc.replace(year=y, day=28)

        def add_months(dt, m):
            y_add, m_add = divmod(dt.month - 1 + m, 12)
            new_year = dt.year + y_add
            new_month = m_add + 1
            _, max_days = calendar.monthrange(new_year, new_month)
            new_day = min(dt.day, max_days)
            return dt.replace(year=new_year, month=new_month, day=new_day)

        months = 0
        while True:
            next_temp = add_months(temp, months + 1)
            if next_temp > event_utc:
                break
            months += 1

        temp_months = add_months(temp, months)
        delta = event_utc - temp_months
        days = delta.days

        total_seconds = delta.seconds
        hours, rest = divmod(total_seconds, 3600)
        mins, _ = divmod(rest, 60)

        def pluralize(n, one, two, many):
            if n % 10 == 1 and n % 100 != 11:
                return f"{n} {one}"
            elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
                return f"{n} {two}"
            else:
                return f"{n} {many}"

        parts = []
        if years > 0:
            parts.append(pluralize(years, "год", "года", "лет"))
        if months > 0:
            parts.append(pluralize(months, "месяц", "месяца", "месяцев"))
        if days > 0:
            parts.append(pluralize(days, "день", "дня", "дней"))
        if hours > 0:
            parts.append(pluralize(hours, "час", "часа", "часов"))
        if mins > 0:
            parts.append(pluralize(mins, "минута", "минуты", "минут"))

        if not parts:
            return "меньше 1 минуты"

        return " ".join(parts)
    except Exception:
        return "—"


def calculate_next_occurrence(dt_str: str, creator_user_id: Optional[int] = None) -> str:
    if not dt_str or not dt_str.strip():
        return dt_str
    is_full_dt = len(dt_str) >= 19
    fmt = '%Y-%m-%d %H:%M:%S' if is_full_dt else '%Y-%m-%d'
    try:
        if is_full_dt:
            event_dt = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
        else:
            event_dt = datetime.strptime(dt_str[:10], '%Y-%m-%d')
    except Exception:
        return dt_str
    creator_offset = 3
    if creator_user_id:
        try:
            from database import db
            tz_id = db.get_user_setting(creator_user_id, "timezone")
            creator_offset = _tz_offset(tz_id, 3)
        except Exception:
            pass
    now_creator = datetime.now(timezone.utc) + timedelta(hours=creator_offset)
    now_creator = now_creator.replace(tzinfo=None)
    if event_dt >= now_creator:
        return dt_str
    y = now_creator.year
    while True:
        try:
            candidate = event_dt.replace(year=y)
        except ValueError:
            candidate = event_dt.replace(year=y, day=28)
        if candidate >= now_creator:
            return candidate.strftime(fmt)
        y += 1


def format_scheduled_event_text(event: ScheduledEvent, user_id: Optional[int] = None,
                                expired: bool = False) -> str:
    """
    Форматирует текст ожидаемого события для просмотра.
    event.user_id — создатель (event_datetime в его поясе); user_id — зритель (отображение в его поясе).
    """
    from database import db
    title = (event.title or "").strip()
    desc = sanitize_html_for_telegram((event.description or "").strip())
    creator_id = event.user_id
    viewer_id = user_id or creator_id
    # Время «Будет» показывается в часовом поясе зрителя (viewer_id), а не создателя
    dt_display = format_scheduled_event_datetime(event.event_datetime, creator_id, viewer_id)
    text = f"ℹ️ <b>{preserve_formatting(title)}</b>\n\n"
    text += f"💬 Описание: {desc if desc else '—'}\n"
    text += f"📅 Будет {dt_display}\n"
    if expired:
        text += "\n✅ Событие уже наступило.\n"
    else:
        remaining = format_time_remaining(event.event_datetime, creator_id)
        text += f"🕓 Осталось: {remaining}\n\n"
    creator = db.get_user(event.user_id) or {}
    display_name = db.get_display_name(event.user_id) or creator.get("first_name") or (f"@{creator.get('username')}" if creator.get("username") else f"ID {event.user_id}")
    profile_url = f"https://t.me/{creator.get('username')}" if creator.get("username") else f"tg://user?id={event.user_id}"
    text += f"👤 Добавил(а): <a href=\"{profile_url}\">{preserve_formatting(display_name)}</a>"
    return text


def create_scheduled_events_menu_keyboard(
    events: List[ScheduledEvent],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
    per_page: int = 10,
    from_search: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура меню событий: список (до per_page), поиск, добавить, пагинация, назад."""
    keyboard: List[List[InlineKeyboardButton]] = []
    for ev in events:
        title_short = (ev.title or "")[:28] + ("..." if len(ev.title or "") > 28 else "")
        keyboard.append([
            InlineKeyboardButton(
                text=f"📌 {title_short}",
                callback_data=f"scheduled_event_{ev.id}"
            )
        ])
    if not from_search:
        keyboard.append([
            InlineKeyboardButton(text="🔍 Поиск", callback_data="scheduled_events_search")
        ])
        keyboard.append([
            InlineKeyboardButton(text="➕ Добавить событие", callback_data="scheduled_event_add")
        ])
    if total > per_page:
        prev_p = page - 1 if page > 1 else page
        next_p = page + 1 if page < total_pages else page
        if from_search:
            prev_cb = f"scheduled_events_search_p{prev_p}"
            next_cb = f"scheduled_events_search_p{next_p}"
        else:
            prev_cb = f"scheduled_events_p{prev_p}"
            next_cb = f"scheduled_events_p{next_p}"
        keyboard.append([
            InlineKeyboardButton(text="⬅️", callback_data=prev_cb),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="scheduled_events_page_info"),
            InlineKeyboardButton(text="➡️", callback_data=next_cb),
        ])
    if from_search:
        keyboard.append([
            InlineKeyboardButton(text="🔙 К списку событий", callback_data="scheduled_events_menu")
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_scheduled_event_detail_keyboard(
    event_id: int, expired: bool = False, user_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """Клавиатура просмотра события."""
    from database import db
    keyboard = [
        [InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=f"scheduled_event_edit_{event_id}"
        )],
    ]
    if user_id and db.is_favorites_enabled(user_id) and db.is_admin(user_id):
        in_fav = db.is_in_favorites(user_id, "scheduled_event", event_id)
        keyboard.append([InlineKeyboardButton(
            text="❌ Удалить из избранного" if in_fav else "⭐ Добавить в избранное",
            callback_data=f"favorite_toggle_scheduled_event_{event_id}"
        )])
    keyboard.append([InlineKeyboardButton(
        text="🗑️ Удалить",
        callback_data=f"scheduled_event_delete_{event_id}"
    )])
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="scheduled_events_menu"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_scheduled_event_confirm_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения: Да верно / Нет изменить"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, верно", callback_data="scheduled_event_confirm_yes")],
        [InlineKeyboardButton(text="❌ Нет, изменить", callback_data="scheduled_event_confirm_no")]
    ])


def create_scheduled_event_recurrence_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора повторения события"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Повторять каждый год", callback_data="scheduled_event_recurrence_yes")],
        [InlineKeyboardButton(text="1️⃣ Однократное событие", callback_data="scheduled_event_recurrence_no")]
    ])



def create_scheduled_event_edit_options_keyboard(has_description: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура выбора что изменить при добавлении: название, описание (всегда — можно добавить или изменить), дата"""
    keyboard: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📝 Название", callback_data="scheduled_event_edit_title")],
        [InlineKeyboardButton(text="📖 Описание", callback_data="scheduled_event_edit_desc")],
        [InlineKeyboardButton(text="📅 Дата", callback_data="scheduled_event_edit_date")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="scheduled_event_edit_back")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_scheduled_event_existing_edit_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора что изменить у существующего события. Описание показывается всегда — можно добавить или изменить."""
    keyboard: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="📝 Название",
            callback_data=f"scheduled_event_existing_edit_title_{event_id}"
        )],
        [InlineKeyboardButton(
            text="📖 Описание",
            callback_data=f"scheduled_event_existing_edit_desc_{event_id}"
        )],
        [InlineKeyboardButton(
            text="📅 Дата",
            callback_data=f"scheduled_event_existing_edit_date_{event_id}"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"scheduled_event_{event_id}"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_scheduled_event_delete_confirm_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления события"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"scheduled_event_delete_yes_{event_id}")],
        [InlineKeyboardButton(text="❌ Нет, оставить", callback_data=f"scheduled_event_delete_no_{event_id}")]
    ])


def create_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создаёт главное меню: Категории / Желания / Сайт+Избранное / Настройки."""
    from database import db

    favorites_on = db.is_favorites_enabled(user_id)
    site_row = [InlineKeyboardButton(text="🌐 Сайт", callback_data="bot_site")]
    if favorites_on:
        site_row.append(InlineKeyboardButton(text="⭐ Избранное", callback_data="favorites_menu"))

    keyboard: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="💑 Наша пара", callback_data="our_couple")],
        [InlineKeyboardButton(text="📁 Категории", callback_data="cat_menu")],
        [InlineKeyboardButton(text="🎁 Желания", callback_data="wishes_menu")],
        site_row,
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings_menu")],
    ]
    if db.is_creator(user_id):
        keyboard.append([InlineKeyboardButton(text="🔧 Админ-панель", callback_data="admin_panel")])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_all_categories(couple_id: Optional[int] = None) -> dict:
    """Возвращает объединённый словарь стандартных и пользовательских категорий пары.

    Ключи стандартных: 'important_moments', 'memories', 'important_dates'.
    Ключи пользовательских: 'custom_<id>'.
    """
    from database import db
    result: dict = dict(config.CATEGORIES)
    try:
        custom = db.get_custom_categories(couple_id)
        for cat in custom:
            key = f"custom_{cat['id']}"
            emoji = cat.get("emoji") or "📁"
            result[key] = {
                "title": f"{emoji} {cat['name']}",
                "description": cat.get("description") or "",
                "emoji": emoji,
                "is_custom": True,
                "id": cat["id"],
            }
    except Exception:
        pass
    return result


def create_categories_keyboard(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Подменю «Категории»: стандартные + пользовательские + события на дату + добавить + назад."""
    from database import db
    rows: List[List[InlineKeyboardButton]] = []
    # Стандартные категории
    for key, cat in config.CATEGORIES.items():
        rows.append([InlineKeyboardButton(text=cat["title"], callback_data=f"category_{key}")])
    # Пользовательские категории (из БД)
    if user_id is not None:
        try:
            couple = db.get_couple_by_user(user_id)
            couple_id = couple["id"] if couple else None
            custom_cats = db.get_custom_categories(couple_id)
            for cat in custom_cats:
                key = f"custom_{cat['id']}"
                emoji = cat.get("emoji") or "📁"
                rows.append([InlineKeyboardButton(text=f"{emoji} {cat['name']}", callback_data=f"category_{key}")])
        except Exception:
            pass
    rows.append([InlineKeyboardButton(text="🎯 События на дату", callback_data="scheduled_events_menu")])
    rows.append([InlineKeyboardButton(text="➕ Добавить категорию", callback_data="add_category")])
    rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


TIMEZONE_OPTIONS = [
    ("Europe/Moscow",  "Москва (UTC+3)"),
]


def create_settings_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура раздела «Настройки»: подпункты и назад."""
    from database import db
    keyboard: List[List[InlineKeyboardButton]] = []
    if db.is_in_couple(user_id) or db.is_admin(user_id):
        keyboard.append([InlineKeyboardButton(
            text="🔔 Уведомления",
            callback_data="settings_notifications"
        )])
    if db.is_admin(user_id):
        keyboard.append([InlineKeyboardButton(
            text="⭐️ Избранное",
            callback_data="settings_favorites"
        )])
    keyboard.append([InlineKeyboardButton(
        text="🕐 Время в боте",
        callback_data="settings_time"
    )])
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="back_to_main"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_timezone_label(tz_id: Optional[str]) -> str:
    """Возвращает подпись для часового пояса по id с UTC-смещением.

    Для известных часовых поясов из TIMEZONE_OPTIONS возвращает готовый label.
    Для любого другого IANA-пояса динамически вычисляет UTC-смещение через
    _tz_offset() (учитывает DST) и добавляет его в скобках.
    """
    if not tz_id:
        return "не выбрано"
    # Ищем в предзаданном списке
    for tid, label in TIMEZONE_OPTIONS:
        if tid == tz_id:
            return label
    # Динамически вычисляем UTC-смещение
    try:
        offset_hours = _tz_offset(tz_id)
        if offset_hours == int(offset_hours):
            sign = "+" if offset_hours >= 0 else ""
            utc_str = f"UTC {sign}{int(offset_hours)}"
        else:
            # Дробные часы (например, Adelaide UTC+9:30)
            total_min = int(offset_hours * 60)
            sign = "+" if total_min >= 0 else "-"
            h, m = divmod(abs(total_min), 60)
            utc_str = f"UTC {sign}{h}:{m:02d}"
        return f"{tz_id} ({utc_str})"
    except Exception:
        return tz_id


def get_timezone_label_for_display(tz_id: Optional[str], display_name: Optional[str]) -> str:
    """Подпись TZ с приоритетом пользовательского названия города/региона."""
    base = get_timezone_label(tz_id)
    if not tz_id:
        return base
    if not isinstance(display_name, str) or not display_name.strip():
        return base
    name = display_name.strip()
    try:
        offset_hours = _tz_offset(tz_id)
        if offset_hours == int(offset_hours):
            sign = "+" if offset_hours >= 0 else ""
            utc_str = f"UTC {sign}{int(offset_hours)}"
        else:
            total_min = int(offset_hours * 60)
            sign = "+" if total_min >= 0 else "-"
            h, m = divmod(abs(total_min), 60)
            utc_str = f"UTC {sign}{h}:{m:02d}"
        return f"{name} ({utc_str})"
    except Exception:
        return f"{name} ({tz_id})"


def get_default_timezone(user_id: int) -> str:
    """Дефолтный часовой пояс: пока не определён."""
    return "UTC"


def create_favorites_menu_keyboard(
    items: List[Dict],
    page: int = 1,
    total_pages: int = 1,
    total: int = 0,
    per_page: int = 10,
    from_search: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура избранного: список (type_label + title), поиск, добавить, пагинация, назад."""
    keyboard: List[List[InlineKeyboardButton]] = []
    for it in items:
        type_label = it.get("type_label", "")
        title = (it.get("title") or "")[:TITLE_PREVIEW_LENGTH]
        if len(it.get("title") or "") > TITLE_PREVIEW_LENGTH:
            title += "..."
        btn_text = f"⭐️ {type_label}: {title}" if type_label else f"⭐️ {title}"
        cb = f"favorite_view_{it['item_type']}_{it['item_id']}"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=cb)])
    if not from_search:
        keyboard.append([InlineKeyboardButton(text="🔍 Поиск", callback_data="favorites_search")])
        keyboard.append([InlineKeyboardButton(text="➕ Добавить", callback_data="favorites_add_menu")])
    if total > per_page:
        prev_p = page - 1 if page > 1 else page
        next_p = page + 1 if page < total_pages else page
        prev_cb = f"favorites_search_p{prev_p}" if from_search else f"favorites_p{prev_p}"
        next_cb = f"favorites_search_p{next_p}" if from_search else f"favorites_p{next_p}"
        keyboard.append([
            InlineKeyboardButton(text="⬅️", callback_data=prev_cb),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="favorites_page_info"),
            InlineKeyboardButton(text="➡️", callback_data=next_cb),
        ])
    back_cb = "favorites_menu" if from_search else "back_to_main"
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_favorites_add_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Меню «Добавить в избранное»: категории, события, желания (без настроек и админ-панели)."""
    from database import db
    keyboard: List[List[InlineKeyboardButton]] = []
    categories = list(config.CATEGORIES.items())
    for i in range(0, len(categories), 2):
        row = []
        for j in range(2):
            if i + j < len(categories):
                key, cat = categories[i + j]
                row.append(InlineKeyboardButton(text=cat["title"], callback_data=f"category_{key}"))
        if row:
            keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="🎯 События на дату", callback_data="scheduled_events_menu")])
    partner_id = db.get_couple_partner(user_id)
    if is_wishes_available():
        if partner_id:
            keyboard.append([InlineKeyboardButton(text="🎁 Желания партнёра", callback_data="creator_wishes_partner")])
        keyboard.append([InlineKeyboardButton(text="💫 Мои желания", callback_data="wishes_menu")])
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="favorites_menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_notifications_label(enabled: bool) -> str:
    """Возвращает подпись для статуса уведомлений."""
    return "включены" if enabled else "выключены"


# ── Категории уведомлений ─────────────────────────────────────────────────────
# (category_key, button_label, emoji_for_detail_screen)
NOTIF_CATEGORIES_ALL = [
    ("important_moments", "Важные моменты", "💫"),
    ("memories",          "Воспоминания",   "📖"),
    ("important_dates",   "Важные даты",    "📅"),
    ("events",            "События на дату","📆"),
    ("wishes",            "Желания",        "🎁"),
]
NOTIF_CATEGORIES_CREATOR = [
    ("site_visits", "Вход на сайт", "👤"),
    ("logger",      "Логгер",       "ℹ️"),
]

NOTIF_CATEGORY_DESCRIPTION = {
    "important_moments": (
        "Если включишь уведомления то ты увидишь когда создался, изменился или удалился "
        "какой либо <i>важный момент</i>"
    ),
    "memories": (
        "Если включишь уведомления то ты увидишь когда создалось, изменилось или удалилось "
        "какое нибудь <i>воспоминание</i>"
    ),
    "important_dates": (
        "Если включишь уведомления то ты увидишь когда создалась, изменилась или удалилась "
        "<i>важная дата</i>"
    ),
    "events": (
        "Если включишь уведомления то ты увидишь когда создалось, изменилось или удалилось "
        "<i>событие на дату</i>, <b>А ТАКЖЕ</b> узнаешь когда это событие наступит 🥳"
    ),
    "wishes": (
        "Если включишь уведомления то ты увидишь когда создалось, изменилось или удалилось "
        "какое либо <i>твое желание</i>, и да, ещё ты будешь всегда видеть его статус "
        "(создано, в процессе или выполнено) 😁"
    ),
    "site_visits": "Ты будешь получать информацию об устройстве когда кто то заходит на сайт",
    "logger":      "Тебе суда будут приходить логи с сайта (все, в том числе с неба и обычные)",
}


def build_notif_main_text(enabled: bool) -> str:
    """Текст главного экрана уведомлений."""
    if enabled:
        return (
            "🔔 <b>Уведомления</b>\n\n"
            "Ты можешь настроить каждый пункт и отдельно для всего включить или отключить уведомления"
        )
    return "🔔 <b>Уведомления</b>\n\n❌ Отключены"


def create_settings_notifications_keyboard(
    enabled: bool = True,
    cat_states: Optional[dict] = None,
    is_creator: bool = False,
) -> InlineKeyboardMarkup:
    """
    Главная клавиатура настроек уведомлений.
    - disabled → одна кнопка «✅ Включить уведомления»
    - enabled  → сетка категорий + кнопка «❌ Отключить уведомления»
    """
    keyboard: List[List[InlineKeyboardButton]] = []

    if not enabled:
        keyboard.append([InlineKeyboardButton(
            text="✅ Включить уведомления",
            callback_data="settings_notifications_set_1",
        )])
    else:
        cat_states = cat_states or {}
        cats = list(NOTIF_CATEGORIES_ALL)
        if is_creator:
            cats += NOTIF_CATEGORIES_CREATOR

        # Размещаем кнопки по 2 в ряд
        row: List[InlineKeyboardButton] = []
        for cat_key, label, _emoji in cats:
            icon = "✅" if cat_states.get(cat_key, True) else "❌"
            btn = InlineKeyboardButton(
                text=f"{icon} {label}",
                callback_data=f"notif_cat_{cat_key}",
            )
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton(
            text="❌ Отключить уведомления",
            callback_data="settings_notifications_set_0",
        )])

    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад в настройки",
        callback_data="settings_menu",
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def _find_notif_cat_info(cat_key: str):
    """Возвращает (label, emoji) для категории или (cat_key, '🔔')."""
    for key, label, emoji in NOTIF_CATEGORIES_ALL + NOTIF_CATEGORIES_CREATOR:
        if key == cat_key:
            return label, emoji
    return cat_key, "🔔"


def build_notif_cat_text(cat_key: str, enabled: bool) -> str:
    """Текст экрана детальной настройки категории уведомлений."""
    label, emoji = _find_notif_cat_info(cat_key)
    desc = NOTIF_CATEGORY_DESCRIPTION.get(cat_key, "")
    status = "✅ Уведомления включены" if enabled else "❌ Уведомления отключены"
    return (
        f"{emoji} <b>{label}</b>\n\n"
        f"ℹ️ {desc}\n\n"
        f"{status}"
    )


def create_notif_cat_keyboard(cat_key: str, enabled: bool) -> InlineKeyboardMarkup:
    """Клавиатура экрана категории уведомлений: тогл + кнопка Назад."""
    if enabled:
        toggle_btn = InlineKeyboardButton(
            text="❌ Отключить уведомления",
            callback_data=f"notif_cat_{cat_key}_set_0",
        )
    else:
        toggle_btn = InlineKeyboardButton(
            text="✅ Включить уведомления",
            callback_data=f"notif_cat_{cat_key}_set_1",
        )
    back_btn = InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="settings_notifications",
    )
    return InlineKeyboardMarkup(inline_keyboard=[[toggle_btn], [back_btn]])


def create_settings_favorites_keyboard(enabled: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура настройки избранного: Вкл/Выкл. По умолчанию включено."""
    keyboard: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text=("✅ " if enabled else "") + "Включено",
            callback_data="settings_favorites_set_1"
        )],
        [InlineKeyboardButton(
            text=("✅ " if not enabled else "") + "Выключено",
            callback_data="settings_favorites_set_0"
        )],
    ]
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад в настройки",
            callback_data="settings_menu"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_settings_time_keyboard(current_tz: Optional[str] = None, current_display: Optional[str] = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора времени: быстрый выбор + ручной ввод города + назад."""
    keyboard: List[List[InlineKeyboardButton]] = []
    active_tz = (current_tz or "").strip()
    if active_tz:
        active_label = get_timezone_label_for_display(active_tz, current_display)
    else:
        active_tz = "UTC"
        active_label = "не выбрано"
    # Используем `|` как разделитель, чтобы не ломать timezone с `_` (например America/New_York).
    encoded_active = active_tz.replace("/", "|")
    keyboard.append([
        InlineKeyboardButton(
            text=f"✅ {active_label}",
            callback_data=f"settings_time_set_{encoded_active}",
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="🏙 Указать город",
            callback_data="settings_time_city_start",
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад в настройки",
            callback_data="settings_menu"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_category_keyboard(
    category_key: str, memories: List[Memory], user_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для категории с поддержкой пагинации.
    
    Если моментов больше 10, под списком появляется ряд:
    ⬅️ | текущая/всего | ➡️
    """
    return create_category_keyboard_paged(category_key, memories, page=1, user_id=user_id)


def create_category_keyboard_paged(
    category_key: str,
    memories: List[Memory],
    page: int = 1,
    per_page: int = 10,
    user_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для категории с явной поддержкой пагинации"""
    keyboard: List[List[InlineKeyboardButton]] = []
    
    total = len(memories)
    total_pages = max(1, math.ceil(total / per_page)) if total > 0 else 1
    
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_memories = memories[start_idx:end_idx]
    
    if page_memories:
        for memory in page_memories:
            button_text = substitute_params(memory.title or "", user_id)
            if len(button_text) > 30:
                button_text = button_text[:27] + "..."
            keyboard.append([
                InlineKeyboardButton(
                    text=f"📌 {button_text}",
                    callback_data=f"catmem_{category_key}_{page}_{memory.id}"
                )
            ])
    
    if total > per_page:
        prev_page = page - 1 if page > 1 else page
        next_page = page + 1 if page < total_pages else page
        
        keyboard.append([
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"category_{category_key}_p{prev_page}"
            ),
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data=f"page_info_{page}_{total_pages}"
            ),
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"category_{category_key}_p{next_page}"
            ),
        ])
    
    # Кнопки действий
    keyboard.append([
        InlineKeyboardButton(
            text="➕ Добавить момент",
            callback_data=f"add_to_{category_key}"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="🔍 Поиск",
            callback_data="search_memories"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_main"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_search_results_keyboard(
    memories: List[Memory],
    page: int = 1,
    per_page: int = 10,
    user_id: Optional[int] = None,
) -> InlineKeyboardMarkup:
    """Клавиатура результатов поиска: список воспоминаний, пагинация, назад."""
    keyboard: List[List[InlineKeyboardButton]] = []
    total = len(memories)
    total_pages = max(1, math.ceil(total / per_page)) if total > 0 else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_memories = memories[start_idx:end_idx]
    if page_memories:
        for memory in page_memories:
            button_text = substitute_params(memory.title or "", user_id)
            if len(button_text) > 30:
                button_text = button_text[:27] + "..."
            keyboard.append([
                InlineKeyboardButton(
                    text=f"📌 {button_text}",
                    callback_data=f"memory_search_{memory.id}"
                )
            ])
    if total > per_page:
        prev_page = page - 1 if page > 1 else page
        next_page = page + 1 if page < total_pages else page
        keyboard.append([
            InlineKeyboardButton(text="⬅️", callback_data=f"search_results_p{prev_page}"),
            InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data=f"page_info_{page}_{total_pages}"),
            InlineKeyboardButton(text="➡️", callback_data=f"search_results_p{next_page}"),
        ])
    keyboard.append([
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_memory_detail_keyboard(
    memory_id: int, category: str, from_search: bool = False, page: int = 1, user_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для детального просмотра воспоминания."""
    from database import db
    if from_search:
        back_callback = "back_to_search_results"
        back_text = "🔙 К результатам поиска"
    else:
        back_callback = f"back_to_category_{category}_p{page}" if page > 1 else f"back_to_category_{category}"
        back_text = "🔙 Назад"
    keyboard = [
        [InlineKeyboardButton(
            text="✏️ Изменить",
            callback_data=f"edit_memory_{memory_id}"
        )],
    ]
    if user_id and db.is_favorites_enabled(user_id) and db.is_admin(user_id):
        in_fav = db.is_in_favorites(user_id, "memory", memory_id)
        keyboard.append([InlineKeyboardButton(
            text="❌ Удалить из избранного" if in_fav else "⭐ Добавить в избранное",
            callback_data=f"favorite_toggle_memory_{memory_id}"
        )])
    del_cb = f"delete_confirm_search_{memory_id}" if from_search else f"delete_confirm_{memory_id}"
    keyboard.append([InlineKeyboardButton(
        text="🗑️ Удалить",
        callback_data=del_cb
    )])
    keyboard.append([InlineKeyboardButton(
        text=back_text,
        callback_data=back_callback
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_confirmation_keyboard(memory_id: int, category: str, from_search: bool = False) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру подтверждения удаления"""
    yes_cb = f"delete_yes_search_{memory_id}" if from_search else f"delete_yes_{memory_id}"
    no_cb = f"delete_no_search_{memory_id}" if from_search else f"delete_no_{memory_id}"
    keyboard = [
        [InlineKeyboardButton(text="✅ Да, удалить", callback_data=yes_cb)],
        [InlineKeyboardButton(text="❌ Нет, оставить", callback_data=no_cb)]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_admin_delete_confirm_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру подтверждения удаления админа"""
    keyboard = [
        [InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"admin_delete_yes_{admin_id}"
        )],
        [InlineKeyboardButton(
            text="❌ Нет, оставить",
            callback_data=f"admin_delete_no_{admin_id}"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_edit_options_keyboard(
    memory_id: int,
    category: str,
    can_edit_privacy: bool = False,
) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру выбора что редактировать."""
    from database import db

    keyboard = [
        [InlineKeyboardButton(
            text="📝 Название",
            callback_data=f"edit_title_{memory_id}"
        )],
        [InlineKeyboardButton(
            text="📅 Дата",
            callback_data=f"edit_date_{memory_id}"
        )],
        [InlineKeyboardButton(
            text="📖 Описание",
            callback_data=f"edit_content_{memory_id}"
        )],
    ]

    if can_edit_privacy:
        privacy = db.get_memory_privacy(memory_id)
        p_type = (privacy.get("privacy_type") or "").strip()
        if not p_type:
            privacy_text = "🔒 Изменить приватность"
            privacy_cb = f"edit_privacy_{memory_id}"
        elif p_type == "limited_views":
            privacy_text = "🔓 Удалить ограниченный доступ"
            privacy_cb = f"privacy_remove_limited_{memory_id}"
        elif p_type == "password":
            privacy_text = "🔓 Удалить доступ по паролю"
            privacy_cb = f"privacy_remove_password_{memory_id}"
        else:
            privacy_text = "🔒 Изменить приватность"
            privacy_cb = f"edit_privacy_{memory_id}"

        keyboard.append([
            InlineKeyboardButton(
                text=privacy_text,
                callback_data=privacy_cb
            )
        ])

    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=f"memory_{memory_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_cancel_adding_keyboard(category_key: str) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для отмены добавления воспоминания"""
    keyboard = [
        [InlineKeyboardButton(
            text="❌ Отменить добавление",
            callback_data=f"cancel_adding_{category_key}"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_admin_keyboard() -> InlineKeyboardMarkup:
    """Создаёт клавиатуру админ-панели"""
    from database import db
    test_mode = db.get_setting("test_version") == "1"
    toggle_label = "✅ Продакшн" if test_mode else "🔧 Тест версия"
    keyboard = [
        [
            InlineKeyboardButton(text="История версий", callback_data="admin_version_history:0"),
            InlineKeyboardButton(text="🩺 Диагностика", callback_data="admin_diagnostics"),
        ],
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
            InlineKeyboardButton(text="📋 Админы", callback_data="list_admins"),
        ],
        [
            InlineKeyboardButton(text="📨 Рассылка", callback_data="broadcast_menu"),
            InlineKeyboardButton(text="📱 Устройства", callback_data="admin_devices"),
        ],
        [InlineKeyboardButton(text="📤 Экспорт воспоминаний", callback_data="admin_export_memories")],
        [
            InlineKeyboardButton(text="🔄 Перезагрузка", callback_data="admin_restart"),
            InlineKeyboardButton(text=toggle_label, callback_data="admin_toggle_test"),
        ],
        [InlineKeyboardButton(text="🗑️ Удалить данные", callback_data="admin_delete_data")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_admin_list_keyboard(admins: List[Dict]) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру со списком администраторов (только ID)"""
    from database import db
    keyboard = []
    for i in range(0, len(admins), 2):
        row = []
        for j in range(2):
            if i + j < len(admins):
                admin = admins[i + j]
                uid = admin['user_id']
                if uid == db.get_creator_id():
                    emoji = "👑"
                elif db.is_in_couple(uid) and not db.is_creator(uid):
                    emoji = "💖"
                else:
                    emoji = "👨‍💼"
                row.append(InlineKeyboardButton(
                    text=f"{emoji} ID {uid}",
                    callback_data=f"admin_detail_{uid}"
                ))
        if row:
            keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_broadcast_target_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора получателей рассылки"""
    keyboard = [
        [InlineKeyboardButton(
            text="👥 Всем",
            callback_data="broadcast_to_all"
        )],
        [InlineKeyboardButton(
            text="💖 Только партнёру",
            callback_data="broadcast_to_partner"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="admin_panel"
        )]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def create_admin_detail_keyboard(admin_id: int) -> InlineKeyboardMarkup:
    """Создаёт клавиатуру для детального просмотра администратора"""
    from database import db
    keyboard = []
    
    if not db.is_creator(admin_id):
        keyboard.append([
            InlineKeyboardButton(
                text="🗑️ Удалить админа",
                callback_data=f"admin_delete_confirm_{admin_id}"
            )
        ])
    
    # Кнопка возврата
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад к списку",
            callback_data="list_admins"
        )
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def format_visit_telegram_message(data: Dict[str, Any], minimal: bool = False) -> str:
    """
    Формирует текст уведомления «Вход на сайт» из словаря (для log_visit и кнопки «Обновить»).
    Ключи: prefix, timezone, location_str, isp_str, coords_str, ua_pretty, lang_display,
    screen_str, pixel_ratio, screen_w, screen_h, battery_level, battery_charging,
    conn_label, downlink, rtt_ms, save_data, real_ip, ip_server, referrer, theme,
    architecture, model, device_type, role, role_label.
    """
    lines = []
    prefix = data.get("prefix") or "Вход"
    tz = data.get("timezone") or "не указан"
    role_label = data.get("role_label")
    if role_label:
        lines.append(f"🌐 {prefix} на сайт {config.BOT_SITE_URL} ({role_label})")
    else:
        lines.append(f"🌐 {prefix} на сайт {config.BOT_SITE_URL}")
    lines.append(f"🌍 Часовой пояс: {tz}")
    lines.append("")
    loc = data.get("location_str") or "—"
    if not minimal and data.get("isp_str"):
        loc += f" ({data['isp_str']})"
    if not minimal and data.get("coords_str"):
        loc += f". Координаты: <code>{data['coords_str']}</code>"
    lines.append(f"📍 Местоположение: {loc}")
    lines.append(f"💻 Устройство: {data.get('ua_pretty') or '—'}")
    if data.get("lang_display"):
        lines.append(f"🌐 Язык: {data['lang_display']}")
    if minimal:
        return "\n".join(lines)
    sw = data.get("screen_str")
    if sw:
        pr = data.get("pixel_ratio")
        if pr:
            lines.append(f"🖥 Экран: {sw}, плотность {pr}x")
        else:
            lines.append(f"🖥 Экран: {sw}")
    bat_level = data.get("battery_level")
    if bat_level is not None:
        ch = data.get("battery_charging")
        ch_str = "заряжается" if ch else "не заряжается"
        lines.append(f"🔋 Заряд: {bat_level}% ({ch_str})")
    conn_label = data.get("conn_label")
    downlink = data.get("downlink")
    if conn_label or downlink is not None:
        if downlink is not None:
            lines.append(f"📶 Соединение: {conn_label or '—'}, скорость {downlink} Mbps")
        else:
            lines.append(f"📶 Соединение: {conn_label or '—'}")
    if data.get("rtt_ms") is not None:
        lines.append(f"📉 Задержка: {data['rtt_ms']} ms")
    if data.get("save_data"):
        lines.append("💡 Режим экономии трафика: включен")
    rw, rh = data.get("screen_real_w"), data.get("screen_real_h")
    if rw and rh:
        lines.append(f"🔍 Реальное разрешение: {rw}x{rh}")
    real_ip = data.get("real_ip")
    ip_server = data.get("ip_server")
    if real_ip and ip_server and real_ip != ip_server:
        lines.append(f"📎 Основной IP: {real_ip} (настоящий)")
        lines.append(f"📎 IP с VPN/прокси: {ip_server}")
    else:
        ip_show = real_ip or ip_server
        if ip_show:
            lines.append(f"📎 IP: {ip_show}")
    if data.get("referrer"):
        ref = (data["referrer"] or "")[:100]
        lines.append(f"📎 Реферер: {ref}")
    if data.get("architecture"):
        lines.append(f"📐 Архитектура: {data['architecture']}")
    if data.get("model"):
        lines.append(f"📛 Модель: {data['model']}")
    if data.get("device_type"):
        lines.append(f"📲 Тип: {data['device_type']}")
    theme = data.get("theme")
    if theme:
        theme_l = "тёмная" if theme == "dark" else ("светлая" if theme == "light" else theme)
        lines.append(f"🌙 Тема сайта: {theme_l}")
    return "\n".join(lines)


def format_admin_short_line(admin: Dict) -> str:
    """Одна строка для списка админов: роль, ID, добавлен, активность"""
    from database import db
    user_id = admin['user_id']
    if user_id == db.get_creator_id():
        role = "👑"
    elif db.is_in_couple(user_id) and not db.is_creator(user_id):
        role = "💖"
    else:
        role = "👨‍💼"
    added = (admin.get('added_at') or '—')[:10] if admin.get('added_at') else '—'
    last_seen = (admin.get('last_seen') or '—')[:10] if admin.get('last_seen') else '—'
    return f"{role} ID {user_id} · добавлен {added} · активность {last_seen}"


def format_admin_details(admin: Dict, user_stats: Optional[Dict[str, int]] = None) -> str:
    """Форматирует детальную информацию об администраторе и его статистику."""
    from database import db
    user_id = admin['user_id']
    added_at = admin.get('added_at', 'неизвестно')
    created_at = admin.get('created_at', 'неизвестно')
    last_seen = admin.get('last_seen', 'неизвестно')

    if db.is_creator(user_id):
        role = "👑 Разработчик"
        can_delete = False
    elif db.is_in_couple(user_id) and not db.is_creator(user_id):
        role = "💖 Партнёр"
        can_delete = False
    else:
        role = "👨‍💼 Администратор"
        can_delete = True

    def _d(s: str) -> str:
        if not s or s in ('неизвестно', '—'):
            return '—'
        return s[:10] if len(s) >= 10 else s

    text = f"<b>{role}</b>\n"
    text += f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
    text += f"📅 В боте с: {_d(created_at)}\n"
    text += f"📌 Добавлен как админ: {_d(added_at)}\n"
    text += f"🕐 Последняя активность: {_d(last_seen)}\n"

    stats = user_stats if user_stats is not None else db.get_user_stats(user_id)
    text += "\n<b>📊 Статистика пользователя</b>\n"
    text += f"  · Воспоминаний: {stats.get('total_memories', 0)}\n"
    text += f"  · Категорий: {stats.get('categories_count', 0)}\n"
    text += f"  · Событий на дату: {stats.get('scheduled_events_count', 0)}\n"
    text += f"  · Желаний: {stats.get('wishes_count', 0)}\n"

    if not can_delete:
        text += "\n⚠️ <i>Этого администратора нельзя удалить</i>"
    return text

WISH_STATUS_LABELS = {
    "created":     "⬜ Создано",
    "in_progress": "🔵 В процессе",
    "done":        "✅ Выполнено",
}

def format_wish_text(wish: Wish, user_id: Optional[int] = None) -> str:
    """Форматирует текст желания для отображения. user_id — для учёта часового пояса в дате добавления."""
    from database import db
    author_name = db.get_display_name(wish.user_id) if wish.user_id else "Партнёр"
    text = f"💫 <b>Желание {author_name} #{wish.id}</b>\n\n"
    text += "💬 " + sanitize_html_for_telegram((wish.content or "").strip()) + "\n\n"

    status = getattr(wish, "status", "created") or "created"
    status_label = WISH_STATUS_LABELS.get(status, "⬜ Создано")
    text += f"🏷 Статус: <b>{status_label}</b>\n\n"

    tz_id = db.get_user_setting(user_id, "timezone") if user_id else None
    formatted_datetime = format_datetime_for_user(wish.created_at or "", tz_id)
    text += f"📅 Желание добавлено {formatted_datetime}"
    if wish.updated_at and (wish.updated_at or "") != (wish.created_at or ""):
        formatted_updated = format_datetime_for_user(wish.updated_at or "", tz_id)
        text += f" • отредактировано {formatted_updated}"
    return text


async def send_wish_with_media(
    chat_id: int,
    bot,
    wish: Wish,
    text: str,
    keyboard: InlineKeyboardMarkup
) -> int:
    """
    Отправляет желание с описанием и, при необходимости, медиа.
    Логика аналогична воспоминаниям.
    """
    if not (wish.media_type and wish.media_file_id):
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return msg.message_id

    try:
        if wish.media_type == "photo":
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=wish.media_file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id

        if wish.media_type == "video":
            msg = await bot.send_video(
                chat_id=chat_id,
                video=wish.media_file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id

        text_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

        if wish.media_type == "video_note":
            await bot.send_video_note(
                chat_id=chat_id,
                video_note=wish.media_file_id,
            )
        elif wish.media_type == "voice":
            await bot.send_voice(
                chat_id=chat_id,
                voice=wish.media_file_id,
            )
        elif wish.media_type == "audio":
            await bot.send_audio(
                chat_id=chat_id,
                audio=wish.media_file_id,
            )
        elif wish.media_type == "document":
            await bot.send_document(
                chat_id=chat_id,
                document=wish.media_file_id,
            )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=wish.media_file_id,
            )

        return text_msg.message_id

    except Exception as e:
        logger.error(f"Ошибка при отправке медиа для желания: {e}")
        msg = await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка при загрузке медиа\n\n{text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return msg.message_id


async def send_scheduled_event_with_media(
    chat_id: int,
    bot,
    event: ScheduledEvent,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> int:
    """
    Отправляет событие с текстом и при необходимости медиа (как у воспоминаний/желаний).
    """
    if not (event.media_type and event.media_file_id):
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return msg.message_id
    try:
        if event.media_type == "photo":
            msg = await bot.send_photo(
                chat_id=chat_id,
                photo=event.media_file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id
        if event.media_type == "video":
            msg = await bot.send_video(
                chat_id=chat_id,
                video=event.media_file_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id
        text_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        if event.media_type == "video_note":
            await bot.send_video_note(chat_id=chat_id, video_note=event.media_file_id)
        elif event.media_type == "voice":
            await bot.send_voice(chat_id=chat_id, voice=event.media_file_id)
        elif event.media_type == "audio":
            await bot.send_audio(chat_id=chat_id, audio=event.media_file_id)
        elif event.media_type == "document":
            await bot.send_document(chat_id=chat_id, document=event.media_file_id)
        else:
            await bot.send_document(chat_id=chat_id, document=event.media_file_id)
        return text_msg.message_id
    except Exception as e:
        logger.error(f"Ошибка при отправке медиа события: {e}")
        msg = await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка при загрузке медиа\n\n{text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return msg.message_id


def create_wishes_menu_keyboard(wishes: List[Wish]) -> InlineKeyboardMarkup:
    """Клавиатура для меню желаний пользователя: список + кнопка добавить."""
    from database import db
    keyboard: List[List[InlineKeyboardButton]] = []
    for wish in wishes:
        author_name = db.get_display_name(wish.user_id) if wish.user_id else "Партнёр"
        preview = (wish.content or "").strip()[:35]
        if len((wish.content or "").strip()) > 35:
            preview += "…"
        keyboard.append([
            InlineKeyboardButton(
                text=f"💫 {author_name}: {preview}",
                callback_data=f"wish_view_{wish.id}"
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            text="➕ Добавить желание",
            callback_data="wish_add_new"
        )
    ])
    keyboard.append([
        InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_main"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_wishes_menu_keyboard_partner(wishes: List[Wish]) -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости."""
    return create_wishes_menu_keyboard(wishes)


def create_wishes_menu_keyboard_creator(wishes: List[Wish]) -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости."""
    return create_wishes_menu_keyboard(wishes)


def create_wish_keyboard(wish_id: int, user_id: Optional[int] = None, back_target: str = "wishes_menu") -> InlineKeyboardMarkup:
    """Клавиатура управления желанием. Редактирование/удаление — только автору. Статус — любому участнику пары."""
    from database import db
    keyboard = [
        [InlineKeyboardButton(
            text="🏷 Изменить статус",
            callback_data=f"wish_status_{wish_id}"
        )],
    ]
    wish = db.get_wish(wish_id)
    is_owner = wish and user_id and wish.user_id == user_id
    if is_owner:
        keyboard.append([InlineKeyboardButton(
            text="✏️ Изменить желание",
            callback_data=f"wish_edit_{wish_id}"
        )])
    if user_id and db.is_favorites_enabled(user_id) and db.is_admin(user_id):
        in_fav = db.is_in_favorites(user_id, "wish", wish_id)
        keyboard.append([InlineKeyboardButton(
            text="❌ Удалить из избранного" if in_fav else "⭐ Добавить в избранное",
            callback_data=f"favorite_toggle_wish_{wish_id}"
        )])
    if is_owner:
        keyboard.append([InlineKeyboardButton(
            text="🗑️ Удалить желание",
            callback_data=f"wish_delete_{wish_id}"
        )])
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад к желаниям",
        callback_data="wishes_menu"
    )])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def create_wish_user_keyboard(wish_id: int, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости."""
    return create_wish_keyboard(wish_id, user_id, back_target="wishes_menu")


def create_wish_admin_keyboard(wish_id: int, user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """Алиас для обратной совместимости — просмотр желания партнёра."""
    return create_wish_keyboard(wish_id, user_id, back_target="partner_wishes")

def save_media_file(file_content: bytes, file_extension: str) -> str:
    """Сохраняет медиа-файл на диск"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    filename = f"{timestamp}_{unique_id}.{file_extension}"
    
    file_path = os.path.join(config.MEDIA_FOLDER, filename)
    
    with open(file_path, 'wb') as f:
        f.write(file_content)
    
    return file_path

def validate_title(title: str) -> tuple[bool, str]:
    """Проверяет валидность названия"""
    if not title or len(title.strip()) == 0:
        return False, "Название не может быть пустым"
    
    if len(title.strip()) < 2:
        return False, "Название должно содержать хотя бы 2 символа"
    
    if len(title) > 100:
        return False, "Название слишком длинное (максимум 100 символов)"
    
    return True, ""

def validate_date(date: str) -> tuple[bool, str]:
    """Проверяет валидность даты"""
    if not date or len(date.strip()) == 0:
        return False, "Дата не может быть пустой"
    
    if len(date.strip()) < 2:
        return False, "Дата должна содержать хотя бы 2 символа"
    
    if len(date) > 50:
        return False, "Дата слишком длинная (максимум 50 символов)"
    
    return True, ""

def validate_content(content: str) -> tuple[bool, str]:
    """Проверяет валидность описания - текст теперь необязательный"""
    if content and len(content) > 4000:
        return False, "Описание слишком длинное (максимум 4000 символов)"
    
    return True, ""

def get_text_for_other_users() -> str:
    """Текст для пользователей без доступа."""
    from database import db
    partner_mention = "особого человека"
    partner_id = db.get_ksusha_id()
    if partner_id:
        partner = db.get_user(partner_id)
        username = (partner or {}).get("username")
        if username:
            partner_mention = f"@{username}"
    return config.TEXT_FOR_OTHER_USERS.format(ksusha_mention=partner_mention)


def is_wishes_available() -> bool:
    """
    Проверяет, доступен ли полный функционал (желания, сайт и т.п.) для партнёра.
    Дата начала берётся из config.SITE_OPEN_DATE.
    """
    today = date.today()
    start = getattr(config, "SITE_OPEN_DATE", None)
    if not isinstance(start, date):
        return True
    return today >= start

def format_welcome_message(user_id: int) -> str:
    """Форматирует приветственное сообщение — одинаковое для всех."""
    from database import db

    # Кастомное приветствие из настроек (если задано)
    partner_id = db.get_couple_partner(user_id)
    if partner_id:
        custom = db.get_setting("greeting_partner") or db.get_setting("greeting_ksusha")
        if custom:
            return custom
    if db.is_admin(user_id):
        custom = db.get_setting("greeting_admins")
        if custom:
            return custom

    return (
        "💕 <b>Привет!</b>\n\n"
        "Здесь хранятся ваши моменты, воспоминания и события — всё важное в одном месте.\n\n"
        "Выбери раздел ниже 👇"
    )

def format_stats_message(stats: Dict[str, Any], user_stats: Dict[str, int]) -> str:
    """Форматирует расширенное сообщение со статистикой."""
    from database import db
    category_stats = db.get_category_stats()
    admin_stats = db.get_admin_stats()
    couples = db.get_all_couples()

    total_mem = stats.get('total_memories', 0)
    total_safe = total_mem or 1
    days = max(stats.get('days_active', 0), 1)
    total_couples = len(couples)
    full_couples = sum(1 for c in couples if c.get('user2_id'))

    now_str = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    lines = []
    lines.append(f"📊 <b>Статистика бота</b>")
    lines.append(f"<i>Обновлено: {now_str}</i>")
    lines.append("")

    lines.append("👥 <b>Пользователи и пары</b>")
    lines.append(f"  • Уникальных пользователей: <b>{stats.get('unique_users', 0)}</b>")
    lines.append(f"  • Пар всего: <b>{total_couples}</b>  |  полных: <b>{full_couples}</b>")
    lines.append(f"  • Устройств: <b>{admin_stats.get('total_devices', 0)}</b>  |  онлайн: <b>{admin_stats.get('online_count', 0)}</b>")
    lines.append(f"  • Визитов на сайт: <b>{admin_stats.get('site_visits', 0)}</b>")
    lines.append("")

    lines.append("📝 <b>Контент</b>")
    lines.append(f"  • Воспоминаний: <b>{total_mem}</b>")
    lines.append(f"  • Событий на дату: <b>{stats.get('scheduled_events_count', 0)}</b>")
    lines.append(f"  • Желаний: <b>{stats.get('wishes_count', 0)}</b>")
    lines.append(f"  • Категорий используется: <b>{stats.get('categories_used', 0)}</b> из {len(config.CATEGORIES)}")
    lines.append("")

    lines.append("📂 <b>По категориям</b>")
    cat_items = []
    for cat_key, cat_info in config.CATEGORIES.items():
        cnt = category_stats.get(cat_key, 0)
        pct = (cnt / total_safe) * 100
        bar_filled = round(pct / 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        cat_items.append((cnt, f"  {cat_info['emoji']} {cat_info['title']}: {cnt} [{bar}] {pct:.0f}%"))
    for _, line in sorted(cat_items, reverse=True):
        lines.append(line)
    lines.append("")

    lines.append("🖼 <b>Медиа</b>")
    lines.append(
        f"  📸 {stats.get('photo_count', 0)} фото  "
        f"🎥 {stats.get('video_count', 0)} видео  "
        f"📹 {stats.get('video_note_count', 0)} кружков"
    )
    lines.append(
        f"  🎤 {stats.get('audio_count', 0)} аудио  "
        f"🎙 {stats.get('voice_count', 0)} голос  "
        f"📄 {stats.get('document_count', 0)} файлов"
    )
    lines.append(f"  📝 Только текст: {stats.get('text_only_count', 0)}")
    lines.append("")

    lines.append("📈 <b>Активность</b>")
    lines.append(f"  • Дней работы: <b>{stats.get('days_active', 0)}</b>")
    avg = total_mem / days
    lines.append(f"  • В среднем в день: <b>{avg:.1f}</b>")
    last_week = stats.get('last_week_count', 0)
    if last_week:
        lines.append(f"  • За последние 7 дней: <b>{last_week}</b> 🔥")
    ai_msgs = admin_stats.get('ai_messages', 0)
    if ai_msgs:
        lines.append(f"  • Сообщений ИИ-спутнику: <b>{ai_msgs}</b>")
    lines.append("")

    my_mem = user_stats.get('total_memories', 0)
    my_pct = (my_mem / total_safe) * 100
    lines.append("🙋 <b>Ты (разработчик)</b>")
    lines.append(f"  • Воспоминаний: <b>{my_mem}</b>  ({my_pct:.1f}% от всех)")
    lines.append(f"  • Категорий: <b>{user_stats.get('categories_count', 0)}</b>")

    return "\n".join(lines)

def extract_username_or_id(input_text: str) -> tuple[bool, Optional[int], Optional[str]]:
    """
    Извлекает user_id или username из текста
    Возвращает: (success, user_id, error_message)
    """
    input_text = input_text.strip()
    
    if input_text.isdigit():
        user_id = int(input_text)
        if user_id <= 0:
            return False, None, "ID должен быть положительным числом"
        return True, user_id, None
    
    if input_text.startswith('t.me/'):
        username = input_text[5:].strip()
        if not username:
            return False, None, "Укажи юзернейм после t.me/"
        return False, None, username
    
    if input_text.startswith('@'):
        username = input_text[1:].strip()
        if not username:
            return False, None, "Укажи юзернейм в формате @юзернейм"
        return False, None, username
    

    if re.match(r'^[a-zA-Z][a-zA-Z0-9_]{4,31}$', input_text):
        return False, None, input_text
    
    return False, None, "❌ Неверный формат, отправь юзернейм или id"

def truncate_text(text: str, max_length: int = 100) -> str:
    """Обрезает текст до указанной длины"""
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[:max_length-3] + "..."
