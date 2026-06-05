"""
avatar_service.py — единый сервис Telegram-аватарок.

Хранение:
  media/tg_avatars/tg_avatar_{user_id}.jpg  — файл на диске
  users.tg_avatar_path TEXT                  — путь к файлу
  users.tg_avatar_file_id TEXT               — file_unique_id для детектирования изменений
  users.tg_avatar_updated REAL               — unix timestamp последнего обновления

Обновление:
  1. При /start — немедленно для конкретного пользователя (cooldown 6 ч)
  2. Фоновая задача — все пользователи раз в 12 ч (запускается из bot.py on_startup)
  3. Принудительно — POST /api/refresh_tg_avatar

Логика изменения аватарки:
  Сравниваем file_unique_id. Если не изменился — файл не скачиваем.
  Если фото удалено из Telegram — удаляем локальный файл и обнуляем в БД.
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

_AVATAR_DIR_NAME = "tg_avatars"
_SINGLE_USER_COOLDOWN = 6 * 3600       # 6 ч между обновлениями одного пользователя
_BATCH_INTERVAL = 12 * 3600            # 12 ч между полными проходами по всем пользователям
_BETWEEN_USERS_DELAY = 1.5             # пауза между пользователями в пакетном режиме (сек)
_MAX_RETRIES = 2                       # повторных попыток при ошибке загрузки


def _avatar_dir() -> Path:
    """Возвращает абсолютный путь к папке аватарок, создаёт если нет."""
    # Используем __file__ чтобы путь был корректным независимо от CWD
    project_root = Path(__file__).resolve().parent
    media_folder = os.environ.get("MEDIA_FOLDER", "media") if not config.MEDIA_FOLDER else config.MEDIA_FOLDER
    # Если MEDIA_FOLDER абсолютный — используем его, иначе относительно project_root
    mf = Path(media_folder)
    if not mf.is_absolute():
        mf = project_root / mf
    d = mf / _AVATAR_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _avatar_filename(user_id: int) -> str:
    return f"tg_avatar_{user_id}.jpg"


def get_avatar_local_path(user_id: int) -> Optional[str]:
    """Путь к локальному файлу или None."""
    p = _avatar_dir() / _avatar_filename(user_id)
    return str(p) if p.exists() else None


def get_avatar_url(user_id: int) -> Optional[str]:
    """URL аватарки для HTTP-ответа или None."""
    if not user_id:
        return None
    p = _avatar_dir() / _avatar_filename(user_id)
    if p.exists():
        return f"/media/tg_avatars/{_avatar_filename(user_id)}"
    return None


async def _download_file(url: str) -> Optional[bytes]:
    """Скачивает файл по URL, возвращает байты или None."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        return await resp.read()
                    logger.warning("avatar: HTTP %s for %s (attempt %d)", resp.status, url, attempt + 1)
        except asyncio.TimeoutError:
            logger.warning("avatar: timeout downloading %s (attempt %d)", url, attempt + 1)
        except Exception as e:
            logger.warning("avatar: error downloading %s: %s (attempt %d)", url, e, attempt + 1)
        if attempt < _MAX_RETRIES:
            await asyncio.sleep(2 ** attempt)
    return None


async def fetch_and_save_avatar(bot, user_id: int) -> tuple[Optional[str], Optional[str]]:
    """
    Скачивает аватарку пользователя и сохраняет на диск.

    Возвращает (local_path, file_unique_id) или (None, None).
    """
    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
        if not photos or not photos.photos:
            logger.debug("avatar: user %s has no profile photos", user_id)
            return None, None

        photo_sizes = photos.photos[0]
        # Берём самый большой размер по file_size, fallback по width
        best = max(photo_sizes, key=lambda p: (p.file_size or 0, p.width or 0))
        file_unique_id = best.file_unique_id

        tg_file = await bot.get_file(best.file_id)
        if not tg_file or not tg_file.file_path:
            logger.warning("avatar: get_file returned empty path for user %s", user_id)
            return None, None

        url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{tg_file.file_path}"
        data = await _download_file(url)
        if not data:
            return None, None

        save_path = _avatar_dir() / _avatar_filename(user_id)
        save_path.write_bytes(data)

        logger.info("avatar: saved for user %s → %s (%d bytes)", user_id, save_path, len(data))
        return str(save_path), file_unique_id

    except Exception:
        logger.exception("avatar: error fetching for user %s", user_id)
        return None, None


async def sync_user_avatar(bot, user_id: int, force: bool = False) -> Optional[str]:
    """
    Синхронизирует аватарку пользователя.

    - Проверяет cooldown (пропускает если обновляли недавно, если не force)
    - Сравнивает file_unique_id — скачивает только если аватарка изменилась
    - Обновляет БД
    - Возвращает URL или None
    """
    from database import db

    user = db.get_user(user_id)
    if not user:
        return None

    now = time.time()
    last_updated = float(user.get("tg_avatar_updated") or 0)
    stored_file_id = user.get("tg_avatar_file_id") or ""

    if not force and (now - last_updated) < _SINGLE_USER_COOLDOWN:
        return get_avatar_url(user_id)

    try:
        photos = await bot.get_user_profile_photos(user_id=user_id, limit=1)
    except Exception as e:
        logger.warning("avatar: cannot get photos for user %s: %s", user_id, e)
        return get_avatar_url(user_id)

    # Пользователь убрал аватарку
    if not photos or not photos.photos:
        local = get_avatar_local_path(user_id)
        if local:
            try:
                Path(local).unlink(missing_ok=True)
            except Exception:
                pass
        db.update_user_avatar(user_id, None, None)
        return None

    photo_sizes = photos.photos[0]
    best = max(photo_sizes, key=lambda p: (p.file_size or 0, p.width or 0))
    current_file_id = best.file_unique_id

    # Аватарка не изменилась и файл существует
    if current_file_id == stored_file_id and get_avatar_local_path(user_id):
        db.update_user_avatar(user_id, get_avatar_local_path(user_id), stored_file_id)
        return get_avatar_url(user_id)

    # Скачиваем новую версию
    tg_file = await bot.get_file(best.file_id)
    if not tg_file or not tg_file.file_path:
        return get_avatar_url(user_id)

    url = f"https://api.telegram.org/file/bot{config.BOT_TOKEN}/{tg_file.file_path}"
    data = await _download_file(url)
    if not data:
        return get_avatar_url(user_id)

    save_path = _avatar_dir() / _avatar_filename(user_id)
    save_path.write_bytes(data)

    db.update_user_avatar(user_id, str(save_path), current_file_id)
    logger.info(
        "avatar: updated user %s (file_id %s → %s, %d bytes)",
        user_id, stored_file_id or "none", current_file_id, len(data),
    )
    return get_avatar_url(user_id)


async def run_background_sync(bot) -> None:
    """
    Фоновая задача: синхронизирует аватарки всех пользователей раз в 12 ч.

    Запускается из bot.py on_startup как asyncio.create_task.
    При первом запуске немедленно выполняет миграцию (пользователи без аватарок).
    """
    from database import db

    # Небольшая задержка чтобы бот полностью стартовал
    await asyncio.sleep(10)
    logger.info("avatar: background sync started")

    first_run = True
    while True:
        try:
            user_ids = db.get_all_user_ids()
            logger.info("avatar: starting sync for %d users (first_run=%s)", len(user_ids), first_run)

            synced = 0
            skipped = 0
            errors = 0

            for uid in user_ids:
                try:
                    # На первом запуске — принудительно для пользователей без аватарок
                    user = db.get_user(uid)
                    has_avatar = user and get_avatar_local_path(uid)
                    needs_sync = first_run and not has_avatar

                    await sync_user_avatar(bot, uid, force=needs_sync)
                    synced += 1
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning("avatar: error syncing user %s: %s", uid, e)
                    errors += 1

                # Пауза между пользователями — не флудим Telegram API
                await asyncio.sleep(_BETWEEN_USERS_DELAY)

            logger.info(
                "avatar: batch sync done — synced=%d skipped=%d errors=%d",
                synced, skipped, errors,
            )
            first_run = False

        except asyncio.CancelledError:
            logger.info("avatar: background sync cancelled")
            break
        except Exception as e:
            logger.error("avatar: background sync error: %s", e)

        await asyncio.sleep(_BATCH_INTERVAL)


