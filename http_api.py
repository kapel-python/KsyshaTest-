import asyncio
import math
import hmac
import hashlib
import base64
import secrets

from typing import Any, Optional

import aiohttp

from aiohttp import web

from pathlib import Path

from datetime import datetime, timezone, timedelta

from zoneinfo import ZoneInfo

import calendar

import json

import re

import logging

import traceback

import time

from collections import defaultdict, deque

import html as _html_module

try:
    from PIL import Image as _PILImage
except ImportError:
    _PILImage = None

import aiohttp

from aiogram import Bot

from aiogram.enums import ParseMode

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config import config

from database import db, Memory, Wish, ScheduledEvent

from app_version import get_version_metadata

from utils import (

    substitute_params,

    format_datetime_for_user,

    format_scheduled_event_datetime_for_timezone,

    is_scheduled_event_moment_passed,

    format_visit_telegram_message,

)

from utils import _event_datetime_to_utc

logger = logging.getLogger(__name__)


def _safe_int(value, default: Optional[int] = None) -> Optional[int]:
    """Безопасно конвертирует value в int, возвращает default при ошибке."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _pstr(val) -> str:
    """Возвращает val если это str, иначе '' — защита от list/dict/None в payload."""
    return val if isinstance(val, str) else ""


def _parse_page_limit(request: web.Request, default_limit: int = 30, max_limit: int = 100) -> tuple[int, int]:
    page = max(1, _safe_int(request.rel_url.query.get("page"), 1) or 1)
    limit = max(1, min(max_limit, _safe_int(request.rel_url.query.get("limit"), default_limit) or default_limit))
    return page, limit


def _build_weak_etag(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return f'W/"{hashlib.sha256(raw).hexdigest()[:24]}"'


def _json_response_with_etag(request: web.Request, payload: Any, status: int = 200) -> web.Response:
    etag = _build_weak_etag(payload)
    inm = (request.headers.get("If-None-Match") or "").strip()
    if inm and inm == etag:
        not_modified = web.Response(status=304)
        not_modified.headers["ETag"] = etag
        not_modified.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"
        not_modified.headers["Vary"] = "Cookie, X-Visitor-Id, X-Visitor-Signature, X-Api-Key"
        return _add_cors_headers(not_modified)
    resp = web.json_response(payload, status=status)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "private, no-cache, max-age=0, must-revalidate"
    resp.headers["Vary"] = "Cookie, X-Visitor-Id, X-Visitor-Signature, X-Api-Key"
    return _add_cors_headers(resp)

def _is_low_signal_ai_message(message: str) -> bool:
    """Короткий/шумный запрос, который лучше не отправлять в LLM."""
    text = (message or "").strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    # 1 символ (например, "р" или ".") и почти пустые шумовые токены.
    if len(compact) <= 1:
        return True
    meaningful = re.sub(r"[^0-9A-Za-zА-Яа-яЁё]", "", compact)
    if len(meaningful) <= 1:
        return True
    return False


def _low_signal_ai_reply() -> str:
    return "Слишком короткий запрос. Напиши чуть подробнее, например: «покажи мои ближайшие события»."


def _format_ago_ru(created_at_str: str) -> str:

    """Форматирует дату создания в «N мин назад» и т.п. (UTC)."""

    if not (created_at_str or "").strip():

        return ""

    s = (created_at_str or "").strip()

    dt = None

    try:

        # "2026-03-01 12:00:00" or "2026-03-01T12:00:00"

        s_norm = s.replace("T", " ")[:19]

        if len(s_norm) >= 19:

            dt = datetime.strptime(s_norm, "%Y-%m-%d %H:%M:%S")

        elif len(s_norm) >= 10:

            dt = datetime.strptime(s_norm[:10], "%Y-%m-%d")

    except ValueError:

        pass

    if dt is None:

        try:

            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))

        except ValueError:

            return s

    if dt.tzinfo is None:

        dt = dt.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)

    delta = now - dt

    if delta < timedelta(0):

        delta = timedelta(0)

    sec = int(delta.total_seconds())

    if sec < 60:

        return "только что"

    if sec < 3600:

        m = sec // 60

        return f"{m} мин назад"

    if sec < 86400:

        h = sec // 3600

        return f"{h} ч назад"

    if sec < 86400 * 2:

        return "вчера"

    if sec < 86400 * 7:

        d = sec // 86400

        return f"{d} дн. назад"

    if sec < 86400 * 30:

        w = sec // (86400 * 7)

        return f"{w} нед. назад"

    if sec < 86400 * 365:

        mo = sec // (86400 * 30)

        return f"{mo} мес. назад"

    y = sec // (86400 * 365)

    return f"{y} г. назад"

def _calc_tz_diff(tz_me: str, tz_partner: str, partner_name: str) -> str:
    """
    Вычисляет разницу часовых поясов и возвращает строку для вставки в сообщение.
    Например: '. У партнёра позже на 3 часа' или '. У партнёра раньше на 6 часов 30 минут'
    """
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone as dt_timezone

    def _hours_word(n: int) -> str:
        """Русское склонение слова «час»."""
        if n % 10 == 1 and n % 100 != 11:
            return "час"
        elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            return "часа"
        return "часов"

    try:
        now = datetime.now(dt_timezone.utc)
        off_me = now.astimezone(ZoneInfo(tz_me)).utcoffset()
        off_partner = now.astimezone(ZoneInfo(tz_partner)).utcoffset()
        diff_sec = int((off_partner - off_me).total_seconds())
        if diff_sec == 0:
            return ""
        abs_sec = abs(diff_sec)
        hours = abs_sec // 3600
        minutes = (abs_sec % 3600) // 60
        if hours and minutes:
            diff_str = f"{hours} {_hours_word(hours)} {minutes} минут"
        elif hours:
            diff_str = f"{hours} {_hours_word(hours)}"
        else:
            diff_str = f"{minutes} минут"
        direction = "позже" if diff_sec > 0 else "раньше"
        return f". У {partner_name} {direction} на {diff_str}"
    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        logger.debug("SSE client disconnected during streaming loop")
    except Exception:
        return ""


def _allowed_cors_origin(request: Optional[web.Request] = None) -> str:
    """
    Возвращает разрешённый Origin для CORS.
    Если BOT_SITE_URL не задан, оставляем '*'.
    Если задан — разрешаем только этот origin.
    """
    configured = (getattr(config, "BOT_SITE_URL", "") or "").strip().rstrip("/")
    if not configured:
        return "*"
    try:
        from urllib.parse import urlparse
        allowed_origin = f"{urlparse(configured).scheme}://{urlparse(configured).netloc}"
    except Exception:
        return "*"
    if request is None:
        return allowed_origin
    req_origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if req_origin and req_origin == allowed_origin:
        return allowed_origin
    # Для same-origin запросов без Origin (например, curl/мобильный webview)
    # не ставим '*' при фиксированном домене.
    return allowed_origin


def _is_secure_request(request: web.Request) -> bool:
    """Определяет, был ли исходный запрос HTTPS (в т.ч. за reverse proxy)."""
    xf_proto = (request.headers.get("X-Forwarded-Proto") or "").strip().lower()
    if xf_proto:
        return xf_proto == "https"
    try:
        return bool(request.secure)
    except Exception:
        return False


def _add_cors_headers(response: web.StreamResponse, request: Optional[web.Request] = None) -> web.StreamResponse:

    """Простейший CORS, чтобы GitHub Pages мог обращаться к API."""

    response.headers["Access-Control-Allow-Origin"] = _allowed_cors_origin(request)

    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"

    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, X-Api-Key, X-Visitor-Id, X-Visitor-Signature, X-AI-Session"
    )

    return response

def _check_api_secret(request: web.Request) -> bool:

    """Проверяет секретный ключ в заголовках для доступа к API."""

    secret = (getattr(config, "API_SECRET_KEY", "") or "").strip()

    if not secret:
        logger.error("API_SECRET_KEY не задан — API заблокирован (fail-closed)")
        return False

    provided = (request.headers.get("X-Api-Key") or "").strip()
    if provided:
        ok = hmac.compare_digest(provided, secret)
    else:
        admin_session = (request.cookies.get("admin_session") or "").strip()
        if _verify_admin_session(admin_session, request) is not None:
            ok = True
        else:
            # Сайт больше не получает API_SECRET_KEY в JS.
            # Для same-origin запросов допускаем подписанную пользовательскую сессию.
            session_vid = (request.cookies.get("visitor_id") or "").strip()
            session_sig = (request.cookies.get("visitor_sig") or "").strip()
            ok = bool(session_vid and session_sig and _verify_visitor_signature(session_vid, session_sig))

    if not ok:

        logger.warning(

            "API access forbidden: path=%s ip=%s provided_key=%r",

            request.path_qs,

            request.remote,

            provided[:5] + "***" if provided else "",

        )

    return ok


def _ai_hmac_secret() -> bytes:
    raw = (
        (getattr(config, "AI_SESSION_SECRET", "") or "").strip()
        or (getattr(config, "API_SECRET_KEY", "") or "").strip()
        or "fallback-dev-secret"
    )
    return raw.encode("utf-8", errors="ignore")


def _sign_payload(value: str) -> str:
    mac = hmac.new(_ai_hmac_secret(), value.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def _issue_ai_session(visitor_id: str, ttl_seconds: int = 3600) -> tuple[str, int]:
    exp = int(time.time()) + max(60, int(ttl_seconds))
    nonce = secrets.token_urlsafe(8)
    body = f"{visitor_id}.{exp}.{nonce}"
    sig = _sign_payload(body)
    return f"{body}.{sig}", exp


def _ua_fingerprint(request: web.Request) -> str:
    ua = (request.headers.get("User-Agent") or "")[:256]
    return hashlib.sha256(ua.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _issue_admin_session(user_id: int, request: web.Request, ttl_seconds: int = 12 * 3600) -> tuple[str, int]:
    exp = int(time.time()) + max(300, int(ttl_seconds))
    nonce = secrets.token_urlsafe(8)
    uaf = _ua_fingerprint(request)
    body = f"{int(user_id)}.{exp}.{nonce}.{uaf}"
    sig = _sign_payload(f"admin:{body}")
    return f"{body}.{sig}", exp


def _verify_admin_session(token: str, request: web.Request) -> Optional[int]:
    tok = (token or "").strip()
    if not tok:
        return None
    parts = tok.split(".")
    if len(parts) < 5:
        return None
    uid_s, exp_s, nonce, uaf = parts[0], parts[1], parts[2], parts[3]
    sig = ".".join(parts[4:])
    try:
        uid = int(uid_s)
        exp = int(exp_s)
    except Exception:
        return None
    if uid <= 0 or exp < int(time.time()):
        return None
    if not hmac.compare_digest(uaf, _ua_fingerprint(request)):
        return None
    body = f"{uid}.{exp}.{nonce}.{uaf}"
    expected = _sign_payload(f"admin:{body}")
    if not hmac.compare_digest(expected, sig):
        return None
    if not db.is_creator(uid):
        return None
    return uid


def _verify_ai_session(token: str, visitor_id: str) -> bool:
    tok = (token or "").strip()
    vid = (visitor_id or "").strip()
    if not tok or not vid:
        return False
    parts = tok.split(".")
    if len(parts) < 4:
        return False
    tok_vid, tok_exp, tok_nonce = parts[0], parts[1], parts[2]
    tok_sig = ".".join(parts[3:])
    if tok_vid != vid:
        return False
    try:
        exp_i = int(tok_exp)
    except Exception:
        return False
    if exp_i < int(time.time()):
        return False
    body = f"{tok_vid}.{tok_exp}.{tok_nonce}"
    expected = _sign_payload(body)
    return hmac.compare_digest(expected, tok_sig)


def _verify_visitor_signature(visitor_id: str, signature: str) -> bool:
    vid = (visitor_id or "").strip()
    sig = (signature or "").strip()
    if not vid or not sig:
        return False
    expected = _sign_payload(f"visitor:{vid}")
    return hmac.compare_digest(expected, sig)


def _check_ai_request_auth(request: web.Request, visitor_id: str) -> bool:
    session_token = (request.headers.get("X-AI-Session") or "").strip()
    visitor_sig = (request.headers.get("X-Visitor-Signature") or "").strip()
    if not _verify_ai_session(session_token, visitor_id):
        return False
    if not _verify_visitor_signature(visitor_id, visitor_sig):
        return False
    return True
def _get_trusted_visitor_id(
    request: web.Request,
    payload: Optional[dict] = None,
    payload_key: str = "visitor_id",
    query_key: str = "v",
    allow_header_fallback: bool = True,
) -> Optional[str]:
    """
    Возвращает visitor_id только из подписанной сессии.
    Если клиент прислал visitor_id в payload/query/header, он обязан совпасть с подписанным.
    """
    payload = payload or {}
    claimed = (
        _pstr(payload.get(payload_key)).strip()
        or (request.rel_url.query.get(query_key) or "").strip()
        or (request.rel_url.query.get("visitor_id") or "").strip()
        or (request.headers.get("X-Visitor-Id") or "").strip()
    )
    session_vid = (request.cookies.get("visitor_id") or "").strip()
    session_sig = (request.cookies.get("visitor_sig") or "").strip()
    if allow_header_fallback:
        session_vid = session_vid or (request.headers.get("X-Visitor-Id") or "").strip()
        session_sig = session_sig or (request.headers.get("X-Visitor-Signature") or "").strip()

    if not session_vid or not session_sig:
        return None
    if not _verify_visitor_signature(session_vid, session_sig):
        return None
    if claimed and claimed != session_vid:
        logger.warning("Tenant isolation block: claimed visitor_id=%r mismatches signed session=%r", claimed, session_vid)
        return None

    # Revocation check
    if "_" in session_vid:
        if not db.get_device_by_visitor_id(session_vid):
            logger.warning("Tenant isolation block: session_vid=%r not found in devices (revoked/logged out)", session_vid)
            return None

    return session_vid


def _safe_html(text: str) -> str:
    """Экранирует пользовательский текст для безопасной вставки через innerHTML.
    Конвертирует переносы строк в <br> для отображения.
    """
    if not text:
        return ""
    return _html_module.escape(str(text)).replace("\n", "<br>")

def _can_read_site_media(request: web.Request) -> bool:
    """Доступ к медиа только для авторизованных участников зарегистрированной пары."""
    trusted_vid = _get_trusted_visitor_id(
        request,
        payload=None,
        query_key="v",
        allow_header_fallback=False,
    )
    if not trusted_vid:
        return False
    user_id = _visitor_to_user_id(trusted_vid)
    if not user_id:
        return False
    return bool(db.is_in_couple(user_id))


def _media_belongs_to_user_couple(path_part: str, viewer_user_id: int) -> bool:
    """Проверяет, что медиа-файл принадлежит паре текущего пользователя."""
    raw_path = str(path_part or "").strip()
    filename = Path(raw_path).name
    if not filename:
        return False
    requested_rel = raw_path.replace("\\", "/").lstrip("/")

    def _norm_media_rel(p: Any) -> str:
        s = str(p or "").strip().replace("\\", "/")
        if not s:
            return ""
        s = s.lstrip("/")
        marker = "/media/"
        i = s.rfind(marker)
        if i >= 0:
            s = s[i + len(marker):]
        if s.startswith("media/"):
            s = s[len("media/"):]
        return s.lstrip("/")

    def _path_matches(stored_path: Any) -> bool:
        rel = _norm_media_rel(stored_path)
        if not rel:
            return False
        # Строгое сравнение относительного пути медиа.
        if rel == requested_rel:
            return True
        # Thumbnails are stored as <stem>_thumb.jpg, while the original media path
        # is what gets persisted in DB. Allow a thumbnail URL when it belongs to the
        # same original file stem.
        req_stem = Path(requested_rel).stem
        if req_stem.endswith("_thumb"):
            base_stem = req_stem[:-6]
            if Path(rel).stem == base_stem:
                return True
        return False

    viewer_couple = db.get_couple_by_user(viewer_user_id) or {}
    viewer_couple_id = viewer_couple.get("id")
    if not viewer_couple_id:
        return False
    viewer_members = set(db.get_couple_members(int(viewer_couple_id)))
    if not viewer_members:
        return False

    # avatars: avatar_<visitor_id>.<ext> доступны только участникам той же пары
    if path_part.startswith("avatars/"):
        stem = Path(filename).stem
        prefix = "avatar_"
        if not stem.startswith(prefix):
            return False
        owner_vid = stem[len(prefix):]
        owner_uid = _visitor_to_user_id(owner_vid)
        return bool(owner_uid and int(owner_uid) in viewer_members)

    def _same_couple_by_user(owner_user_id: Any) -> bool:
        try:
            return int(owner_user_id) in viewer_members
        except Exception:
            return False

    like_mask = f"%{filename}%"
    try:
        with db._get_connection() as conn:
            # memories: учитываем и media_path, и JSON media_items
            mem_rows = conn.execute(
                """SELECT user_id, couple_id, media_path, media_items
                   FROM memories
                   WHERE media_path LIKE ? OR media_items LIKE ?""",
                (like_mask, like_mask),
            ).fetchall()
            for row in mem_rows:
                if int(row["couple_id"] or 0) != int(viewer_couple_id):
                    continue
                candidates = set()
                if row["media_path"]:
                    candidates.add(str(row["media_path"]))
                raw_items = row["media_items"]
                if raw_items:
                    try:
                        parsed = json.loads(raw_items)
                        if isinstance(parsed, list):
                            for it in parsed:
                                if isinstance(it, dict) and it.get("path"):
                                    candidates.add(str(it.get("path")))
                    except Exception:
                        pass
                if any(_path_matches(c) for c in candidates):
                    return True

            # wishes
            wish_rows = conn.execute(
                """SELECT user_id, media_path
                   FROM wishes
                   WHERE media_path LIKE ?""",
                (like_mask,),
            ).fetchall()
            for row in wish_rows:
                if not row["media_path"]:
                    continue
                if _path_matches(row["media_path"]) and _same_couple_by_user(row["user_id"]):
                    return True

            # scheduled_events
            event_rows = conn.execute(
                """SELECT user_id, media_path
                   FROM scheduled_events
                   WHERE media_path LIKE ?""",
                (like_mask,),
            ).fetchall()
            for row in event_rows:
                if not row["media_path"]:
                    continue
                if _path_matches(row["media_path"]) and _same_couple_by_user(row["user_id"]):
                    return True

            # site_notifications: доступны только текущей паре (по couple_id)
            notif_rows = conn.execute(
                """SELECT couple_id, media_path, media_items
                   FROM site_notifications
                   WHERE media_path LIKE ? OR media_items LIKE ?""",
                (like_mask, like_mask),
            ).fetchall()
            if notif_rows:
                creator_couple = db.get_couple_by_user(int(config.CREATOR_ID)) or {}
                creator_couple_id = int(creator_couple.get("id") or 0)
                for row in notif_rows:
                    notif_couple_id = int(row["couple_id"] or 0)
                    if notif_couple_id:
                        if notif_couple_id != int(viewer_couple_id):
                            continue
                    else:
                        # legacy строки без couple_id доступны только паре создателя
                        if not creator_couple_id or int(viewer_couple_id) != creator_couple_id:
                            continue

                    candidates = set()
                    if row["media_path"]:
                        candidates.add(str(row["media_path"]))
                    raw_items = row["media_items"]
                    if raw_items:
                        try:
                            parsed = json.loads(raw_items)
                            if isinstance(parsed, list):
                                for it in parsed:
                                    if isinstance(it, dict) and it.get("path"):
                                        candidates.add(str(it.get("path")))
                        except Exception:
                            pass
                    if any(_path_matches(c) for c in candidates):
                        return True
    except Exception:
        logger.exception("media access ownership check failed for file=%r viewer_user_id=%r", filename, viewer_user_id)
        return False

    return False


def _validate_media_path_for_user(media_path: Optional[str], user_id: int) -> Optional[str]:
    """Разрешает только безопасные медиа-пути, принадлежащие текущему пользователю.

    Обязательный формат для новых файлов: префикс `u<user_id>_`.
    Это исключает привязку чужих/угаданных файлов через подмену media_path.
    """
    raw = _pstr(media_path).strip()
    if not raw:
        return None

    media_root = Path(config.MEDIA_FOLDER).resolve()
    try:
        full_path = Path(raw).resolve()
        full_path.relative_to(media_root)
    except Exception:
        return None

    if not full_path.is_file():
        return None

    expected_prefix = f"u{int(user_id)}_"
    if full_path.name.startswith(expected_prefix):
        return str(full_path)

    return None


# Разрешённые расширения для загружаемых файлов
_ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif"}
_ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v"}
_ALLOWED_UPLOAD_EXT = _ALLOWED_IMAGE_EXT | _ALLOWED_VIDEO_EXT


def _generate_thumbnail(image_path: Path, max_side: int = 400) -> Optional[str]:
    """Генерирует thumbnail для изображения. Возвращает путь к thumb или None."""
    if not _PILImage:
        return None
    try:
        with _PILImage.open(image_path) as img:
            img.draft(None, (max_side, max_side))
            w, h = img.size
            if w <= 0 or h <= 0:
                return None
            if w <= max_side and h <= max_side:
                return None
            scale = min(max_side / w, max_side / h)
            new_size = (max(1, round(w * scale)), max(1, round(h * scale)))
            thumb = img.resize(new_size, _PILImage.LANCZOS)
            if thumb.mode in ("RGBA", "P"):
                thumb = thumb.convert("RGB")
            thumb_path = image_path.with_stem(image_path.stem + "_thumb").with_suffix(".jpg")
            thumb.save(thumb_path, "JPEG", quality=75, optimize=True)
            return str(thumb_path)
    except Exception:
        return None


def _spawn_background_task(coro, label: str) -> None:
    """Запускает фоновую задачу и логирует её завершение."""
    task = asyncio.create_task(coro)

    def _on_done(done_task: asyncio.Task) -> None:
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            logger.info("%s cancelled", label)
            return
        except Exception as e:
            logger.warning("%s completion check failed: %s", label, e)
            return
        if exc:
            logger.warning("%s failed: %s", label, exc)
        else:
            logger.info("%s completed", label)

    task.add_done_callback(_on_done)


def _thumb_url_for(media_path: Optional[str]) -> Optional[str]:
    """Возвращает URL thumbnail если файл существует, иначе URL оригинала."""
    if not media_path:
        return None
    p = Path(media_path)
    thumb_p = p.with_stem(p.stem + "_thumb").with_suffix(".jpg")
    if thumb_p.is_file():
        return f"/media/{thumb_p.name}"
    return f"/media/{p.name}"

def _safe_upload_ext(filename: str, content_type: str) -> Optional[str]:
    """Возвращает разрешённое расширение или None если тип файла запрещён."""
    ext = Path(filename).suffix.lower()
    if ext in _ALLOWED_UPLOAD_EXT:
        return ext
    # Попытка определить по Content-Type если расширение отсутствует или подозрительное
    ct = content_type.lower()
    if ct.startswith("image/jpeg") or ct == "image/jpg":
        return ".jpg"
    if ct.startswith("image/png"):
        return ".png"
    if ct.startswith("image/gif"):
        return ".gif"
    if ct.startswith("image/webp"):
        return ".webp"
    if ct.startswith("video/mp4"):
        return ".mp4"
    if ct.startswith("video/quicktime"):
        return ".mov"
    if ct.startswith("video/webm"):
        return ".webm"
    if ct.startswith("image/heic") or ct.startswith("image/heif"):
        return ".heic"
    if ct.startswith("image/avif"):
        return ".avif"
    return None


def _visitor_to_user_id(visitor_id: str) -> Optional[int]:
    """Конвертирует visitor_id в реальный user_id.

    Поддерживает:
      - 'ksyusha' / 'ksusha' → db.get_ksusha_id()
      - 'creator'             → config.CREATOR_ID
      - числовую строку       → int (новые пары)
    """
    if not visitor_id:
        return None
    norm = str(visitor_id).strip().lower()
    if "_" in norm:
        norm = norm.split("_")[0]
    if norm == "creator":
        cid = int(getattr(config, "CREATOR_ID", 0) or 0)
        return cid if cid > 0 else None
    if norm in ("ksyusha", "ksusha", "partner"):
        try:
            kid = int(db.get_ksusha_id() or 0)
        except Exception:
            kid = int(getattr(config, "KSUSHA_ID", 0) or 0)
        return kid if kid > 0 else None
    try:
        return int(norm)
    except (ValueError, TypeError):
        return None


def _normalize_site_role(role_raw: str) -> Optional[str]:
    """Канонизирует role в creator|partner, поддерживая legacy-алиасы."""
    role = (role_raw or "").strip().lower()
    if role == "creator":
        return "creator"
    if role in ("partner", "ksyusha", "ksusha"):
        return "partner"
    return None


def _visitor_partner_id(visitor_id: str) -> Optional[int]:
    """Возвращает user_id партнёра для данного visitor_id.

    Для legacy пары: creator↔ksusha.
    Для новых пар: использует get_couple_partner.
    """
    uid = _visitor_to_user_id(visitor_id)
    if uid is None:
        return None
    partner = db.get_couple_partner(uid)
    return partner


# Клиенты WebSocket на странице техперерыва (для уведомления об окончании)

_maintenance_ws_clients = set()

# WebSocket-клиенты страницы сайта (для push-уведомлений), строго в рамках пары.
# Формат: {couple_id: {"creator": set(ws), "partner": set(ws)}}
_site_ws_clients: dict = {}
_unlink_ws_clients: dict = {}  # { token: set(ws) }

async def _notify_unlink_ws(token: str, payload: dict) -> None:
    clients = _unlink_ws_clients.get(token, set())
    for ws in list(clients):
        try:
            await ws.send_json(payload)
        except Exception:
            _unlink_ws_clients.get(token, set()).discard(ws)



def _ws_bucket(couple_id: int) -> dict:
    bucket = _site_ws_clients.get(couple_id)
    if not bucket:
        bucket = {"creator": set(), "partner": set()}
        _site_ws_clients[couple_id] = bucket
    return bucket


def _ws_add(couple_id: int, role: str, ws: web.WebSocketResponse) -> None:
    _ws_bucket(couple_id).setdefault(role, set()).add(ws)


def _ws_discard(couple_id: int, role: str, ws: web.WebSocketResponse) -> None:
    bucket = _site_ws_clients.get(couple_id) or {}
    role_set = bucket.get(role) or set()
    role_set.discard(ws)
    if not (bucket.get("creator") or bucket.get("partner")):
        _site_ws_clients.pop(couple_id, None)


def _ws_clients(couple_id: int, *roles: str) -> list:
    bucket = _site_ws_clients.get(couple_id) or {}
    result = []
    for role in roles:
        result.extend(list(bucket.get(role, set())))
    return result

async def ws_maintenance(request: web.Request) -> web.WebSocketResponse:

    """WebSocket для страницы техперерыва: уведомляем о старте/окончании."""
    req_origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    allowed_origin = _allowed_cors_origin(request)
    if req_origin and allowed_origin != "*" and req_origin != allowed_origin:
        raise web.HTTPForbidden(text="forbidden")

    ws = web.WebSocketResponse()

    await ws.prepare(request)

    _maintenance_ws_clients.add(ws)

    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                if msg.data == 'ping':
                    try:
                        await ws.send_str('pong')
                    except Exception:
                        break
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
    finally:
        _maintenance_ws_clients.discard(ws)
    return ws

async def broadcast_maintenance_off() -> None:

    """Уведомить все открытые страницы техперерыва об обновлении (переход в продакшн)."""

    dead = []

    for client in list(_maintenance_ws_clients):

        try:

            await client.send_json({"reload": True})

        except Exception:

            dead.append(client)

    for c in dead:

        _maintenance_ws_clients.discard(c)

async def ws_site(request: web.Request) -> web.WebSocketResponse:
    """WebSocket для главной страницы: push-уведомления для пользователя."""
    req_origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    allowed_origin = _allowed_cors_origin(request)
    # Блокируем cross-site WS hijack: при фиксированном BOT_SITE_URL Origin обязан совпасть.
    if req_origin and allowed_origin != "*" and req_origin != allowed_origin:
        raise web.HTTPForbidden(text="forbidden")

    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        raise web.HTTPForbidden(text="forbidden")

    # Разрешаем WS только пользователю из зарегистрированной пары.
    visitor_uid = _visitor_to_user_id(visitor_id)
    couple = db.get_couple_by_user(visitor_uid) if visitor_uid else None
    if not couple or not couple.get("user2_id"):
        raise web.HTTPForbidden(text="forbidden")

    sender_role = "creator" if couple.get("user1_id") == visitor_uid else "partner"
    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        raise web.HTTPForbidden(text="forbidden")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    _ws_add(couple_id, sender_role, ws)
    total = sum(len(v.get("creator", set())) + len(v.get("partner", set())) for v in _site_ws_clients.values())
    logger.info("WS_SITE connect: visitor_id=%r couple_id=%s role=%r total=%d", visitor_id, couple_id, sender_role, total)
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                if msg.data == 'ping':
                    try:
                        await ws.send_str('pong')
                    except Exception:
                        break
                else:
                    # Пробуем разобрать как JSON — обрабатываем star_sent / stars_seen
                    try:
                        import json as _json
                        data = _json.loads(msg.data)
                        if data.get("type") == "star_sent":
                            count  = _safe_int(data.get("count"), 1)
                            if sender_role == "creator":
                                recipients = ["partner"]
                            elif sender_role == "partner":
                                recipients = ["creator"]
                            else:
                                recipients = []
                            logger.info("WS star_sent: from=%r count=%d -> recipients=%r", sender_role, count, recipients)
                            payload_out = _json.dumps({"type": "star_sent", "from": sender_role, "count": count})
                            dead = []
                            for rec in recipients:
                                for rec_ws in _ws_clients(couple_id, rec):
                                    try:
                                        await rec_ws.send_str(payload_out)
                                    except Exception:
                                        dead.append((rec, rec_ws))
                            for rec, dead_ws in dead:
                                _ws_discard(couple_id, rec, dead_ws)
                        elif data.get("type") == "stars_seen":
                            # Получатель закрыл inbox — шлём отправителю чтобы он сбросил счётчик
                            if sender_role == "creator":
                                notify = ["partner"]
                            elif sender_role == "partner":
                                notify = ["creator"]
                            else:
                                notify = []
                            logger.info("WS stars_seen: viewer=%r -> notifying %r", sender_role, notify)
                            payload_out = _json.dumps({"type": "stars_seen"})
                            dead = []
                            for rec in notify:
                                for rec_ws in _ws_clients(couple_id, rec):
                                    try:
                                        await rec_ws.send_str(payload_out)
                                    except Exception:
                                        dead.append((rec, rec_ws))
                            for rec, dead_ws in dead:
                                _ws_discard(couple_id, rec, dead_ws)
                    except Exception:
                        pass
            elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break
    finally:
        _ws_discard(couple_id, sender_role, ws)
        logger.info("WS_SITE disconnect: visitor_id=%r couple_id=%s role=%r", visitor_id, couple_id, sender_role)
    return ws


async def _push_celebration_to_all(cel_data: dict) -> None:
    """Пушит праздничную анимацию всем подключённым WS-клиентам."""
    dead = []
    for couple_id, bucket in list(_site_ws_clients.items()):
        for role in ("creator", "partner"):
            for ws in list(bucket.get(role, set())):
                try:
                    await ws.send_json({"type": "celebration", "celebration": cel_data})
                except Exception:
                    dead.append((couple_id, role, ws))
    for couple_id, role, ws in dead:
        _ws_discard(couple_id, role, ws)


async def _check_and_fire_celebrations() -> None:
    """Проверяет годовщины и прошедшие события, создаёт анимации в БД и пушит по WS."""
    from config import config as _cfg
    from datetime import date as _date, timedelta as _td, datetime as _dt, timezone as _tz

    now_utc = _dt.now(_tz.utc)

    # ── 1. Годовщина (30 октября) ──
    # Создаём запись ОДИН РАЗ в год при наступлении 30 октября в любом
    # из двух часовых поясов. Запись хранится пока оба пользователя её
    # не получат — даже если они зашли через день/неделю.
    date_met = _cfg.DATE_MET  # date(2025, 10, 30)
    if date_met:
        current_year = now_utc.year
        # Ключ включает год — чтобы каждый год была новая запись
        anniv_ctype = f"anniversary_{current_year}"
        last_anniv = db.get_last_celebration_date(anniv_ctype)
        if not last_anniv:
            # Проверяем: наступило ли 30 октября хотя бы в одном из двух поясов
            for tz_offset in (3, 6):
                local_now = now_utc + _td(hours=tz_offset)
                if (local_now.month == date_met.month and
                    local_now.day == date_met.day and
                    local_now >= _dt(local_now.year, date_met.month, date_met.day,
                                     0, 0, tzinfo=_tz.utc) - _td(hours=-tz_offset)):
                    year_num = local_now.year - date_met.year
                    if year_num <= 0:
                        break
                    suffix = "год" if year_num == 1 else "года" if 1 < year_num < 5 else "лет"
                    title = f"🎉 {year_num}-я годовщина! Мы вместе уже {year_num} {suffix}!"
                    cel_id = db.add_celebration(anniv_ctype, title)
                    if cel_id:
                        await _push_celebration_to_all(
                            {"id": cel_id, "celebration_type": "anniversary", "event_title": title}
                        )
                    break

    # ── 2. Прошедшие события ──
    # Запись создаётся ОДИН РАЗ при наступлении события и хранится
    # пока оба пользователя (ksyusha, creator) не получат анимацию.
    for ev in (db.get_scheduled_events(limit=200) or []):
        try:
            from utils import _event_datetime_to_utc
            ev_utc = _event_datetime_to_utc(ev.event_datetime or "", ev.user_id)
            if not ev_utc:
                continue
            # Событие уже наступило
            if now_utc < ev_utc:
                continue
            ctype = f"event_{ev.id}"
            # Создаём запись только если её ещё нет совсем
            last = db.get_last_celebration_date(ctype)
            if not last:
                cel_id = db.add_celebration(ctype, ev.title or "Событие", ev.id)
                if cel_id:
                    await _push_celebration_to_all({
                        "id": cel_id,
                        "celebration_type": "event",
                        "event_title": ev.title or "Событие",
                        "event_id": ev.id,
                    })
        except Exception as e:
            logger.debug("scheduler celebration sync failed for event_id=%s: %s", getattr(ev, "id", None), e)


async def broadcast_notification(notif_data: dict) -> None:
    """Отправить уведомление только WS-клиентам партнёра в целевой паре."""
    couple_id = _safe_int(notif_data.get("couple_id"), 0) or 0
    if couple_id <= 0:
        return
    clients = _ws_clients(couple_id, "partner")
    logger.info("WS broadcast_notification: couple_id=%s clients=%d", couple_id, len(clients))
    dead = []
    for ws in clients:
        try:
            await ws.send_json({"type": "notification", "notification": notif_data})
        except Exception as e:
            logger.debug("WS notify send failed: %s", e)
            dead.append(ws)
    for ws in dead:
        _ws_discard(couple_id, "partner", ws)


async def broadcast_wish_status(wish_id: int, new_status: str) -> None:
    """Рассылает обновление статуса желания только в пару владельца желания."""
    payload = {"type": "wish_status_update", "wish_id": wish_id, "status": new_status}
    wish = db.get_wish(wish_id)
    if not wish:
        return
    couple = db.get_couple_by_user(wish.user_id) if wish.user_id else None
    couple_id = _safe_int((couple or {}).get("id"), 0) or 0
    if couple_id <= 0:
        return
    dead = []
    for vid in ("creator", "partner"):
        for ws in _ws_clients(couple_id, vid):
            try:
                await ws.send_json(payload)
            except Exception as e:
                logger.debug("WS wish_status send failed: role=%s, wish_id=%s, err=%s", vid, wish_id, e)
                dead.append((vid, ws))
    for vid, ws in dead:
        _ws_discard(couple_id, vid, ws)


async def broadcast_maintenance_on() -> None:

    """Уведомить все открытые продакшн-страницы, что включён техперерыв (показать maintenance)."""

    dead = []

    for client in list(_maintenance_ws_clients):

        try:

            await client.send_json({"maintenance": True})

        except Exception:

            dead.append(client)

    for c in dead:

        _maintenance_ws_clients.discard(c)

async def handle_options(request: web.Request) -> web.Response:

    """Ответ на preflight-запросы браузера."""

    resp = web.Response(status=204)

    return _add_cors_headers(resp)

async def info(request: web.Request) -> web.Response:

    """Возвращает базовую информацию о боте (ID пользователей пары)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    data = {

        "creator_id": config.CREATOR_ID,

        "ksusha_id":db.get_ksusha_id(),

    }

    resp = web.json_response(data)

    return _add_cors_headers(resp)

def _parse_user_agent(user_agent: str) -> str:

    """Ставит человекопонятный вид вида 'Android 10, Chrome' вместо длинной UA-строки."""

    if not user_agent:

        return "Неизвестное устройство"

    ua = user_agent

    ua_l = ua.lower()

    # Определяем ОС

    os = "Неизвестная ОС"

    if "android" in ua_l:

        m = re.search(r"Android\s+([\d\.]+)", ua, re.IGNORECASE)

        os = f"Android {m.group(1)}" if m else "Android"

    elif "iphone" in ua_l or "ipad" in ua_l or "cpu iphone os" in ua_l:

        m = re.search(r"OS\s+([\d_]+)", ua)

        if m:

            ver = m.group(1).replace("_", ".")

            os = f"iOS {ver}"

        else:

            os = "iOS"

    elif "windows nt" in ua_l:

        nt_map = {

            "10.0": "Windows 10/11",

            "6.3": "Windows 8.1",

            "6.2": "Windows 8",

            "6.1": "Windows 7",

        }

        m = re.search(r"Windows NT\s+([\d\.]+)", ua, re.IGNORECASE)

        if m:

            ver = m.group(1)

            os = nt_map.get(ver, f"Windows {ver}")

        else:

            os = "Windows"

    elif "mac os x" in ua_l:

        m = re.search(r"Mac OS X\s+([\d_]+)", ua, re.IGNORECASE)

        if m:

            ver = m.group(1).replace("_", ".")

            os = f"macOS {ver}"

        else:

            os = "macOS"

    elif "linux" in ua_l:

        os = "Linux"

    browser = "Неизвестный браузер"

    if "samsungbrowser/" in ua_l:

        m = re.search(r"SamsungBrowser/([\d\.]+)", ua, re.IGNORECASE)

        browser = f"Samsung Browser {m.group(1)}" if m else "Samsung Browser"

    elif "edg/" in ua_l or "edga/" in ua_l or "edgios/" in ua_l:

        m = re.search(r"Edg[aAios]?/([\d\.]+)", ua, re.IGNORECASE)

        browser = f"Edge {m.group(1)}" if m else "Edge"

    elif "opr/" in ua_l or "opera/" in ua_l:

        m = re.search(r"(?:OPR|Opera)/([\d\.]+)", ua, re.IGNORECASE)

        browser = f"Opera {m.group(1)}" if m else "Opera"

    elif "yabrowser/" in ua_l:

        m = re.search(r"YaBrowser/([\d\.]+)", ua, re.IGNORECASE)

        browser = f"Яндекс Браузер {m.group(1)}" if m else "Яндекс Браузер"

    elif "chrome/" in ua_l and "chromium/" not in ua_l:

        m = re.search(r"Chrome/([\d\.]+)", ua)

        browser = f"Chrome {m.group(1)}" if m else "Chrome"

    elif "firefox/" in ua_l:

        m = re.search(r"Firefox/([\d\.]+)", ua)

        browser = f"Firefox {m.group(1)}" if m else "Firefox"

    elif "safari/" in ua_l and "chrome/" not in ua_l:

        m = re.search(r"Version/([\d\.]+)", ua)

        browser = f"Safari {m.group(1)}" if m else "Safari"

    # Тип устройства

    device = "Desktop"

    if "mobile" in ua_l or "android" in ua_l or "iphone" in ua_l:

        device = "Mobile"

    elif "ipad" in ua_l or "tablet" in ua_l:

        device = "Tablet"

    # Пытаемся вытащить модель устройства (для Android это, как правило, строка вида SM-XXXX)

    model = ""

    if "android" in ua_l:

        # Пример: "... Android 10; SM-A515F Build/..."

        m = re.search(r"Android\s+[\d\.]+;\s*([^;]+?)\s+Build", ua, re.IGNORECASE)

        if m:

            model = m.group(1).strip()

    elif "iphone" in ua_l or "cpu iphone os" in ua_l:

        # Для iPhone из UA нельзя достоверно узнать конкретную модель (11/12/13 и т.п.)

        # Поэтому оставляем просто "iPhone".

        model = "iPhone"

    elif "ipad" in ua_l:

        model = "iPad"

    base = f"{os}, {browser} ({device})"

    if model:

        return f"{base}, {model}"

    return base

def _format_device_info(user_agent_raw: str, ua_hints: dict | None) -> str:

    """

    Более современный формат описания устройства:

    - Пытаемся использовать User-Agent Client Hints (то, что пришло с фронта),

      чтобы показать реальную платформу, версию и модель (Pixel 8, и т.п.)

    - Если hints нет или они пустые — возвращаем парсинг обычного UA.

    """

    if not ua_hints or not isinstance(ua_hints, dict):

        return _parse_user_agent(user_agent_raw)

    platform = (ua_hints.get("platform") or "").strip()

    platform_ver = (ua_hints.get("platformVersion") or "").strip()

    model = (ua_hints.get("model") or "").strip()

    arch = (ua_hints.get("architecture") or "").strip()

    bitness = (ua_hints.get("bitness") or "").strip()

    mobile = bool(ua_hints.get("mobile"))

    full_list = ua_hints.get("fullVersionList") or ua_hints.get("full_version_list") or []

    # ОС

    os_str = platform or "Неизвестная платформа"

    if platform_ver:

        os_str = f"{os_str} {platform_ver}"

    # Браузер

    browser_str = "Неизвестный браузер"

    if isinstance(full_list, list):

        # Ищем запись с Chrome/Chromium, иначе берём первую

        chrome_entry = None

        if full_list:

            for item in full_list:

                try:

                    brand = (item.get("brand") or "").lower()

                    if "chrome" in brand or "chromium" in brand:

                        chrome_entry = item

                        break

                except Exception:

                    continue

        entry = chrome_entry or (full_list[0] if full_list else None)

        if entry:

            brand = entry.get("brand") or "Браузер"

            version = entry.get("version") or ""

            browser_str = f"{brand} {version}".strip()

    # Тип устройства

    device_type = "Desktop"

    if mobile or platform.lower() in ("android", "ios"):

        device_type = "Mobile"

    base = f"{os_str}, {browser_str} ({device_type})"

    extra_bits = []

    if arch:

        if bitness:

            extra_bits.append(f"{arch}/{bitness}")

        else:

            extra_bits.append(arch)

    elif bitness:

        extra_bits.append(f"{bitness}-bit")

    if model:

        extra_bits.append(model)

    if extra_bits:

        return f"{base}, " + ", ".join(extra_bits)

    return base

def _is_private_ip(ip: str) -> bool:

    """Простейшая проверка, является ли IP локальным/приватным."""

    if not ip:

        return False

    ip = ip.strip()

    if ip.startswith("10."):

        return True

    if ip.startswith("192.168."):

        return True

    if ip.startswith("127."):

        return True

    if ip.startswith("169.254."):

        return True

    if ip.startswith("172."):

        # 172.16.0.0 – 172.31.255.255

        try:

            parts = ip.split(".")

            if len(parts) >= 2:

                second = int(parts[1])

                if 16 <= second <= 31:

                    return True

        except ValueError:

            pass

    return False

def _memory_to_public_dict(m: Memory, timezone_id: str | None = None) -> dict:

    # Подставляем параметры от лица создателя записи

    user_for_params = m.user_id or db.get_ksusha_id()

    title_resolved = substitute_params(m.title or "", user_for_params)

    content_resolved = substitute_params(m.content or "", user_for_params)

    date_resolved = substitute_params(m.date or "", user_for_params)

    # Если JOIN не дал имени — делаем прямой запрос к users
    username   = m.username
    first_name = m.first_name
    last_name  = m.last_name
    if not username and not first_name:
        user_row = db.get_user(m.user_id)
        if user_row:
            username   = user_row.get("username")   or username
            first_name = user_row.get("first_name") or first_name
            last_name  = user_row.get("last_name")  or last_name

    display_name = db.get_display_name(m.user_id) if m.user_id else ""

    media_url = f"/media/{Path(m.media_path).name}" if m.media_path else None
    media_items_payload = []
    if getattr(m, "media_items", None):
        for item in (m.media_items or [])[:6]:
            p = (item.get("path") or "").strip() if isinstance(item, dict) else ""
            t = (item.get("type") or "").strip() if isinstance(item, dict) else ""
            if not p or not t:
                continue
            name = Path(p).name
            payload_item = {
                "type": t,
                "name": name,
                "filename": name,
                "url": f"/media/{name}",
                "thumb_url": _thumb_url_for(p),
                "preview_url": _thumb_url_for(p),
                "original_url": f"/media/{name}",
            }
            width = item.get("width") if isinstance(item, dict) else None
            height = item.get("height") if isinstance(item, dict) else None
            if isinstance(width, int) and width > 0:
                payload_item["width"] = width
            if isinstance(height, int) and height > 0:
                payload_item["height"] = height
            media_items_payload.append(payload_item)
    if not media_items_payload and m.media_path and m.media_type in {"photo", "video"}:
        media_items_payload = [{
            "type": m.media_type,
            "name": Path(m.media_path).name,
            "filename": Path(m.media_path).name,
            "url": media_url,
            "thumb_url": _thumb_url_for(m.media_path) if m.media_type == "photo" else media_url,
            "preview_url": _thumb_url_for(m.media_path) if m.media_type == "photo" else media_url,
            "original_url": media_url,
        }]
    preview_url = _thumb_url_for(m.media_path) if m.media_type == "photo" else media_url

    return {

        "id": m.id,

        "user_id": m.user_id,

        "username": username,

        "first_name": first_name,

        "last_name": last_name,

        "display_name": display_name,

        "category": m.category,

        "title": title_resolved or m.title,

        "date": m.date,

        "date_human": format_datetime_for_user(date_resolved or "", timezone_id),

        "date_resolved": date_resolved,

        "content_html": _safe_html(content_resolved or m.content or ""),

        "media_type": m.media_type,

        "media_url": media_url,
        "preview_url": preview_url,
        "thumb_url": preview_url,
        "original_url": media_url,
        "name": Path(m.media_path).name if m.media_path else None,
        "filename": Path(m.media_path).name if m.media_path else None,
        "media_items": media_items_payload,

        "created_at": m.created_at,

        "updated_at": m.updated_at,

        "created_human": format_datetime_for_user(m.created_at or "", timezone_id),

        "updated_human": format_datetime_for_user(m.updated_at or "", timezone_id),

        "privacy_type": m.privacy_type,

        "privacy_views_limit": m.privacy_views_limit,

        # На сайт отдаём только вопрос, ответ никогда не выдаём

        "privacy_question": m.privacy_question if (m.privacy_type == "password") else None,

    }

def _wish_to_public_dict(w: Wish, timezone_id: str | None = None) -> dict:
    media_url = f"/media/{Path(w.media_path).name}" if w.media_path else None
    preview_url = _thumb_url_for(w.media_path) if w.media_type == "photo" else media_url

    return {

        "id": w.id,

        "user_id": w.user_id,

        "wish_number": w.wish_number,

        "content_html": _safe_html(w.content or ""),

        "status": getattr(w, "status", "created") or "created",

        "media_type": w.media_type,

        "media_url": media_url,
        "preview_url": preview_url,
        "thumb_url": preview_url,
        "original_url": media_url,
        "name": Path(w.media_path).name if w.media_path else None,
        "filename": Path(w.media_path).name if w.media_path else None,

        "created_at": w.created_at,

        "updated_at": w.updated_at,

        "created_human": format_datetime_for_user(w.created_at or "", timezone_id),

        "updated_human": format_datetime_for_user(w.updated_at or "", timezone_id),

    }

def _event_to_public_dict(e: ScheduledEvent, timezone_id: str | None = None) -> dict:

    # Добавляем человека, который создал событие

    user = db.get_user(e.user_id) or {}

    display_name = db.get_display_name(e.user_id) if e.user_id else ""

    event_utc = _event_datetime_to_utc(e.event_datetime or "", e.user_id)

    event_utc_iso = event_utc.isoformat() if event_utc else None

    media_url = f"/media/{Path(e.media_path).name}" if e.media_path else None
    preview_url = _thumb_url_for(e.media_path) if e.media_type == "photo" else media_url
    return {

        "id": e.id,

        "user_id": e.user_id,

        "username": user.get("username"),

        "first_name": user.get("first_name"),

        "last_name": user.get("last_name"),

        "display_name": display_name,

        "title": e.title,

        "description_html": _safe_html(e.description or ""),

        "event_datetime": e.event_datetime,

        "event_datetime_human": format_scheduled_event_datetime_for_timezone(e.event_datetime or "", e.user_id, timezone_id),

        "event_utc_iso": event_utc_iso,

        "is_passed": bool(is_scheduled_event_moment_passed(e.event_datetime or "", e.user_id)),

        "created_at": e.created_at,

        "created_human": format_datetime_for_user(e.created_at or "", timezone_id),

        "media_type": e.media_type,

        "media_url": media_url,
        "preview_url": preview_url,
        "thumb_url": preview_url,
        "original_url": media_url,
        "name": Path(e.media_path).name if e.media_path else None,
        "filename": Path(e.media_path).name if e.media_path else None,

    }

async def all_data(request: web.Request) -> web.Response:

    """Отдаёт все данные для сайта: статистика, воспоминания, события, желания."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    timezone_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    data = _collect_site_data(timezone_id, visitor_id)

    return _json_response_with_etag(request, data)

async def site_bootstrap_data(request: web.Request) -> web.Response:

    """Быстрый payload для первого экрана сайта (без лишних блоков)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    timezone_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    page, limit = _parse_page_limit(request, default_limit=30, max_limit=60)
    data = _collect_site_data(timezone_id, visitor_id, endpoint="api/site_bootstrap")
    all_memories = data.get("memories") or []
    all_events = data.get("events") or []
    wishes_obj = data.get("wishes") or {}
    wishes_user = wishes_obj.get("user") or []
    wishes_partner = wishes_obj.get("partner") or []

    mem_start = (page - 1) * limit
    evt_limit = max(10, min(40, limit // 2 or 10))
    bootstrap_payload = {
        "ok": True,
        "creator_id": data.get("creator_id"),
        "ksusha_id": data.get("ksusha_id"),
        "partner_id": data.get("partner_id"),
        "users": data.get("users") or {},
        "stats": data.get("stats") or {},
        "stats_url": data.get("stats_url") or "/stats",
        "is_open": data.get("is_open"),
        "last_added": data.get("last_added"),
        "date_met": data.get("date_met"),
        "custom_categories": data.get("custom_categories") or [],
        "memories": all_memories[mem_start:mem_start + limit],
        "events": all_events[:evt_limit],
        "wishes": {
            "user": wishes_user,
            "partner": wishes_partner,
        },
        "user_settings": data.get("user_settings"),
        "deferred": {
            "memories": {"page": page, "limit": limit, "total": len(all_memories), "has_more": (mem_start + limit) < len(all_memories)},
            "events": {"page": 1, "limit": evt_limit, "total": len(all_events), "has_more": evt_limit < len(all_events)},
            "wishes": {"page": 1, "limit": len(wishes_partner), "total": len(wishes_partner), "has_more": False},
        },
    }
    return _json_response_with_etag(request, bootstrap_payload)

async def memories_data(request: web.Request) -> web.Response:

    """Отдаёт только воспоминания (для отладки и возможного использования ИИ)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    timezone_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    page, limit = _parse_page_limit(request, default_limit=50, max_limit=100)
    data = _collect_site_data(timezone_id, visitor_id, endpoint="api/memories")
    items = data.get("memories") or []
    start = (page - 1) * limit
    payload = {
        "items": items[start:start + limit],
        "page": page,
        "limit": limit,
        "total": len(items),
        "has_more": (start + limit) < len(items),
    }
    return _json_response_with_etag(request, payload)

async def events_data(request: web.Request) -> web.Response:

    """Отдаёт только события (для отладки и возможного использования ИИ)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    timezone_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    page, limit = _parse_page_limit(request, default_limit=30, max_limit=100)
    data = _collect_site_data(timezone_id, visitor_id, endpoint="api/events")
    items = data.get("events") or []
    start = (page - 1) * limit
    payload = {
        "items": items[start:start + limit],
        "page": page,
        "limit": limit,
        "total": len(items),
        "has_more": (start + limit) < len(items),
    }
    return _json_response_with_etag(request, payload)

async def wishes_data(request: web.Request) -> web.Response:

    """Отдаёт только желания партнёра (для отладки и возможного использования ИИ)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    timezone_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    page, limit = _parse_page_limit(request, default_limit=20, max_limit=200)
    data = _collect_site_data(timezone_id, visitor_id, endpoint="api/wishes")
    wishes_obj = data.get("wishes") or {}
    wishes = wishes_obj.get("partner") or []
    start = (page - 1) * limit
    payload = {
        "items": wishes[start:start + limit],
        "page": page,
        "limit": limit,
        "total": len(wishes),
        "has_more": (start + limit) < len(wishes),
    }
    return _json_response_with_etag(request, payload)


async def profile_stats_data(request: web.Request) -> web.Response:
    """Отдаёт базовую персональную статистику в стиле /stats (без HTML)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    tz_id = (request.rel_url.query.get("tz") or "").strip() or None
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    stats = _build_profile_stats_for_visitor(visitor_id, tz_id)
    return _add_cors_headers(web.json_response({"ok": True, "profile_stats": stats}))


def _build_profile_stats_for_visitor(visitor_id: str | None, tz_id: str | None) -> dict:
    """Короткая сводка статистики профиля для AI и /api/profile_stats."""
    if not visitor_id:
        return {}
    _vis_uid = _visitor_to_user_id(visitor_id) if visitor_id else None
    if _vis_uid:
        tz_mode = db.get_user_setting(_vis_uid, "website_timezone_mode")
        if tz_mode == "profile" or tz_id == "__bot__":
            tz_id = db.get_user_setting(_vis_uid, "timezone") or None
    try:
        visits_summary = db.get_site_visits_summary(visitor_id) or {}
        total_visits = int(visits_summary.get("total", 0) or 0)
        days_span = int(visits_summary.get("days_span", 0) or 0)
        first_utc = visits_summary.get("first_utc")

        total_sec = int(db.get_visitor_total_site_seconds(visitor_id) or 0)
        streak_days = int(db.get_streak_days(visitor_id) or 0)
        photos_opened = int(db.get_photos_opened_count(visitor_id) or 0)
        longest_streak = int(db.get_longest_streak(visitor_id) or 0)
        most_visits = db.get_most_visits_in_day(visitor_id) or {}
        longest_day = db.get_longest_viewing_day(visitor_id) or {}

        fav_cat = db.get_favorite_category(visitor_id) or {}
        fav_cat_label = (fav_cat.get("label") or fav_cat.get("category") or "").strip()

        fav_time_slot = ""
        slot_counts = {"morning": 0, "day": 0, "evening": 0, "night": 0}
        for utc_str, visit_tz_id in db.get_visits_for_time_slots(visitor_id):
            try:
                dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                local_tz = ZoneInfo(visit_tz_id) if visit_tz_id else (ZoneInfo(tz_id) if tz_id else ZoneInfo("UTC"))
                h = dt_utc.astimezone(local_tz).hour
                if 6 <= h < 12:
                    slot_counts["morning"] += 1
                elif 12 <= h < 18:
                    slot_counts["day"] += 1
                elif 18 <= h < 23:
                    slot_counts["evening"] += 1
                else:
                    slot_counts["night"] += 1
            except Exception:
                slot_counts["night"] += 1
        if sum(slot_counts.values()) > 0:
            fav_time_slot = max(slot_counts, key=slot_counts.get)

        return {
            "total_visits": total_visits,
            "days_span": days_span,
            "first_visit_utc": first_utc,
            "streak_days": streak_days,
            "longest_streak_days": longest_streak,
            "photos_opened": photos_opened,
            "total_site_seconds": total_sec,
            "favorite_time_slot": fav_time_slot,
            "favorite_category_label": fav_cat_label,
            "most_visits_in_day": most_visits,
            "longest_viewing_day": longest_day,
        }
    except Exception:
        logger.exception("profile stats build failed for visitor=%r", visitor_id)
        return {}

def _collect_site_data(
    timezone_id: str | None,
    visitor_id: str | None = None,
    endpoint: str | None = None,
) -> dict:

    """Общий сбор данных для сайта (используется в /api/all и ИИ-компаньоне)."""

    # Для сайта visitor_id обязателен: без него не выдаём данные (fail-closed).
    if not visitor_id:
        return {
            "creator_id": None,
            "ksusha_id": None,
            "users": {},
            "stats": {},
            "stats_url": "/stats",
            "is_open": False,
            "memories": [],
            "events": [],
            "wishes": {"ksusha": []},
            "favorites": {"creator": {"items": []}, "ksyusha": {"items": []}},
            "user_settings": {"creator": {}, "ksyusha": {}},
            "last_added": None,
            "date_met": None,
            "profile_stats": {},
            "custom_categories": [],
        }

    # Определяем пару по visitor_id (для мульти-тенант)
    visitor_user_id = _visitor_to_user_id(visitor_id) if visitor_id else None
    if visitor_user_id:
        tz_mode = db.get_user_setting(visitor_user_id, "website_timezone_mode")
        if tz_mode == "profile" or timezone_id == "__bot__":
            timezone_id = db.get_user_setting(visitor_user_id, "timezone") or None
    couple = db.get_couple_by_user(visitor_user_id) if visitor_user_id else None

    # Фильтруем данные строго по паре — защита от межпарной утечки данных
    # Если visitor_id явно передан, но пара не найдена → изолируем (пустые данные)
    _visitor_unknown = bool(visitor_id and not couple)
    if couple:
        _couple_id = couple['id']
        _couple_user_ids = [uid for uid in (couple.get('user1_id'), couple.get('user2_id')) if uid]
    elif _visitor_unknown:
        # Неизвестный visitor — возвращаем гарантированно пустые данные
        _couple_id = -1
        _couple_user_ids = []
    else:
        _couple_id = -1
        _couple_user_ids = []

    endpoint = (endpoint or "api/all").strip().lower()
    wants_bootstrap = endpoint == "api/site_bootstrap"
    wants_all = endpoint == "api/all"
    wants_memories = wants_all or wants_bootstrap or endpoint in ("api/memories", "api/memories_recent") or endpoint.startswith("api/memory/")
    wants_events = wants_all or wants_bootstrap or endpoint in ("api/events", "api/events_recent") or endpoint.startswith("api/event/")
    wants_wishes = wants_all or wants_bootstrap or endpoint in ("api/wishes", "api/wishes_recent")
    wants_favorites = wants_all or endpoint == "api/favorites"
    wants_settings = wants_all or wants_bootstrap or endpoint == "api/user_settings"
    wants_stats = wants_all or wants_bootstrap or endpoint == "api/user_stats"
    wants_profile_stats = endpoint == "api/profile_stats"

    memories = db.get_recent_memories(limit=500, couple_id=_couple_id) if wants_memories else []
    events = db.get_scheduled_events(limit=200, user_ids=_couple_user_ids) if wants_events else []
    # Никогда не берём глобальную статистику для ИИ-компаньона:
    # она может включать агрегаты других пар (кросс-тенант утечка).
    stats = {}

    from utils import is_wishes_available

    is_open = is_wishes_available()

    base = (getattr(config, "SITE_DIRECT_URL", None) or "").strip().rstrip("/")

    stats_url = (base + "/stats") if base else "/stats"
    if visitor_id:
        _stats_sig = _sign_payload(f"visitor:{visitor_id}")
        stats_url += ("&" if "?" in stats_url else "?") + "v=" + visitor_id + "&sig=" + _stats_sig

    last_added = None

    last_item = db.get_last_added_item(couple_id=_couple_id, user_ids=_couple_user_ids) if (wants_all or wants_bootstrap) else None

    if last_item:

        kind, created_at_str, item_id = last_item

        ago_str = _format_ago_ru(created_at_str)

        if kind == "memory":

            m = db.get_memory(item_id)

            if m and not (m.privacy_type or "").strip():

                last_added = {"type": "memory", "item": _memory_to_public_dict(m, timezone_id), "ago_str": ago_str}

        elif kind == "event":

            e = db.get_scheduled_event(item_id)

            if e:

                last_added = {"type": "event", "item": _event_to_public_dict(e, timezone_id), "ago_str": ago_str}

        elif kind == "wish":

            w = db.get_wish(item_id)

            if w:

                last_added = {"type": "wish", "item": _wish_to_public_dict(w, timezone_id), "ago_str": ago_str}

    # Определяем creator и partner из пары (legacy-алиас ksusha оставляем для совместимости)
    if couple:
        creator_id = couple['user1_id']
        partner_id = couple['user2_id'] or db.get_ksusha_id()
    else:
        creator_id = None
        partner_id = None

    wishes_user = (db.get_user_wishes(visitor_user_id) if visitor_user_id else []) if wants_wishes else []
    wishes_partner = (db.get_user_wishes(partner_id) if partner_id else []) if wants_wishes else []

    # Статистика только в границах текущей пары.
    if wants_stats or wants_all:
        try:
            creator_stats = db.get_user_stats(creator_id) if creator_id else {}
        except Exception:
            creator_stats = {}
        try:
            partner_stats = db.get_user_stats(partner_id) if partner_id else {}
        except Exception:
            partner_stats = {}
        stats = {
            "total_memories": len(memories),
            "total_events": len(events),
            "total_wishes": len(wishes_partner),
            "creator": creator_stats,
            "partner": partner_stats,
        }

    # Избранное по ролям (creator / ksyusha)

    try:
        fav_creator_items, _ = db.get_user_favorites_paged(creator_id, page=1, per_page=200) if wants_favorites else ([], 0)
    except Exception:
        fav_creator_items = []

    try:
        fav_partner_items, _ = db.get_user_favorites_paged(partner_id, page=1, per_page=200) if wants_favorites else ([], 0)
    except Exception:
        fav_partner_items = []

    favorites = {

        "creator": {

            "items": fav_creator_items,

        },

        "ksyusha": {

            "items": fav_partner_items,

        },
        "partner": {
            "items": fav_partner_items,
        },

    }

    # Настройки пользователей в боте (из user_settings)

    try:
        settings_creator = db.get_user_all_settings(creator_id) if wants_settings else {}
    except Exception:
        settings_creator = {}

    try:
        settings_partner = db.get_user_all_settings(partner_id) if wants_settings else {}
    except Exception:
        settings_partner = {}

    user_settings = {

        "creator": settings_creator,

        "ksyusha": settings_partner,
        "partner": settings_partner,

    }

    # User display info from DB (first_name, last_name, username)
    creator_user = db.get_user(creator_id) or {} if creator_id else {}
    partner_user  = db.get_user(partner_id)  or {} if partner_id  else {}

    def _user_display(u: dict, fallback: str) -> str:
        fn = u.get("first_name") or ""
        ln = u.get("last_name")  or ""
        un = u.get("username")   or ""
        name = (fn + " " + ln).strip() or fn or ("@" + un if un else "") or fallback
        return name

    users_info = {
        "creator": {
            "user_id": creator_id,
            "first_name": creator_user.get("first_name") or "Создатель",
            "last_name":  creator_user.get("last_name")  or "",
            "username":   creator_user.get("username")   or "",
            "display":    db.get_display_name(creator_id, "Создатель"),
        },
        "ksyusha": {
            "user_id": partner_id,
            "first_name": partner_user.get("first_name") or "Партнёр",
            "last_name":  partner_user.get("last_name")  or "",
            "username":   partner_user.get("username")   or "",
            "display":    db.get_display_name(partner_id, "Партнёр"),
        },
        "partner": {
            "user_id": partner_id,
            "first_name": partner_user.get("first_name") or "Партнёр",
            "last_name":  partner_user.get("last_name")  or "",
            "username":   partner_user.get("username")   or "",
            "display":    db.get_display_name(partner_id, "Партнёр"),
        },
    }

    # Дата знакомства пары: берём из пары (если есть поле) или из global config как fallback.
    # Храним как строку ISO (YYYY-MM-DD) или None — чтобы dict был JSON-сериализуем.
    try:
        _date_met_raw = couple.get("date_met") if couple and couple.get("date_met") else None
    except Exception:
        _date_met_raw = None
    if _date_met_raw is None:
        _date_met_raw = None
    # Конвертируем date → str для JSON-сериализации
    if _date_met_raw is not None:
        try:
            _date_met = _date_met_raw.isoformat()  # "YYYY-MM-DD"
        except Exception:
            _date_met = str(_date_met_raw)
    else:
        _date_met = None

    profile_stats = {}
    if wants_profile_stats:
        profile_stats = _build_profile_stats_for_visitor(visitor_id, timezone_id)

    return {

        "creator_id": creator_id,

        "ksusha_id": partner_id,  # legacy key
        "partner_id": partner_id,

        "users": users_info,

        "stats": stats,

        "stats_url": stats_url,

        "is_open": is_open,

        "memories": [_memory_to_public_dict(m, timezone_id) for m in memories],

        "events": [_event_to_public_dict(e, timezone_id) for e in events],

        "wishes": {
            "user": [_wish_to_public_dict(w, timezone_id) for w in wishes_user],
            "partner": [_wish_to_public_dict(w, timezone_id) for w in wishes_partner],
        },

        "favorites": favorites,

        "user_settings": user_settings,

        "last_added": last_added,

        "date_met": _date_met,
        "profile_stats": profile_stats,

        "custom_categories": db.get_custom_categories(
            _couple_id if _couple_id is not None and _couple_id != -1 else None
        ),

    }

async def ai_companion(request: web.Request) -> web.Response:

    """HTTP-эндпоинт для ИИ-компаньона (чат для сайта)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    message = _pstr(payload.get("message")).strip()

    history_from_client = payload.get("history") or []

    tz = _pstr(payload.get("tz")).strip() or None

    visitor_id = _pstr(payload.get("visitor_id")).strip() or None

    site_role_raw = _pstr(payload.get("role")).strip().lower()

    # Роль на сайте: к кому сейчас «подключён» компаньон (компаньон или Создатель)

    site_role = _normalize_site_role(site_role_raw)

    # Если фронт не передал роль, пробуем достать её из устройства по visitor_id

    if not site_role and visitor_id:

        try:

            dev = db.get_device_by_visitor_id(visitor_id)

            if dev:

                role_from_device = _normalize_site_role((dev.get("role") or "").strip().lower())
                if role_from_device:
                    site_role = role_from_device

        except Exception:

            # Если что-то пошло не так — просто продолжаем без роли

            pass

    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "visitor_required"}, status=401))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))

    logger.info(

        "AI-companion HTTP request: message_len=%d, history_len=%d, tz=%s, visitor_id=%s, site_role=%s",
        len(message),

        len(history_from_client),

        tz,

        visitor_id,

        site_role,

    )

    if not message:

        return _add_cors_headers(web.json_response({"ok": False, "error": "empty_message"}, status=400))

    if len(message) > _AI_MAX_MSG_LEN:

        return _add_cors_headers(web.json_response({"ok": False, "error": "message_too_long"}, status=400))

    if _is_low_signal_ai_message(message):
        reply = _low_signal_ai_reply()
        try:
            db.add_companion_message(
                visitor_id=visitor_id,
                role="user",
                site_role=site_role,
                message=message,
            )
            db.add_companion_message(
                visitor_id=visitor_id,
                role="assistant",
                site_role=site_role,
                message=reply,
            )
        except Exception:
            logger.exception("Не удалось сохранить короткий ответ ИИ-компаньона в БД")
        return _add_cors_headers(web.json_response({"ok": True, "reply": reply, "suggestions": []}))

    _ai_rl_key = visitor_id or (request.remote or "")

    if not _ai_rate_check(_ai_rl_key):

        logger.warning("AI companion rate limit exceeded: key=%s", _ai_rl_key)

        return _add_cors_headers(web.json_response({"ok": False, "error": "rate_limited"}, status=429))

    _limit_key = _get_companion_limit_key(visitor_id)
    if _limit_key:
        from constants import COMPANION_LIMIT_BY_TIER
        _tier  = db.get_user_tier(_limit_key)
        _limit = COMPANION_LIMIT_BY_TIER.get(_tier, 50)
        _usage = db.check_and_record_ai_usage(_limit_key, _limit)
        if not _usage["allowed"]:
            _lmsg = _companion_limit_message()
            logger.info("AI companion limit exceeded (JSON): key=%s used=%s limit=%s", _limit_key, _usage.get("used"), _usage.get("limit"))
            try:
                db.add_companion_message(visitor_id=visitor_id, role="user", site_role=site_role, message=message)
                db.add_companion_message(visitor_id=visitor_id, role="assistant", site_role=site_role, message=_lmsg)
            except Exception:
                logger.exception("Не удалось сохранить limit-сообщение в историю")
            return _add_cors_headers(web.json_response({"ok": True, "reply": _lmsg, "suggestions": []}))
    else:
        _limit_key = None



    # Всегда сохраняем текущее сообщение пользователя в историю компаньона.

    # Если visitor_id нет, оно всё равно сохранится (visitor_id = NULL), но историю

    # для конкретного пользователя тогда берём с клиента.

    try:

        db.add_companion_message(

            visitor_id=visitor_id,

            role="user",

            site_role=site_role,

            message=message,

        )

    except Exception:

        logger.exception("Не удалось сохранить пользовательское сообщение ИИ-компаньона в БД")

    def _normalize_history(raw_history):
        cleaned = []
        if not isinstance(raw_history, list):
            return cleaned
        for item in raw_history[-100:]:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or "").strip()
            content = (item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                cleaned.append({"role": role, "content": content[:1200]})
        return cleaned

    # История для ИИ: если есть visitor_id, берём диалог из БД (источник истины).
    history_for_ai = _normalize_history(history_from_client)

    if visitor_id:

        try:

            raw_history = db.get_companion_history(visitor_id)

        except Exception:

            logger.exception("Не удалось получить историю ИИ-компаньона из БД, использую history с клиента")

            raw_history = []

        if raw_history:

            # Последняя запись — только что добавленное сообщение пользователя; его передаём отдельно как user_message

            prev = raw_history[:-1]

        else:

            prev = []

        history_items = []

        for h in prev:

            role = (h.get("role") or "").strip()

            content = (h.get("content") or "").strip()

            if role in ("user", "assistant") and content:

                history_items.append({"role": role, "content": content})

        # В БД хранится вся переписка, но в модель отправляем последние 100 сообщений (50 ходов) для сохранения контекста
        history_for_ai = _normalize_history(history_items[-100:])

    from api import ask_companion, route_companion_request

    try:

        # ask_companion синхронный; выносим в отдельный поток чтобы не блокировать event loop

        import asyncio

        loop = asyncio.get_running_loop()

        extra = {
            "timezone_id": tz or "",
            "ip": request.remote or "",
            "site_role": site_role or "",
        }
        routing = await loop.run_in_executor(
            None,
            lambda: route_companion_request(message, history_for_ai, extra),
        )
        if routing.get("needs_data"):
            endpoint = (routing.get("endpoint") or "api/all").strip().lower()
            site_data = _collect_site_data(tz, visitor_id, endpoint=endpoint)
            logger.info(
                "AI-companion site data collected for %s: memories=%d, events=%d, wishes=%d",
                endpoint,
                len(site_data.get("memories") or []),
                len(site_data.get("events") or []),
                len(
                    (site_data.get("wishes") or {}).get("partner")
                    or (site_data.get("wishes") or {}).get("ksyusha")
                    or (site_data.get("wishes") or {}).get("ksusha")
                    or []
                ),
            )
            reply, suggestions = await loop.run_in_executor(
                None,
                lambda: ask_companion(
                    user_message=message,
                    history=history_for_ai,
                    all_data=site_data,
                    extra={**extra, "routing": routing},
                ),
            )
        else:
            reply = (routing.get("reply") or "Не смог ответить, попробуй ещё раз").strip()
            suggestions = routing.get("suggestions") or []

        logger.info(

            "AI-companion success: reply_len=%d, suggestions=%s",

            len(reply or ""),

            suggestions,

        )

    except Exception:

        logger.exception("Ошибка при обращении к ИИ-компаньону")

        if _limit_key:
            try:
                db.decrement_ai_usage(_limit_key)
            except Exception:
                logger.exception("Не удалось откатить счётчик лимита (JSON)")

        return _add_cors_headers(web.json_response({"ok": False, "error": "ai_failed"}, status=502))


    # Сохраняем ответ ИИ в историю диалога (вся переписка на стороне сервера)

    try:

        db.add_companion_message(

            visitor_id=visitor_id,

            role="assistant",

            site_role=site_role,

            message=reply,

        )

    except Exception:

        logger.exception("Не удалось сохранить ответ ИИ-компаньона в БД")

    resp = web.json_response(

        {

            "ok": True,

            "reply": reply,

            "suggestions": suggestions,

        }

    )

    return _add_cors_headers(resp)

async def ai_companion_stream(request: web.Request) -> web.Response:

    """

    Стриминговый эндпоинт ИИ-компаньона.

    Отдаёт SSE (text/event-stream):

      data: <токен текста>\n\n

      ...

      data: {"__suggestions__": [...]}\n\n

      data: [DONE]\n\n

    """

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    message     = _pstr(payload.get("message")).strip()

    history_from_client = payload.get("history") or []

    tz          = _pstr(payload.get("tz")).strip() or None

    visitor_id  = _pstr(payload.get("visitor_id")).strip() or None

    site_role_raw = _pstr(payload.get("role")).strip().lower()

    site_role = _normalize_site_role(site_role_raw)

    # Пробуем получить роль из устройства если не передана

    if not site_role and visitor_id:

        try:

            dev = db.get_device_by_visitor_id(visitor_id)

            if dev:

                role_from_device = _normalize_site_role((dev.get("role") or "").strip().lower())
                if role_from_device:
                    site_role = role_from_device

        except Exception as e:
            logger.debug("Не удалось определить роль по visitor_id=%s: %s", visitor_id, e)

    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "visitor_required"}, status=401))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))

    if not message:

        return _add_cors_headers(web.json_response({"ok": False, "error": "empty_message"}, status=400))

    if len(message) > _AI_MAX_MSG_LEN:

        return _add_cors_headers(web.json_response({"ok": False, "error": "message_too_long"}, status=400))

    if _is_low_signal_ai_message(message):
        quick_reply = _low_signal_ai_reply()
        try:
            db.add_companion_message(
                visitor_id=visitor_id,
                role="user",
                site_role=site_role,
                message=message,
            )
            db.add_companion_message(
                visitor_id=visitor_id,
                role="assistant",
                site_role=site_role,
                message=quick_reply,
            )
        except Exception:
            logger.exception("Не удалось сохранить короткий ответ ИИ-компаньона в БД (stream)")

        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream; charset=utf-8"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["X-Accel-Buffering"] = "no"
        response.headers["X-Content-Type-Options"] = "nosniff"
        _add_cors_headers(response)
        try:
            await response.prepare(request)
            await response.write(f"data: {quick_reply}\n\n".encode("utf-8"))
            await response.write(b"data: [DONE]\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            logger.debug("SSE client disconnected before low-signal stream start")
            return response
        try:
            await response.write_eof()
        except (ConnectionResetError, RuntimeError):
            logger.debug("SSE already closed before write_eof (low signal)")
        return response

    _ai_rl_key = visitor_id or (request.remote or "")

    if not _ai_rate_check(_ai_rl_key):

        logger.warning("AI companion (stream) rate limit exceeded: key=%s", _ai_rl_key)

        return _add_cors_headers(web.json_response({"ok": False, "error": "rate_limited"}, status=429))

    _limit_key = _get_companion_limit_key(visitor_id)
    if _limit_key:
        from constants import COMPANION_LIMIT_BY_TIER
        _tier  = db.get_user_tier(_limit_key)
        _limit = COMPANION_LIMIT_BY_TIER.get(_tier, 50)
        _usage = db.check_and_record_ai_usage(_limit_key, _limit)
        if not _usage["allowed"]:
            _lmsg = _companion_limit_message()
            logger.info("AI companion limit exceeded (stream): key=%s used=%s limit=%s", _limit_key, _usage.get("used"), _usage.get("limit"))
            try:
                db.add_companion_message(visitor_id=visitor_id, role="user", site_role=site_role, message=message)
                db.add_companion_message(visitor_id=visitor_id, role="assistant", site_role=site_role, message=_lmsg)
            except Exception:
                logger.exception("Не удалось сохранить limit-сообщение в историю (stream)")
            _lmsg_safe = _lmsg.replace("\n", "\\n")
            import json as _json_limit
            _limit_resp = web.StreamResponse()
            _limit_resp.headers["Content-Type"] = "text/event-stream; charset=utf-8"
            _limit_resp.headers["Cache-Control"] = "no-cache"
            _limit_resp.headers["Connection"] = "keep-alive"
            _add_cors_headers(_limit_resp)
            try:
                await _limit_resp.prepare(request)
                await _limit_resp.write(f"data: {_lmsg_safe}\n\n".encode("utf-8"))
                await _limit_resp.write(
                    ("event: suggestions\ndata: " + _json_limit.dumps({"suggestions": []}) + "\n\n").encode("utf-8")
                )
                await _limit_resp.write(b"data: [DONE]\n\n")
                await _limit_resp.write_eof()
            except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
                pass
            return _limit_resp
    else:
        _limit_key = None



    logger.info(

        "AI-companion-stream request: message_len=%d, visitor_id=%s, site_role=%s",
        len(message), visitor_id, site_role,

    )

    # Сохраняем сообщение пользователя в БД

    try:

        db.add_companion_message(visitor_id=visitor_id, role="user", site_role=site_role, message=message)

    except Exception:

        logger.exception("Не удалось сохранить сообщение пользователя (stream)")

    def _normalize_history(raw_history):
        cleaned = []
        if not isinstance(raw_history, list):
            return cleaned
        for item in raw_history[-100:]:
            if not isinstance(item, dict):
                continue
            role = (item.get("role") or "").strip()
            content = (item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                cleaned.append({"role": role, "content": content[:1200]})
        return cleaned

    # Собираем историю из БД
    history_for_ai = _normalize_history(history_from_client)

    if visitor_id:

        try:

            raw_history = db.get_companion_history(visitor_id)

            prev = raw_history[:-1] if raw_history else []

            history_items = [

                {"role": (h.get("role") or "").strip(), "content": (h.get("content") or "").strip()}

                for h in prev

                if (h.get("role") or "").strip() in ("user", "assistant") and (h.get("content") or "").strip()

            ]

            # В модель отправляем последние 100 сообщений (50 ходов) для сохранения контекста
            history_for_ai = _normalize_history(history_items[-100:])

        except Exception:

            logger.exception("Не удалось получить историю из БД (stream)")

    # Готовим SSE-ответ сразу, до тяжёлой подготовки данных.
    # Это снижает риск разрыва клиентом/прокси из-за долгого time-to-first-byte.
    response = web.StreamResponse()

    response.headers["Content-Type"]    = "text/event-stream; charset=utf-8"

    response.headers["Cache-Control"]   = "no-cache"

    response.headers["Connection"]      = "keep-alive"

    response.headers["X-Accel-Buffering"]   = "no"    # nginx

    response.headers["X-Content-Type-Options"] = "nosniff"

    _add_cors_headers(response)
    try:
        await response.prepare(request)
    except (ConnectionResetError, asyncio.CancelledError):
        logger.debug("SSE client disconnected before stream prepare")
        return response

    # Padding 2KB — пробивает буфер cloudflared/nginx
    try:
        await response.write(b": " + b"x" * 2048 + b"\n\n")
        await response.drain()
    except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
        logger.debug("SSE client disconnected during stream prelude")
        return response

    extra = {"timezone_id": tz or "", "ip": request.remote or "", "site_role": site_role or ""}
    from api import ask_companion_stream, route_companion_request
    try:
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        routing = await loop.run_in_executor(
            None,
            lambda: route_companion_request(message, history_for_ai, extra, router_timeout_seconds=40),
        )
        endpoint = (routing.get("endpoint") or "api/all").strip().lower()
        try:
            site_data = _collect_site_data(tz, visitor_id, endpoint=endpoint) if routing.get("needs_data") else {}
        except Exception:
            logger.exception("AI-companion-stream: collect_site_data failed for endpoint=%s, using empty payload", endpoint)
            site_data = {}
    except Exception:
        logger.exception("AI-companion-stream: router precheck failed, fallback to api/all")
        routing = {"needs_data": True, "reply": "", "suggestions": []}
        try:
            site_data = _collect_site_data(tz, visitor_id, endpoint="api/all")
        except Exception:
            logger.exception("AI-companion-stream: fallback collect_site_data(api/all) failed, using empty payload")
            site_data = {}

    full_reply   = ""

    suggestions  = []

    import json as _json, asyncio, queue, threading

    loop = asyncio.get_running_loop()

    q: queue.Queue = queue.Queue()

    def _run_stream():

        try:
            if not routing.get("needs_data"):
                q.put((routing.get("reply") or "Не смог ответить, попробуй ещё раз").strip())
                q.put(_json.dumps({"__suggestions__": routing.get("suggestions") or []}, ensure_ascii=False))
                return

            for chunk in ask_companion_stream(

                user_message=message,

                history=history_for_ai,

                all_data=site_data,

                extra={**extra, "routing": routing},

            ):

                q.put(chunk)

        except Exception:
            logger.exception("AI-companion-stream worker failed")
            q.put("__worker_error__")

        finally:

            q.put(None)

    thread = threading.Thread(target=_run_stream, daemon=True)

    thread.start()

    try:

        keepalive_interval = 0  # счётчик для периодических ping

        while True:

            try:

                # Ждём чанк максимум 1 секунду, потом шлём ping чтобы соединение не рвалось

                chunk = await asyncio.wait_for(

                    loop.run_in_executor(None, lambda: q.get(timeout=1)),

                    timeout=2,

                )

            except (asyncio.TimeoutError, Exception):

                # Таймаут — шлём keep-alive комментарий и ждём дальше

                keepalive_interval += 1

                if keepalive_interval % 2 == 0:

                    await response.write(b": ping\n\n")

                if not thread.is_alive() and q.empty():

                    break

                continue

            if chunk is None:

                break

            if chunk == "__worker_error__":
                if _limit_key:
                    try:
                        db.decrement_ai_usage(_limit_key)
                    except Exception:
                        logger.exception("Не удалось откатить счётчик лимита (stream worker_error)")
                await response.write(b"data: [ERROR] ai_failed\n\n")
                break

            # Подсказки

            if chunk.startswith('{"__suggestions__"'):

                try:

                    meta = _json.loads(chunk)

                    suggestions = meta.get("__suggestions__") or []

                except Exception as e:
                    logger.debug("Не удалось распарсить suggestions chunk: %s", e)

                await response.write(

                    f"event: suggestions\ndata: {_json.dumps({'suggestions': suggestions}, ensure_ascii=False)}\n\n".encode("utf-8")

                )

            else:

                full_reply += chunk

                safe = chunk.replace("\n", "\\n")

                await response.write(f"data: {safe}\n\n".encode("utf-8"))
                await response.drain()

        await response.write(b"data: [DONE]\n\n")
        await response.drain()

    except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
        logger.debug("SSE client disconnected during streaming loop")
    except Exception:

        logger.exception("Ошибка в стриминговом ИИ-компаньоне")

        if _limit_key and not full_reply:
            try:
                db.decrement_ai_usage(_limit_key)
            except Exception:
                logger.exception("Не удалось откатить счётчик лимита (stream exception)")

        try:
            await response.write(b"data: [ERROR]\n\ndata: [DONE]\n\n")
        except Exception as e:
            logger.debug("Не удалось отправить [ERROR]/[DONE] в SSE: %s", e)

    # Сохраняем полный ответ ИИ в БД

    if full_reply:

        # Убираем [SUGGESTIONS:...] если вдруг остался в тексте

        import re as _re

        # Сначала очищаем от XML-тегов <suggestions>...</suggestions>
        clean_reply = _re.sub(r"<suggestions>.*?</suggestions>", "", full_reply, flags=_re.IGNORECASE | _re.DOTALL).strip()
        # Убираем возможные незакрытые теги <suggestions>
        clean_reply = _re.sub(r"<suggestions>.*", "", clean_reply, flags=_re.IGNORECASE | _re.DOTALL).strip()
        # Убираем bracket-синтаксис (включая кириллическую С в СUGGESTIONS)
        clean_reply = _re.sub(r"\[(?:SUGGESTIONS|СУПЕР|ПОДСКАЗКИ|ПРЕДЛОЖЕНИЯ|SUGGEST|СUGGESTIONS):.*?\]", "", clean_reply, flags=_re.IGNORECASE | _re.DOTALL).strip()

        try:

            saved_msg_id = db.add_companion_message(visitor_id=visitor_id, role="assistant", site_role=site_role, message=clean_reply)
            # Также сохраняем сообщение пользователя — возвращаем msg_id ответа ИИ фронту
            if saved_msg_id:
                import json as _json
                try:
                    await response.write(
                        ("data: " + _json.dumps({"msg_id": saved_msg_id}) + "\n\n").encode()
                    )
                except (ConnectionResetError, RuntimeError):
                    logger.debug("SSE closed before msg_id delivery")

        except Exception:

            logger.exception("Не удалось сохранить ответ ИИ в БД (stream)")

    logger.info("AI-companion-stream done: reply_len=%d, suggestions=%s", len(full_reply), suggestions)

    try:
        await response.write_eof()
    except (ConnectionResetError, RuntimeError):
        logger.debug("SSE already closed before write_eof")

    return response

async def log_visit(request: web.Request) -> web.Response:

    """Логирует визит на сайт и уведомляет создателя в Telegram."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    is_new = bool(payload.get("is_new"))

    user_agent_raw = payload.get("user_agent") or request.headers.get("User-Agent", "")

    theme = payload.get("theme") or ""

    timezone = _pstr(payload.get("timezone")).strip()

    screen = payload.get("screen") or {}

    language = _pstr(payload.get("language")).strip()

    client_referrer = _pstr(payload.get("referrer")).strip()

    connection = payload.get("connection") or {}

    battery = payload.get("battery") or {}

    ua_hints = payload.get("ua_client_hints") or {}

    # IP, который видит сервер (может быть IP VPN)

    ip_server = request.headers.get("X-Forwarded-For", "")

    if ip_server:

        ip_server = ip_server.split(",")[0].strip()

    if not ip_server:

        ip_server = request.remote or ""

    # IP, который видит сервер (может быть IP VPN)

    real_ip = ""

    # Геолокация по IP сервера (скорее IP VPN/провайдера)

    location_str = "Не удалось определить"

    coords_str = ""

    isp_str = ""

    country = ""

    city = ""

    geo_ip = real_ip or ip_server

    if geo_ip:

        try:

            async with aiohttp.ClientSession() as session:

                async with session.get(f"http://ip-api.com/json/{geo_ip}?lang=ru") as resp:

                    if resp.status == 200:

                        data = await resp.json()

                        if data.get("status") == "success":

                            country = data.get("country") or ""

                            city = data.get("city") or ""

                            lat = data.get("lat")

                            lon = data.get("lon")

                            isp = data.get("isp") or data.get("org") or ""

                            parts = [p for p in [country, city] if p]

                            if parts:

                                location_str = ", ".join(parts)

                            if isp:

                                isp_str = isp

                            if lat is not None and lon is not None:

                                coords_str = f"{lat}, {lon}"

        except Exception as e:
            logger.debug("Geo lookup failed for ip=%s: %s", geo_ip, e)

    # Человекочитаемое описание устройства

    user_agent_pretty = _format_device_info(user_agent_raw, ua_hints)

    # Экран

    screen_str = ""

    pixel_ratio = None

    if isinstance(screen, dict):

        w = screen.get("width")

        h = screen.get("height")

        if w and h:

            screen_str = f"{w}x{h}"

        pixel_ratio = screen.get("pixel_ratio")

    # Заголовки

    referer_header = request.headers.get("Referer", "")

    accept_language = request.headers.get("Accept-Language", "")

    referrer = client_referrer or referer_header

    lang_display = language or accept_language

    visitor_id = _pstr(payload.get("visitor_id")).strip() or None
    visitor_user_id = _visitor_to_user_id(visitor_id) if visitor_id else None
    if visitor_user_id and timezone:
        timezone = _resolve_user_timezone(visitor_user_id, timezone)

    # Собираем данные для единого формата сообщения (используется и для кнопки «Обновить»)

    conn_label = ""

    downlink = None

    rtt_ms = None

    save_data = False

    if isinstance(connection, dict) and connection:

        eff_type = connection.get("effective_type") or connection.get("type") or ""

        conn_label = eff_type.upper() if eff_type else ""

        downlink = connection.get("downlink_mbps")

        rtt_ms = connection.get("rtt_ms")

        save_data = bool(connection.get("save_data"))

    screen_real_w = screen_real_h = None

    if isinstance(screen, dict) and screen.get("width") and screen.get("height") and pixel_ratio:

        try:

            screen_real_w = int(screen["width"] * float(pixel_ratio))

            screen_real_h = int(screen["height"] * float(pixel_ratio))

        except (TypeError, ValueError):

            pass

    arch_str = model_h = device_type_h = None

    if isinstance(ua_hints, dict) and ua_hints:

        ar = ua_hints.get("architecture")

        bt = ua_hints.get("bitness")

        if ar or bt:

            arch_str = f"{ar or ''}{bt or ''}-bit".strip().rstrip("-bit") or (ar or "")

        model_h = (ua_hints.get("model") or "").strip() or None

        if ua_hints.get("mobile") is True:

            device_type_h = "Смартфон (Mobile)"

        elif ua_hints.get("mobile") is False:

            device_type_h = "Desktop"

    role_raw = _pstr(payload.get("role")).strip().lower()

    if role_raw == "ksyusha":

        role_label = "Партнёр"

    elif role_raw == "creator":

        role_label = "Создатель"

    else:

        role_label = None

    visit_data = {

        "prefix": "Новый вход" if is_new else "Вход",

        "timezone": timezone or "не указан",

        "location_str": location_str,

        "isp_str": isp_str or None,

        "coords_str": coords_str or None,

        "ua_pretty": user_agent_pretty,

        "lang_display": lang_display or None,

        "screen_str": screen_str or None,

        "pixel_ratio": pixel_ratio,

        "screen_real_w": screen_real_w,

        "screen_real_h": screen_real_h,

        "battery_level": battery.get("level_percent") if isinstance(battery, dict) and battery else None,

        "battery_charging": battery.get("charging") if isinstance(battery, dict) and battery else None,

        "conn_label": conn_label or None,

        "downlink": downlink,

        "rtt_ms": rtt_ms,

        "save_data": save_data,

        "real_ip": real_ip or None,

        "ip_server": ip_server or None,

        "referrer": referrer or None,

        "theme": theme or None,

        "architecture": arch_str,

        "model": model_h,

        "device_type": device_type_h,

        "role": role_raw or None,

        "role_label": role_label,

    }

    text = format_visit_telegram_message(visit_data)

    def _norm_ip(value: str | None) -> str:
        raw = (value or "").strip().lower()
        if raw.startswith("http://"):
            raw = raw[7:]
        elif raw.startswith("https://"):
            raw = raw[8:]
        if "/" in raw:
            raw = raw.split("/", 1)[0]
        if ":" in raw and raw.count(":") == 1:
            raw = raw.split(":", 1)[0]
        return raw

    ip_real_n = _norm_ip(real_ip)
    ip_server_n = _norm_ip(ip_server)
    is_local_system_visit = any(
        ip in {"127.0.0.1", "localhost", "::1"}
        for ip in (ip_real_n, ip_server_n)
        if ip
    )

    visitor_id = _pstr(payload.get("visitor_id")).strip() or None

    # Сохраняем визит в базу для последующей статистики (с visitor_id для персональной статистики)

    if not is_local_system_visit:
        try:
            db.add_site_visit(
                timezone_id=timezone or None,
                ip=real_ip or ip_server or "",
                ua_pretty=user_agent_pretty or "",
                visitor_id=visitor_id,
            )
        except Exception:
            pass

    if (not is_local_system_visit) and db.get_setting("test_version") == "1":

        try:

            v = int(db.get_setting("maintenance_visits") or "0") + 1

            db.set_setting("maintenance_visits", str(v))

        except Exception:

            pass

    # Устройство: создаём или обновляем запись по visitor_id для админки «Устройства»

    if visitor_id and (not is_local_system_visit):

        try:

            os_name = os_ver = browser_ver = arch = device_type = model = None

            if isinstance(ua_hints, dict) and ua_hints:

                os_name = (ua_hints.get("platform") or "").strip() or None

                os_ver = (ua_hints.get("platformVersion") or "").strip() or None

                model = (ua_hints.get("model") or "").strip() or None

                arch_raw = (ua_hints.get("architecture") or "").strip()

                bitness = (ua_hints.get("bitness") or "").strip()

                if arch_raw and bitness:

                    arch = f"{arch_raw}{bitness}-bit"

                elif arch_raw:

                    arch = arch_raw

                if ua_hints.get("mobile") is True:

                    device_type = "Смартфон (Mobile)"

                elif ua_hints.get("mobile") is False:

                    device_type = "Desktop"

                fvl = ua_hints.get("fullVersionList") or []

                if isinstance(fvl, list) and fvl and isinstance(fvl[0], dict):

                    brand = fvl[0].get("brand") or ""

                    ver = fvl[0].get("version") or ""

                    browser_ver = f"{brand} {ver}".strip() or None

            screen_w = screen.get("width") if isinstance(screen, dict) else None

            screen_h = screen.get("height") if isinstance(screen, dict) else None

            pr = screen.get("pixel_ratio") if isinstance(screen, dict) else None

            bat_level = battery.get("level_percent") if isinstance(battery, dict) and battery else None

            bat_charging = battery.get("charging") if isinstance(battery, dict) and battery else None

            conn_type = None

            if isinstance(connection, dict) and connection:

                eff = connection.get("effective_type") or connection.get("type")

                conn_type = (eff or "").upper() if eff else None

            downlink = connection.get("downlink_mbps") if isinstance(connection, dict) else None

            rtt = connection.get("rtt_ms") if isinstance(connection, dict) else None

            db.add_or_update_device(

                visitor_id,

                ua_pretty=user_agent_pretty or None,

                country=country or None,

                city=city or None,

                isp=isp_str or None,

                coords=coords_str or None,

                timezone_id=timezone or None,

                os=os_name,

                os_version=os_ver,

                browser=browser_ver,

                browser_version=None,

                architecture=arch,

                device_type=device_type,

                model=model,

                screen_w=screen_w,

                screen_h=screen_h,

                pixel_ratio=pr,

                language=lang_display or None,

                connection_type=conn_type,

                downlink_mbps=downlink,

                rtt_ms=rtt,

                battery_level=bat_level,

                battery_charging=bat_charging,

                ip_server=ip_server or None,

                ip_webrtc=real_ip or None,

                referrer=referrer or None,

                theme=theme or None,

                role=role_raw or None,

            )

        except Exception:

            pass

    # Авто-сохранение часового пояса из браузера в user_settings
    visitor_user_id = _visitor_to_user_id(visitor_id) if visitor_id else None
    if visitor_user_id and timezone:
        try:
            # Website must NEVER overwrite timezone/timezone_display.
            pass
        except Exception:
            pass

    # Уведомление о первом входе — отправляем самому пользователю однократно
    if visitor_user_id and (not is_local_system_visit):
        try:
            already_notified = db.get_user_setting(visitor_user_id, 'site_registered_notified')
            if not already_notified:
                # Базовый текст и клавиатура
                reg_text = "✅ <b>Успешная регистрация через сайт</b>"
                reg_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📋 Главное меню", callback_data="back_to_main")]
                ])

                # Проверяем разницу часовых поясов с партнёром
                my_tz = timezone  # из запроса visit
                if my_tz:
                    partner_id = db.get_partner_id(visitor_user_id)
                    if partner_id:
                        partner_tz = db.get_user_setting(partner_id, "timezone")
                        if partner_tz and my_tz != partner_tz:
                            partner_name = db.get_display_name(
                                partner_id, fallback="Партнёр"
                            )
                            # Вычисляем разницу часовых поясов
                            tz_diff_str = _calc_tz_diff(my_tz, partner_tz, partner_name)
                            reg_text += (
                                f"\n\n🌍 Но наша система распознала, что вы находитесь "
                                f"в <b>разных часовых поясах</b>\n"
                                f"Ты в <code>{_html_module.escape(my_tz)}</code>, "
                                f"а {_html_module.escape(partner_name)} в <code>{_html_module.escape(partner_tz)}</code>"
                                f"{_html_module.escape(tz_diff_str)}\n\n"
                                f"Это верно?"
                            )
                            reg_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="✅ Да, верно",
                                        callback_data="tz_diff_yes",
                                    ),
                                    InlineKeyboardButton(
                                        text="✏️ Нет, изменить",
                                        callback_data="tz_diff_no",
                                    ),
                                ]
                            ])

                _reg_bot = Bot(token=config.BOT_TOKEN)
                try:
                    await _reg_bot.send_message(
                        chat_id=visitor_user_id,
                        text=reg_text,
                        reply_markup=reg_keyboard,
                        parse_mode="HTML",
                    )
                    db.set_user_setting(visitor_user_id, 'site_registered_notified', '1')
                finally:
                    await _reg_bot.session.close()
        except Exception as e:
            logger.warning("Не удалось отправить сообщение о регистрации через сайт user_id=%s: %s", visitor_user_id, e)

    # Уведомление о входе отправляем создателю пары (или CREATOR_ID по умолчанию)

    try:
        if is_local_system_visit:
            resp = web.Response(status=204)
            return _add_cors_headers(resp)
        # Уведомления о входе на сайт отправляем только глобальному создателю бота.
        notify_target = config.CREATOR_ID

        if notify_target and db.are_notifications_enabled(notify_target) and db.is_category_notif_enabled(notify_target, "site_visits"):

            bot = Bot(token=config.BOT_TOKEN)

            keyboard = InlineKeyboardMarkup(

                inline_keyboard=[

                    [InlineKeyboardButton(text="🔄 Обновить", callback_data="site_visit_refresh")]

                ]

            )

            await bot.send_message(

                chat_id=notify_target,

                text=text,

                parse_mode=ParseMode.HTML,

                reply_markup=keyboard,

            )

            await bot.session.close()

    except Exception:

        pass

    resp = web.Response(status=204)

    return _add_cors_headers(resp)

async def wish_update_status(request: web.Request) -> web.Response:
    """Обновляет статус желания. Доступно только участникам пары."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))
    wish_id_raw = payload.get("wish_id")
    status      = _pstr(payload.get("status")).strip()
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    wish_id = _safe_int(wish_id_raw)
    allowed_statuses = {"created", "in_progress", "done"}
    if not wish_id or not status or not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid params"}, status=400))
    if status not in allowed_statuses:
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid status"}, status=400))
    # Проверяем, что visitor является участником пары, которой принадлежит желание
    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    wish = db.get_wish(wish_id)
    if not wish:
        return _add_cors_headers(web.json_response({"ok": False, "error": "not found"}, status=404))
    couple = db.get_couple_by_user(user_id)
    if couple:
        couple_user_ids = {couple.get('user1_id'), couple.get('user2_id')} - {None}
        if wish.user_id not in couple_user_ids:
            return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    ok = db.update_wish_status(wish_id, status)
    if ok:
        asyncio.create_task(broadcast_wish_status(wish_id, status))
    return _add_cors_headers(web.json_response({"ok": ok}))


async def ai_message_reaction(request: web.Request) -> web.Response:
    """Сохраняет или убирает реакцию на сообщение ИИ."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))
    msg_id_raw = payload.get("msg_id")
    visitor_id = _pstr(payload.get("visitor_id")).strip()
    reaction   = payload.get("reaction") or None  # None = убрать реакцию
    msg_id = _safe_int(msg_id_raw)
    if not msg_id or not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "missing fields"}, status=400))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))
    try:
        with db._get_connection() as conn:
            conn.execute(
                "UPDATE companion_messages SET reaction = ? WHERE id = ? AND visitor_id = ?",
                (reaction, msg_id, visitor_id)
            )
            conn.commit()
        return _add_cors_headers(web.json_response({"ok": True}))
    except Exception as e:
        logger.exception("Ошибка ai_message_reaction: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))


async def ai_message_pin(request: web.Request) -> web.Response:
    """Закрепляет или открепляет сообщение ИИ."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))
    msg_id_raw = payload.get("msg_id")
    visitor_id = _pstr(payload.get("visitor_id")).strip()
    pinned     = bool(payload.get("pinned", False))
    msg_id = _safe_int(msg_id_raw)
    if not msg_id or not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "missing fields"}, status=400))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))
    try:
        with db._get_connection() as conn:
            if pinned:
                conn.execute(
                    "UPDATE companion_messages SET is_pinned = 0 WHERE visitor_id = ?",
                    (visitor_id,)
                )
            conn.execute(
                "UPDATE companion_messages SET is_pinned = ? WHERE id = ? AND visitor_id = ?",
                (1 if pinned else 0, msg_id, visitor_id)
            )
            conn.commit()
        return _add_cors_headers(web.json_response({"ok": True}))
    except Exception as e:
        logger.exception("Ошибка ai_message_pin: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))


async def ai_companion_history_clear(request: web.Request) -> web.Response:
    """Удаляет всю историю чата с ИИ для visitor_id."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _pstr(payload.get("visitor_id")).strip()
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "no visitor_id"}))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))
    deleted = db.clear_companion_history(visitor_id)
    logger.info("AI history cleared: visitor_id=%r deleted=%d", visitor_id, deleted)
    return _add_cors_headers(web.json_response({"ok": True, "deleted": deleted}))


async def refresh_ai_session(request: web.Request) -> web.Response:
    """Генерирует новую AI сессию, если подпись посетителя верна."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    visitor_id = _pstr(payload.get("visitor_id")).strip()
    visitor_sig = (request.headers.get("X-Visitor-Signature") or "").strip()
    if not visitor_id or not visitor_sig:
        return _add_cors_headers(web.json_response({"ok": False, "error": "visitor_required"}, status=401))
    if not _verify_visitor_signature(visitor_id, visitor_sig):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))
    ai_session, ai_session_exp = _issue_ai_session(visitor_id)
    return _add_cors_headers(web.json_response({
        "ok": True,
        "ai_session": ai_session,
        "ai_session_exp": ai_session_exp,
    }))


async def ai_companion_history(request: web.Request) -> web.Response:
    """Возвращает историю диалога ИИ-компаньона для visitor_id."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    visitor_id = (request.rel_url.query.get("visitor_id") or "").strip() or None
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": True, "history": []}))
    if not _check_ai_request_auth(request, visitor_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized_ai_session"}, status=401))

    try:
        raw = db.get_companion_history(visitor_id, limit=40)
    except Exception:
        logger.exception("Не удалось получить историю ИИ-компаньона")
        return _add_cors_headers(web.json_response({"ok": False, "error": "db_error"}, status=500))

    history = []
    for h in raw:
        role = (h.get("role") or "").strip()
        # get_companion_history уже переименовывает message → content
        content = (h.get("content") or h.get("message") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        history.append({
            "id":        h.get("id"),
            "role":      role,
            "content":   content,
            "time":      h.get("created_at_utc") or h.get("time") or "",
            "reaction":  h.get("reaction") or None,
            "is_pinned": 1 if h.get("is_pinned") else 0,
        })
    return _add_cors_headers(web.json_response({"ok": True, "history": history}))




# ═══════════════════════════════════════════════════════════════
# ADMIN PANEL ENDPOINTS
# ═══════════════════════════════════════════════════════════════

async def admin_check(request: web.Request) -> web.Response:
    """Проверяет роль — только creator получает ok:true."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    is_creator = bool(user_id and db.is_creator(user_id))
    return _add_cors_headers(web.json_response({"ok": True, "is_creator": is_creator}))


async def admin_stats(request: web.Request) -> web.Response:
    """Сводная статистика для админ-панели."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    stats = db.get_admin_stats()
    partner_info = db.get_partner_info(
        partner_visitor_id="partner",
        partner_user_id=getattr(config, "KSUSHA_ID", 0) or 0,
    )
    # Все устройства (список)
    devices = db.get_devices_list()

    return _add_cors_headers(web.json_response({
        "ok": True,
        "stats": stats,
        "partner": partner_info,
        "ksusha": partner_info,  # legacy alias
        "devices": devices,
        "maintenance": db.get_setting("test_version") == "1",
        "maintenance_started_at": db.get_setting("maintenance_started_at") or None,
    }))


async def admin_health_metrics(request: web.Request) -> web.Response:
    """Runtime-метрики для прод-диагностики (только авторизованный участник пары)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    uptime_sec = max(0, int(time.time() - float(_metrics.get("started_at", time.time()))))
    lat_cnt = int(_metrics.get("latency_count", 0) or 0)
    lat_avg = round((_metrics.get("latency_ms_sum", 0.0) / lat_cnt), 2) if lat_cnt else 0.0
    payload = {
        "ok": True,
        "metrics": {
            "uptime_sec": uptime_sec,
            "total_requests": int(_metrics.get("total_requests", 0)),
            "status_2xx": int(_metrics.get("status_2xx", 0)),
            "status_3xx": int(_metrics.get("status_3xx", 0)),
            "status_4xx": int(_metrics.get("status_4xx", 0)),
            "status_5xx": int(_metrics.get("status_5xx", 0)),
            "rate_limited_429": int(_metrics.get("rate_limited", 0)),
            "forbidden_403": int(_metrics.get("forbidden", 0)),
            "internal_errors": int(_metrics.get("errors", 0)),
            "avg_latency_ms": lat_avg,
        },
    }
    return _add_cors_headers(web.json_response(payload))


async def api_version(request: web.Request) -> web.Response:
    """Возвращает текущую версию приложения и её описание (публичный эндпоинт)."""
    from app_version import get_version_metadata, get_git_commit
    version, description = get_version_metadata()
    git_commit = get_git_commit()
    return _add_cors_headers(web.json_response({
        "ok": True,
        "version": version,
        "description": description,
        "git_commit": git_commit,
    }))


async def admin_version_history(request: web.Request) -> web.Response:
    """Возвращает полную историю версий (только creator)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    history = db.get_version_history()
    return _add_cors_headers(web.json_response({"ok": True, "history": history}))


async def admin_action(request: web.Request) -> web.Response:
    """Выполняет действие из админ-панели (включить/выключить техперерыв и др.)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    action = _pstr(payload.get("action")).strip()

    if action == "maintenance_on":
        from datetime import datetime, timezone as tz_module
        now_utc = datetime.now(tz_module.utc).strftime("%Y-%m-%d %H:%M:%S")
        db.set_setting("test_version", "1")
        db.set_setting("maintenance_started_at", now_utc)
        db.set_setting("maintenance_visits", "0")
        try:
            import asyncio
            from http_api import broadcast_maintenance_on
            asyncio.create_task(broadcast_maintenance_on())
        except Exception:
            pass
        return _add_cors_headers(web.json_response({"ok": True, "action": "maintenance_on"}))

    elif action == "maintenance_off":
        db.set_setting("test_version", "0")
        db.set_setting("maintenance_visits", "0")
        try:
            import asyncio
            from http_api import broadcast_maintenance_off
            asyncio.create_task(broadcast_maintenance_off())
        except Exception:
            pass
        return _add_cors_headers(web.json_response({"ok": True, "action": "maintenance_off"}))

    else:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unknown_action"}))



_ADMIN_AUTH_GATE_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Admin Access</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#111;color:#eee}}
form{{background:#222;padding:2rem;border-radius:8px;display:flex;flex-direction:column;gap:1rem;min-width:280px}}
input{{padding:.6rem;border-radius:4px;border:1px solid #555;background:#333;color:#eee;font-size:1rem}}
button{{padding:.6rem;background:#2563eb;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:1rem}}
.err{{color:#f87171;font-size:.9rem}}</style></head>
<body><form method="GET"><h2>Admin</h2>
{error}<input type="password" name="key" placeholder="API Key" autofocus>
<button type="submit">Войти</button></form></body></html>"""

def _get_registered_couple(request: web.Request):
    """Возвращает пару (dict) если visitor_id найден и пара зарегистрирована, иначе None."""
    from database import db as _db
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return None
    uid = _visitor_to_user_id(visitor_id)
    if not uid:
        return None
    return _db.get_couple_by_user(uid)


def _check_admin_page_key(request: web.Request) -> bool:
    """Проверяет ключ доступа к /admin из query-параметра или cookie."""
    import hmac as _hmac
    secret = (getattr(config, "API_SECRET_KEY", "") or "").strip()
    if not secret:
        return False  # fail-closed: без секрета доступ к /admin запрещён
    provided_query = (request.rel_url.query.get("key") or "").strip()
    return _hmac.compare_digest(provided_query, secret)


def _has_admin_access(request: web.Request) -> bool:
    if _verify_admin_session((request.cookies.get("admin_session") or "").strip(), request) is not None:
        return True
    return _check_admin_page_key(request)

async def admin_page(request: web.Request) -> web.Response:
    """Отдаёт страницу /admin (admin.html). Серверная проверка ключа + cookie-сессия."""
    secret = (getattr(config, "API_SECRET_KEY", "") or "").strip()
    provided_key = (request.rel_url.query.get("key") or "").strip()

    if secret and not _has_admin_access(request):
        error_html = '<p class="err">Неверный ключ</p>' if "key" in request.rel_url.query else ""
        return web.Response(
            text=_ADMIN_AUTH_GATE_HTML.format(error=error_html),
            content_type="text/html", charset="utf-8", status=401
        )

    project_root = Path(__file__).resolve().parent
    admin_path = project_root / "admin.html"
    if not admin_path.exists():
        return web.Response(text="admin.html not found", status=404)
    try:
        html = admin_path.read_text(encoding="utf-8")
    except Exception:
        return web.Response(text="cannot read admin.html", status=500)
    # Не пробрасываем серверный API-ключ в клиентский HTML.
    html = html.replace("{{API_SECRET_KEY}}", "")
    response = web.Response(text=html, content_type="text/html", charset="utf-8")
    # Legacy key (если введён) конвертируем в безопасную admin-session.
    if secret and hmac.compare_digest(provided_key, secret):
        creator_uid = int(getattr(config, "CREATOR_ID", 0) or 0)
        if creator_uid > 0:
            admin_sess, _ = _issue_admin_session(creator_uid, request)
            response.set_cookie(
                "admin_session", admin_sess,
                httponly=True, samesite="Strict",
                max_age=12 * 3600,
                secure=_is_secure_request(request),
            )
    return response



# ──────────────────────────────────────────────────────────────────────────────
#  SKY PAGE — вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

# Словарь IANA timezone → данные города (координаты, названия на ru/de/en)
_TZ_CITY_INFO: dict = {
    "Europe/Moscow":      {"lat": 55.75,  "lon":  37.62,  "ru": "Москва",           "de": "Moskau",              "en": "Moscow"},
    "Asia/Bishkek":       {"lat": 42.87,  "lon":  74.59,  "ru": "Бишкек",           "de": "Bischkek",            "en": "Bishkek"},
    "Europe/Berlin":      {"lat": 52.52,  "lon":  13.40,  "ru": "Берлин",           "de": "Berlin",              "en": "Berlin"},
    "Europe/London":      {"lat": 51.51,  "lon":  -0.13,  "ru": "Лондон",           "de": "London",              "en": "London"},
    "Europe/Paris":       {"lat": 48.86,  "lon":   2.35,  "ru": "Париж",            "de": "Paris",               "en": "Paris"},
    "Europe/Warsaw":      {"lat": 52.23,  "lon":  21.01,  "ru": "Варшава",          "de": "Warschau",            "en": "Warsaw"},
    "Europe/Kiev":        {"lat": 50.45,  "lon":  30.52,  "ru": "Киев",             "de": "Kiew",                "en": "Kyiv"},
    "Europe/Kyiv":        {"lat": 50.45,  "lon":  30.52,  "ru": "Киев",             "de": "Kiew",                "en": "Kyiv"},
    "Europe/Istanbul":    {"lat": 41.01,  "lon":  28.97,  "ru": "Стамбул",          "de": "Istanbul",            "en": "Istanbul"},
    "Europe/Amsterdam":   {"lat": 52.37,  "lon":   4.90,  "ru": "Амстердам",        "de": "Amsterdam",           "en": "Amsterdam"},
    "Europe/Prague":      {"lat": 50.08,  "lon":  14.44,  "ru": "Прага",            "de": "Prag",                "en": "Prague"},
    "Europe/Vienna":      {"lat": 48.21,  "lon":  16.37,  "ru": "Вена",             "de": "Wien",                "en": "Vienna"},
    "Europe/Rome":        {"lat": 41.90,  "lon":  12.50,  "ru": "Рим",              "de": "Rom",                 "en": "Rome"},
    "Europe/Madrid":      {"lat": 40.42,  "lon":  -3.70,  "ru": "Мадрид",           "de": "Madrid",              "en": "Madrid"},
    "Europe/Athens":      {"lat": 37.98,  "lon":  23.73,  "ru": "Афины",            "de": "Athen",               "en": "Athens"},
    "Europe/Helsinki":    {"lat": 60.17,  "lon":  24.94,  "ru": "Хельсинки",        "de": "Helsinki",            "en": "Helsinki"},
    "Europe/Stockholm":   {"lat": 59.33,  "lon":  18.07,  "ru": "Стокгольм",        "de": "Stockholm",           "en": "Stockholm"},
    "Europe/Oslo":        {"lat": 59.91,  "lon":  10.75,  "ru": "Осло",             "de": "Oslo",                "en": "Oslo"},
    "Europe/Copenhagen":  {"lat": 55.68,  "lon":  12.57,  "ru": "Копенгаген",       "de": "Kopenhagen",          "en": "Copenhagen"},
    "Europe/Zurich":      {"lat": 47.38,  "lon":   8.54,  "ru": "Цюрих",            "de": "Zürich",              "en": "Zurich"},
    "Europe/Brussels":    {"lat": 50.85,  "lon":   4.35,  "ru": "Брюссель",         "de": "Brüssel",             "en": "Brussels"},
    "Europe/Lisbon":      {"lat": 38.72,  "lon":  -9.14,  "ru": "Лиссабон",         "de": "Lissabon",            "en": "Lisbon"},
    "Europe/Budapest":    {"lat": 47.50,  "lon":  19.04,  "ru": "Будапешт",         "de": "Budapest",            "en": "Budapest"},
    "Europe/Bucharest":   {"lat": 44.44,  "lon":  26.10,  "ru": "Бухарест",         "de": "Bukarest",            "en": "Bucharest"},
    "Europe/Sofia":       {"lat": 42.70,  "lon":  23.32,  "ru": "София",            "de": "Sofia",               "en": "Sofia"},
    "Europe/Vilnius":     {"lat": 54.69,  "lon":  25.28,  "ru": "Вильнюс",          "de": "Vilnius",             "en": "Vilnius"},
    "Europe/Riga":        {"lat": 56.95,  "lon":  24.11,  "ru": "Рига",             "de": "Riga",                "en": "Riga"},
    "Europe/Tallinn":     {"lat": 59.44,  "lon":  24.75,  "ru": "Таллин",           "de": "Tallinn",             "en": "Tallinn"},
    "Europe/Minsk":       {"lat": 53.90,  "lon":  27.57,  "ru": "Минск",            "de": "Minsk",               "en": "Minsk"},
    "Europe/Samara":      {"lat": 53.20,  "lon":  50.15,  "ru": "Самара",           "de": "Samara",              "en": "Samara"},
    "Europe/Saratov":     {"lat": 51.53,  "lon":  46.03,  "ru": "Саратов",          "de": "Saratow",             "en": "Saratov"},
    "Europe/Volgograd":   {"lat": 48.71,  "lon":  44.51,  "ru": "Волгоград",        "de": "Wolgograd",           "en": "Volgograd"},
    "Europe/Ulyanovsk":   {"lat": 54.33,  "lon":  48.39,  "ru": "Ульяновск",        "de": "Uljanowsk",           "en": "Ulyanovsk"},
    "Europe/Kirov":       {"lat": 58.60,  "lon":  49.65,  "ru": "Киров",            "de": "Kirow",               "en": "Kirov"},
    "Europe/Astrakhan":   {"lat": 46.35,  "lon":  48.04,  "ru": "Астрахань",        "de": "Astrachan",           "en": "Astrakhan"},
    "Asia/Yekaterinburg": {"lat": 56.84,  "lon":  60.60,  "ru": "Екатеринбург",     "de": "Jekaterinburg",       "en": "Yekaterinburg"},
    "Asia/Novosibirsk":   {"lat": 54.99,  "lon":  82.90,  "ru": "Новосибирск",      "de": "Nowosibirsk",         "en": "Novosibirsk"},
    "Asia/Omsk":          {"lat": 54.99,  "lon":  73.37,  "ru": "Омск",             "de": "Omsk",                "en": "Omsk"},
    "Asia/Krasnoyarsk":   {"lat": 56.01,  "lon":  92.79,  "ru": "Красноярск",       "de": "Krasnojarsk",         "en": "Krasnoyarsk"},
    "Asia/Irkutsk":       {"lat": 52.29,  "lon": 104.30,  "ru": "Иркутск",          "de": "Irkutsk",             "en": "Irkutsk"},
    "Asia/Chita":         {"lat": 52.03,  "lon": 113.50,  "ru": "Чита",             "de": "Tschita",             "en": "Chita"},
    "Asia/Yakutsk":       {"lat": 62.03,  "lon": 129.73,  "ru": "Якутск",           "de": "Jakutsk",             "en": "Yakutsk"},
    "Asia/Vladivostok":   {"lat": 43.12,  "lon": 131.90,  "ru": "Владивосток",      "de": "Wladiwostok",         "en": "Vladivostok"},
    "Asia/Magadan":       {"lat": 59.57,  "lon": 150.79,  "ru": "Магадан",          "de": "Magadan",             "en": "Magadan"},
    "Asia/Sakhalin":      {"lat": 46.96,  "lon": 142.74,  "ru": "Южно-Сахалинск",   "de": "Juschno-Sachalinsk",  "en": "Yuzhno-Sakhalinsk"},
    "Asia/Kamchatka":     {"lat": 53.01,  "lon": 158.65,  "ru": "Петропавловск-К.", "de": "Petropawlowsk-K.",    "en": "Petropavlovsk-K."},
    "Asia/Almaty":        {"lat": 43.26,  "lon":  76.94,  "ru": "Алматы",           "de": "Almaty",              "en": "Almaty"},
    "Asia/Tashkent":      {"lat": 41.30,  "lon":  69.24,  "ru": "Ташкент",          "de": "Taschkent",           "en": "Tashkent"},
    "Asia/Tbilisi":       {"lat": 41.69,  "lon":  44.83,  "ru": "Тбилиси",          "de": "Tiflis",              "en": "Tbilisi"},
    "Asia/Yerevan":       {"lat": 40.18,  "lon":  44.51,  "ru": "Ереван",           "de": "Erewan",              "en": "Yerevan"},
    "Asia/Baku":          {"lat": 40.41,  "lon":  49.87,  "ru": "Баку",             "de": "Baku",                "en": "Baku"},
    "Asia/Dubai":         {"lat": 25.20,  "lon":  55.27,  "ru": "Дубай",            "de": "Dubai",               "en": "Dubai"},
    "Asia/Kolkata":       {"lat": 22.57,  "lon":  88.37,  "ru": "Калькутта",        "de": "Kalkutta",            "en": "Kolkata"},
    "Asia/Karachi":       {"lat": 24.86,  "lon":  67.01,  "ru": "Карачи",           "de": "Karatschi",           "en": "Karachi"},
    "Asia/Dhaka":         {"lat": 23.72,  "lon":  90.41,  "ru": "Дакка",            "de": "Dhaka",               "en": "Dhaka"},
    "Asia/Bangkok":       {"lat": 13.75,  "lon": 100.52,  "ru": "Бангкок",          "de": "Bangkok",             "en": "Bangkok"},
    "Asia/Jakarta":       {"lat": -6.21,  "lon": 106.85,  "ru": "Джакарта",         "de": "Jakarta",             "en": "Jakarta"},
    "Asia/Singapore":     {"lat":  1.35,  "lon": 103.82,  "ru": "Сингапур",         "de": "Singapur",            "en": "Singapore"},
    "Asia/Shanghai":      {"lat": 31.23,  "lon": 121.47,  "ru": "Шанхай",           "de": "Shanghai",            "en": "Shanghai"},
    "Asia/Tokyo":         {"lat": 35.69,  "lon": 139.69,  "ru": "Токио",            "de": "Tokio",               "en": "Tokyo"},
    "Asia/Seoul":         {"lat": 37.57,  "lon": 126.98,  "ru": "Сеул",             "de": "Seoul",               "en": "Seoul"},
    "Asia/Taipei":        {"lat": 25.05,  "lon": 121.57,  "ru": "Тайпей",           "de": "Taipeh",              "en": "Taipei"},
    "Asia/Riyadh":        {"lat": 24.69,  "lon":  46.72,  "ru": "Эр-Рияд",          "de": "Riad",                "en": "Riyadh"},
    "Asia/Tehran":        {"lat": 35.69,  "lon":  51.39,  "ru": "Тегеран",          "de": "Teheran",             "en": "Tehran"},
    "Asia/Baghdad":       {"lat": 33.34,  "lon":  44.40,  "ru": "Багдад",           "de": "Bagdad",              "en": "Baghdad"},
    "Africa/Cairo":       {"lat": 30.05,  "lon":  31.25,  "ru": "Каир",             "de": "Kairo",               "en": "Cairo"},
    "Africa/Lagos":       {"lat":  6.45,  "lon":   3.40,  "ru": "Лагос",            "de": "Lagos",               "en": "Lagos"},
    "Africa/Johannesburg":{"lat":-26.20,  "lon":  28.04,  "ru": "Йоханнесбург",     "de": "Johannesburg",        "en": "Johannesburg"},
    "Africa/Nairobi":     {"lat": -1.29,  "lon":  36.82,  "ru": "Найроби",          "de": "Nairobi",             "en": "Nairobi"},
    "America/New_York":   {"lat": 40.71,  "lon": -74.01,  "ru": "Нью-Йорк",         "de": "New York",            "en": "New York"},
    "America/Los_Angeles":{"lat": 34.05,  "lon":-118.24,  "ru": "Лос-Анджелес",     "de": "Los Angeles",         "en": "Los Angeles"},
    "America/Chicago":    {"lat": 41.85,  "lon": -87.65,  "ru": "Чикаго",           "de": "Chicago",             "en": "Chicago"},
    "America/Denver":     {"lat": 39.74,  "lon":-104.98,  "ru": "Денвер",           "de": "Denver",              "en": "Denver"},
    "America/Toronto":    {"lat": 43.65,  "lon": -79.38,  "ru": "Торонто",          "de": "Toronto",             "en": "Toronto"},
    "America/Vancouver":  {"lat": 49.25,  "lon":-123.12,  "ru": "Ванкувер",         "de": "Vancouver",           "en": "Vancouver"},
    "America/Sao_Paulo":  {"lat":-23.55,  "lon": -46.63,  "ru": "Сан-Паулу",        "de": "São Paulo",           "en": "São Paulo"},
    "America/Argentina/Buenos_Aires":{"lat":-34.61,"lon":-58.38,"ru":"Буэнос-Айрес","de":"Buenos Aires",          "en": "Buenos Aires"},
    "America/Mexico_City":{"lat": 19.43,  "lon": -99.13,  "ru": "Мехико",           "de": "Mexiko-Stadt",        "en": "Mexico City"},
    "America/Bogota":     {"lat":  4.71,  "lon": -74.07,  "ru": "Богота",           "de": "Bogotá",              "en": "Bogota"},
    "America/Lima":       {"lat":-12.05,  "lon": -77.04,  "ru": "Лима",             "de": "Lima",                "en": "Lima"},
    "America/Santiago":   {"lat":-33.46,  "lon": -70.65,  "ru": "Сантьяго",         "de": "Santiago",            "en": "Santiago"},
    "Pacific/Auckland":   {"lat":-36.87,  "lon": 174.77,  "ru": "Окленд",           "de": "Auckland",            "en": "Auckland"},
    "Pacific/Sydney":     {"lat":-33.87,  "lon": 151.21,  "ru": "Сидней",           "de": "Sydney",              "en": "Sydney"},
    "Australia/Sydney":   {"lat":-33.87,  "lon": 151.21,  "ru": "Сидней",           "de": "Sydney",              "en": "Sydney"},
    "Australia/Melbourne":{"lat":-37.81,  "lon": 144.96,  "ru": "Мельбурн",         "de": "Melbourne",           "en": "Melbourne"},
    "Australia/Perth":    {"lat":-31.95,  "lon": 115.86,  "ru": "Перт",             "de": "Perth",               "en": "Perth"},
    "Australia/Adelaide": {"lat":-34.93,  "lon": 138.60,  "ru": "Аделаида",         "de": "Adelaide",            "en": "Adelaide"},
    "Pacific/Honolulu":   {"lat": 21.31,  "lon":-157.86,  "ru": "Гонолулу",         "de": "Honolulu",            "en": "Honolulu"},
    "UTC":                {"lat": 51.48,  "lon":   0.00,  "ru": "UTC",              "de": "UTC",                 "en": "UTC"},
}


def _sky_tz_offset(tz_id: str) -> float:
    """Возвращает UTC-смещение в часах для IANA timezone."""
    from datetime import datetime, timezone as _utc
    try:
        now = datetime.now(_utc.utc)
        off = now.astimezone(ZoneInfo(tz_id)).utcoffset()
        return off.total_seconds() / 3600
    except Exception:
        return 0.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками на Земле в км (формула Хаверсина)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(max(0.0, a)))


def _smart_distance(km: float) -> str:
    """Умное округление расстояния с текстом 'почти' / 'чуть больше'.

    < 1000 км → до ближайших 100 км
    ≥ 1000 км → до ближайших 500 км
    Если округление вверх → 'почти X км'
    Если округление вниз → 'чуть больше X км'
    """
    if km <= 0:
        return "рядом"
    step = 100 if km < 1000 else 500
    rounded = round(km / step) * step
    if rounded == 0:
        rounded = step
    if rounded > km:
        return f"почти {int(rounded)} км"
    elif rounded < km:
        return f"чуть больше {int(rounded)} км"
    else:
        return f"{int(rounded)} км"


def _tz_to_city_info(tz_id: str, lang: str = "ru") -> dict:
    """Возвращает словарь с данными о городе для данного IANA timezone."""
    info = _TZ_CITY_INFO.get(tz_id)
    offset = _sky_tz_offset(tz_id)
    if info:
        label = info.get(lang) or info.get("ru") or info["en"]
        return {"tzId": tz_id, "cityLabel": label, "offsetHours": offset, "lat": info["lat"], "lon": info["lon"]}
    # Fallback: извлекаем название из IANA-строки
    city_raw = tz_id.split("/")[-1].replace("_", " ") if "/" in tz_id else tz_id
    return {"tzId": tz_id, "cityLabel": city_raw, "offsetHours": offset, "lat": 0.0, "lon": 0.0}


def _get_saved_browser_tz(user_id: int) -> Optional[str]:
    from database import db as _db
    try:
        with _db._get_connection() as conn:
            cursor = conn.execute(
                "SELECT visitor_id, timezone_id FROM devices WHERE timezone_id IS NOT NULL AND timezone_id != '' ORDER BY last_seen_utc DESC"
            )
            for row in cursor.fetchall():
                v_id = row["visitor_id"]
                if _visitor_to_user_id(v_id) == user_id:
                    return row["timezone_id"]
    except Exception:
        pass
    return None


def _resolve_user_timezone(user_id: int, req_tz: Optional[str] = None) -> str:
    from database import db as _db
    tz_mode = _db.get_user_setting(user_id, "website_timezone_mode") or "auto"
    if tz_mode == "auto":
        if req_tz and req_tz != "__bot__":
            return req_tz
        saved_tz = _get_saved_browser_tz(user_id)
        if saved_tz:
            return saved_tz
    return _db.get_user_setting(user_id, "timezone") or "UTC"


def _build_sky_cfg(request: web.Request) -> dict:
    """Строит SKY_CFG для sky.html на основе пары пользователя из БД."""
    from database import db as _db

    # Дефолт: без подмены на старые города, только нейтральный UTC-заглушка.
    def _default(has_couple=False):
        left  = _tz_to_city_info("UTC")
        right = _tz_to_city_info("UTC")
        cfg = _sky_cfg_from_cities(left, right, left_name="Создатель", right_name="Партнёр")
        cfg["hasCouple"] = has_couple
        cfg["sameCity"] = True
        return cfg

    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    uid = _visitor_to_user_id(visitor_id) if visitor_id else None

    lang = (request.rel_url.query.get("lang") or "").strip()[:2]
    if lang not in ("ru", "de", "en") and uid:
        lang = (_db.get_user_setting(uid, "lang") or "").strip()
    if lang not in ("ru", "de", "en"):
        lang = "ru"

    def _default(has_couple=False):
        left  = _tz_to_city_info("UTC", lang)
        right = _tz_to_city_info("UTC", lang)
        cfg = _sky_cfg_from_cities(left, right, left_name="Создатель", right_name="Партнёр")
        cfg["hasCouple"] = has_couple
        cfg["sameCity"] = True
        return cfg

    if not visitor_id or not uid:
        return _default(has_couple=False)

    couple = _db.get_couple_by_user(uid)
    if not couple:
        return _default(has_couple=False)

    u1 = couple.get("user1_id")
    u2 = couple.get("user2_id")
    if not u2:
        return _default(has_couple=False)

    partner_id = u2 if u1 == uid else u1
    req_tz  = (request.rel_url.query.get("tz") or "").strip()

    my_tz = _resolve_user_timezone(uid, req_tz)
    partner_tz = _resolve_user_timezone(partner_id)

    my_city      = _tz_to_city_info(my_tz, lang)
    partner_city = _tz_to_city_info(partner_tz, lang)

    # Имена пользователей (первое имя / username)
    u1_name = (_db.get_display_name(u1) or "Создатель").split()[0]
    u2_name = (_db.get_display_name(u2) or "Партнёр").split()[0]

    # Левая панель (ключ 'moscow') = creator/user1, правая (ключ 'bishkek') = partner/user2
    is_creator = (u1 == uid)
    left  = my_city      if is_creator else partner_city
    right = partner_city if is_creator else my_city

    cfg = _sky_cfg_from_cities(left, right, left_name=u1_name, right_name=u2_name)
    cfg["hasCouple"] = True
    cfg["sameCity"] = (my_tz == partner_tz or my_tz == "UTC" or partner_tz == "UTC")
    return cfg


def _sky_cfg_from_cities(left: dict, right: dict,
                          left_name: str = "Создатель",
                          right_name: str = "Партнёр") -> dict:
    """Финальный SKY_CFG из двух городов (левый = creator, правый = partner)."""
    left_real = (left.get("tzId") or "").strip().upper() not in {"UTC", "ETC/UTC"}
    right_real = (right.get("tzId") or "").strip().upper() not in {"UTC", "ETC/UTC"}

    dist_km = _haversine_km(left["lat"], left["lon"], right["lat"], right["lon"])
    dist_label = _smart_distance(dist_km)

    # Разница по времени
    diff_h = abs(right["offsetHours"] - left["offsetHours"])
    if diff_h == 0:
        time_diff_label = "одинаковое время"
    elif diff_h % 1 == 0:
        h = int(diff_h)
        if h % 10 == 1 and h % 100 != 11:
            hw = "час"
        elif h % 10 in (2, 3, 4) and h % 100 not in (12, 13, 14):
            hw = "часа"
        else:
            hw = "часов"
        time_diff_label = f"разница {h} {hw}"
    else:
        total_min = int(diff_h * 60)
        h = total_min // 60
        m = total_min % 60
        time_diff_label = f"разница {h}:{m:02d}"

    return {
        "moscow":         left,
        "bishkek":        right,
        "leftName":       left_name,   # имя creator/user1 (левая панель)
        "rightName":      right_name,  # имя partner/user2 (правая панель)
        "distanceKm":     round(dist_km),
        "distanceLabel":  dist_label,
        "timeDiffLabel":  time_diff_label,
        "sameTimezone":   diff_h == 0 or not left_real or not right_real,
    }


async def api_sky_cfg(request: web.Request) -> web.Response:
    """GET /api/sky_cfg — возвращает SKY_CFG для текущего пользователя в JSON."""
    cfg = _build_sky_cfg(request)
    resp = web.Response(
        text=json.dumps(cfg, ensure_ascii=False),
        content_type="application/json",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )
    return _add_cors_headers(resp)


async def sky_page(request: web.Request) -> web.Response:
    """Отдаёт страницу /sky (sky.html). Требует зарегистрированную пару."""
    if not _get_registered_couple(request):
        raise web.HTTPFound("/404")
    project_root = Path(__file__).resolve().parent
    sky_path = project_root / "sky.html"
    if not sky_path.exists():
        return web.Response(text="sky.html not found", status=404)
    try:
        html = sky_path.read_text(encoding="utf-8")
    except Exception:
        return web.Response(text="cannot read sky.html", status=500)

    # Диагностика: что за visitor_id пришёл и что мы строим
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v") or ""
    sky_cfg = _build_sky_cfg(request)
    m_label = (sky_cfg.get("moscow") or {}).get("cityLabel", "?")
    b_label = (sky_cfg.get("bishkek") or {}).get("cityLabel", "?")
    logger.info(
        "sky_page: visitor_id=%r → moscow=%r bishkek=%r dist=%r",
        visitor_id, m_label, b_label, sky_cfg.get("distanceLabel"),
    )

    # Не пробрасываем серверный API-ключ в клиентский HTML.
    html = html.replace("{{API_SECRET_KEY}}", "")
    html = html.replace("{{SKY_CFG_JSON}}", json.dumps(sky_cfg, ensure_ascii=False))
    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


async def upload_notification_media(request: web.Request) -> web.Response:
    """Загружает медиафайл для сайт-уведомления.
    visitor_id передаётся как query-параметр (?v=creator), файл — в теле multipart."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:
        reader = await request.multipart()
        saved_path = None
        media_type_str = None
        max_size = _UPLOAD_MAX_BY_PATH.get("/api/upload_notification_media", 50 * 1024 * 1024)

        async for field in reader:
            logger.info("UPLOAD field: name=%r filename=%r ct=%r", field.name, getattr(field, 'filename', None), field.headers.get("Content-Type",""))
            if field.name != "file":
                await field.read()
                continue

            filename = field.filename or "upload"
            content_type = field.headers.get("Content-Type", "")
            allowed_ext = _safe_upload_ext(filename, content_type)
            if not allowed_ext:
                logger.warning("upload_notification_media: запрещённый тип файла filename=%r ct=%r", filename, content_type)
                return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden_file_type"}, status=415))
            media_type_str = "video" if allowed_ext in _ALLOWED_VIDEO_EXT else "photo"

            ts = int(datetime.now(timezone.utc).timestamp() * 1000)
            safe_name = f"notif_{ts}{allowed_ext}"

            media_folder = getattr(config, "MEDIA_FOLDER", "media")
            save_dir = Path(media_folder) / "notifications"
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = save_dir / safe_name

            logger.info("UPLOAD saving to: %s", save_path)
            size = 0
            with open(save_path, "wb") as f_out:
                while True:
                    chunk = await field.read_chunk(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_size:
                        try:
                            save_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        return _add_cors_headers(web.json_response({"ok": False, "error": "file_too_large"}, status=413))
                    f_out.write(chunk)

            saved_path = str(save_path)
            logger.info("UPLOAD saved: %s bytes=%d", saved_path, size)
            break

        if not saved_path:
            logger.warning("UPLOAD no file received, visitor_id=%r", visitor_id)
            return _add_cors_headers(web.json_response({"ok": False, "error": "no file"}))

        return _add_cors_headers(web.json_response({
            "ok": True,
            "path": saved_path,
            "media_type": media_type_str,
        }))
    except Exception as e:
        logger.exception("Ошибка upload_notification_media: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

async def admin_send_notification(request: web.Request) -> web.Response:
    """Создаёт сайт-уведомление для партнёра (из админ-панели). Поддерживает список media_items."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    couple = db.get_couple_by_user(user_id) or {}
    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    text = _pstr(payload.get("text")).strip()
    if not text:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty text"}))
    media_items = payload.get("media_items") or None  # список [{path, type}, ...]
    notif_id = db.add_site_notification(text, media_items=media_items, couple_id=couple_id)

    # Push через WebSocket — обогащаем медиа URL-ами и отправляем партнёру
    if notif_id:
        try:
            push_items = []
            if media_items:
                for item in media_items:
                    p = item.get("path", "")
                    name = Path(p).name if p else ""
                    push_items.append({
                        "name": name,
                        "filename": name,
                        "url": f"/media/notifications/{name}" if name else "",
                        "type": item.get("type", "photo"),
                    })
            asyncio.create_task(broadcast_notification({
                "id": notif_id,
                "couple_id": couple_id,
                "text": text,
                "media_items": push_items if push_items else None,
            }))
        except Exception as e:
            logger.warning("WS broadcast failed: %s", e)

    return _add_cors_headers(web.json_response({"ok": True, "id": notif_id}))


async def admin_get_notification(request: web.Request) -> web.Response:
    """Возвращает первое непоказанное уведомление для партнёра при загрузке сайта."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_in_couple(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    couple = db.get_couple_by_user(user_id) or {}
    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    notif = db.get_pending_site_notification(couple_id=couple_id)
    logger.info("GET_NOTIF raw: %r", notif)
    if notif:
        if notif.get("media_items"):
            for item in notif["media_items"]:
                if item.get("path"):
                    name = Path(item["path"]).name
                    item["name"] = name
                    item["filename"] = name
                    item["url"] = f"/media/notifications/{name}"
        elif notif.get("media_path"):
            notif["media_url"] = f"/media/{Path(notif['media_path']).name}"
    logger.info("GET_NOTIF response: %r", notif)
    return _add_cors_headers(web.json_response({"ok": True, "notification": notif}))


async def admin_mark_notification_delivered(request: web.Request) -> web.Response:
    """Помечает уведомление как доставленное."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_in_couple(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    couple = db.get_couple_by_user(user_id) or {}
    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    notif_id = _safe_int(payload.get("id"), 0)
    if notif_id:
        db.mark_notification_delivered(notif_id, couple_id=couple_id)
    return _add_cors_headers(web.json_response({"ok": True}))


async def admin_backup(request: web.Request) -> web.Response:
    """Создаёт резервную копию БД (заменяя предыдущую)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        backup_dir = Path(__file__).resolve().parent / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / "memories_backup_latest.db"
        if backup_path.exists():
            backup_path.unlink()
        ok = db.backup_database(backup_path=str(backup_path))
        return _add_cors_headers(web.json_response({"ok": bool(ok), "path": str(backup_path)}))
    except Exception as e:
        logger.exception("Ошибка admin_backup: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

async def check_site_password(request: web.Request) -> web.Response:
    """Устарело: вход через пароль отключён — только по токену из бота."""
    return _add_cors_headers(web.json_response({"ok": False, "error": "disabled"}, status=410))


async def token_check(request: web.Request) -> web.Response:
    """Проверяет чей токен (не расходуя его).
    POST /api/token_check  { "token": "...", "current_visitor_id": "123" }
    """
    try:
        payload = await request.json()
        if not isinstance(payload, dict): payload = {}
    except Exception:
        payload = {}

    token = _pstr(payload.get("token")).strip()
    debug_id = _pstr(payload.get("debug_id")).strip() or "none"
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    logger.info(f"[auth-debug:{debug_id}] token_check called, current_visitor_id: {payload.get('current_visitor_id')}")

    token_data = db.peek_user_login_token(token)
    if not token_data:
        logger.info(f"[auth-debug:{debug_id}] token_check invalid/used token")
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid"}))

    token_user_id = token_data["user_id"]
    token_user_info = db.get_user(token_user_id) or {}
    
    current_vid = _pstr(payload.get("current_visitor_id")).strip()
    current_user_id = _visitor_to_user_id(current_vid)
    current_user_info = {}
    if current_user_id:
        current_user_info = db.get_user(current_user_id) or {}

    def _format_name(u: dict, default: str) -> str:
        fn = u.get("first_name") or ""
        ln = u.get("last_name") or ""
        un = u.get("username") or ""
        return (fn + " " + ln).strip() or fn or ("@" + un if un else "") or default

    return _add_cors_headers(web.json_response({
        "ok": True,
        "match": current_user_id == token_user_id if current_user_id is not None else False,
        "token_user": {
            "id": token_user_id,
            "name": _format_name(token_user_info, f"User {token_user_id}")
        },
        "current_user": {
            "id": current_vid,
            "name": _format_name(current_user_info, f"User {current_vid}") if current_vid else ""
        }
    }))

async def auth_debug_log(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False}))
    
    debug_id = payload.get("id", "none")
    logger.info(f"========== AUTH-DEBUG-TRACE [{debug_id}] ==========")
    logger.info(f"[{debug_id}] Token opening attempt:")
    logger.info(f"[{debug_id}] - visitor_id cookie present: {payload.get('vid_cookie_present')}")
    logger.info(f"[{debug_id}] - role cookie present: {payload.get('role_cookie_present')}")
    logger.info(f"[{debug_id}] - treated as: {payload.get('treated_as')}")
    logger.info(f"[{debug_id}] - token_check executed: {payload.get('token_check_called')}")
    logger.info(f"[{debug_id}] - token_check ok: {payload.get('token_check_result')}")
    logger.info(f"[{debug_id}] - token_check match: {payload.get('token_check_match')}")
    logger.info(f"[{debug_id}] - modal action: {payload.get('modal_action')}")
    logger.info(f"[{debug_id}] - final shouldConsumeToken: {payload.get('should_consume_token')}")
    logger.info(f"[{debug_id}] - token_auth called: {payload.get('token_auth_called')}")
    logger.info(f"[{debug_id}] - token_auth result: {payload.get('token_auth_result')}")
    logger.info(f"=====================================================")
    return _add_cors_headers(web.json_response({"ok": True}))

async def token_auth(request: web.Request) -> web.Response:
    """Проверяет персональный токен пользователя и возвращает его роль.
    Используется для автологина по персональной ссылке из бота.
    POST /api/token_auth  { "token": "..." }
    Возвращает: { ok: true, role: "creator"|"partner"|"admin"|"user", user_id: int }
    """
    now = time.monotonic()
    token_key = _client_rate_key(request)[0]
    bucket = [t for t in _token_auth_buckets.get(token_key, []) if now - t < _TOKEN_AUTH_WINDOW_SEC]
    if len(bucket) >= _TOKEN_AUTH_MAX_ATTEMPTS:
        return _add_cors_headers(
            web.json_response(
                {"ok": False, "error": "rate_limited"},
                status=429,
                headers={"Retry-After": str(_TOKEN_AUTH_WINDOW_SEC)},
            )
        )
    bucket.append(now)
    _token_auth_buckets[token_key] = bucket

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}

    token = _pstr(payload.get("token")).strip()
    debug_id = _pstr(payload.get("debug_id")).strip() or "none"
    if not token:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty"}))

    consumed_by = f"ip={request.remote or ''};ua={(request.headers.get('User-Agent') or '')[:120]}"
    token_data = db.consume_user_login_token(token, consumed_by=consumed_by)
    if not token_data:
        logger.info(f"[auth-debug:{debug_id}] token_auth invalid/used token")
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid"}))
    
    logger.info(f"[auth-debug:{debug_id}] token_auth executed and consumed token for user_id={token_data['user_id']}")

    user_id = token_data["user_id"]
    role = token_data.get("role") or "user"

    # Сохраняем часовой пояс сразу при входе через токен (фикс: ТЗ не попадала в БД)
    tz_from_auth = (_pstr(payload.get("tz")) or _pstr(payload.get("timezone"))).strip()
    if tz_from_auth:
        try:
            # Website must NEVER overwrite timezone/timezone_display.
            pass
        except Exception:
            pass

    user_info = db.get_user(user_id) or {}
    import uuid
    visitor_id = f"{user_id}_{uuid.uuid4().hex[:12]}"
    
    # Pre-register device to prevent race conditions in subsequent page load calls
    ua_raw = request.headers.get("User-Agent", "")
    ua_pretty = _format_device_info(ua_raw, None) or "Неизвестное устройство"
    db.add_or_update_device(
        visitor_id,
        role=role,
        ua_pretty=ua_pretty,
    )

    ai_session, ai_session_exp = _issue_ai_session(visitor_id)
    visitor_sig = _sign_payload(f"visitor:{visitor_id}")
    response = _add_cors_headers(web.json_response({
        "ok": True,
        "role": role,
        "user_id": user_id,
        "first_name": user_info.get("first_name") or "",
        "username": user_info.get("username") or "",
        "visitor_id": visitor_id,
        "visitor_sig": visitor_sig,
        "ai_session": ai_session,
        "ai_session_exp": ai_session_exp,
    }))
    response.set_cookie(
        "visitor_id",
        visitor_id,
        max_age=86400 * 30,
        samesite="Lax",
        secure=_is_secure_request(request),
    )
    response.set_cookie(
        "visitor_sig",
        visitor_sig,
        max_age=86400 * 30,
        httponly=True,
        samesite="Lax",
        secure=_is_secure_request(request),
    )
    response.set_cookie(
        "ai_session",
        ai_session,
        max_age=3600,
        httponly=True,
        samesite="Lax",
        secure=_is_secure_request(request),
    )
    if db.is_creator(user_id):
        admin_sess, _ = _issue_admin_session(user_id, request)
        response.set_cookie(
            "admin_session",
            admin_sess,
            max_age=12 * 3600,
            httponly=True,
            samesite="Strict",
            secure=_is_secure_request(request),
        )
    else:
        response.del_cookie("admin_session")
    return response


async def api_unlink_init(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return _add_cors_headers(web.Response(status=200))
        
    visitor_id = _get_trusted_visitor_id(request)
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unauthorized"}, status=401))

    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_user"}, status=401))

    couple = db.get_couple_by_user(user_id)
    if not couple:
        return _add_cors_headers(web.json_response({"ok": False, "error": "no_couple"}, status=400))

    if db.get_recent_unlink_denied(user_id, 3600):
        return _add_cors_headers(web.json_response({"ok": False, "error": "blocked"}, status=403))

    ip = request.headers.get("X-Forwarded-For", request.remote or "")
    ua = request.headers.get("User-Agent", "")
    country = request.headers.get("CF-IPCountry", "-")
    city = request.headers.get("CF-IPCity", "-")

    token = secrets.token_urlsafe(32)
    db.create_unlink_request(user_id, couple["id"], token, ip, ua, country, city)

    async def _send_bot_msg():
        from zoneinfo import ZoneInfo
        now_msk = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Moscow"))
        time_str = now_msk.strftime("%d %B %Y, %H:%M МСК")
        
        text = (
            f"🔓 <b>Запрос на отвязку аккаунта</b>\n\n"
            f"Устройство: {ua}\n"
            f"IP: {ip} ({country}, {city})\n"
            f"Время: {time_str}\n\n"
            f"Отвязать текущий аккаунт от сайта?\n"
            f"После отвязки вход с текущим Telegram-аккаунтом будет невозможен.\n"
            f"Для повторного входа потребуется другой аккаунт и специальная ссылка."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, отвязать", callback_data=f"unlink_confirm:{token}")],
            [InlineKeyboardButton(text="❌ Нет, это не я!", callback_data=f"unlink_deny:{token}")]
        ])
        
        bot = Bot(token=config.BOT_TOKEN)
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode=ParseMode.HTML, reply_markup=kb)
        except Exception as e:
            logger.exception(f"Failed to send unlink init message to {user_id}: {e}")
        finally:
            await bot.session.close()

    asyncio.create_task(_send_bot_msg())
    return _add_cors_headers(web.json_response({"ok": True, "token": token}))


async def ws_unlink(request: web.Request) -> web.Response:
    token = request.query.get("token")
    vid = request.query.get("v")
    if not token or not vid:
        return web.HTTPBadRequest()

    req = db.get_unlink_request(token)
    if not req or req["status"] != "pending":
        return web.HTTPForbidden()

    user_id = _visitor_to_user_id(vid)
    if not user_id or req["user_id"] != user_id:
        return web.HTTPForbidden()

    ws = web.WebSocketResponse(heartbeat=30.0)
    await ws.prepare(request)
    _unlink_ws_clients.setdefault(token, set()).add(ws)

    try:
        async for msg in ws:
            pass
    finally:
        _unlink_ws_clients.get(token, set()).discard(ws)

    return ws



async def site_save_settings(request: web.Request) -> web.Response:
    """Сохраняет настройки сайта (lang, tz) для пользователя в базу данных.
    POST /api/site_save_settings  { "visitor_id": "...", "lang": "ru", "tz": "Europe/Moscow" }
    """
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}

    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    lang = _pstr(payload.get("lang")).strip()
    tz   = _pstr(payload.get("tz")).strip()

    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unknown_user"}))

    if lang in ("ru", "de", "en"):
        db.set_user_setting(user_id, "lang", lang)
    if tz:
        if tz == "__auto__":
            db.set_user_setting(user_id, "website_timezone_mode", "auto")
        elif tz == "__bot__":
            db.set_user_setting(user_id, "website_timezone_mode", "profile")

    return _add_cors_headers(web.json_response({"ok": True}))


async def memory_unlock(request: web.Request) -> web.Response:

    """Проверяет пароль для приватного воспоминания (тип privacy_type = password)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    memory_id = payload.get("memory_id")

    answer = _pstr(payload.get("answer")).strip()

    if not isinstance(memory_id, int):

        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_memory_id"}))

    privacy = db.get_memory_privacy(memory_id)

    p_type = (privacy.get("privacy_type") or "").strip()

    correct = (privacy.get("privacy_answer") or "").strip()

    if p_type != "password" or not correct:

        return _add_cors_headers(web.json_response({"ok": False, "error": "not_password_protected"}))

    if not answer:

        return _add_cors_headers(web.json_response({"ok": False, "error": "empty_answer"}))

    if db.verify_privacy_answer(correct, answer):

        return _add_cors_headers(web.json_response({"ok": True, "memory_id": memory_id}))

    return _add_cors_headers(web.json_response({"ok": False, "error": "wrong_answer"}))

async def memory_view_start(request: web.Request) -> web.Response:

    """Создаёт сессию просмотра момента (для подсчёта времени на сайте)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    memory_id = payload.get("memory_id")

    if not isinstance(memory_id, int):

        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_memory_id"}))

    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    session_id = db.add_memory_view_session_start(memory_id, visitor_id=visitor_id)

    if session_id <= 0:

        return _add_cors_headers(web.json_response({"ok": False, "error": "cannot_create_session"}))

    return _add_cors_headers(web.json_response({"ok": True, "session_id": session_id}))

async def memory_view_finish(request: web.Request) -> web.Response:

    """Завершает сессию просмотра момента. Принимает JSON или тело как текст (sendBeacon)."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    payload = {}

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except Exception:

        try:

            body = await request.text()

            if body:

                payload = json.loads(body)

        except Exception:

            pass

    session_id = payload.get("session_id")

    if session_id is not None and isinstance(session_id, (int, float)):

        session_id = int(session_id)

    elif isinstance(session_id, str) and session_id.isdigit():

        session_id = int(session_id)

    else:

        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_session_id"}))

    db.finish_memory_view_session(session_id)

    return _add_cors_headers(web.json_response({"ok": True}))

async def category_open(request: web.Request) -> web.Response:

    """Регистрирует одно открытие секции (нажатие «Показать») — без дублирования на клиенте."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    section_id = _pstr(payload.get("section_id")).strip()

    allowed = {"important_moments", "memories", "important_dates", "events", "wishes"}
    is_custom_section = section_id.startswith("custom_cat_") and section_id[11:].isdigit()

    if section_id not in allowed and not is_custom_section:

        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_section_id"}))

    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    db_section_id = section_id
    if is_custom_section:
        db_section_id = "custom_" + section_id[11:]

    db.add_category_open(visitor_id=visitor_id, section_id=db_section_id)

    return _add_cors_headers(web.json_response({"ok": True}))

async def visit_heartbeat(request: web.Request) -> web.Response:

    """Раз в 5 сек с фронта: добавляет 5 сек к суммарному времени на сайте для visitor_id."""

    if not _check_api_secret(request):

        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:

        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}

    except web.HTTPException:

        raise

    except Exception:

        payload = {}

    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")

    if not visitor_id:

        return _add_cors_headers(web.json_response({"ok": False, "error": "visitor_id required"}))

    db.add_visitor_site_time(visitor_id, seconds=5)

    return _add_cors_headers(web.json_response({"ok": True}))

def _render_maintenance_page() -> str:

    """Рендер страницы технического перерыва с реальными данными из БД."""

    project_root = Path(__file__).resolve().parent

    maintenance_path = project_root / "maintenance.html"

    if not maintenance_path.exists():

        return "<!DOCTYPE html><html><body><p>Технический перерыв. Скоро вернёмся.</p></body></html>"

    html = maintenance_path.read_text(encoding="utf-8")

    started_at = db.get_setting("maintenance_started_at")

    if started_at:

        try:

            dt = datetime.strptime(started_at[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

            start_ms = int(dt.timestamp() * 1000)

        except Exception:

            start_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 60 * 1000

    else:

        start_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - 60 * 1000

    html = html.replace("{{MAINTENANCE_START_SCRIPT}}", f"window.MAINTENANCE_START_MS={start_ms};")

    html = html.replace("{{MAINTENANCE_SUBTITLE}}", "Чо то какие-то у меня технические шоколадки<br>Немного падажди")

    visits = db.get_setting("maintenance_visits") or "0"

    try:

        n = int(visits)

    except ValueError:

        n = 0

    if n > 0:

        visits_block = (

            '<div class="timer-card" style="margin-bottom:12px">'

            '<div class="timer-label">За время перерыва зашли на сайт</div>'

            f'<div class="timer-num" style="font-size:1.8rem;margin:0">{n}</div>'

            '<div class="timer-unit-label">человек</div></div>'

        )

    else:

        visits_block = ""

    html = html.replace("{{MAINTENANCE_VISITS_BLOCK}}", visits_block)

    return html

async def index(request: web.Request) -> web.StreamResponse:

    """Отдаёт главную страницу или страницу технического перерыва (если включена тест версия)."""
    try:
        with open("/app/frontend_debug.log", "a", encoding="utf-8") as f:
            f.write("SERVER: GET / requested\n")
    except:
        pass

    project_root = Path(__file__).resolve().parent

    if db.get_setting("test_version") == "1":

        html = _render_maintenance_page()

        return web.Response(text=html, content_type="text/html", charset="utf-8")

    index_path = project_root / "index.html"

    if not index_path.exists():

        return web.Response(text="index.html not found", status=404)

    try:

        html = index_path.read_text(encoding="utf-8")

    except Exception:

        logger.exception("Cannot read index.html")

        return web.Response(text="cannot read index.html", status=500)

    # Не пробрасываем серверный API-ключ в клиентский HTML.
    html = html.replace("{{API_SECRET_KEY}}", "")

    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


async def not_found_page(request: web.Request) -> web.Response:
    """Отдаёт 404.html с подставленным именем бота."""
    project_root = Path(__file__).resolve().parent
    page_path = project_root / "404.html"
    if not page_path.exists():
        return web.Response(text="404 — page not found", status=404)
    html = page_path.read_text(encoding="utf-8")
    bot_username = (getattr(config, "BOT_USERNAME", "") or "Akimova_Ksysha_love_bot").strip()
    html = html.replace("{{BOT_USERNAME}}", bot_username)
    return web.Response(text=html, content_type="text/html", charset="utf-8", status=200)


async def profile_page(request: web.Request) -> web.Response:
    """Отдаёт страницу профиля (/profile).

    Защита на уровне HTML-страницы: требует наличия visitor_id cookie.
    Реальная авторизация данных — на уровне API-эндпоинтов (site_bootstrap, profile_stats),
    которые проверяют visitor_sig через _get_trusted_visitor_id().
    Паттерн идентичен stats_page.
    """
    visitor_id = (request.cookies.get("visitor_id") or "").strip()
    if not visitor_id:
        logger.warning(f"SERVER REDIRECT: profile_page missing visitor_id cookie! Headers: {request.headers}")
        raise web.HTTPFound("/")

    project_root = Path(__file__).resolve().parent
    page_path = project_root / "profile.html"
    if not page_path.exists():
        return web.Response(text="profile.html not found", status=404)

    try:
        html = page_path.read_text(encoding="utf-8")
    except Exception:
        logger.exception("Cannot read profile.html")
        return web.Response(text="cannot read profile.html", status=500)

    return web.Response(
        text=html,
        content_type="text/html",
        charset="utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )


async def api_sessions(request: web.Request) -> web.Response:
    """Возвращает список активных сессий пользователя.
    GET /api/sessions
    """
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    devices = db.get_user_devices(visitor_id)
    # Mark the current session
    for dev in devices:
        dev["is_current"] = (dev.get("visitor_id") == visitor_id)

    return _add_cors_headers(web.json_response({"ok": True, "sessions": devices}))


async def api_sessions_revoke(request: web.Request) -> web.Response:
    """Завершает выбранную сессию пользователя.
    POST /api/sessions/revoke  { "device_id": 123 }
    """
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:
        payload = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad_json"}))

    device_id = payload.get("device_id")
    if not device_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "missing_device_id"}))

    # Fetch device to make sure it belongs to the user
    dev = db.get_device_by_id(device_id)
    if not dev:
        return _add_cors_headers(web.json_response({"ok": False, "error": "not_found"}))

    dev_vid = dev.get("visitor_id") or ""
    my_base = visitor_id.split("_")[0] if "_" in visitor_id else visitor_id
    dev_base = dev_vid.split("_")[0] if "_" in dev_vid else dev_vid

    if my_base != dev_base:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    # Perform revocation
    success = db.delete_device_by_id(device_id)
    return _add_cors_headers(web.json_response({"ok": success}))


async def logout(request: web.Request) -> web.Response:
    """Завершает текущую сессию пользователя, удаляя все авторизационные cookie.

    POST /api/logout
    Требует валидных cookie visitor_id + visitor_sig (fail-closed).
    Возвращает: { ok: true } и удаляет 4 cookie.
    """
    visitor_id = _get_trusted_visitor_id(request, payload=None, allow_header_fallback=False)
    if not visitor_id:
        return _add_cors_headers(
            web.json_response({"ok": False, "error": "forbidden"}, status=403)
        )

    logger.info("logout: visitor_id=%r terminated session", visitor_id)
    db.delete_device_by_visitor_id(visitor_id)

    response = _add_cors_headers(web.json_response({"ok": True}))
    for name in ("visitor_id", "visitor_sig", "ai_session", "admin_session"):
        response.del_cookie(name, path="/")

    return response


def _stats_loader_html() -> str:
    """HTML-страница загрузки /stats: анимация и редирект с определением tz и visitor_id."""
    return """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="utf-8">
    <title>Статистика — загрузка</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>

        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; padding: 24px; background: #fafafa; color: #333; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; box-sizing: border-box; }

        .loading-screen { display: flex; flex-direction: column; align-items: center; gap: 20px; padding: 48px 24px; background: #fff; border-radius: 24px; box-shadow: 0 3px 10px rgba(0,0,0,0.1); animation: fadeUp 0.3s ease both; }

        .loading-heart { font-size: 2.4rem; animation: heartbeat 1.2s ease-in-out infinite; }

        @keyframes heartbeat { 0%,100%{ transform: scale(1); opacity:1 } 25%{ transform: scale(1.2); opacity:1 } 50%{ transform: scale(1); opacity:0.7 } 75%{ transform: scale(1.1); opacity:1 } }

        .loading-text { font-size: 0.95rem; color: #d43f8d; font-weight: 600; }

        .loading-dots span { display: inline-block; width: 6px; height: 6px; background: #ffb6c1; border-radius: 50%; margin: 0 3px; animation: dotBounce 1.2s ease-in-out infinite; }

        .loading-dots span:nth-child(2) { animation-delay: 0.2s; }

        .loading-dots span:nth-child(3) { animation-delay: 0.4s; }

        @keyframes dotBounce { 0%,80%,100%{ transform: translateY(0); opacity:0.4 } 40%{ transform: translateY(-6px); opacity:1 } }

        .loading-sub { font-size: 0.8rem; color: #999; margin-top: -10px; }

        @keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

    </style>

</head>

<body>

    <div class="loading-screen" id="loader">

        <div class="loading-heart">❤️</div>

        <div class="loading-text">Загружаю твою статистику</div>

        <div class="loading-dots"><span></span><span></span><span></span></div>

        <div class="loading-sub">Определяю часовой пояс…</div>

    </div>

    <script>

        (function() {

            var tz = '';

            try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}

            var v = '';

            var cookies = document.cookie || '';

            cookies.split(';').forEach(function(s) {

                var p = s.trim().split('=');

                if (p[0] === 'visitor_id' && p[1]) v = decodeURIComponent(p[1]).trim();

            });

            var params = [];

            if (tz) params.push('tz=' + encodeURIComponent(tz));

            if (v) params.push('v=' + encodeURIComponent(v));

            var langParam = (typeof localStorage !== 'undefined' && localStorage.getItem('memories_lang')) || 'ru';
            if (langParam) params.push('lang=' + encodeURIComponent(langParam));
            
            var texts = {
                'en': ['Loading your stats', 'Detecting timezone...'],
                'de': ['Lade deine Statistiken', 'Ermittle Zeitzone...'],
                'ru': ['Загружаю твою статистику', 'Определяю часовой пояс…']
            };
            var t = texts[langParam] || texts['ru'];
            var elText = document.querySelector('.loading-text');
            var elSub = document.querySelector('.loading-sub');
            if(elText) elText.textContent = t[0];
            if(elSub) elSub.textContent = t[1];

            var q = params.length ? '?' + params.join('&') : '';

            window.location.replace('/stats' + q);

        })();

    </script>

</body>

</html>"""

def _fmt_date_lang(iso: str, lang: str) -> str:
    if lang == 'de':
        _M = ["Jan.","Feb.","März","Apr.","Mai","Juni","Juli","Aug.","Sept.","Okt.","Nov.","Dez."]
    elif lang == 'en':
        _M = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    else:
        _M = ["января","февраля","марта","апреля","мая","июня",
              "июля","августа","сентября","октября","ноября","декабря"]
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(iso[:10], "%Y-%m-%d")
        if lang == 'en':
            return f"{_M[d.month-1]} {d.day}, {d.year}"
        elif lang == 'de':
            return f"{d.day}. {_M[d.month-1]} {d.year}"
        return f"{d.day} {_M[d.month-1]} {d.year}"
    except Exception:
        return iso

def _fmt_datetime_lang(dt_str: str, tz_id: str, lang: str) -> str:
    if not dt_str: return ""
    from datetime import datetime, timezone, timedelta
    from utils import _tz_offset
    try:
        if len(dt_str) >= 19 and dt_str[10] in (' ', 'T'):
            dt_utc = datetime.strptime(dt_str[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            offset = _tz_offset(tz_id, 0)
            dt_loc = dt_utc + timedelta(hours=offset)
        else:
            dt_loc = datetime.strptime(dt_str[:10], "%Y-%m-%d")
        
        M_ru = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
        M_en = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        M_de = ["Jan.","Feb.","März","Apr.","Mai","Juni","Juli","Aug.","Sept.","Okt.","Nov.","Dez."]
        
        d = dt_loc
        time_part = f" {d.hour:02}:{d.minute:02}" if len(dt_str) >= 19 else ""
        if lang == 'en':
            return f"{M_en[d.month-1]} {d.day}, {d.year} at{time_part}" if time_part else f"{M_en[d.month-1]} {d.day}, {d.year}"
        elif lang == 'de':
            return f"{d.day}. {M_de[d.month-1]} {d.year} um{time_part}" if time_part else f"{d.day}. {M_de[d.month-1]} {d.year}"
        else:
            return f"{d.day} {M_ru[d.month-1]} {d.year} в{time_part}" if time_part else f"{d.day} {M_ru[d.month-1]} {d.year}"
    except Exception:
        return dt_str


async def stats_page(request: web.Request) -> web.StreamResponse:

    """Отдаёт страницу с расширенной статистикой (на основе stats.html как шаблона). Без tz — показываем загрузчик с редиректом."""

    # Для /stats доверяем подписанной cookie-сессии или query-параметрам v+sig
    # (query-параметры нужны для Telegram WebView, где cookies недоступны).
    session_vid = (request.cookies.get("visitor_id") or "").strip() or (request.headers.get("X-Visitor-Id") or "").strip()
    session_sig = (request.cookies.get("visitor_sig") or "").strip() or (request.headers.get("X-Visitor-Signature") or "").strip()
    if not session_vid or not session_sig or not _verify_visitor_signature(session_vid, session_sig):
        # Fallback: принимаем v+sig из query (для Telegram WebView без cookies)
        session_vid = (request.rel_url.query.get("v") or "").strip()
        session_sig = (request.rel_url.query.get("sig") or "").strip()
        if not session_vid or not session_sig or not _verify_visitor_signature(session_vid, session_sig):
            raise web.HTTPFound("/404")
    _stats_uid = _visitor_to_user_id(session_vid)
    if not _stats_uid or not db.get_couple_by_user(_stats_uid):
        raise web.HTTPFound("/404")

    project_root = Path(__file__).resolve().parent

    stats_path = project_root / "stats.html"

    if not stats_path.exists():

        return web.Response(text="stats.html not found", status=404)

    # Временная зона и идентификатор посетителя (для персональной статистики)
    # Используем только cryptographically trusted visitor_id.
    visitor_id = session_vid

    # TZ: сначала из БД по visitor_id, затем из query-параметра (обратная совместимость)
    tz_id = None
    if visitor_id:
        _vis_uid = _visitor_to_user_id(visitor_id)
        if _vis_uid:
            tz_id = db.get_user_setting(_vis_uid, "timezone") or None
    if not tz_id:
        tz_id = (request.rel_url.query.get("tz") or "").strip() or None

    lang = (request.rel_url.query.get("lang") or "").strip()[:5]
    if not lang and visitor_id:
        _vis_uid2 = _visitor_to_user_id(visitor_id)
        if _vis_uid2:
            lang = (db.get_user_setting(_vis_uid2, "lang") or "").strip()[:5]
    if lang not in ("ru", "ky", "de", "en"):
        lang = "ru"

    # Переводы для всех строк генерируемых сервером
    _S = {
        "ru": {
            "page_title":      "Твоя статистика",
            "hero_badge":      "✨ Только для тебя",
            "hero_title":      "Твоя<br><em>статистика</em>",
            "hero_sub":        "Тут ты увидишь свою статистику сайта",
            "counter_from":    "С",
            "counter_days":    lambda d: f"· {d} дн.",
            "counter_no_data": "Ещё нет ни одного визита",
            "streak_day1":     "день",
            "streak_day234":   "дня",
            "streak_days":     "дней",
            "first_visit_sub": "Этот момент был долгожданным ❤️",
            "no_visit_yet":    "Ещё не было ни одного визита",
            "device_sub":      "По последнему входу ❤️",
            "no_data":         "Ещё нет данных",
            "fav_time_sub":    lambda pct: f"{pct}% всех визитов именно в это время",
            "fav_cat_opens":   lambda n, total: f"Открыто {n} раз из {total}" if total else f"Открыто {n} раз",
            "fav_cat_sub":     "Больше всего времени в этой категории",
            "fav_cat_sub_def": "Самая просматриваемая по времени",
            "fav_mem_meta":    lambda dur: f"Дольше всего здесь — {dur}",
            "dur_min_sec":     lambda m, s: f"{m} мин {s} сек",
            "dur_sec":         lambda s: f"{s} сек",
            "dur_hm":          lambda h, m: f"{h}ч {m}м",
            "slot_labels":     {"morning":"Утро 6–12","day":"День 12–18","evening":"Вечер 18–23","night":"Ночь 23–6"},
            "month_names":     ("январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"),
            "month_names_cap": ("Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"),
            "rec_longest":     "Самый долгий вход",
            "rec_streak":      "Серия дней подряд",
            "rec_most_visits": "Больше всего входов за день",
            "rec_fav_memory":  "Дольше всего на одном моменте",
            "rec_streak_num":  lambda d: f"{d} дн.",
            "rec_visits_num":  lambda n: f"{n} раз",
            "rec_empty":       "—",
            "no_fav_mem":      "Пока нет любимого момента",
            "no_title":        "(без названия)",
            "of_total_time":   lambda pct: f"{pct} от всего времени",
        },
        "ky": {
            "page_title":      "Сенин статистикаң",
            "hero_badge":      "✨ Сен үчүн гана",
            "hero_title":      "Сенин<br><em>статистикаң</em>",
            "hero_sub":        "Бул жерде сайттагы статистикаңды көрөсүң",
            "counter_from":    "Баштап",
            "counter_days":    lambda d: f"· {d} күн",
            "counter_no_data": "Али бир да кириш жок",
            "streak_day1":     "күн",
            "streak_day234":   "күн",
            "streak_days":     "күн",
            "first_visit_sub": "Сен чынында бул учурду күттүң ❤️",
            "no_visit_yet":    "Али бир да кириш болгон жок",
            "device_sub":      "Акыркы кириш боюнча ❤️",
            "no_data":         "Азырынча маалымат жок",
            "fav_time_sub":    lambda pct: f"Бардык кириштердин {pct}% ушул убакта",
            "fav_cat_opens":   lambda n, total: f"{total} дан {n} жолу ачылды" if total else f"{n} жолу ачылды",
            "fav_cat_sub":     "Бул категорияда эң көп убакыт өткөн",
            "fav_cat_sub_def": "Убакыт боюнча эң көп каралган",
            "fav_mem_meta":    lambda dur: f"Эң узак убакыт өткөн — {dur}",
            "dur_min_sec":     lambda m, s: f"{m} мин {s} сек",
            "dur_sec":         lambda s: f"{s} сек",
            "dur_hm":          lambda h, m: f"{h}с {m}м",
            "slot_labels":     {"morning":"Эртең 6–12","day":"Күн 12–18","evening":"Кеч 18–23","night":"Түн 23–6"},
            "month_names":     ("январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"),
            "month_names_cap": ("Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"),
            "rec_longest":     "Эң узак кириш",
            "rec_streak":      "Катарынан күн сериясы",
            "rec_most_visits": "Күнүнө эң көп кириш",
            "rec_fav_memory":  "Бир учурда эң көп убакыт",
            "rec_streak_num":  lambda d: f"{d} күн",
            "rec_visits_num":  lambda n: f"{n} жолу",
            "rec_empty":       "—",
            "no_fav_mem":      "Азырынча сүйүктүү учур жок",
            "no_title":        "(аталышсыз)",
            "of_total_time":   lambda pct: f"{pct} жалпы убакыттан",
        },
        "de": {
            "page_title":      "Deine Statistik",
            "hero_badge":      "✨ Nur für dich",
            "hero_title":      "Deine<br><em>Statistik</em>",
            "hero_sub":        "Hier siehst du deine persönliche Seitenstatistik",
            "counter_from":    "Seit",
            "counter_days":    lambda d: f"· {d} Tage",
            "counter_no_data": "Noch keine Besuche",
            "streak_day1":     "Tag",
            "streak_day234":   "Tage",
            "streak_days":     "Tage",
            "first_visit_sub": "Du hast auf diesen Moment gewartet ❤️",
            "no_visit_yet":    "Noch kein einziger Besuch",
            "device_sub":      "Letzter Besuch ❤️",
            "no_data":         "Noch keine Daten",
            "fav_time_sub":    lambda pct: f"{pct}% aller Besuche genau zu dieser Zeit",
            "fav_cat_opens":   lambda n, total: f"{n} von {total} mal geöffnet" if total else f"{n} mal geöffnet",
            "fav_cat_sub":     "Meiste Zeit in dieser Kategorie verbracht",
            "fav_cat_sub_def": "Meistgesehen nach Zeit",
            "fav_mem_meta":    lambda dur: f"Am längsten dort verbracht — {dur}",
            "dur_min_sec":     lambda m, s: f"{m} Min {s} Sek",
            "dur_sec":         lambda s: f"{s} Sek",
            "dur_hm":          lambda h, m: f"{h} Std {m} Min",
            "slot_labels":     {"morning":"Morgen 6–12","day":"Tag 12–18","evening":"Abend 18–23","night":"Nacht 23–6"},
            "month_names":     ("Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"),
            "month_names_cap": ("Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"),
            "rec_longest":     "Längster Besuch",
            "rec_streak":      "Tage-Serie",
            "rec_most_visits": "Meiste Besuche an einem Tag",
            "rec_fav_memory":  "Längste Zeit bei einem Moment",
            "rec_streak_num":  lambda d: f"{d} T.",
            "rec_visits_num":  lambda n: f"{n} mal",
            "rec_empty":       "—",
            "no_fav_mem":      "Noch kein Lieblingsmoment",
            "no_title":        "(ohne Titel)",
            "of_total_time":   lambda pct: f"{pct} der Gesamtzeit",
        },
        "en": {
            "page_title":      "Your statistics",
            "hero_badge":      "✨ Just for you",
            "hero_title":      "Your<br><em>statistics</em>",
            "hero_sub":        "Here you'll see your personal site statistics",
            "counter_from":    "Since",
            "counter_days":    lambda d: f"· {d} days",
            "counter_no_data": "No visits yet",
            "streak_day1":     "day",
            "streak_day234":   "days",
            "streak_days":     "days",
            "first_visit_sub": "You were really waiting for this moment ❤️",
            "no_visit_yet":    "Not a single visit yet",
            "device_sub":      "By last visit ❤️",
            "no_data":         "No data yet",
            "fav_time_sub":    lambda pct: f"{pct}% of all visits happened exactly at this time",
            "fav_cat_opens":   lambda n, total: f"Opened {n} times out of {total}" if total else f"Opened {n} times",
            "fav_cat_sub":     "Most time spent in this category",
            "fav_cat_sub_def": "Most viewed by time",
            "fav_mem_meta":    lambda dur: f"Spent the most time here — {dur}",
            "dur_min_sec":     lambda m, s: f"{m} min {s} sec",
            "dur_sec":         lambda s: f"{s} sec",
            "dur_hm":          lambda h, m: f"{h}h {m}m",
            "slot_labels":     {"morning":"Morning 6–12","day":"Day 12–18","evening":"Evening 18–23","night":"Night 23–6"},
            "month_names":     ("January","February","March","April","May","June","July","August","September","October","November","December"),
            "month_names_cap": ("January","February","March","April","May","June","July","August","September","October","November","December"),
            "rec_longest":     "Longest visit",
            "rec_streak":      "Days streak",
            "rec_most_visits": "Most visits in a day",
            "rec_fav_memory":  "Longest time on one moment",
            "rec_streak_num":  lambda d: f"{d} days",
            "rec_visits_num":  lambda n: f"{n} times",
            "rec_empty":       "—",
            "no_fav_mem":      "No favorite moment yet",
            "no_title":        "(no title)",
            "of_total_time":   lambda pct: f"{pct} of total time",
        },
    }[lang]

    # Если TZ не определён — используем UTC (страница отображается без редиректа)
    if not tz_id:
        tz_id = "UTC"

    try:

        html = stats_path.read_text(encoding="utf-8")

    except Exception:

        return web.Response(text="cannot read stats.html", status=500)

    # Статистика по visitor_id (у каждого пользователя своя)

    total_stats = db.get_total_stats()

    visits_summary = db.get_site_visits_summary(visitor_id)

    view_stats = db.get_memory_view_stats(visitor_id)

    last_visit = db.get_last_site_visit_info(visitor_id)

    total_visits = visits_summary.get("total", 0) or 0

    first_utc = visits_summary.get("first_utc")

    days_span = visits_summary.get("days_span", 0) or 0

    # Оформляем подпись под большим счётчиком

    counter_desc = ""

    if first_utc:

        # Используем тот же форматтер, что и для других дат (с учётом tz пользователя)

        try:

            human_first = _fmt_datetime_lang(first_utc, tz_id, lang)

        except Exception:

            human_first = first_utc

        if days_span > 0:

            counter_desc = _S["counter_from"] + " " + human_first + " " + _S["counter_days"](days_span)

        else:

            counter_desc = _S["counter_from"] + " " + human_first

    else:

        counter_desc = _S["counter_no_data"]

    memories_count = total_stats.get("total_memories", 0) or 0

    photos_count = total_stats.get("photo_count", 0) or 0

    # Суммарное время на сайте: из visitor_site_time (обновляется каждые 5 сек с фронта)

    total_sec = db.get_visitor_total_site_seconds(visitor_id)

    hours = total_sec // 3600

    minutes = (total_sec % 3600) // 60

    total_time_str = _S["dur_hm"](hours, minutes) if total_sec > 0 else _S["dur_hm"](0, 0)

    # Любимый момент

    fav_mem_id = view_stats.get("favorite_memory_id")

    fav_mem_title = ""

    fav_mem_cat_label = ""

    fav_mem_duration_str = ""

    fav_mem_percent = "0%"
    fav_mem_views = 0
    fav_mem_total_views = int(view_stats.get("total_memory_views", 0) or 0)

    if fav_mem_id:

        try:

            m = db.get_memory(fav_mem_id)

            if m:

                fav_mem_title = (m.title or _S["no_title"]).strip()

                cat_conf = config.CATEGORIES.get(m.category, {})

                fav_mem_cat_label = (cat_conf.get("title") or m.category).strip()
                # Translate category name
                _cat_map = {
                    "ru": {"important_moments":"Важные моменты","memories":"Воспоминания","important_dates":"Важные даты"},
                    "ky": {"important_moments":"Маанилүү учурлар","memories":"Эскеруулер","important_dates":"Маанилүү күндөр"},
                    "de": {"important_moments":"Wichtige Momente","memories":"Erinnerungen","important_dates":"Wichtige Daten"},
                    "en": {"important_moments":"Important moments","memories":"Memories","important_dates":"Important dates"},
                }
                fav_mem_cat_label = _cat_map.get(lang, _cat_map["ru"]).get(m.category, fav_mem_cat_label)
                if m.category.startswith("custom_"):
                    try:
                        cat_id = int(m.category.replace("custom_", ""))
                        cc = db.get_custom_category(cat_id)
                        if cc:
                            fav_mem_cat_label = cc.get("name", m.category).strip()
                    except Exception:
                        pass

        except Exception:

            pass

        fav_mem_views = int(view_stats.get("favorite_memory_views", 0) or 0)

        fav_dur = int(view_stats.get("favorite_memory_duration_sec", 0) or 0)

        if fav_dur > 0:

            mm = (fav_dur % 3600) // 60

            ss = fav_dur % 60

            if fav_dur >= 60:

                fav_mem_duration_str = _S["dur_min_sec"](mm, ss)

            else:

                fav_mem_duration_str = _S["dur_sec"](ss)

            if total_sec > 0:

                fav_mem_percent = f"{min(100, round(fav_dur * 100 / total_sec)):d}%"

    # Любимое устройство (последний визит)

    device_str = ""

    if last_visit and last_visit.get("ua_pretty"):

        device_str = last_visit["ua_pretty"]

    last_device_sub = _S["device_sub"] if device_str else _S["no_data"]

    # Серия подряд и фото открыто (по visitor_id)

    streak_days = db.get_streak_days(visitor_id)

    if streak_days == 0:

        streak_str = "0 " + _S["streak_days"]

    elif streak_days == 1:

        streak_str = "1 " + _S["streak_day1"]

    elif 2 <= streak_days <= 4:

        streak_str = str(streak_days) + " " + _S["streak_day234"]

    else:

        streak_str = str(streak_days) + " " + _S["streak_days"]

    photos_opened = db.get_photos_opened_count(visitor_id)

    # Первый вход — дата и подпись

    first_visit_date = "—"

    first_visit_sub = _S["no_visit_yet"]

    if first_utc:

        try:

            first_visit_date = _fmt_datetime_lang(first_utc, tz_id, lang)

            first_visit_sub = _S["first_visit_sub"]

        except Exception:

            first_visit_date = first_utc

    # Любимое время суток (распределение по слотам в часовом поясе пользователя)

    tz_obj = ZoneInfo(tz_id) if tz_id else ZoneInfo("UTC")

    slot_counts = {"morning": 0, "day": 0, "evening": 0, "night": 0}

    for utc_str, visit_tz_id in db.get_visits_for_time_slots(visitor_id):

        try:

            dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)

            local_tz = ZoneInfo(visit_tz_id) if visit_tz_id else tz_obj

            local = dt_utc.astimezone(local_tz)

            h = local.hour

            if 6 <= h < 12:

                slot_counts["morning"] += 1

            elif 12 <= h < 18:

                slot_counts["day"] += 1

            elif 18 <= h < 23:

                slot_counts["evening"] += 1

            else:

                slot_counts["night"] += 1

        except Exception:

            slot_counts["night"] += 1

    total_slots = sum(slot_counts.values())

    time_morning_pct = round(slot_counts["morning"] * 100 / total_slots) if total_slots else 0

    time_day_pct = round(slot_counts["day"] * 100 / total_slots) if total_slots else 0

    time_evening_pct = round(slot_counts["evening"] * 100 / total_slots) if total_slots else 0

    time_night_pct = round(slot_counts["night"] * 100 / total_slots) if total_slots else 0

    # Любимый слот и подпись (max при равенстве берёт первый по порядку ключа)

    slot_labels = _S["slot_labels"]

    slot_pcts = {"morning": time_morning_pct, "day": time_day_pct, "evening": time_evening_pct, "night": time_night_pct}

    fav_slot = max(slot_counts, key=slot_counts.get)

    fav_slot_name = slot_labels[fav_slot]

    fav_slot_pct = slot_pcts[fav_slot]

    fav_time_label = fav_slot_name if total_slots else "—"

    fav_time_sub = _S["fav_time_sub"](fav_slot_pct) if total_slots else _S["no_data"]

    # Любимая категория (по времени просмотра); «X раз из Y» — по открытиям секции «Показать»

    fav_cat = db.get_favorite_category(visitor_id)

    category_opens = db.get_category_opens(visitor_id)

    opens_total = category_opens.get("total") or 0

    opens_by_section = category_opens.get("by_section") or {}
    migrated_opens = {}
    for k, v in opens_by_section.items():
        if k.startswith("custom_cat_container_"):
            new_k = "custom_" + k[21:]
            migrated_opens[new_k] = migrated_opens.get(new_k, 0) + v
        else:
            migrated_opens[k] = migrated_opens.get(k, 0) + v
    opens_by_section = migrated_opens

    fav_category_label = "—"

    fav_category_value = "—"

    fav_category_sub = _S["fav_cat_sub_def"]

    if fav_cat:

        fav_category_label = fav_cat.get("label") or fav_cat.get("category", "—")

        section_opens = opens_by_section.get(fav_cat.get("category", ""), 0)

        fav_category_value = _S["fav_cat_opens"](section_opens, opens_total)

        fav_category_sub = _S["fav_cat_sub"]

    # Текущий месяц в часовом поясе пользователя для теплокарты

    now_utc = datetime.now(timezone.utc)

    now_local = now_utc.astimezone(tz_obj)

    heatmap_year, heatmap_month = now_local.year, now_local.month

    heatmap_month_label = _S["month_names"][heatmap_month - 1]

    last_day = calendar.monthrange(heatmap_year, heatmap_month)[1]

    visits_by_day = db.get_visits_per_day_for_month(heatmap_year, heatmap_month, visitor_id)

    max_visits = max(visits_by_day.values()) if visits_by_day else 0

    heatmap_days_html_parts = []

    for day in range(1, last_day + 1):

        cnt = visits_by_day.get(day, 0)

        if max_visits <= 0:

            level_class = ""

        else:

            q = cnt / max_visits

            if q <= 0:

                level_class = ""

            elif q <= 0.2:

                level_class = " d1"

            elif q <= 0.4:

                level_class = " d2"

            elif q <= 0.6:

                level_class = " d3"

            elif q <= 0.8:

                level_class = " d4"

            else:

                level_class = " d5"

        heatmap_days_html_parts.append(f'<div class="hm-day{level_class}">{day}</div>')

    heatmap_days_html = "\n      ".join(heatmap_days_html_parts)

    # Рекорды (по visitor_id)

    longest_day = db.get_longest_viewing_day(visitor_id)

    longest_streak = db.get_longest_streak(visitor_id)

    most_visits = db.get_most_visits_in_day(visitor_id)

    record_rows = []

    # 1) Самый длинный визит (день с макс. временем просмотра)

    if longest_day:

        d_iso, dur_sec = longest_day

        date_short = _fmt_date_lang(d_iso, lang)
        mm = dur_sec // 60

        record_rows.append(

            f'<div class="record-row"><div class="record-medal">🥇</div><div class="record-body">'

            '<div class="record-name">' + _S["rec_longest"] + '</div><div class="record-val">' + date_short + '</div></div>'

            + '<div class="record-num">' + str(mm) + (' мин' if lang == 'ru' else (' мин' if lang == 'ky' else (' Min' if lang == 'de' else ' min'))) + '</div></div>'

        )

    else:

        record_rows.append(

            '<div class="record-row"><div class="record-medal">🥇</div><div class="record-body">'

            + '<div class="record-name">' + _S["rec_longest"] + '</div><div class="record-val">' + _S["rec_empty"] + '</div></div>'

            + '<div class="record-num">' + _S["rec_empty"] + '</div></div>'

        )

    # 2) Серия дней подряд

    if longest_streak:

        start_iso, end_iso, days = longest_streak

        range_str = _fmt_date_lang(start_iso, lang) + " — " + _fmt_date_lang(end_iso, lang)

        record_rows.append(

            f'<div class="record-row"><div class="record-medal">🥈</div><div class="record-body">'

            '<div class="record-name">' + _S["rec_streak"] + '</div><div class="record-val">' + range_str + '</div></div>'

            f'<div class="record-num">' + _S['rec_streak_num'](days) + '</div></div>'

        )

    else:

        record_rows.append(

            '<div class="record-row"><div class="record-medal">🥈</div><div class="record-body">'

            + '<div class="record-name">' + _S["rec_streak"] + '</div><div class="record-val">' + _S["rec_empty"] + '</div></div>'

            + '<div class="record-num">' + _S["rec_empty"] + '</div></div>'

        )

    # 3) Больше всего визитов за день

    if most_visits:

        d_iso, cnt = most_visits

        date_short = _fmt_date_lang(d_iso, lang)

        record_rows.append(

            f'<div class="record-row"><div class="record-medal">🥉</div><div class="record-body">'

            '<div class="record-name">' + _S["rec_most_visits"] + '</div><div class="record-val">' + date_short + '</div></div>'

            f'<div class="record-num">' + _S['rec_visits_num'](cnt) + '</div></div>'

        )

    else:

        record_rows.append(

            '<div class="record-row"><div class="record-medal">🥉</div><div class="record-body">'

            + '<div class="record-name">' + _S["rec_most_visits"] + '</div><div class="record-val">' + _S["rec_empty"] + '</div></div>'

            + '<div class="record-num">' + _S["rec_empty"] + '</div></div>'

        )

    # 4) Дольше всего на одном моменте

    if fav_mem_id and fav_mem_duration_str:

        record_rows.append(

            f'<div class="record-row"><div class="record-medal">💫</div><div class="record-body">'

            '<div class="record-name">' + _S["rec_fav_memory"] + '</div><div class="record-val">' + fav_mem_title + '</div></div>'

            f'<div class="record-num">{fav_mem_duration_str}</div></div>'

        )

    else:

        record_rows.append(

            '<div class="record-row"><div class="record-medal">💫</div><div class="record-body">'

            + '<div class="record-name">' + _S["rec_fav_memory"] + '</div><div class="record-val">' + _S["rec_empty"] + '</div></div>'

            + '<div class="record-num">' + _S["rec_empty"] + '</div></div>'

        )

    records_html = "\n    ".join(record_rows)

    # Подставляем значения в шаблон (простая замена плейсхолдеров)

    html = html.replace("{{TOTAL_VISITS}}", str(total_visits))

    html = html.replace("{{COUNTER_DESC}}", counter_desc)

    html = html.replace("{{MEMORIES_COUNT}}", str(memories_count))

    html = html.replace("{{PHOTOS_COUNT}}", str(photos_count))

    html = html.replace("{{PHOTOS_OPENED}}", str(photos_opened))

    html = html.replace("{{TOTAL_TIME}}", total_time_str)

    html = html.replace("{{STREAK_DAYS}}", streak_str)
    html = html.replace("{{STREAK_DAYS_NUM}}", str(streak_days))

    html = html.replace("{{FAV_MEMORY_TITLE}}", fav_mem_title or _S["no_fav_mem"])

    html = html.replace("{{FAV_MEMORY_CATEGORY}}", fav_mem_cat_label or "—")

    html = html.replace("{{FAV_MEMORY_DURATION}}", fav_mem_duration_str or "0 сек")
    html = html.replace("{{FAV_MEMORY_META}}", _S["fav_mem_meta"](fav_mem_duration_str or "0 сек"))
    html = html.replace("{{STATS_PAGE_TITLE}}", _S["page_title"])
    html = html.replace("{{STATS_HERO_BADGE}}", _S["hero_badge"])
    html = html.replace("{{STATS_HERO_TITLE}}", _S["hero_title"])
    html = html.replace("{{STATS_HERO_SUB}}", _S["hero_sub"])
    html = html.replace("{{STATS_LANG}}", lang)

    html = html.replace("{{FAV_MEMORY_PERCENT}}", fav_mem_percent)
    html = html.replace("{{STAT_OF_TOTAL_TIME}}", _S["of_total_time"](fav_mem_percent))
    html = html.replace("{{FAV_MEMORY_VIEWS}}", str(fav_mem_views))
    html = html.replace("{{FAV_MEMORY_TOTAL_VIEWS}}", str(fav_mem_total_views))

    html = html.replace("{{LAST_DEVICE}}", device_str or _S["no_data"])

    html = html.replace("{{LAST_DEVICE_SUB}}", last_device_sub)

    html = html.replace("{{FIRST_VISIT_DATE}}", first_visit_date)

    html = html.replace("{{FIRST_VISIT_SUB}}", first_visit_sub)

    html = html.replace("{{FAV_TIME_LABEL}}", fav_time_label)

    html = html.replace("{{FAV_TIME_SUB}}", fav_time_sub)

    html = html.replace("{{FAV_CATEGORY_LABEL}}", fav_category_label)

    html = html.replace("{{FAV_CATEGORY_VALUE}}", fav_category_value)

    html = html.replace("{{FAV_CATEGORY_SUB}}", fav_category_sub)

    heatmap_month_year = heatmap_month_label.capitalize() + " " + str(heatmap_year)
    html = html.replace("{{HEATMAP_MONTH}}", heatmap_month_label)
    html = html.replace("{{HEATMAP_MONTH_YEAR}}", heatmap_month_year)
    _vid_js = (visitor_id or "").replace('"', "")
    html = html.replace("</body>",
        '<script>window.__STATS_VID__="' + _vid_js + '";</script></body>',
        1)


    html = html.replace("{{HEATMAP_DAYS_HTML}}", heatmap_days_html)

    html = html.replace("{{TIME_MORNING_PCT}}", str(time_morning_pct))

    html = html.replace("{{TIME_DAY_PCT}}", str(time_day_pct))

    html = html.replace("{{TIME_EVENING_PCT}}", str(time_evening_pct))

    html = html.replace("{{TIME_NIGHT_PCT}}", str(time_night_pct))

    html = html.replace("{{RECORDS_HTML}}", records_html)

    return web.Response(text=html, content_type="text/html", charset="utf-8")

# ═══════════════════════════ DDoS Protection ════════════════════════════
#
# Многоуровневая система защиты:
#   1. Скользящее окно — подсчёт запросов за N секунд для каждого IP
#   2. Прогрессивный бан — чем больше нарушений, тем дольше блокировка
#   3. Burst-детектор — считает запросы за последние 2 секунды
#   4. Глобальный счётчик — защита от распределённых атак с множества IP
# ═══════════════════════════════════════════════════════════════════════

# ── 1. Sliding window buckets ─────────────────────────────────────────
# { "ip:prefix": [monotonic_timestamp, ...] }
_rl_buckets: dict = defaultdict(list)

# (path_prefix, max_requests, window_seconds, enforce_ban)
# Порядок важен — первый совпавший префикс применяется
_RL_RULES: list = [
    ("/api/token_auth",          8,  60, True),    # авторизация
    ("/api/check_site_password", 4,  60, True),    # пароль
    ("/api/ai_companion",       20,  60, True),    # дорогой LLM
    ("/api/create_memory",      30,  60, True),    # мутации данных
    ("/api/create_event",       30,  60, True),
    ("/api/create_wish",        30,  60, True),
    ("/api/upload",             10,  60, True),    # загрузка файлов
    ("/api/visit_heartbeat",   180,  60, False),   # фоновый heartbeat
    ("/api/visit",             120,  60, False),   # визиты/телеметрия
    ("/api/",                  220,  60, False),   # остальные API — мягко
    ("/",                      240,  60, False),   # страницы/статика — без бана
]

# ── 2. Progressive ban table ──────────────────────────────────────────
# { ip: {"count": int, "until": monotonic, "total_violations": int} }
_ban_table: dict = {}

# Прогрессивные периоды бана по числу нарушений: 1-е→60с, 2-е→5мин, 3-е→30мин, 4+→2ч
_BAN_DURATIONS = [60, 300, 1_800, 7_200]

# ── 3. Burst detector ─────────────────────────────────────────────────
# { ip: [timestamps за последние 2 сек] }
_burst_buckets: dict = defaultdict(list)
_BURST_WINDOW   = 2      # секунды
_BURST_LIMIT    = 30     # макс запросов за 2 сек с одного IP

# ── 4. Global request counter ─────────────────────────────────────────
_global_req_window: list = []   # временны́е метки всех запросов
_GLOBAL_RPS_LIMIT = 500         # макс 500 запросов в секунду на весь сервер

# ── 5. Per-visitor AI companion rate limiter ───────────────────────────
# Предотвращает флуд к дорогому LLM-эндпоинту.
# Ключ: visitor_id (или IP если visitor_id отсутствует).
# Значение: список временны́х меток запросов за последние _AI_RL_WINDOW секунд.
_ai_rl_buckets: dict = defaultdict(list)
_ai_rl_last_seen: dict[str, float] = {}
_AI_RL_WINDOW   = 60   # скользящее окно, секунды
_AI_RL_LIMIT    = 15   # макс запросов в минуту на одного посетителя
_AI_RL_MAX_KEYS = 10000  # hard-cap числа уникальных ключей в памяти
_AI_RL_SWEEP_EVERY = 250  # каждые N запросов выполняем очистку старых ключей
_ai_rl_ops_count = 0
_AI_MAX_MSG_LEN = 2000 # макс длина сообщения пользователя (символы)

# ── 5b. Бизнес-лимит ИИ-компаньона (сессионное окно 24ч) ─────────────
# Использует таблицу ai_usage_window в SQLite.
# Антифлуд (_ai_rate_check) остаётся первым барьером.

def _get_companion_limit_key(visitor_id: str) -> str:
    """Ключ для таблицы ai_usage_window. Сейчас — visitor_id напрямую."""
    return f"vid:{visitor_id}" if visitor_id else ""

def _companion_limit_message() -> str:
    return (
        "🌙 На сегодня всё.\n\n"
        "Ты использовал все сообщения за этот период. "
        "Лимит полностью сбросится через 24 часа после "
        "твоего первого сообщения этого периода.\n\n"
        "Увидимся совсем скоро! 💛"
    )



# ── 6. Runtime metrics ───────────────────────────────────────────────────
_metrics = {
    "started_at": time.time(),
    "total_requests": 0,
    "status_2xx": 0,
    "status_3xx": 0,
    "status_4xx": 0,
    "status_5xx": 0,
    "rate_limited": 0,
    "forbidden": 0,
    "errors": 0,
    "latency_ms_sum": 0.0,
    "latency_count": 0,
}

_ops_429_events = deque(maxlen=3000)
_ops_5xx_events = deque(maxlen=1000)
_ops_latency_ms = deque(maxlen=5000)
_ops_last_alert_at = {"429": 0.0, "5xx": 0.0, "lat": 0.0}
_OPS_ALERT_COOLDOWN_SEC = 300
_ops_last_eval_ts = 0.0


async def _send_ops_alert(kind: str, text: str) -> None:
    now = time.time()
    if now - _ops_last_alert_at.get(kind, 0.0) < _OPS_ALERT_COOLDOWN_SEC:
        return
    _ops_last_alert_at[kind] = now
    try:
        bot = Bot(token=config.BOT_TOKEN)
        await bot.send_message(chat_id=config.CREATOR_ID, text=text, parse_mode=ParseMode.HTML)
        await bot.session.close()
    except Exception:
        logger.debug("ops alert send failed for kind=%s", kind)


async def _maybe_emit_ops_alerts() -> None:
    global _ops_last_eval_ts
    now = time.time()
    if now - _ops_last_eval_ts < 20:
        return
    _ops_last_eval_ts = now

    cutoff_60 = now - 60
    while _ops_429_events and _ops_429_events[0] < cutoff_60:
        _ops_429_events.popleft()
    while _ops_5xx_events and _ops_5xx_events[0] < cutoff_60:
        _ops_5xx_events.popleft()

    if len(_ops_429_events) >= 120:
        await _send_ops_alert(
            "429",
            f"⚠️ <b>Rate-limit spike</b>\n429 за 60с: <b>{len(_ops_429_events)}</b>\nПроверь нагрузку/бот-трафик."
        )
    if len(_ops_5xx_events) >= 8:
        await _send_ops_alert(
            "5xx",
            f"❌ <b>Server errors spike</b>\n5xx за 60с: <b>{len(_ops_5xx_events)}</b>\nНужна проверка логов."
        )
    if _ops_latency_ms:
        sample = list(_ops_latency_ms)[-200:]
        avg_ms = sum(sample) / max(1, len(sample))
        if avg_ms >= 1500:
            await _send_ops_alert(
                "lat",
                f"🐢 <b>Latency degraded</b>\nСредняя latency (последние {len(sample)} req): <b>{avg_ms:.0f} ms</b>"
            )


def _ai_rate_check(key: str) -> bool:
    """True → запрос разрешён; False → превышен лимит."""
    global _ai_rl_ops_count
    if not key:
        return True
    now = time.monotonic()
    _ai_rl_ops_count += 1

    # Периодическая очистка "мертвых" ключей, чтобы структура не росла бесконечно.
    if _ai_rl_ops_count % _AI_RL_SWEEP_EVERY == 0:
        stale_before = now - (_AI_RL_WINDOW * 2)
        stale_keys = [k for k, ts in _ai_rl_last_seen.items() if ts < stale_before]
        for k in stale_keys:
            _ai_rl_last_seen.pop(k, None)
            _ai_rl_buckets.pop(k, None)

    # Fail-closed на случай попытки DoS большим числом уникальных ключей.
    if key not in _ai_rl_buckets and len(_ai_rl_buckets) >= _AI_RL_MAX_KEYS:
        logger.warning("AI rate-limit table overflow: max_keys=%d", _AI_RL_MAX_KEYS)
        return False

    bucket = _ai_rl_buckets[key]
    _ai_rl_buckets[key] = [t for t in bucket if now - t < _AI_RL_WINDOW]
    if len(_ai_rl_buckets[key]) >= _AI_RL_LIMIT:
        _ai_rl_last_seen[key] = now
        return False
    _ai_rl_buckets[key].append(now)
    _ai_rl_last_seen[key] = now
    return True


def _is_banned(client_key: str, now: float) -> bool:
    """True если клиент сейчас в бане."""
    entry = _ban_table.get(client_key)
    if not entry:
        return False
    if now < entry["until"]:
        return True
    # Бан истёк — оставляем счётчик нарушений, но убираем блокировку
    entry["until"] = 0.0
    return False


def _add_violation(client_key: str, now: float) -> float:
    """Фиксирует нарушение, возвращает длительность нового бана."""
    entry = _ban_table.setdefault(client_key, {"count": 0, "until": 0.0, "total": 0})
    entry["total"] += 1
    tier = min(entry["total"] - 1, len(_BAN_DURATIONS) - 1)
    duration = _BAN_DURATIONS[tier]
    entry["until"] = now + duration
    return duration


# IP-адреса полностью освобождены от rate limit (localhost, тесты, внутренние вызовы)
_RATE_LIMIT_WHITELIST: frozenset = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_whitelisted_ip(ip: str) -> bool:
    if ip in _RATE_LIMIT_WHITELIST:
        return True
    try:
        import ipaddress
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback
    except Exception:
        return False


def _ddos_check(client_key: str, path: str, display_ip: str) -> tuple[bool, str]:
    """
    Комплексная проверка. Возвращает (allowed: bool, reason: str).
    Вызывается синхронно из middleware.
    """
    # Localhost и внутренние IP всегда пропускаем
    if _is_whitelisted_ip(display_ip):
        return True, "ok"

    now = time.monotonic()

    # ── Шаг 1: проверяем бан ─────────────────────────────────────────
    if _is_banned(client_key, now):
        entry = _ban_table[client_key]
        remaining = int(entry["until"] - now)
        return False, f"banned:{remaining}s"

    is_api_path = path.startswith("/api/")

    # ── Шаг 2: burst — слишком много запросов за 2 секунды ───────────
    burst = _burst_buckets[client_key]
    _burst_buckets[client_key] = [t for t in burst if now - t < _BURST_WINDOW]
    _burst_buckets[client_key].append(now)
    if len(_burst_buckets[client_key]) > _BURST_LIMIT:
        if is_api_path:
            dur = _add_violation(client_key, now)
            logger.warning("DDoS burst: ip=%s key=%s reqs=%d ban=%ds", display_ip, client_key, len(_burst_buckets[client_key]), dur)
            return False, f"burst_ban:{dur}s"
        logger.warning("DDoS burst-soft: ip=%s key=%s reqs=%d path=%s", display_ip, client_key, len(_burst_buckets[client_key]), path)
        return False, "burst_soft:10s"

    # ── Шаг 3: глобальный RPS-лимит (защита от distributed flood) ────
    _global_req_window[:] = [t for t in _global_req_window if now - t < 1.0]
    _global_req_window.append(now)
    if len(_global_req_window) > _GLOBAL_RPS_LIMIT:
        logger.warning("DDoS global RPS exceeded: rps=%d ip=%s", len(_global_req_window), display_ip)
        return False, "global_overload"

    # ── Шаг 4: скользящее окно по endpoint-группам ───────────────────
    for prefix, max_req, window, enforce_ban in _RL_RULES:
        if path.startswith(prefix):
            key = f"{client_key}:{prefix}"
            bucket = _rl_buckets[key]
            _rl_buckets[key] = [t for t in bucket if now - t < window]
            if len(_rl_buckets[key]) >= max_req:
                if enforce_ban:
                    dur = _add_violation(client_key, now)
                    logger.warning(
                        "DDoS rate-ban: ip=%s key=%s path=%s limit=%d/%ds ban=%ds",
                        display_ip, client_key, prefix, max_req, window, dur
                    )
                    return False, f"rate_ban:{dur}s"
                logger.warning(
                    "DDoS rate-soft: ip=%s key=%s path=%s limit=%d/%ds",
                    display_ip, client_key, prefix, max_req, window
                )
                retry = max(1, int(window / max_req * 2))
                return False, f"rate_soft:{retry}s"
            _rl_buckets[key].append(now)
            break

    return True, "ok"


def _extract_client_ip(request: web.Request) -> str:
    """
    Определяет IP клиента с учётом прокси.
    Приоритет: CF-Connecting-IP -> X-Real-IP -> X-Forwarded-For -> request.remote.
    """
    import ipaddress

    candidates = []
    cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
    if cf_ip:
        candidates.append(cf_ip)
    x_real = (request.headers.get("X-Real-IP") or "").strip()
    if x_real:
        candidates.append(x_real)
    xff = (request.headers.get("X-Forwarded-For") or "").strip()
    if xff:
        candidates.extend([p.strip() for p in xff.split(",") if p.strip()])
    if request.remote:
        candidates.append(request.remote)

    for c in candidates:
        try:
            ipaddress.ip_address(c)
            return c
        except Exception:
            continue
    return request.remote or "unknown"


def _client_rate_key(request: web.Request) -> tuple[str, str]:
    """
    Возвращает (client_key, display_ip).
    Ключ включает IP + fingerprint UA, чтобы пользователи за одним прокси не банили друг друга.
    """
    ip = _extract_client_ip(request)
    ua = (request.headers.get("User-Agent") or "")[:256]
    ua_f = hashlib.sha256(ua.encode("utf-8", errors="ignore")).hexdigest()[:12]
    return f"{ip}|ua:{ua_f}", ip


async def _ddos_cleanup():
    """Периодически очищает устаревшие записи, чтобы не росла память."""
    while True:
        await asyncio.sleep(300)  # каждые 5 минут
        now = time.monotonic()
        cutoff = now - 3600  # удаляем данные старше часа

        for key in list(_rl_buckets.keys()):
            _rl_buckets[key] = [t for t in _rl_buckets[key] if t > cutoff]
            if not _rl_buckets[key]:
                del _rl_buckets[key]

        for key in list(_burst_buckets.keys()):
            _burst_buckets[key] = [t for t in _burst_buckets[key] if t > cutoff]
            if not _burst_buckets[key]:
                del _burst_buckets[key]

        token_cutoff = now - _TOKEN_AUTH_WINDOW_SEC
        for key in list(_token_auth_buckets.keys()):
            _token_auth_buckets[key] = [t for t in _token_auth_buckets[key] if t > token_cutoff]
            if not _token_auth_buckets[key]:
                del _token_auth_buckets[key]

        # Чистим истёкшие баны (старше 3ч)
        for ip in list(_ban_table.keys()):
            if _ban_table[ip].get("until", 0) < now - 10_800:
                del _ban_table[ip]

        logger.debug("DDoS cleanup: rl_keys=%d burst_keys=%d bans=%d",
                     len(_rl_buckets), len(_burst_buckets), len(_ban_table))


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    # OPTIONS preflight и WebSocket Upgrade — освобождены от rate-limit.
    # OPTIONS всегда нужны для CORS; WS — долгоживущие соединения, не burst.
    if request.method == "OPTIONS":
        return await handler(request)
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await handler(request)

    client_key, display_ip = _client_rate_key(request)
    allowed, reason = _ddos_check(client_key, request.path, display_ip)
    if not allowed:
        logger.warning("Rate limit blocked: ip=%s key=%s path=%s reason=%s", display_ip, client_key, request.path, reason)
        retry_after = reason.split(":")[1].rstrip("s") if ":" in reason else "60"
        return web.json_response(
            {"ok": False, "error": "rate_limited", "reason": reason},
            status=429,
            headers={"Retry-After": retry_after}
        )
    return await handler(request)


# ──────────────── Body Size Guard (upload vs API) ────────────────────────
# Эндпоинты загрузки файлов могут принимать до 500 МБ (видео).
# Все остальные API-запросы ограничены 10 МБ для защиты от DoS.

_UPLOAD_PATHS = {
    "/api/upload_media",
    "/api/upload_notification_media",
    "/api/upload_avatar",
}
_API_MAX_BODY = 10 * 1024 * 1024  # 10 MB для обычных API
_UPLOAD_MAX_BY_PATH = {
    "/api/upload_media": 100 * 1024 * 1024,               # 100 MB
    "/api/upload_notification_media": 50 * 1024 * 1024,   # 50 MB
    "/api/upload_avatar": 10 * 1024 * 1024,               # 10 MB
}

# Точечный анти-брутфорс для /api/token_auth
_token_auth_buckets: dict[str, list[float]] = {}
_TOKEN_AUTH_WINDOW_SEC = 300
_TOKEN_AUTH_MAX_ATTEMPTS = 30


@web.middleware
async def body_size_middleware(request: web.Request, handler):
    cl = request.content_length
    if request.path in _UPLOAD_PATHS:
        max_upload = _UPLOAD_MAX_BY_PATH.get(request.path, 0)
        if max_upload and cl is not None and cl > max_upload:
            return web.json_response(
                {"ok": False, "error": "file_too_large"},
                status=413,
            )
        return await handler(request)

    if cl is not None and cl > _API_MAX_BODY:
        return web.json_response(
            {"ok": False, "error": "request_too_large"},
            status=413,
        )
    return await handler(request)


# ─────────────────────────── Security Headers ────────────────────────────

@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-XSS-Protection", "1; mode=block")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; media-src 'self' https: blob:; connect-src 'self' https: ws: wss:; "
        "object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
    )
    # CORS — гарантируем наличие заголовка на всех ответах (включая 429/413)
    response.headers.setdefault("Access-Control-Allow-Origin", _allowed_cors_origin(request))
    response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type, X-Api-Key, X-Visitor-Id, X-Visitor-Signature, X-AI-Session")
    # Скрываем стек технологий
    response.headers["Server"] = "nginx"

    status = int(getattr(response, "status", 0) or 0)
    _metrics["total_requests"] += 1
    if 200 <= status < 300:
        _metrics["status_2xx"] += 1
    elif 300 <= status < 400:
        _metrics["status_3xx"] += 1
    elif 400 <= status < 500:
        _metrics["status_4xx"] += 1
        if status == 403:
            _metrics["forbidden"] += 1
        elif status == 429:
            _metrics["rate_limited"] += 1
            _ops_429_events.append(time.time())
    elif status >= 500:
        _metrics["status_5xx"] += 1
        _ops_5xx_events.append(time.time())

    started = request.get("_req_started_monotonic")
    if started:
        latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        _metrics["latency_ms_sum"] += latency_ms
        _metrics["latency_count"] += 1
        _ops_latency_ms.append(latency_ms)

    await _maybe_emit_ops_alerts()
    return response


# ─────────────────────────── Error Middleware ─────────────────────────────

@web.middleware
async def site_error_middleware(request: web.Request, handler):

    """Глобальный перехватчик ошибок HTTP‑части: шлём подробности создателю в бота."""

    try:
        request["_req_started_monotonic"] = time.monotonic()
        return await handler(request)

    except web.HTTPException:

        # Стандартные HTTP‑ошибки не считаем авариями

        raise

    except (ConnectionResetError, asyncio.CancelledError):
        # Клиент закрыл соединение до завершения ответа; не считаем это 500-ошибкой сервера.
        logger.debug(
            "HTTP client disconnected: method=%s path=%s ip=%s",
            request.method,
            request.path_qs,
            request.remote or "-",
        )
        raise

    except Exception as e:

        logger.exception("Необработанная ошибка HTTP запроса")
        _metrics["errors"] += 1
        _metrics["status_5xx"] += 1
        _metrics["total_requests"] += 1

        # Формируем краткий текст для создателя

        try:

            tb = traceback.format_exc(limit=10)

            text = (

                "❌ <b>Ошибка на сайте</b>\n\n"

                f"<b>Метод:</b> {request.method}\n"

                f"<b>Путь:</b> {request.path_qs}\n"

                f"<b>IP:</b> {request.remote or '-'}\n\n"

                f"<b>Ошибка:</b> <code>{repr(e)}</code>\n\n"

                "<b>Traceback:</b>\n"

                f"<code>{tb}</code>"

            )

            bot = Bot(token=config.BOT_TOKEN)

            await bot.send_message(chat_id=config.CREATOR_ID, text=text, parse_mode=ParseMode.HTML)

            await bot.session.close()

        except Exception:

            # Не даём вторичной ошибке сломать отдачу ответа

            pass

        resp = web.Response(status=500, text="Internal Server Error")

        return _add_cors_headers(resp)

async def _upload_file_to_telegram(bot: Bot, media_path: str, media_type: str, chat_id: int) -> str | None:
    """
    Загружает файл с диска в Telegram через указанный чат и возвращает file_id.
    Сообщение после загрузки сразу удаляется — оно нужно только для получения file_id.
    Возвращает file_id или None при ошибке.
    """
    import os
    from aiogram.types import FSInputFile
    if not media_path or not os.path.exists(media_path):
        return None
    try:
        file = FSInputFile(media_path)
        if media_type == "photo":
            msg = await bot.send_photo(chat_id=chat_id, photo=file)
            file_id = msg.photo[-1].file_id
        elif media_type == "video":
            msg = await bot.send_video(chat_id=chat_id, video=file)
            file_id = msg.video.file_id
        elif media_type == "video_note":
            msg = await bot.send_video_note(chat_id=chat_id, video_note=file)
            file_id = msg.video_note.file_id
        elif media_type == "voice":
            msg = await bot.send_voice(chat_id=chat_id, voice=file)
            file_id = msg.voice.file_id
        elif media_type == "audio":
            msg = await bot.send_audio(chat_id=chat_id, audio=file)
            file_id = msg.audio.file_id
        else:
            msg = await bot.send_document(chat_id=chat_id, document=file)
            file_id = msg.document.file_id
        # Удаляем техническое сообщение
        try:
            await bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
        except Exception:
            pass
        return file_id
    except Exception as e:
        logger.warning("_upload_file_to_telegram error: %s", e)
        return None


async def site_create_memory(request: web.Request) -> web.Response:
    """Создаёт воспоминание/важный момент/важную дату с сайта."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        p = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))

    visitor_id = _get_trusted_visitor_id(request, payload=p, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    category = _pstr(p.get("category")).strip()
    title    = _pstr(p.get("title")).strip()
    date     = _pstr(p.get("date")).strip()
    content  = _pstr(p.get("content")).strip()

    allowed_cats = {"important_moments", "memories", "important_dates"}
    is_custom = category.startswith("custom_") and category[7:].isdigit()
    if not category or (category not in allowed_cats and not is_custom):
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid category"}, status=400))
    if not title or not date or not content:
        return _add_cors_headers(web.json_response({"ok": False, "error": "missing fields"}, status=400))

    # Определяем user_id из visitor_id (поддержка legacy строк и новых числовых id)
    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unknown visitor"}, status=400))

    media_path = _pstr(p.get("media_path")).strip() or None
    media_type = _pstr(p.get("media_type")).strip() or None
    if media_path:
        validated_media_path = _validate_media_path_for_user(media_path, int(user_id))
        if not validated_media_path:
            return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_media_path"}, status=403))
        media_path = validated_media_path

    media_items_raw = p.get("media_items") if isinstance(p.get("media_items"), list) else None
    media_items_validated = None
    if media_items_raw:
        media_items_validated = []
        for mi in media_items_raw[:6]:
            if not isinstance(mi, dict):
                continue
            mi_path = _pstr(mi.get("path")).strip()
            mi_type = _pstr(mi.get("type")).strip()
            if not mi_path or not mi_type:
                continue
            validated_mi_path = _validate_media_path_for_user(mi_path, int(user_id))
            if not validated_mi_path:
                continue
            item: Dict[str, Any] = {"type": mi_type, "path": validated_mi_path}
            thumb_path = _pstr(mi.get("thumb_path")).strip()
            if thumb_path:
                validated_thumb_path = _validate_media_path_for_user(thumb_path, int(user_id))
                if validated_thumb_path:
                    item["thumb_path"] = validated_thumb_path
            width = _safe_int(mi.get("width"))
            height = _safe_int(mi.get("height"))
            if width and width > 0:
                item["width"] = width
            if height and height > 0:
                item["height"] = height
            media_items_validated.append(item)
        if not media_items_validated:
            media_items_validated = None

    try:
        mem_id = db.add_memory(user_id, category, title, date, content,
                               media_type=media_type, media_path=media_path,
                               media_items=media_items_validated)
        if not mem_id or mem_id == -1:
            return _add_cors_headers(web.json_response({"ok": False, "error": "db error"}))
    except Exception as e:
        logger.exception("site_create_memory error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

    # Уведомление партнёра уводим в фон, чтобы ответ сайта не ждал Telegram.
    try:
        cat_labels = {"important_moments": "важный момент", "memories": "воспоминание", "important_dates": "важную дату"}
        cat_label = cat_labels.get(category)
        if not cat_label and category.startswith("custom_"):
            try:
                cat_id = int(category.replace("custom_", ""))
                cc = db.get_custom_category(cat_id)
                if cc:
                    cat_label = f"момент в категорию «{cc['name']}»"
            except Exception:
                pass
        if not cat_label:
            cat_label = "момент"
        other_id = _visitor_partner_id(visitor_id)
        actor = "Создатель" if visitor_id == "creator" else "Партнёр"
        notify_text = f"✨ <b>{actor} добавил(а) {cat_label}</b>\n\n<b>{title}</b>\n{date}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"memory_{mem_id}")]
        ])

        async def _notify_partner() -> None:
            if not other_id or not db.are_notifications_enabled(other_id):
                return
            _bot = Bot(token=config.BOT_TOKEN)
            try:
                if media_path and media_type:
                    file_id = await _upload_file_to_telegram(_bot, media_path, media_type, config.CREATOR_ID)
                    if file_id:
                        db.update_memory(mem_id, media_file_id=file_id)
                        if media_type == "photo":
                            await _bot.send_photo(
                                chat_id=other_id,
                                photo=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        elif media_type == "video":
                            await _bot.send_video(
                                chat_id=other_id,
                                video=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        else:
                            await _bot.send_message(
                                chat_id=other_id,
                                text=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                            await _bot.send_document(chat_id=other_id, document=file_id)
                    else:
                        await _bot.send_message(
                            chat_id=other_id,
                            text=notify_text,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    await _bot.send_message(
                        chat_id=other_id,
                        text=notify_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                    )
            finally:
                await _bot.session.close()

        _spawn_background_task(_notify_partner(), "site_create_memory.notify")
    except Exception as e:
        logger.warning("site_create_memory notify scheduling error: %s", e)

    created_memory = None
    try:
        created = db.get_memory(mem_id)
        if created:
            created_memory = _memory_to_public_dict(created)
    except Exception:
        created_memory = None

    return _add_cors_headers(web.json_response({"ok": True, "id": mem_id, "memory": created_memory}))


async def site_resolve_memory_params(request: web.Request) -> web.Response:
    """Возвращает текст с подставленными параметрами для live-preview на сайте."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        p = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))

    visitor_id = _get_trusted_visitor_id(request, payload=p, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unknown visitor"}, status=400))

    title = _pstr(p.get("title"))
    date_str = _pstr(p.get("date"))
    content = _pstr(p.get("content"))

    return _add_cors_headers(web.json_response({
        "ok": True,
        "resolved": {
            "title": substitute_params(title, user_id),
            "date": substitute_params(date_str, user_id),
            "content": substitute_params(content, user_id),
        },
    }))


async def site_create_event(request: web.Request) -> web.Response:
    """Создаёт событие на дату с сайта."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        p = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}, status=400))

    visitor_id = _get_trusted_visitor_id(request, payload=p, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    title       = _pstr(p.get("title")).strip()
    description = _pstr(p.get("description")).strip()
    event_dt    = _pstr(p.get("event_datetime")).strip()  # already parsed DB format

    if not title or not event_dt:
        return _add_cors_headers(web.json_response({"ok": False, "error": "missing fields"}, status=400))

    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "unknown visitor"}, status=400))

    media_path = _pstr(p.get("media_path")).strip() or None
    media_type = _pstr(p.get("media_type")).strip() or None
    if media_path:
        validated_media_path = _validate_media_path_for_user(media_path, int(user_id))
        if not validated_media_path:
            return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_media_path"}, status=403))
        media_path = validated_media_path

    try:
        is_recurring = int(p.get("is_recurring", 0)) if p.get("is_recurring") is not None else 0
        event_id = db.add_scheduled_event(user_id, title, description, event_dt,
                                          media_type=media_type, media_path=media_path,
                                          is_recurring=is_recurring)
        if not event_id or event_id == -1:
            return _add_cors_headers(web.json_response({"ok": False, "error": "db error"}))
    except Exception as e:
        logger.exception("site_create_event error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

    try:
        other_id = _visitor_partner_id(visitor_id)
        actor = "Создатель" if visitor_id == "creator" else "Партнёр"
        notify_text = f"🎯 <b>{actor} добавил(а) событие</b>\n\n<b>{title}</b>"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👀 Посмотреть", callback_data=f"scheduled_event_{event_id}")]
        ])

        async def _notify_partner() -> None:
            if not other_id or not db.are_notifications_enabled(other_id):
                return
            _bot = Bot(token=config.BOT_TOKEN)
            try:
                if media_path and media_type:
                    file_id = await _upload_file_to_telegram(_bot, media_path, media_type, config.CREATOR_ID)
                    if file_id:
                        db.update_scheduled_event(event_id, media_file_id=file_id)
                        if media_type == "photo":
                            await _bot.send_photo(
                                chat_id=other_id,
                                photo=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        elif media_type == "video":
                            await _bot.send_video(
                                chat_id=other_id,
                                video=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        else:
                            await _bot.send_message(
                                chat_id=other_id,
                                text=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                            await _bot.send_document(chat_id=other_id, document=file_id)
                    else:
                        await _bot.send_message(
                            chat_id=other_id,
                            text=notify_text,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    await _bot.send_message(
                        chat_id=other_id,
                        text=notify_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                    )
            finally:
                await _bot.session.close()

        _spawn_background_task(_notify_partner(), "site_create_event.notify")
    except Exception as e:
        logger.warning("site_create_event notify scheduling error: %s", e)

    timezone_id = db.get_user_setting(user_id, "timezone") if user_id else None
    event_obj = db.get_scheduled_event(event_id)
    if event_obj:
        return _add_cors_headers(web.json_response({
            "ok": True,
            "id": event_id,
            "event_datetime": event_obj.event_datetime,
            "event_datetime_human": format_scheduled_event_datetime_for_timezone(event_obj.event_datetime or "", event_obj.user_id, timezone_id),
            "is_passed": bool(is_scheduled_event_moment_passed(event_obj.event_datetime or "", event_obj.user_id)),
        }))
    return _add_cors_headers(web.json_response({"ok": True, "id": event_id}))


async def handle_options(request: web.Request) -> web.Response:
    """Ответ на preflight-запросы браузера."""
    resp = web.Response(status=204)
    return _add_cors_headers(resp)

# --- START DIAGNOSTIC ---
async def diag_auth_status(request: web.Request) -> web.Response:
    """Diagnostic endpoint to return the current authenticated user identity."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
        
    visitor_id = _get_trusted_visitor_id(request)
    
    user_info = {}
    visitor_id_base = visitor_id.split("_")[0] if visitor_id and "_" in visitor_id else visitor_id
    if visitor_id_base and visitor_id_base.isdigit():
        user_info = db.get_user(int(visitor_id_base)) or {}
        
    def _format_name(u: dict, default: str) -> str:
        fn = u.get("first_name") or ""
        ln = u.get("last_name") or ""
        un = u.get("username") or ""
        return (fn + " " + ln).strip() or fn or ("@" + un if un else "") or default

    data = {
        "ok": True,
        "backend_user_id": visitor_id,
        "backend_name": _format_name(user_info, f"User {visitor_id}") if visitor_id else "None"
    }
    return _add_cors_headers(web.json_response(data))
# --- END DIAGNOSTIC ---


async def site_parse_date_ai(request: web.Request) -> web.Response:
    """Разбирает введённую дату через ИИ, возвращает распознанную дату."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        p = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}))

    raw_date   = (p.get("date_text") or "").strip()
    visitor_id = (p.get("visitor_id") or "").strip()
    tz         = (p.get("tz") or "").strip() or None

    if not raw_date:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty date"}))

    try:
        from api import parse_date_with_ai
        from utils import get_user_datetime_context, parse_ai_date_to_db, format_scheduled_event_datetime

        user_id = _visitor_to_user_id(visitor_id) or config.CREATOR_ID
        ctx     = get_user_datetime_context(user_id)

        import asyncio as _asyncio
        ai_date = await _asyncio.to_thread(parse_date_with_ai, raw_date, ctx, True)

        if ai_date == "ERROR:PAST_DATE":
            return _add_cors_headers(web.json_response({"ok": False, "error": "❌ Не используй даты в прошлом, мне нужны будущие события"}))

        if not ai_date:
            return _add_cors_headers(web.json_response({"ok": False, "error": "could not parse"}))

        db_format = parse_ai_date_to_db(ai_date)
        if not db_format:
            return _add_cors_headers(web.json_response({"ok": False, "error": "could not convert"}))

        from utils import calculate_next_occurrence
        next_db_format = calculate_next_occurrence(db_format, user_id)
        display = format_scheduled_event_datetime(next_db_format, user_id, user_id)
        return _add_cors_headers(web.json_response({
            "ok":       True,
            "db_format": db_format,
            "display":  display,
        }))

    except Exception as e:
        logger.exception("site_parse_date_ai error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))


async def site_create_wish(request: web.Request) -> web.Response:
    """Создаёт/обновляет желание участника пары с сайта."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        p = await request.json()
    except Exception:
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad json"}))

    visitor_id = _get_trusted_visitor_id(request, payload=p, payload_key="visitor_id", query_key="visitor_id")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}))
    content_txt = (p.get("content") or "").strip()
    wish_id_raw = p.get("wish_id")  # int or null — if set, update that wish

    user_id = _visitor_to_user_id(visitor_id)
    if not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}))
    if not content_txt:
        return _add_cors_headers(web.json_response({"ok": False, "error": "empty content"}))

    media_path = (p.get("media_path") or "").strip() or None
    media_type_val = (p.get("media_type") or "").strip() or None
    if media_path:
        validated_media_path = _validate_media_path_for_user(media_path, int(user_id))
        if not validated_media_path:
            return _add_cors_headers(web.json_response({"ok": False, "error": "invalid_media_path"}, status=403))
        media_path = validated_media_path
    try:
        wish_id_int = _safe_int(wish_id_raw) if wish_id_raw is not None else None
        if wish_id_int:
            existing = db.get_wish(wish_id_int)
            if existing and existing.user_id == user_id:
                db.update_wish(existing.id, content_txt,
                               media_type=media_type_val, media_path=media_path)
                wish_id = existing.id
            else:
                return _add_cors_headers(web.json_response({"ok": False, "error": "wish_not_found_or_forbidden"}))
        else:
            wish_id = db.add_wish(user_id, content_txt,
                                  media_type=media_type_val, media_path=media_path)
        if not wish_id or wish_id == -1:
            return _add_cors_headers(web.json_response({"ok": False, "error": "db error"}))
    except Exception as e:
        logger.exception("site_create_wish error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

    # Уведомление партнёра уводим в фон, чтобы save не ждал Telegram.
    try:
        actor = "Партнёр"
        other_id = _visitor_partner_id(visitor_id)
        notify_text = f"💫 <b>{actor} написал(а) желание #{wish_id}</b>\n\n{content_txt[:200]}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👀 Открыть желание", callback_data=f"wish_view_{wish_id}")]
        ])

        async def _notify_partner() -> None:
            if not other_id or not db.are_notifications_enabled(other_id):
                return
            _bot = Bot(token=config.BOT_TOKEN)
            try:
                if media_path and media_type_val:
                    file_id = await _upload_file_to_telegram(_bot, media_path, media_type_val, config.CREATOR_ID)
                    if file_id:
                        db.update_wish(
                            wish_id,
                            content_txt,
                            media_type=media_type_val,
                            media_file_id=file_id,
                            media_path=media_path,
                        )
                        if media_type_val == "photo":
                            await _bot.send_photo(
                                chat_id=other_id,
                                photo=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        elif media_type_val == "video":
                            await _bot.send_video(
                                chat_id=other_id,
                                video=file_id,
                                caption=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                        else:
                            await _bot.send_message(
                                chat_id=other_id,
                                text=notify_text,
                                reply_markup=keyboard,
                                parse_mode=ParseMode.HTML,
                            )
                            await _bot.send_document(chat_id=other_id, document=file_id)
                    else:
                        await _bot.send_message(
                            chat_id=other_id,
                            text=notify_text,
                            reply_markup=keyboard,
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    await _bot.send_message(
                        chat_id=other_id,
                        text=notify_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                    )
            finally:
                await _bot.session.close()

        _spawn_background_task(_notify_partner(), "site_create_wish.notify")
    except Exception as e:
        logger.warning("site_create_wish notify scheduling error: %s", e)

    return _add_cors_headers(web.json_response({"ok": True, "id": wish_id}))


async def upload_media_general(request: web.Request) -> web.Response:
    """Загружает медиафайл для воспоминания/желания/события.
    Сохраняет в основную папку media/ (а не notifications/).
    Доступно для creator и ksyusha."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not visitor_id or not user_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:
        started_at = time.monotonic()
        reader = await request.multipart()
        saved_path = None
        media_type_str = None
        max_size = _UPLOAD_MAX_BY_PATH.get("/api/upload_media", 100 * 1024 * 1024)

        async for field in reader:
            if field.name != "file":
                await field.read()
                continue

            filename = field.filename or "upload"
            content_type = field.headers.get("Content-Type", "")
            allowed_ext = _safe_upload_ext(filename, content_type)
            if not allowed_ext:
                logger.warning("upload_media_general: запрещённый тип файла filename=%r ct=%r visitor=%s", filename, content_type, visitor_id)
                return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden_file_type"}, status=415))
            media_type_str = "video" if allowed_ext in _ALLOWED_VIDEO_EXT else "photo"

            ts = int(datetime.now(timezone.utc).timestamp() * 1000)
            safe_name = f"u{int(user_id)}_{ts}_{secrets.token_hex(4)}{allowed_ext}"

            media_folder = Path(getattr(config, "MEDIA_FOLDER", "media"))
            media_folder.mkdir(parents=True, exist_ok=True)
            save_path = media_folder / safe_name

            size = 0
            with open(save_path, "wb") as f_out:
                while True:
                    chunk = await field.read_chunk(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_size:
                        try:
                            save_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        return _add_cors_headers(web.json_response({"ok": False, "error": "file_too_large"}, status=413))
                    f_out.write(chunk)

            saved_path = str(save_path)
            logger.info("upload_media_general saved: %s bytes=%d visitor=%s", saved_path, size, visitor_id)
            break

        if not saved_path:
            return _add_cors_headers(web.json_response({"ok": False, "error": "no file"}))

        thumb_path = None
        width = None
        height = None
        if media_type_str == "photo":
            if _PILImage:
                try:
                    with _PILImage.open(saved_path) as img:
                        width, height = img.size
                        if width is not None and width <= 0:
                            width = None
                        if height is not None and height <= 0:
                            height = None
                except Exception as dim_err:
                    logger.warning("upload_media_general: failed to read image size for %s: %s", saved_path, dim_err)
            if _PILImage:
                thumb_path = await asyncio.to_thread(_generate_thumbnail, Path(saved_path))

        resp_data = {
            "ok": True,
            "path": saved_path,
            "media_type": media_type_str,
        }
        if width and height:
            resp_data["width"] = width
            resp_data["height"] = height
        if thumb_path:
            resp_data["thumb_path"] = thumb_path

        elapsed_ms = round((time.monotonic() - started_at) * 1000, 1)
        logger.info(
            "upload_media_general done visitor=%s path=%s type=%s bytes=%d thumb=%s elapsed_ms=%s",
            visitor_id,
            saved_path,
            media_type_str,
            size,
            bool(thumb_path),
            elapsed_ms,
        )
        return _add_cors_headers(web.json_response(resp_data))
    except Exception as e:
        logger.exception("upload_media_general error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))



async def api_heatmap_endpoint(request: web.Request) -> web.Response:
    """Тепловая карта за произвольный месяц для авторизованного пользователя."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    hm_lang = (request.rel_url.query.get("lang") or "ru").strip()[:5]
    if hm_lang not in ("ru", "ky", "de", "en"):
        hm_lang = "ru"
    try:
        year  = int(request.rel_url.query.get("year",  0))
        month = int(request.rel_url.query.get("month", 0))
    except (ValueError, TypeError):
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad params"}))
    if not (1 <= month <= 12 and year >= 2020):
        return _add_cors_headers(web.json_response({"ok": False, "error": "bad params"}))
    try:
        last_day = calendar.monthrange(year, month)[1]
        vbd = db.get_visits_per_day_for_month(year, month, visitor_id)
        max_v = max(vbd.values()) if vbd else 0
        parts = []
        for day in range(1, last_day + 1):
            cnt = vbd.get(day, 0)
            if max_v <= 0 or cnt == 0:
                lc = ""
            else:
                q = cnt / max_v
                lc = " d1" if q <= 0.2 else " d2" if q <= 0.4 else " d3" if q <= 0.6 else " d4" if q <= 0.8 else " d5"
            _v = {"ru":("визит","визита","визитов"), "de":("Besuch","Besuche","Besuche"), "en":("visit","visits","visits"), "ky":("кириш","кириш","кириш")}
            _vw = _v.get(hm_lang, _v["ru"])
            tip = (str(day) + ": " + str(cnt) + " " +
                   (_vw[0] if cnt == 1 else _vw[1] if 2 <= cnt <= 4 else _vw[2])) if cnt else str(day)
            parts.append('<div class="hm-day' + lc + '" title="' + tip + '">' + str(day) + '</div>')
        html_out = "\n      ".join(parts)
        return _add_cors_headers(web.json_response({"ok": True, "html": html_out}))
    except Exception as e:
        logger.exception("api_heatmap_endpoint: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))

async def serve_settings_js(request: web.Request) -> web.Response:
    """Отдаёт settings.js из папки проекта."""
    p = Path(__file__).resolve().parent / "settings.js"
    if not p.exists():
        return web.Response(status=404, text="settings.js not found")
    text = p.read_text(encoding="utf-8")
    return web.Response(
        text=text,
        content_type="application/javascript",
        charset="utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def serve_avatar(request: web.Request) -> web.Response:
    """Отдаёт аватарку по имени файла."""
    trusted_vid = _get_trusted_visitor_id(request, payload=None, query_key="v")
    viewer_user_id = _visitor_to_user_id(trusted_vid or "")
    if not viewer_user_id:
        return web.Response(status=403)
    filename = request.match_info.get("filename", "")
    if not filename:
        return web.Response(status=404)
    avatars_root = (Path(__file__).resolve().parent / "media" / "avatars").resolve()
    try:
        path = (avatars_root / filename).resolve()
        path.relative_to(avatars_root)
    except (ValueError, Exception):
        return web.Response(status=400)
    if not path.exists():
        return web.Response(status=404)
    if not _media_belongs_to_user_couple(f"avatars/{filename}", viewer_user_id):
        return web.Response(status=403)
    import mimetypes
    ct, _ = mimetypes.guess_type(str(path))
    return web.FileResponse(path, headers={"Content-Type": ct or "image/jpeg",
                                           "Cache-Control": "public, max-age=86400"})


async def check_celebrations(request: web.Request) -> web.Response:
    """Возвращает первую непоказанную праздничную анимацию для visitor_id."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    visitor_id = _get_trusted_visitor_id(request, payload=None, query_key="v")
    if not visitor_id:
        return _add_cors_headers(web.json_response({"ok": True, "celebration": None}))
    cel = db.get_pending_celebration(visitor_id)
    return _add_cors_headers(web.json_response({"ok": True, "celebration": cel}))


async def mark_celebration_delivered(request: web.Request) -> web.Response:
    """Помечает анимацию как показанную."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    cel_id = _safe_int(payload.get("id"), 0)
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="v")
    if cel_id and visitor_id:
        db.mark_celebration_delivered(cel_id, visitor_id)
    return _add_cors_headers(web.json_response({"ok": True}))


async def trigger_test_celebration(request: web.Request) -> web.Response:
    """Создаёт тестовую анимацию (доступно только создателю)."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}
    visitor_id = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="visitor_id")
    user_id = _visitor_to_user_id(visitor_id or "")
    if not user_id or not db.is_creator(user_id):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    cel_id = db.add_celebration("test", "Тестовая анимация 🎉")
    # Push via WS to all connected clients
    for bucket in list(_site_ws_clients.values()):
        for role in ("creator", "partner"):
            for ws in list(bucket.get(role, set())):
                try:
                    import asyncio
                    asyncio.create_task(ws.send_json({
                        "type": "celebration",
                        "celebration": {"id": cel_id, "celebration_type": "test", "event_title": "Тестовая анимация 🎉"}
                    }))
                except Exception:
                    pass
    return _add_cors_headers(web.json_response({"ok": True, "id": cel_id}))


async def upload_avatar(request: web.Request) -> web.Response:
    """Сохраняет аватарку пользователя в media/avatars/."""
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    try:
        reader = await request.multipart()
        declared_visitor_id = None
        filename = "avatar.jpg"
        max_size = 10 * 1024 * 1024  # 10 MB hard limit per avatar
        saved_temp_path = None
        total_size = 0

        async for field in reader:
            if field.name == "visitor_id":
                declared_visitor_id = (await field.read(decode=True)).decode("utf-8", errors="ignore").strip() or None
            elif field.name == "file":
                filename = (field.filename or "avatar.jpg").strip()
                project_root = Path(__file__).resolve().parent
                save_dir = project_root / "media" / "avatars"
                save_dir.mkdir(parents=True, exist_ok=True)
                saved_temp_path = save_dir / f".avatar_upload_tmp_{secrets.token_hex(8)}"
                with open(saved_temp_path, "wb") as tmpf:
                    while True:
                        chunk = await field.read_chunk(65536)
                        if not chunk:
                            break
                        total_size += len(chunk)
                        if total_size > max_size:
                            try:
                                saved_temp_path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return _add_cors_headers(web.json_response({"ok": False, "error": "file_too_large"}, status=413))
                        tmpf.write(chunk)

        if not saved_temp_path or not saved_temp_path.exists() or total_size <= 0:
            return _add_cors_headers(web.json_response({"ok": False, "error": "no file"}, status=400))

        trusted_visitor_id = _get_trusted_visitor_id(
            request,
            payload={"visitor_id": declared_visitor_id} if declared_visitor_id else None,
            payload_key="visitor_id",
            query_key="v",
        )
        if not trusted_visitor_id or not _visitor_to_user_id(trusted_visitor_id):
            try:
                saved_temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

        # Determine save directory
        project_root = Path(__file__).resolve().parent
        save_dir = project_root / "media" / "avatars"
        save_dir.mkdir(parents=True, exist_ok=True)

        # Filename: avatar_{visitor_id}.{ext}
        ext = Path(filename).suffix.lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            ext = ".jpg"
        safe_vid = (trusted_visitor_id or "unknown").replace("/", "_").replace("..", "")
        save_name = f"avatar_{safe_vid}{ext}"
        save_path = save_dir / save_name

        saved_temp_path.replace(save_path)

        # Return URL so JS can reference it
        url = f"/media/avatars/{save_name}"
        logger.info("upload_avatar saved: %s bytes=%d visitor=%s", save_path, total_size, trusted_visitor_id)
        return _add_cors_headers(web.json_response({"ok": True, "url": url}))

    except Exception as e:
        logger.exception("upload_avatar error: %s", e)
        return _add_cors_headers(web.json_response({"ok": False, "error": "internal error"}, status=500))


async def serve_logger_js(request: web.Request) -> web.Response:
    """Отдаёт logger.js из папки проекта."""
    p = Path(__file__).resolve().parent / "logger.js"
    if not p.exists():
        return web.Response(status=404, text="logger.js not found")
    text = p.read_text(encoding="utf-8")
    return web.Response(
        text=text,
        content_type="application/javascript",
        charset="utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


async def client_log(request: web.Request) -> web.Response:
    """
    Принимает пакет клиентских логов и пересылает их создателю в Telegram.
    Учитывает настройку уведомлений.
    Тело: { visitor_id, page, ua, entries: [{level, tag, msg, time}] }
    """
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    # Не бомбим бота если уведомления или категория логгера выключены
    if not db.are_notifications_enabled(config.CREATOR_ID) or not db.is_category_notif_enabled(config.CREATOR_ID, "logger"):
        return _add_cors_headers(web.Response(status=204))

    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        return _add_cors_headers(web.Response(status=204))

    entries = payload.get("entries") or []
    if not entries:
        return _add_cors_headers(web.Response(status=204))

    visitor_id = (payload.get("visitor_id") or "unknown").strip()
    page       = (payload.get("page") or "/").strip()
    ua         = _pstr(payload.get("ua")).strip()[:80]

    # Иконки по уровню
    ICONS = {"error": "🔴", "warn": "🟡", "info": "⚪"}

    lines = [f"📋 <b>Лог с сайта</b> | <code>{visitor_id}</code> | <code>{page}</code>"]
    if ua:
        lines.append(f"<i>{ua}</i>")
    lines.append("")

    for e in entries[:30]:  # не более 30 записей за раз
        level = (e.get("level") or "info").strip()
        tag   = (e.get("tag")   or "?").strip()
        msg   = (e.get("msg")   or "").strip()[:300]
        time  = (e.get("time")  or "").strip()
        icon  = ICONS.get(level, "⚪")
        lines.append(f"{icon} <code>[{time}][{tag}]</code> {msg}")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3990] + "\n…"

    try:
        bot = Bot(token=config.BOT_TOKEN)
        await bot.send_message(
            chat_id=config.CREATOR_ID,
            text=text,
            parse_mode=ParseMode.HTML,
        )
        await bot.session.close()
    except Exception as ex:
        logger.warning("client_log: не удалось отправить в Telegram: %s", ex)

    return _add_cors_headers(web.Response(status=204))


# ═══════════════════════════════════════════════════════════════
# STARS — персистентное хранение звёзд через settings таблицу
# Ключи: "pending_stars_ksyusha", "pending_stars_creator"
# Значение: JSON строка, например "3"
# ═══════════════════════════════════════════════════════════════

def _stars_pending_key(role: str, couple_id: int) -> str:
    return f"pending_stars_{couple_id}_{role}"

def _stars_get(role: str, couple_id: int) -> int:
    """Сколько непрочитанных звёзд у роли."""
    try:
        val = db.get_setting(_stars_pending_key(role, couple_id))
        return int(val or "0")
    except Exception:
        return 0

def _stars_add(role: str, count: int, couple_id: int) -> int:
    """Добавляет звёзды получателю, возвращает новый итог."""
    current = _stars_get(role, couple_id)
    new_total = current + count
    db.set_setting(_stars_pending_key(role, couple_id), str(new_total))
    return new_total

def _stars_clear(role: str, couple_id: int) -> None:
    """Сбрасывает счётчик (получатель прочитал)."""
    db.set_setting(_stars_pending_key(role, couple_id), "0")


async def stars_send(request: web.Request) -> web.Response:
    """Отправитель шлёт звёзды получателю. Сохраняется в БД независимо от WS.
    Body: { from: "creator"|"ksyusha", count: N }
    """
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}

    count  = _safe_int(payload.get("count"), 1)
    sender_vid = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="v")
    sender_uid = _visitor_to_user_id(sender_vid or "")
    if not sender_uid or not count or count <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "invalid params"}))
    couple = db.get_couple_by_user(sender_uid)
    if not couple or not couple.get("user2_id"):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    sender = "creator" if couple.get("user1_id") == sender_uid else "partner"
    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    recipient = "partner" if sender == "creator" else "creator"
    new_total = _stars_add(recipient, count, couple_id)

    # Также пушим через WS если получатель онлайн
    payload_ws = json.dumps({"type": "star_sent", "from": sender, "count": count})
    dead = []
    for ws in _ws_clients(couple_id, recipient):
        try:
            await ws.send_str(payload_ws)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_discard(couple_id, recipient, ws)

    logger.info("stars_send: %s → %s (count=%d, total=%d)", sender, recipient, count, new_total)
    return _add_cors_headers(web.json_response({"ok": True, "pending": new_total}))


async def stars_pending(request: web.Request) -> web.Response:
    """Проверяет есть ли непрочитанные звёзды для роли.
    Query: ?role=ksyusha|creator
    """
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))

    viewer_vid = _get_trusted_visitor_id(request, payload=None, query_key="v")
    viewer_uid = _visitor_to_user_id(viewer_vid or "")
    couple = db.get_couple_by_user(viewer_uid) if viewer_uid else None
    if not couple or not couple.get("user2_id"):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    role = "creator" if couple.get("user1_id") == viewer_uid else "partner"

    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    count = _stars_get(role, couple_id)
    sender_role = "creator" if role == "partner" else "partner"

    # Получаем отображаемое имя отправителя динамически из БД
    if count > 0:
        if sender_role == "creator":
            _uid = db.get_creator_id()
        else:
            _uid = db.get_ksusha_id()
        sender_name = (db.get_display_name(_uid, fallback="") or "").split()[0] or None
    else:
        sender_name = None

    return _add_cors_headers(web.json_response({
        "ok": True,
        "count": count,
        "from": sender_name,
    }))


async def stars_seen(request: web.Request) -> web.Response:
    """Получатель подтверждает что увидел звёзды — сбрасываем счётчик.
    Body: { role: "ksyusha"|"creator" }
    """
    if not _check_api_secret(request):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            payload = {}
    except web.HTTPException:
        raise
    except Exception:
        payload = {}

    viewer_vid = _get_trusted_visitor_id(request, payload=payload, payload_key="visitor_id", query_key="v")
    viewer_uid = _visitor_to_user_id(viewer_vid or "")
    couple = db.get_couple_by_user(viewer_uid) if viewer_uid else None
    if not couple or not couple.get("user2_id"):
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    role = "creator" if couple.get("user1_id") == viewer_uid else "partner"

    couple_id = _safe_int(couple.get("id"), 0) or 0
    if couple_id <= 0:
        return _add_cors_headers(web.json_response({"ok": False, "error": "forbidden"}, status=403))
    _stars_clear(role, couple_id)

    # Уведомляем отправителя через WS чтобы он сбросил локальный счётчик
    sender_role = "creator" if role == "partner" else "partner"
    payload_ws = json.dumps({"type": "stars_seen"})
    dead = []
    for ws in _ws_clients(couple_id, sender_role):
        try:
            await ws.send_str(payload_ws)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_discard(couple_id, sender_role, ws)

    logger.info("stars_seen: role=%s cleared", role)
    return _add_cors_headers(web.json_response({"ok": True}))


def create_app() -> web.Application:

    """Создаёт aiohttp-приложение с HTTP‑API и простой веб-страницей."""

    app = web.Application(
        middlewares=[
            security_headers_middleware,
            body_size_middleware,     # ← сначала отклоняем гигантские тела
            rate_limit_middleware,    # ← потом rate limit (не засчитывает 413 в burst)
            site_error_middleware,
        ],
        client_max_size=500 * 1024 * 1024,  # 500 MB — для загрузки видео
    )

    app.router.add_get("/", index)
    app.router.add_get("/404", not_found_page)
    app.router.add_get("/404.html", not_found_page)

    app.router.add_get("/stats", stats_page)
    app.router.add_get("/admin", admin_page)
    app.router.add_get("/sky", sky_page)
    app.router.add_get("/api/sky_cfg", api_sky_cfg)
    app.router.add_get("/profile", profile_page)
    app.router.add_route("OPTIONS", "/api/logout", handle_options)
    app.router.add_post("/api/logout", logout)
    app.router.add_route("OPTIONS", "/api/sessions", handle_options)
    app.router.add_get("/api/sessions", api_sessions)
    app.router.add_route("OPTIONS", "/api/sessions/revoke", handle_options)
    app.router.add_post("/api/sessions/revoke", api_sessions_revoke)

    app.router.add_get("/ws/maintenance", ws_maintenance)
    app.router.add_get("/ws/site", ws_site)

    media_root = Path(config.MEDIA_FOLDER).resolve()

    def _media_access_denied_response(request: web.Request) -> web.Response:
        accept = (request.headers.get("Accept") or "").lower()
        headers = {
            "X-Media-Access": "denied",
            "Cache-Control": "private, no-store, no-cache, max-age=0, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Vary": "Cookie, Accept",
        }
        if "text/html" in accept:
            html = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Доступ ограничен</title>
  <style>
    body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;background:linear-gradient(135deg,#f6f7fb,#eceffd);min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
    .card{max-width:360px;width:100%;background:#fff;border-radius:16px;padding:22px;box-shadow:0 18px 44px rgba(42,46,72,.18)}
    .title{font-size:1.05rem;font-weight:700;color:#23263a;margin:0 0 10px}
    .text{font-size:.95rem;line-height:1.55;color:#4a4f69;margin:0 0 16px}
    .btn{display:inline-block;padding:10px 14px;border-radius:10px;background:#3f5efb;color:#fff;text-decoration:none;font-weight:600}
  </style>
</head>
<body>
  <div class="card">
    <h1 class="title">🔒 Доступ ограничен</h1>
    <p class="text">Это медиа доступно только двум участникам пары, в которой оно было создано.</p>
    <a class="btn" href="/">На главную</a>
  </div>
</body>
</html>"""
            return web.Response(status=403, text=html, content_type="text/html", headers=headers)
        return web.Response(status=403, text="Forbidden", headers=headers)

    async def protected_media(request: web.Request) -> web.StreamResponse:
        trusted_vid = _get_trusted_visitor_id(
            request,
            payload=None,
            query_key="v",
            allow_header_fallback=False,
        )
        viewer_user_id = _visitor_to_user_id(trusted_vid or "")
        path_part = request.match_info.get("path", "")
        if not viewer_user_id:
            logger.info("MEDIA_ACCESS_DENY unauthenticated path=%s", path_part)
            return _media_access_denied_response(request)

        # Защита от path traversal: проверяем, что итоговый путь внутри media_root
        try:
            full_path = (media_root / path_part).resolve()
            full_path.relative_to(media_root)
        except (ValueError, Exception):
            logger.warning("MEDIA_ACCESS_BAD_PATH user_id=%s path=%s", viewer_user_id, path_part)
            return web.Response(status=400, text="Bad request")

        if not full_path.is_file():
            logger.info("MEDIA_ACCESS_NOT_FOUND user_id=%s path=%s", viewer_user_id, path_part)
            return web.Response(status=404, text="Not found")
        if not _media_belongs_to_user_couple(path_part, viewer_user_id):
            logger.info("MEDIA_ACCESS_DENY unauthorized user_id=%s path=%s", viewer_user_id, path_part)
            return _media_access_denied_response(request)
        logger.info("MEDIA_ACCESS_ALLOW user_id=%s path=%s", viewer_user_id, path_part)
        resp = web.FileResponse(full_path)
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        resp.headers["Vary"] = "X-Visitor-Id, X-Visitor-Signature"
        return resp

    app.router.add_route("GET", "/media/{path:.*}", protected_media)

    # CORS preflight

    app.router.add_route("OPTIONS", "/api/info", handle_options)

    app.router.add_route("OPTIONS", "/api/all", handle_options)
    app.router.add_route("OPTIONS", "/api/site_bootstrap", handle_options)

    app.router.add_route("OPTIONS", "/api/memories", handle_options)

    app.router.add_route("OPTIONS", "/api/events", handle_options)

    app.router.add_route("OPTIONS", "/api/wishes", handle_options)
    app.router.add_route("OPTIONS", "/api/profile_stats", handle_options)

    app.router.add_route("OPTIONS", "/api/visit", handle_options)

    app.router.add_route("OPTIONS", "/api/memory_unlock", handle_options)

    app.router.add_route("OPTIONS", "/api/memory_view_start", handle_options)

    app.router.add_route("OPTIONS", "/api/memory_view_finish", handle_options)

    app.router.add_route("OPTIONS", "/api/category_open", handle_options)

    app.router.add_route("OPTIONS", "/api/visit_heartbeat", handle_options)

    app.router.add_route("OPTIONS", "/api/check_site_password", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/stats", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/check", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/health_metrics", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/action", handle_options)

    app.router.add_route("OPTIONS", "/api/ai_companion", handle_options)

    app.router.add_route("OPTIONS", "/api/ai_companion_stream", handle_options)
    app.router.add_route("OPTIONS", "/api/ai_companion_history", handle_options)

    # Основные эндпоинты

    # --- START DIAGNOSTIC ---
    app.router.add_get("/api/diag_auth_status", diag_auth_status)
    # --- END DIAGNOSTIC ---
    app.router.add_get("/api/info", info)

    app.router.add_get("/api/all", all_data)
    app.router.add_get("/api/site_bootstrap", site_bootstrap_data)

    app.router.add_get("/api/memories", memories_data)

    app.router.add_get("/api/events", events_data)

    app.router.add_get("/api/wishes", wishes_data)
    app.router.add_get("/api/profile_stats", profile_stats_data)

    app.router.add_post("/api/visit", log_visit)

    app.router.add_post("/api/memory_unlock", memory_unlock)

    app.router.add_post("/api/memory_view_start", memory_view_start)

    app.router.add_post("/api/memory_view_finish", memory_view_finish)

    app.router.add_post("/api/category_open", category_open)

    app.router.add_post("/api/visit_heartbeat", visit_heartbeat)

    app.router.add_post("/api/check_site_password", check_site_password)
    app.router.add_route("OPTIONS", "/api/token_auth", handle_options)
    app.router.add_post("/api/token_auth", token_auth)
    app.router.add_route("OPTIONS", "/api/token_check", handle_options)
    app.router.add_post("/api/token_check", token_check)
    app.router.add_route("OPTIONS", "/api/auth_debug_log", handle_options)
    app.router.add_post("/api/auth_debug_log", auth_debug_log)
    app.router.add_route("OPTIONS", "/api/site_save_settings", handle_options)
    app.router.add_post("/api/site_save_settings", site_save_settings)

    app.router.add_route("OPTIONS", "/api/unlink/init", handle_options)
    app.router.add_post("/api/unlink/init", api_unlink_init)
    app.router.add_get("/ws/unlink", ws_unlink)
    app.router.add_get("/api/admin/check", admin_check)
    app.router.add_get("/api/admin/health_metrics", admin_health_metrics)
    app.router.add_route("OPTIONS", "/api/admin/notification", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/notification/delivered", handle_options)
    app.router.add_route("OPTIONS", "/api/admin/backup", handle_options)
    app.router.add_route("OPTIONS", "/api/upload_notification_media", handle_options)
    app.router.add_post("/api/upload_notification_media", upload_notification_media)
    app.router.add_route("OPTIONS", "/api/upload_media", handle_options)
    app.router.add_post("/api/upload_media", upload_media_general)
    app.router.add_post("/api/admin/notification", admin_send_notification)
    app.router.add_get("/api/admin/notification", admin_get_notification)
    app.router.add_post("/api/admin/notification/delivered", admin_mark_notification_delivered)
    app.router.add_post("/api/admin/backup", admin_backup)
    app.router.add_get("/api/admin/stats", admin_stats)
    app.router.add_post("/api/admin/action", admin_action)
    app.router.add_get("/api/version", api_version)
    app.router.add_get("/api/admin/version-history", admin_version_history)

    app.router.add_post("/api/ai_companion", ai_companion)

    app.router.add_post("/api/ai_companion_stream", ai_companion_stream)
    app.router.add_route("OPTIONS", "/api/wish_update_status", handle_options)
    app.router.add_post("/api/wish_update_status", wish_update_status)
    app.router.add_route("OPTIONS", "/api/ai_message_reaction", handle_options)
    app.router.add_post("/api/ai_message_reaction", ai_message_reaction)
    app.router.add_route("OPTIONS", "/api/ai_message_pin", handle_options)
    app.router.add_post("/api/ai_message_pin", ai_message_pin)
    app.router.add_route("OPTIONS", "/api/ai_companion_history_clear", handle_options)
    app.router.add_post("/api/ai_companion_history_clear", ai_companion_history_clear)
    app.router.add_get("/api/ai_companion_history", ai_companion_history)
    app.router.add_route("OPTIONS", "/api/refresh_ai_session", handle_options)
    app.router.add_post("/api/refresh_ai_session", refresh_ai_session)

    # ── Create endpoints from site ────────────────────────────────
    app.router.add_route("OPTIONS", "/api/create_memory",  handle_options)
    app.router.add_route("OPTIONS", "/api/resolve_memory_params", handle_options)
    app.router.add_route("OPTIONS", "/api/create_event",   handle_options)
    app.router.add_route("OPTIONS", "/api/parse_date_ai",  handle_options)
    app.router.add_post("/api/create_memory",  site_create_memory)
    app.router.add_post("/api/resolve_memory_params", site_resolve_memory_params)
    app.router.add_post("/api/create_event",   site_create_event)
    app.router.add_post("/api/parse_date_ai",  site_parse_date_ai)
    app.router.add_route("OPTIONS", "/api/create_wish", handle_options)
    app.router.add_post("/api/create_wish",    site_create_wish)
    app.router.add_get("/api/heatmap", api_heatmap_endpoint)
    app.router.add_get("/settings.js", serve_settings_js)
    app.router.add_route("OPTIONS", "/api/upload_avatar", handle_options)
    app.router.add_post("/api/upload_avatar", upload_avatar)
    app.router.add_get("/api/celebrations", check_celebrations)
    app.router.add_route("OPTIONS", "/api/celebrations", handle_options)
    app.router.add_route("OPTIONS", "/api/celebrations/delivered", handle_options)
    app.router.add_post("/api/celebrations/delivered", mark_celebration_delivered)
    app.router.add_route("OPTIONS", "/api/test_celebration", handle_options)
    app.router.add_post("/api/test_celebration", trigger_test_celebration)
    # Serve avatars via /media/avatars/
    app.router.add_get("/media/avatars/{filename}", serve_avatar)
    # Client logger
    app.router.add_route("OPTIONS", "/api/log", handle_options)
    async def nav_debug(request):
        try:
            data = await request.json()
            with open("/app/frontend_debug.log", "a", encoding="utf-8") as f:
                f.write(f"NAV_DEBUG: {data}\n")
        except:
            pass
        return web.Response(text="ok")
    app.router.add_post("/api/nav_debug", nav_debug)

    app.router.add_post("/api/log", client_log)
    app.router.add_get("/logger.js", serve_logger_js)

    # Stars endpoints
    app.router.add_route("OPTIONS", "/api/stars/send",    handle_options)
    app.router.add_route("OPTIONS", "/api/stars/pending", handle_options)
    app.router.add_route("OPTIONS", "/api/stars/seen",    handle_options)
    app.router.add_post("/api/stars/send",    stars_send)
    app.router.add_get("/api/stars/pending",  stars_pending)
    app.router.add_post("/api/stars/seen",    stars_seen)

    async def _on_startup(application: web.Application):
        asyncio.create_task(_ddos_cleanup())
        # Fallback-уведомление: если процесс запущен без bot.py, шлём alert о self-heal БД отсюда.
        try:
            recovery_alert = db.consume_recovery_alert()
            if recovery_alert:
                bot = Bot(token=config.BOT_TOKEN)
                try:
                    await bot.send_message(chat_id=config.CREATOR_ID, text=recovery_alert, parse_mode=ParseMode.HTML)
                finally:
                    await bot.session.close()
        except Exception:
            logger.debug("Не удалось отправить DB self-heal alert из http_api startup")

    app.on_startup.append(_on_startup)

    return app
