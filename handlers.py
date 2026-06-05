import io
import json
import os
import re
import asyncio
import logging
import html
import time
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
import aiohttp

from aiogram import Router, F
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, FSInputFile
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode, ContentType, ChatAction

from config import config
from constants import (
    MSG_ACCESS_DENIED, MSG_ACCESS_DENIED_OR_FAVORITES_OFF, MSG_ACCESS_DENIED_CREATOR,
    MSG_MEMORY_NOT_FOUND, MSG_EVENT_NOT_FOUND, MSG_WISH_NOT_FOUND, MSG_ADMIN_NOT_FOUND,
)
from database import db, Memory, ScheduledEvent
from api import parse_date_with_ai, parse_timezone_with_ai, parse_timezone_with_ai_details
from utils import (
    save_media_file, format_memory_text, truncate_text, text_and_entities_to_html,
    sanitize_html_for_telegram,
    create_main_keyboard, create_categories_keyboard, create_category_keyboard, create_category_keyboard_paged,
    get_all_categories,
    create_search_results_keyboard,
    get_text_for_other_users,
    create_memory_detail_keyboard, create_confirmation_keyboard,
    create_admin_delete_confirm_keyboard, create_edit_options_keyboard,
    create_admin_keyboard, create_admin_list_keyboard, create_admin_detail_keyboard,
    create_cancel_adding_keyboard, format_welcome_message, format_stats_message,
    format_admin_details, format_admin_short_line, validate_title, validate_date, validate_content,
    get_params_help_text, substitute_params,
    create_broadcast_target_keyboard,
    is_wishes_available, format_wish_text, create_wishes_menu_keyboard,
    create_wishes_menu_keyboard_creator, create_wishes_menu_keyboard_partner,
    create_wish_user_keyboard, create_wish_admin_keyboard,
    create_wish_keyboard,
    WISH_STATUS_LABELS,
    send_wish_with_media,
    create_settings_menu_keyboard, create_settings_time_keyboard,
    create_settings_notifications_keyboard, create_settings_favorites_keyboard,
    create_notif_cat_keyboard, build_notif_main_text, build_notif_cat_text,
    get_timezone_label, get_timezone_label_for_display, get_notifications_label, TIMEZONE_OPTIONS,
    format_visit_telegram_message,
    create_favorites_menu_keyboard, create_favorites_add_menu_keyboard,
    format_scheduled_event_text, format_scheduled_event_datetime,
    create_scheduled_events_menu_keyboard, create_scheduled_event_detail_keyboard,
    create_scheduled_event_confirm_keyboard, create_scheduled_event_edit_options_keyboard,
    create_scheduled_event_recurrence_keyboard,
    create_scheduled_event_existing_edit_keyboard, create_scheduled_event_delete_confirm_keyboard,
    send_scheduled_event_with_media,
    parse_ai_date_to_db,
    get_user_datetime_context,
    format_time_remaining,
    is_scheduled_event_expired,
    is_scheduled_event_moment_passed,
    safe_delete_message,
)

router = Router()
EXPECTED_DEPLOY_SERVICES = ["ksysha-bot", "ksysha-cloudflared"]
RESTART_STATUS_PATH = Path("/app/data/restart_status.json")
RESTART_PID_PATH = Path("/app/data/restart_runner.pid")
RESTART_SCRIPT_PATH = Path("/app/scripts/restart_clean.sh")
RESTART_ROOT_DIR = "/workspace"
RESTART_STATUS_FILE_IN_RUNNER = "/workspace/data/restart_status.json"
RESTART_RUNNER_PREFIX = "ksysha-restart-runner-"
RESTART_RUNNER_IMAGE = "ksyshatest-ksysha-bot:latest"
RESTART_HOST_ROOT_DIR = "/root/KsyshaTest"
RESTART_STUCK_SECONDS = 420
PDF_EXPORTS_DIR = Path("/tmp/couple_exports")


def _humanize_iso_utc(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw == "—":
        return "—"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        return dt.strftime("%d.%m.%Y %H:%M:%S UTC")
    except Exception:
        return raw


def _format_deploy_report(deploy_state: dict) -> str:
    ok_raw = deploy_state.get("ok")
    ok = bool(ok_raw)
    old_id = (deploy_state.get("old_container_id") or "—").strip() or "—"
    new_id = (deploy_state.get("new_container_id") or "—").strip() or "—"
    recreated = bool(deploy_state.get("recreated"))
    duration_ms = int(deploy_state.get("duration_ms") or 0)
    duration_sec = duration_ms / 1000.0
    version = (deploy_state.get("version") or "неизвестно").strip()
    started_iso = _humanize_iso_utc((deploy_state.get("started_at") or "—").strip())
    finished_iso = _humanize_iso_utc((deploy_state.get("finished_at") or "—").strip())
    log_tail = (deploy_state.get("log") or "").strip()
    containers = deploy_state.get("containers") or {}
    total = int(containers.get("total") or 0)
    running = int(containers.get("running") or 0)
    missing_expected = containers.get("missing_expected") or []
    expected = containers.get("expected") or EXPECTED_DEPLOY_SERVICES[:]
    details = containers.get("details") or []
    details_by_name = {
        ((item.get("name") or "").strip()): item
        for item in details
        if (item.get("name") or "").strip()
    }
    data_incomplete = (
        not containers
        or (total == 0 and running == 0 and not details)
        or (not containers.get("expected") and not details)
    )

    if ok_raw is True:
        status_line = "✅ Деплой завершён успешно."
    elif ok_raw is False:
        status_line = "❌ Деплой завершился с ошибкой."
    else:
        status_line = "ℹ️ Статус деплоя пока недоступен."
    status_line += "\nИсточник: Deployer сервис"
    recreate_line = "да" if recreated else "нет"
    containers_error = (containers.get("error") or "").strip()
    if containers_error:
        important_line = "Не удалось получить список контейнеров от Docker."
    elif data_incomplete:
        important_line = "Данные о контейнерах пока неполные."
    else:
        important_line = (
            "Все ключевые контейнеры на месте."
            if not missing_expected
            else f"Не найдены контейнеры: {', '.join(missing_expected)}."
        )
    detail_lines = []
    ordered_names = expected if expected else list(details_by_name.keys())
    for name in ordered_names:
        item = details_by_name.get(name)
        if not item:
            detail_lines.append(f"  • {name}: статус временно недоступен, нажми «🔄 Обновить статус»")
            continue
        state = "запущен" if item.get("running") else "не запущен"
        status = (item.get("status") or "статус неизвестен").strip()
        image = (item.get("image") or "образ неизвестен").strip()
        detail_lines.append(f"  • {name}: {state} ({status}), образ: {image}")
    expected_line = ", ".join(expected) if expected else "не задано"
    if containers_error:
        docker_state_block = (
            "Состояние Docker сейчас:\n"
            "• Данные Docker: недоступны\n"
            f"• Причина: {containers_error}\n"
            f"• Ожидаемые сервисы: {expected_line}"
        )
    elif data_incomplete:
        docker_state_block = (
            "Состояние Docker сейчас:\n"
            "• Данные Docker: неполные\n"
            f"• Запущено контейнеров: {running} из {total}\n"
            f"• Ожидаемые сервисы: {expected_line}\n"
            "• Действие: нажми «🔄 Обновить статус» через 3-5 секунд"
        )
    else:
        docker_state_block = (
            "Состояние Docker сейчас:\n"
            f"• Запущено контейнеров: {running} из {total}\n"
            f"• Ожидаемые сервисы: {expected_line}\n"
            f"• Проверка: {important_line}"
        )

    quick_state = "норма" if (not containers_error and not missing_expected and running > 0) else "требует проверки"
    report = (
        f"{status_line}\n"
        f"Итог: состояние системы {quick_state}.\n"
        f"Что произошло:\n"
        f"• Версия кода: {version}\n"
        f"• Контейнер пересоздан: {recreate_line}\n"
        f"• Старый ID контейнера: {old_id[:12] if old_id != '—' else old_id}\n"
        f"• Новый ID контейнера: {new_id[:12] if new_id != '—' else new_id}\n"
        f"• Начало обновления: {started_iso}\n"
        f"• Завершение: {finished_iso}\n"
        f"• Длительность: {duration_sec:.2f} сек.\n\n"
        f"{docker_state_block}"
    )
    if detail_lines:
        report += "\n• Детали по контейнерам:\n" + "\n".join(detail_lines)

    report += "\n\nДля повторной проверки нажми «🔄 Обновить статус»."
    if ok_raw is False and log_tail:
        report += f"\n\nХвост лога деплоя:\n{log_tail[-700:]}"
    return report

# ── Middleware: записываем последнюю активность в боте ───────────────────────
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from typing import Callable, Awaitable, Any as TypingAny

class BotActivityMiddleware(BaseMiddleware):
    """Записывает last_active в БД при каждом сообщении или callback от известных пользователей."""

    # Описание действия по callback-префиксу
    _ACTION_MAP = {
        "category_":        "просматривает категорию",
        "view_memory_":     "просматривает воспоминание",
        "edit_memory_":     "редактирует воспоминание",
        "delete_memory_":   "удаляет воспоминание",
        "event_":           "смотрит события",
        "wish_":            "смотрит желания",
        "favorites":        "просматривает избранное",
        "stats":            "смотрит статистику",
        "admin":            "в панели управления",
        "search":           "использует поиск",
        "add_memory":       "добавляет воспоминание",
        "main_menu":        "в главном меню",
        "site_link":        "переходит на сайт",
    }
    _ALLOWED_UNAUTHORIZED_STATE_PREFIXES = (
        "CoupleOnboardingStates:",
    )
    _ALLOWED_WAITING_PARTNER_CALLBACKS = (
        "get_invite_link",
    )

    @staticmethod
    def _is_authorized_user(user_id: int) -> bool:
        # Полный доступ к функционалу бота только для админов
        # или пользователей, состоящих в полной паре (оба партнёра).
        return bool(
            db.is_admin(user_id)
            or db.is_in_couple(user_id)
        )

    @staticmethod
    def _is_waiting_for_partner(user_id: int) -> bool:
        # Пользователь прошёл онбординг, но партнёр ещё не присоединился.
        return bool(
            db.is_user_onboarded(user_id)
            and not db.is_in_couple(user_id)
        )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict], Awaitable[TypingAny]],
        event: TelegramObject,
        data: dict,
    ) -> TypingAny:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        from aiogram.enums import ParseMode
        user_id = None
        action = ""

        if isinstance(event, Update):
            if event.callback_query and event.callback_query.from_user:
                user_id = event.callback_query.from_user.id
                cb_data = event.callback_query.data or ""
                action = "нажал кнопку"
                for prefix, label in BotActivityMiddleware._ACTION_MAP.items():
                    if cb_data.startswith(prefix) or cb_data == prefix:
                        action = label
                        break
            elif event.message and event.message.from_user:
                user_id = event.message.from_user.id
                text = (event.message.text or "").strip()
                if text.startswith("/"):
                    action = f"команда {text.split()[0]}"
                elif text:
                    action = "отправил сообщение"
                else:
                    action = "прислал медиафайл"
            elif event.edited_message and event.edited_message.from_user:
                user_id = event.edited_message.from_user.id
                action = "отредактировал сообщение"
            elif event.inline_query and event.inline_query.from_user:
                user_id = event.inline_query.from_user.id
                action = "использовал инлайн-поиск"
            elif event.chosen_inline_result and event.chosen_inline_result.from_user:
                user_id = event.chosen_inline_result.from_user.id
                action = "выбрал инлайн-результат"
            elif event.shipping_query and event.shipping_query.from_user:
                user_id = event.shipping_query.from_user.id
                action = "оформил доставку"
            elif event.pre_checkout_query and event.pre_checkout_query.from_user:
                user_id = event.pre_checkout_query.from_user.id
                action = "оплачивает"
            elif event.poll_answer and event.poll_answer.user:
                user_id = event.poll_answer.user.id
                action = "ответил в опросе"
            elif event.my_chat_member and event.my_chat_member.from_user:
                user_id = event.my_chat_member.from_user.id
                action = "изменил статус бота"
            elif event.chat_member and event.chat_member.from_user:
                user_id = event.chat_member.from_user.id
                action = "изменил статус в чате"
            elif event.chat_join_request and event.chat_join_request.from_user:
                user_id = event.chat_join_request.from_user.id
                action = "запрос на вступление"

        is_allowed_unauthorized_state = False
        if isinstance(event, Update):
            current_state = data.get("state")
            state_name = ""
            if current_state:
                try:
                    state_name = await current_state.get_state() or ""
                except Exception as e:
                    logger.debug("Не удалось получить FSM state: %s", e)
                    state_name = ""

            is_allowed_unauthorized_state = any(
                state_name.startswith(prefix)
                for prefix in self._ALLOWED_UNAUTHORIZED_STATE_PREFIXES
            ) if state_name else False

        if user_id:
            try:
                # Логируем активность для любого пользователя (пары или legacy)
                db.update_bot_last_active(user_id, action)
            except Exception as e:
                logger.debug("Не удалось обновить last_active user_id=%s: %s", user_id, e)

            if isinstance(event, Update):
                message_or_call = event.message or getattr(event, 'callback_query', None)
                if message_or_call and getattr(message_or_call, "data", "") != "rebound_create_new":
                    msg_text = getattr(message_or_call, "text", "") or ""
                    if not msg_text.startswith("/start invite_") and not is_allowed_unauthorized_state:
                        rebound_info = db.get_rebound_account(user_id)
                        if rebound_info:
                            new_user_id = rebound_info["user_id"]
                            new_first_name = rebound_info.get("first_name") or "нового аккаунта"
                            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                            from aiogram.enums import ParseMode
                            import html
                            
                            safe_name = html.escape(new_first_name)
                            text = (
                                f"🔐 Этот аккаунт перепривязан к <a href='tg://user?id={new_user_id}'>{safe_name}</a>. "
                                f"Хочешь создать новый аккаунт?"
                            )
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🫪 Создать", callback_data="rebound_create_new")]
                            ])
                            
                            if event.message:
                                await event.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
                            elif event.callback_query:
                                await event.callback_query.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
                                await event.callback_query.answer()
                            return

        # Жесткий gate: если пользователь не авторизован, блокируем любой доступ
        # к функционалу кроме /start (вход/привязка пары).
        if isinstance(event, Update):
            message = event.message
            callback = event.callback_query

            if message and message.from_user:
                msg_user_id = message.from_user.id
                msg_text = (message.text or "").strip()
                is_start = msg_text.lower().startswith("/start")

                if (not is_start) and (not is_allowed_unauthorized_state) and (not self._is_authorized_user(msg_user_id)):
                    # Пользователь онбордился, но партнёр ещё не присоединился —
                    # показываем напоминание с инвайт-ссылкой.
                    if self._is_waiting_for_partner(msg_user_id):
                        if msg_text:
                            kb = InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🔗 Получить ссылку-приглашение", callback_data="get_invite_link")]
                            ])
                            await message.answer(
                                "⏳ Твой партнёр ещё не присоединился.\n\n"
                                "Отправь ему ссылку-приглашение, чтобы начать пользоваться ботом вместе!",
                                reply_markup=kb,
                                parse_mode=ParseMode.HTML,
                            )
                        return

                    # Полностью новый пользователь — запускаем онбординг.
                    try:
                        db.add_or_update_user(
                            msg_user_id,
                            message.from_user.username,
                            message.from_user.first_name,
                            message.from_user.last_name,
                        )
                        if current_state:
                            await current_state.update_data(creating_couple=True)
                            await current_state.set_state(CoupleOnboardingStates.waiting_for_name)
                    except Exception as e:
                        logger.debug("Не удалось включить онбординг для неавторизованного user_id=%s: %s", msg_user_id, e)

                    if msg_text:
                        first_name = message.from_user.first_name or "друг"
                        skip_kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_name")]
                        ])
                        await message.answer(
                            f"👋 Привет, <b>{first_name}</b>!\n\n"
                            "Создай пару с своим партнёром и вместе создавайте, изменяйте и делитесь моментами!\n\n"
                            "Для начала — как тебя зовут?",
                            reply_markup=skip_kb,
                            parse_mode=ParseMode.HTML,
                        )
                    return

            if callback and callback.from_user:
                cb_user_id = callback.from_user.id
                cb_data = callback.data or ""

                if (not is_allowed_unauthorized_state) and (not self._is_authorized_user(cb_user_id)):
                    # Пользователь ждёт партнёра — разрешаем только get_invite_link
                    if self._is_waiting_for_partner(cb_user_id):
                        if any(cb_data.startswith(allowed) for allowed in self._ALLOWED_WAITING_PARTNER_CALLBACKS):
                            return await handler(event, data)
                        try:
                            await callback.answer(
                                "⏳ Партнёр ещё не присоединился. Отправь ему ссылку-приглашение!",
                                show_alert=True,
                            )
                        except Exception as e:
                            logger.debug("Не удалось показать alert ожидающему партнёра: %s", e)
                        return

                    try:
                        await callback.answer(
                            "⛔️ Доступ не активирован. Напиши /start",
                            show_alert=True,
                        )
                    except Exception as e:
                        logger.debug("Не удалось показать alert неавторизованному пользователю: %s", e)
                    return

        return await handler(event, data)

logger = logging.getLogger(__name__)


def _format_tz_with_now(tz_id: str, display_name: Optional[str] = None) -> str:
    """Формирует удобную подпись TZ с текущим временем пользователя."""
    label = display_name.strip() if isinstance(display_name, str) and display_name.strip() else tz_id
    try:
        from zoneinfo import ZoneInfo
        now_str = datetime.now(ZoneInfo(tz_id)).strftime("%H:%M")
        return f"{label} ({tz_id}, сейчас {now_str})"
    except Exception:
        return f"{label} ({tz_id})"


def _parse_timezone_local_flexible(user_input: str) -> dict:
    """
    Гибко распознаёт TZ без AI:
    - прямой IANA (`Europe/Moscow`)
    - IANA с другим регистром / пробелами
    - популярные имена городов (ru/en, любой регистр)
    - эвристика по последнему сегменту IANA
    """
    from zoneinfo import ZoneInfo, available_timezones

    raw = (user_input or "").strip()
    if not raw:
        return {}

    norm = re.sub(r"\s+", " ", raw).strip()
    compact = norm.replace(" ", "_")
    candidates = [norm, compact, norm.title().replace(" ", "_")]

    for cand in candidates:
        try:
            ZoneInfo(cand)
            return {"timezone": cand, "display_name": cand.replace("_", " ")}
        except Exception:
            pass

    # UTC/GMT offset forms: UTC+3, GMT-5, UTC + 5:30, и т.п.
    offset_match = re.search(
        r"(?i)\b(?:utc|gmt)\s*([+-])\s*(\d{1,2})(?:[:.](\d{1,2}))?\b",
        norm,
    )
    if offset_match:
        sign = offset_match.group(1)
        hours = int(offset_match.group(2))
        minutes = int(offset_match.group(3) or "0")
        if 0 <= hours <= 14 and minutes in (0, 30, 45):
            # Для целых часов используем Etc/GMT (у него инвертированный знак в имени).
            if minutes == 0:
                if sign == "+":
                    tz = f"Etc/GMT-{hours}"
                else:
                    tz = f"Etc/GMT+{hours}"
                if hours == 0:
                    tz = "Etc/GMT"
                try:
                    ZoneInfo(tz)
                    return {"timezone": tz, "display_name": f"UTC {sign}{hours}"}
                except Exception:
                    pass
            # Популярные дробные смещения.
            frac_map = {
                ("+", 30, 3): "Asia/Tehran",
                ("+", 30, 4): "Asia/Kabul",
                ("+", 30, 5): "Asia/Kolkata",
                ("+", 30, 9): "Australia/Darwin",
                ("+", 30, 10): "Australia/Adelaide",
                ("+", 45, 5): "Asia/Kathmandu",
                ("+", 45, 8): "Australia/Eucla",
                ("+", 45, 12): "Pacific/Chatham",
                ("-", 30, 3): "America/St_Johns",
            }
            tz = frac_map.get((sign, minutes, hours))
            if tz:
                return {"timezone": tz, "display_name": f"UTC {sign}{hours}:{minutes:02d}"}

    alias_map = {
        "moscow": "Europe/Moscow", "москва": "Europe/Moscow",
        "saint petersburg": "Europe/Moscow", "санкт петербург": "Europe/Moscow",
        "st petersburg": "Europe/Moscow", "st petersburg ru": "Europe/Moscow",
        "петербург": "Europe/Moscow", "спб": "Europe/Moscow",
        "spb": "Europe/Moscow", "питер": "Europe/Moscow",
        "yekaterinburg": "Asia/Yekaterinburg", "екатеринбург": "Asia/Yekaterinburg",
        "novosibirsk": "Asia/Novosibirsk", "новосибирск": "Asia/Novosibirsk",
        "vladivostok": "Asia/Vladivostok", "владивосток": "Asia/Vladivostok",
        "almaty": "Asia/Almaty", "алматы": "Asia/Almaty",
        "bishkek": "Asia/Bishkek", "бишкек": "Asia/Bishkek",
        "minsk": "Europe/Minsk", "минск": "Europe/Minsk",
        "kyiv": "Europe/Kyiv", "kiev": "Europe/Kyiv", "киев": "Europe/Kyiv",
        "tbilisi": "Asia/Tbilisi", "тбилиси": "Asia/Tbilisi",
        "yerevan": "Asia/Yerevan", "ереван": "Asia/Yerevan",
        "berlin": "Europe/Berlin", "берлин": "Europe/Berlin",
        "paris": "Europe/Paris", "париж": "Europe/Paris",
        "london": "Europe/London", "лондон": "Europe/London",
        "new york": "America/New_York", "нью йорк": "America/New_York",
        "rio de janeiro": "America/Sao_Paulo", "рио де жанейро": "America/Sao_Paulo",
        "los angeles": "America/Los_Angeles", "лос анджелес": "America/Los_Angeles",
        "chicago": "America/Chicago", "чикаго": "America/Chicago",
        "toronto": "America/Toronto", "торонто": "America/Toronto",
        "dubai": "Asia/Dubai", "дубай": "Asia/Dubai",
        "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "дели": "Asia/Kolkata",
        "tokyo": "Asia/Tokyo", "токио": "Asia/Tokyo",
        "seoul": "Asia/Seoul", "сеул": "Asia/Seoul",
        "shanghai": "Asia/Shanghai", "шанхай": "Asia/Shanghai",
        "beijing": "Asia/Shanghai", "пекин": "Asia/Shanghai",
        "sydney": "Australia/Sydney", "сидней": "Australia/Sydney",
        "perth": "Australia/Perth", "перт": "Australia/Perth", "пёрт": "Australia/Perth",
    }
    low = norm.lower()
    key_low = re.sub(r"[_\-]+", " ", low)
    key_low = re.sub(r"\s+", " ", key_low).strip()
    if key_low in alias_map:
        tz = alias_map[key_low]
        return {"timezone": tz, "display_name": norm}

    normalized = re.sub(r"[._\-]+", " ", low)
    normalized = re.sub(r"[^a-z0-9а-яё/ ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in alias_map:
        tz = alias_map[normalized]
        return {"timezone": tz, "display_name": norm}

    tz_set = available_timezones()
    if "/" not in norm:
        token = normalized.replace(" ", "_")
        for tz in tz_set:
            if tz.lower().endswith(f"/{token}"):
                return {"timezone": tz, "display_name": norm}

    return {}

async def callback_edit_or_answer(callback: CallbackQuery, text: str, **kwargs):
    """Приоритетно редактирует текущее сообщение callback, fallback — отправка нового."""
    msg = callback.message
    if msg:
        try:
            result = await callback.bot.edit_message_text(
                chat_id=msg.chat.id,
                message_id=msg.message_id,
                text=text,
                **kwargs,
            )
            setattr(msg, "_edited_via_callback", True)
            return result
        except Exception as e:
            logger.debug("edit_message_text in callback failed, trying edit caption: %s", e)
            # Если сообщение медиа, пробуем обновить caption.
            if kwargs.get("reply_markup") is not None:
                try:
                    result = await callback.bot.edit_message_caption(
                        chat_id=msg.chat.id,
                        message_id=msg.message_id,
                        caption=text,
                        reply_markup=kwargs.get("reply_markup"),
                        parse_mode=kwargs.get("parse_mode"),
                        caption_entities=kwargs.get("entities"),
                    )
                    setattr(msg, "_edited_via_callback", True)
                    return result
                except Exception as cap_err:
                    logger.debug("edit_message_caption fallback failed: %s", cap_err)
    return await callback.message.answer(text, **kwargs)


async def safe_delete_callback_message(callback: CallbackQuery):
    """Удаляет callback.message только если она не была успешно отредактирована."""
    msg = callback.message
    if not msg:
        return
    if getattr(msg, "_edited_via_callback", False):
        return
    await safe_delete_message(msg)


async def _is_duplicate_media_group(state: FSMContext, message: Message, key: str) -> bool:
    """Возвращает True, если это повторное сообщение из того же media_group в рамках шага FSM."""
    mgid = getattr(message, "media_group_id", None)
    if not mgid:
        return False
    data = await state.get_data()
    if data.get(key) == mgid:
        return True
    await state.update_data(**{key: mgid})
    return False


class OnboardingStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()


class CoupleOnboardingStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_met_date_raw = State()
    waiting_for_met_date_confirm = State()


class AddMemoryStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_date = State()
    waiting_for_content = State()
    waiting_for_voice_description = State()


@router.message(AddMemoryStates.waiting_for_voice_description)
async def process_voice_description_text(message: Message, state: FSMContext):
    """Получаем текстовое описание для голосового/видео-сообщения."""
    data = await state.get_data()
    user_id = data.get("pending_user_id") or message.from_user.id
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    content = text_and_entities_to_html(message.text or "", message.entities or [])
    media_type = data.get("pending_media_type")
    media_file_id = data.get("pending_media_file_id")
    media_path = data.get("pending_media_path")
    category_key = data.get("pending_category")
    title = data.get("pending_title") or ""
    date = data.get("pending_date") or ""

    db.add_or_update_user(
        message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    memory_id = db.add_memory(
        user_id=user_id,
        category=category_key,
        title=title,
        date=date,
        content=content,
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
    )
    if memory_id == -1:
        await message.answer("❌ Ошибка при сохранении воспоминания")
        await state.clear()
        return

    memory = db.get_memory(memory_id)
    success_text = "🎉 <b>Момент успешно добавлен!</b>\n\n" + format_memory_text(memory, user_id)
    _couple = db.get_couple_by_user(user_id)
    _all_cats = get_all_categories(_couple['id'] if _couple else None)
    category = _all_cats.get(category_key, {"title": "момент"})
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📌 Посмотреть добавленное",
            callback_data=f"memory_{memory_id}"
        )],
        [InlineKeyboardButton(
            text=f"🔙 Назад к {category['title']}",
            callback_data=f"back_to_category_{category_key}"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_main"
        )]
    ])

    await send_message_with_media(
        chat_id=message.chat.id,
        bot=message.bot,
        memory=memory,
        text=success_text,
        keyboard=keyboard,
    )

    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = (
                f"✨ {user_name} добавил(а) новый момент\n\n"
                + format_memory_text(memory, partner_id)
            )
            await message.bot.send_message(
                chat_id=partner_id,
                text=notify_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{memory.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о новом моменте: {e}")

    await state.clear()


@router.callback_query(F.data == "voice_desc_no")
async def voice_desc_no(callback: CallbackQuery, state: FSMContext):
    """Пользователь не хочет добавлять описание к голосовому/видео-сообщению."""
    data = await state.get_data()
    user_id = data.get("pending_user_id") or callback.from_user.id
    media_type = data.get("pending_media_type")
    media_file_id = data.get("pending_media_file_id")
    media_path = data.get("pending_media_path")
    category_key = data.get("pending_category")
    title = data.get("pending_title") or ""
    date = data.get("pending_date") or ""

    db.add_or_update_user(
        callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
    )
    memory_id = db.add_memory(
        user_id=user_id,
        category=category_key,
        title=title,
        date=date,
        content="",
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
    )
    if memory_id == -1:
        await callback_edit_or_answer(callback, "❌ Ошибка при сохранении воспоминания")
        await state.clear()
        await callback.answer()
        return

    memory = db.get_memory(memory_id)
    success_text = "🎉 <b>Момент успешно добавлен!</b>\n\n" + format_memory_text(memory, user_id)
    _couple = db.get_couple_by_user(user_id)
    _all_cats = get_all_categories(_couple['id'] if _couple else None)
    category = _all_cats.get(category_key, {"title": "момент"})
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📌 Посмотреть добавленное",
            callback_data=f"memory_{memory_id}"
        )],
        [InlineKeyboardButton(
            text=f"🔙 Назад к {category['title']}",
            callback_data=f"back_to_category_{category_key}"
        )],
        [InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back_to_main"
        )]
    ])

    await send_message_with_media(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        memory=memory,
        text=success_text,
        keyboard=keyboard,
    )

    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = (
                f"✨ {user_name} добавил(а) новый момент\n\n"
                + format_memory_text(memory, partner_id)
            )
            await callback.bot.send_message(
                chat_id=partner_id,
                text=notify_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{memory.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о новом моменте: {e}")

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "voice_desc_yes")
async def voice_desc_yes(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет добавить описание к голосовому/видео-сообщению."""
    data = await state.get_data()
    if not data.get("pending_media_file_id"):
        await callback.answer("Нет ожидающего медиа, добавь момент заново.")
        await state.clear()
        return
    await callback_edit_or_answer(callback, 
        "📝 Напиши описание для этого момента.\n\n"
        "Поддерживается форматирование: <b>жирный</b>, <i>курсив</i>, ссылки и т.д.",
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()

class EditMemoryStates(StatesGroup):
    waiting_for_new_title = State()
    waiting_for_new_date = State()
    waiting_for_new_content = State()
    waiting_for_new_privacy_limit = State()
    waiting_for_privacy_question = State()
    waiting_for_privacy_answer = State()

class AdminStates(StatesGroup):
    waiting_for_admin_input = State()
    waiting_for_broadcast_content = State()
    waiting_for_wish_delete_reason = State()
    waiting_for_release_description = State()

class WishesStates(StatesGroup):
    waiting_for_wish_content = State()

class SearchStates(StatesGroup):
    waiting_for_search_query = State()


class ViewMemoryStates(StatesGroup):
    waiting_for_password = State()

class TimezoneStates(StatesGroup):
    waiting_for_input = State()


@router.message(ViewMemoryStates.waiting_for_password)
async def process_memory_password(message: Message, state: FSMContext):
    """Проверка ответа на вопрос для доступа по паролю."""
    data = await state.get_data()
    memory_id = data.get("memory_id")
    if not memory_id:
        await state.clear()
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await state.clear()
        await message.answer(MSG_MEMORY_NOT_FOUND)
        return
    privacy = db.get_memory_privacy(memory_id)
    stored_answer = (privacy.get("privacy_answer") or "").strip()
    user_answer = (message.text or "").strip()
    if not stored_answer:
        await state.clear()
        await message.answer("❌ Для этого момента не найден пароль. Попробуй ещё раз позже.")
        return
    if not db.verify_privacy_answer(stored_answer, user_answer):
        question = privacy.get("privacy_question") or "Ответь на вопрос для доступа к этому моменту."
        await message.answer(
            "❌ Неверный ответ\n\n"
            "Попробуй ещё раз\n\n"
            f"❓ Вопрос:\n<b>{question}</b>\n\n"
            "💬 Напиши ответ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML,
        )
        return

    # Пароль верный — показываем момент без дополнительных ограничений
    user_id = message.from_user.id
    await state.clear()
    try:
        await message.delete()
    except Exception as e:
        logger.debug("Не удалось удалить сообщение с ответом на privacy вопрос: %s", e)
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    text = format_memory_text(memory, user_id)
    await send_message_with_media(
        chat_id=message.chat.id,
        bot=message.bot,
        memory=memory,
        text=text,
        keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=user_id),
    )


class AddScheduledEventStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_date_raw = State()
    waiting_for_confirm = State()
    waiting_for_edit_choice = State()
    waiting_for_edit_title = State()
    waiting_for_edit_description = State()
    waiting_for_edit_date = State()
    waiting_for_recurrence = State()


class EditScheduledEventStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_description = State()
    waiting_for_date_raw = State()
    waiting_for_date_confirm = State()


class ScheduledEventSearchStates(StatesGroup):
    waiting_for_query = State()
    viewing_results = State()


class FavoritesSearchStates(StatesGroup):
    waiting_for_query = State()


class AddCategoryStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()


async def save_media(message: Message) -> tuple:
    """Сохраняет медиа из сообщения - поддерживает все типы"""
    media_type = None
    media_file_id = None
    media_path = None
    
    try:
        if message.photo:
            media_type = "photo"
            media_file_id = message.photo[-1].file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            media_path = save_media_file(file_bytes.read(), "jpg")
            
        elif message.video:
            media_type = "video"
            media_file_id = message.video.file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            extension = "mp4"
            if message.video.mime_type:
                if "gif" in message.video.mime_type:
                    extension = "gif"
                elif "webm" in message.video.mime_type:
                    extension = "webm"
            media_path = save_media_file(file_bytes.read(), extension)
            
        elif message.video_note:
            media_type = "video_note"
            media_file_id = message.video_note.file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            media_path = save_media_file(file_bytes.read(), "mp4")
            
        elif message.voice:
            media_type = "voice"
            media_file_id = message.voice.file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            media_path = save_media_file(file_bytes.read(), "ogg")
            
        elif message.audio:
            media_type = "audio"
            media_file_id = message.audio.file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            extension = "mp3"
            if message.audio.mime_type:
                if "ogg" in message.audio.mime_type:
                    extension = "ogg"
                elif "wav" in message.audio.mime_type:
                    extension = "wav"
            media_path = save_media_file(file_bytes.read(), extension)
            
        elif message.document:
            media_type = "document"
            media_file_id = message.document.file_id
            file = await message.bot.get_file(media_file_id)
            file_bytes = await message.bot.download_file(file.file_path)
            extension = message.document.file_name.split('.')[-1] if message.document.file_name else 'bin'
            media_path = save_media_file(file_bytes.read(), extension)
    
    except Exception as e:
        logger.error(f"Ошибка при сохранении медиа: {e}")
    
    return media_type, media_file_id, media_path


async def _finalize_memory_creation(
    message: Message,
    state: FSMContext,
    data: dict,
    user_id: int,
    content: str,
    media_type: Optional[str],
    media_file_id: Optional[str],
    media_path: Optional[str],
    media_items: Optional[list] = None,
)-> Optional[Memory]:
    db.add_or_update_user(
        message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
    )
    memory_id = db.add_memory(
        user_id=user_id,
        category=data['category'],
        title=data['title'],
        date=data['date'],
        content=content,
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
        media_items=media_items,
    )
    if memory_id == -1:
        await message.answer("❌ Ошибка при сохранении воспоминания")
        return None

    memory = db.get_memory(memory_id)
    success_text = "🎉 <b>Момент успешно добавлен!</b>\n\n" + format_memory_text(memory, user_id)
    _couple_for_cat = db.get_couple_by_user(user_id)
    _all_cats = get_all_categories(_couple_for_cat['id'] if _couple_for_cat else None)
    category = _all_cats.get(data['category'], {'title': data['category']})
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📌 Посмотреть добавленное", callback_data=f"memory_{memory_id}")],
        [InlineKeyboardButton(text=f"🔙 Назад к {category['title']}", callback_data=f"back_to_category_{data['category']}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await send_message_with_media(
        chat_id=message.chat.id,
        bot=message.bot,
        memory=memory,
        text=success_text,
        keyboard=keyboard
    )
    await state.clear()
    return memory

async def send_message_with_media(
    chat_id: int,
    bot,
    memory: Memory,
    text: str,
    keyboard: InlineKeyboardMarkup
) -> int:
    """Отправляет медиа (если есть), затем описание момента отдельным сообщением."""
    async def _send_media_item(mt: str, fid: str, mp: str) -> None:
        if not fid and not mp:
            return
        source = fid if fid else FSInputFile(mp)
        if mt == "photo":
            await bot.send_photo(chat_id=chat_id, photo=source)
        elif mt == "video":
            await bot.send_video(chat_id=chat_id, video=source)
        elif mt == "video_note":
            await bot.send_video_note(chat_id=chat_id, video_note=source)
        elif mt == "voice":
            await bot.send_voice(chat_id=chat_id, voice=source)
        elif mt == "audio":
            await bot.send_audio(chat_id=chat_id, audio=source)
        else:
            await bot.send_document(chat_id=chat_id, document=source)

    media_items = list(getattr(memory, "media_items", None) or [])
    if media_items:
        # Жёсткий лимит безопасности
        media_items = media_items[:6]
        for item in media_items:
            mt = (item.get("type") or "").strip()
            fid = (item.get("file_id") or "").strip()
            mp = (item.get("path") or "").strip()
            if not mt:
                continue
            if not fid and (not mp or not os.path.exists(mp)):
                continue
            await _send_media_item(mt, fid, mp)
        text_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return text_msg.message_id

    if not (memory.media_type and memory.media_file_id):
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return msg.message_id

    try:
        if memory.media_type == "photo":
            await bot.send_photo(
                chat_id=chat_id,
                photo=memory.media_file_id,
            )
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id

        if memory.media_type == "video":
            await bot.send_video(
                chat_id=chat_id,
                video=memory.media_file_id,
            )
            msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            return msg.message_id

        if memory.media_type == "video_note":
            await bot.send_video_note(
                chat_id=chat_id,
                video_note=memory.media_file_id,
            )
        elif memory.media_type == "voice":
            await bot.send_voice(
                chat_id=chat_id,
                voice=memory.media_file_id,
            )
        elif memory.media_type == "audio":
            await bot.send_audio(
                chat_id=chat_id,
                audio=memory.media_file_id,
            )
        elif memory.media_type == "document":
            await bot.send_document(
                chat_id=chat_id,
                document=memory.media_file_id,
            )
        else:
            await bot.send_document(
                chat_id=chat_id,
                document=memory.media_file_id,
            )

        text_msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return text_msg.message_id

    except Exception as e:
        logger.error(f"Ошибка при отправке медиа: {e}")
        msg = await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Ошибка при загрузке медиа\n\n{text}",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return msg.message_id

def _memory_has_any_media(memory: Memory) -> bool:
    items = list(getattr(memory, "media_items", None) or [])
    if items:
        return True
    return bool(getattr(memory, "media_type", None) and (getattr(memory, "media_file_id", None) or getattr(memory, "media_path", None)))


async def _delete_callback_message_silent(callback: CallbackQuery) -> None:
    try:
        await callback.message.delete()
    except Exception:
        pass

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start. Поддерживает инвайт-ссылки для вступления в пару."""
    if db.get_setting("test_version") == "1":
        await message.answer("Бот на временном техническом перерыве")
        return
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    db.add_or_update_user(user_id, username, first_name, last_name)

    # Проверяем инвайт-параметр: /start invite_XXXX
    raw_args = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    invite_code = raw_args[len("invite_"):] if raw_args.startswith("invite_") else None

    # ── Обрабатываем инвайт-код ──
    if invite_code:
        invite_data = db.get_invite_code(invite_code)
        if not invite_data:
            await message.answer(
                "❌ <b>Ссылка не найдена</b>\n\n"
                "Возможно, ссылка была удалена или введена неверно.\n"
                "Попроси партнёра поделиться актуальной ссылкой.",
                parse_mode=ParseMode.HTML
            )
            return

        if invite_data.get("pre_bound_user_id") and invite_data["pre_bound_user_id"] != user_id:
            await message.answer(
                "❌ Эта ссылка не предназначена для этого аккаунта.\n\n"
                "Не передавай эту ссылку другим людям. Она предназначена только для восстановления доступа к твоему аккаунту."
            )
            return

        if invite_data.get("used"):
            await message.answer(
                "🔒 <b>Ссылка уже использована</b>\n\n"
                "Эта ссылка уже была активирована другим пользователем.\n"
                "Попроси партнёра создать новую ссылку.",
                parse_mode=ParseMode.HTML
            )
            return

        couple_id = invite_data["couple_id"]
        creator_id = invite_data.get("creator_id")

        # Нельзя вступить в свою же пару
        if user_id == creator_id:
            await message.answer(
                "🙈 <b>Это твоя собственная ссылка!</b>\n\n"
                "Нельзя пригласить самого себя в пару. Отправь эту ссылку своему партнёру.",
                parse_mode=ParseMode.HTML
            )
            return

        # Проверка на токен перепривязки (transfer_invite_code) — до проверки "пара укомплектована",
        # потому что при обычной перепривязке слот создателя остаётся положительным до момента join.
        unlink_req = db.get_unlink_request_by_transfer_code(invite_code)
        if unlink_req:
            old_user_id = unlink_req["user_id"]
            # Сначала освобождаем слот старого пользователя (делаем id отрицательным),
            # затем присоединяем нового — join_couple сам перенесёт данные.
            db.unlink_user_from_couple(unlink_req["couple_id"], old_user_id)
            used_couple_id = db.use_invite_code(invite_code, user_id)
            if used_couple_id and db.join_couple(used_couple_id, user_id):
                # Отзываем все сессии и login-токены старого аккаунта.
                # Делается ПОСЛЕ join_couple, чтобы старый владелец не потерял доступ
                # в процессе трансфера — только по его завершении.
                #
                # join_couple уже перенёс user_login_tokens и user_tokens с
                # old_user_id → user_id (новый Telegram ID). Поэтому:
                #   - devices удаляем по old_user_id (visitor_id_base),
                #   - токены ревоцируем по user_id (новый владелец, куда они переехали).
                # Новый токен для нового аккаунта создаётся ниже (get_or_create_user_token).
                visitor_base = f"{old_user_id}_"
                db.revoke_all_user_sessions(old_user_id, visitor_base, token_user_id=user_id)
                try:
                    from http_api import _notify_force_logout
                    import asyncio
                    asyncio.create_task(_notify_force_logout(old_user_id))
                except Exception:
                    pass
                db.add_admin(user_id, added_by=user_id)
                site_url = (getattr(config, "BOT_SITE_URL", "") or getattr(config, "SITE_DIRECT_URL", "")).strip().rstrip("/")
                site_kb_rows = []
                if site_url:
                    cpl = db.get_couple_by_id(used_couple_id)
                    assigned_role = "creator" if cpl and cpl.get("user1_id") == user_id else "partner"
                    token = db.get_or_create_user_token(user_id, role=assigned_role)
                    site_link = f"{site_url}?token={token}&rebind=1"
                    site_kb_rows.append([InlineKeyboardButton(text="🌐 Перейти на сайт", url=site_link)])
                
                await message.answer(
                    "✅ <b>Аккаунт на сайте перепривязан к этому Telegram аккаунту</b>, "
                    "но чтобы изменения вступили в силу перейди по ссылке",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=site_kb_rows) if site_kb_rows else None,
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer("❌ Произошла ошибка при перепривязке аккаунта.")
            return

        # Пользователь уже состоит в паре
        if db.is_in_couple(user_id):
            await message.answer(
                "💑 <b>Ты уже состоишь в паре!</b>\n\n"
                "Нельзя вступить в новую пару, не покинув текущую.\n"
                "Нажми /start чтобы открыть главное меню.",
                parse_mode=ParseMode.HTML
            )
            return

        # Пара уже полная (у создателя уже есть партнёр)
        target_couple = db.get_couple_by_id(couple_id)
        u1 = target_couple.get("user1_id") if target_couple else None
        u2 = target_couple.get("user2_id") if target_couple else None
        if target_couple and u1 and u1 > 0 and u2 and u2 > 0:
            await message.answer(
                "🚫 <b>Пара уже укомплектована</b>\n\n"
                "У автора этой ссылки уже есть партнёр. Вступить нельзя.\n"
                "Если это ошибка — напиши партнёру напрямую.",
                parse_mode=ParseMode.HTML
            )
            return

        # Пользователь УЖЕ является участником целевой пары (например, сам открыл свою же transfer-ссылку)
        if target_couple and (u1 == user_id or u2 == user_id):
            await message.answer(
                "ℹ️ <b>Этот аккаунт уже является участником данной пары.</b>\n\n"
                "Использование этого токена не требуется.",
                parse_mode=ParseMode.HTML
            )
            return

        # Всё ок — начинаем онбординг для вступления
        inviter_name = db.get_display_name(creator_id) if creator_id else "твой партнёр"
        await state.update_data(
            joining_couple_id=couple_id,
            invite_code=invite_code,
            joining=True
        )
        await state.set_state(CoupleOnboardingStates.waiting_for_name)
        skip_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_name")]
        ])
        await message.answer(
            f"👋 Привет, <b>{first_name}</b>!\n\n"
            f"Создай пару с своим партнёром и вместе создавайте, изменяйте и делитесь моментами!\n\n"
            f"👤 <b>{inviter_name}</b> приглашает тебя вступить в пару\n\n"
            "Но для начала мне нужно узнать, а как тебя зовут?",
            reply_markup=skip_kb,
            parse_mode=ParseMode.HTML
        )
        return
    # ── Пользователь не состоит в паре ──
    if not db.is_in_couple(user_id):
        couple = db.get_couple_by_user(user_id)
        if not couple:
            # Нет пары — запускаем онбординг, пара создастся после
            await state.update_data(creating_couple=True)
            await state.set_state(CoupleOnboardingStates.waiting_for_name)
            skip_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_name")]
            ])
            await message.answer(
                f"👋 Привет, <b>{first_name}</b>!\n\n"
                "Создай пару с своим партнёром и вместе создавайте, изменяйте и делитесь моментами!\n\n"
                "Для начала — как тебя зовут?",
                reply_markup=skip_kb,
                parse_mode=ParseMode.HTML
            )
            return
        # Уже создана пара (партнёр ещё не присоединился) — показываем ссылку
        couple_id = couple["id"]
        existing_code = db.get_active_invite_for_couple(couple_id)
        code = existing_code if existing_code else db.create_invite_code(couple_id, user_id)
        bot_username = (await message.bot.get_me()).username
        invite_link = f"https://t.me/{bot_username}?start=invite_{code}"
        from urllib.parse import quote
        share_text = "Вступай в нашу пару по этой ссылке!"
        share_url = f"https://t.me/share/url?url={quote(invite_link, safe='')}&text={quote(share_text, safe='')}"
        await message.answer(
            "💓 <b>Партнёр ещё не добавлен</b>\n\n"
            "🎁 Чтобы ты мог(ла) создавать, изменять и делиться моментами — тебе нужно добавить партнёра\n\n"
            "🔗 Сделать это можно по ссылке ниже",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Пригласить партнёра", url=share_url)]
            ]),
            parse_mode=ParseMode.HTML
        )
        return

    # ── Пользователь в паре: показываем главное меню ──
    welcome_text = format_welcome_message(user_id)
    await message.answer(
        welcome_text,
        reply_markup=create_main_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )


# === Онбординг пары ===

@router.message(CoupleOnboardingStates.waiting_for_name)
async def couple_onboarding_name(message: Message, state: FSMContext):
    """Получаем имя пользователя во время онбординга пары."""
    user_id = message.from_user.id
    name = (message.text or "").strip()
    if name:
        await state.update_data(display_name=name)
    else:
        await state.update_data(display_name=None)
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_description")]
    ])
    await state.set_state(CoupleOnboardingStates.waiting_for_description)
    await message.answer(
        "💬 Теперь можешь добавить описание, его будет видеть твой партнёр",
        reply_markup=skip_kb,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "onboarding_skip_name", CoupleOnboardingStates.waiting_for_name)
async def couple_onboarding_skip_name(callback: CallbackQuery, state: FSMContext):
    """Пропуск имени — используем first_name из Telegram."""
    await state.update_data(display_name=None)
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_description")]
    ])
    await state.set_state(CoupleOnboardingStates.waiting_for_description)
    await callback.message.edit_text(
        "💬 Теперь можешь добавить описание, его будет видеть твой партнёр",
        reply_markup=skip_kb,
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.message(CoupleOnboardingStates.waiting_for_description)
async def couple_onboarding_description(message: Message, state: FSMContext):
    """Получаем описание пользователя."""
    description = (message.text or "").strip() or None
    await state.update_data(description=description)
    data = await state.get_data()
    if data.get("creating_couple"):
        await _ask_met_date(message, state)
    else:
        await _finish_couple_onboarding(message, state)


@router.callback_query(F.data == "onboarding_skip_description", CoupleOnboardingStates.waiting_for_description)
async def couple_onboarding_skip_description(callback: CallbackQuery, state: FSMContext):
    """Пропуск описания."""
    await state.update_data(description=None)
    data = await state.get_data()
    if data.get("creating_couple"):
        await _ask_met_date(callback.message, state, callback=callback)
    else:
        await _finish_couple_onboarding(callback.message, state, callback=callback)


async def _ask_met_date(message: Message, state: FSMContext, callback: CallbackQuery = None):
    """Запрашиваем дату знакомства у создателя пары."""
    await state.set_state(CoupleOnboardingStates.waiting_for_met_date_raw)
    send_fn = callback.message.edit_text if callback else message.answer
    await send_fn(
        "💑 <b>Когда вы познакомились?</b>\n\n"
        "Напиши дату в любом формате, например:\n"
        "• <i>15 мая 2024</i>\n"
        "• <i>15.05.2024</i>\n"
        "• <i>пятнадцатое мая прошлого года</i>",
        parse_mode=ParseMode.HTML
    )


async def _finish_couple_onboarding(message: Message, state: FSMContext, callback: CallbackQuery = None):
    """Завершение онбординга — создаём пару или присоединяемся."""
    data = await state.get_data()
    user_id = message.chat.id
    display_name = data.get("display_name")
    description = data.get("description")

    first_name = None
    if callback:
        first_name = callback.from_user.first_name
        user_id = callback.from_user.id
    elif hasattr(message, 'from_user') and message.from_user:
        first_name = message.from_user.first_name

    final_name = display_name or first_name or "Участник"
    db.save_user_profile(user_id, display_name=final_name, description=description, onboarded=True)

    def _make_site_link(uid: int) -> str:
        """Возвращает строку со ссылкой на сайт (без токена), если сайт настроен."""
        direct_url = getattr(config, "BOT_SITE_URL", "")
        if not direct_url:
            direct_url = getattr(config, "SITE_DIRECT_URL", "")
        if not direct_url:
            return ""
        return f"\n\n🌐 <b>Ссылка на сайт:</b>\n{direct_url}"

    if data.get("joining") and data.get("joining_couple_id"):
        couple_id = data["joining_couple_id"]
        invite_code = data.get("invite_code", "")

        invite_info = db.get_invite_code(invite_code) if invite_code else None
        used_couple_id = db.use_invite_code(invite_code, user_id)
        if used_couple_id and db.join_couple(used_couple_id, user_id):
            db.add_admin(user_id, added_by=user_id)

            # Берём couple_id только из atomically-used invite, чтобы не зависеть от FSM-данных.
            creator_id = invite_info.get("creator_id") if invite_info else None
            notify_candidates = []
            joined_couple = db.get_couple_by_id(used_couple_id)
            if joined_couple:
                u1 = joined_couple.get('user1_id')
                u2 = joined_couple.get('user2_id')
                # Отправляем уведомление всем валидным кандидатам, кроме присоединившегося.
                for candidate in (creator_id, u1, u2):
                    if candidate and candidate != user_id and candidate not in notify_candidates:
                        notify_candidates.append(candidate)
                if creator_id is None or creator_id == user_id:
                    creator_id = u1 if u1 != user_id else None

            await state.clear()
            # Имя партнёра (creator) как кликабельная ссылка, если есть user_id.
            partner_name = html.escape(db.get_display_name(creator_id) if creator_id else "партнёра")
            partner_display = (
                f'<a href="tg://user?id={creator_id}">{partner_name}</a>'
                if creator_id
                else partner_name
            )
            # Генерируем токен для авто-логина на сайте
            site_url = (getattr(config, "BOT_SITE_URL", "") or getattr(config, "SITE_DIRECT_URL", "")).strip().rstrip("/")
            site_kb_rows = []
            if site_url:
                token = db.get_or_create_user_token(user_id, role="partner")
                site_link = f"{site_url}?token={token}"
                site_kb_rows.append([InlineKeyboardButton(text="🌐 Да, хочу!", url=site_link)])
            # Кнопка «Нет, не хочу» → показывает главное меню
            site_kb_rows.append(
                [InlineKeyboardButton(text="🚫 Нет, не хочу", callback_data="joined_no_site")]
            )
            send_fn = callback.message.edit_text if callback else message.answer
            await send_fn(
                f"✅ Ты присоединился(ась) к паре с {partner_display}!\n\n"
                "Теперь ты можешь добавлять воспоминания в боте, вспоминать моменты, "
                "создавать события на какую-либо дату и многое другое!\n\n"
                "Но не спеши, хочешь ли ты привязать свой аккаунт к сайту, "
                "чтобы делиться личными моментами стало ещё удобнее?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=site_kb_rows),
                parse_mode=ParseMode.HTML
            )
            # Уведомляем создателя пары
            if notify_candidates:
                partner_name = html.escape(final_name or "Партнёр")
                partner_link = f'<a href="tg://user?id={user_id}">{partner_name}</a>'
                text_notify = (
                    f"✅ Твой партнёр {partner_link} присоединился(-лась) к паре!\n\n"
                    "Теперь ты можешь добавлять воспоминания в боте, вспоминать моменты, "
                    "создавать события на какую-либо дату и многое другое!\n\n"
                    "Но не спеши, хочешь ли ты привязать свой аккаунт к сайту, "
                    "чтобы делиться личными моментами стало ещё удобнее?"
                )
                notify_site_url = (getattr(config, "BOT_SITE_URL", "") or getattr(config, "SITE_DIRECT_URL", "")).strip().rstrip("/")

                logger.info(
                    "Подготовка уведомления о присоединении: invite=%s joined=%s couple_id=%s creator_id=%s recipients=%s",
                    invite_code,
                    user_id,
                    used_couple_id,
                    creator_id,
                    notify_candidates,
                )

                for notify_user_id in notify_candidates:
                    notify_rows = []
                    if notify_site_url:
                        role = "creator" if joined_couple and joined_couple.get("user1_id") == notify_user_id else "partner"
                        token = db.get_or_create_user_token(notify_user_id, role=role)
                        notify_rows.append([InlineKeyboardButton(text="🌐 Да, хочу!", url=f"{notify_site_url}?token={token}")])
                    notify_rows.append([InlineKeyboardButton(text="🚫 Нет, не хочу", callback_data="joined_no_site")])
                    notify_kb = InlineKeyboardMarkup(inline_keyboard=notify_rows)

                    send_attempt = 0
                    try:
                        while True:
                            try:
                                await message.bot.send_message(
                                    chat_id=notify_user_id,
                                    text=text_notify,
                                    reply_markup=notify_kb,
                                    parse_mode=ParseMode.HTML,
                                    force_new_message=True,
                                )
                                logger.info(
                                    "Уведомление о присоединении отправлено: invite=%s joined=%s to=%s",
                                    invite_code, user_id, notify_user_id
                                )
                                break
                            except TelegramRetryAfter as e:
                                send_attempt += 1
                                retry_after = max(1, int(getattr(e, "retry_after", 0) or 0))
                                if send_attempt > 1:
                                    raise
                                logger.warning(
                                    "Telegram rate limit при отправке уведомления о присоединении "
                                    "(to=%s, invite=%s, joined=%s, retry_after=%s): %s",
                                    notify_user_id, invite_code, user_id, retry_after, e
                                )
                                await asyncio.sleep(min(retry_after, 5))
                    except Exception as e:
                        logger.warning(
                            "Не удалось отправить уведомление о присоединении (to=%s, invite=%s, joined=%s): %s",
                            notify_user_id, invite_code, user_id, e
                        )
        else:
            await state.clear()
            send_fn = callback.message.edit_text if callback else message.answer
            await send_fn(
                "❌ Не удалось присоединиться к паре. Инвайт-ссылка недействительна.",
                parse_mode=ParseMode.HTML
            )
    elif data.get("creating_couple"):
        # Создаём новую пару
        couple_id = db.create_couple(user_id)
        if couple_id > 0:
            db.add_admin(user_id, added_by=user_id)
            met_date_db = data.get("met_date_db")
            if met_date_db:
                db.set_couple_met_date(couple_id, met_date_db[:10])
            invite_code = db.create_invite_code(couple_id, user_id)
            bot_username = (await message.bot.get_me()).username
            invite_link = f"https://t.me/{bot_username}?start=invite_{invite_code}"
            site_block = _make_site_link(user_id)

            await state.clear()
            from urllib.parse import quote
            share_text = "Вступай в нашу пару по этой ссылке!"
            share_url = f"https://t.me/share/url?url={quote(invite_link, safe='')}&text={quote(share_text, safe='')}"
            send_fn = callback.message.edit_text if callback else message.answer
            await send_fn(
                f"✅ <b>{final_name}</b>, регистрация успешно пройдена!\n\n"
                f"✅ Теперь осталось добавить своего партнёра.\n\n"
                f"🔗 Отправь ссылку тому, кого хочешь пригласить — по кнопке ниже",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Пригласить партнёра", url=share_url)],
                ]),
                parse_mode=ParseMode.HTML
            )
        else:
            await state.clear()
            send_fn = callback.message.edit_text if callback else message.answer
            await send_fn("❌ Ошибка при создании пары. Попробуй снова /start")
    else:
        await state.clear()


def _format_met_date_ru(date_db: str) -> str:
    """Форматирует 'YYYY-MM-DD ...' в '15 мая 2024'."""
    MONTHS_RU = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
    }
    try:
        parts = date_db[:10].split('-')
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{d} {MONTHS_RU.get(m, str(m))} {y}"
    except Exception as e:
        logger.debug("Не удалось форматировать дату знакомства %r: %s", date_db, e)
        return date_db


@router.message(CoupleOnboardingStates.waiting_for_met_date_raw, F.text)
async def onboarding_met_date_raw(message: Message, state: FSMContext):
    """Обработка ввода даты знакомства — вызов ИИ и подтверждение."""
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Напиши дату текстом, например: <i>15 мая 2024</i>", parse_mode=ParseMode.HTML)
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    msg_status = await message.answer("⏳ Определяю дату...")
    try:
        ctx = get_user_datetime_context(user_id)
        ai_date = await asyncio.to_thread(parse_date_with_ai, raw, ctx)
    except Exception as e:
        logger.error(f"Ошибка ИИ при распознавании даты знакомства: {e}")
        ai_date = ""
    try:
        await msg_status.delete()
    except Exception as e:
        logger.debug("Не удалось удалить статусное сообщение onboarding met date: %s", e)
    if not ai_date:
        await message.answer(
            "❌ Не удалось распознать дату. Попробуй написать по другому, например:\n"
            "• <i>15 мая 2024</i>\n• <i>15.05.2024</i>",
            parse_mode=ParseMode.HTML
        )
        return
    db_format = parse_ai_date_to_db(ai_date)
    if not db_format:
        await message.answer(
            "❌ Не удалось преобразовать дату. Попробуй ещё раз.",
            parse_mode=ParseMode.HTML
        )
        return
    display = _format_met_date_ru(db_format)
    await state.update_data(met_date_db=db_format, met_date_display=display)
    await state.set_state(CoupleOnboardingStates.waiting_for_met_date_confirm)
    await message.answer(
        f"💑 Вы познакомились <b>{display}</b>?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да", callback_data="onboarding_met_date_yes"),
                InlineKeyboardButton(text="✏️ Нет, изменить", callback_data="onboarding_met_date_no"),
            ]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "onboarding_met_date_yes", CoupleOnboardingStates.waiting_for_met_date_confirm)
async def onboarding_met_date_yes(callback: CallbackQuery, state: FSMContext):
    """Подтверждение даты знакомства — переходим к завершению онбординга."""
    await callback.answer()
    await _finish_couple_onboarding(callback.message, state, callback=callback)


@router.callback_query(F.data == "onboarding_met_date_no", CoupleOnboardingStates.waiting_for_met_date_confirm)
async def onboarding_met_date_no(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет изменить дату — запрашиваем снова."""
    await callback.answer()
    await state.update_data(met_date_db=None, met_date_display=None)
    await state.set_state(CoupleOnboardingStates.waiting_for_met_date_raw)
    await callback.message.edit_text(
        "💑 <b>Напиши дату знакомства ещё раз:</b>\n\n"
        "• <i>15 мая 2024</i>\n"
        "• <i>15.05.2024</i>",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "get_invite_link")
async def get_invite_link(callback: CallbackQuery):
    """Получить/обновить инвайт-ссылку для своей пары."""
    user_id = callback.from_user.id
    couple = db.get_couple_by_user(user_id)
    if not couple:
        await callback.answer("Ты не состоишь в паре", show_alert=True)
        return
    couple_id = couple['id']
    # Используем существующий код или создаём новый
    existing_code = db.get_active_invite_for_couple(couple_id)
    if existing_code:
        code = existing_code
    else:
        code = db.create_invite_code(couple_id, user_id)
    bot_username = (await callback.bot.get_me()).username
    invite_link = f"https://t.me/{bot_username}?start=invite_{code}"
    await callback_edit_or_answer(callback, 
        f"🔗 <b>Инвайт-ссылка для партнёра:</b>\n\n"
        f"<code>{invite_link}</code>\n\n"
        "Отправь её партнёру — он(а) нажмёт и автоматически присоединится к паре.",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    """Обработчик команды /admin"""
    user_id = message.from_user.id
    
    if not db.is_creator(user_id):
        await message.answer("Эта команда доступна только разработчику")
        return
    
    status_text = _get_rollback_status_block()
    await message.answer(
        "⚙️ <b>Панель администратора</b>\n\n"
        f"{status_text}\n"
        "Выбери действие:",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.message(Command("add"), F.reply_to_message, F.reply_to_message.document)
async def cmd_add_import(message: Message):
    """Восстановление данных из JSON-файла экспорта. Только в ответ на документ .json. Только создатель."""
    if not db.is_creator(message.from_user.id):
        await message.reply(MSG_ACCESS_DENIED)
        return
    doc = message.reply_to_message.document
    if not (doc.file_name and doc.file_name.lower().endswith(".json")):
        await message.reply("Отправь команду /add в ответ на JSON-файл экспорта (кнопка «Экспорт воспоминаний»).")
        return
    try:
        tg_file = await message.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await message.bot.download_file(tg_file.file_path, buf)
        data = json.loads(buf.getvalue().decode("utf-8"))
    except Exception as e:
        await message.reply(f"Не удалось прочитать файл: {e}")
        return
    if not isinstance(data, dict):
        await message.reply("В файле должен быть JSON-объект (экспорт из бота).")
        return
    try:
        mem_ok, ev_ok, wish_ok = db.import_from_export(data)
        await message.reply(
            f"✅ Импорт завершён:\n"
            f"• Воспоминаний: {mem_ok}\n"
            f"• Событий на дату: {ev_ok}\n"
            f"• Желаний: {wish_ok}"
        )
    except Exception as e:
        await message.reply(f"Ошибка при импорте: {e}")


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    user_id = callback.from_user.id
    await state.clear()
    # Если пользователь не в паре — показываем экран ожидания, не главное меню
    if not db.is_in_couple(user_id):
        couple = db.get_couple_by_user(user_id)
        if couple:
            couple_id = couple["id"]
            existing_code = db.get_active_invite_for_couple(couple_id)
            code = existing_code if existing_code else db.create_invite_code(couple_id, user_id)
            bot_username = (await callback.bot.get_me()).username
            invite_link = f"https://t.me/{bot_username}?start=invite_{code}"
            from urllib.parse import quote
            share_text = "Вступай в нашу пару по этой ссылке!"
            share_url = f"https://t.me/share/url?url={quote(invite_link, safe='')}&text={quote(share_text, safe='')}"
            await callback_edit_or_answer(callback, 
                "💓 <b>Партнёр ещё не добавлен</b>\n\n"
                "🎁 Чтобы ты мог(ла) создавать, изменять и делиться моментами — тебе нужно добавить партнёра\n\n"
                "🔗 Сделать это можно по ссылке ниже",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔗 Пригласить партнёра", url=share_url)]
                ]),
                parse_mode=ParseMode.HTML
            )
        else:
            await callback_edit_or_answer(callback, "Напиши /start чтобы начать.")
        await callback.answer()
        return
    welcome_text = format_welcome_message(user_id)
    await callback_edit_or_answer(callback, 
        welcome_text,
        reply_markup=create_main_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "cat_menu")
async def cat_menu(callback: CallbackQuery):
    """Подменю категорий"""
    await callback.message.edit_text(
        "📁 <b>Категории</b>\n\nВыбери раздел:",
        reply_markup=create_categories_keyboard(user_id=callback.from_user.id),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


def _format_couple_message(user_id: int, with_details: bool = False) -> tuple:
    """Формирует текст и клавиатуру для экрана 'Наша пара'."""
    from datetime import datetime, timezone as _tz

    couple = db.get_couple_by_user(user_id)
    if not couple:
        return "❌ Пара не найдена.", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
        ])

    u1 = couple["user1_id"]
    u2 = couple.get("user2_id")

    def _name(uid): return db.get_display_name(uid, "Участник")
    def _city(uid): return db.get_user_setting(uid, "city") or ""
    def _desc(uid):
        p = db.get_user_profile(uid)
        return (p or {}).get("description") or ""
    def _esc(value: str) -> str:
        return html.escape((value or "").strip())

    name1 = _name(u1); city1 = _city(u1); desc1 = _desc(u1)
    name2 = _name(u2) if u2 else None
    city2 = _city(u2) if u2 else ""
    desc2 = _desc(u2) if u2 else ""

    # ── Время вместе ──
    paired_str = couple.get("paired_at")
    time_together_line = ""
    if paired_str and u2 and u2 > 0 and u1 > 0:
        time_together = "только начинаем ✨"
        try:
            paired_dt = datetime.strptime(paired_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
            delta = datetime.now(_tz.utc) - paired_dt
            total_sec = max(int(delta.total_seconds()), 0)
            days_total, rest = divmod(total_sec, 86400)
            hours, rest = divmod(rest, 3600)
            mins, secs = divmod(rest, 60)
            years, rem_days = divmod(days_total, 365)
            months, days = divmod(rem_days, 30)
            parts = []
            if years:   parts.append(f"{years} г.")
            if months:  parts.append(f"{months} мес.")
            if days:    parts.append(f"{days} д.")
            if not years and not months and not days:
                parts.append(f"{hours} ч {mins} мин")
            time_together = " ".join(parts) if parts else f"{hours} ч {mins} мин"
        except Exception:
            pass
        time_together_line = f"⏳ Вместе: <b>{time_together}</b>\n\n"
    
    created_str = couple.get("created_at", "")

    # ── Часовой пояс участника ──
    def _tz_line(uid):
        tz = db.get_user_setting(uid, "timezone")
        if not tz:
            return ""
        if tz.strip().upper() in {"UTC", "ETC/UTC"}:
            return ""
        label = get_timezone_label(tz)
        pretty = re.sub(r"\s+", " ", (label or "").strip())
        pretty = pretty.replace("UTC +", "UTC+").replace("UTC -", "UTC-")
        m = re.match(r"^([^()]+)\((UTC[+-]?\d+(?::\d{2})?)\)$", pretty)
        if m:
            city = m.group(1).strip()
            utc = m.group(2).strip()
            city_short = city.split("/")[-1].replace("_", " ").strip()
            return f"🌍 {city_short} ({utc})"
        return f"🌍 {pretty}"

    def _status_line(uid):
        try:
            ts = (db.get_user_last_seen(uid) or "").strip()
            if not ts:
                return "⚪ давно не заходил(а)"
            ts_dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
            delta_sec = max(int((datetime.now(_tz.utc) - ts_dt).total_seconds()), 0)
            if delta_sec <= 120:
                return "🟢 в сети"
            if delta_sec < 3600:
                mins = max(1, delta_sec // 60)
                return f"🕓 был(а) {mins} мин назад"
            hours = max(1, delta_sec // 3600)
            return f"🕓 был(а) {hours} ч назад"
        except Exception:
            return "⚪ статус недоступен"

    def _format_dt(dt_str: str) -> str:
        try:
            dt = datetime.strptime((dt_str or "")[:19], "%Y-%m-%d %H:%M:%S")
            return dt.strftime("%d.%m.%Y")
        except Exception:
            return "—"

    def _day_month_label(dt_obj) -> str:
        months = {
            1: "января", 2: "февраля", 3: "марта", 4: "апреля",
            5: "мая", 6: "июня", 7: "июля", 8: "августа",
            9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
        }
        return f"{dt_obj.day} {months.get(dt_obj.month, '')}".strip()

    def _relative_day(dt_str: str) -> str:
        try:
            dt = datetime.strptime((dt_str or "")[:19], "%Y-%m-%d %H:%M:%S").date()
            now_d = datetime.now(_tz.utc).date()
            diff = (now_d - dt).days
            if diff <= 0:
                return "сегодня"
            if diff == 1:
                return "вчера"
            if diff == 2:
                return "позавчера"
            return _day_month_label(dt)
        except Exception:
            return "недавно"

    def _met_date_human(value):
        try:
            if not value:
                return ""
            return _day_month_label(value)
        except Exception:
            return ""

    def _last_item_title(kind: str, item_id: int) -> str:
        try:
            if kind == "memory":
                m = db.get_memory(item_id)
                if m and (m.title or "").strip():
                    return (m.title or "").strip()
                return "Новый момент"
            if kind == "event":
                ev = db.get_scheduled_event(item_id)
                if ev and (ev.title or "").strip():
                    return (ev.title or "").strip()
                return "Новое событие"
            if kind == "wish":
                w = db.get_wish(item_id)
                text = ((w.content if w else "") or "").strip()
                if text:
                    return text[:60] + ("…" if len(text) > 60 else "")
                return "Новое желание"
            return "Новая запись"
        except Exception:
            return "Новая запись"

    def _last_item_noun(kind: str) -> str:
        return {
            "memory": "момент",
            "event": "событие",
            "wish": "желание",
        }.get(kind, "запись")

    def _progress_line(label: str, value: int, target: int) -> str:
        safe_value = max(int(value), 0)
        safe_target = max(int(target), 1)
        percent = min(int((safe_value / safe_target) * 100), 100)
        filled = min(10, max(0, int(percent / 10)))
        bar = ("▓" * filled) + ("░" * (10 - filled))
        return f"{label} {safe_value}/{safe_target}  {bar} {percent}%"

    # ── Блок участника ──
    def _member_block(name, city, desc, tz_line, status_line, emoji, role):
        lines = [f"{emoji} <b>{_esc(name)}</b>", status_line]
        meta = []
        if city:
            meta.append(f"📍 {_esc(city)}")
        if tz_line:
            meta.append(_esc(tz_line))
        if meta:
            lines.append("  ".join(meta))
        if desc:
            lines.append(f"💬 <i>{_esc(desc[:140])}</i>")
        return "\n".join(lines)

    tz1 = _tz_line(u1)
    st1 = _status_line(u1)
    p1_block = _member_block(name1, city1, desc1, tz1, st1, "👑", "создатель пары")

    if u2:
        tz2 = _tz_line(u2)
        st2 = _status_line(u2)
        p2_block = _member_block(name2, city2, desc2, tz2, st2, "💙", "партнёр")
        pair_line = f"<b>{_esc(name1)}</b>  ❤️  <b>{_esc(name2)}</b>"
    else:
        p2_block = (
            "💙 <b>Партнёр ещё не присоединился</b>\n"
            "<i>Добавь партнёра по ссылке-приглашению, чтобы открыть общую статистику и события.</i>"
        )
        pair_line = f"<b>{_esc(name1)}</b>  ❤️  <b>?</b>"

    cid = couple["id"]
    user_ids = [u1] + ([u2] if u2 else [])
    mem_count = (
        len(db.get_memories_by_category("memories", limit=9999, couple_id=cid) or []) +
        len(db.get_memories_by_category("important_moments", limit=9999, couple_id=cid) or []) +
        len(db.get_memories_by_category("important_dates", limit=9999, couple_id=cid) or [])
    )
    ev_count = len(db.get_scheduled_events_for_users(user_ids) or [])
    wish_count = sum(len(db.get_user_wishes(uid) or []) for uid in user_ids)

    met_date = db.get_couple_met_date(user_id)
    met_human = _met_date_human(met_date)
    met_date_line = f"🗓 Вы познакомились {html.escape(met_human)}" if met_human else "🗓 Дата знакомства пока не указана"
    last_item = db.get_last_added_item(couple_id=cid, user_ids=user_ids)
    if last_item:
        when_label = _relative_day(last_item[1])
        noun = _last_item_noun(last_item[0])
        title = _esc(_last_item_title(last_item[0], int(last_item[2])))
        last_line = f"📝 {when_label.capitalize()} добавлено {noun}: «{title}»"
    else:
        last_line = "📝 Последнее: пока пусто — добавь первый момент ✨"

    text = (
        f"💑 <b>Наша пара</b>\n\n"
        f"{pair_line}\n\n"
        f"{p1_block}\n\n"
        f"{p2_block}\n\n"
        f"{time_together_line}"
        f"{met_date_line}\n"
        f"{last_line}"
    )

    if with_details:
        text += (
            f"\n\n"
            f"📊 <b>Прогресс пары</b>\n\n"
            f"{_progress_line('📖 Воспоминания', mem_count, 100)}\n"
            f"{_progress_line('🗓 События', ev_count, 24)}\n"
            f"{_progress_line('🎁 Желания', wish_count, 30)}\n\n"
            f"📅 Пара создана: {_format_dt(created_str)}"
        )
    if mem_count == 0 and ev_count == 0 and wish_count == 0:
        text += (
            f"\n\n"
            "✨ Пока пусто\n"
            "<i>Добавьте первый момент, и тут появится ваша история.</i>"
        )

    if u2:
        first_row = [InlineKeyboardButton(text="🙈 Скрыть статистику", callback_data="our_couple_hide")] if with_details else [InlineKeyboardButton(text="📊 Статистика пары", callback_data="our_couple_show")]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            first_row,
            [InlineKeyboardButton(text="➕ Добавить момент", callback_data="cat_menu")],
            [InlineKeyboardButton(text="📄 Экспорт истории (PDF)", callback_data="our_couple_export_pdf")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Пригласить партнёра", callback_data="get_invite_link")],
            [InlineKeyboardButton(text="➕ Добавить первый момент", callback_data="cat_menu")],
            [InlineKeyboardButton(text="📄 Экспорт истории (PDF)", callback_data="our_couple_export_pdf")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
        ])
    return text, kb


def _ru_month_label(dt_obj) -> str:
    months = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
    }
    return f"{dt_obj.day} {months.get(dt_obj.month, '')}".strip()


def _fmt_ru_date(date_str: str) -> str:
    raw = (date_str or "").strip()
    if not raw:
        return "—"
    try:
        dt = datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S").date()
        return _ru_month_label(dt)
    except Exception:
        pass
    try:
        dt = datetime.strptime(raw[:10], "%Y-%m-%d").date()
        return _ru_month_label(dt)
    except Exception:
        return raw


def _build_couple_pdf_payload(user_id: int) -> dict:
    couple = db.get_couple_by_user(user_id) or {}
    if not couple:
        return {}
    u1 = couple["user1_id"]
    u2 = couple.get("user2_id")
    user_ids = [u1] + ([u2] if u2 else [])
    couple_id = couple["id"]
    name1 = db.get_display_name(u1, "Участник")
    name2 = db.get_display_name(u2, "Партнёр") if u2 else "Партнёр"

    memories = []
    for cat in ("important_moments", "memories", "important_dates"):
        memories.extend(db.get_memories_by_category(cat, limit=1000, couple_id=couple_id) or [])
    memories = sorted(memories, key=lambda m: (m.created_at or ""))
    events = sorted(db.get_scheduled_events_for_users(user_ids) or [], key=lambda e: (e.created_at or ""))
    wishes = []
    for uid in user_ids:
        wishes.extend(db.get_user_wishes(uid) or [])
    wishes = sorted(wishes, key=lambda w: (w.created_at or ""))

    timeline = []
    for m in memories[-20:]:
        timeline.append({
            "sort": m.created_at or "",
            "date": _fmt_ru_date(m.created_at),
            "item": {"type": "moment", "title": m.title or "Момент", "text": (m.content or "")[:260]},
        })
    for e in events[-20:]:
        timeline.append({
            "sort": e.created_at or "",
            "date": _fmt_ru_date(e.created_at),
            "item": {"type": "event", "title": e.title or "Событие", "text": (e.description or "")[:260]},
        })
    for w in wishes[-20:]:
        timeline.append({
            "sort": w.created_at or "",
            "date": _fmt_ru_date(w.created_at),
            "item": {"type": "wish", "title": "Желание", "text": (w.content or "")[:260]},
        })
    timeline = sorted(timeline, key=lambda x: x["sort"])
    grouped = {}
    for row in timeline:
        grouped.setdefault(row["date"], []).append(row["item"])
    timeline_final = [{"date": d, "items": grouped[d]} for d in grouped.keys()]

    return {
        "couple_names": [name1, name2],
        "period_start": _fmt_ru_date(couple.get("created_at") or ""),
        "period_end": _fmt_ru_date(datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
        "stats": {
            "memories": len(memories),
            "events": len(events),
            "wishes": len(wishes),
        },
        "timeline": timeline_final,
        "sections": {
            "memories": [{
                "title": (m.title or "Момент")[:120],
                "date": _fmt_ru_date(m.created_at),
                "text": (m.content or "")[:1200],
                "author": db.get_display_name(m.user_id, "Участник"),
            } for m in memories[-80:]],
            "events": [{
                "title": (e.title or "Событие")[:120],
                "date": _fmt_ru_date(e.event_datetime or e.created_at),
                "text": ((e.description or "")[:1200] or "Без описания"),
                "author": db.get_display_name(e.user_id, "Участник"),
            } for e in events[-80:]],
            "wishes": [{
                "title": "Желание",
                "date": _fmt_ru_date(w.created_at),
                "text": (w.content or "")[:1200],
                "author": db.get_display_name(w.user_id, "Участник"),
            } for w in wishes[-80:]],
        },
    }


def _build_restart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")],
        [InlineKeyboardButton(text="🔙 В админ-панель", callback_data="admin_panel")],
    ])


def _read_restart_status() -> dict:
    try:
        if RESTART_STATUS_PATH.exists():
            return json.loads(RESTART_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_restart_status(status: dict) -> None:
    try:
        RESTART_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _is_container_healthy(name: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", name],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if proc.returncode != 0:
            return False
        parts = ((proc.stdout or "").strip()).split()
        if not parts:
            return False
        running = parts[0] == "true"
        health = parts[1] if len(parts) > 1 else "none"
        return running and health in {"healthy", "none"}
    except Exception:
        return False


def _is_restart_stuck(status: dict) -> bool:
    state = (status.get("status") or "").strip().lower()
    updated_at = (status.get("updated_at") or "").strip()
    if state != "running" or not updated_at:
        return False
    try:
        updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        age_sec = (datetime.now(timezone.utc) - updated_dt).total_seconds()
        if age_sec <= RESTART_STUCK_SECONDS:
            return False
        return not _local_restart_in_progress()
    except Exception:
        return False


def _recover_restart_status_if_needed(status: dict) -> dict:
    state = (status.get("status") or "").strip().lower()
    bot_ok = _is_container_healthy("ksysha-bot")
    cf_ok = _is_container_healthy("ksysha-cloudflared")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if bot_ok and cf_ok and state in {"failed", "running"}:
        fixed = {
            "status": "success",
            "message": "restart_recovered_by_healthcheck",
            "attempt": int(status.get("attempt") or 0),
            "updated_at": now,
        }
        for k in ["build_duration_sec", "total_duration_sec"]:
            if k in status:
                fixed[k] = status[k]
        _write_restart_status(fixed)
        return fixed

    if _is_restart_stuck(status):
        fixed = {
            "status": "failed",
            "message": "restart_stuck_timeout",
            "attempt": int(status.get("attempt") or 0),
            "updated_at": now,
        }
        for k in ["build_duration_sec", "total_duration_sec"]:
            if k in status:
                fixed[k] = status[k]
        _write_restart_status(fixed)
        return fixed

    return status


async def _read_deploy_status() -> dict:
    deploy_url = (getattr(config, "DEPLOYER_URL", "") or "").strip()
    deploy_secret = (getattr(config, "DEPLOYER_SECRET", "") or "").strip()
    if not deploy_url or not deploy_secret:
        return {}
    try:
        parts = urlsplit(deploy_url)
        deploy_path = parts.path or ""
        health_path = f"{deploy_path[:-7]}/health" if deploy_path.endswith("/deploy") else "/health"
        health_url = urlunsplit((parts.scheme, parts.netloc, health_path, "", ""))
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(health_url, headers={"X-Deploy-Secret": deploy_secret}) as resp:
                if resp.status >= 400:
                    return {}
                payload = await resp.json(content_type=None)
        deploy = (payload or {}).get("deploy") or {}
        return deploy if isinstance(deploy, dict) else {}
    except Exception:
        return {}


async def _trigger_deploy() -> tuple[bool, str]:
    deploy_url = (getattr(config, "DEPLOYER_URL", "") or "").strip()
    deploy_secret = (getattr(config, "DEPLOYER_SECRET", "") or "").strip()
    if not deploy_url or not deploy_secret:
        return False, "Не настроены DEPLOYER_URL/DEPLOYER_SECRET."
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                deploy_url,
                headers={"X-Deploy-Secret": deploy_secret},
            ) as resp:
                payload = await resp.json(content_type=None)
                if resp.status == 200 and (payload or {}).get("ok"):
                    return True, "accepted"
                if resp.status == 409:
                    return False, "in_progress"
                err = (payload or {}).get("error") or f"http_{resp.status}"
                return False, str(err)
    except Exception as e:
        return False, str(e)


def _local_restart_in_progress() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0:
            return False
        names = [(line or "").strip() for line in (proc.stdout or "").splitlines()]
        return any(name.startswith(RESTART_RUNNER_PREFIX) for name in names)
    except Exception:
        return False


async def _trigger_local_restart() -> tuple[bool, str]:
    if _local_restart_in_progress():
        return False, "in_progress"
    if not RESTART_SCRIPT_PATH.exists():
        return False, f"restart_script_not_found:{RESTART_SCRIPT_PATH}"

    RESTART_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    status = {
        "status": "running",
        "message": "restart_started_local",
        "attempt": 0,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        RESTART_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    host_root_dir = (getattr(config, "RESTART_HOST_ROOT_DIR", "") or RESTART_HOST_ROOT_DIR).strip()
    runner_name = f"{RESTART_RUNNER_PREFIX}{int(time.time())}"
    cmd = [
        "docker", "run", "-d", "--rm",
        "--name", runner_name,
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "-v", f"{host_root_dir}:{RESTART_ROOT_DIR}",
        "-e", f"ROOT_DIR={RESTART_ROOT_DIR}",
        "-e", "COMPOSE_PROJECT_NAME=workspace",
        "-e", f"STATUS_FILE={RESTART_STATUS_FILE_IN_RUNNER}",
        "-w", RESTART_ROOT_DIR,
        RESTART_RUNNER_IMAGE,
        "bash", f"{RESTART_ROOT_DIR}/scripts/restart_clean.sh",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="ignore").strip() or (
            stdout or b""
        ).decode("utf-8", errors="ignore").strip() or f"docker_run_exit_{proc.returncode}"
        return False, detail
    RESTART_PID_PATH.write_text(runner_name, encoding="utf-8")
    return True, "accepted_local"


async def _format_restart_status_text(status: dict) -> str:
    state = (status.get("status") or "unknown").strip().lower()
    attempt = int(status.get("attempt") or 0)
    updated_at = (status.get("updated_at") or "—").strip()
    message = (status.get("message") or "").strip()
    
    msg_map = {
        "restart_started_local": "Локальный перезапуск начат",
        "attempt_1": "Попытка 1: остановка и пересоздание",
        "attempt_2": "Попытка 2: остановка и пересоздание",
        "attempt_3": "Попытка 3: остановка и пересоздание",
        "restart_verified": "Проверка пройдена, всё работает",
        "all_attempts_failed": "Все попытки перезапуска провалились",
        "compose_file_not_found": "Не найден файл docker-compose",
        "restart_recovered_by_healthcheck": "Восстановлено (проверка здоровья пройдена)",
        "restart_stuck_timeout": "Таймаут перезапуска (завис)",
    }
    human_msg = msg_map.get(message, message)

    if state == "success":
        head = "✅ Перезапуск завершён успешно."
    elif state == "failed":
        head = "❌ Перезапуск завершился ошибкой."
    elif state == "running":
        head = "⏳ Перезапуск выполняется."
    else:
        head = "ℹ️ Статус перезапуска пока недоступен."

    lines = [head]
    lines.append("Источник: Локальный перезапуск (safe restart)")
    
    b_dur = status.get("build_duration_sec")
    t_dur = status.get("total_duration_sec")
    if t_dur is not None:
        dur_str = f"Длительность: {t_dur} сек"
        if b_dur is not None:
            dur_str += f" (в т.ч. сборка: {b_dur} сек)"
        lines.append(dur_str)

    lines.append(f"Попытка: {attempt}")
    lines.append(f"Обновлено: {updated_at}")
    if human_msg:
        lines.append(f"Детали: {human_msg}")

    health_result = "успешно" if state == "success" else ("ошибка" if state == "failed" else "ожидание")
    lines.append(f"Healthcheck: {health_result}")

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "-a", "--format", "{{.Names}} - {{.Status}}",
            stdout=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        out_lines = stdout.decode("utf-8", errors="ignore").strip().split('\n')
        ksysha_containers = [ln for ln in out_lines if "ksysha-" in ln]
        if ksysha_containers:
            lines.append("\nАктивные сервисы:")
            for c in ksysha_containers:
                lines.append(f"  • {c}")
    except Exception:
        pass

    try:
        from app_version import version as app_version
        version_val = app_version or "unknown"
    except ImportError:
        version_val = "unknown"
    commit_val = os.environ.get("GIT_COMMIT")
    if not commit_val:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--short", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                cwd="/workspace"
            )
            stdout, _ = await proc.communicate()
            commit_val = stdout.decode("utf-8", errors="ignore").strip() or "unknown"
        except Exception:
            commit_val = "unknown"

    lines.append(f"\nВерсия: {version_val}")
    lines.append(f"Коммит: {commit_val}")

    return "\n".join(lines)


@router.callback_query(F.data == "our_couple")
async def our_couple(callback: CallbackQuery):
    """Показывает информацию о паре."""
    text, kb = _format_couple_message(callback.from_user.id)
    await callback_edit_or_answer(callback, text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "our_couple_show")
async def our_couple_show(callback: CallbackQuery):
    """Раскрывает статистику пары."""
    text, kb = _format_couple_message(callback.from_user.id, with_details=True)
    await callback_edit_or_answer(callback, text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "our_couple_hide")
async def our_couple_hide(callback: CallbackQuery):
    """Скрывает статистику пары."""
    text, kb = _format_couple_message(callback.from_user.id, with_details=False)
    await callback_edit_or_answer(callback, text, reply_markup=kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "our_couple_export_pdf")
async def our_couple_export_pdf(callback: CallbackQuery):
    user_id = callback.from_user.id
    try:
        from pdf_story_template import generate_couple_story_pdf
    except Exception as e:
        await callback.answer("PDF модуль недоступен", show_alert=True)
        logger.warning("PDF import failed: %s", e)
        return

    try:
        payload = _build_couple_pdf_payload(user_id)
        if not payload:
            await callback.answer("Пара не найдена", show_alert=True)
            return
        PDF_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_path = PDF_EXPORTS_DIR / f"couple_story_{user_id}_{stamp}.pdf"
        pdf_path = generate_couple_story_pdf(payload, str(out_path))
        await callback.message.answer_document(
            document=FSInputFile(pdf_path),
            caption="📄 История пары (PDF)",
        )
        await callback.answer("PDF готов")
    except Exception as e:
        logger.exception("Не удалось экспортировать PDF истории пары: %s", e)
        await callback.answer("Не удалось сформировать PDF", show_alert=True)


@router.callback_query(F.data == "bot_site")
async def bot_site(callback: CallbackQuery):
    """Показать актуальную ссылку на сайт бота"""
    user_id = callback.from_user.id

    if not db.is_in_couple(user_id):
        await callback.answer("Сначала нужно создать пару", show_alert=True)
        return

    domain_url = (getattr(config, "BOT_SITE_URL", "") or
                  getattr(config, "SITE_DIRECT_URL", "") or "").strip()

    if not domain_url:
        await callback.answer("Ссылка на сайт ещё не настроена")
        return

    # Всегда генерируем персональную ссылку с токеном
    role = "creator" if db.is_creator(user_id) else "partner"
    token = db.get_or_create_user_token(user_id, role=role)
    personal_url = f"{domain_url}?token={token}" if token else domain_url

    # Имя партнёра для текста (если есть)
    partner_id = db.get_partner_id(user_id)
    partner_name = db.get_display_name(partner_id) if partner_id else None
    if partner_name:
        sky_hint = f", посмотреть на небо (если вы с {partner_name} в разных городах)"
    else:
        sky_hint = ", посмотреть на небо"

    lines = [
        "🌐 <b>Сайт</b>\n",
        f"На сайте удобнее смотреть моменты, можно использовать ИИ{sky_hint} и не только",
        "\n⚠️ Браузер Telegram может работать нестабильно и криво, советую использовать основной браузер, например Chrome",
    ]

    buttons = [
        [InlineKeyboardButton(text="🌐 Открыть сайт", url=personal_url)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")],
    ]

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "search_memories")
async def search_memories_start(callback: CallbackQuery, state: FSMContext):
    """Начало поиска: запрос ключевого слова. Сбрасываем старые данные поиска."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.update_data(search_query=None)
    await state.set_state(SearchStates.waiting_for_search_query)
    await callback_edit_or_answer(callback, 
        "🔍 <b>Поиск</b>\n\n"
        "Напиши ключевое слово или фразу для поиска",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="search_cancel")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "search_cancel")
async def search_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена поиска"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.clear()
    welcome_text = format_welcome_message(user_id)
    await callback_edit_or_answer(callback, 
        welcome_text,
        reply_markup=create_main_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "admin_add_cancel")
async def admin_add_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления администратора"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED_CREATOR)
        return
    await state.clear()
    await callback_edit_or_answer(callback, 
        "Добавление администратора отменено",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


# === События на дату ===

@router.callback_query(F.data == "scheduled_events_menu")
async def scheduled_events_menu(callback: CallbackQuery, state: FSMContext):
    """Меню событий на дату (первая страница, с пагинацией и поиском)."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.clear()
    per_page = 10
    events, total = db.get_scheduled_events_paged(page=1, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    await safe_delete_callback_message(callback)
    text = (
        "🎯 <b>События на дату</b>\n\n"
        "Тут можно добавить событие которое будет в будущем (например цель) и бот будет показывать сколько осталось до этого события"
    )
    if total:
        text += f"\n\nВсего событий: {total}"
    else:
        text += "\n\n📭 Пока нет событий"
    await callback_edit_or_answer(callback, 
        text,
        reply_markup=create_scheduled_events_menu_keyboard(
            events, page=1, total_pages=total_pages, total=total, per_page=per_page
        ),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_events_p\d+$"))
async def scheduled_events_page(callback: CallbackQuery, state: FSMContext):
    """Листание страниц списка событий."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    page = int(callback.data.replace("scheduled_events_p", ""))
    per_page = 10
    events, total = db.get_scheduled_events_paged(page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    text = (
        "🎯 <b>События на дату</b>\n\n"
        f"Страница {page} из {total_pages}. Всего событий: {total}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=create_scheduled_events_menu_keyboard(
            events, page=page, total_pages=total_pages, total=total, per_page=per_page
        ),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "scheduled_events_page_info")
async def scheduled_events_page_info(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "scheduled_events_search")
async def scheduled_events_search_start(callback: CallbackQuery, state: FSMContext):
    """Начало поиска по событиям."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.set_state(ScheduledEventSearchStates.waiting_for_query)
    await callback_edit_or_answer(callback, 
        "🔍 <b>Поиск по событиям</b>\n\nВведи текст для поиска",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.message(ScheduledEventSearchStates.waiting_for_query, F.text)
async def scheduled_events_search_query(message: Message, state: FSMContext):
    """Обработка поискового запроса по событиям."""
    user_id = message.from_user.id
    if not db.is_admin(user_id):
        return
    query = (message.text or "").strip()
    await state.update_data(scheduled_events_search_query=query)
    await state.set_state(ScheduledEventSearchStates.viewing_results)
    per_page = 10
    events, total = db.search_scheduled_events(query, page=1, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    text = (
        "🎯 <b>Результаты поиска</b>\n\n"
        f"Найдено: {total}"
    )
    if not events:
        text += "\n\nНичего не найдено"
    await message.answer(
        text,
        reply_markup=create_scheduled_events_menu_keyboard(
            events, page=1, total_pages=total_pages, total=total,
            per_page=per_page, from_search=True
        ),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_events_search_p\d+$"))
async def scheduled_events_search_page(callback: CallbackQuery, state: FSMContext):
    """Страница результатов поиска событий."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    page = int(callback.data.replace("scheduled_events_search_p", ""))
    data = await state.get_data()
    query = (data.get("scheduled_events_search_query") or "").strip()
    per_page = 10
    events, total = db.search_scheduled_events(query, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    text = (
        "🎯 <b>Результаты поиска</b>\n\n"
        f"Найдено: {total}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=create_scheduled_events_menu_keyboard(
            events, page=page, total_pages=total_pages, total=total,
            per_page=per_page, from_search=True
        ),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "scheduled_event_add")
async def scheduled_event_add_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления события — запрос названия"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.set_state(AddScheduledEventStates.waiting_for_title)
    await callback_edit_or_answer(callback, 
        "🎯 <b>Добавление события</b>\n\n"
        "Шаг 1 из 3\n\n"
        "Напиши название события\n"
        "Например: <i>День рождения, Встреча с друзьями, Поездка</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="scheduled_event_cancel_add")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_cancel_add")
async def scheduled_event_cancel_add(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления события"""
    await state.clear()
    await scheduled_events_menu(callback, state)


@router.message(AddScheduledEventStates.waiting_for_title, F.text)
async def scheduled_event_process_title(message: Message, state: FSMContext):
    """Обработка названия события"""
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_title(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    title = text_and_entities_to_html(message.text or "", message.entities or [])
    await state.update_data(title=title)
    await state.set_state(AddScheduledEventStates.waiting_for_description)
    await message.answer(
        "Шаг 2 из 3\n\n"
        "ℹ️ Опиши событие: можно написать текст с форматированием (<b>жирный</b>, <i>курсив</i>), "
        "или отправить фото/видео/гс, кружок",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="scheduled_event_skip_desc")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="scheduled_event_cancel_add")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_skip_desc",
                       AddScheduledEventStates.waiting_for_description)
async def scheduled_event_skip_description(callback: CallbackQuery, state: FSMContext):
    """Пропуск описания"""
    await state.update_data(description="")
    await state.set_state(AddScheduledEventStates.waiting_for_date_raw)
    await safe_delete_callback_message(callback)
    today = datetime.now()
    await callback_edit_or_answer(callback, 
        "Шаг 3 из 3\n\n"
        "Теперь самое интересное 🎯\n\n"
        "Напиши дату в <b>любом формате</b>, как угодно например\n"
        "• <i>через два месяца</i>\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="scheduled_event_cancel_add")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.message(
    AddScheduledEventStates.waiting_for_description,
    F.text | F.photo | F.video | F.voice | F.video_note | F.document | F.caption
)
async def scheduled_event_process_description(message: Message, state: FSMContext):
    """Обработка описания события (текст с форматированием или подпись к медиа, как у воспоминаний)."""
    if await _is_duplicate_media_group(state, message, "_mg_seen_add_event_desc"):
        return
    use_caption = message.caption is not None
    raw = (message.text or message.caption or "").strip()
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    content = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or []),
    )
    media_type, media_file_id, media_path = await save_media(message)
    await state.update_data(
        description=content,
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
    )
    await state.set_state(AddScheduledEventStates.waiting_for_date_raw)
    await message.answer(
        "Шаг 3 из 3\n\n"
        "Теперь самое интересное 🎯\n\n"
        "Напиши дату в <b>любом формате</b>, как угодно например\n"
        "<i>через два месяца</i>\n",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="scheduled_event_cancel_add")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.message(AddScheduledEventStates.waiting_for_date_raw, F.text)
async def scheduled_event_process_date_raw(message: Message, state: FSMContext):
    """Обработка даты: вызов ИИ, преобразование, подтверждение"""
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введи дату текстом. Например: тридцатое января следующего года")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    msg_status = await message.answer("⏳ Определяю дату...")
    try:
        ctx = get_user_datetime_context(user_id)
        ai_date = await asyncio.to_thread(parse_date_with_ai, raw, ctx, True)
    except Exception as e:
        logger.error(f"Ошибка ИИ при распознавании даты: {e}")
        ai_date = ""
    try:
        await msg_status.delete()
    except Exception as e:
        logger.debug("Не удалось удалить статусное сообщение edit scheduled event date: %s", e)
    if ai_date == "ERROR:PAST_DATE":
        await message.answer("❌ Не используй даты в прошлом, мне нужны будущие события")
        return
    if not ai_date:
        await message.answer(
            "❌ Не удалось распознать дату. Попробуй написать по другому например\n"
            "• <i>25 января 2026</i>\n• <i>25.1.2026</i>\n• <i>25.1.2026 в 14:30</i>",
            parse_mode=ParseMode.HTML
        )
        return
    db_format = parse_ai_date_to_db(ai_date)
    if not db_format:
        await message.answer(
            "❌ Не удалось преобразовать дату. Попробуй по другому",
            parse_mode=ParseMode.HTML
        )
        return
    from utils import calculate_next_occurrence
    next_db_format = calculate_next_occurrence(db_format, user_id)
    display_format = format_scheduled_event_datetime(next_db_format, user_id, user_id)
    await state.update_data(
        event_datetime_db=db_format,
        event_datetime_display=display_format,
        date_raw=raw
    )
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    data = await state.get_data()
    title = data.get("title", "")
    desc = data.get("description", "") or "—"
    remaining = format_time_remaining(next_db_format, user_id)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display_format}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>💬 Описание:</b> {desc}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await message.answer(
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_confirm_yes",
                       AddScheduledEventStates.waiting_for_confirm)
async def scheduled_event_confirm_yes(callback: CallbackQuery, state: FSMContext):
    """Подтверждение даты — переходим к выбору повторения события."""
    await state.set_state(AddScheduledEventStates.waiting_for_recurrence)
    await callback.message.edit_text(
        "🔁 Повторять это событие каждый год?",
        reply_markup=create_scheduled_event_recurrence_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.in_({"scheduled_event_recurrence_yes", "scheduled_event_recurrence_no"}),
                       AddScheduledEventStates.waiting_for_recurrence)
async def scheduled_event_recurrence_selected(callback: CallbackQuery, state: FSMContext):
    """Выбор повторения — добавляем событие в БД и показываем."""
    user_id = callback.from_user.id
    is_recurring = 1 if callback.data == "scheduled_event_recurrence_yes" else 0
    data = await state.get_data()
    title = data.get("title", "")
    desc = data.get("description") or ""
    event_datetime = data.get("event_datetime_db", "")
    media_type = data.get("media_type")
    media_file_id = data.get("media_file_id")
    media_path = data.get("media_path")
    if not title or not event_datetime:
        await callback.answer("Ошибка: не хватает данных")
        await state.clear()
        return
    event_id = db.add_scheduled_event(
        user_id, title, desc, event_datetime,
        media_type=media_type, media_file_id=media_file_id, media_path=media_path,
        is_recurring=is_recurring,
    )
    if event_id == -1:
        await callback.answer("Ошибка при сохранении")
        await state.clear()
        return
    event = db.get_scheduled_event(event_id)
    await state.clear()
    await safe_delete_callback_message(callback)
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = "✅ Событие добавлено!\n\n" + format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    await send_scheduled_event_with_media(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        event=event,
        text=text,
        keyboard=keyboard,
    )
    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "events"):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = f"✨ {user_name} добавил(а) новое событие\n\n" + format_scheduled_event_text(event, partner_id, expired=expired)
            await callback.bot.send_message(
                chat_id=partner_id,
                text=notify_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"scheduled_event_{event.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Уведомление о новом событии: {e}")


@router.callback_query(F.data == "scheduled_event_confirm_no",
                       AddScheduledEventStates.waiting_for_confirm)
async def scheduled_event_confirm_no(callback: CallbackQuery, state: FSMContext):
    """Нет, изменить — показываем выбор что менять"""
    data = await state.get_data()
    desc = data.get("description", "")
    has_desc = bool(desc and desc.strip())
    keyboard = create_scheduled_event_edit_options_keyboard(has_desc)
    await callback.message.edit_text(
        "Что хочешь изменить?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )
    await state.set_state(AddScheduledEventStates.waiting_for_edit_choice)


@router.callback_query(F.data == "scheduled_event_edit_title",
                       AddScheduledEventStates.waiting_for_edit_choice)
async def scheduled_event_edit_title_choice(callback: CallbackQuery, state: FSMContext):
    """Выбор редактирования названия"""
    await state.set_state(AddScheduledEventStates.waiting_for_edit_title)
    data = await state.get_data()
    await callback_edit_or_answer(callback, 
        f"ℹ️ Текущее название: {data.get('title', '')}\n\nНапиши новое:",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_edit_desc",
                       AddScheduledEventStates.waiting_for_edit_choice)
async def scheduled_event_edit_desc_choice(callback: CallbackQuery, state: FSMContext):
    """Выбор редактирования описания"""
    await state.set_state(AddScheduledEventStates.waiting_for_edit_description)
    data = await state.get_data()
    await callback_edit_or_answer(callback, 
        f"💬 Текущее описание: {data.get('description', '—')}\n\nНапиши новое или «Пропустить»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="scheduled_event_skip_desc")]
        ]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_edit_date",
                       AddScheduledEventStates.waiting_for_edit_choice)
async def scheduled_event_edit_date_choice(callback: CallbackQuery, state: FSMContext):
    """Выбор редактирования даты"""
    await state.set_state(AddScheduledEventStates.waiting_for_edit_date)
    today = datetime.now()
    uid = callback.from_user.id
    await callback_edit_or_answer(callback, 
        f"📅 Текущая дата: {format_scheduled_event_datetime((await state.get_data()).get('event_datetime_db', ''), uid, uid)}\n\n"
        "Напиши новую дату в любом формате:\n"
        f"Сейчас: {today.day} {today.month}, {today.year}",
        parse_mode=ParseMode.HTML
    )


@router.message(AddScheduledEventStates.waiting_for_edit_title, F.text)
async def scheduled_event_process_edit_title(message: Message, state: FSMContext):
    """Обработка нового названия при редактировании"""
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_title(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    title = text_and_entities_to_html(message.text or "", message.entities or [])
    await state.update_data(title=title)
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    data = await state.get_data()
    user_id = message.from_user.id
    desc = data.get("description", "") or "—"
    db_fmt = data.get("event_datetime_db", "")
    remaining = format_time_remaining(db_fmt, user_id)
    display = data.get("event_datetime_display", "")
    confirm_text = (
        f"📅 Я распознал дату: <b>{display}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>💬 Описание:</b> {desc}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await message.answer(
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.message(
    AddScheduledEventStates.waiting_for_edit_description,
    F.text | F.photo | F.video | F.voice | F.video_note | F.document | F.caption
)
async def scheduled_event_process_edit_desc(message: Message, state: FSMContext):
    """Обработка нового описания при редактировании (текст или медиа с подписью)."""
    if await _is_duplicate_media_group(state, message, "_mg_seen_add_event_edit_desc"):
        return
    use_caption = message.caption is not None
    raw = (message.text or message.caption or "").strip()
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    desc = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or []),
    )
    media_type, media_file_id, media_path = await save_media(message)
    await state.update_data(
        description=desc,
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
    )
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    data = await state.get_data()
    title = data.get("title", "")
    display = data.get("event_datetime_display", "")
    db_fmt = data.get("event_datetime_db", "")
    remaining = format_time_remaining(db_fmt, message.from_user.id)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>💬 Описание:</b> {desc}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await message.answer(
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_skip_desc",
                       AddScheduledEventStates.waiting_for_edit_description)
async def scheduled_event_skip_desc_edit(callback: CallbackQuery, state: FSMContext):
    """Пропуск описания при редактировании"""
    await state.update_data(description="", media_type=None, media_file_id=None, media_path=None)
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    data = await state.get_data()
    title = data.get("title", "")
    display = data.get("event_datetime_display", "")
    db_fmt = data.get("event_datetime_db", "")
    remaining = format_time_remaining(db_fmt, callback.from_user.id)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await callback_edit_or_answer(callback, 
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.message(AddScheduledEventStates.waiting_for_edit_date, F.text)
async def scheduled_event_process_edit_date(message: Message, state: FSMContext):
    """Обработка новой даты при редактировании — вызов ИИ"""
    user_id = message.from_user.id
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введи дату текстом")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    msg_status = await message.answer("⏳ Определяю дату...")
    try:
        ctx = get_user_datetime_context(user_id)
        ai_date = await asyncio.to_thread(parse_date_with_ai, raw, ctx, True)
    except Exception as e:
        logger.error(f"Ошибка ИИ при распознавании даты: {e}")
        ai_date = ""
    try:
        await msg_status.delete()
    except Exception:
        pass
    if ai_date == "ERROR:PAST_DATE":
        await message.answer("❌ Не используй даты в прошлом, мне нужны будущие события")
        return
    if not ai_date:
        await message.answer("❌ Не удалось распознать дату. Попробуй иначе: 25.1.2026 или 25 января 2026 в 14:30")
        return
    db_format = parse_ai_date_to_db(ai_date)
    if not db_format:
        await message.answer("❌ Не удалось преобразовать дату. Попробуй: 25.1.2026")
        return
    from utils import calculate_next_occurrence
    next_db_format = calculate_next_occurrence(db_format, user_id)
    display_format = format_scheduled_event_datetime(next_db_format, user_id, user_id)
    await state.update_data(
        event_datetime_db=db_format,
        event_datetime_display=display_format,
        date_raw=raw
    )
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    data = await state.get_data()
    title = data.get("title", "")
    desc = data.get("description", "") or "—"
    remaining = format_time_remaining(next_db_format, user_id)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display_format}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>💬 Описание:</b> {desc}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await message.answer(
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "scheduled_event_edit_back",
                       AddScheduledEventStates.waiting_for_edit_choice)
async def scheduled_event_edit_back(callback: CallbackQuery, state: FSMContext):
    """Назад к подтверждению"""
    data = await state.get_data()
    await state.set_state(AddScheduledEventStates.waiting_for_confirm)
    title = data.get("title", "")
    desc = data.get("description", "") or "—"
    display = data.get("event_datetime_display", "")
    db_fmt = data.get("event_datetime_db", "")
    remaining = format_time_remaining(db_fmt, callback.from_user.id)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display}</b>\n\n"
        "Всё верно?\n\n"
        f"<b>ℹ️ Название:</b> {title}\n"
        f"<b>💬 Описание:</b> {desc}\n"
        f"<b>🕒 Осталось:</b> {remaining}"
    )
    await callback.message.edit_text(
        confirm_text,
        reply_markup=create_scheduled_event_confirm_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_event_edit_\d+$"))
async def scheduled_event_show_edit_options(callback: CallbackQuery, state: FSMContext):
    """Показать опции редактирования существующего события"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    keyboard = create_scheduled_event_existing_edit_keyboard(event_id)
    await callback.message.edit_text(
        f"✏️ <b>Редактирование:</b> {event.title or ''}\n\nЧто изменить?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_event_delete_\d+$"))
async def scheduled_event_delete_confirm(callback: CallbackQuery, state: FSMContext):
    """Подтверждение удаления события"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    delete_text = (
        f"❓ <b>Удалить событие?</b>\n\n"
        f"ℹ️ <b>{event.title or ''}</b>\n"
        f"📅 Дата: {format_scheduled_event_datetime(event.event_datetime, event.user_id, user_id)}"
    )
    keyboard = create_scheduled_event_delete_confirm_keyboard(event_id)
    try:
        await callback.message.edit_text(delete_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        await callback_edit_or_answer(callback, delete_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data.regexp(r"^scheduled_event_delete_yes_\d+$"))
async def scheduled_event_delete_yes(callback: CallbackQuery, state: FSMContext):
    """Удаление события и уведомление второго пользователя."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    success = db.delete_scheduled_event(event_id)
    if success:
        try:
            partner_id = db.get_partner_id(user_id)
            if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "events"):
                user_name = db.get_display_name(user_id, fallback="Партнёр")
                notify = f"🗑 {user_name} удалил(а) событие: <b>{event.title or ''}</b>"
                await callback.bot.send_message(partner_id, notify, parse_mode=ParseMode.HTML, force_new_message=True)
        except Exception as e:
            logger.error(f"Уведомление об удалении события: {e}")
        await callback.answer("Событие удалено")
        await safe_delete_callback_message(callback)
        per_page = 10
        events, total = db.get_scheduled_events_paged(page=1, per_page=per_page)
        total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
        text = (
            "🎯 <b>События на дату</b>\n\n"
            "Тут можно добавить событие которое в будущем (например цель) и бот будет показывать сколько осталось до этого события"
        )
        if total:
            text += f"\n\nВсего событий: {total}"
        else:
            text += "\n\n📭 Пока нет событий"
        await callback_edit_or_answer(callback, 
            text,
            reply_markup=create_scheduled_events_menu_keyboard(
                events, page=1, total_pages=total_pages, total=total, per_page=per_page
            ),
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.answer("Ошибка при удалении")


@router.callback_query(F.data.regexp(r"^scheduled_event_delete_no_\d+$"))
async def scheduled_event_delete_no(callback: CallbackQuery, state: FSMContext):
    """Отмена удаления — возврат к просмотру события (если сообщение было с медиа — удаляем и шлём заново)."""
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    user_id = callback.from_user.id
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        await safe_delete_callback_message(callback)
        await send_scheduled_event_with_media(
            chat_id=callback.message.chat.id, bot=callback.bot, event=event, text=text, keyboard=keyboard
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^scheduled_event_existing_edit_title_\d+$"))
async def scheduled_event_existing_edit_title(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование названия существующего события"""
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    await state.update_data(editing_event_id=event_id)
    await state.set_state(EditScheduledEventStates.waiting_for_title)
    await callback_edit_or_answer(callback, 
        f"ℹ️ Текущее название: {event.title or ''}\n\nНапиши новое:",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_event_existing_edit_desc_\d+$"))
async def scheduled_event_existing_edit_desc(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование описания существующего события"""
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    await state.update_data(editing_event_id=event_id)
    await state.set_state(EditScheduledEventStates.waiting_for_description)
    desc_preview = sanitize_html_for_telegram(truncate_text(event.description or "", 80))
    await callback_edit_or_answer(callback, 
        f"💬 Текущее описание: {desc_preview or '—'}\n\n"
        "Отправь новый текст (с форматированием) или фото/видео с подписью:",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_event_existing_edit_date_\d+$"))
async def scheduled_event_existing_edit_date(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование даты существующего события"""
    try:
        event_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    await state.update_data(editing_event_id=event_id)
    await state.set_state(EditScheduledEventStates.waiting_for_date_raw)
    today = datetime.now()
    uid = callback.from_user.id
    await callback_edit_or_answer(callback, 
        f"📅 Текущая дата: {format_scheduled_event_datetime(event.event_datetime, event.user_id, uid)}\n\n"
        "Напиши новую дату в любом формате. Сейчас: "
        f"{today.day} {today.month}, {today.year}",
        parse_mode=ParseMode.HTML
    )


@router.message(EditScheduledEventStates.waiting_for_title, F.text)
async def scheduled_event_existing_process_title(message: Message, state: FSMContext):
    """Обработка нового названия при редактировании существующего события"""
    data = await state.get_data()
    event_id = data.get("editing_event_id")
    if not event_id:
        await state.clear()
        return
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_title(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    title = text_and_entities_to_html(message.text or "", message.entities or [])
    db.update_scheduled_event(event_id, title=title)
    await state.clear()
    event = db.get_scheduled_event(event_id)
    if not event:
        await message.answer(MSG_EVENT_NOT_FOUND)
        return
    user_id = message.from_user.id
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = "✅ Название изменено!\n\n" + format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    await send_scheduled_event_with_media(
        chat_id=message.chat.id, bot=message.bot, event=event, text=text, keyboard=keyboard
    )
    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "events"):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            await message.bot.send_message(
                partner_id,
                f"✨ {user_name} изменил(а) название события\n\n" + format_scheduled_event_text(event, partner_id, expired=expired),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"scheduled_event_{event.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Уведомление об изменении события: {e}")


@router.message(
    EditScheduledEventStates.waiting_for_description,
    F.text | F.photo | F.video | F.voice | F.video_note | F.document | F.caption
)
async def scheduled_event_existing_process_desc(message: Message, state: FSMContext):
    """Обработка нового описания при редактировании (текст с форматированием или медиа с подписью)."""
    if await _is_duplicate_media_group(state, message, "_mg_seen_edit_existing_event_desc"):
        return
    data = await state.get_data()
    event_id = data.get("editing_event_id")
    if not event_id:
        await state.clear()
        return
    use_caption = message.caption is not None
    raw = (message.text or message.caption or "").strip()
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    content = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or []),
    )
    media_type, media_file_id, media_path = await save_media(message)
    db.update_scheduled_event(
        event_id,
        description=content,
        media_type=media_type or None,
        media_file_id=media_file_id or None,
        media_path=media_path or None,
    )
    await state.clear()
    event = db.get_scheduled_event(event_id)
    if not event:
        await message.answer(MSG_EVENT_NOT_FOUND)
        return
    user_id = message.from_user.id
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = "✅ Описание изменено!\n\n" + format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    await send_scheduled_event_with_media(
        chat_id=message.chat.id, bot=message.bot, event=event, text=text, keyboard=keyboard
    )
    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "events"):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            await message.bot.send_message(
                partner_id,
                f"✨ {user_name} изменил(а) описание события\n\n" + format_scheduled_event_text(event, partner_id, expired=expired),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"scheduled_event_{event.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Уведомление об изменении события: {e}")


@router.message(EditScheduledEventStates.waiting_for_date_raw, F.text)
async def scheduled_event_existing_process_date(message: Message, state: FSMContext):
    """Обработка новой даты при редактировании — вызов ИИ"""
    user_id = message.from_user.id
    data = await state.get_data()
    event_id = data.get("editing_event_id")
    if not event_id:
        await state.clear()
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введи дату текстом")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    msg_status = await message.answer("⏳ Определяю дату...")
    try:
        ctx = get_user_datetime_context(user_id)
        ai_date = await asyncio.to_thread(parse_date_with_ai, raw, ctx, True)
    except Exception as e:
        logger.error(f"Ошибка ИИ при распознавании даты: {e}")
        ai_date = ""
    try:
        await msg_status.delete()
    except Exception:
        pass
    if ai_date == "ERROR:PAST_DATE":
        await message.answer("❌ Не используй даты в прошлом, мне нужны будущие события")
        return
    if not ai_date:
        await message.answer(
            "❌ Не удалось распознать дату. Попробуй иначе: 25.1.2026 или 25 января 2026 в 14:30"
        )
        return
    db_format = parse_ai_date_to_db(ai_date)
    if not db_format:
        await message.answer("❌ Не удалось преобразовать дату. Попробуй: 25.1.2026")
        return
    from utils import calculate_next_occurrence
    next_db_format = calculate_next_occurrence(db_format, user_id)
    display_format = format_scheduled_event_datetime(next_db_format, user_id, user_id)
    await state.update_data(
        new_event_datetime_db=db_format,
        new_event_datetime_display=display_format
    )
    await state.set_state(EditScheduledEventStates.waiting_for_date_confirm)
    confirm_text = (
        f"📅 Я распознал дату: <b>{display_format}</b>\n\n"
        "Изменить дату события на эту?"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, верно", callback_data="scheduled_event_existing_date_confirm_yes")],
        [InlineKeyboardButton(text="❌ Нет, изменить", callback_data="scheduled_event_existing_date_confirm_no")]
    ])
    await message.answer(confirm_text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "scheduled_event_existing_date_confirm_yes",
                       EditScheduledEventStates.waiting_for_date_confirm)
async def scheduled_event_existing_date_confirm_yes(callback: CallbackQuery, state: FSMContext):
    """Подтверждение изменения даты — обновляем в БД"""
    user_id = callback.from_user.id
    data = await state.get_data()
    event_id = data.get("editing_event_id")
    db_format = data.get("new_event_datetime_db")
    if not event_id or not db_format:
        await callback.answer("Ошибка: данные потеряны")
        await state.clear()
        return
    db.update_scheduled_event(event_id, event_datetime=db_format)
    await state.clear()
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = "✅ Дата изменена!\n\n" + format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    await safe_delete_callback_message(callback)
    await send_scheduled_event_with_media(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        event=event,
        text=text,
        keyboard=keyboard,
    )
    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "events"):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            await callback.bot.send_message(
                partner_id,
                f"✨ {user_name} изменил(а) дату события\n\n" + format_scheduled_event_text(event, partner_id, expired=expired),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"scheduled_event_{event.id}")]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Уведомление об изменении события: {e}")
    await callback.answer()


@router.callback_query(F.data == "scheduled_event_existing_date_confirm_no",
                       EditScheduledEventStates.waiting_for_date_confirm)
async def scheduled_event_existing_date_confirm_no(callback: CallbackQuery, state: FSMContext):
    """Отмена изменения даты — возврат к опциям редактирования"""
    data = await state.get_data()
    event_id = data.get("editing_event_id")
    if not event_id:
        await callback.answer("Ошибка")
        await state.clear()
        return
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        await state.clear()
        return
    await state.clear()
    keyboard = create_scheduled_event_existing_edit_keyboard(event_id)
    await callback.message.edit_text(
        f"✏️ <b>Редактирование:</b> {event.title or ''}\n\nЧто изменить?",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.regexp(r"^scheduled_event_\d+$"))
async def scheduled_event_view(callback: CallbackQuery, state: FSMContext):
    """Просмотр конкретного события (с медиа при наличии)."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    raw = callback.data.replace("scheduled_event_", "")
    if not raw.isdigit():
        return
    event_id = int(raw)
    event = db.get_scheduled_event(event_id)
    if not event:
        await callback.answer(MSG_EVENT_NOT_FOUND)
        return
    await safe_delete_callback_message(callback)
    expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
    text = format_scheduled_event_text(event, user_id, expired=expired)
    keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
    await send_scheduled_event_with_media(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        event=event,
        text=text,
        keyboard=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("search_results_p"))
async def search_results_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация результатов поиска"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    data = await state.get_data()
    query = data.get("search_query")
    if not query:
        await callback.answer("Сессия поиска истекла")
        return
    try:
        page = int(callback.data.replace("search_results_p", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    memories = db.search_memories_fuzzy(query, limit=100)
    if not memories:
        await callback.answer("Результаты поиска пусты")
        return
    try:
        await callback.message.edit_text(
            f"🔍 <b>Поиск: «{query}»</b>\n\nНайдено: {len(memories)}",
            reply_markup=create_search_results_keyboard(memories, page=page, user_id=user_id),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("catmem_"))
async def show_memory_from_category(callback: CallbackQuery, state: FSMContext):
    """Детальный просмотр воспоминания из категории (с сохранением страницы)"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    # Формат: catmem_<category_key>_<page>_<memory_id> (category_key может содержать _: important_moments, important_dates)
    raw = callback.data.replace("catmem_", "", 1)
    parts = raw.rsplit("_", 2)
    if len(parts) != 3:
        await callback.answer("Ошибка")
        return
    try:
        category_key = parts[0]
        page = int(parts[1])
        memory_id = int(parts[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return

    # Проверяем приватность
    privacy = db.get_memory_privacy(memory_id)
    p_type = (privacy.get("privacy_type") or "").strip()
    owner_id = privacy.get("owner_id") or memory.user_id
    owner_id = privacy.get("owner_id") or memory.user_id
    remaining_views = None

    if p_type == "password" and user_id != owner_id:
        question = privacy.get("privacy_question") or "Ответь на вопрос для доступа к этому моменту."
        await state.set_state(ViewMemoryStates.waiting_for_password)
        await state.update_data(memory_id=memory_id)
        await callback_edit_or_answer(callback, 
            "🔒 <b>Доступ по паролю</b>\n\n"
            f"❓ Вопрос:\n<b>{question}</b>\n\n"
            "💬 Напиши ответ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return
    elif p_type == "limited_views":
        used, limit, allowed = db.register_memory_view(memory_id, user_id)
        if not allowed:
            await callback_edit_or_answer(callback, 
                "🚫 Лимит просмотров этого момента для тебя исчерпан",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        if limit:
            remaining_views = max(limit - used, 0)

    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    text = format_memory_text(memory, user_id, remaining_views=remaining_views)
    keyboard = create_memory_detail_keyboard(memory_id, memory.category, from_search=False, page=page, user_id=user_id)
    if _memory_has_any_media(memory):
        await _delete_callback_message_silent(callback)
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=keyboard,
        )
    else:
        await callback_edit_or_answer(
            callback,
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data.startswith("memory_search_"))
async def show_memory_from_search(callback: CallbackQuery, state: FSMContext):
    """Детальный просмотр воспоминания из результатов поиска"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return

    # Проверяем приватность
    privacy = db.get_memory_privacy(memory_id)
    p_type = (privacy.get("privacy_type") or "").strip()
    remaining_views = None

    if p_type == "password" and user_id != db.get_creator_id():
        question = privacy.get("privacy_question") or "Ответь на вопрос для доступа к этому моменту."
        await state.set_state(ViewMemoryStates.waiting_for_password)
        await state.update_data(memory_id=memory_id)
        await callback_edit_or_answer(callback, 
            "🔒 <b>Доступ по паролю</b>\n\n"
            f"Вопрос:\n{question}\n\n"
            "💬 Напиши ответ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return
    elif p_type == "limited_views":
        used, limit, allowed = db.register_memory_view(memory_id, user_id)
        if not allowed:
            await callback_edit_or_answer(callback, 
                "🚫 Лимит просмотров этого момента для тебя исчерпан",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        if limit:
            remaining_views = max(limit - used, 0)

    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    text = format_memory_text(memory, user_id, remaining_views=remaining_views)
    keyboard = create_memory_detail_keyboard(memory_id, memory.category, from_search=True, user_id=user_id)
    if _memory_has_any_media(memory):
        await _delete_callback_message_silent(callback)
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=keyboard
        )
    else:
        await callback_edit_or_answer(
            callback,
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == "back_to_search_results")
async def back_to_search_results(callback: CallbackQuery, state: FSMContext):
    """Возврат к результатам поиска"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    data = await state.get_data()
    query = data.get("search_query")
    page = data.get("search_page", 1)
    if not query:
        welcome_text = format_welcome_message(user_id)
        await state.clear()
        await callback_edit_or_answer(callback, 
            welcome_text,
            reply_markup=create_main_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
        return
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    memories = db.search_memories_fuzzy(query, limit=100)
    if not memories:
        await callback_edit_or_answer(callback, 
            f"🔍 <b>Поиск: «{query}»</b>\n\n📭 Ничего не найдено.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML
        )
    else:
        await callback_edit_or_answer(callback, 
            f"🔍 <b>Поиск: «{query}»</b>\n\nНайдено: {len(memories)}",
            reply_markup=create_search_results_keyboard(memories, page=page, user_id=user_id),
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "settings_menu")
async def settings_menu(callback: CallbackQuery):
    """Раздел «Настройки» в главном меню"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback_edit_or_answer(callback, 
        "⚙️ <b>Настройки</b>\n\n"
        "Выбери, что хочешь настроить:",
        reply_markup=create_settings_menu_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "settings_time")
async def settings_time(callback: CallbackQuery):
    """Подпункт «Время в боте»: выбор часового пояса"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    current_tz = db.get_user_setting(user_id, "timezone")
    current_display = db.get_user_setting(user_id, "timezone_display")
    label = get_timezone_label_for_display(current_tz, current_display)
    await callback_edit_or_answer(callback, 
        "🕐 <b>Время в боте</b>\n\n"
        f"Сейчас выбрано: <i>{label}</i>\n\n"
        "Выбери часовой пояс — в боте даты и время будут показываться в нём:",
        reply_markup=create_settings_time_keyboard(current_tz, current_display),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("settings_time_set_"))
async def settings_time_set(callback: CallbackQuery):
    """Установка часового пояса пользователя"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    # Новый формат: settings_time_set_Europe|Moscow -> Europe/Moscow
    # Legacy формат с "_" сохраняем для обратной совместимости.
    suffix = callback.data.replace("settings_time_set_", "")
    if "|" in suffix:
        tz_id = suffix.replace("|", "/")
    else:
        # Legacy decode: пробуем прямое значение из predefined options.
        tz_id = suffix
        for opt_tz, _opt_label in TIMEZONE_OPTIONS:
            if suffix == opt_tz.replace("/", "_"):
                tz_id = opt_tz
                break
        else:
            # Старый fallback (может ломать зоны с `_`, но нужен для старых кнопок в чате).
            tz_id = suffix.replace("_", "/")
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(tz_id)
    except Exception:
        await callback.answer("Неизвестный вариант")
        return
    db.set_user_setting(user_id, "timezone", tz_id)
    db.set_user_setting(user_id, "timezone_display", "")
    label = get_timezone_label(tz_id)
    await callback.answer(f"Время в боте: {label}")
    try:
        await callback.message.edit_text(
            "🕐 <b>Время в боте</b>\n\n"
            f"✅ Установлено: <i>{label}</i>\n\n"
            "Можно выбрать другой вариант:",
            reply_markup=create_settings_time_keyboard(tz_id, None),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


@router.callback_query(F.data == "settings_time_city_start")
async def settings_time_city_start(callback: CallbackQuery, state: FSMContext):
    """Запрашивает у пользователя город/часовой пояс в свободной форме."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.update_data(
        tz_mode="settings_time_city",
        tz_target_user_id=user_id,
        tz_candidate=None,
        tz_candidate_display=None,
    )
    await state.set_state(TimezoneStates.waiting_for_input)
    await callback_edit_or_answer(
        callback,
        "🏙 <b>Указать город</b>\n\n"
        "Напиши город или часовой пояс в свободной форме.\n"
        "Примеры: <code>Europe/Moscow</code>, <code>Moscow</code>, <code>Москва</code>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="settings_time")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "settings_time_city_confirm_yes")
async def settings_time_city_confirm_yes(callback: CallbackQuery, state: FSMContext):
    """Подтверждение распознанного TZ и запись в БД."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    data = await state.get_data()
    tz_mode = data.get("tz_mode")
    tz_id = data.get("tz_candidate")
    display_name = data.get("tz_candidate_display")
    if tz_mode != "settings_time_city" or not tz_id:
        await callback.answer("Сначала укажи город")
        return
    db.set_user_setting(user_id, "timezone", tz_id)
    if isinstance(display_name, str) and display_name.strip():
        db.set_user_setting(user_id, "timezone_display", display_name.strip())
    else:
        db.set_user_setting(user_id, "timezone_display", "")
    await state.clear()
    label = _format_tz_with_now(tz_id, display_name)
    await callback_edit_or_answer(
        callback,
        "🕐 <b>Время в боте</b>\n\n"
        f"✅ Твой часовой пояс: <i>{label}</i>\n\n"
        "Можно выбрать другой вариант:",
        reply_markup=create_settings_time_keyboard(tz_id, display_name),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer("Сохранено")


@router.callback_query(F.data == "settings_time_city_confirm_no")
async def settings_time_city_confirm_no(callback: CallbackQuery, state: FSMContext):
    """Повторный ввод города/TZ."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.update_data(
        tz_mode="settings_time_city",
        tz_target_user_id=user_id,
        tz_candidate=None,
        tz_candidate_display=None,
    )
    await state.set_state(TimezoneStates.waiting_for_input)
    await callback_edit_or_answer(
        callback,
        "✏️ Напиши ещё раз город или часовой пояс.\n"
        "Примеры: <code>Europe/Moscow</code>, <code>Moscow</code>, <code>Москва</code>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="settings_time")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


def _notif_main_keyboard(user_id: int, enabled: bool) -> "InlineKeyboardMarkup":
    """Вспомогательная функция: собирает главную клавиатуру уведомлений для user_id."""
    cat_states = db.get_all_category_notif(user_id) if enabled else {}
    is_creator = db.is_creator(user_id)
    return create_settings_notifications_keyboard(enabled, cat_states, is_creator)


@router.callback_query(F.data == "settings_notifications")
async def settings_notifications(callback: CallbackQuery):
    """Подпункт «Уведомления»: главный экран с категориями."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    enabled = db.are_notifications_enabled(user_id)
    text = build_notif_main_text(enabled)
    keyboard = _notif_main_keyboard(user_id, enabled)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        await callback_edit_or_answer(callback, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data.startswith("settings_notifications_set_"))
async def settings_notifications_set(callback: CallbackQuery):
    """Глобальное включение / отключение уведомлений."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    suffix = callback.data.replace("settings_notifications_set_", "")
    enabled = suffix == "1"
    db.set_user_setting(user_id, "notifications_enabled", "1" if enabled else "0")
    await callback.answer("✅ Включены" if enabled else "❌ Отключены")
    text = build_notif_main_text(enabled)
    keyboard = _notif_main_keyboard(user_id, enabled)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.callback_query(F.data.regexp(r"^notif_cat_(\w+)_set_([01])$"))
async def notif_cat_set(callback: CallbackQuery):
    """Переключение уведомлений отдельной категории."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    import re as _re
    m = _re.match(r"^notif_cat_(\w+)_set_([01])$", callback.data)
    if not m:
        await callback.answer()
        return
    cat_key = m.group(1)
    enabled = m.group(2) == "1"
    db.set_category_notif(user_id, cat_key, enabled)
    await callback.answer("✅ Включены" if enabled else "❌ Отключены")
    text = build_notif_cat_text(cat_key, enabled)
    keyboard = create_notif_cat_keyboard(cat_key, enabled)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.callback_query(F.data.regexp(r"^notif_cat_(\w+)$"))
async def notif_cat_detail(callback: CallbackQuery):
    """Детальная настройка отдельной категории уведомлений."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    cat_key = callback.data[len("notif_cat_"):]
    from utils import NOTIF_CATEGORIES_CREATOR
    creator_cats = [k for k, _, _ in NOTIF_CATEGORIES_CREATOR]
    if cat_key in creator_cats and not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    enabled = db.is_category_notif_enabled(user_id, cat_key)
    text = build_notif_cat_text(cat_key, enabled)
    keyboard = create_notif_cat_keyboard(cat_key, enabled)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        await callback_edit_or_answer(callback, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data == "settings_favorites")
async def settings_favorites(callback: CallbackQuery):
    """Подпункт «Избранное»: вкл/выкл (доступно админам, создателю, Ксюше)"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    current = db.is_favorites_enabled(user_id)
    await callback_edit_or_answer(callback, 
        "⭐️ <b>Избранное</b>\n\n"
        "Ты можешь добавить любой момент/событие/желание и тд вообщем всё что есть в боте\n\n"
        f"Сейчас: <i>{'включено' if current else 'выключено'}</i>",
        reply_markup=create_settings_favorites_keyboard(current),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data.startswith("settings_favorites_set_"))
async def settings_favorites_set(callback: CallbackQuery):
    """Установка вкл/выкл избранного"""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    suffix = callback.data.replace("settings_favorites_set_", "")
    enabled = suffix == "1"
    db.set_user_setting(user_id, "favorites_enabled", "1" if enabled else "0")
    await callback.answer("Избранное: " + ("включено" if enabled else "выключено"))
    try:
        text = (
            "⭐️ <b>Избранное</b>\n\n"
            "Ты можешь добавить любой момент/событие/желание и тд вообщем всё что есть в боте\n\n"
            f"✅ Установлено: <i>{'включено' if enabled else 'выключено'}</i>"
        )
        await callback.message.edit_text(
            text,
            reply_markup=create_settings_favorites_keyboard(enabled),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass


@router.callback_query(F.data == "favorites_menu")
async def favorites_menu(callback: CallbackQuery, state: FSMContext):
    """Раздел «Избранное»: статистика и список."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED_OR_FAVORITES_OFF)
        return
    await state.clear()
    stats = db.get_user_favorites_stats(user_id)
    cat = config.CATEGORIES
    important_moments = stats.get("important_moments", 0)
    memories_count = stats.get("memories", 0)
    important_dates = stats.get("important_dates", 0)
    scheduled_events = stats.get("scheduled_events", 0)
    wishes = stats.get("wishes", 0)
    text = (
        "⭐️ <b>Избранное</b>\n\n"
        "Сюда ты можешь добавить воспоминания, события или желания, вообщем всё что есть в боте"
    )
    items, total = db.get_user_favorites_paged(user_id, page=1, per_page=10)
    total_pages = max(1, (total + 9) // 10) if total else 1
    await callback_edit_or_answer(callback, 
        text,
        reply_markup=create_favorites_menu_keyboard(items, page=1, total_pages=total_pages, total=total, per_page=10),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "favorites_add_menu")
async def favorites_add_menu(callback: CallbackQuery, state: FSMContext):
    """Меню «Добавить» в избранном: категории, события, желания (без настроек и админ-панели)."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback_edit_or_answer(callback, 
        "➕ <b>Добавить в избранное</b>\n\n"
        "Выбери раздел — перейди к нужному моменту/событию/желанию и нажми «Добавить в избранное»:",
        reply_markup=create_favorites_add_menu_keyboard(user_id),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^favorites_p\d+$"))
async def favorites_page(callback: CallbackQuery, state: FSMContext):
    """Пагинация списка избранного."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    page = int(callback.data.replace("favorites_p", ""))
    per_page = 10
    items, total = db.get_user_favorites_paged(user_id, page=page, per_page=per_page)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    stats = db.get_user_favorites_stats(user_id)
    text = (
        "⭐️ <b>Избранное</b>\n\n"
        f"Страница {page}/{total_pages}. Всего: {total}\n\n"
        f"💫 Важных моментов: {stats.get('important_moments', 0)} | "
        f"📖 Воспоминаний: {stats.get('memories', 0)} | "
        f"📅 Важных дат: {stats.get('important_dates', 0)}\n"
        f"🎯 Событий: {stats.get('scheduled_events', 0)} | "
        f"💫 Желаний: {stats.get('wishes', 0)}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=create_favorites_menu_keyboard(items, page=page, total_pages=total_pages, total=total, per_page=per_page),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data == "favorites_page_info")
async def favorites_page_info(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "favorites_search")
async def favorites_search_start(callback: CallbackQuery, state: FSMContext):
    """Начало поиска по избранному."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.set_state(FavoritesSearchStates.waiting_for_query)
    await callback_edit_or_answer(callback, 
        "🔍 <b>Поиск по избранному</b>\n\nВведите текст для поиска:",
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.message(FavoritesSearchStates.waiting_for_query, F.text)
async def favorites_search_query(message: Message, state: FSMContext):
    """Обработка поискового запроса по избранному."""
    user_id = message.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        return
    query = (message.text or "").strip()
    await state.update_data(favorites_search_query=query)
    per_page = 10
    items, total = db.get_user_favorites_paged(user_id, page=1, per_page=per_page, query=query)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    text = (
        "⭐️ <b>Результаты поиска</b>\n\n"
        f"Найдено: {total}"
    )
    if not items:
        text += "\n\nНичего не найдено."
    await message.answer(
        text,
        reply_markup=create_favorites_menu_keyboard(
            items, page=1, total_pages=total_pages, total=total, per_page=per_page, from_search=True
        ),
        parse_mode=ParseMode.HTML
    )
    await state.update_data(favorites_search_query=query)


@router.callback_query(F.data.regexp(r"^favorites_search_p\d+$"))
async def favorites_search_page(callback: CallbackQuery, state: FSMContext):
    """Страница результатов поиска избранного."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    page = int(callback.data.replace("favorites_search_p", ""))
    data = await state.get_data()
    query = (data.get("favorites_search_query") or "").strip()
    per_page = 10
    items, total = db.get_user_favorites_paged(user_id, page=page, per_page=per_page, query=query)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages
    text = (
        "⭐️ <b>Результаты поиска</b>\n\n"
        f"Найдено: {total}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=create_favorites_menu_keyboard(
            items, page=page, total_pages=total_pages, total=total, per_page=per_page, from_search=True
        ),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^favorite_toggle_memory_(\d+)$"))
async def favorite_toggle_memory(callback: CallbackQuery, state: FSMContext):
    """Добавить/удалить воспоминание из избранного."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    memory_id = int(callback.data.split("_")[-1])
    in_fav = db.is_in_favorites(user_id, "memory", memory_id)
    if in_fav:
        db.remove_favorite(user_id, "memory", memory_id)
        await callback.answer("Удалено из избранного")
    else:
        db.add_favorite(user_id, "memory", memory_id)
        await callback.answer("Добавлено в избранное")
    memory = db.get_memory(memory_id)
    if memory:
        text = format_memory_text(memory, user_id)
        keyboard = create_memory_detail_keyboard(memory_id, memory.category, from_search=False, user_id=user_id)
        if _memory_has_any_media(memory):
            await _delete_callback_message_silent(callback)
            await send_message_with_media(callback.message.chat.id, callback.bot, memory, text, keyboard)
        else:
            await callback_edit_or_answer(callback, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.regexp(r"^favorite_toggle_scheduled_event_(\d+)$"))
async def favorite_toggle_scheduled_event(callback: CallbackQuery, state: FSMContext):
    """Добавить/удалить событие из избранного."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    event_id = int(callback.data.split("_")[-1])
    in_fav = db.is_in_favorites(user_id, "scheduled_event", event_id)
    if in_fav:
        db.remove_favorite(user_id, "scheduled_event", event_id)
        await callback.answer("Удалено из избранного")
    else:
        db.add_favorite(user_id, "scheduled_event", event_id)
        await callback.answer("Добавлено в избранное")
    event = db.get_scheduled_event(event_id)
    if event:
        expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
        text = format_scheduled_event_text(event, user_id, expired=expired)
        keyboard = create_scheduled_event_detail_keyboard(event_id, expired=expired, user_id=user_id)
        await send_scheduled_event_with_media(
            chat_id=callback.message.chat.id, bot=callback.bot, event=event, text=text, keyboard=keyboard
        )
    else:
        await callback.answer()


@router.callback_query(F.data.regexp(r"^favorite_toggle_wish_(\d+)$"))
async def favorite_toggle_wish(callback: CallbackQuery, state: FSMContext):
    """Добавить/удалить желание из избранного."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    wish_id = int(callback.data.split("_")[-1])
    in_fav = db.is_in_favorites(user_id, "wish", wish_id)
    if in_fav:
        db.remove_favorite(user_id, "wish", wish_id)
        await callback.answer("Удалено из избранного")
    else:
        db.add_favorite(user_id, "wish", wish_id)
        await callback.answer("Добавлено в избранное")
    wish = db.get_wish(wish_id)
    if wish:
        text = "✅ Избранное обновлено\n\n" + format_wish_text(wish, user_id)
        back_target = "wishes_menu"
        keyboard = create_wish_keyboard(wish_id, user_id, back_target=back_target)
        await send_wish_with_media(callback.message.chat.id, callback.bot, wish, text, keyboard)
    else:
        await callback.answer()


@router.callback_query(F.data.regexp(r"^favorite_view_(memory|scheduled_event|wish)_(\d+)$"))
async def favorite_view_item(callback: CallbackQuery, state: FSMContext):
    """Просмотр элемента из избранного (то же, что при открытии из главного меню)."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id) or not db.is_favorites_enabled(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    m = re.match(r"^favorite_view_(memory|scheduled_event|wish)_(\d+)$", callback.data)
    if not m:
        await callback.answer("Ошибка")
        return
    item_type = m.group(1)
    item_id = int(m.group(2))
    if item_type == "memory":
        memory = db.get_memory(item_id)
        if not memory:
            await callback.answer(MSG_MEMORY_NOT_FOUND)
            return
        text = format_memory_text(memory, user_id)
        keyboard = create_memory_detail_keyboard(item_id, memory.category, from_search=False, user_id=user_id)
        if _memory_has_any_media(memory):
            await _delete_callback_message_silent(callback)
            await send_message_with_media(callback.message.chat.id, callback.bot, memory, text, keyboard)
        else:
            await callback_edit_or_answer(callback, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    elif item_type == "scheduled_event":
        event = db.get_scheduled_event(item_id)
        if not event:
            await callback.answer(MSG_EVENT_NOT_FOUND)
            return
        expired = is_scheduled_event_moment_passed(event.event_datetime, event.user_id)
        text = format_scheduled_event_text(event, user_id, expired=expired)
        keyboard = create_scheduled_event_detail_keyboard(item_id, expired=expired, user_id=user_id)
        await send_scheduled_event_with_media(callback.message.chat.id, callback.bot, event, text, keyboard)
    elif item_type == "wish":
        wish = db.get_wish(item_id)
        if not wish:
            await callback.answer(MSG_WISH_NOT_FOUND)
            return
        text = format_wish_text(wish, user_id)
        back_target = "wishes_menu"
        keyboard = create_wish_keyboard(item_id, user_id, back_target=back_target)
        await send_wish_with_media(callback.message.chat.id, callback.bot, wish, text, keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("back_to_category_"))
async def back_to_category(callback: CallbackQuery):
    """Возврат к категории"""
    user_id = callback.from_user.id
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    raw = callback.data.replace("back_to_category_", "")
    page = 1
    if "_p" in raw:
        category_key, page_part = raw.split("_p", 1)
        try:
            page = int(page_part)
        except ValueError:
            page = 1
    else:
        category_key = raw

    couple = db.get_couple_by_user(user_id)
    couple_id = couple['id'] if couple else None
    all_cats = get_all_categories(couple_id)
    category = all_cats.get(category_key)

    if not db.is_admin(user_id) or not category:
        await callback.answer("Ошибка доступа")
        return
    memories = db.get_memories_by_category(category_key, couple_id=couple_id)
    count = len(memories)
    
    message_text = f"<b>{category['title']}</b>\n"
    message_text += f"<i>{category['description']}</i>\n\n"
    
    if count > 0:
        message_text += f"Всего моментов: {count}\n\n"
        message_text += f"Выбери воспоминание:"
    else:
        message_text += "📭 <i>В этой категории пока нет воспоминаний...</i>"
    await callback_edit_or_answer(callback, 
        message_text,
        reply_markup=create_category_keyboard_paged(category_key, memories, page=page, user_id=callback.from_user.id),
        parse_mode=ParseMode.HTML
    )

async def _show_category_common(callback: CallbackQuery, category_key: str, page: int) -> None:
    """Общая логика показа списка воспоминаний по категории."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    couple = db.get_couple_by_user(user_id)
    couple_id = couple['id'] if couple else None
    all_cats = get_all_categories(couple_id)
    category = all_cats.get(category_key)
    if not category:
        await callback.answer("Категория не найдена")
        return

    memories = db.get_memories_by_category(category_key, couple_id=couple_id)
    count = len(memories)

    message_text = f"<b>{category['title']}</b>\n"
    message_text += f"<i>{category['description']}</i>\n\n"

    if count > 0:
        message_text += f"Всего моментов: {count}\n\n"
        message_text += "Выбери воспоминание:"
    else:
        message_text += "📭 <i>В этой категории пока нет воспоминаний...</i>"
    await callback_edit_or_answer(callback, 
        message_text,
        reply_markup=create_category_keyboard_paged(category_key, memories, page=page, user_id=user_id),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("cancel_adding_"))
async def cancel_adding(callback: CallbackQuery, state: FSMContext):
    """Отмена добавления воспоминания и возврат к списку категории."""
    await state.clear()
    category_key = callback.data.replace("cancel_adding_", "")
    await _show_category_common(callback, category_key, page=1)


@router.callback_query(F.data.startswith("category_"))
async def show_category(callback: CallbackQuery):
    """Отображение категории с воспоминаниями (с пагинацией)."""
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    raw = callback.data.replace("category_", "")
    page = 1
    if "_p" in raw:
        category_key, page_part = raw.split("_p", 1)
        try:
            page = int(page_part)
        except ValueError:
            page = 1
    else:
        category_key = raw
    
    await _show_category_common(callback, category_key, page=page)

@router.callback_query(F.data.startswith("add_to_"))
async def start_adding_memory(callback: CallbackQuery, state: FSMContext):
    """Начало добавления нового воспоминания"""
    category_key = callback.data.replace("add_to_", "")
    user_id = callback.from_user.id
    couple = db.get_couple_by_user(user_id)
    couple_id = couple['id'] if couple else None
    all_cats = get_all_categories(couple_id)
    category = all_cats.get(category_key)

    if not category:
        await callback.answer("Ошибка категории")
        return
    
    await state.update_data(category=category_key)
    await state.set_state(AddMemoryStates.waiting_for_title)
    await callback_edit_or_answer(callback, 
        "📝 <b>Добавление нового момента</b>\n\n"
        "Шаг 1 из 3\n\n"
        "Придумай название для этого момента\n"
        "Например: <i>Новый год, Первая встреча, День рождения</i>\n\n",
        reply_markup=create_cancel_adding_keyboard(category_key),
        parse_mode=ParseMode.HTML
    )

@router.message(SearchStates.waiting_for_search_query)
async def process_search_query(message: Message, state: FSMContext):
    """Обработка запроса поиска"""
    user_id = message.from_user.id
    if not db.is_admin(user_id):
        await state.clear()
        return
    raw = (message.text or message.caption or "").strip()
    if raw.lower() == "/cancel":
        await state.clear()
        welcome_text = format_welcome_message(user_id)
        await message.answer(
            welcome_text,
            reply_markup=create_main_keyboard(user_id),
            parse_mode=ParseMode.HTML
        )
        return
    if not raw:
        await message.answer(
            "🔍 Введи ключевое слово или фразу для поиска",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="search_cancel")]
            ]),
            parse_mode=ParseMode.HTML
        )
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    memories = db.search_memories_fuzzy(raw, limit=100)
    await state.update_data(search_query=raw, search_page=1)
    if not memories:
        await message.answer(
            f"🔍 <b>Поиск: «{raw}»</b>\n\n📭 Ничего не найдено",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            f"🔍 <b>Поиск: «{raw}»</b>\n\nНайдено: {len(memories)}",
            reply_markup=create_search_results_keyboard(memories, page=1, user_id=user_id),
            parse_mode=ParseMode.HTML
        )


@router.message(AddMemoryStates.waiting_for_title)
async def process_memory_title(message: Message, state: FSMContext):
    """Обработка названия воспоминания (текст + форматирование сохраняются как HTML)"""
    data = await state.get_data()
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_title(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    title = text_and_entities_to_html(message.text or "", message.entities or [])
    await state.update_data(title=title)
    await state.set_state(AddMemoryStates.waiting_for_date)
    
    category_key = data['category']
    await message.answer(
        "Шаг 2 из 3\n\n"
        "Теперь напиши, когда это было\n"
        "Можно в любом формате например:\n"
        "• <i>1 января 2026</i>\n"
        "• <i>Весна 2026</i>\n"
        "• <i>{month_name}</i> — подставится название месяца",
        reply_markup=create_cancel_adding_keyboard(category_key),
        parse_mode=ParseMode.HTML
    )

@router.message(AddMemoryStates.waiting_for_date)
async def process_memory_date(message: Message, state: FSMContext):
    """Обработка даты воспоминания (текст + форматирование сохраняются как HTML)"""
    data = await state.get_data()
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_date(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    date = text_and_entities_to_html(message.text or "", message.entities or [])
    await state.update_data(date=date)
    await state.set_state(AddMemoryStates.waiting_for_content)
    
    category_key = data['category']
    params_help = get_params_help_text()
    await message.answer(
        "Шаг 3 из 3\n\n"
        "Теперь самое интересное. Тут будет много текста но тут все легко принципе, короче опиши этот момент. Ты можешь:\n\n"
        "• Написать текст с форматированием: <b>жирный</b>, <i>курсив</i>, <u>подчеркнутый</u>, <s>зачеркнутый</s>, <code>моно</code> или в виде ссылки\n"
        "• Добавить фото, видео, кружок и тд)\n"
        f"{params_help}\n\n"
        "Ну например: <i>Привет любимый(ая), мы с тобой уже {days_together} дней!</i>",
        reply_markup=create_cancel_adding_keyboard(category_key),
        parse_mode=ParseMode.HTML
    )

@router.message(AddMemoryStates.waiting_for_content)
async def process_memory_content(message: Message, state: FSMContext):
    """Обработка описания воспоминания — текст/подпись к медиа сохраняются как HTML"""
    data = await state.get_data()
    user_id = message.from_user.id
    raw = (message.text or message.caption or "").strip()
    use_caption = message.caption is not None
    content_html = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or [])
    )
    media_type, media_file_id, media_path = await save_media(message)
    media_group_id = getattr(message, "media_group_id", None)

    if media_group_id:
        if not media_type:
            return
        # Для альбомов несколько update-хендлеров приходят почти одновременно.
        # Берём самое свежее состояние, иначе можно потерять часть медиа.
        album_state = await state.get_data()
        album_items = list(album_state.get("_pending_album_items") or [])
        if len(album_items) >= 6:
            await message.answer("❌ В одном моменте можно отправить максимум 6 медиа. Отправь заново до 6 файлов.")
            await state.update_data(_pending_album_items=[], _pending_album_token=None, _pending_album_content=None)
            return
        album_items.append({"type": media_type, "file_id": media_file_id, "path": media_path})
        merged_content = album_state.get("_pending_album_content") or content_html or ""
        token = f"{media_group_id}:{message.message_id}"
        await state.update_data(
            _pending_album_items=album_items,
            _pending_album_content=merged_content,
            _pending_album_token=token,
        )
        await asyncio.sleep(1.0)
        latest = await state.get_data()
        if latest.get("_pending_album_token") != token:
            return
        items = list(latest.get("_pending_album_items") or [])
        content_for_save = latest.get("_pending_album_content") or ""
        if not items:
            return
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        first = items[0]
        memory = await _finalize_memory_creation(
            message=message,
            state=state,
            data=latest,
            user_id=user_id,
            content=content_for_save,
            media_type=first.get("type"),
            media_file_id=first.get("file_id"),
            media_path=first.get("path"),
            media_items=items,
        )
        if memory:
            try:
                partner_id = db.get_partner_id(user_id)
                if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                    user_name = db.get_display_name(user_id, fallback="Партнёр")
                    notify_text = (
                        f"✨ {user_name} добавил(а) новый момент\n\n"
                        + format_memory_text(memory, partner_id)
                    )
                    await message.bot.send_message(
                        chat_id=partner_id,
                        text=notify_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{memory.id}")]
                        ]),
                        parse_mode=ParseMode.HTML,
                        force_new_message=True,
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления о новом моменте: {e}")
        return

    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    content = content_html

    # Если это голосовое или кружок без текста — предлагаем добавить описание отдельно
    if media_type in ("voice", "video_note") and not content:
        await state.update_data(
            pending_media_type=media_type,
            pending_media_file_id=media_file_id,
            pending_media_path=media_path,
            pending_category=data["category"],
            pending_title=data["title"],
            pending_date=data["date"],
            pending_user_id=user_id,
        )
        await state.set_state(AddMemoryStates.waiting_for_voice_description)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, добавить", callback_data="voice_desc_yes"),
                InlineKeyboardButton(text="❌ Нет, оставить так", callback_data="voice_desc_no"),
            ]
        ])
        await message.answer(
            "📝 Хочешь добавить описание к этому моменту?\n\n",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        return

    memory = await _finalize_memory_creation(
        message=message,
        state=state,
        data=data,
        user_id=user_id,
        content=content,
        media_type=media_type,
        media_file_id=media_file_id,
        media_path=media_path,
        media_items=([{"type": media_type, "file_id": media_file_id, "path": media_path}] if media_type else None),
    )
    if memory:
        try:
            partner_id = db.get_partner_id(user_id)
            if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                user_name = db.get_display_name(user_id, fallback="Партнёр")
                notify_text = (
                    f"✨ {user_name} добавил(а) новый момент\n\n"
                    + format_memory_text(memory, partner_id)
                )
                await message.bot.send_message(
                    chat_id=partner_id,
                    text=notify_text,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{memory.id}")]
                    ]),
                    parse_mode=ParseMode.HTML,
                    force_new_message=True,
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомления о новом моменте: {e}")


@router.callback_query(F.data.startswith("memory_"))
async def show_memory_detail(callback: CallbackQuery, state: FSMContext):
    """Детальный просмотр воспоминания"""
    user_id = callback.from_user.id
    
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    memory_id = int(callback.data.split("_")[1])
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return

    # Проверяем приватность
    privacy = db.get_memory_privacy(memory_id)
    p_type = (privacy.get("privacy_type") or "").strip()
    remaining_views = None

    if p_type == "password":
        # Всегда запрашиваем пароль, в том числе у создателя момента
        question = privacy.get("privacy_question") or "Ответь на вопрос для доступа к этому моменту."
        await state.set_state(ViewMemoryStates.waiting_for_password)
        await state.update_data(memory_id=memory_id)
        await callback_edit_or_answer(callback, 
            "🔒 <b>Доступ по паролю</b>\n\n"
            f"Вопрос:\n<b>{question}</b>\n\n"
            "💬 Напиши ответ:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
            ]),
            parse_mode=ParseMode.HTML,
        )
        await callback.answer()
        return
    elif p_type == "limited_views":
        used, limit, allowed = db.register_memory_view(memory_id, user_id)
        if not allowed:
            await callback_edit_or_answer(callback, 
                "🚫 Лимит просмотров этого момента для тебя исчерпан",
                parse_mode=ParseMode.HTML,
            )
            await callback.answer()
            return
        if limit:
            remaining_views = max(limit - used, 0)
    
    await callback.bot.send_chat_action(chat_id=callback.message.chat.id, action=ChatAction.TYPING)
    text = format_memory_text(memory, user_id, remaining_views=remaining_views)
    keyboard = create_memory_detail_keyboard(memory_id, memory.category, user_id=user_id)
    if memory.media_type:
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=keyboard
        )
    else:
        await callback_edit_or_answer(
            callback,
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )

@router.callback_query(F.data.startswith("edit_memory_"))
async def edit_memory_options(callback: CallbackQuery):
    """Выбор что редактировать в воспоминании"""
    user_id = callback.from_user.id
    memory_id = int(callback.data.split("_")[2])
    
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    
    can_edit_privacy = (memory.user_id == user_id)
    await callback_edit_or_answer(callback, 
        f"✏️ <b>Редактирование:</b> {memory.title or ''}\n\n"
        "Что ты хочешь изменить?",
        reply_markup=create_edit_options_keyboard(memory_id, memory.category, can_edit_privacy=can_edit_privacy),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("edit_title_"))
async def edit_memory_title(callback: CallbackQuery, state: FSMContext):
    """Редактирование названия"""
    memory_id = int(callback.data.split("_")[2])
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    
    await state.update_data(memory_id=memory_id, category=memory.category)
    await state.set_state(EditMemoryStates.waiting_for_new_title)
    
    await callback_edit_or_answer(callback, 
        f"✏️ <b>Изменение названия</b>\n\n"
        f"Текущее название: {memory.title or ''}\n\n"
        "Напиши новое название",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("edit_date_"))
async def edit_memory_date(callback: CallbackQuery, state: FSMContext):
    """Редактирование даты"""
    memory_id = int(callback.data.split("_")[2])
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    
    await state.update_data(memory_id=memory_id, category=memory.category)
    await state.set_state(EditMemoryStates.waiting_for_new_date)
    
    await callback_edit_or_answer(callback, 
        f"✏️ <b>Изменение даты</b>\n\n"
        f"Текущая дата: <i>{memory.date or ''}</i>\n\n"
        "Напиши новую дату",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("edit_content_"))
async def edit_memory_content(callback: CallbackQuery, state: FSMContext):
    """Редактирование описания"""
    memory_id = int(callback.data.split("_")[2])
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    
    await state.update_data(memory_id=memory_id, category=memory.category)
    await state.set_state(EditMemoryStates.waiting_for_new_content)
    
    content_preview = sanitize_html_for_telegram(truncate_text(memory.content or "", 100))
    params_help = get_params_help_text()
    
    await callback_edit_or_answer(callback, 
        f"✏️ <b>Изменение описания</b>\n\n"
        f"Текущее описание:\n{content_preview}\n\n"
        "Отправь новое описание. Ты можешь:\n\n"
        "• Написать текст с форматированием: <b>жирный</b>, <i>курсив</i>, <u>подчеркнутый</u>, <s>зачеркнутый</s>, <code>моно</code> или в виде ссылки\n"
        "• Добавить фото, видео, кружоки и тд)\n"
        f"{params_help}\n\n"
        "Ну например: <i>Привет любимый(ая), мы с тобой уже {days_together} дней!</i>",
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("edit_privacy_"))
async def edit_memory_privacy(callback: CallbackQuery):
    """Меню выбора типа приватности воспоминания."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    text = (
        f"🔒 <b>Приватность момента</b>\n\n"
        f"{memory.title or ''}\n\n"
        "Выбери тип приватности:\n"
        "📊 • <b>Ограниченное количество просмотров</b> — каждый пользователь (кроме создателя момента) "
        "сможет открыть этот момент столько раз сколько ты добавишь\n"
        "🔑 • <b>Доступ по паролю</b> — перед просмотром бот задаст вопрос на который нужно будет ответить\n"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📊 Ограниченное количество просмотров",
                callback_data=f"privacy_set_limit_{memory_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🔑 Доступ по паролю",
                callback_data=f"privacy_set_password_{memory_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="🔙 Назад",
                callback_data=f"edit_memory_{memory_id}",
            )
        ],
    ])
    await callback_edit_or_answer(callback, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data.startswith("privacy_set_limit_"))
async def privacy_set_limit(callback: CallbackQuery, state: FSMContext):
    """Выбор лимита просмотров для приватности."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await state.update_data(memory_id=memory_id)
    await state.set_state(EditMemoryStates.waiting_for_new_privacy_limit)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1", callback_data=f"privacy_limit_choose_1_{memory_id}"),
            InlineKeyboardButton(text="2", callback_data=f"privacy_limit_choose_2_{memory_id}"),
        ],
        [
            InlineKeyboardButton(text="3", callback_data=f"privacy_limit_choose_3_{memory_id}"),
            InlineKeyboardButton(text="5", callback_data=f"privacy_limit_choose_5_{memory_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_privacy_{memory_id}"),
        ],
    ])
    await callback_edit_or_answer(callback, 
        "📊 <b>Ограниченное количество просмотров</b>\n\n"
        "Выбери готовый вариант или отправь своё число (1–100):",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^privacy_limit_choose_\d+_\d+$"))
async def privacy_limit_choose(callback: CallbackQuery, state: FSMContext):
    """Установка лимита через инлайн-кнопку."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        parts = callback.data.split("_")
        # privacy_limit_choose_{limit}_{memory_id}
        limit_str = parts[-2]
        mem_id_str = parts[-1]
        limit = int(limit_str)
        memory_id = int(mem_id_str)
    except Exception:
        await callback.answer("Ошибка")
        return
    if limit <= 0 or limit > 100:
        await callback.answer("Некорректный лимит")
        return
    db.set_memory_privacy_limited(memory_id, limit)
    await state.clear()
    memory = db.get_memory(memory_id)
    if memory:
        await callback_edit_or_answer(callback, 
            f"✅ Приватность установлена: не более {limit} просмотров для каждого пользователя (кроме создателя момента)",
            parse_mode=ParseMode.HTML,
        )
        text = format_memory_text(memory, user_id)
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=user_id),
        )
    await callback.answer()


@router.message(EditMemoryStates.waiting_for_new_privacy_limit)
async def process_new_privacy_limit(message: Message, state: FSMContext):
    """Обработка пользовательского числа для лимита просмотров."""
    data = await state.get_data()
    memory_id = data.get("memory_id")
    if not memory_id:
        await state.clear()
        return
    text_raw = (message.text or "").strip()
    if not text_raw.isdigit():
        await message.answer("❌ Нужна цифра (1–100). Попробуй ещё раз.")
        return
    limit = int(text_raw)
    if limit <= 0 or limit > 100:
        await message.answer("❌ Лимит должен быть от 1 до 100. Попробуй ещё раз.")
        return
    if not db.set_memory_privacy_limited(memory_id, limit):
        await message.answer("❌ Не удалось сохранить приватность. Попробуй позже.")
        await state.clear()
        return
    await state.clear()
    memory = db.get_memory(memory_id)
    if not memory:
        await message.answer(MSG_MEMORY_NOT_FOUND)
        return
    await message.answer(
        f"✅ Приватность установлена: не более {limit} просмотров для каждого пользователя (кроме создателя момента)",
        parse_mode=ParseMode.HTML,
    )
    text = format_memory_text(memory, message.from_user.id)
    await send_message_with_media(
        chat_id=message.chat.id,
        bot=message.bot,
        memory=memory,
        text=text,
        keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=message.from_user.id),
    )


@router.callback_query(F.data.startswith("privacy_set_password_"))
async def privacy_set_password(callback: CallbackQuery, state: FSMContext):
    """Начало настройки доступа по паролю: задаём вопрос."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await state.update_data(memory_id=memory_id)
    await state.set_state(EditMemoryStates.waiting_for_privacy_question)
    await callback_edit_or_answer(callback, 
        "🔑 <b>Доступ по паролю</b>\n\n"
        "Сначала напиши вопрос, на который нужно будет ответить перед просмотром момента\n"
        "Например: <i>Когда у нас годовщина?</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data=f"edit_privacy_back_{memory_id}")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_privacy_back_"))
async def edit_privacy_back_to_memory(callback: CallbackQuery, state: FSMContext):
    """Кнопка «Назад» при настройке пароля: возврат к шагу изменения момента."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    await state.clear()
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    can_edit_privacy = memory.user_id == user_id
    await callback.message.edit_text(
        "✏️ <b>Изменить момент</b>\n\nВыбери, что изменить:",
        reply_markup=create_edit_options_keyboard(memory_id, memory.category, can_edit_privacy=can_edit_privacy),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(EditMemoryStates.waiting_for_privacy_question)
async def process_privacy_question(message: Message, state: FSMContext):
    """Сохраняем вопрос и просим ответ (пароль)."""
    data = await state.get_data()
    memory_id = data.get("memory_id")
    if not memory_id:
        await state.clear()
        return
    question = (message.text or "").strip()
    if not question:
        await message.answer("❌ Вопрос не может быть пустым. Напиши вопрос")
        return
    await state.update_data(memory_id=memory_id, privacy_question=question)
    await state.set_state(EditMemoryStates.waiting_for_privacy_answer)
    await message.answer(
        "🔓 Теперь напиши <b>ответ</b> на этот вопрос — он и будет паролем",
        parse_mode=ParseMode.HTML,
    )


@router.message(EditMemoryStates.waiting_for_privacy_answer)
async def process_privacy_answer(message: Message, state: FSMContext):
    """Сохраняем пароль и включаем доступ по паролю."""
    data = await state.get_data()
    memory_id = data.get("memory_id")
    question = data.get("privacy_question")
    if not memory_id or not question:
        await state.clear()
        return
    answer = (message.text or "").strip()
    if not answer:
        await message.answer("❌ Ответ не может быть пустым. Напиши ответ.")
        return
    ok = db.set_memory_privacy_password(memory_id, question, answer)
    await state.clear()
    if not ok:
        await message.answer("❌ Не удалось сохранить приватность. Попробуй позже.")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await message.answer(MSG_MEMORY_NOT_FOUND)
        return
    await message.answer(
        "✅ Установлен доступ по паролю\n"
        "🔐 Это работает так что перед тем как посмотреть момент нужно будет ввести пароль",
        parse_mode=ParseMode.HTML,
    )
    text = format_memory_text(memory, message.from_user.id)
    await send_message_with_media(
        chat_id=message.chat.id,
        bot=message.bot,
        memory=memory,
        text=text,
        keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=message.from_user.id),
    )


@router.callback_query(F.data.regexp(r"^privacy_remove_limited_\d+$"))
async def privacy_remove_limited(callback: CallbackQuery):
    """Подтверждение удаления ограниченного доступа."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, удалить ограниченный доступ",
                callback_data=f"privacy_remove_limited_confirm_{memory_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"edit_memory_{memory_id}",
            )
        ],
    ])
    await callback_edit_or_answer(callback, 
        "🔓 <b>Удалить ограниченный доступ?</b>\n\n"
        "Если удалить лимита на просмотр больше не будлет",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^privacy_remove_password_\d+$"))
async def privacy_remove_password(callback: CallbackQuery):
    """Подтверждение удаления доступа по паролю."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        memory_id = int(callback.data.split("_")[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    memory = db.get_memory(memory_id)
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, удалить доступ по паролю",
                callback_data=f"privacy_remove_password_confirm_{memory_id}",
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=f"edit_memory_{memory_id}",
            )
        ],
    ])
    await callback_edit_or_answer(callback, 
        "🔓 <b>Удалить доступ по паролю?</b>\n\n"
        "После удаления доступ к этому воспоминанию будет без пароля",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^privacy_remove_(limited|password)_confirm_\d+$"))
async def privacy_remove_confirm(callback: CallbackQuery):
    """Фактическое удаление любой приватности."""
    user_id = callback.from_user.id
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    parts = callback.data.split("_")
    try:
        memory_id = int(parts[-1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка")
        return
    ok = db.clear_memory_privacy(memory_id)
    if not ok:
        await callback.answer("Ошибка при удалении приватности")
        return
    memory = db.get_memory(memory_id)
    if memory:
        await callback_edit_or_answer(callback, 
            "✅ Приватность удалена. Теперь момент доступен без ограничений",
            parse_mode=ParseMode.HTML,
        )
        text = format_memory_text(memory, user_id)
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=user_id),
        )
    await callback.answer()

@router.message(EditMemoryStates.waiting_for_new_title)
async def process_new_title(message: Message, state: FSMContext):
    """Обработка нового названия (форматирование сохраняется как HTML)"""
    data = await state.get_data()
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_title(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    new_title = text_and_entities_to_html(message.text or "", message.entities or [])
    success = db.update_memory(data['memory_id'], title=new_title)
    
    if success:
        memory = db.get_memory(data['memory_id'])
        if memory:
            text = format_memory_text(memory, message.from_user.id)
            

            await message.answer(
                f"✅ Название изменено на: {new_title}",
                parse_mode=ParseMode.HTML
            )

            await send_message_with_media(
                chat_id=message.chat.id,
                bot=message.bot,
                memory=memory,
                text=text,
                keyboard=create_memory_detail_keyboard(data['memory_id'], memory.category, user_id=message.from_user.id)
            )
            
            
            try:
                partner_id = db.get_partner_id(message.from_user.id)
                if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                    user_name = db.get_display_name(message.from_user.id, fallback="Партнёр")
                    notify_text = (
                        f"✏️ {user_name} изменил(а) название момента\n\n"
                        + text
                    )
                    await message.bot.send_message(
                        chat_id=partner_id,
                        text=notify_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{data['memory_id']}")]
                        ]),
                        parse_mode=ParseMode.HTML,
                        force_new_message=True,
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления об изменении названия момента: {e}")
    else:
        await message.answer("❌ Ошибка при обновлении названия")
    
    await state.clear()

@router.message(EditMemoryStates.waiting_for_new_date)
async def process_new_date(message: Message, state: FSMContext):
    """Обработка новой даты (форматирование сохраняется как HTML)"""
    data = await state.get_data()
    raw = (message.text or "").strip()
    is_valid, error_msg = validate_date(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    new_date = text_and_entities_to_html(message.text or "", message.entities or [])
    success = db.update_memory(data['memory_id'], date=new_date)
    
    if success:
        memory = db.get_memory(data['memory_id'])
        if memory:
            text = format_memory_text(memory, message.from_user.id)
            

            await message.answer(
                f"✅ Дата изменена на: <i>{new_date}</i>",
                parse_mode=ParseMode.HTML
            )

            await send_message_with_media(
                chat_id=message.chat.id,
                bot=message.bot,
                memory=memory,
                text=text,
                keyboard=create_memory_detail_keyboard(data['memory_id'], memory.category, user_id=message.from_user.id)
            )
            
            
            try:
                partner_id = db.get_partner_id(message.from_user.id)
                if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                    user_name = db.get_display_name(message.from_user.id, fallback="Партнёр")
                    notify_text = (
                        f"📅 {user_name} изменил(а) дату момента\n\n"
                        + text
                    )
                    await message.bot.send_message(
                        chat_id=partner_id,
                        text=notify_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{data['memory_id']}")]
                        ]),
                        parse_mode=ParseMode.HTML,
                        force_new_message=True,
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления об изменении даты момента: {e}")
    else:
        await message.answer("❌ Ошибка при обновлении даты")
    
    await state.clear()

@router.message(EditMemoryStates.waiting_for_new_content)
async def process_new_content(message: Message, state: FSMContext):
    """Обработка нового описания (текст/подпись сохраняются как HTML)"""
    data = await state.get_data()
    raw = (message.text or message.caption or "").strip()
    use_caption = message.caption is not None
    content_html = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or [])
    )
    media_type, media_file_id, media_path = await save_media(message)
    media_group_id = getattr(message, "media_group_id", None)

    if media_group_id:
        if not media_type:
            return
        album_state = await state.get_data()
        album_items = list(album_state.get("_pending_album_items") or [])
        if len(album_items) >= 6:
            await message.answer("❌ В одном моменте можно отправить максимум 6 медиа. Отправь заново до 6 файлов.")
            await state.update_data(_pending_album_items=[], _pending_album_token=None, _pending_album_content=None)
            return
        album_items.append({"type": media_type, "file_id": media_file_id, "path": media_path})
        merged_content = album_state.get("_pending_album_content") or content_html or ""
        token = f"{media_group_id}:{message.message_id}"
        await state.update_data(
            _pending_album_items=album_items,
            _pending_album_content=merged_content,
            _pending_album_token=token,
        )
        await asyncio.sleep(1.0)
        latest = await state.get_data()
        if latest.get("_pending_album_token") != token:
            return
        items = list(latest.get("_pending_album_items") or [])
        content_for_save = latest.get("_pending_album_content") or ""
        if not items:
            return
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        first = items[0]
        update_data = {
            'content': content_for_save,
            'media_type': first.get("type"),
            'media_file_id': first.get("file_id"),
            'media_path': first.get("path"),
            'media_items': items,
        }
        success = db.update_memory(data['memory_id'], **update_data)
        await state.update_data(_pending_album_items=[], _pending_album_token=None, _pending_album_content=None)
        if success:
            memory = db.get_memory(data['memory_id'])
            if memory:
                text = format_memory_text(memory, message.from_user.id)
                await message.answer("✅ Описание обновлено", parse_mode=ParseMode.HTML)
                await send_message_with_media(
                    chat_id=message.chat.id,
                    bot=message.bot,
                    memory=memory,
                    text=text,
                    keyboard=create_memory_detail_keyboard(data['memory_id'], memory.category, user_id=message.from_user.id)
                )
                try:
                    partner_id = db.get_partner_id(message.from_user.id)
                    if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                        user_name = db.get_display_name(message.from_user.id, fallback="Партнёр")
                        notify_text = (
                            f"💬 {user_name} изменил(а) описание момента\n\n"
                            + text
                        )
                        await message.bot.send_message(
                            chat_id=partner_id,
                            text=notify_text,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{data['memory_id']}")]
                            ]),
                            parse_mode=ParseMode.HTML,
                            force_new_message=True,
                        )
                except Exception as e:
                    logger.error(f"Ошибка при отправке уведомления об изменении описания момента: {e}")
        else:
            await message.answer("❌ Ошибка при обновлении описания")
        await state.clear()
        return

    if await _is_duplicate_media_group(state, message, "_mg_seen_edit_memory_content"):
        return
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй еще раз:")
        return
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    content = content_html

    update_data = {
        'content': content,
        'media_type': media_type,
        'media_file_id': media_file_id,
        'media_path': media_path,
        'media_items': [{"type": media_type, "file_id": media_file_id, "path": media_path}] if media_type else None,
    }

    success = db.update_memory(data['memory_id'], **update_data)

    if success:
        memory = db.get_memory(data['memory_id'])
        if memory:
            text = format_memory_text(memory, message.from_user.id)

            await message.answer(
                "✅ Описание обновлено",
                parse_mode=ParseMode.HTML
            )

            await send_message_with_media(
                chat_id=message.chat.id,
                bot=message.bot,
                memory=memory,
                text=text,
                keyboard=create_memory_detail_keyboard(data['memory_id'], memory.category, user_id=message.from_user.id)
            )
            try:
                partner_id = db.get_partner_id(message.from_user.id)
                if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, memory.category):
                    user_name = db.get_display_name(message.from_user.id, fallback="Партнёр")
                    notify_text = (
                        f"💬 {user_name} изменил(а) описание момента\n\n"
                        + text
                    )
                    await message.bot.send_message(
                        chat_id=partner_id,
                        text=notify_text,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{data['memory_id']}")]
                        ]),
                        parse_mode=ParseMode.HTML,
                        force_new_message=True,
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления об изменении описания момента: {e}")
    else:
        await message.answer("❌ Ошибка при обновлении описания")

    await state.clear()

@router.callback_query(F.data.startswith("delete_confirm_"))
async def confirm_delete(callback: CallbackQuery):
    """Подтверждение удаления"""
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    memory_id = int(parts[-1])
    from_search = "search" in callback.data
    
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    memory = db.get_memory(memory_id)
    
    if not memory:
        await callback.answer(MSG_MEMORY_NOT_FOUND)
        return
    
    title_subst = substitute_params(memory.title or "", user_id)
    date_subst = substitute_params(memory.date or "", user_id)
    await callback_edit_or_answer(callback, 
        f"❓ <b>Ты уверен(а) что хочешь удалить это воспоминание?</b>\n\n"
        f"{title_subst}\n"
        f"<i>Момент был {date_subst}</i>\n\n"
        "После этого оно будет удалено без возможности восстановления",
        reply_markup=create_confirmation_keyboard(memory_id, memory.category, from_search=from_search),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.regexp(r"^delete_yes(_search)?_\d+$"))
async def delete_memory(callback: CallbackQuery, state: FSMContext):
    """Удаление воспоминания"""
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    memory_id = int(parts[-1])
    from_search = "search" in callback.data
    
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    memory = db.get_memory(memory_id)
    
    if memory:
        success = db.delete_memory(memory_id)
        if success:
            await callback.answer("Воспоминание удалено ✅")
            if from_search:
                data = await state.get_data()
                query = data.get("search_query", "")
                page = data.get("search_page", 1)
                memories = db.search_memories_fuzzy(query, limit=100) if query else []
                await safe_delete_callback_message(callback)
                if memories:
                    await callback_edit_or_answer(callback, 
                        f"🔍 <b>Поиск: «{query}»</b>\n\nНайдено: {len(memories)}",
                        reply_markup=create_search_results_keyboard(memories, page=page, user_id=user_id),
                        parse_mode=ParseMode.HTML
                    )
                else:
                    welcome_text = format_welcome_message(user_id)
                    await state.clear()
                    await callback_edit_or_answer(callback, 
                        welcome_text,
                        reply_markup=create_main_keyboard(user_id),
                        parse_mode=ParseMode.HTML
                    )
            else:
                _couple = db.get_couple_by_user(user_id)
                _couple_id = _couple['id'] if _couple else None
                _all_cats = get_all_categories(_couple_id)
                category = _all_cats.get(memory.category)
                if category:
                    memories = db.get_memories_by_category(memory.category, couple_id=_couple_id)
                    count = len(memories)
                    message_text = f"<b>{category['title']}</b>\n"
                    message_text += f"<i>{category['description']}</i>\n\n"
                    if count > 0:
                        message_text += f"Всего моментов: {count}\n\nВыбери воспоминание:"
                    else:
                        message_text += "📭 <i>В этой категории пока нет воспоминаний...</i>\n\n"
                    await callback_edit_or_answer(callback, 
                        message_text,
                        reply_markup=create_category_keyboard_paged(memory.category, memories, page=1, user_id=callback.from_user.id),
                        parse_mode=ParseMode.HTML
                    )
        else:
            await callback.answer("❌ Ошибка при удалении")
    else:
        await callback.answer(MSG_MEMORY_NOT_FOUND)

@router.callback_query(F.data.startswith("delete_no_"))
async def cancel_delete(callback: CallbackQuery):
    """Отмена удаления"""
    user_id = callback.from_user.id
    # callback: delete_no_<id> или delete_no_search_<id> (из результатов поиска)
    memory_id = int(callback.data.split("_")[-1])
    
    if not db.is_admin(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    memory = db.get_memory(memory_id)
    
    if memory:
        text = format_memory_text(memory, user_id)
        
        await send_message_with_media(
            chat_id=callback.message.chat.id,
            bot=callback.bot,
            memory=memory,
            text=text,
            keyboard=create_memory_detail_keyboard(memory_id, memory.category, user_id=user_id)
        )
    else:
        await callback.answer(MSG_MEMORY_NOT_FOUND)

@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    """Открытие админ-панели"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    status_text = _get_rollback_status_block()
    await callback_edit_or_answer(callback, 
        "🔧 <b>Админ-панель</b>\n\n"
        f"{status_text}\n"
        "Управление ботом и статистика:",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "admin_diagnostics")
async def admin_diagnostics(callback: CallbackQuery):
    """Панель диагностики системы"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    await callback.answer("Собираю диагностику...")
    
    from app_version import version as running_ver, get_git_commit, get_git_status_info
    running_commit = get_git_commit() or "—"
    status_info = get_git_status_info()
    
    repo_branch = status_info.get("branch", "—")
    repo_commit = status_info.get("commit", "—")
    is_clean = status_info.get("is_clean", True)
    
    is_rollback_active = db.get_setting("rollback_active") == "1"
    target_commit = db.get_setting("rollback_target_commit") or "—"
    
    rb_state = "Активен (Легитимный)" if is_rollback_active else "Неактивен"
    
    lines = [
        "🩺 <b>Системная Диагностика</b>\n",
        f"<b>Запущенная версия:</b> <code>{html.escape(running_ver)}</code>",
        f"<b>Запущенный коммит:</b> <code>{html.escape(running_commit)}</code>\n",
        f"<b>Ветка репозитория:</b> <code>{html.escape(repo_branch)}</code>",
        f"<b>Коммит репозитория:</b> <code>{html.escape(repo_commit[:8])}</code>",
        f"<b>Репозиторий чист:</b> {'✅ Да' if is_clean else '❌ Нет'}\n",
        f"<b>Состояние отката:</b> {rb_state}"
    ]
    if is_rollback_active:
        lines.append(f"<b>Целевой коммит отката:</b> <code>{html.escape(target_commit[:8])}</code>")
        
    # Read last rollback history from DB settings
    last_rb_date = db.get_setting("last_rollback_date")
    if last_rb_date:
        lines.append(f"\n<b>История откатов (Последний):</b>")
        lines.append(f"Дата: {html.escape(last_rb_date)}")
        lines.append(f"Исходная версия: <code>{html.escape(db.get_setting('last_rollback_source_ver') or '—')}</code>")
        lines.append(f"Целевая версия: <code>{html.escape(db.get_setting('last_rollback_dest_ver') or '—')}</code>")
        lines.append(f"Целевой коммит: <code>{html.escape((db.get_setting('last_rollback_target') or '—')[:8])}</code>")
        
    import sqlite3
    db_path = "—"
    try:
        db_path = db.db_path or "—"
    except Exception:
        pass
        
    lines.append(f"\n<b>Путь к базе данных:</b> <code>{html.escape(str(db_path))}</code>")
    
    await callback_edit_or_answer(callback, 
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]]),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("admin_version_history:"))
async def admin_version_history(callback: CallbackQuery):
    """Отображение истории версий с пагинацией и управлением релизами"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    page_str = callback.data.replace("admin_version_history:", "")
    try:
        page = int(page_str)
    except ValueError:
        page = 0
        
    versions = db.get_version_history()
    total_versions = len(versions)
    page_size = 10
    total_pages = max(1, (total_versions + page_size - 1) // page_size)
    
    if page < 0:
        page = 0
    elif page >= total_pages:
        page = total_pages - 1
        
    start_idx = page * page_size
    end_idx = start_idx + page_size
    page_versions = versions[start_idx:end_idx]
    
    status_text = _get_rollback_status_block()
    lines = [
        "📦 <b>История версий</b>\n",
        status_text,
        ""
    ]
    
    if not page_versions:
        lines.append("\nИстория версий пуста.")
    else:
        from app_version import get_git_commit
        running_commit = get_git_commit() or ""
        rb_active = db.get_setting("rollback_active") == "1"
        target_commit = db.get_setting("rollback_target_commit") or ""
        
        def _parse_version(v_str):
            try: return tuple(map(int, v_str.split(".")))
            except Exception: return (0, 0, 0)

        for v in page_versions:
            ver = v.get("version") or "—"
            desc = v.get("description") or "—"
            commit = v.get("git_commit")
            commit_str = commit[:7] if commit else "—"
            try:
                status = v.get("status") or "stable"
            except Exception:
                status = "stable"
            
            date_str = "—"
            created_at = v.get("created_at")
            if created_at:
                try:
                    dt_part = created_at.split('.')[0]
                    dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
                    date_str = dt.strftime("%d.%m.%Y %H:%M")
                except Exception:
                    try:
                        dt = datetime.fromisoformat(created_at)
                        date_str = dt.strftime("%d.%m.%Y %H:%M")
                    except Exception:
                        date_str = created_at
                
            ver_esc = html.escape(str(ver))
            desc_esc = html.escape(str(desc))
            commit_esc = html.escape(str(commit_str))
            date_esc = html.escape(str(date_str))
            
            is_supported = _parse_version(str(ver)) >= (1, 0, 14)
            badges = []
            if commit and running_commit and commit[:8] == running_commit[:8]:
                badges.append("🔄 Текущая")
            if rb_active and commit and target_commit and commit[:8] == target_commit[:8]:
                badges.append("📍 Цель отката")
                
            if status == "broken":
                badges.append("❌ Сломана")
            elif not is_supported:
                badges.append("⚠️ Устаревшая")
            else:
                badges.append("✅ Стабильная")
                
            badges_str = " | ".join(badges)
            
            lines.append(
                f"\n<b>Версия:</b> <code>{ver_esc}</code> [{badges_str}]\n"
                f"<b>Описание:</b> {desc_esc}\n"
                f"<b>Коммит:</b> <code>{commit_esc}</code>\n"
                f"<b>Дата:</b> {date_esc}"
            )
            
    lines.append(f"\nСтраница {page + 1} из {total_pages}")
    
    text = "\n".join(lines)
    
    keyboard = []
    
    # 1. Кнопка создания релиза вверху списка
    keyboard.append([
        InlineKeyboardButton(text="🚀 Создать релиз", callback_data="admin_create_release")
    ])
    
    # 2. Под списком отображаем по кнопке "Детали" для каждой версии на текущей странице
    for v in page_versions:
        ver = v.get("version")
        if ver:
            keyboard.append([
                InlineKeyboardButton(text=f"🔍 Версия {ver}", callback_data=f"admin_version_detail:{ver}:{page}")
            ])
            
    # 3. Кнопки пагинации
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_version_history:{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="➡️ Вперёд", callback_data=f"admin_version_history:{page + 1}"))
        
    if row:
        keyboard.append(row)
        
    keyboard.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="admin_panel")])
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback_edit_or_answer(callback, 
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "admin_git_status")
async def admin_git_status(callback: CallbackQuery):
    """Отображение статуса Git репозитория"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    await callback.answer("Загружаю статус Git…")
    
    from app_version import get_git_status_info, get_version_metadata, get_git_commit
    status_info = get_git_status_info()
    current_version, _ = get_version_metadata()
    current_git_commit = get_git_commit() or "—"
    
    branch = status_info.get("branch") or "—"
    commit = status_info.get("commit") or "—"
    if commit and len(commit) > 7:
        commit = commit[:7]
    total_commits = status_info.get("total_commits")
    if total_commits is None:
        total_commits = "—"
        
    is_clean = status_info.get("is_clean", True)
    modified_files = status_info.get("modified_files", [])
    
    if current_git_commit and len(current_git_commit) > 7:
        current_git_commit = current_git_commit[:7]

    lines = [
        "🧩 <b>Статус Git</b>\n",
        f"Ветка: <code>{html.escape(str(branch))}</code>",
        f"Коммит: <code>{html.escape(str(commit))}</code>",
        f"Всего коммитов: <code>{total_commits}</code>\n",
        "Состояние репозитория:"
    ]
    
    if is_clean:
        lines.append("✅ Репозиторий чист\n")
    else:
        lines.append("⚠️ Есть незакоммиченные изменения\n")
        lines.append(f"Изменено файлов: {len(modified_files)}")
        for f in modified_files[:10]:
            lines.append(f"• <code>{html.escape(f)}</code>")
        if len(modified_files) > 10:
            remaining = len(modified_files) - 10
            lines.append(f"и ещё {remaining} файлов")
        lines.append("")
        
    lines.append(f"Текущая версия: <code>{html.escape(str(current_version))}</code>")
    lines.append(f"Текущий git-коммит: <code>{html.escape(str(current_git_commit))}</code>")
    
    text = "\n".join(lines)
    
    keyboard = [
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
    ]
    
    await callback_edit_or_answer(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


def increment_patch_version(current_ver: str) -> str:
    """Automatically increments patch version (e.g. 1.0.0 -> 1.0.1, 1.2.9 -> 1.2.10)"""
    parts = current_ver.strip().split(".")
    if len(parts) == 3:
        try:
            major, minor, patch = parts[0], parts[1], parts[2]
            new_patch = int(patch) + 1
            return f"{major}.{minor}.{new_patch}"
        except ValueError:
            pass
    return current_ver + ".1"


def update_app_version_file(new_version: str, new_description: str) -> None:
    """Updates app_version.py with new version and description.

    Uses repr() to serialise both values so that any character
    (newlines, quotes, apostrophes, emoji, arbitrary Unicode) is
    safely escaped into a valid Python string literal.

    Raises SyntaxError if the resulting file would not parse as valid
    Python, so callers can abort and surface the error before touching
    git.
    """
    import re
    from app_version import _get_repo_root
    repo_root = _get_repo_root()
    filepath = os.path.join(repo_root, "app_version.py")
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # repr() produces a safe Python literal: 'some text' or "some text",
    # with all special characters escaped.  Strip the outer quotes so we
    # can wrap in our chosen delimiter below.
    version_repr     = repr(str(new_version))
    description_repr = repr(str(new_description))

    # Replace the assignment lines using the repr()-produced literals
    # directly — no additional quoting needed. Use lambda to avoid re.sub
    # escape processing.
    content = re.sub(
        r'\bversion\s*=\s*(?:""".*?"""|\'\'\'.*?\'\'\'|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')',
        lambda _: f'version = {version_repr}',
        content,
        flags=re.DOTALL,
    )
    content = re.sub(
        r'\bdescription\s*=\s*(?:""".*?"""|\'\'\'.*?\'\'\'|"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')',
        lambda _: f'description = {description_repr}',
        content,
        flags=re.DOTALL,
    )

    # Validate before writing — fail fast with a clear error rather than
    # baking a broken file into the image.
    try:
        compile(content, filepath, "exec")
    except SyntaxError as exc:
        raise SyntaxError(
            f"Generated app_version.py would be invalid Python: {exc}"
        ) from exc

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)



@router.callback_query(F.data == "admin_create_release")
async def admin_create_release(callback: CallbackQuery, state: FSMContext):
    """Начало создания релиза"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    await callback.answer("Проверяю статус репозитория…")
    
    from app_version import get_git_status_info
    status_info = get_git_status_info()
    
    is_clean = status_info.get("is_clean", True)
    history = db.get_version_history()
    last_release_commit = history[0].get("git_commit") if history else None
    current_commit = status_info.get("commit")
    has_new_commits = False
    if current_commit and last_release_commit:
        current_commit_short = current_commit[:7]
        last_release_short = last_release_commit[:7]
        has_new_commits = current_commit_short != last_release_short

    # Safety: Block release creation only when repository is clean AND HEAD commit equals last released commit
    if is_clean and not has_new_commits:
        await callback_edit_or_answer(
            callback,
            "ℹ️ Нет изменений для создания релиза.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )
        return
        
    rb_active = db.get_setting("rollback_active") == "1"
    warn_msg = ""
    if rb_active:
        warn_msg += "⚠️ <b>Внимание:</b> Активен откат. Релиз будет создан поверх текущей активной версии (база отката) + ваши изменения. Номер версии увеличится относительно максимальной существующей версии. Откат будет завершён.\n\n"
        
    dirty_files = status_info.get("modified_files", [])
    if dirty_files:
        warn_msg += "⚠️ <b>Измененные файлы (войдут в релиз):</b>\n" + "\n".join([f"• <code>{html.escape(f)}</code>" for f in dirty_files[:5]])
        if len(dirty_files) > 5:
            warn_msg += f"\n<i>...и еще {len(dirty_files)-5} файлов</i>"
        warn_msg += "\n\n"
        
    # Ask for description
    await callback.message.answer(
        warn_msg + "Введите описание новой версии:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_release")]
        ]),
        parse_mode=ParseMode.HTML
    )
    await state.set_state(AdminStates.waiting_for_release_description)
    await _delete_callback_message_silent(callback)


@router.callback_query(F.data == "admin_cancel_release")
async def admin_cancel_release(callback: CallbackQuery, state: FSMContext):
    """Отмена создания релиза"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    await state.clear()
    await callback.answer("Создание релиза отменено.")
    
    await callback_edit_or_answer(callback, 
        "🔧 <b>Админ-панель</b>\n\n"
        "Управление ботом и статистика:",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )


@router.message(AdminStates.waiting_for_release_description)
async def admin_release_description_received(message: Message, state: FSMContext):
    """Получение описания релиза и показ подтверждения"""
    user_id = message.from_user.id
    if not db.is_creator(user_id):
        return
        
    description = message.text.strip()
    if not description:
        await message.answer("Описание не может быть пустым. Введите описание новой версии:")
        return
        
    await state.update_data(release_description=description)
    
    # Version numbering: always based on version_history (authoritative), never on app_version.py file.
    from app_version import get_version_metadata as _get_ver_meta_fallback
    _hist_preview = db.get_version_history()
    def _ver_tuple_p(v: str):
        try:
            return tuple(int(x) for x in v.strip().split("."))
        except Exception:
            return (0, 0, 0)
    if _hist_preview:
        current_ver = max((_e.get("version", "") for _e in _hist_preview), key=_ver_tuple_p)
    else:
        current_ver, _ = _get_ver_meta_fallback()
    new_version = increment_patch_version(current_ver)
    
    text = (
        "🚀 <b>Создание релиза</b>\n\n"
        "<b>Текущая версия:</b>\n"
        f"<code>{html.escape(current_ver)}</code>\n\n"
        "<b>Будет создана:</b>\n"
        f"<code>{html.escape(new_version)}</code>\n\n"
        "<b>Описание:</b>\n"
        f"{html.escape(description)}\n\n"
        "<b>Будет выполнено:</b>\n"
        "• git add .\n"
        "• git commit\n"
        "• git push\n"
        "• обновление версии\n"
        "• запись в version_history\n"
        "• перезапуск проекта\n\n"
        "Продолжить?"
    )
    
    keyboard = [
        [
            InlineKeyboardButton(text="✅ Создать релиз", callback_data="admin_confirm_release"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_release")
        ]
    ]
    
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "admin_confirm_release")
async def admin_confirm_release(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и запуск процесса создания релиза"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    data = await state.get_data()
    description = data.get("release_description")
    await state.clear()
    
    if not description:
        await callback.answer("Описание релиза не найдено. Начните сначала.", show_alert=True)
        return
        
    is_rollback_release = db.get_setting("rollback_active") == "1"
        
    await callback_edit_or_answer(callback, "🚀 <b>Запуск создания релиза...</b>\n\nВыполняю проверку репозитория...", parse_mode=ParseMode.HTML)
    
    try:
        from app_version import get_git_status_info, _get_repo_root
        status_info = get_git_status_info()
        repo_root = _get_repo_root()
        
        # 1. Verify Git repository exists
        if not os.path.isdir(os.path.join(repo_root, ".git")):
            raise Exception("Директория .git не найдена. Это не Git-репозиторий.")
            
        # Auto-recover from detached HEAD if rollback is not active
        branch = status_info.get("branch")
        if branch == "HEAD" and db.get_setting("rollback_active") != "1":
            await callback.message.answer("⚠️ Обнаружен detached HEAD при неактивном откате. Автоматически переключаюсь на ветку main...")
            
            # Stash changes to avoid checkout abort
            status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True)
            has_changes = bool(status_res.stdout.strip())
            if has_changes:
                subprocess.run(["git", "stash"], cwd=repo_root, capture_output=True)
                
            checkout_main = subprocess.run(["git", "checkout", "main"], cwd=repo_root, capture_output=True, text=True)
            
            if has_changes:
                subprocess.run(["git", "stash", "pop"], cwd=repo_root, capture_output=True)
            if checkout_main.returncode == 0:
                status_info = get_git_status_info()
                branch = status_info.get("branch")
                await callback.message.answer("✅ Успешно переключено на ветку main.")
            
        # 2. Verify branch is known and not detached
        # During rollback the workspace is in detached HEAD — that is expected and allowed.
        if not branch or branch in ("—", "HEAD"):
            if not is_rollback_release:
                raise Exception("Ветка репозитория не определена (HEAD detached). Пожалуйста, вернитесь на ветку main перед созданием релиза.")
            # Rollback-release: stay on detached HEAD, will commit on top of it.
            await callback.message.answer("ℹ️ Репозиторий находится в detached HEAD (состояние отката). Создаю релиз поверх текущего активного состояния...")

        # 3. Verify there are changes to commit
        is_clean = status_info.get("is_clean", True)
        history = db.get_version_history()
        current_commit = status_info.get("commit")
        has_new_commits = False
        if is_rollback_release:
            # Compare against the rollback target commit (i.e. the version we rolled back TO),
            # not against history[0] which is the latest-released but not active version.
            rollback_target_commit = db.get_setting("rollback_target_commit") or ""
            if current_commit and rollback_target_commit:
                has_new_commits = current_commit[:7] != rollback_target_commit[:7]
            else:
                has_new_commits = not is_clean
        else:
            last_release_commit = history[0].get("git_commit") if history else None
            if current_commit and last_release_commit:
                has_new_commits = current_commit[:7] != last_release_commit[:7]

        if is_clean and not has_new_commits:
            await callback_edit_or_answer(callback,
                "ℹ️ Нет изменений для создания релиза.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return

        # 4. Increment patch version automatically.
        # Version numbering is ALWAYS based on version_history (authoritative source),
        # never on app_version.py which is display-only metadata.
        # This prevents a manually edited app_version.py from producing wrong version numbers.
        from app_version import get_version_metadata as _get_ver_meta_fallback
        history = db.get_version_history()
        def _ver_tuple(v: str):
            try:
                return tuple(int(x) for x in v.strip().split("."))
            except Exception:
                return (0, 0, 0)
        if history:
            all_versions = [entry.get("version", "") for entry in history]
            max_ver = max(all_versions, key=_ver_tuple)
            new_version = increment_patch_version(max_ver)
        else:
            current_ver_fallback, _ = _get_ver_meta_fallback()
            new_version = increment_patch_version(current_ver_fallback)
        
        await callback.message.answer(f"📈 <b>Новая версия: {new_version}</b>\nНастраиваю Git...")
        
        # Ensure git user config exists inside container so commit doesn't fail
        try:
            subprocess.run(["git", "config", "user.name"], cwd=repo_root, check=True, capture_output=True)
        except Exception:
            subprocess.run(["git", "config", "user.name", "Ksysha Bot"], cwd=repo_root)
            subprocess.run(["git", "config", "user.email", "bot@ksysha.local"], cwd=repo_root)
            
        # 5. Update app_version.py BEFORE git add
        await callback.message.answer("📝 Обновляю файл версии app_version.py...")
        update_app_version_file(new_version, description)
            
        # 6. Git add .
        await callback.message.answer("📦 Добавляю файлы в коммит (git add .)...")
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
        
        # 7. Git commit -m "Release v{new_version}: {description}"
        commit_msg = f"Release v{new_version}: {description}"
        await callback.message.answer(f"💾 Создаю коммит: '{commit_msg}'...")
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_root, check=True)

        # 8. Git push.
        # When releasing from rollback we are in detached HEAD.
        # Move the main branch pointer to this new commit, then push.
        await callback.message.answer("📤 Отправляю изменения на GitHub (git push)...")
        if is_rollback_release:
            # Point main branch at the new detached-HEAD commit, then push it.
            subprocess.run(["git", "branch", "-f", "main", "HEAD"], cwd=repo_root, check=True)
            res_push = subprocess.run(["git", "push", "-f", "origin", "main"], cwd=repo_root, capture_output=True, text=True)
        else:
            res_push = subprocess.run(["git", "push"], cwd=repo_root, capture_output=True, text=True)
        if res_push.returncode != 0:
            raise Exception(f"git push завершился ошибкой:\nStdout: {res_push.stdout}\nStderr: {res_push.stderr}")
            
        # 9. Get new commit hash
        res_hash = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
        new_commit = res_hash.stdout.strip()
        new_commit_short = new_commit[:7]

        # 9a. Create git tag for the new version (protects commit from gc)
        tag_name = f"v{new_version}"
        subprocess.run(["git", "tag", "-f", tag_name, new_commit], cwd=repo_root, capture_output=True)
        subprocess.run(["git", "push", "-f", "origin", tag_name], cwd=repo_root, capture_output=True)

        # 9b. During rollback-release: tag ALL history versions that are missing a tag.
        # This prevents git gc from collecting commits of versions 1.0.233–1.0.250 that are
        # no longer reachable from the main branch after "git branch -f main HEAD".
        if is_rollback_release:
            await callback.message.answer("🏷 Защищаю коммиты всех версий из истории git-тегами...")
            tag_errors = []
            for hist_entry in history:
                h_ver = hist_entry.get("version", "")
                h_commit = hist_entry.get("git_commit", "")
                if not h_ver or not h_commit:
                    continue
                h_tag = f"v{h_ver}"
                # Resolve stored commit to full SHA (DB may store short or full hash)
                resolve_stored = subprocess.run(
                    ["git", "rev-parse", "--verify", h_commit + "^{commit}"],
                    cwd=repo_root, capture_output=True, text=True
                )
                if resolve_stored.returncode != 0:
                    tag_errors.append(f"{h_ver} (коммит {h_commit[:7]} не найден локально)")
                    continue
                full_h_commit = resolve_stored.stdout.strip()
                # Check whether an existing tag already points to exactly this commit
                existing = subprocess.run(
                    ["git", "rev-parse", "--verify", h_tag + "^{commit}"],
                    cwd=repo_root, capture_output=True, text=True
                )
                if existing.returncode == 0 and existing.stdout.strip() == full_h_commit:
                    continue  # already tagged correctly
                subprocess.run(["git", "tag", "-f", h_tag, full_h_commit], cwd=repo_root, capture_output=True)
                push_tag = subprocess.run(
                    ["git", "push", "-f", "origin", h_tag],
                    cwd=repo_root, capture_output=True, text=True
                )
                if push_tag.returncode != 0:
                    tag_errors.append(f"{h_ver} (push тега не удался)")
            if tag_errors:
                await callback.message.answer(
                    f"⚠️ Не удалось запушить теги для: {', '.join(tag_errors)}\n"
                    "Коммиты сохранены локально, но могут быть потеряны после git gc на remote."
                )
            else:
                await callback.message.answer("✅ Все версии из истории защищены git-тегами.")

        # 10. Write new version directly to version_history database table and clear rollback settings
        await callback.message.answer("💾 Записываю новый релиз в историю версий БД...")
        with db._get_connection() as conn:
            db._register_version_history_entry(conn, new_version, description, git_commit=new_commit)
        db.delete_setting("rollback_active")
        db.delete_setting("rollback_previous_commit")
        db.delete_setting("rollback_previous_version")
        db.delete_setting("rollback_target_commit")
            
        # 11. Trigger local restart using existing restart workflow
        await callback.message.answer("🔄 <b>Релиз подготовлен успешно! Запускаю перезапуск проекта...</b>")
        local_ok, local_reason = await _trigger_local_restart()
        if not local_ok:
            raise Exception(f"Не удалось запустить перезапуск: {local_reason}")
            
        await callback.message.answer(
            f"✅ <b>Релиз успешно создан</b>\n"
            f"Версия: <code>{html.escape(new_version)}</code>\n"
            f"Коммит: <code>{html.escape(new_commit_short)}</code>\n\n"
            "Проект перезапускается. Пожалуйста, подождите 10-15 секунд.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")]
            ]),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        # If any step fails, show full error
        error_text = f"❌ <b>Произошла ошибка при создании релиза:</b>\n\n<code>{html.escape(str(e))}</code>"
        await callback.message.answer(
            error_text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data.startswith("admin_version_detail:"))
async def admin_version_detail(callback: CallbackQuery):
    """Детальная информация о выбранной версии"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    parts = callback.data.split(":")
    ver_name = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    
    await callback.answer("Загружаю детали версий…")
    
    # Fetch details from DB
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT version, description, git_commit, created_at, status FROM version_history WHERE version = ? LIMIT 1",
            (ver_name,)
        ).fetchone()
        
    if not row:
        await callback.answer("Версия не найдена.", show_alert=True)
        return
        
    ver = row["version"]
    desc = row["description"]
    commit = row["git_commit"]
    commit_str = commit[:7] if commit else "—"
    
    # Handle missing column in older DB files that weren't migrated
    try:
        status = row["status"] or "stable"
    except IndexError:
        status = "stable"
    date_str = "—"
    created_at = row["created_at"]
    if created_at:
        try:
            dt_part = created_at.split('.')[0]
            dt = datetime.strptime(dt_part, "%Y-%m-%d %H:%M:%S")
            date_str = dt.strftime("%d.%m.%Y %H:%M")
        except Exception:
            try:
                dt = datetime.fromisoformat(created_at)
                date_str = dt.strftime("%d.%m.%Y %H:%M")
            except Exception:
                date_str = created_at
                
    ver_esc = html.escape(str(ver))
    desc_esc = html.escape(str(desc))
    commit_esc = html.escape(str(commit_str))
    date_esc = html.escape(str(date_str))
    
    commit_block = f"<code>{html.escape(str(commit))}</code>" if commit else "—"
    def _parse_version(v_str):
        try:
            return tuple(map(int, v_str.split(".")))
        except Exception:
            return (0, 0, 0)
            
    is_supported = _parse_version(ver) >= (1, 0, 14)
    
    from app_version import get_git_commit
    running_commit = get_git_commit()
    is_current = commit and running_commit and commit[:8] == running_commit[:8]
    
    if status == "broken":
        status_label = "❌ Сломана"
    elif not is_supported:
        status_label = "⚠️ Устаревшая"
    else:
        status_label = "✅ Стабильная"
        
    rollback_supported_label = "Да" if is_supported and status != "broken" else "Нет"
    current_running_label = "Да" if is_current else "Нет"

    text = (
        f"📦 <b>Детали версии</b>\n\n"
        f"<b>Версия:</b> <code>{ver_esc}</code>\n"
        f"<b>Статус:</b> {status_label}\n"
        f"<b>Откат поддерживается:</b> {rollback_supported_label}\n"
        f"<b>Запущенная версия:</b> {current_running_label}\n\n"
        f"<b>Коммит:</b>\n{commit_block}\n\n"
        f"<b>Дата:</b>\n{date_esc}\n\n"
        f"<b>Описание:</b>\n{desc_esc}"
    )
    
    # Check rollback button rules
    from app_version import get_git_commit
    running_commit = get_git_commit()
    
    is_rollback_active = db.get_setting("rollback_active") == "1"
    prev_commit = db.get_setting("rollback_previous_commit")
    
    show_rollback_btn = False
    show_undo_btn = False
    
    def _parse_version(v_str):
        try:
            return tuple(map(int, v_str.split(".")))
        except Exception:
            return (0, 0, 0)
            
    is_supported = _parse_version(ver) >= (1, 0, 14)
    
    if is_rollback_active and prev_commit and commit and commit[:8] == prev_commit[:8]:
        show_undo_btn = True
    elif commit and running_commit and commit[:8] != running_commit[:8] and status != "broken" and is_supported:
        show_rollback_btn = True
        
    if commit and running_commit and commit[:8] != running_commit[:8] and status != "broken" and not is_supported:
        text += "\n\n⚠️ <i>Откат недоступен: Версия не поддерживает фреймворк отката (слишком старая).</i>"
        
    keyboard = []
    if show_undo_btn:
        keyboard.append([
            InlineKeyboardButton(text="↩️ Вернуться к предыдущей версии", callback_data="admin_undo_rollback")
        ])
    elif show_rollback_btn:
        keyboard.append([
            InlineKeyboardButton(text="🔄 Откатить к этой версии", callback_data=f"admin_rollback_trigger:{ver}")
        ])
        
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад к списку", callback_data=f"admin_version_history:{page}")])
    
    await callback_edit_or_answer(callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


def _get_rollback_status_block() -> str:
    """Returns a formatted status text distinguishing between running app and repo state."""
    from app_version import version as running_ver, get_git_commit, _get_repo_root
    import subprocess
    import os
    
    running_commit = get_git_commit() or "—"
    
    # Get repo branch & HEAD version/commit
    repo_branch = "—"
    repo_commit = "—"
    repo_root = _get_repo_root()
    try:
        proc_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_branch.returncode == 0:
            repo_branch = proc_branch.stdout.strip()
            
        proc_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_commit.returncode == 0:
            repo_commit = proc_commit.stdout.strip()[:8]
    except Exception:
        pass
        
    is_rollback_active = db.get_setting("rollback_active") == "1"
    
    status_lines = []
    if is_rollback_active:
        status_lines.append("⚠️ <b>Активен откат</b>\n")
        status_lines.append(f"Запущенная версия: <code>{html.escape(str(running_ver))}</code>")
        status_lines.append(f"Запущенный коммит: <code>{html.escape(str(running_commit))}</code>\n")
        status_lines.append("<b>Репозиторий:</b>")
        status_lines.append(f"Ветка: <code>{html.escape(str(repo_branch))}</code>")
        status_lines.append(f"Коммит: <code>{html.escape(str(repo_commit))}</code>\n")
    else:
        status_lines.append("✅ <b>Система стабильна</b>\n")
        status_lines.append(f"Запущенная версия: <code>{html.escape(str(running_ver))}</code>")
        status_lines.append(f"Запущенный коммит: <code>{html.escape(str(running_commit))}</code>\n")
        
    return "\n".join(status_lines)


@router.callback_query(F.data.startswith("admin_rollback_trigger:"))
async def admin_rollback_trigger(callback: CallbackQuery):
    """Показ экрана подтверждения отката с safety проверками"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    parts = callback.data.split(":")
    ver = parts[1]
    
    # Fetch commit hash for this version
    with db._get_connection() as conn:
        row = conn.execute("SELECT git_commit FROM version_history WHERE version = ? LIMIT 1", (ver,)).fetchone()
        
    if not row or not row["git_commit"]:
        await callback.answer("Ошибка: коммит не найден в истории версий.", show_alert=True)
        return
        
    commit = row["git_commit"]
    commit_short = commit[:7]
    
    # 1. Run hard safety checks before confirmation (these always block on failure)
    try:
        from app_version import get_git_status_info, _get_repo_root
        status_info = get_git_status_info()
        repo_root = _get_repo_root()

        # Check if repository is clean
        if not status_info.get("is_clean", True):
            modified = status_info.get("modified_files", [])
            warning_text = (
                "⚠️ <b>Внимание: невозможно начать откат</b>\n\n"
                "В репозитории есть незакоммиченные изменения:\n"
                + "\n".join([f"• <code>{html.escape(f)}</code>" for f in modified[:10]])
                + (f"\nи ещё {len(modified)-10} файлов" if len(modified) > 10 else "")
                + "\n\nПожалуйста, сбросьте или закоммитьте изменения перед откатом."
            )
            await callback.message.answer(warning_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]))
            await callback.answer()
            return

        # Verify target commit exists in local repo
        import subprocess
        proc_verify = subprocess.run(
            ["git", "rev-parse", "--verify", commit],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_verify.returncode != 0:
            raise Exception(f"Целевой коммит {commit_short} не найден в локальном репозитории.")

    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Проверка безопасности не пройдена:</b>\n\n<code>{html.escape(str(e))}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()
        return

    # 2. Optional deployer health check — informs the confirmation screen but never blocks it.
    #    If deployer is unreachable the confirm handler will fall back to local restart,
    #    exactly the same contract as admin_restart.
    deployer_note = ""
    try:
        import aiohttp
        from urllib.parse import urlsplit, urlunsplit
        deploy_url = (getattr(config, "DEPLOYER_URL", "") or "").strip()
        if deploy_url:
            _parts = urlsplit(deploy_url)
            health_url = urlunsplit((_parts.scheme, _parts.netloc, "/health", "", ""))
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(health_url) as resp:
                    if resp.status != 200:
                        deployer_note = (
                            "\n⚠️ <i>Deployer вернул ошибку — будет использован "
                            "локальный безопасный перезапуск.</i>"
                        )
    except Exception:
        deployer_note = (
            "\n⚠️ <i>Deployer недоступен — будет использован "
            "локальный безопасный перезапуск.</i>"
        )

    from app_version import version as current_ver, get_git_commit
    current_commit = get_git_commit() or "—"
    is_rollback_active = db.get_setting("rollback_active") == "1"
    
    warnings = []
    if is_rollback_active:
        warnings.append("⚠️ <b>Активен другой откат.</b> Выполнение нового отката перезапишет текущий.")
        
    with db._get_connection() as conn:
        status_row = conn.execute("SELECT status FROM version_history WHERE version = ?", (ver,)).fetchone()
        target_status = status_row["status"] if status_row else "stable"
        
    if target_status == "broken":
        warnings.append("❌ <b>СЛОМАННАЯ ВЕРСИЯ:</b> Откат к этой версии крайне не рекомендуется.")
        
    def _parse_version(v_str):
        try: return tuple(map(int, v_str.split(".")))
        except Exception: return (0, 0, 0)
    if _parse_version(ver) < (1, 0, 14):
        warnings.append("⚠️ <b>Legacy Версия:</b> Версия не поддерживает фреймворк отката. После отката UI возврата не будет доступен.")
        
    warning_block = "\n".join(warnings) + "\n\n" if warnings else ""
    
    text = (
        "⚠️ <b>Подтверждение отката</b>\n\n"
        f"<b>Целевая версия:</b> <code>{html.escape(ver)}</code>\n"
        f"<b>Целевой коммит:</b> <code>{html.escape(commit_short)}</code>\n\n"
        f"<b>Текущая запущенная версия:</b> <code>{html.escape(current_ver)}</code>\n"
        f"<b>Текущий запущенный коммит:</b> <code>{html.escape(current_commit[:8])}</code>\n\n"
        f"{warning_block}"
        "Проект будет пересобран и перезапущен."
        + deployer_note
        + "\n\nУверен, что хочешь продолжить?"
    )

    keyboard = [
        [
            InlineKeyboardButton(text="✅ Подтвердить откат", callback_data=f"admin_confirm_rollback:{ver}:{commit[:8]}"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin_panel")
        ]
    ]

    await callback_edit_or_answer(
        callback,
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("admin_confirm_rollback:"))
async def admin_confirm_rollback(callback: CallbackQuery):
    """Выполнение отката: checkout на коммит и триггер пересборки"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    parts = callback.data.split(":")
    ver = parts[1]
    commit = parts[2]
    
    await callback_edit_or_answer(callback, "🚀 <b>Запуск отката...</b>\n\nВыполняю checkout и деплой...", parse_mode=ParseMode.HTML)
    
    try:
        from app_version import _get_repo_root, get_git_commit
        repo_root = _get_repo_root()
        current_commit = get_git_commit() or "—"
        # current_ver: from version_history (authoritative), not from app_version.py file
        _hist_rb = db.get_version_history()
        def _ver_tuple_rb(v: str):
            try:
                return tuple(int(x) for x in str(v).strip().split("."))
            except Exception:
                return (0, 0, 0)
        if _hist_rb:
            current_ver = max((_e.get("version", "") for _e in _hist_rb), key=_ver_tuple_rb)
        else:
            from app_version import version as _av_fallback
            current_ver = _av_fallback or "unknown"
        # -1. Verify rollback compatibility
        def _parse_version(v_str):
            try:
                return tuple(map(int, v_str.split(".")))
            except Exception:
                return (0, 0, 0)
        if _parse_version(ver) < (1, 0, 14):
            await callback_edit_or_answer(callback, "❌ <b>Откат прерван!</b>\n\nВерсия слишком старая и не поддерживает фреймворк отката.", parse_mode=ParseMode.HTML)
            return
            
        with db._get_connection() as conn:
            status_row = conn.execute("SELECT status FROM version_history WHERE version = ?", (ver,)).fetchone()
            if status_row:
                try:
                    ver_status = status_row["status"]
                    if ver_status == "broken":
                        await callback_edit_or_answer(callback, "❌ <b>Откат прерван!</b>\n\nЭта версия помечена как СЛОМАНАЯ.", parse_mode=ParseMode.HTML)
                        return
                except IndexError:
                    pass

        import subprocess
        # 0.a Verify target commit exists
        check_commit = subprocess.run(["git", "rev-parse", "--verify", commit], cwd=repo_root, capture_output=True, text=True)
        if check_commit.returncode != 0:
            await callback_edit_or_answer(callback, "❌ <b>Откат прерван!</b>\n\nЦелевой коммит не найден в репозитории.", parse_mode=ParseMode.HTML)
            return
            
        # 0.b Verify clean repository state
        check_clean = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True)
        if check_clean.stdout.strip() != "":
            await callback_edit_or_answer(callback, "❌ <b>Откат прерван!</b>\n\nВ репозитории есть незакоммиченные изменения. Откат небезопасен.", parse_mode=ParseMode.HTML)
            return

        # 0.c Pre-flight Syntax Validation
        import subprocess
        files_res = subprocess.run(["git", "ls-tree", "-r", "--name-only", commit], cwd=repo_root, capture_output=True, text=True)
        if files_res.returncode == 0:
            for f_name in files_res.stdout.splitlines():
                if f_name.endswith('.py'):
                    content_res = subprocess.run(["git", "show", f"{commit}:{f_name}"], cwd=repo_root, capture_output=True, text=True)
                    if content_res.returncode == 0:
                        try:
                            compile(content_res.stdout, f_name, 'exec')
                        except SyntaxError as e:
                            err_msg = f"❌ <b>Откат прерван!</b>\n\nВ целевой версии обнаружена критическая синтаксическая ошибка:\n<code>{e}</code>\nФайл: <code>{f_name}</code>"
                            await callback_edit_or_answer(callback, err_msg, parse_mode=ParseMode.HTML)
                            return
        

        
        # 2. Perform git checkout to target commit in host workspace
        import subprocess
        from datetime import datetime, timezone

        check_main = subprocess.run(["git", "rev-parse", "main"], cwd=repo_root, capture_output=True, text=True)
        is_main = check_main.returncode == 0 and check_main.stdout.strip().startswith(commit[:7])

        # Check if there are local changes that need stashing
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True)
        has_changes = bool(status_res.stdout.strip())
        
        if has_changes:
            subprocess.run(["git", "stash"], cwd=repo_root, capture_output=True)

        if is_main:
            # We are returning to the tip of main. Clear rollback state.
            db.delete_setting("rollback_active")
            db.delete_setting("rollback_previous_commit")
            db.delete_setting("rollback_previous_version")
            db.delete_setting("rollback_target_commit")
            
            db.set_setting("last_rollback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
            db.set_setting("last_rollback_target", "main")
            db.set_setting("last_rollback_source_ver", current_ver)
            db.set_setting("last_rollback_dest_ver", ver)

            checkout_res = subprocess.run(
                ["git", "checkout", "main"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10
            )
        else:
            # Rolling back to older commit. Set rollback_active to block releases.
            db.set_setting("rollback_active", "1")
            db.set_setting("rollback_previous_commit", current_commit)
            db.set_setting("rollback_previous_version", current_ver)
            db.set_setting("rollback_target_commit", commit)
            
            db.set_setting("last_rollback_date", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
            db.set_setting("last_rollback_target", commit)
            db.set_setting("last_rollback_source_ver", current_ver)
            db.set_setting("last_rollback_dest_ver", ver)

            checkout_res = subprocess.run(
                ["git", "checkout", commit],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10
            )
            
        if has_changes:
            subprocess.run(["git", "stash", "pop"], cwd=repo_root, capture_output=True)
        if checkout_res.returncode != 0:
            # Revert DB settings if checkout fails
            db.delete_setting("rollback_active")
            db.delete_setting("rollback_previous_commit")
            db.delete_setting("rollback_previous_version")
            db.delete_setting("rollback_target_commit")
            raise Exception(f"git checkout завершился с ошибкой:\nStdout: {checkout_res.stdout}\nStderr: {checkout_res.stderr}")
            
        # 3. Trigger rebuild: try deployer first, fall back to local restart
        #    (same contract as admin_restart — deployer is optional)
        await callback.message.answer("📦 Запускаю пересборку Docker контейнера...")
        from handlers import _trigger_deploy, _trigger_local_restart
        ok, reason = await _trigger_deploy()
        used_local = False
        if not ok and reason != "in_progress":
            local_ok, local_reason = await _trigger_local_restart()
            if local_ok:
                ok = True
                used_local = True
            elif local_reason == "in_progress":
                ok = True
                used_local = True
            else:
                # Both paths failed — revert checkout and clear state
                subprocess.run(["git", "checkout", "main"], cwd=repo_root)
                db.delete_setting("rollback_active")
                db.delete_setting("rollback_previous_commit")
                db.delete_setting("rollback_previous_version")
                db.delete_setting("rollback_target_commit")
                raise Exception(
                    f"Не удалось запустить пересборку.\n"
                    f"deployer: {reason}\nlocal: {local_reason}"
                )

        if not ok:
            # reason == "in_progress" from deployer
            await callback.message.answer(
                "⏳ Перезапуск уже выполняется. Нажми «🔄 Обновить статус».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")]
                ]),
            )
            return

        restart_note = (
            "\n\n⏳ <i>Deployer недоступен, используется локальный безопасный перезапуск.</i>"
            if used_local else ""
        )
        await callback.message.answer(
            f"✅ <b>Запущен откат до версии {ver}</b>\n\n"
            "Проект пересобирается и перезапускается в фоне. Пожалуйста, подождите 10-15 секунд."
            + restart_note,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")]
            ]),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Ошибка при выполнении отката:</b>\n\n<code>{html.escape(str(e))}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )


@router.callback_query(F.data == "admin_undo_rollback")
async def admin_undo_rollback(callback: CallbackQuery):
    """Отмена отката: возврат на main и пересборка"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
        
    await callback_edit_or_answer(callback, "🚀 <b>Запуск отмены отката...</b>\n\nВыполняю возврат на ветку main и деплой...", parse_mode=ParseMode.HTML)
    
    try:
        from app_version import _get_repo_root
        repo_root = _get_repo_root()
        
        # 1. Perform git checkout main in host workspace
        import subprocess
        
        # Check if there are local changes that need stashing
        status_res = subprocess.run(["git", "status", "--porcelain"], cwd=repo_root, capture_output=True, text=True)
        has_changes = bool(status_res.stdout.strip())
        
        if has_changes:
            subprocess.run(["git", "stash"], cwd=repo_root, capture_output=True)

        checkout_res = subprocess.run(
            ["git", "checkout", "main"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if has_changes:
            subprocess.run(["git", "stash", "pop"], cwd=repo_root, capture_output=True)

        if checkout_res.returncode != 0:
            raise Exception(f"git checkout main завершился с ошибкой:\nStdout: {checkout_res.stdout}\nStderr: {checkout_res.stderr}")
            
        # 2. Clear DB persistent state
        db.delete_setting("rollback_active")
        db.delete_setting("rollback_previous_commit")
        db.delete_setting("rollback_previous_version")
        db.delete_setting("rollback_target_commit")
        
        # 3. Trigger rebuild: try deployer first, fall back to local restart
        #    (same contract as admin_restart — deployer is optional)
        await callback.message.answer("📦 Запускаю пересборку Docker контейнера с ветки main...")
        from handlers import _trigger_deploy, _trigger_local_restart
        ok, reason = await _trigger_deploy()
        used_local = False
        if not ok and reason != "in_progress":
            local_ok, local_reason = await _trigger_local_restart()
            if local_ok:
                ok = True
                used_local = True
            elif local_reason == "in_progress":
                ok = True
                used_local = True
            else:
                raise Exception(
                    f"Не удалось запустить пересборку.\n"
                    f"deployer: {reason}\nlocal: {local_reason}"
                )

        if not ok:
            # reason == "in_progress" from deployer
            await callback.message.answer(
                "⏳ Перезапуск уже выполняется. Нажми «🔄 Обновить статус».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")]
                ]),
            )
            return

        restart_note = (
            "\n\n⏳ <i>Deployer недоступен, используется локальный безопасный перезапуск.</i>"
            if used_local else ""
        )
        await callback.message.answer(
            "✅ <b>Запущено возвращение к последней версии main</b>\n\n"
            "Проект пересобирается и перезапускается в фоне. Пожалуйста, подождите 10-15 секунд."
            + restart_note,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить статус", callback_data="admin_restart_refresh")]
            ]),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await callback.message.answer(
            f"❌ <b>Ошибка при отмене отката:</b>\n\n<code>{html.escape(str(e))}</code>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
            ]),
            parse_mode=ParseMode.HTML
        )


BACKUP_EXPORT_DIR = "backup"
BACKUP_DB_FILENAME = "memories_backup_latest.db"


@router.callback_query(F.data == "admin_export_memories")
async def admin_export_memories(callback: CallbackQuery):
    """Экспорт в JSON (отправка в чат) и сохранение точной копии БД в папку backup."""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback.answer("Формирую экспорт…")
    try:
        data = db.get_export_data()
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        filename = f"memories_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        doc = BufferedInputFile(json_bytes, filename=filename)
        await callback.message.answer_document(
            document=doc,
            caption="📤 Экспорт: важные моменты, воспоминания, важные даты, события на дату, желания (с форматированием, приватностью и медиа-данными).",
        )
        backup_dir = Path(__file__).resolve().parent / BACKUP_EXPORT_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / BACKUP_DB_FILENAME
        if backup_path.exists():
            backup_path.unlink()
        db.backup_database(backup_path=str(backup_path))
    except Exception as e:
        await callback_edit_or_answer(callback, f"❌ Ошибка экспорта: {e}")


@router.callback_query(F.data == "admin_restart")
async def admin_restart(callback: CallbackQuery):
    """Запускает безопасный деплой через deployer-сервис."""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback.answer("Запускаю перезапуск…")
    kb = _build_restart_kb()
    try:
        ok, reason = await _trigger_deploy()
        if ok:
            await callback.message.answer(
                "⏳ Перезапуск запущен через deployer. Контейнеры будут пересозданы, это может занять до 2-3 минут.\n"
                "Нажми «🔄 Обновить статус».",
                reply_markup=kb,
            )
            return
        if reason == "in_progress":
            await callback.message.answer(
                "⏳ Перезапуск уже выполняется. Нажми «🔄 Обновить статус».",
                reply_markup=kb,
            )
            return
        local_ok, local_reason = await _trigger_local_restart()
        if local_ok:
            await callback.message.answer(
                "⏳ Deployer недоступен, запустил локальный безопасный перезапуск. "
                "Контейнеры будут пересозданы, это может занять до 2-3 минут.\n"
                "Нажми «🔄 Обновить статус».",
                reply_markup=kb,
            )
            return
        if local_reason == "in_progress":
            await callback.message.answer(
                "⏳ Перезапуск уже выполняется. Нажми «🔄 Обновить статус».",
                reply_markup=kb,
            )
            return
        await callback.message.answer(
            "❌ Не удалось запустить перезапуск ни через deployer, ни локально.\n"
            f"deployer: {html.escape(reason)}\n"
            f"local: {html.escape(local_reason)}",
            reply_markup=kb,
        )
    except Exception as e:
        await callback.message.answer(
            f"❌ Ошибка запуска перезапуска: {e}",
            reply_markup=kb,
        )


@router.callback_query(F.data == "admin_restart_refresh")
async def admin_restart_refresh(callback: CallbackQuery):
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED, show_alert=True)
        return
    await callback.answer("Обновляю статус…")

    kb = _build_restart_kb()
    status = _read_restart_status()
    if status:
        status = _recover_restart_status_if_needed(status)
        text = await _format_restart_status_text(status)
        await callback_edit_or_answer(
            callback,
            text,
            reply_markup=kb,
        )
        return
    deploy_status = await _read_deploy_status()
    if deploy_status:
        await callback_edit_or_answer(
            callback,
            _format_deploy_report(deploy_status),
            reply_markup=kb,
        )
        return
    await callback_edit_or_answer(
        callback,
        "ℹ️ Статус пока недоступен. Запусти перезапуск и обнови позже.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "admin_toggle_test")
async def admin_toggle_test(callback: CallbackQuery):
    """Переключение тест/продакшн: меняет настройку и обновляет клавиатуру."""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    current = db.get_setting("test_version")
    if current == "1":
        db.set_setting("test_version", "0")
        db.set_setting("maintenance_visits", "0")
        try:
            from http_api import broadcast_maintenance_off
            asyncio.create_task(broadcast_maintenance_off())
        except Exception:
            pass
        status_text = "✅ Включена продакшн версия. Бот и сайт работают в обычном режиме."
    else:
        db.set_setting("test_version", "1")
        db.set_setting("maintenance_started_at", now_utc)
        try:
            from http_api import broadcast_maintenance_on
            asyncio.create_task(broadcast_maintenance_on())
        except Exception:
            pass
        status_text = "🔧 Включена тест версия. При /start всем показывается перерыв; сайт отображает страницу технического перерыва (визиты продолжают логироваться)."
    await callback.answer(status_text[:200])
    try:
        await callback.message.edit_reply_markup(reply_markup=create_admin_keyboard())
    except Exception:
        await callback_edit_or_answer(callback, 
            "🔧 <b>Админ-панель</b>\n\n" + status_text,
            reply_markup=create_admin_keyboard(),
            parse_mode=ParseMode.HTML,
        )


def _device_relative_time(last_seen_utc: Optional[str]) -> str:
    """Возвращает «X мин назад» или «в сети» для last_seen_utc."""
    if not last_seen_utc:
        return "—"
    try:
        dt = datetime.strptime(last_seen_utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        if delta.total_seconds() < 120:
            return "в сети"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() // 60)} мин назад"
        if delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds() // 3600)} ч назад"
        if delta.days < 30:
            return f"{delta.days} дн. назад"
        return f"{delta.days // 30} мес. назад"
    except Exception:
        return last_seen_utc[:16] if last_seen_utc else "—"


@router.callback_query(F.data == "admin_devices")
async def admin_devices(callback: CallbackQuery):
    """Список устройств: кнопки по 2 в ряд, текст «Android 16 • 2 мин назад»."""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    devices = db.get_devices_list()
    if not devices:
        try:
            await callback.message.edit_text(
                "📱 <b>Устройства</b>\n\nСписок пуст. Устройства появятся после визитов на сайт.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
                ]),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            await callback_edit_or_answer(callback, 
                "📱 <b>Устройства</b>\n\nСписок пуст. Устройства появятся после визитов на сайт.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
                ]),
                parse_mode=ParseMode.HTML,
            )
        await callback.answer()
        return
    keyboard = []
    for i in range(0, len(devices), 2):
        row = []
        for j in range(2):
            if i + j < len(devices):
                d = devices[i + j]
                label = (d.get("ua_pretty") or "Устройство")[:25]
                rel = _device_relative_time(d.get("last_seen_utc"))
                text = f"{label} • {rel}"
                if len(text) > 32:
                    text = text[:29] + "…"
                row.append(InlineKeyboardButton(
                    text=text,
                    callback_data=f"admin_device_{d['id']}",
                ))
        if row:
            keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")])
    try:
        await callback.message.edit_text(
            "📱 <b>Устройства</b>\n\nВыбери устройство:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "📱 <b>Устройства</b>\n\nВыбери устройство:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_device_") & ~F.data.startswith("admin_device_delete_"))
async def admin_device_detail(callback: CallbackQuery):
    """Детальная карточка устройства по id."""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        device_id = int(callback.data.replace("admin_device_", ""))
    except ValueError:
        await callback.answer("Ошибка")
        return
    dev = db.get_device_by_id(device_id)
    if not dev:
        await callback.answer("Устройство не найдено")
        return
    from utils import format_datetime_for_user
    ua = dev.get("ua_pretty") or "—"
    visits = dev.get("visit_count") or 0
    first_utc = dev.get("first_seen_utc")
    last_utc = dev.get("last_seen_utc")
    first_str = format_datetime_for_user(
        (first_utc or "") if (first_utc and len(first_utc) >= 19) else ((first_utc or "") + " 12:00:00"),
        dev.get("timezone_id"),
    ) if first_utc else "—"
    last_str = format_datetime_for_user((last_utc or "")[:19], dev.get("timezone_id")) if last_utc else "—"
    last_rel = _device_relative_time(last_utc)
    loc_country = (dev.get("country") or "") + (f", {dev.get('city')}" if dev.get("city") else "")
    loc_country = loc_country.strip() or "—"
    isp = dev.get("isp") or "—"
    coords = dev.get("coords") or "—"
    tz = dev.get("timezone_id") or "—"
    os_str = (dev.get("os") or "—") + (f" {dev.get('os_version')}" if dev.get("os_version") else "")
    browser_str = dev.get("browser") or ua or "—"
    arch = dev.get("architecture") or "—"
    dtype = dev.get("device_type") or "—"
    model = dev.get("model") or "—"
    gpu_v = dev.get("gpu_vendor") or "—"
    gpu_r = dev.get("gpu_renderer") or "—"
    sw = dev.get("screen_w")
    sh = dev.get("screen_h")
    screen_str = f"{sw}x{sh}" if sw and sh else "—"
    pr = dev.get("pixel_ratio")
    if pr and sw and sh:
        screen_str += f"\n🔍 Плотность: {pr}x (реальное: {int(sw*pr)}x{int(sh*pr)})"
    elif pr:
        screen_str += f"\n🔍 Плотность: {pr}x" if screen_str != "—" else f"Плотность: {pr}x"
    color = dev.get("color_depth")
    screen_str += f"\n🎨 Глубина цвета: {color} bit" if color else ""
    orient = dev.get("orientation") or "—"
    if orient != "—":
        screen_str += f"\n📱 Ориентация: {orient}"
    lang = dev.get("language") or "—"
    cookies = "включены" if dev.get("cookies_enabled") else ("выключены" if dev.get("cookies_enabled") is False else "—")
    dnt = "включено" if dev.get("do_not_track") else ("выключено" if dev.get("do_not_track") is False else "—")
    ram = f"{dev.get('ram_gb')} ГБ" if dev.get("ram_gb") else "—"
    cpu = f"{dev.get('cpu_cores')} ядер" if dev.get("cpu_cores") else "—"
    tp = dev.get("touch_points")
    touch = f"да ({tp} точек)" if tp and tp > 0 else ("нет" if tp == 0 else "—")
    is_bot = "да" if dev.get("is_bot") else "нет"
    conn_t = dev.get("connection_type") or "—"
    downlink = f"{dev.get('downlink_mbps')} Mbps" if dev.get("downlink_mbps") is not None else "—"
    rtt = f"{dev.get('rtt_ms')} ms" if dev.get("rtt_ms") is not None else "—"
    save_data = "включен" if dev.get("save_data") else ("выключен" if dev.get("save_data") is False else "—")
    bat = dev.get("battery_level")
    bat_str = f"{bat}%" if bat is not None else "—"
    bat_charge = "заряжается" if dev.get("battery_charging") else ("не заряжается" if dev.get("battery_charging") is False else "—")
    ip_s = dev.get("ip_server") or "—"
    ip_w = dev.get("ip_webrtc") or "—"
    ref = (dev.get("referrer") or "—")[:80]
    theme = "тёмная" if dev.get("theme") == "dark" else ("светлая" if dev.get("theme") == "light" else (dev.get("theme") or "—"))
    role_raw = (dev.get("role") or "").lower()
    role_display = "Партнёр" if role_raw in ("ksyusha", "partner") else ("Разработчик" if role_raw == "creator" else None)
    lines = [
        f"📱 Устройство: {ua}",
        f"🔢 Визитов: {visits}",
        f"📅 Первый вход: {first_str}",
        f"🕒 Последний вход: {last_str} ({last_rel})",
        f"👤 Роль: {role_display}" if role_display else None,
        "",
        "📍 МЕСТОПОЛОЖЕНИЕ",
        f"🌍 Страна/город: {loc_country}",
        f"🏢 Провайдер: {isp}",
        f"📌 Координаты: {coords}",
        f"🌐 Часовой пояс: {tz}",
        "",
        "💻 УСТРОЙСТВО",
        f"📱 ОС: {os_str}",
        f"🌐 Браузер: {browser_str}",
        f"📐 Архитектура: {arch}",
        f"📲 Тип: {dtype}",
        f"Видеокарта: {gpu_v} / {gpu_r}" if (gpu_v and gpu_v != "—") or (gpu_r and gpu_r != "—") else None,
        f"📛 Модель: {model}" if model != "—" else None,
        "",
        "🖥 ЭКРАН",
        f"📏 Разрешение: {screen_str}" if screen_str != "—" else None,
        "",
        "🌐 БРАУЗЕР И СИСТЕМА",
        f"🗣 Язык: {lang}",
        f"🍪 Куки: {cookies}",
        f"🚫 Не отслеживать: {dnt}",
        f"💾 RAM: {ram}" if ram != "—" else None,
        f"⚙️ Ядер CPU: {cpu}" if cpu != "—" else None,
        f"👆 Сенсорный экран: {touch}" if touch != "—" else None,
        f"🤖 Это бот: {is_bot}",
        "",
        "📶 СОЕДИНЕНИЕ",
        f"📡 Тип: {conn_t}",
        f"⚡️ Скорость: {downlink}" if downlink != "—" else None,
        f"📉 Задержка: {rtt}" if rtt != "—" else None,
        f"💡 Режим экономии: {save_data}" if save_data != "—" else None,
        "",
        "🔋 БАТАРЕЯ",
        f"⚡️ Заряд: {bat_str}",
        f"🔌 Статус: {bat_charge}" if bat_charge != "—" else None,
        "",
        "🔒 СЕТЬ",
        f"📎 IP (сервер): {ip_s}",
        f"📎 IP (WebRTC): {ip_w}",
        f"↩️ Реферер: {ref}",
        "",
        "🎨 ПРОЧЕЕ",
        f"🌙 Тема сайта: {theme}",
    ]
    text = "\n".join(l for l in lines if l is not None)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    back_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="❌ Удалить устройство", callback_data=f"admin_device_delete_{device_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 К списку устройств", callback_data="admin_devices"),
        ],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=back_kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback_edit_or_answer(callback, text, reply_markup=back_kb, parse_mode=ParseMode.HTML)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_device_delete_"))
async def admin_device_delete(callback: CallbackQuery):
    """Удаление устройства по id из админки."""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    try:
        device_id = int(callback.data.replace("admin_device_delete_", ""))
        print(f"[admin_device_delete] Попытка удалить устройство id={device_id}")
    except ValueError:
        print(f"[admin_device_delete] Не смог распарсить id из callback.data={callback.data!r}")
        await callback.answer("Ошибка")
        return
    from database import db as _db
    ok = _db.delete_device_by_id(device_id)
    print(f"[admin_device_delete] Результат удаления id={device_id}: ok={ok}")
    if not ok:
        await callback.answer("Не удалось удалить устройство")
        return
    try:
        await callback.message.edit_text(
            "✅ Устройство удалено.\n\nВернись к списку устройств, чтобы обновить данные.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📱 К списку устройств", callback_data="admin_devices")]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "✅ Устройство удалено.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="📱 К списку устройств", callback_data="admin_devices")]
                ]
            ),
            parse_mode=ParseMode.HTML,
        )
    await callback.answer("Устройство удалено")


@router.callback_query(F.data == "wishes_menu")
async def wishes_menu(callback: CallbackQuery):
    """Меню желаний — доступно любому участнику пары"""
    user_id = callback.from_user.id

    if not is_wishes_available():
        await callback.answer("Эта функция сейчас недоступна")
        return
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Эта функция недоступна — вы не состоите в паре")
        return

    user_wishes = db.get_user_wishes(user_id) or []
    partner_id = db.get_partner_id(user_id)
    partner_wishes = []
    if partner_id:
        partner_wishes = db.get_user_wishes(partner_id) or []

    all_wishes = list(user_wishes) + list(partner_wishes)
    all_wishes.sort(key=lambda w: w.id)

    intro_text = (
        "💫 <b>Желания пары</b>\n\n"
        "Здесь можно просмотреть и добавить желания"
    )
    await callback_edit_or_answer(callback,
        intro_text,
        reply_markup=create_wishes_menu_keyboard(all_wishes),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("page_info_"))
async def page_info(callback: CallbackQuery):
    """Техническая кнопка отображения номера страницы (ничего не делает)"""
    await callback.answer()


@router.callback_query(F.data == "broadcast_menu")
async def broadcast_menu(callback: CallbackQuery, state: FSMContext):
    """Меню выбора получателей рассылки"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback_edit_or_answer(callback, 
        "📨 <b>Рассылка</b>\n\n"
        "Кому отправить рассылку?",
        reply_markup=create_broadcast_target_keyboard(),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "add_admin")
async def add_admin_callback(callback: CallbackQuery, state: FSMContext):
    """Добавление нового администратора"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED_CREATOR)
        return
    
    await state.set_state(AdminStates.waiting_for_admin_input)
    
    try:
        await callback.message.edit_text(
            "➕ <b>Добавить администратора</b>\n\n"
            "Отправь <b>числовой ID</b> пользователя Telegram (только цифры).\n"
            "Узнать ID можно через @userinfobot.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="list_admins")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_add_cancel")],
            ]),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "➕ <b>Добавить администратора</b>\n\n"
            "Отправь <b>числовой ID</b> пользователя Telegram (только цифры).\n"
            "Узнать ID можно через @userinfobot.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="list_admins")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_add_cancel")],
            ]),
            parse_mode=ParseMode.HTML
        )
    

@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    """Показ статистики"""
    user_id = callback.from_user.id

    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return

    total_stats = db.get_total_stats()
    user_stats = db.get_user_stats(user_id)

    stats_text = format_stats_message(total_stats, user_stats)
    await callback_edit_or_answer(callback, 
        stats_text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]
        ]),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "list_admins")
async def list_admins(callback: CallbackQuery):
    """Список администраторов - показывает кнопки"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    admins = db.get_all_admins()
    
    if not admins:
        await callback_edit_or_answer(callback, 
            "👥 <b>Список администраторов пуст</b>",
            reply_markup=create_admin_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    list_lines = "\n".join(format_admin_short_line(a) for a in admins)
    await callback_edit_or_answer(callback, 
        "📋 <b>Администраторы</b>\n\n"
        f"{list_lines}\n\n"
        "Нажми на ID для просмотра и статистики:",
        reply_markup=create_admin_list_keyboard(admins),
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "creator_wishes_ksusha")
@router.callback_query(F.data == "creator_wishes_partner")
@router.callback_query(F.data == "partner_wishes")
async def partner_wishes_view(callback: CallbackQuery):
    await wishes_menu(callback)


@router.callback_query(F.data == "creator_wish_empty")
async def creator_wish_empty(callback: CallbackQuery):
    await callback.answer("Партнёр ещё не добавил(а) это желание")


@router.callback_query(F.data == "wish_add_new")
async def wish_add(callback: CallbackQuery, state: FSMContext):
    """Начало добавления желания — доступно обоим участникам пары"""
    user_id = callback.from_user.id
    if not is_wishes_available():
        await callback.answer("Эта функция сейчас недоступна")
        return
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Эта функция недоступна — вы не состоите в паре")
        return
    await state.update_data(wish_id=None)
    await state.set_state(WishesStates.waiting_for_wish_content)
    await callback_edit_or_answer(callback,
        "💫 <b>Добавление желания</b>\n\n"
        "Опиши своё желание как угодно\n"
        "Можно использовать любой текст и форматирование\n\n",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("wish_view_"))
async def wish_view(callback: CallbackQuery):
    """Просмотр конкретного желания (для Ксюши и создателя)"""
    user_id = callback.from_user.id
    
    try:
        wish_id = int(callback.data.split("_")[2])
    except Exception:
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        await callback.answer(MSG_WISH_NOT_FOUND)
        return
    
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    text = format_wish_text(wish, user_id)
    
    back_target = "wishes_menu"
    keyboard = create_wish_keyboard(wish.id, user_id, back_target=back_target)
    
    await send_wish_with_media(
        chat_id=callback.message.chat.id,
        bot=callback.bot,
        wish=wish,
        text=text,
        keyboard=keyboard
    )


@router.callback_query(F.data.startswith("wish_status_") & ~F.data.startswith("wish_status_set_"))
async def wish_status_menu(callback: CallbackQuery):
    """Меню выбора статуса желания (для Ксюши и создателя)."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    try:
        wish_id = int(callback.data.replace("wish_status_", ""))
    except Exception:
        await callback.answer("Ошибка")
        return

    wish = db.get_wish(wish_id)
    if not wish:
        await callback.answer("Желание не найдено")
        return

    current = getattr(wish, "status", "created") or "created"

    def make_kb(cur_status):
        rows = []
        for key, label in WISH_STATUS_LABELS.items():
            check = "✅ " if key == cur_status else ""
            rows.append([InlineKeyboardButton(
                text=f"{check}{label}",
                callback_data=f"wish_status_set_{wish_id}_{key}"
            )])
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"wish_view_{wish_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    status_label = WISH_STATUS_LABELS.get(current, current)
    text = f"\U0001f3f7 <b>Статус желания #{wish_id}</b>\n\nТекущий: {status_label}\n\nВыбери новый:"
    try:
        await callback.message.edit_text(text, reply_markup=make_kb(current), parse_mode=ParseMode.HTML)
    except Exception:
        try:
            await callback_edit_or_answer(callback, text, reply_markup=make_kb(current), parse_mode=ParseMode.HTML)
        except Exception:
            pass
    await callback.answer()


@router.callback_query(F.data.startswith("wish_status_set_"))
async def wish_status_set(callback: CallbackQuery):
    """Устанавливает статус желания — только обновляет галочки в сообщении."""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return

    parts = callback.data.split("_")
    # wish_status_set_{wish_id}_{status}
    # parts: ['wish','status','set', wish_id, status]  — status может быть 'in_progress'
    try:
        wish_id = int(parts[3])
        new_status = "_".join(parts[4:])  # in_progress → parts[4]+'_'+parts[5]
    except Exception:
        await callback.answer("Ошибка")
        return

    if new_status not in ("created", "in_progress", "done"):
        await callback.answer("Неверный статус")
        return

    ok = db.update_wish_status(wish_id, new_status)
    if not ok:
        await callback.answer("Не удалось обновить")
        return

    try:
        from http_api import broadcast_wish_status
        asyncio.create_task(broadcast_wish_status(wish_id, new_status))
    except Exception as e:
        logger.error(f"Failed to broadcast wish status: {e}")

    # Обновляем только клавиатуру (галочку), текст не трогаем
    def make_kb(cur_status):
        rows = []
        for key, label in WISH_STATUS_LABELS.items():
            check = "✅ " if key == cur_status else ""
            rows.append([InlineKeyboardButton(
                text=f"{check}{label}",
                callback_data=f"wish_status_set_{wish_id}_{key}"
            )])
        rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data=f"wish_view_{wish_id}")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    new_label = WISH_STATUS_LABELS.get(new_status, new_status)
    try:
        await callback.message.edit_reply_markup(reply_markup=make_kb(new_status))
    except Exception:
        pass
    await callback.answer(f"\u2705 {new_label}")

@router.callback_query(F.data.startswith("wish_edit_"))
async def wish_edit(callback: CallbackQuery, state: FSMContext):
    """Редактирование желания — только автор"""
    user_id = callback.from_user.id
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    try:
        wish_id = int(callback.data.split("_")[2])
    except Exception:
        await callback.answer("Ошибка получения желания")
        return
    wish = db.get_wish(wish_id)
    if not wish:
        await callback.answer(MSG_WISH_NOT_FOUND)
        return
    if wish.user_id != user_id:
        await callback.answer("Можно редактировать только свои желания")
        return
    await state.update_data(wish_id=wish.id)
    await state.set_state(WishesStates.waiting_for_wish_content)
    await callback_edit_or_answer(callback,
        f"✏️ <b>Изменение желания #{wish.id}</b>\n\n"
        f"Текущее желание:\n{sanitize_html_for_telegram(wish.content or '')}\n\n"
        "Отправь новый вариант",
        parse_mode=ParseMode.HTML
    )

@router.callback_query(
    F.data.startswith("wish_delete_")
    & ~F.data.startswith("wish_delete_confirm_")
    & ~F.data.startswith("wish_delete_cancel_")
)
async def wish_delete(callback: CallbackQuery):
    """Запрос подтверждения удаления желания — доступно обоим участникам пары"""
    user_id = callback.from_user.id
    
    logger.info(f"wish_delete: user_id={user_id}, callback_data='{callback.data}'")
    
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    try:
        
        wish_id = int(callback.data.split("_")[-1])
    except Exception:
        logger.exception(
            f"wish_delete: не удалось распарсить wish_id из callback_data='{callback.data}'"
        )
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        logger.warning(f"wish_delete: желание id={wish_id} не найдено (user_id={user_id})")
        await callback.answer(MSG_WISH_NOT_FOUND)
        return

    if wish.user_id != user_id:
        await callback.answer("Можно удалять только свои желания")
        return

    logger.info(
        f"wish_delete: найдено желание id={wish.id}, wish_number={wish.wish_number} "
        f"(user_id={user_id})"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"wish_delete_confirm_{wish_id}"
        )],
        [InlineKeyboardButton(
            text="❌ Нет, оставить",
            callback_data=f"wish_delete_cancel_{wish_id}"
        )]
    ])
    await callback_edit_or_answer(callback,
        f"❓ <b>Удалить желание #{wish.id}?</b>\n\n"
        f"Текущее желание:\n{sanitize_html_for_telegram(wish.content or '')}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data.startswith("wish_delete_confirm_"))
async def wish_delete_confirm(callback: CallbackQuery):
    """Подтверждение удаления желания — доступно обоим участникам пары"""
    user_id = callback.from_user.id
    
    logger.info(f"wish_delete_confirm: user_id={user_id}, callback_data='{callback.data}'")
    
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    try:
        
        wish_id = int(callback.data.split("_")[-1])
    except Exception:
        logger.exception(
            f"wish_delete_confirm: не удалось распарсить wish_id из callback_data='{callback.data}'"
        )
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        logger.warning(f"wish_delete_confirm: желание id={wish_id} не найдено (user_id={user_id})")
        await callback.answer(MSG_WISH_NOT_FOUND)
        return

    if wish.user_id != user_id:
        await callback.answer("Можно удалять только свои желания")
        return
    
    # Удаляем желание
    logger.info(f"wish_delete_confirm: удаляем желание id={wish_id} (user_id={user_id})")
    delete_ok = db.delete_wish(wish_id)
    if not delete_ok:
        logger.error(f"wish_delete_confirm: db.delete_wish вернул False для id={wish_id}")
        await callback.answer("Ошибка при удалении желания")
        return
    
    await callback.answer("Желание удалено")
    
    try:
        partner_id = db.get_partner_id(user_id)
        if partner_id and db.are_notifications_enabled(partner_id) and db.is_category_notif_enabled(partner_id, "wishes"):
            user_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = (
                f"❌ {user_name} удалил(а) своё желание #{wish.id}.\n\n"
                f"💬 Текст был:\n{sanitize_html_for_telegram(wish.content or '')}"
            )
            await callback.bot.send_message(
                chat_id=partner_id,
                text=notify_text,
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(
            f"wish_delete_confirm: ошибка при отправке уведомления создателю об удалении "
            f"желания Ксюшей (wish_id={wish_id}): {e}"
        )
    
    deleted_own = (wish.user_id == user_id)
    if deleted_own:
        wishes = db.get_user_wishes(user_id)
        text_after = (
            "💫 <b>Мои желания</b>\n\n"
            "Желание удалено"
        )
        kb_after = create_wishes_menu_keyboard(wishes)
    else:
        partner_id_for_menu = wish.user_id
        wishes = db.get_user_wishes(partner_id_for_menu)
        partner_name = db.get_display_name(partner_id_for_menu) or "партнёра"
        text_after = f"🎁 <b>Желания {partner_name}</b>\n\nЖелание удалено"
        kb_after = create_wishes_menu_keyboard_partner(wishes)
    try:
        await callback.message.edit_text(text_after, reply_markup=kb_after, parse_mode=ParseMode.HTML)
    except Exception:
        await callback_edit_or_answer(callback, text_after, reply_markup=kb_after, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("wish_delete_cancel_"))
async def wish_delete_cancel(callback: CallbackQuery):
    """Отмена удаления желания — доступно обоим участникам пары"""
    user_id = callback.from_user.id
    
    logger.info(f"wish_delete_cancel: user_id={user_id}, callback_data='{callback.data}'")
    
    if not db.is_in_couple(user_id) and not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    try:
        wish_id = int(callback.data.split("_")[-1])
    except Exception:
        logger.exception(
            f"wish_delete_cancel: не удалось распарсить wish_id из callback_data='{callback.data}'"
        )
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        logger.warning(f"wish_delete_cancel: желание id={wish_id} не найдено (user_id={user_id})")
        await callback.answer(MSG_WISH_NOT_FOUND)
        return
    
    text = format_wish_text(wish, user_id)
    back_target = "wishes_menu"
    await callback_edit_or_answer(callback,
        text,
        reply_markup=create_wish_keyboard(wish.id, user_id, back_target=back_target),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(
    F.data.startswith("admin_wish_delete_")
    & ~F.data.startswith("admin_wish_delete_no_reason_")
)
async def admin_wish_delete(callback: CallbackQuery, state: FSMContext):
    """Инициирование удаления желания создателем с возможностью указать причину"""
    user_id = callback.from_user.id
    
    logger.info(f"admin_wish_delete: user_id={user_id}, callback_data='{callback.data}'")
    
    if not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    try:
        
        wish_id = int(callback.data.split("_")[-1])
    except Exception:
        logger.exception(
            f"admin_wish_delete: не удалось распарсить wish_id из callback_data='{callback.data}'"
        )
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        logger.warning(f"admin_wish_delete: желание id={wish_id} не найдено (user_id={user_id})")
        await callback.answer(MSG_WISH_NOT_FOUND)
        return
    
    await state.update_data(wish_id=wish.id)
    await state.set_state(AdminStates.waiting_for_wish_delete_reason)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Не указывать причину",
            callback_data=f"admin_wish_delete_no_reason_{wish_id}"
        )]
    ])
    
    await callback_edit_or_answer(callback, 
        f"🗑️ <b>Удаление желания #{wish.id}</b>\n\n"
        "Можешь по желанию написать причину удаления, она будет отправлена партнёру\n",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("admin_wish_delete_no_reason_"))
async def admin_wish_delete_no_reason(callback: CallbackQuery, state: FSMContext):
    """Удаление желания создателем без указания причины"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer("Доступ запрещён")
        return
    
    try:
        wish_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("Ошибка получения желания")
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        await callback.answer(MSG_WISH_NOT_FOUND)
        await state.clear()
        return
    
    db.delete_wish(wish_id)
    await callback.answer("Желание удалено")
    
    try:
        if db.are_notifications_enabled(wish.user_id) and db.is_category_notif_enabled(wish.user_id, "wishes"):
            deleter_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = (
                f"❌ <b>Твоё желание #{wish.id} было удалено ({deleter_name})</b>\n\n"
                "💬 Причина не указана"
            )
            await callback.bot.send_message(
                chat_id=wish.user_id,
                text=notify_text,
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления партнёру об удалении желания: {e}")
    
    await callback_edit_or_answer(callback, 
        "✅ Желание удалено",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )
    
    await state.clear()

@router.callback_query(F.data == "admin_wish_close")
async def admin_wish_close(callback: CallbackQuery):
    """Закрытие сообщения с желанием у создателя"""
    await safe_delete_callback_message(callback)

@router.callback_query(F.data == "broadcast_to_all")
async def broadcast_to_all(callback: CallbackQuery, state: FSMContext):
    """Выбор рассылки всем пользователям"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    await state.update_data(broadcast_target="all")
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await callback_edit_or_answer(callback, 
        "✉️ <b>Рассылка всем пользователям</b>\n\n"
        "Отправь сообщение, можно любой тип контента и любое форматирование",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="broadcast_menu"
            )]
        ]),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data == "broadcast_to_ksusha")
@router.callback_query(F.data == "broadcast_to_partner")
async def broadcast_to_ksusha(callback: CallbackQuery, state: FSMContext):
    """Выбор рассылки только партнёру"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    await state.update_data(broadcast_target="partner")
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await callback_edit_or_answer(callback, 
        "💖 <b>Рассылка только партнёру</b>\n\n"
        "Отправь сообщение, можно любой тип контента и любое форматирование",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🔙 Назад",
                callback_data="broadcast_menu"
            )]
        ]),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("admin_detail_"))
async def admin_detail(callback: CallbackQuery):
    """Детальная информация об администраторе"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    try:
        admin_id = int(callback.data.split("_")[2])
    except:
        await callback.answer("Ошибка получения ID администратора")
        return
    
    admins = db.get_all_admins()
    
    admin = None
    for a in admins:
        if a['user_id'] == admin_id:
            admin = a
            break
    
    if not admin:
        await callback.answer(MSG_ADMIN_NOT_FOUND)
        return
    
    admin_details = format_admin_details(admin)
    await callback_edit_or_answer(callback, 
        admin_details,
        reply_markup=create_admin_detail_keyboard(admin_id),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("admin_delete_confirm_"))
async def admin_delete_confirm(callback: CallbackQuery):
    """Подтверждение удаления администратора"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    try:
        admin_id = int(callback.data.split("_")[3])
    except:
        await callback.answer("Ошибка получения ID администратора")
        return
    
    if db.is_creator(admin_id) or db.is_in_couple(admin_id):
        await callback.answer("Этот администратор не может быть удалён")
        return
    
    admin = db.get_user(admin_id)
    if not admin:
        await callback.answer(MSG_ADMIN_NOT_FOUND)
        return
    
    display_name = admin.get('first_name') or admin.get('username') or f"ID {admin_id}"
    
    await callback_edit_or_answer(callback, 
        f"❓ <b>Ты уверен что хочешь удалить администратора?</b>\n\n"
        f"👤 <b>{display_name}</b>\n"
        f"🆔 ID: {admin_id}\n\n"
        "После этого пользователь потеряет доступ к функциям администратора",
        reply_markup=create_admin_delete_confirm_keyboard(admin_id),
        parse_mode=ParseMode.HTML
    )

@router.callback_query(F.data.startswith("admin_delete_yes_"))
async def admin_delete_yes(callback: CallbackQuery):
    """Удаление администратора"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    try:
        admin_id = int(callback.data.split("_")[3])
    except:
        await callback.answer("Ошибка получения ID администратора")
        return
    
    if db.is_creator(admin_id) or db.is_in_couple(admin_id):
        await callback.answer("Этот администратор не может быть удалён")
        return
    
    admin = db.get_user(admin_id)
    if not admin:
        await callback.answer(MSG_ADMIN_NOT_FOUND)
        return
    
    display_name = admin.get('first_name') or admin.get('username') or f"ID {admin_id}"
    
    # Удаляем админа
    success = db.remove_admin(admin_id)
    
    if success:
        await callback.answer(f"Администратор {display_name} удалён")
        
        admins = db.get_all_admins()
        await callback_edit_or_answer(callback, 
            "👥 <b>Выбери администратора:</b>",
            reply_markup=create_admin_list_keyboard(admins),
            parse_mode=ParseMode.HTML
        )
    else:
        await callback.answer("❌ Ошибка при удалении администратора")

@router.callback_query(F.data.startswith("admin_delete_no_"))
async def admin_delete_no(callback: CallbackQuery):
    """Отмена удаления администратора"""
    user_id = callback.from_user.id
    
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    
    try:
        admin_id = int(callback.data.split("_")[3])
    except:
        await callback.answer("Ошибка получения ID администратора")
        return
    
    admins = db.get_all_admins()
    
    admin = None
    for a in admins:
        if a['user_id'] == admin_id:
            admin = a
            break
    
    if not admin:
        await callback.answer(MSG_ADMIN_NOT_FOUND)
        return
    
    admin_details = format_admin_details(admin)
    await callback_edit_or_answer(callback, 
        admin_details,
        reply_markup=create_admin_detail_keyboard(admin_id),
        parse_mode=ParseMode.HTML
    )

@router.message(AdminStates.waiting_for_admin_input)
async def process_admin_input(message: Message, state: FSMContext):
    """Обработка ввода ID нового администратора (только числовой ID)."""
    try:
        if message.text and message.text.strip().lower() == '/cancel':
            await message.answer(
                "Добавление администратора отменено",
                reply_markup=create_admin_keyboard()
            )
            await state.clear()
            return

        input_text = (message.text or "").strip()
        if not input_text.isdigit():
            await message.answer(
                "❌ Нужен <b>числовой ID</b> (только цифры).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="list_admins")],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_add_cancel")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return

        user_id = int(input_text)
        await process_user_id(message, user_id, state)
    except Exception as e:
        logger.error(f"Ошибка при обработке админа: {e}")
        await message.answer(
            "❌ Ошибка. Отправь числовой ID.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Назад", callback_data="list_admins")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_add_cancel")]
            ]),
            parse_mode=ParseMode.HTML
        )
        await state.clear()


@router.message(AdminStates.waiting_for_broadcast_content)
async def process_broadcast_content(message: Message, state: FSMContext):
    """Получает сообщение для рассылки и рассылает его выбранным получателям"""
    user_id = message.from_user.id
    
    if not db.is_creator(user_id):
        await message.answer("Эта функция доступна только создателю.")
        await state.clear()
        return
    
    data = await state.get_data()
    target = data.get("broadcast_target", "all")
    
    if target in ("ksusha", "partner"):
        recipients = [db.get_ksusha_id()]
        target_text = "партнёру"
    else:
        all_ids = db.get_all_user_ids()
        recipients = list(set(all_ids))
        target_text = f"всем пользователям ({len(recipients)})"
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    sent = 0
    failed = 0
    
    for uid in recipients:
        try:
            await message.bot.copy_message(
                chat_id=uid,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
            sent += 1
        except Exception as e:
            logger.error(f"Ошибка при отправке рассылки пользователю {uid}: {e}")
            failed += 1
    
    await message.answer(
        f"✅ Рассылка завершена\n"
        f"Отправлено: {sent}\n"
        f"Не удалось отправить: {failed}\n"
        f"Цель: {target_text}",
        parse_mode=ParseMode.HTML
    )
    await message.answer(
        "⚙️ <b>Панель администратора</b>\n\n"
        "Выбери действие:",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )
    
    await state.clear()


@router.callback_query(F.data == "admin_delete_data")
async def admin_delete_data_menu(callback: CallbackQuery):
    """Меню удаления данных"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="👤 Только мои данные",
            callback_data="delete_data_confirm_mine"
        )],
        [InlineKeyboardButton(
            text="💑 Мои данные + данные партнёра",
            callback_data="delete_data_confirm_couple"
        )],
        [InlineKeyboardButton(
            text="☢️ Удалить АБСОЛЮТНО ВСЕ данные",
            callback_data="delete_data_confirm_all"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")],
    ])
    try:
        await callback.message.edit_text(
            "🗑️ <b>Удаление данных</b>\n\n"
            "⚠️ Это действие <b>необратимо</b>. Выбери, что удалить:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "🗑️ <b>Удаление данных</b>\n\n"
            "⚠️ Это действие <b>необратимо</b>. Выбери, что удалить:",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "delete_data_confirm_mine")
async def delete_data_confirm_mine(callback: CallbackQuery):
    """Подтверждение удаления только своих данных"""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить мои данные", callback_data="delete_data_exec_mine")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_data")],
    ])
    try:
        await callback.message.edit_text(
            "⚠️ <b>Подтверди удаление</b>\n\n"
            "Будут удалены <b>только твои</b> воспоминания, события, желания, настройки и токены.\n\n"
            "Данные партнёра и пар <b>не затрагиваются</b>. Продолжить?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "⚠️ <b>Подтверди удаление</b>\n\n"
            "Будут удалены <b>только твои</b> воспоминания, события, желания, настройки и токены.\n\n"
            "Данные партнёра и пар <b>не затрагиваются</b>. Продолжить?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "delete_data_confirm_couple")
async def delete_data_confirm_couple(callback: CallbackQuery):
    """Подтверждение удаления своих данных + данных партнёра"""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить данные пары", callback_data="delete_data_exec_couple")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_data")],
    ])
    try:
        await callback.message.edit_text(
            "⚠️ <b>Подтверди удаление</b>\n\n"
            "Будут удалены данные <b>обоих партнёров</b> и сама пара.\n"
            "Воспоминания, события, желания, настройки обоих.\n\n"
            "Это <b>необратимо</b>. Продолжить?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "⚠️ <b>Подтверди удаление</b>\n\n"
            "Будут удалены данные <b>обоих партнёров</b> и сама пара.\n"
            "Воспоминания, события, желания, настройки обоих.\n\n"
            "Это <b>необратимо</b>. Продолжить?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "delete_data_confirm_all")
async def delete_data_confirm_all(callback: CallbackQuery):
    """Подтверждение удаления абсолютно всех данных"""
    if not db.is_creator(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="☢️ ДА, УДАЛИТЬ ВСЁ", callback_data="delete_data_exec_all")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_delete_data")],
    ])
    try:
        await callback.message.edit_text(
            "☢️ <b>ВНИМАНИЕ! Удаление ВСЕХ данных</b>\n\n"
            "Будет удалено <b>абсолютно всё</b>: воспоминания, события, желания, "
            "пары, устройства, визиты на сайт, настройки всех пользователей.\n\n"
            "❌ Это НЕВОЗМОЖНО отменить. Ты уверен?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await callback_edit_or_answer(callback, 
            "☢️ <b>ВНИМАНИЕ! Удаление ВСЕХ данных</b>\n\n"
            "Будет удалено <b>абсолютно всё</b>: воспоминания, события, желания, "
            "пары, устройства, визиты на сайт, настройки всех пользователей.\n\n"
            "❌ Это НЕВОЗМОЖНО отменить. Ты уверен?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    await callback.answer()


@router.callback_query(F.data == "delete_data_exec_mine")
async def delete_data_exec_mine(callback: CallbackQuery):
    """Выполняет удаление только данных создателя"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback.answer("Удаляю…")
    db.delete_user_data(user_id)
    await callback_edit_or_answer(callback, 
        "✅ <b>Данные удалены</b>\n\nТвои воспоминания, события, желания и настройки удалены.",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "delete_data_exec_couple")
async def delete_data_exec_couple(callback: CallbackQuery):
    """Выполняет удаление данных обоих партнёров"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback.answer("Удаляю…")
    db.delete_couple_data(user_id)
    await callback_edit_or_answer(callback, 
        "✅ <b>Данные пары удалены</b>\n\nДанные обоих партнёров и информация о паре удалены.",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "delete_data_exec_all")
async def delete_data_exec_all(callback: CallbackQuery):
    """Выполняет удаление абсолютно всех данных"""
    user_id = callback.from_user.id
    if not db.is_creator(user_id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await callback.answer("Удаляю всё…")
    db.delete_all_data()
    await callback_edit_or_answer(callback, 
        "✅ <b>Все данные удалены</b>\n\nСистема очищена полностью.",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML,
    )


@router.message(AdminStates.waiting_for_wish_delete_reason)
async def process_wish_delete_reason(message: Message, state: FSMContext):
    """Обработка причины удаления желания создателем"""
    user_id = message.from_user.id
    
    if not db.is_creator(user_id):
        await message.answer("Эта функция доступна только создателю.")
        await state.clear()
        return
    
    data = await state.get_data()
    wish_id = data.get("wish_id")
    
    if not wish_id:
        await message.answer("Не удалось определить желание для удаления")
        await state.clear()
        return
    
    wish = db.get_wish(wish_id)
    if not wish:
        await message.answer("Желание уже было удалено")
        await state.clear()
        return
    
    reason_raw = (message.text or "").strip()
    reason_html = text_and_entities_to_html(message.text or "", message.entities or [])
    
    # Удаляем желание
    db.delete_wish(wish_id)
    
    # Уведомляем Ксюшу
    try:
        if db.are_notifications_enabled(wish.user_id) and db.is_category_notif_enabled(wish.user_id, "wishes"):
            deleter_name = db.get_display_name(user_id, fallback="Партнёр")
            notify_text = (
                f"❌ <b>Твоё желание #{wish.id} было удалено ({deleter_name})</b>\n\n"
            )
            if reason_raw:
                notify_text += f"💬 Причина: {reason_html}"
            else:
                notify_text += "💬 Причина не указана"
            
            await message.bot.send_message(
                chat_id=wish.user_id,
                text=notify_text,
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления партнёру об удалении желания: {e}")
    
    await message.answer(
        "✅ Желание удалено",
        reply_markup=create_admin_keyboard(),
        parse_mode=ParseMode.HTML
    )
    
    await state.clear()

@router.message(WishesStates.waiting_for_wish_content)
async def process_wish_content(message: Message, state: FSMContext):
    """Создание или изменение желания"""
    if await _is_duplicate_media_group(state, message, "_mg_seen_wish_content"):
        return
    user_id = message.from_user.id
    data = await state.get_data()
    
    wish_id = data.get("wish_id")
    
    raw = (message.text or message.caption or "").strip()
    media_type, media_file_id, media_path = await save_media(message)
    
    if not raw and not media_type:
        await message.answer(
            "Описание желания или прикреплённое медиа должны быть не пустыми. Попробуй ещё раз."
        )
        return
    
    is_valid, error_msg = validate_content(raw)
    if not is_valid:
        await message.answer(f"❌ {error_msg}\n\nПопробуй ещё раз:")
        return
    
    await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    use_caption = message.caption is not None
    content = text_and_entities_to_html(
        message.caption if use_caption else (message.text or ""),
        message.caption_entities if use_caption else (message.entities or [])
    )
    
    created_new = False
    
    wish_owner_id = message.from_user.id
    if wish_id:
        db.update_wish(
            wish_id,
            content,
            media_type=media_type,
            media_file_id=media_file_id,
            media_path=media_path
        )
        wish = db.get_wish(wish_id)
        created_new = False
    else:
        new_id = db.add_wish(
            wish_owner_id,
            content,
            media_type=media_type,
            media_file_id=media_file_id,
            media_path=media_path
        )
        if new_id == -1:
            await message.answer("❌ Ошибка при сохранении желания.")
            await state.clear()
            return
        wish = db.get_wish(new_id)
        created_new = True
    
    if not wish:
        await message.answer("Не удалось найти желание после сохранения")
        await state.clear()
        return
        
    await message.answer(
        f"✅ Желание сохранено!\n\n" + format_wish_text(wish, message.from_user.id),
        reply_markup=create_wish_user_keyboard(wish.id, message.from_user.id),
        parse_mode=ParseMode.HTML
    )
    
    try:
        notify_partner_id = db.get_partner_id(message.from_user.id)
        if notify_partner_id and db.are_notifications_enabled(notify_partner_id) and db.is_category_notif_enabled(notify_partner_id, "wishes"):
            actor_name = db.get_display_name(message.from_user.id, fallback="Партнёр")
            if created_new:
                header = f"✨ {actor_name} написал(а) новое желание!"
            else:
                header = f"✏️ {actor_name} изменил(а) желание"
            notify_text = header + "\n\n" + format_wish_text(wish, notify_partner_id)
            await message.bot.send_message(
                chat_id=notify_partner_id,
                text=notify_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="👀 Посмотреть желание",
                        callback_data=f"wish_view_{wish.id}"
                    )]
                ]),
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о желании партнёру: {e}")
    
    await state.clear()



async def process_user_id(message: Message, user_id: int, state: FSMContext):
    """Обработка user_id администратора. По ID запрашивает данные пользователя через get_chat (если он хоть раз писал боту)."""
    try:
        if user_id <= 0:
            await message.answer("ID должен быть положительным числом")
            return
        
        if user_id == message.from_user.id:
            await message.answer("Ты уже администратор!")
            await state.clear()
            return
        
        if db.is_in_couple(user_id) and user_id != message.from_user.id:
            await message.answer("Партнёр уже администратор!")
            await state.clear()
            return
        
        if db.is_admin(user_id):
            await message.answer(
                f"❌ Пользователь с ID {user_id} уже является администратором",
                reply_markup=create_admin_keyboard()
            )
            await state.clear()
            return
        
        username, first_name, last_name = None, None, None
        try:
            chat = await message.bot.get_chat(user_id)
            if chat.type == "private":
                username = chat.username
                first_name = chat.first_name
                last_name = chat.last_name
        except Exception as e:
            logger.debug(f"get_chat({user_id}) не удался (пользователь мог не писать боту): {e}")
        
        success = db.add_admin(
            user_id,
            message.from_user.id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        
        if success:
            try:
                await message.bot.send_message(
                    chat_id=user_id,
                    text="✅ Тебе выдан доступ к боту. Напиши /start",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"Не удалось отправить уведомление новому админу {user_id}: {e}")
            
            user_info = db.get_user(user_id)
            username = user_info.get('username', '') if user_info else ''
            first_name = user_info.get('first_name', '') if user_info else ''
            
            display_name = first_name or username or f"ID {user_id}"
            
            await message.answer(
                f"✅ <b>{display_name}</b> добавлен как администратор!\n"
                f"ID: {user_id}",
                reply_markup=create_admin_keyboard(),
                parse_mode=ParseMode.HTML
            )
        else:
            await message.answer(
                "❌ Не удалось добавить администратора",
                reply_markup=create_admin_keyboard()
            )
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении админа: {e}")
        await message.answer(
            "❌ Произошла ошибка при добавлении администратора",
            reply_markup=create_admin_keyboard()
        )
        await state.clear()

@router.callback_query(F.data == "joined_no_site")
async def joined_no_site(callback: CallbackQuery):
    """«Нет, не хочу» после присоединения к паре — показываем главное меню."""
    user_id = callback.from_user.id
    welcome_text = format_welcome_message(user_id)
    await callback.message.edit_text(
        welcome_text,
        reply_markup=create_main_keyboard(user_id),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(StateFilter(None))
async def unknown_message(message: Message, state: FSMContext):
    """Обработка неизвестных сообщений / зависших FSM-состояний"""
    user_id = message.from_user.id

    db.add_or_update_user(
        user_id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    # Пользователь в паре — показываем главное меню
    if db.is_in_couple(user_id):
        welcome_text = format_welcome_message(user_id)
        await message.answer(
            welcome_text,
            reply_markup=create_main_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )
        return

    # Пользователь прошёл онбординг, но партнёр ещё не добавлен
    if db.is_user_onboarded(user_id):
        await message.answer(
            "💓 Партнёр ещё не добавлен.\n\nНажми /start чтобы получить ссылку-приглашение.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Пользователь из другой пары / просто зашёл
    if db.is_admin(user_id):
        welcome_text = format_welcome_message(user_id)
        await message.answer(
            welcome_text,
            reply_markup=create_main_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer(get_text_for_other_users())


@router.callback_query(F.data == "site_visit_refresh")
async def site_visit_refresh(callback: CallbackQuery):
    """
    Кнопка «Обновить» в уведомлении о входе на сайт: подтягивает актуальные данные
    из БД (последний визит + устройство) и обновляет текст сообщения.
    """
    try:
        visit = db.get_last_site_visit_any()
        if not visit:
            await callback.answer("Нет данных о визитах.")
            return
        vid = visit.get("visitor_id")
        device = db.get_device_by_visitor_id(vid) if vid else None
        if not device:
            device = {}
        loc_parts = [device.get("country") or "", device.get("city") or ""]
        location_str = ", ".join(p for p in loc_parts if p).strip() or "—"
        try:
            pr = device.get("pixel_ratio")
            if pr is not None:
                pr = float(pr)
        except (TypeError, ValueError):
            pr = None
        sw = device.get("screen_w")
        sh = device.get("screen_h")
        if sw is not None:
            try:
                sw = int(sw)
            except (TypeError, ValueError):
                sw = None
        if sh is not None:
            try:
                sh = int(sh)
            except (TypeError, ValueError):
                sh = None
        screen_str = f"{sw}x{sh}" if (sw and sh) else None
        screen_real_w = int(sw * pr) if (sw is not None and pr is not None) else None
        screen_real_h = int(sh * pr) if (sh is not None and pr is not None) else None
        conn_label = (device.get("connection_type") or "").strip() or None
        theme = device.get("theme")
        data = {
            "prefix": "Вход",
            "timezone": device.get("timezone_id") or visit.get("timezone_id") or "—",
            "location_str": location_str,
            "isp_str": device.get("isp") or None,
            "coords_str": device.get("coords") or None,
            "ua_pretty": device.get("ua_pretty") or visit.get("ua_pretty") or "—",
            "lang_display": device.get("language") or None,
            "screen_str": screen_str,
            "pixel_ratio": pr,
            "screen_real_w": screen_real_w,
            "screen_real_h": screen_real_h,
            "battery_level": device.get("battery_level"),
            "battery_charging": device.get("battery_charging"),
            "conn_label": conn_label,
            "downlink": device.get("downlink_mbps"),
            "rtt_ms": device.get("rtt_ms"),
            "save_data": bool(device.get("save_data")),
            "real_ip": device.get("ip_webrtc") or None,
            "ip_server": device.get("ip_server") or visit.get("ip") or None,
            "referrer": device.get("referrer") or None,
            "theme": theme,
            "architecture": device.get("architecture") or None,
            "model": device.get("model") or None,
            "device_type": device.get("device_type") or None,
        }
        text = format_visit_telegram_message(data)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="site_visit_refresh")]
        ])
        try:
            await callback.message.edit_text(
                text,
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
            await callback.answer("Данные обновлены из БД.")
        except Exception:
            await callback.answer("Не удалось обновить сообщение.")
    except Exception as e:
        try:
            await callback.answer("Ошибка обновления.")
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# Timezone diff handlers
# Вызываются из уведомления «✅ Успешная регистрация через сайт» когда TZ разные
# ─────────────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "tz_diff_yes")
async def tz_diff_yes(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил, что часовые пояса верны."""
    await state.clear()
    try:
        await callback.message.edit_text(
            "✅ <b>Успешная регистрация через сайт</b>\n\n"
            "🌍 Часовые пояса записаны — буду учитывать разницу во времени.",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await callback.answer("Всё верно!")


@router.callback_query(F.data == "tz_diff_no")
async def tz_diff_no(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет поменять один из часовых поясов."""
    user_id = callback.from_user.id
    couple = db.get_couple_by_user(user_id)
    if not couple:
        await callback.answer("Пара не найдена")
        return

    u1 = couple.get("user1_id")
    u2 = couple.get("user2_id")

    # Текущий пользователь — «Мой», партнёр — по имени
    partner_id = u2 if user_id == u1 else u1
    partner_name = db.get_display_name(partner_id) if partner_id else "Партнёр"

    buttons_row = [
        InlineKeyboardButton(text="Мой", callback_data=f"tz_pick_{user_id}"),
    ]
    if partner_id:
        buttons_row.append(
            InlineKeyboardButton(text=partner_name, callback_data=f"tz_pick_{partner_id}")
        )
    skip_row = [InlineKeyboardButton(text="Пропустить", callback_data="tz_skip")]

    await callback.message.edit_text(
        "🌍 <b>Чей часовой пояс поменять?</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons_row, skip_row]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tz_pick_"))
async def tz_pick(callback: CallbackQuery, state: FSMContext):
    """Выбрали, для кого меняем TZ — переходим в FSM-ввод."""
    raw = callback.data.replace("tz_pick_", "")
    try:
        target_user_id = int(raw)
    except ValueError:
        await callback.answer("Ошибка")
        return

    user_id = callback.from_user.id
    allowed_ids = {user_id}
    partner_id = db.get_partner_id(user_id)
    if partner_id:
        allowed_ids.add(partner_id)
    if target_user_id not in allowed_ids:
        await callback.answer(MSG_ACCESS_DENIED)
        return

    target_name = db.get_display_name(target_user_id)
    await state.update_data(tz_target_user_id=target_user_id)
    await state.set_state(TimezoneStates.waiting_for_input)

    await callback.message.edit_text(
        f"✏️ Напиши часовой пояс для <b>{target_name}</b> — "
        f"можно на русском или английском:\n"
        f"Например: «Москва», «Владивосток», «New York», «Paris»",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="tz_skip")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "tz_skip")
async def tz_skip(callback: CallbackQuery, state: FSMContext):
    """Пропустить — закрываем сообщение без изменений."""
    await state.clear()
    await safe_delete_callback_message(callback)
    await callback.answer("Хорошо!")


@router.message(TimezoneStates.waiting_for_input)
async def tz_input_handler(message: Message, state: FSMContext):
    """Принимаем текст часового пояса и распознаём (локально + AI fallback)."""
    data = await state.get_data()
    if data.get("tz_mode") == "settings_time_city":
        user_id = message.from_user.id
        if not db.is_admin(user_id):
            await state.clear()
            return
        user_text = (message.text or "").strip()
        if not user_text:
            await message.answer("Напиши город или часовой пояс текстом.")
            return

        wait_msg = await message.answer("⏳ Определяю часовой пояс…")

        parsed = _parse_timezone_local_flexible(user_text)
        if not parsed:
            loop = asyncio.get_event_loop()
            parsed = await loop.run_in_executor(None, parse_timezone_with_ai_details, user_text)

        try:
            await wait_msg.delete()
        except Exception:
            pass

        tz_id = (parsed or {}).get("timezone")
        display_name = (parsed or {}).get("display_name") or user_text
        if not tz_id:
            await message.answer(
                "🤔 Не удалось определить часовой пояс.\n"
                "Попробуй снова: город, страна или IANA-формат (например Europe/Moscow).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад", callback_data="settings_time")]
                ]),
            )
            return

        await state.update_data(
            tz_mode="settings_time_city",
            tz_target_user_id=user_id,
            tz_candidate=tz_id,
            tz_candidate_display=display_name,
        )
        label = _format_tz_with_now(tz_id, display_name)
        await message.answer(
            "🕐 <b>Время в боте</b>\n\n"
            f"Твой часовой пояс: <i>{label}</i>\n\n"
            "Это верно?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да", callback_data="settings_time_city_confirm_yes"),
                    InlineKeyboardButton(text="✏️ Нет, ввести заново", callback_data="settings_time_city_confirm_no"),
                ]
            ]),
            parse_mode=ParseMode.HTML,
        )
        return

    target_user_id = data.get("tz_target_user_id")
    if not target_user_id:
        await state.clear()
        return

    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer(
            "Напиши название города или страны — например «Москва» или «New York»."
        )
        return

    wait_msg = await message.answer("⏳ Определяю часовой пояс…")

    loop = asyncio.get_event_loop()
    tz_result = await loop.run_in_executor(None, parse_timezone_with_ai, user_text)

    try:
        await wait_msg.delete()
    except Exception:
        pass

    if not tz_result:
        await message.answer(
            "🤔 Не удалось определить часовой пояс.\n"
            "Попробуй ещё раз — напиши название города или страны:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Пропустить", callback_data="tz_skip")]
            ]),
        )
        return

    # Показываем текущее время в этом TZ для подтверждения
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        now_str = _dt.now(ZoneInfo(tz_result)).strftime("%H:%M")
        time_hint = f" (сейчас там {now_str})"
    except Exception:
        time_hint = ""

    target_name = db.get_display_name(target_user_id)

    # Кодируем TZ для callback: / → __ (двойное подчёркивание)
    tz_encoded = tz_result.replace("/", "__")
    callback_ok = f"tz_ok_{target_user_id}_{tz_encoded}"
    callback_retry = f"tz_retry_{target_user_id}"

    await message.answer(
        f"🌍 Я определил часовой пояс для <b>{target_name}</b>: "
        f"<code>{tz_result}</code>{time_hint}\n\nПравильно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, верно", callback_data=callback_ok),
                InlineKeyboardButton(text="✏️ Нет, поменять", callback_data=callback_retry),
            ]
        ]),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("tz_ok_"))
async def tz_ok(callback: CallbackQuery, state: FSMContext):
    """Пользователь подтвердил распознанный TZ — сохраняем в БД."""
    # формат: tz_ok_{user_id}_{tz_encoded}
    raw = callback.data[6:]  # убираем "tz_ok_"
    parts = raw.split("_", 1)
    if len(parts) != 2:
        await callback.answer("Ошибка")
        return
    try:
        target_user_id = int(parts[0])
    except ValueError:
        await callback.answer("Ошибка")
        return

    user_id = callback.from_user.id
    allowed_ids = {user_id}
    partner_id = db.get_partner_id(user_id)
    if partner_id:
        allowed_ids.add(partner_id)
    if target_user_id not in allowed_ids:
        await callback.answer(MSG_ACCESS_DENIED)
        return

    tz_encoded = parts[1]
    tz = tz_encoded.replace("__", "/")

    try:
        db.set_user_setting(target_user_id, "timezone", tz)
    except Exception as e:
        await callback.answer(f"Ошибка сохранения: {e}")
        return

    await state.clear()
    changer_id = callback.from_user.id
    target_name = db.get_display_name(target_user_id)
    changer_name = db.get_display_name(changer_id)

    # Текущее время в новом часовом поясе для справки
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime as _dt
        now_str = _dt.now(ZoneInfo(tz)).strftime("%H:%M")
        time_hint = f" (сейчас там {now_str})"
    except Exception:
        time_hint = ""

    try:
        await callback.message.edit_text(
            f"✅ Часовой пояс <b>{target_name}</b> обновлён: <code>{tz}</code>{time_hint}",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await callback.answer("Сохранено!")

    # Уведомляем второго партнёра об изменении
    try:
        recipient_id = db.get_partner_id(changer_id)
        if recipient_id:
            possessive = "свой" if target_user_id == changer_id else "твой"
            notify_text = (
                f"✅ <b>{changer_name}</b> изменил {possessive} часовой пояс "
                f"на <code>{tz}</code>{time_hint}"
            )
            await callback.bot.send_message(
                chat_id=recipient_id,
                text=notify_text,
                parse_mode=ParseMode.HTML,
                force_new_message=True,
            )
    except Exception:
        pass


@router.callback_query(F.data.startswith("tz_retry_"))
async def tz_retry(callback: CallbackQuery, state: FSMContext):
    """Пользователь хочет ввести TZ повторно."""
    raw = callback.data.replace("tz_retry_", "")
    try:
        target_user_id = int(raw)
    except ValueError:
        await callback.answer("Ошибка")
        return

    user_id = callback.from_user.id
    allowed_ids = {user_id}
    partner_id = db.get_partner_id(user_id)
    if partner_id:
        allowed_ids.add(partner_id)
    if target_user_id not in allowed_ids:
        await callback.answer(MSG_ACCESS_DENIED)
        return

    target_name = db.get_display_name(target_user_id)
    await state.update_data(tz_target_user_id=target_user_id)
    await state.set_state(TimezoneStates.waiting_for_input)

    await callback.message.edit_text(
        f"✏️ Попробуй ещё раз — напиши часовой пояс для <b>{target_name}</b>:\n"
        f"Например: «Москва», «Владивосток», «New York», «Paris»",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пропустить", callback_data="tz_skip")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


# ── Добавление пользовательской категории ────────────────────────────────────

@router.callback_query(F.data == "add_category")
async def add_category_start(callback: CallbackQuery, state: FSMContext):
    """Начало создания пользовательской категории."""
    if not db.is_admin(callback.from_user.id):
        await callback.answer(MSG_ACCESS_DENIED)
        return
    await state.set_state(AddCategoryStates.waiting_for_name)
    await callback.message.edit_text(
        "➕ <b>Новая категория</b>\n\n"
        "Введи название категории:\n"
        "<i>Например: Наши путешествия, Смешные моменты, Любимые места</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category")]
        ]),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_add_category")
async def cancel_add_category(callback: CallbackQuery, state: FSMContext):
    """Отмена создания пользовательской категории."""
    await state.clear()
    await callback.message.edit_text(
        "📁 <b>Категории</b>\n\nВыбери раздел:",
        reply_markup=create_categories_keyboard(user_id=callback.from_user.id),
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()


@router.message(AddCategoryStates.waiting_for_name)
async def add_category_name(message: Message, state: FSMContext):
    """Получаем название новой пользовательской категории."""
    name = (message.text or "").strip()
    if not name:
        await message.answer(
            "❌ Название не может быть пустым. Попробуй ещё раз:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category")]
            ]),
        )
        return
    if len(name) > 64:
        await message.answer(
            "❌ Название слишком длинное (максимум 64 символа). Попробуй покороче:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category")]
            ]),
        )
        return
    await state.update_data(category_name=name)
    await state.set_state(AddCategoryStates.waiting_for_description)
    await message.answer(
        f"✅ Название: <b>{name}</b>\n\n"
        "Теперь добавь описание категории (или нажми «Пропустить»):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_category_description")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category")],
        ]),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data == "skip_category_description",
                       StateFilter(AddCategoryStates.waiting_for_description))
async def skip_category_description(callback: CallbackQuery, state: FSMContext):
    """Пользователь нажал «Пропустить» при вводе описания категории."""
    await _finish_add_category(callback.message, callback.from_user.id, state, description=None)
    await callback.answer()


@router.message(AddCategoryStates.waiting_for_description)
async def add_category_description(message: Message, state: FSMContext):
    """Получаем описание новой пользовательской категории."""
    description = (message.text or "").strip() or None
    if description and len(description) > 256:
        await message.answer(
            "❌ Описание слишком длинное (максимум 256 символов). Попробуй покороче:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_category_description")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add_category")],
            ]),
        )
        return
    await _finish_add_category(message, message.from_user.id, state, description=description)


_CATEGORY_COLORS = [
    "crimson", "coral", "tomato", "orangered", "darkorange", "goldenrod",
    "olive", "teal", "steelblue", "royalblue", "slateblue", "mediumpurple",
    "darkorchid", "mediumvioletred", "hotpink", "deeppink", "mediumseagreen",
    "seagreen", "cadetblue", "indianred", "chocolate", "sienna", "darkslateblue",
    "mediumslateblue", "dodgerblue", "darkcyan", "darkmagenta", "firebrick",
]

def _extract_emoji(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "📁"
    import re
    # Поиск стандартных эмодзи в диапазонах Юникода
    match = re.search(r'[\U0001f300-\U0001f9ff\U0001fa00-\U0001faff\u2600-\u27bf]', text)
    if match:
        idx = match.start()
        emoji_seq = text[idx:idx+4]
        res = ""
        for char in emoji_seq:
            cp = ord(char)
            if (0x1F300 <= cp <= 0x1F9FF) or (0x1F600 <= cp <= 0x1F64F) or \
               (0x1F680 <= cp <= 0x1F6FF) or (0x2600 <= cp <= 0x27BF) or \
               (0x1FA70 <= cp <= 0x1FAFF) or (0x1F000 <= cp <= 0x1F0FF) or \
               (cp == 0x200D) or (cp == 0xFE0F) or (0x1F3FB <= cp <= 0x1F3FF):
                res += char
            else:
                break
        if res:
            return res
    for char in text:
        if not char.isalnum() and char not in " \t\n\r.,!?;:\"'-()[]{}<>":
            return char
    return "📁"

async def _finish_add_category(
    message_or_msg,
    user_id: int,
    state: FSMContext,
    description: Optional[str],
):
    """Финализирует создание пользовательской категории и возвращает в меню."""
    import random as _random
    data = await state.get_data()
    name = data.get("category_name", "Без названия")
    await state.clear()

    # Генерация эмодзи с помощью ИИ
    emoji = "📁"
    try:
        from api import send_prompt
        prompt = (
            f"You are a helpful assistant. You must return EXACTLY ONE emoji that best represents "
            f"the following category title: \"{name}\". Do not output any other characters, text, "
            f"explanation, or whitespace. Just one single emoji."
        )
        ai_res = send_prompt(prompt, timeout_seconds=10)
        emoji = _extract_emoji(ai_res)
    except Exception as e:
        logger.exception(f"Ошибка при автоматической генерации эмодзи: {e}")

    couple = db.get_couple_by_user(user_id)
    couple_id = couple['id'] if couple else None
    color = _random.choice(_CATEGORY_COLORS)
    cat_id = db.create_custom_category(couple_id=couple_id, name=name, description=description, color=color, emoji=emoji)
    if cat_id == -1:
        await message_or_msg.answer("❌ Не удалось создать категорию. Попробуй позже.")
        return

    desc_line = f"\n<i>{description}</i>" if description else ""
    await message_or_msg.answer(
        f"✅ <b>Категория создана!</b>\n\n"
        f"{emoji} <b>{name}</b>{desc_line}\n\n"
        "Теперь ты можешь добавлять в неё воспоминания.",
        reply_markup=create_categories_keyboard(user_id=user_id),
        parse_mode=ParseMode.HTML,
    )

@router.callback_query(F.data.startswith("unlink_deny:"))
async def unlink_deny(callback: CallbackQuery):
    token = callback.data.split(":")[1]
    req = db.get_unlink_request(token)
    if not req or req["status"] not in ("pending", "awaiting_final"):
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return

    user_id = req["user_id"]
    db.set_unlink_status(token, "denied")
    visitor_base = f"{user_id}_"
    db.revoke_all_user_sessions(user_id, visitor_base)
    db.invalidate_transfer_invites_for_user(user_id)
    db.log_security_event('unlink_denied', user_id, req['ip'], req['ua'], req['country'], req['city'], f'{{"token": "{token}"}}')
    
    try:
        from http_api import _notify_unlink_ws, _notify_force_logout
        await _notify_unlink_ws(token, {"status": "denied"})
        await _notify_force_logout(user_id)
    except Exception:
        pass

    text = (
        f"🚨 <b>Доступ к отвязке отменён</b>\n\n"
        f"Зафиксированы данные попытки:\n"
        f"Устройство: {req['ua']}\n"
        f"IP: {req['ip']} ({req['country']}, {req['city']})\n\n"
        f"Все твои текущие сессии на сайте были завершены.\n"
        f"Если ты подозреваешь компрометацию аккаунта, рекомендуется заново войти в систему."
    )
    
    site_url = (getattr(config, "BOT_SITE_URL", "") or getattr(config, "SITE_DIRECT_URL", "")).strip().rstrip("/")
    _recovery_couple = db.get_couple_by_user(user_id)
    _recovery_role = "creator" if (_recovery_couple and _recovery_couple.get("user1_id") == user_id) else "partner"
    login_token = db.issue_user_login_token(user_id, role=_recovery_role)
    login_link = f"{site_url}?token={login_token}" if site_url else ""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Безопасный вход", url=login_link)]
    ]) if login_link else None

    text += "\n\n<i>Не передавай эту ссылку другим людям. Она предназначена только для восстановления доступа к твоему аккаунту.</i>"
    
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("unlink_confirm:"))
async def unlink_confirm(callback: CallbackQuery):
    token = callback.data.split(":")[1]
    req = db.get_unlink_request(token)
    if not req or req["status"] != "pending":
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return

    db.set_unlink_status(token, "awaiting_final")
    try:
        from http_api import _notify_unlink_ws
        await _notify_unlink_ws(token, {"status": "awaiting_final"})
    except Exception:
        pass

    text = (
        f"⚠️ <b>Внимание: Необратимое действие</b>\n\n"
        f"Ты действительно хочешь отвязать этот Telegram-аккаунт?\n"
        f"После отвязки ты потеряешь доступ к сайту с этого аккаунта.\n"
        f"Все твои данные в паре останутся, но для входа потребуется новый аккаунт."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтверждаю отвязку", callback_data=f"unlink_final:{token}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"unlink_cancel:{token}")]
    ])
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("unlink_cancel:"))
async def unlink_cancel(callback: CallbackQuery):
    token = callback.data.split(":")[1]
    req = db.get_unlink_request(token)
    if not req or req["status"] not in ("pending", "awaiting_final"):
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return

    db.set_unlink_status(token, "cancelled")
    
    try:
        from http_api import _notify_unlink_ws
        await _notify_unlink_ws(token, {"status": "cancelled"})
    except Exception:
        pass

    text = "ℹ️ <b>Отвязка отменена</b>\n\nТекущий Telegram-аккаунт остаётся привязанным к сайту."
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML)
    await callback.answer()

@router.callback_query(F.data.startswith("unlink_final:"))
async def unlink_final(callback: CallbackQuery):
    token = callback.data.split(":")[1]
    req = db.get_unlink_request(token)
    if not req or req["status"] != "awaiting_final":
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return

    user_id = req["user_id"]
    # Слот в couples НЕ освобождаем здесь — пользователь остаётся в паре
    # и может читать свои данные до тех пор, пока новый аккаунт не примет инвайт.
    # unlink_user_from_couple вызывается позже, в /start invite_<code> (transfer-ветка).
    invite_code = db.create_transfer_invite(req["couple_id"], user_id, pre_bound_user_id=None)
    db.set_unlink_status(token, "confirmed", invite_code)

    bot_username = getattr(config, "BOT_USERNAME", "")
    bot_link = f"https://t.me/{bot_username}?start=invite_{invite_code}" if bot_username else f"https://t.me/bot?start=invite_{invite_code}"
    
    try:
        from http_api import _notify_unlink_ws
        await _notify_unlink_ws(token, {"status": "confirmed", "bot_link": bot_link})
    except Exception:
        pass

    db.log_security_event('unlink_confirmed', user_id, req['ip'], req['ua'], req['country'], req['city'], f'{{"token": "{token}"}}')

    text = (
        f"✅ <b>Аккаунт успешно отвязан</b>\n\n"
        f"Твой партнёр и ваши общие данные в безопасности.\n\n"
        f"Чтобы привязать новый Telegram-аккаунт, используй эту ссылку:"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Ссылка для нового аккаунта", url=bot_link)]
    ])
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data == "rebound_create_new")
async def rebound_create_new(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    db.clear_rebound_status(user_id)
    
    first_name = callback.from_user.first_name or "друг"
    skip_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пропустить", callback_data="onboarding_skip_name")]
    ])
    
    try:
        db.add_or_update_user(
            user_id,
            callback.from_user.username,
            callback.from_user.first_name,
            callback.from_user.last_name,
        )
        await state.update_data(creating_couple=True)
        await state.set_state(CoupleOnboardingStates.waiting_for_name)
    except Exception as e:
        logger.error("Error clearing rebound and starting onboarding: %s", e)
        
    await callback.message.edit_text(
        f"👋 Привет, <b>{first_name}</b>!\n\n"
        "Создай пару с своим партнёром и вместе создавайте, изменяйте и делитесь моментами!\n\n"
        "Для начала — как тебя зовут?",
        reply_markup=skip_kb,
        parse_mode=ParseMode.HTML,
    )
    await callback.answer()
