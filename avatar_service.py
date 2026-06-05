"""
avatar_service.py — единый сервис для Telegram-аватарок.

Получает, сохраняет и обновляет аватарки пользователей через Bot API.
Используется и в боте (handlers.py), и в API (http_api.py).

Хранение:
  media/tg_avatars/tg_avatar_{user_id}.jpg

БД:
  users.tg_avatar_path     — путь к локальному файлу
  users.tg_avatar_updated  — UTC timestamp последнего обновления

Обновление:
  - при первом входе / регистрации пользователя в боте;
  - при каждом /start (не чаще раза в 24 ч через rate-limit в сервисе);
  - при явном запросе через API (refresh).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Optional

import aiohttp

from config import config

logger = logging.getLogger(__name__)

# Папка для хранения аватарок (внутри MEDIA_FOLDER)
_AVATAR_DIR_NAME = "tg_avatars"
# Минимальный интервал между обновлениями одного пользователя (секунды)
_REFRESH_COOLDOWN = 24 * 3600  # 24 ч


def _avatar_dir() -> Path:
    base = Path(config.MEDIA_FOLDER).resolve()
    d = base / _AVATAR_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _avatar_filename(user_id: int) -> str:
    return f"tg_avatar_{user_id}.jpg"


def get_avatar_local_path(user_id: int) -> Optional[str]:
    """Возвращает путь к локальному файлу аватарки или None если файла нет."""
    p = _avatar_dir() / _avatar_filename(user_id)
    return str(p) if p.exists() else None


def get_avatar_url(user_id: int) -> Optional[str]:
    """Возвращает URL аватарки для отдачи через HTTP API, или None."""
    p = _avatar_dir() / _avatar_filename(user_id)
    if p.exists():
        return f"/media/tg_avatars/{_avatar_filename(user_id)}"
    return None


async def fetch_and_save_avatar(bot, user_id: int) -> Optional[str]:
    """
    Получает аватарку пользователя через Telegram Bot API и сохраняет локально.

    Возвращает локальный путь к файлу или None при ошибке / отсутствии фото.
    """
    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
        if not photos or not photos.photos:
            logger.debug("avatar_service: user %s has no profile photos", user_id)
            return None

        # Берём самый большой размер последней (актуальной) аватарки
        photo_sizes = photos.photos[0]
        best = max(photo_sizes, key=lambda p: p.file_size or 0)

        tg_file = await bot.get_file(best.file_id)
        if not tg_file or not tg_file.file_path:
            return None

        # Скачиваем через Bot API
        token = config.BOT_TOKEN
        url = f"https://api.telegram.org/file/bot{token}/{tg_file.file_path}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(
                        "avatar_service: HTTP %s when downloading avatar for user %s",
                        resp.status, user_id,
                    )
                    return None
                data = await resp.read()

        if not data:
            return None

        save_path = _avatar_dir() / _avatar_filename(user_id)
        with open(save_path, "wb") as f:
            f.write(data)

        logger.info(
            "avatar_service: saved avatar for user %s → %s (%d bytes)",
            user_id, save_path, len(data),
        )
        return str(save_path)

    except Exception:
        logger.exception("avatar_service: error fetching avatar for user %s", user_id)
        return None


async def refresh_avatar_if_needed(bot, user_id: int, force: bool = False) -> Optional[str]:
    """
    Обновляет аватарку пользователя, если прошло достаточно времени с прошлого обновления.

    force=True — принудительно обновить независимо от cooldown.
    Возвращает URL аватарки (или None).
    """
    from database import db  # локальный импорт чтобы избежать circular import

    user = db.get_user(user_id)
    if not user:
        return None

    last_updated = user.get("tg_avatar_updated") or 0
    now = time.time()

    # Если last_updated хранится как строка ISO — конвертируем
    if isinstance(last_updated, str) and last_updated:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
            last_updated = dt.timestamp()
        except Exception:
            last_updated = 0

    if not force and (now - float(last_updated or 0)) < _REFRESH_COOLDOWN:
        # Ещё рано обновлять — вернём текущий URL если файл есть
        return get_avatar_url(user_id)

    # Скачиваем
    local_path = await fetch_and_save_avatar(bot, user_id)

    # Обновляем БД — путь и timestamp
    db.update_user_avatar(user_id, local_path)

    return get_avatar_url(user_id) if local_path else None


def serve_avatar_headers() -> dict:
    """HTTP-заголовки для отдачи аватарки."""
    return {
        "Cache-Control": "public, max-age=3600, stale-while-revalidate=86400",
        "Content-Type": "image/jpeg",
    }
