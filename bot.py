import asyncio
import contextvars
import html
import json
import logging
import os
import subprocess
import urllib.request
from pathlib import Path
from types import MethodType
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, BaseMiddleware, Router, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.strategy import FSMStrategy
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.exceptions import TelegramRetryAfter

from config import config
from handlers import router
from database import db
from constants import CREATOR_TG_LINK, CREATOR_BUTTON_TEXT
from utils import format_error_message_for_user
from http_api import create_app

try:
    from test import FULL_RUN_EXPECTED_TOTAL as TEST_SUITE_EXPECTED_TOTAL
except Exception:
    TEST_SUITE_EXPECTED_TOTAL = 0

STARTUP_TOTAL_CACHE_FILE = Path("/app/data/startup_tests_total.json")


def _load_last_known_tests_total() -> int:
    try:
        if STARTUP_TOTAL_CACHE_FILE.exists():
            payload = json.loads(STARTUP_TOTAL_CACHE_FILE.read_text(encoding="utf-8"))
            val = int((payload or {}).get("total") or 0)
            if val > 0:
                return val
    except Exception:
        pass
    return int(TEST_SUITE_EXPECTED_TOTAL or 0)


def _save_last_known_tests_total(total: int) -> None:
    try:
        total = int(total or 0)
        if total <= 0:
            return
        STARTUP_TOTAL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STARTUP_TOTAL_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"total": total}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STARTUP_TOTAL_CACHE_FILE)
    except Exception:
        pass

logger = logging.getLogger(__name__)
startup_router = Router()

_scheduler_task = None
_cloudflared_process = None
_active_edit_target = contextvars.ContextVar("active_edit_target", default=None)
_startup_test_progress = {
    "running": False,
    "total_expected": _load_last_known_tests_total(),
    "completed": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "section": "Подготовка",
    "current_test": "Ожидание старта",
    "error": "",
    "logs": [],
}
_startup_tests_watch_tasks: dict[tuple[int, int], asyncio.Task] = {}
_startup_tests_view_state: dict[tuple[int, int], dict] = {}

_diagnostic_test_progress = {
    "running": False,
    "total_expected": 9,
    "completed": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "section": "Подготовка",
    "current_test": "Ожидание старта",
    "error": "",
    "logs": [],
}
_diagnostic_tests_watch_tasks: dict[tuple[int, int], asyncio.Task] = {}
_diagnostic_tests_view_state: dict[tuple[int, int], dict] = {}

CHECK_EXPIRED_INTERVAL_SEC = 10


class BotLogFilter(logging.Filter):
    """В bot.log пишем только важное: без aiogram.event INFO про обработку апдейтов."""
    def filter(self, record):
        if record.name == "aiogram.event" and record.levelno == logging.INFO:
            msg = (record.getMessage() or "")
            if "is handled" in msg or "Update id=" in msg:
                return False
        return True


def _setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.addFilter(BotLogFilter())
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(stream_handler)


def _get_external_ip() -> str:
    """Получает внешний IP сервера. При ошибке возвращает fallback."""
    services = [
        "https://ifconfig.me",
        "https://api.ipify.org",
        "https://icanhazip.com",
    ]
    for url in services:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                ip = r.read().decode().strip()
                if ip:
                    logger.info(f"Внешний IP определён через {url}: {ip}")
                    return ip
        except Exception as e:
            logger.warning(f"Не удалось получить IP через {url}: {e}")
    logger.warning("Все сервисы недоступны, используем fallback IP")
    return "185.218.137.132"


def _get_direct_url(external_ip: str) -> str:
    """Возвращает рабочую прямую ссылку на сайт.
    Приоритет:
    1) Явно заданный домен в env (BOT_SITE_URL / SITE_DIRECT_URL)
    На Replit — использует REPLIT_DOMAINS (проксированный HTTPS-домен).
    На VPS — строит http://IP:PORT.
    """
    forced_domain = (getattr(config, "BOT_SITE_URL", "") or getattr(config, "SITE_DIRECT_URL", "") or "").strip()
    if forced_domain:
        return forced_domain.rstrip("/")
    replit_domains = os.environ.get("REPLIT_DOMAINS", "").strip()
    if replit_domains:
        domain = replit_domains.split(",")[0].strip()
        return f"https://{domain}"
    return f"http://{external_ip}:{config.HTTP_PORT}"


def _start_cloudflared(external_ip: str) -> bool:
    """Запускает cloudflared туннель. Возвращает True если успешно."""
    global _cloudflared_process
    if (os.getenv("CLOUDFLARED_MANAGED_EXTERNALLY", "") or "").strip() == "1":
        logger.info("Cloudflared запускается отдельным контейнером, локальный запуск пропущен.")
        return True
    try:
        cloudflared_path = (os.getenv("CLOUDFLARED_PATH", "") or "").strip() or "/app/cloudflared"
        cloudflared_dir = os.path.dirname(cloudflared_path) or "/app"
        cloudflared_bin = os.path.basename(cloudflared_path) or "cloudflared"
        _cloudflared_process = subprocess.Popen(
            [
                f"./{cloudflared_bin}", "tunnel", "run",
                "--url", f"http://{external_ip}:{config.HTTP_PORT}",
                "memories"
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=cloudflared_dir
        )
        logger.info(f"Cloudflared запущен: http://{external_ip}:{config.HTTP_PORT}")
        return True
    except Exception as e:
        logger.warning(f"Не удалось запустить cloudflared: {e}")
        return False


async def _append_deploy_status(lines: list[str]) -> list[str]:
    """Добавляет краткий статус последнего деплоя из deployer /health."""
    deploy_url = (getattr(config, "DEPLOYER_URL", "") or "").strip()
    deploy_secret = (getattr(config, "DEPLOYER_SECRET", "") or "").strip()
    if not deploy_url or not deploy_secret:
        return lines
    try:
        parts = urlsplit(deploy_url)
        deploy_path = parts.path or ""
        health_path = f"{deploy_path[:-7]}/health" if deploy_path.endswith("/deploy") else "/health"
        health_url = urlunsplit((parts.scheme, parts.netloc, health_path, "", ""))
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(health_url, headers={"X-Deploy-Secret": deploy_secret}) as resp:
                if resp.status >= 400:
                    return lines
                payload = await resp.json(content_type=None)
        deploy = (payload or {}).get("deploy") or {}
        if not deploy:
            return lines
        ok = deploy.get("ok")
        recreate = bool(deploy.get("recreated"))
        version = (deploy.get("version") or "unknown").strip()
        old_id = ((deploy.get("old_container_id") or "").strip() or "—")[:12]
        new_id = ((deploy.get("new_container_id") or "").strip() or "—")[:12]
        status = "успешно" if ok is True else "ошибка" if ok is False else "нет данных"
        lines.append(f"🚀 Деплой: {status} | версия={version} | пересоздание={'да' if recreate else 'нет'}")
        lines.append(f"🧱 Контейнер: {old_id} -> {new_id}")
    except Exception as e:
        logger.debug("Не удалось добавить статус deployer в стартовое сообщение: %s", e)
    return lines


def _is_message_not_modified_error(err: Exception) -> bool:
    return "message is not modified" in str(err).lower()


def _is_rate_limit_edit_error(err: Exception) -> bool:
    if isinstance(err, TelegramRetryAfter):
        return True
    msg = str(err).lower()
    return "too many requests" in msg or "flood control" in msg or "retry after" in msg


def _logs_total_pages() -> int:
    logs = _startup_test_progress.get("logs") or []
    completed = int(_startup_test_progress.get("completed") or 0)
    running = bool(_startup_test_progress.get("running"))
    total_for_pages = max(len(logs), completed + (1 if running else 0))
    return max(1, (total_for_pages + 24) // 25)


def _startup_tests_view_keyboard(detailed: bool = False, page: int = 1) -> InlineKeyboardMarkup:
    if detailed:
        rows = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="startup_tests_refresh")]]
        pages = _logs_total_pages()
        page = max(1, min(page, pages))
        if pages > 1:
            btns = []
            for idx in range(1, pages + 1):
                label = f"✅ {idx}" if idx == page else str(idx)
                btns.append(InlineKeyboardButton(text=label, callback_data=f"startup_tests_page_{idx}"))
                if len(btns) == 4:
                    rows.append(btns)
                    btns = []
            if btns:
                rows.append(btns)
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="startup_tests_back")])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧪 Посмотреть тесты", callback_data="startup_tests_view")],
        [InlineKeyboardButton(text="🧪 Глубокая диагностика", callback_data="run_deep_diagnostics")]
    ])


def _split_test_label_and_description(label: str) -> tuple[str, str]:
    raw = (label or "").strip()
    if "→" in raw:
        left, right = raw.split("→", 1)
        return left.strip(), right.strip()
    return raw, "без уточнения"


def _format_startup_tests_block(page: int = 1) -> str:
    st = _startup_test_progress
    total_expected = int(st.get("total_expected") or 0)
    completed = int(st.get("completed") or 0)
    passed = int(st.get("passed") or 0)
    failed = int(st.get("failed") or 0)
    skipped = int(st.get("skipped") or 0)
    section = html.escape((st.get("section") or "—").strip())
    current = html.escape((st.get("current_test") or "—").strip())
    running = bool(st.get("running"))
    error = html.escape((st.get("error") or "").strip())
    logs = st.get("logs") or []
    pages = _logs_total_pages()
    page = max(1, min(page, pages))

    lines = ["🧪 Подробный статус тестов"]
    lines.append(f"• Всего тестов: {total_expected}")
    lines.append(f"• Завершено: {completed}")
    lines.append(f"• Пройдено: {passed}")
    lines.append(f"• Провалено: {failed}")
    lines.append(f"• Пропущено: {skipped}")
    lines.append(f"• Текущий раздел: {section}")
    lines.append(f"• Текущая проверка: {current}")
    lines.append(f"• Состояние: {'выполняются' if running else 'завершены'}")
    if error:
        lines.append(f"• Ошибка: {error}")
    if logs:
        start = (page - 1) * 25
        end = start + 25
        lines.append("")
        lines.append(f"Логи тестов: страница {page}/{pages}")
        for item in logs[start:end]:
            lines.append(html.escape(str(item)))
    return "\n".join(lines)


async def _edit_message_if_changed(message, text: str, reply_markup: InlineKeyboardMarkup):
    try:
        await message.edit_text(
            text=text,
            reply_markup=reply_markup,
            no_fallback_on_edit_error=True,
        )
        return True
    except Exception as e:
        if _is_message_not_modified_error(e):
            return False
        raise


async def _startup_tests_live_watch(chat_id: int, message_id: int, bot: Bot):
    key = (chat_id, message_id)
    idle_cycles = 0
    try:
        while True:
            await asyncio.sleep(5)
            view_state = _startup_tests_view_state.get(key) or {"page": 1}
            page = int(view_state.get("page") or 1)
            txt = _format_startup_tests_block(page=page)
            try:
                changed = await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=txt,
                    reply_markup=_startup_tests_view_keyboard(detailed=True, page=page),
                    no_fallback_on_edit_error=True,
                )
                _ = changed
            except Exception as e:
                if _is_message_not_modified_error(e):
                    pass
                else:
                    logger.debug("Автообновление тестов остановлено: %s", e)
                    break
            is_running = bool(_startup_test_progress.get("running"))
            if is_running:
                idle_cycles = 0
                continue
            idle_cycles += 1
            if idle_cycles >= 3:
                break
    finally:
        task = _startup_tests_watch_tasks.get(key)
        if task is asyncio.current_task():
            _startup_tests_watch_tasks.pop(key, None)


def _ensure_startup_tests_live_watch(chat_id: int, message_id: int, bot: Bot):
    key = (chat_id, message_id)
    task = _startup_tests_watch_tasks.get(key)
    if task and not task.done():
        return
    _startup_tests_view_state.setdefault(key, {"page": 1})
    _startup_tests_watch_tasks[key] = asyncio.create_task(
        _startup_tests_live_watch(chat_id=chat_id, message_id=message_id, bot=bot)
    )


def _stop_startup_tests_live_watch(chat_id: int, message_id: int):
    key = (chat_id, message_id)
    task = _startup_tests_watch_tasks.pop(key, None)
    _startup_tests_view_state.pop(key, None)
    if task and not task.done():
        task.cancel()


# Время последнего уведомления о rate limit — не спамим создателя
_last_rate_limit_notify: float = 0.0
_RATE_LIMIT_NOTIFY_COOLDOWN = 120  # секунд между уведомлениями


class EditTargetMiddleware(BaseMiddleware):
    """Прокидывает в контекст message_id текущего экрана для update/callback."""

    async def __call__(self, handler, event, data):
        target = None
        try:
            cb = getattr(event, "callback_query", None)
            if cb and getattr(cb, "message", None):
                target = {
                    "chat_id": cb.message.chat.id,
                    "message_id": cb.message.message_id,
                }
            else:
                msg = getattr(event, "message", None)
                if msg:
                    target = {
                        "chat_id": msg.chat.id,
                        "message_id": msg.message_id,
                    }
        except Exception as e:
            logger.debug("Не удалось определить target для edit middleware: %s", e)
            target = None

        token = _active_edit_target.set(target)
        try:
            return await handler(event, data)
        finally:
            _active_edit_target.reset(token)


def _extract_retry_after(err: Exception) -> float | None:
    import re
    from aiogram.exceptions import TelegramRetryAfter
    if isinstance(err, TelegramRetryAfter):
        return err.retry_after
    msg = str(err).lower()
    m = re.search(r"retry after\s*(\d+)", msg)
    if m:
        return float(m.group(1))
    m_sec = re.search(r"too many requests.*?(\d+)\s*sec", msg)
    if m_sec:
        return float(m_sec.group(1))
    return None


def _install_resilient_message_delivery(bot: Bot):
    """
    Глобальная стратегия отправки текста:
    1) пытаемся edit последнего сообщения бота в чате
    2) если не вышло — пытаемся удалить старое
    3) отправляем новое
    """
    _orig_send_message = bot.send_message
    _orig_send_photo = bot.send_photo
    _orig_send_document = bot.send_document
    _orig_send_video = bot.send_video
    _orig_send_animation = bot.send_animation
    _orig_send_voice = bot.send_voice
    _orig_send_audio = bot.send_audio
    _orig_send_sticker = bot.send_sticker
    _orig_send_video_note = bot.send_video_note
    _orig_send_location = bot.send_location
    _orig_send_venue = bot.send_venue
    _orig_send_contact = bot.send_contact
    _orig_send_poll = bot.send_poll
    _orig_edit_message_text = bot.edit_message_text
    _orig_edit_message_reply_markup = bot.edit_message_reply_markup
    _orig_edit_message_caption = bot.edit_message_caption
    _orig_edit_message_media = bot.edit_message_media
    _orig_delete_message = bot.delete_message
    _last_bot_msg: dict[int | str, int] = {}

    async def _safe_send_new(chat_id, text, **kwargs):
        msg = await _orig_send_message(chat_id=chat_id, text=text, **kwargs)
        try:
            if msg and getattr(msg, "message_id", None):
                _last_bot_msg[chat_id] = msg.message_id
        except Exception as e:
            logger.debug("Не удалось сохранить id отправленного сообщения (safe_send_new): %s", e)
        return msg

    def _track_sent_message(chat_id, msg):
        try:
            if chat_id is not None and msg and getattr(msg, "message_id", None):
                _last_bot_msg[chat_id] = msg.message_id
        except Exception as e:
            logger.debug("Не удалось сохранить id отправленного сообщения (track_sent_message): %s", e)
        return msg

    async def _tracked_sender(orig_method, self, *args, **kwargs):
        msg = await orig_method(*args, **kwargs)
        chat_id = kwargs.get("chat_id")
        if chat_id is None and args:
            chat_id = args[0]
        return _track_sent_message(chat_id, msg)

    async def _resilient_edit(orig_edit_method, *args, **kwargs):
        try:
            return await orig_edit_method(*args, **kwargs)
        except Exception as err:
            if _is_message_not_modified_error(err):
                logger.debug("edit failed: message is not modified")
                return True
            retry_after = _extract_retry_after(err)
            if retry_after is not None:
                logger.warning("Telegram rate limit hit. Sleeping for %s seconds before retrying edit...", retry_after)
                await asyncio.sleep(retry_after)
                return await _resilient_edit(orig_edit_method, *args, **kwargs)
            raise

    async def resilient_send_message(self, chat_id, text, **kwargs):
        force_new = kwargs.pop("force_new_message", False)
        # Для reply-потоков/тредов оставляем обычную отправку.
        if force_new or kwargs.get("reply_to_message_id") or kwargs.get("message_thread_id"):
            return await _safe_send_new(chat_id, text, **kwargs)

        def _build_edit_target():
            ctx_target = _active_edit_target.get()
            if ctx_target and ctx_target.get("chat_id") == chat_id:
                return ctx_target.get("message_id")
            return _last_bot_msg.get(chat_id)

        edit_target_id = _build_edit_target()
        if edit_target_id:
            try:
                edited = await _resilient_edit(
                    _orig_edit_message_text,
                    chat_id=chat_id,
                    message_id=edit_target_id,
                    text=text,
                    reply_markup=kwargs.get("reply_markup"),
                    parse_mode=kwargs.get("parse_mode"),
                    entities=kwargs.get("entities"),
                    disable_web_page_preview=kwargs.get("disable_web_page_preview"),
                    link_preview_options=kwargs.get("link_preview_options"),
                )
                return edited
            except Exception as e:
                try:
                    await _orig_delete_message(chat_id=chat_id, message_id=edit_target_id)
                except Exception as delete_err:
                    logger.debug("Не удалось удалить предыдущее сообщение при fallback edit->send: %s", delete_err)
                _last_bot_msg.pop(chat_id, None)

        return await _safe_send_new(chat_id, text, **kwargs)

    async def resilient_edit_message_text(self, text, chat_id=None, message_id=None, **kwargs):
        # Явный edit из хендлеров: если не удалось — delete + send new.
        no_fallback_on_edit_error = bool(kwargs.pop("no_fallback_on_edit_error", False))
        try:
            edited = await _resilient_edit(
                _orig_edit_message_text,
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                **kwargs,
            )
            if chat_id and message_id:
                _last_bot_msg[chat_id] = message_id
            return edited
        except Exception as edit_err:
            if no_fallback_on_edit_error:
                logger.debug(
                    "edit_message_text: fallback отключен, сохраняем исходное сообщение (chat_id=%s, message_id=%s): %s",
                    chat_id,
                    message_id,
                    edit_err,
                )
                return True
            logger.debug("edit_message_text failed, fallback to delete+send: %s", edit_err)
            if chat_id and message_id:
                try:
                    await _orig_delete_message(chat_id=chat_id, message_id=message_id)
                except Exception as delete_err:
                    logger.debug("Не удалось удалить сообщение после failed edit: %s", delete_err)
            if chat_id is None:
                raise
            return await _safe_send_new(chat_id=chat_id, text=text, **kwargs)

    async def resilient_edit_message_reply_markup(self, chat_id=None, message_id=None, **kwargs):
        return await _resilient_edit(_orig_edit_message_reply_markup, chat_id=chat_id, message_id=message_id, **kwargs)

    async def resilient_edit_message_caption(self, chat_id=None, message_id=None, **kwargs):
        return await _resilient_edit(_orig_edit_message_caption, chat_id=chat_id, message_id=message_id, **kwargs)

    async def resilient_edit_message_media(self, chat_id=None, message_id=None, **kwargs):
        return await _resilient_edit(_orig_edit_message_media, chat_id=chat_id, message_id=message_id, **kwargs)

    bot.send_message = MethodType(resilient_send_message, bot)
    bot.edit_message_text = MethodType(resilient_edit_message_text, bot)
    bot.edit_message_reply_markup = MethodType(resilient_edit_message_reply_markup, bot)
    bot.edit_message_caption = MethodType(resilient_edit_message_caption, bot)
    bot.edit_message_media = MethodType(resilient_edit_message_media, bot)
    bot.send_photo = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_photo, self, *a, **k), bot)
    bot.send_document = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_document, self, *a, **k), bot)
    bot.send_video = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_video, self, *a, **k), bot)
    bot.send_animation = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_animation, self, *a, **k), bot)
    bot.send_voice = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_voice, self, *a, **k), bot)
    bot.send_audio = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_audio, self, *a, **k), bot)
    bot.send_sticker = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_sticker, self, *a, **k), bot)
    bot.send_video_note = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_video_note, self, *a, **k), bot)
    bot.send_location = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_location, self, *a, **k), bot)
    bot.send_venue = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_venue, self, *a, **k), bot)
    bot.send_contact = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_contact, self, *a, **k), bot)
    bot.send_poll = MethodType(lambda self, *a, **k: _tracked_sender(_orig_send_poll, self, *a, **k), bot)


async def _global_error_handler(event, bot: Bot):
    """При любой необработанной ошибке отправляем пользователю сообщение и кнопку «Написать»."""
    import time as _time
    from aiogram.types import ErrorEvent
    from aiogram.exceptions import TelegramRetryAfter
    global _last_rate_limit_notify

    if not isinstance(event, ErrorEvent):
        return
    exc = event.exception
    update = event.update

    # ── Rate limit от Telegram — уведомляем создателя ────────────────
    if isinstance(exc, TelegramRetryAfter):
        retry_secs = getattr(exc, 'retry_after', '?')
        logger.warning("Telegram rate limit (RetryAfter %ss)", retry_secs)
        now = _time.monotonic()
        if now - _last_rate_limit_notify > _RATE_LIMIT_NOTIFY_COOLDOWN:
            _last_rate_limit_notify = now
            try:
                await bot.send_message(
                    chat_id=config.CREATOR_ID,
                    text=(
                        f"⚠️ <b>Rate Limit от Telegram</b>\n\n"
                        f"Бот временно ограничен. Ожидание: <b>{retry_secs} сек.</b>\n"
                        f"Пользователи могут временно не получать ответы."
                    ),
                    parse_mode=ParseMode.HTML,
                )
            except Exception as notify_err:
                logger.warning("Не удалось отправить уведомление о rate limit создателю: %s", notify_err)
        return  # не показываем пользователю ошибку rate limit

    chat_id = None
    try:
        if update.message:
            chat_id = update.message.chat.id
        elif update.callback_query and update.callback_query.message:
            chat_id = update.callback_query.message.chat.id
    except Exception as e:
        logger.debug("Не удалось извлечь chat_id из update в global error handler: %s", e)
    logger.exception("Необработанная ошибка в боте: %s", exc)
    if chat_id is not None:
        try:
            text = format_error_message_for_user(exc)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=CREATOR_BUTTON_TEXT, url=CREATOR_TG_LINK)]
            ])
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        except Exception as send_err:
            logger.exception("Не удалось отправить сообщение об ошибке пользователю: %s", send_err)


async def _check_expired_events(bot):
    """Периодически проверяет истёкшие события и отправляет уведомления всем парам."""
    from database import db
    from utils import is_scheduled_event_expired
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    from aiogram.enums import ParseMode

    while True:
        try:
            pending_pairs = db.get_scheduled_events_unnotified_for_couples()
            for event, pending_users in pending_pairs:
                try:
                    title = (event.title or "").strip()
                    text = f"⏰ Событие <b>{title}</b> наступило! 🎉"
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="👀 Посмотреть событие",
                                    callback_data=f"scheduled_event_{event.id}",
                                )
                            ]
                        ]
                    )
                    for user_id in pending_users:
                        dt_to_check = getattr(event, "original_event_datetime", None) or event.event_datetime
                        if not is_scheduled_event_expired(dt_to_check, user_id):
                            continue
                        if not db.are_notifications_enabled(user_id):
                            db.mark_event_notified_for_user(event.id, user_id)
                            continue
                        
                        import asyncio as _asyncio_debug
                        import time as _time_debug
                        _task_debug = _asyncio_debug.current_task()
                        _task_id = id(_task_debug) if _task_debug else "none"
                        _task_name = _task_debug.get_name() if _task_debug else "none"
                        _notified_before = None
                        try:
                            with db._get_connection() as _conn_debug:
                                _row_debug = _conn_debug.execute(
                                    'SELECT notified_at FROM event_notifications WHERE event_id = ? AND user_id = ?',
                                    (event.id, user_id)
                                ).fetchone()
                                _notified_before = _row_debug[0] if _row_debug else None
                        except Exception as _e_debug:
                            logger.error(f"Debug logging error before: {_e_debug}")
                        
                        logger.info(
                            "[DEBUG EVENT NOTIFICATION] TASK_ID=%s (%s) EVENT_ID=%s USER_ID=%s TS=%s NOTIFIED_BEFORE=%s",
                            _task_id, _task_name, event.id, user_id, _time_debug.strftime('%Y-%m-%d %H:%M:%S'), _notified_before
                        )
                        
                        try:
                            await bot.send_message(
                                chat_id=user_id,
                                text=text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                                force_new_message=True,
                            )
                            db.mark_event_notified_for_user(event.id, user_id)
                            
                            _notified_after = None
                            try:
                                with db._get_connection() as _conn_debug:
                                    _row_debug = _conn_debug.execute(
                                        'SELECT notified_at FROM event_notifications WHERE event_id = ? AND user_id = ?',
                                        (event.id, user_id)
                                    ).fetchone()
                                    _notified_after = _row_debug[0] if _row_debug else None
                            except Exception as _e_debug:
                                logger.error(f"Debug logging error after: {_e_debug}")
                            
                            logger.info(
                                "[DEBUG EVENT NOTIFICATION SUCCESS] TASK_ID=%s (%s) EVENT_ID=%s USER_ID=%s TS=%s NOTIFIED_AFTER=%s",
                                _task_id, _task_name, event.id, user_id, _time_debug.strftime('%Y-%m-%d %H:%M:%S'), _notified_after
                            )
                        except Exception as e:
                            err_str = str(e).lower()
                            if "chat not found" in err_str or "user not found" in err_str or "bot was blocked" in err_str:
                                logger.debug(f"Пропуск уведомления пользователю {user_id}: чат не найден")
                                db.mark_event_notified_for_user(event.id, user_id)
                            else:
                                logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
                except Exception as e:
                    logger.error(f"Ошибка при уведомлении об истёкшем событии {event.id}: {e}")

            # Проверяем праздничные анимации
            try:
                from http_api import _check_and_fire_celebrations
                await _check_and_fire_celebrations()
            except Exception as _ce:
                logger.debug("Celebration check error: %s", _ce)

            await asyncio.sleep(CHECK_EXPIRED_INTERVAL_SEC)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Ошибка в _check_expired_events: {e}")


async def _run_tests_background(bot, chat_id: int, msg_id, site_url: str):
    """Запускает тест-сьют в фоне и обновляет стартовое сообщение."""
    if msg_id is None:
        return
    await asyncio.sleep(5)  # Ждём, пока HTTP-сервер поднимется
    try:
        from test import run_all_tests
        _startup_test_progress.update({
            "running": True,
            "completed": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "total_expected": _load_last_known_tests_total(),
            "section": "Подготовка",
            "current_test": "Запуск тестового набора",
            "error": "",
            "logs": [],
        })

        def _on_test_progress(payload: dict):
            ok = payload.get("ok")
            mark = "✅" if ok is True else "⚠️" if ok is None else "❌"
            test_name, test_desc = _split_test_label_and_description(payload.get("current_test") or "")
            detail = (payload.get("detail") or "").strip()
            suffix = f" ({detail})" if detail else ""
            log_line = f"{mark} {test_name} - {test_desc}{suffix}"
            logs = list(_startup_test_progress.get("logs") or [])
            logs.append(log_line)
            completed = int(payload.get("completed") or 0)
            hinted_total = int(payload.get("total_expected") or 0)
            current_total = int(_startup_test_progress.get("total_expected") or 0)
            # Базовое "всего" берём из последнего подтверждённого полного прогона.
            # Если в текущем прогоне тестов окажется больше — расширяем автоматически.
            effective_total = max(current_total, hinted_total, completed, _load_last_known_tests_total())
            _startup_test_progress.update({
                "running": True,
                "total_expected": effective_total,
                "completed": completed,
                "passed": payload.get("passed", 0),
                "failed": payload.get("failed", 0),
                "skipped": payload.get("skipped", 0),
                "section": payload.get("section") or "—",
                "current_test": payload.get("current_test") or "—",
                "logs": logs,
            })

        # load_tests=True — полный прогон; localhost в whitelist, не банит реальных пользователей
        result = await run_all_tests(load_tests=True, progress_callback=_on_test_progress)
        passed  = result["passed"]
        failed  = result["failed"]
        total   = result["total"]
        skipped = result["skipped"]
        _startup_test_progress.update({
            "running": False,
            "completed": total,
            "total_expected": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "current_test": "Тесты завершены",
        })
        _save_last_known_tests_total(total)

        lines = ["🌐 Сайт и бот запущен"]
        if config.BOT_SITE_URL:
            lines.append(f"🔗 Домен: {config.BOT_SITE_URL}")
        lines.append(f"📎 Ссылка: {site_url}")
        lines = await _append_deploy_status(lines)
        lines.append(f"✅ Тесты: {passed}/{total}")
        if failed:
            lines.append(f"❌ Провалено: {failed}")
        if skipped:
            lines.append(f"⚠️ Пропущено: {skipped}")
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="\n".join(lines),
            reply_markup=_startup_tests_view_keyboard(detailed=False),
        )
        logger.info(f"Тесты завершены: {passed}/{total} пройдено, {failed} провалено")
    except Exception as e:
        logger.warning(f"Ошибка при запуске тестов: {e}")
        _startup_test_progress.update({
            "running": False,
            "error": str(e),
            "current_test": "Ошибка запуска тестов",
        })
        try:
            lines = ["🌐 Сайт и бот запущен"]
            if config.BOT_SITE_URL:
                lines.append(f"🔗 Домен: {config.BOT_SITE_URL}")
            lines.append(f"📎 Ссылка: {site_url}")
            lines = await _append_deploy_status(lines)
            lines.append(f"⚠️ Тесты не запустились: {e}")
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="\n".join(lines),
                reply_markup=_startup_tests_view_keyboard(detailed=False),
            )
        except Exception:
            pass


@startup_router.callback_query(F.data == "startup_tests_view")
async def startup_tests_view(callback: CallbackQuery):
    if callback.from_user.id != config.CREATOR_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    key = (callback.message.chat.id, callback.message.message_id)
    _startup_tests_view_state[key] = {"page": 1}
    _ensure_startup_tests_live_watch(callback.message.chat.id, callback.message.message_id, callback.bot)
    changed = await _edit_message_if_changed(
        callback.message,
        text=_format_startup_tests_block(page=1),
        reply_markup=_startup_tests_view_keyboard(detailed=True, page=1),
    )
    await callback.answer("Автообновление включено" if changed else "Автообновление уже работает")


@startup_router.callback_query(F.data == "startup_tests_refresh")
async def startup_tests_refresh(callback: CallbackQuery):
    if callback.from_user.id != config.CREATOR_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    key = (callback.message.chat.id, callback.message.message_id)
    page = int((_startup_tests_view_state.get(key) or {}).get("page") or 1)
    changed = await _edit_message_if_changed(
        callback.message,
        text=_format_startup_tests_block(page=page),
        reply_markup=_startup_tests_view_keyboard(detailed=True, page=page),
    )
    await callback.answer("Статус обновлён" if changed else "Данные не изменились")


@startup_router.callback_query(F.data.startswith("startup_tests_page_"))
async def startup_tests_page(callback: CallbackQuery):
    if callback.from_user.id != config.CREATOR_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    try:
        page = int((callback.data or "").rsplit("_", 1)[-1])
    except Exception:
        await callback.answer("Неверная страница", show_alert=True)
        return
    key = (callback.message.chat.id, callback.message.message_id)
    pages = _logs_total_pages()
    page = max(1, min(page, pages))
    _startup_tests_view_state[key] = {"page": page}
    changed = await _edit_message_if_changed(
        callback.message,
        text=_format_startup_tests_block(page=page),
        reply_markup=_startup_tests_view_keyboard(detailed=True, page=page),
    )
    await callback.answer(f"Страница {page}" if changed else "Данные не изменились")


@startup_router.callback_query(F.data == "startup_tests_back")
async def startup_tests_back(callback: CallbackQuery):
    if callback.from_user.id != config.CREATOR_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    if not callback.message:
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    _stop_startup_tests_live_watch(callback.message.chat.id, callback.message.message_id)
    lines = ["🌐 Сайт и бот запущен"]
    if config.BOT_SITE_URL:
        lines.append(f"🔗 Домен: {config.BOT_SITE_URL}")
    direct_url = (getattr(config, "SITE_DIRECT_URL", "") or "").strip()
    if direct_url:
        lines.append(f"📎 Ссылка: {direct_url}")
    st = _startup_test_progress
    if st.get("running"):
        lines.append("⌛ Выполняю тесты...")
        lines.append(f"🧪 Прогресс: {st.get('completed', 0)}/{st.get('total_expected', 0)}")
    else:
        lines.append(
            f"✅ Тесты: {int(st.get('passed', 0))}/{int(st.get('total_expected') or st.get('completed', 0) or 0)}"
        )
        if int(st.get("failed", 0)) > 0:
            lines.append(f"❌ Провалено: {int(st.get('failed', 0))}")
        if int(st.get("skipped", 0)) > 0:
            lines.append(f"⚠️ Пропущено: {int(st.get('skipped', 0))}")
    changed = await _edit_message_if_changed(
        callback.message,
        text="\n".join(lines),
        reply_markup=_startup_tests_view_keyboard(detailed=False),
    )
    await callback.answer("Возврат выполнен" if changed else "Данные не изменились")


async def on_startup(bot):
    global _scheduler_task

    # Rollback Branch Recovery Check: Auto-restore detached HEAD to main branch
    try:
        from app_version import _get_repo_root, get_git_commit
        import subprocess
        from database import db
        repo_root = _get_repo_root()
        proc_ref = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_ref.returncode == 0 and proc_ref.stdout.strip() == "HEAD" and db.get_setting("rollback_active") != "1":
            logger.info("Rollback: Detached HEAD detected on host workspace. Restoring to main branch...")
            subprocess.run(["git", "checkout", "main"], cwd=repo_root, check=True)
            logger.info("Rollback: Successfully restored host workspace to main branch.")
            
        # Stale state cleanup
        if db.get_setting("rollback_active") == "1":
            target_commit = db.get_setting("rollback_target_commit")
            running_commit = get_git_commit()
            if target_commit and running_commit and running_commit[:8] != target_commit[:8]:
                logger.info(f"Rollback: Stale state detected. Running commit ({running_commit[:8]}) does not match target ({target_commit[:8]}). Clearing DB.")
                db.delete_setting("rollback_active")
                db.delete_setting("rollback_previous_commit")
                db.delete_setting("rollback_previous_version")
                db.delete_setting("rollback_target_commit")
    except Exception as e:
        logger.error(f"Rollback Branch Recovery Hook error: {e}")

    # Определяем внешний IP в отдельном потоке чтобы не блокировать event loop
    external_ip = await asyncio.get_event_loop().run_in_executor(None, _get_external_ip)

    # Сохраняем IP глобально в config
    config.EXTERNAL_IP = external_ip

    # Определяем рабочую прямую ссылку (Replit-домен или http://IP:PORT на VPS)
    direct_url = _get_direct_url(external_ip)
    config.SITE_DIRECT_URL = direct_url

    # Запускаем cloudflared туннель (только на VPS, на Replit не нужен)
    tunnel_ok = _start_cloudflared(external_ip)
    if tunnel_ok:
        await asyncio.sleep(4)

    # Fetch bot username dynamically
    try:
        me = await bot.get_me()
        config.BOT_USERNAME = me.username
    except Exception as e:
        logger.error(f"Failed to fetch bot username dynamically: {e}")

    # Уведомление о запуске
    startup_msg_id = None
    try:
        lines = ["🌐 Сайт и бот запущен"]
        if config.BOT_SITE_URL:
            lines.append(f"🔗 Домен: {config.BOT_SITE_URL}")
        lines.append(f"📎 Ссылка: {direct_url}")
        lines = await _append_deploy_status(lines)
        lines.append("⌛ Выполняю тесты...")
        msg = await bot.send_message(
            chat_id=config.CREATOR_ID,
            text="\n".join(lines),
            reply_markup=_startup_tests_view_keyboard(detailed=False),
        )
        startup_msg_id = msg.message_id
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление о запуске: {e}")

    # Если на старте сработал self-heal БД, отправляем экстренное уведомление создателю.
    try:
        recovery_alert = db.consume_recovery_alert()
        if recovery_alert:
            await bot.send_message(
                chat_id=config.CREATOR_ID,
                text=recovery_alert,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        logger.warning("Не удалось отправить DB self-heal alert создателю: %s", e)

    _scheduler_task = asyncio.create_task(_check_expired_events(bot))

    # Тесты запускаем в фоне — localhost в whitelist, реальных пользователей не трогают
    asyncio.create_task(
        _run_tests_background(bot, config.CREATOR_ID, startup_msg_id, direct_url)
    )


async def on_shutdown():
    """Функция, выполняемая при выключении бота"""
    global _scheduler_task, _cloudflared_process

    logger.info("Бот выключается...")

    # Останавливаем планировщик
    if _scheduler_task:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass

    # Останавливаем cloudflared
    if _cloudflared_process:
        try:
            _cloudflared_process.terminate()
            logger.info("Cloudflared остановлен")
        except Exception as e:
            logger.warning(f"Не удалось остановить cloudflared: {e}")

    stats = db.get_total_stats()
    logger.info(f"Статистика: {stats}")


async def main():
    """Основная функция запуска бота"""
    _setup_logging()

    http_app = create_app()
    http_runner = web.AppRunner(http_app)

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    _install_resilient_message_delivery(bot)

    storage = MemoryStorage()
    dp = Dispatcher(
        storage=storage,
        fsm_strategy=FSMStrategy.USER_IN_CHAT
    )

    dp.update.middleware(EditTargetMiddleware())

    # Middleware для записи активности в боте
    from handlers import BotActivityMiddleware
    dp.update.middleware(BotActivityMiddleware())
    dp.include_router(startup_router)
    dp.include_router(router)
    dp.error.register(_global_error_handler)

    async def _run_startup():
        await on_startup(bot)

    dp.startup.register(_run_startup)
    dp.shutdown.register(on_shutdown)

    try:
        # Запускаем HTTP сервер
        await http_runner.setup()
        http_site = web.TCPSite(http_runner, config.HTTP_HOST, config.HTTP_PORT)
        await http_site.start()
        logger.info(f"HTTP API запущен на http://{config.HTTP_HOST}:{config.HTTP_PORT}")

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Начинаю поллинг...")

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            polling_timeout=30
        )

    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

    finally:
        try:
            await http_runner.cleanup()
        except Exception as e:
            logger.error(f"Ошибка при остановке HTTP API: {e}")

        await bot.session.close()
        logger.info("Бот остановлен")


# ==============================================================================
# DEEP DIAGNOSTICS LAYER
# ==============================================================================

def _logs_total_pages_diag() -> int:
    logs = _diagnostic_test_progress.get("logs") or []
    completed = int(_diagnostic_test_progress.get("completed") or 0)
    running = bool(_diagnostic_test_progress.get("running"))
    total_for_pages = max(len(logs), completed + (1 if running else 0))
    return max(1, (total_for_pages + 24) // 25)

def _diagnostic_tests_view_keyboard(page: int = 1) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔄 Обновить", callback_data="diag_tests_refresh")]]
    pages = _logs_total_pages_diag()
    page = max(1, min(page, pages))
    if pages > 1:
        btns = []
        for idx in range(1, pages + 1):
            label = f"✅ {idx}" if idx == page else str(idx)
            btns.append(InlineKeyboardButton(text=label, callback_data=f"diag_tests_page_{idx}"))
            if len(btns) == 4:
                rows.append(btns)
                btns = []
        if btns:
            rows.append(btns)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="diag_tests_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _format_diagnostic_tests_block(page: int = 1) -> str:
    total = _diagnostic_test_progress.get("total_expected", 9)
    completed = _diagnostic_test_progress.get("completed", 0)
    passed = _diagnostic_test_progress.get("passed", 0)
    failed = _diagnostic_test_progress.get("failed", 0)
    skipped = _diagnostic_test_progress.get("skipped", 0)
    logs = _diagnostic_test_progress.get("logs", [])
    running = _diagnostic_test_progress.get("running", False)

    pages = _logs_total_pages_diag()
    page = max(1, min(page, pages))

    lines = ["🔬 <b>Глубокая диагностика</b>\n"]
    if running:
        lines.append(f"⏳ Выполняется: {completed}/{total}")
        lines.append(f"Текущий тест: {_diagnostic_test_progress.get('current_test', 'Загрузка...')}\n")
    else:
        lines.append(f"✅ Успешно: {passed}/{total}")
        if failed:
            lines.append(f"❌ Провалено: {failed}")
        lines.append(f"⚠️ Пропущено: {skipped}\n")
        lines.append("Проверено:\n")

    start_idx = (page - 1) * 25
    end_idx = start_idx + 25
    page_logs = logs[start_idx:end_idx]

    for log in page_logs:
        lines.append(log)

    return "\n".join(lines)

async def _diagnostic_tests_live_watch(chat_id: int, message_id: int, bot: Bot):
    key = (chat_id, message_id)
    last_text = ""
    try:
        while _diagnostic_test_progress.get("running", False):
            await asyncio.sleep(2.0)
            view_state = _diagnostic_tests_view_state.get(key) or {"page": 1}
            page = int(view_state.get("page", 1))
            txt = _format_diagnostic_tests_block(page=page)
            if txt != last_text:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=txt,
                        reply_markup=_diagnostic_tests_view_keyboard(page=page),
                        parse_mode="HTML"
                    )
                    last_text = txt
                except Exception as e:
                    if _is_message_not_modified_error(e):
                        pass
                    elif _is_rate_limit_edit_error(e):
                        await asyncio.sleep(5.0)
                    else:
                        logger.error(f"Error updating diag test progress UI: {e}")
    finally:
        task = _diagnostic_tests_watch_tasks.get(key)
        if task and task == asyncio.current_task():
            _diagnostic_tests_watch_tasks.pop(key, None)

def _ensure_diagnostic_tests_live_watch(chat_id: int, message_id: int, bot: Bot):
    key = (chat_id, message_id)
    task = _diagnostic_tests_watch_tasks.get(key)
    if task and not task.done():
        return
    _diagnostic_tests_view_state.setdefault(key, {"page": 1})
    _diagnostic_tests_watch_tasks[key] = asyncio.create_task(
        _diagnostic_tests_live_watch(chat_id=chat_id, message_id=message_id, bot=bot)
    )

def _stop_diagnostic_tests_live_watch(chat_id: int, message_id: int):
    key = (chat_id, message_id)
    task = _diagnostic_tests_watch_tasks.pop(key, None)
    _diagnostic_tests_view_state.pop(key, None)
    if task and not task.done():
        task.cancel()

async def _run_deep_diagnostics_background(bot, chat_id: int, msg_id: int):
    _diagnostic_test_progress.update({
        "running": True,
        "completed": 0,
        "passed": 0,
        "failed": 0,
        "logs": [],
        "current_test": "Инициализация...",
    })

    _ensure_diagnostic_tests_live_watch(chat_id, msg_id, bot)

    test_scripts = [
        ("diag_release_workflow.py", "Release Infrastructure"),
        ("diag_rollback_workflow.py", "Rollback Infrastructure"),
        ("diag_startup_hook.py", "Startup Hook"),
        ("test_auth.py", "Auth"),
        ("test_version_ux.py", "Version UX"),
        ("test_health_url.py", "Health Checks"),
        ("test_app_version_writer.py", "App Version Writer"),
        ("test_rollback_fallback.py", "Rollback Fallback"),
        ("test_rollback_state.py", "Rollback State"),
        ("diag_load_test.py", "Load Testing"),
    ]
    
    _diagnostic_test_progress["total_expected"] = len(test_scripts)
    
    passed = 0
    failed = 0
    
    from app_version import _get_repo_root
    import sys
    repo_root = _get_repo_root()

    for script, label in test_scripts:
        _diagnostic_test_progress["current_test"] = label
        
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, script,
                cwd=repo_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode == 0:
                passed += 1
                _diagnostic_test_progress["logs"].append(f"✅ {label}")
            else:
                failed += 1
                _diagnostic_test_progress["logs"].append(f"❌ {label}")
                logger.error(f"Diagnostics {label} failed:\n{stdout.decode('utf-8', errors='replace')}")
        except Exception as e:
            failed += 1
            _diagnostic_test_progress["logs"].append(f"❌ {label} (Error)")
            logger.error(f"Diagnostics {label} threw exception: {e}")
            
        _diagnostic_test_progress["passed"] = passed
        _diagnostic_test_progress["failed"] = failed
        _diagnostic_test_progress["completed"] += 1

    _diagnostic_test_progress["running"] = False
    _diagnostic_test_progress["current_test"] = "Завершено"

    _stop_diagnostic_tests_live_watch(chat_id, msg_id)

    lines = ["🔬 <b>Глубокая диагностика завершена</b>\n"]
    lines.append(f"✅ Успешно: {passed}/{len(test_scripts)}")
    if failed:
        lines.append(f"❌ Провалено: {failed}")
    lines.append("⚠️ Пропущено: 0\n")
    lines.append("Проверено:\n")
    
    for log in _diagnostic_test_progress["logs"]:
        lines.append(log)

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧪 Посмотреть детали", callback_data="diag_tests_view")]
            ]),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to send final diagnostic report: {e}")

@startup_router.callback_query(F.data == "run_deep_diagnostics")
async def run_deep_diagnostics(callback: CallbackQuery):
    if not db.is_creator(callback.from_user.id):
        await callback.answer("Отказано в доступе", show_alert=True)
        return
        
    if _diagnostic_test_progress.get("running"):
        await callback.answer("Диагностика уже выполняется", show_alert=True)
        return
        
    await callback.answer("Запуск глубокой диагностики...")
    
    msg = await callback.message.answer("Подготовка к диагностике...")
    
    asyncio.create_task(_run_deep_diagnostics_background(callback.bot, msg.chat.id, msg.message_id))

@startup_router.callback_query(F.data == "diag_tests_view")
async def diag_tests_view(callback: CallbackQuery):
    key = (callback.message.chat.id, callback.message.message_id)
    _diagnostic_tests_view_state[key] = {"page": 1}
    _ensure_diagnostic_tests_live_watch(callback.message.chat.id, callback.message.message_id, callback.bot)
    try:
        await callback.message.edit_text(
            text=_format_diagnostic_tests_block(page=1),
            reply_markup=_diagnostic_tests_view_keyboard(page=1),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(str(e))

@startup_router.callback_query(F.data == "diag_tests_refresh")
async def diag_tests_refresh(callback: CallbackQuery):
    key = (callback.message.chat.id, callback.message.message_id)
    page = int((_diagnostic_tests_view_state.get(key) or {}).get("page") or 1)
    try:
        await callback.message.edit_text(
            text=_format_diagnostic_tests_block(page=page),
            reply_markup=_diagnostic_tests_view_keyboard(page=page),
            parse_mode="HTML"
        )
        await callback.answer("Обновлено")
    except Exception as e:
        if _is_message_not_modified_error(e):
            await callback.answer("Нет новых изменений")
        else:
            await callback.answer(str(e))

@startup_router.callback_query(F.data.startswith("diag_tests_page_"))
async def diag_tests_page(callback: CallbackQuery):
    try:
        page = int(callback.data.split("_")[-1])
    except ValueError:
        page = 1
    key = (callback.message.chat.id, callback.message.message_id)
    _diagnostic_tests_view_state[key] = {"page": page}
    try:
        await callback.message.edit_text(
            text=_format_diagnostic_tests_block(page=page),
            reply_markup=_diagnostic_tests_view_keyboard(page=page),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        if _is_message_not_modified_error(e):
            await callback.answer()
        else:
            await callback.answer(str(e))

@startup_router.callback_query(F.data == "diag_tests_back")
async def diag_tests_back(callback: CallbackQuery):
    key = (callback.message.chat.id, callback.message.message_id)
    _stop_diagnostic_tests_live_watch(callback.message.chat.id, callback.message.message_id)
    
    passed = _diagnostic_test_progress.get("passed", 0)
    failed = _diagnostic_test_progress.get("failed", 0)
    total = _diagnostic_test_progress.get("total_expected", 9)
    
    lines = ["🔬 <b>Глубокая диагностика завершена</b>\n"]
    lines.append(f"✅ Успешно: {passed}/{total}")
    if failed:
        lines.append(f"❌ Провалено: {failed}")
    lines.append("⚠️ Пропущено: 0\n")
    lines.append("Проверено:\n")
    
    for log in _diagnostic_test_progress.get("logs", []):
        lines.append(log)

    try:
        await callback.message.edit_text(
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🧪 Посмотреть детали", callback_data="diag_tests_view")]
            ]),
            parse_mode="HTML"
        )
        await callback.answer()
    except Exception as e:
        await callback.answer(str(e))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
