import sqlite3
import os
import logging
import time
import hashlib
import secrets
import json
import threading
import gzip
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any, Tuple
from zoneinfo import ZoneInfo
from dataclasses import dataclass

from config import config
from app_version import get_version_metadata, get_git_commit

logger = logging.getLogger(__name__)


class BackupAwareConnection(sqlite3.Connection):
    """SQLite connection that runs an optional callback after a real commit."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._post_commit_callback = None

    def set_post_commit_callback(self, callback):
        self._post_commit_callback = callback

    def commit(self):
        had_open_tx = self.in_transaction
        super().commit()
        if had_open_tx and self._post_commit_callback:
            self._post_commit_callback()

@dataclass
class Memory:
    """Класс для представления воспоминания"""
    id: int
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    category: str
    title: str
    date: str
    content: str
    media_type: Optional[str]
    media_file_id: Optional[str]
    media_path: Optional[str]
    created_at: str
    updated_at: str
    # Приватность
    # privacy_type: None | "limited_views" | "password"
    privacy_type: Optional[str] = None
    privacy_views_limit: Optional[int] = None
    privacy_question: Optional[str] = None
    privacy_answer: Optional[str] = None
    media_items: Optional[List[Dict[str, Any]]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.username,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'category': self.category,
            'title': self.title,
            'date': self.date,
            'content': self.content,
            'media_type': self.media_type,
            'media_file_id': self.media_file_id,
            'media_path': self.media_path,
            'media_items': self.media_items,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'privacy_type': self.privacy_type,
            'privacy_views_limit': self.privacy_views_limit,
            'privacy_question': self.privacy_question,
            'privacy_answer': self.privacy_answer,
        }

@dataclass
class Wish:
    """Класс для представления желания"""
    id: int
    user_id: int
    content: str
    media_type: Optional[str]
    media_file_id: Optional[str]
    media_path: Optional[str]
    created_at: str
    updated_at: str
    status: str = "created"  # created | in_progress | done
    wish_number: int = 0  # DEPRECATED: kept for backward compat only


@dataclass
class ScheduledEvent:
    """Класс для представления ожидаемого события (событие на дату)"""
    id: int
    user_id: int
    title: str
    description: Optional[str]
    event_datetime: str
    created_at: str
    notified_at: Optional[str]
    media_type: Optional[str] = None
    media_file_id: Optional[str] = None
    media_path: Optional[str] = None
    original_event_datetime: str = ""
    is_recurring: int = 0

    def __post_init__(self):
        self.original_event_datetime = self.event_datetime
        if self.is_recurring == 1:
            try:
                from utils import calculate_next_occurrence
                self.event_datetime = calculate_next_occurrence(self.event_datetime, self.user_id)
            except Exception:
                pass

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'user_id': self.user_id,
            'title': self.title,
            'description': self.description,
            'event_datetime': self.event_datetime,
            'original_event_datetime': self.original_event_datetime,
            'created_at': self.created_at,
            'notified_at': self.notified_at,
            'media_type': self.media_type,
            'media_file_id': self.media_file_id,
            'media_path': self.media_path,
            'is_recurring': self.is_recurring,
        }


def _row_to_scheduled_event(row) -> ScheduledEvent:
    """Преобразует строку БД (Row или dict) в ScheduledEvent. Избегает ошибки Row.get()."""
    d = dict(row) if hasattr(row, 'keys') and not isinstance(row, dict) else row
    return ScheduledEvent(
        id=d['id'],
        user_id=d['user_id'],
        title=d['title'],
        description=d.get('description') or None,
        event_datetime=d['event_datetime'],
        created_at=d['created_at'],
        notified_at=d.get('notified_at'),
        media_type=d.get('media_type'),
        media_file_id=d.get('media_file_id'),
        media_path=d.get('media_path'),
        is_recurring=d.get('is_recurring', 0) if d.get('is_recurring') is not None else 0,
    )


def _get_event_sort_key(ev: ScheduledEvent):
    try:
        from utils import _event_datetime_to_utc
        from datetime import datetime, timezone
        event_utc = _event_datetime_to_utc(ev.event_datetime, ev.user_id)
        if event_utc is None:
            return (0, 9999999999.0)
        is_passed = event_utc <= datetime.now(timezone.utc)
        utc_val = event_utc.timestamp()
    except Exception:
        is_passed = False
        utc_val = 9999999999.0
    if not is_passed:
        return (0, utc_val)
    else:
        return (1, -utc_val)


class Database:
    """Класс для работы с базой данных SQLite"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.DATABASE_PATH
        self.media_folder = config.MEDIA_FOLDER
        self.hot_backup_enabled = bool(getattr(config, "HOT_BACKUP_ENABLED", True))
        configured_hot_backup_path = (getattr(config, "HOT_BACKUP_PATH", "") or "").strip()
        self.hot_backup_path = configured_hot_backup_path or f"{self.db_path}.hotbackup.db"
        self.hot_backup_prev_path = f"{self.hot_backup_path}.prev"
        self._hot_backup_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._recovery_lock = threading.Lock()
        self._pending_recovery_alert: Optional[str] = None
        self._backup_event = threading.Event()
        self._backup_thread = None
        self._initialize_with_self_heal()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Создаёт соединение с базой данных"""
        # timeout=15: ждём до 15 с если другой поток держит write-блокировку
        db_dir = os.path.dirname(self.db_path) or "."
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception:
            pass
        conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
            timeout=15,
            factory=BackupAwareConnection,
        )
        conn.row_factory = sqlite3.Row
        if isinstance(conn, BackupAwareConnection):
            conn.set_post_commit_callback(self._on_post_commit)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=10000;")  # 10 с на ожидание блокировки
        except Exception as e:
            logger.debug("PRAGMA setup skipped due to DB/SQLite limitation: %s", e)
        return conn

    def _on_post_commit(self):
        if not self.hot_backup_enabled:
            return
        if not self._backup_thread or not self._backup_thread.is_alive():
            self._backup_thread = threading.Thread(target=self._backup_worker, daemon=True)
            self._backup_thread.start()
        self._backup_event.set()

    def _backup_worker(self):
        while True:
            self._backup_event.wait()
            self._backup_event.clear()
            time.sleep(3.0)
            self._backup_event.clear()
            try:
                self._sync_hot_backup(reason="async_commit")
            except Exception as e:
                logger.exception("Async hot backup worker error: %s", e)

    def _sync_hot_backup(self, reason: str = "manual") -> None:
        """
        Создаёт атомарную hot-копию БД после коммита.
        Схема хранения:
        - active: self.hot_backup_path (последняя успешная копия)
        - prev:   self.hot_backup_prev_path (предыдущая успешная копия)
        """
        if not self.hot_backup_enabled:
            return
        with self._hot_backup_lock:
            backup_dir = os.path.dirname(self.hot_backup_path) or "."
            os.makedirs(backup_dir, exist_ok=True)
            tmp_path = f"{self.hot_backup_path}.tmp-{os.getpid()}-{threading.get_ident()}"
            try:
                src = sqlite3.connect(self.db_path, timeout=15)
                dst = sqlite3.connect(tmp_path, timeout=15)
                try:
                    src.backup(dst)
                    dst.commit()
                finally:
                    dst.close()
                    src.close()

                with open(tmp_path, "rb") as tmp_file:
                    os.fsync(tmp_file.fileno())

                if os.path.exists(self.hot_backup_path):
                    os.replace(self.hot_backup_path, self.hot_backup_prev_path)
                os.replace(tmp_path, self.hot_backup_path)

                dir_fd = os.open(backup_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                logger.exception("Hot backup sync failed (reason=%s)", reason)
            finally:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass

    def _backup_candidates(self) -> List[Dict[str, str]]:
        """Список кандидатов для восстановления в порядке убывания свежести."""
        candidates: List[Dict[str, str]] = []
        seen = set()
        for path, kind in (
            (self.hot_backup_path, "hot_active"),
            (self.hot_backup_prev_path, "hot_prev"),
        ):
            if path and os.path.exists(path):
                ap = os.path.abspath(path)
                if ap not in seen:
                    seen.add(ap)
                    candidates.append({"kind": kind, "path": ap})

        backup_dir = os.path.abspath(os.path.join(os.path.dirname(self.db_path) or ".", "..", "backups"))
        if os.path.isdir(backup_dir):
            gz_files = [
                os.path.join(backup_dir, name)
                for name in os.listdir(backup_dir)
                if name.startswith("memories-") and name.endswith(".sqlite3.gz")
            ]
            gz_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for path in gz_files:
                ap = os.path.abspath(path)
                if ap not in seen:
                    seen.add(ap)
                    candidates.append({"kind": "gzip", "path": ap})
        return candidates

    @staticmethod
    def _is_sqlite_file_healthy(path: str) -> Tuple[bool, str]:
        if not path or not os.path.exists(path):
            return False, "file_not_found"
        try:
            conn = sqlite3.connect(path, timeout=10)
            try:
                row = conn.execute("PRAGMA integrity_check;").fetchone()
                if not row:
                    return False, "integrity_check_empty"
                if str(row[0]).lower() != "ok":
                    return False, f"integrity_check_failed:{row[0]}"
                conn.execute("SELECT COUNT(1) FROM sqlite_master;").fetchone()
            finally:
                conn.close()
            return True, "ok"
        except Exception as e:
            return False, f"sqlite_open_failed:{e!r}"

    def _atomic_replace_db_from_plain_file(self, source_db_path: str) -> None:
        db_dir = os.path.dirname(self.db_path) or "."
        os.makedirs(db_dir, exist_ok=True)
        tmp_target = f"{self.db_path}.recover.tmp-{os.getpid()}-{threading.get_ident()}"
        shutil.copy2(source_db_path, tmp_target)
        with open(tmp_target, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_target, self.db_path)
        dir_fd = os.open(db_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _restore_candidate_to_main_db(self, candidate: Dict[str, str]) -> Tuple[bool, str]:
        kind = candidate.get("kind") or "unknown"
        path = candidate.get("path") or ""
        try:
            if kind in ("hot_active", "hot_prev"):
                ok, reason = self._is_sqlite_file_healthy(path)
                if not ok:
                    return False, f"{kind}_invalid:{reason}"
                self._atomic_replace_db_from_plain_file(path)
                return True, "restored_from_hot_backup"

            if kind == "gzip":
                with tempfile.NamedTemporaryFile(prefix="db-recover-", suffix=".sqlite3", delete=False) as tmp:
                    tmp_path = tmp.name
                try:
                    with gzip.open(path, "rb") as gz, open(tmp_path, "wb") as out:
                        shutil.copyfileobj(gz, out)
                    ok, reason = self._is_sqlite_file_healthy(tmp_path)
                    if not ok:
                        return False, f"gzip_invalid:{reason}"
                    self._atomic_replace_db_from_plain_file(tmp_path)
                    return True, "restored_from_gzip_backup"
                finally:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass

            return False, f"unsupported_candidate_kind:{kind}"
        except Exception as e:
            return False, f"restore_exception:{e!r}"

    def _build_recovery_alert_html(
        self,
        *,
        trigger: str,
        outcome: str,
        reason: str,
        source_path: Optional[str],
        target_path: str,
        details: List[str],
    ) -> str:
        src = source_path or "none"
        details_text = "\n".join(details) if details else "no details"
        return (
            "❌ <b>Ошибка на сайте: Self-heal базы данных</b>\n\n"
            f"<b>Триггер:</b> <code>{trigger}</code>\n"
            f"<b>Причина:</b> <code>{reason}</code>\n"
            f"<b>Что сделал алгоритм:</b> <code>{outcome}</code>\n"
            f"<b>Источник восстановления:</b> <code>{src}</code>\n"
            f"<b>Основной файл БД:</b> <code>{target_path}</code>\n\n"
            "<b>Детали шагов:</b>\n"
            f"<code>{details_text}</code>\n\n"
            "<b>Что делать:</b>\n"
            "1) Проверь логи на первопричину повреждения/удаления БД.\n"
            "2) Сверь актуальность данных в интерфейсе и /stats.\n"
            "3) Если данных не хватает — восстанови более старый backup вручную."
        )

    def _quarantine_current_db(self) -> Tuple[Optional[str], str]:
        if not os.path.exists(self.db_path):
            return None, "main_db_absent"
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        quarantined = f"{self.db_path}.broken-{ts}-{os.getpid()}"
        try:
            os.replace(self.db_path, quarantined)
            return quarantined, "quarantined_current_db"
        except Exception as e:
            return None, f"quarantine_failed:{e!r}"

    def _attempt_recover_main_db(self, trigger: str, reason: str) -> Tuple[bool, Optional[str], List[str]]:
        details: List[str] = []
        for candidate in self._backup_candidates():
            cpath = candidate.get("path") or ""
            ckind = candidate.get("kind") or "unknown"
            ok, restore_reason = self._restore_candidate_to_main_db(candidate)
            details.append(f"{ckind}:{cpath} -> {restore_reason}")
            if ok:
                return True, cpath, details
        return False, None, details

    def _initialize_with_self_heal(self) -> None:
        with self._recovery_lock:
            trigger = "startup"
            reason = ""
            final_reason = ""
            db_exists = os.path.exists(self.db_path)
            if not db_exists:
                reason = "main_db_missing"
            else:
                ok, health_reason = self._is_sqlite_file_healthy(self.db_path)
                if not ok:
                    reason = f"main_db_invalid:{health_reason}"
            if reason:
                final_reason = reason

            recovered = False
            recovered_from = None
            details: List[str] = []
            final_outcome = ""
            final_source: Optional[str] = None
            if reason:
                recovered, recovered_from, details = self._attempt_recover_main_db(trigger, reason)
                if recovered:
                    final_outcome = "восстановил БД из последнего валидного backup"
                    final_source = recovered_from

            init_error = None
            if not getattr(config, "IS_DEV_MODE", False):
                if not db_exists and not recovered:
                    import sys
                    msg = f"CRITICAL ERROR: Production database file '{self.db_path}' is missing and could not be recovered from backups. Automatic creation of a new empty database is disabled in Production mode to prevent data loss. Exiting."
                    logger.critical(msg)
                    print(msg, file=sys.stderr)
                    sys.exit(1)
            try:
                self._init_database()
            except Exception as e:
                init_error = e
                reason2 = f"init_database_exception:{e!r}"
                final_reason = reason2
                recovered2, recovered_from2, details2 = self._attempt_recover_main_db(trigger, reason2)
                if recovered2:
                    try:
                        self._init_database()
                        final_outcome = "после ошибки миграции/инициализации восстановил БД из backup и повторно применил миграции"
                        final_source = recovered_from2
                        details.extend(details2)
                        recovered = True
                        init_error = None
                    except Exception as e2:
                        init_error = e2
                        details.extend(details2)

                if init_error is not None:
                    allow_emergency_recreate = str(os.getenv("ALLOW_DB_EMERGENCY_RECREATE", "0")).strip().lower() in ("1", "true", "yes", "on")
                    if allow_emergency_recreate:
                        quarantined_path, quarantine_result = self._quarantine_current_db()
                        details.append(quarantine_result)
                        try:
                            self._init_database()
                            final_outcome = "backup не подошёл, выполнил emergency fallback: изолировал проблемный файл и создал новую пустую БД"
                            final_source = quarantined_path
                            recovered = True
                            init_error = None
                        except Exception as e3:
                            init_error = e3
                    else:
                        logger.critical(
                            "DB init failed and emergency recreate is disabled. "
                            "Keeping current DB file untouched to prevent data loss. "
                            "Set ALLOW_DB_EMERGENCY_RECREATE=1 only for explicit disaster recovery."
                        )
                if init_error is not None:
                    raise init_error

            if recovered:
                self._pending_recovery_alert = self._build_recovery_alert_html(
                    trigger=trigger,
                    outcome=final_outcome or "восстановление выполнено",
                    reason=final_reason or reason or "unknown",
                    source_path=final_source,
                    target_path=self.db_path,
                    details=details,
                )

            self._sync_hot_backup(reason="startup")

    def consume_recovery_alert(self) -> Optional[str]:
        """Возвращает и очищает pending alert о self-heal восстановлении БД."""
        with self._recovery_lock:
            alert = self._pending_recovery_alert
            self._pending_recovery_alert = None
            return alert
    
    def _init_database(self):
        """Инициализирует базу данных и создаёт таблицы"""
        with self._get_connection() as conn:
            # Таблица пользователей
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_admin BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Таблица воспоминаний
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    date TEXT NOT NULL,
                    content TEXT NOT NULL,
                    media_type TEXT,
                    media_file_id TEXT,
                    media_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            # Миграция: приватность воспоминаний + couple_id
            for col in [
                "privacy_type TEXT",
                "privacy_views_limit INTEGER",
                "privacy_question TEXT",
                "privacy_answer TEXT",
                "couple_id INTEGER",
                "media_items TEXT",
            ]:
                try:
                    conn.execute(f"ALTER TABLE memories ADD COLUMN {col}")
                except Exception as e:
                    # Колонка уже существует или другая не критичная ошибка
                    logger.debug("Migration skipped for memories column %r: %s", col, e)
            # Привязываем существующие воспоминания к первой паре (после создания couples)
            
            # Таблица администраторов
            conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER UNIQUE NOT NULL,
                    added_by INTEGER NOT NULL,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id),
                    FOREIGN KEY (added_by) REFERENCES users (user_id)
                )
            ''')
            
            # Таблица желаний (неограниченное количество на пользователя; UNIQUE убрана миграцией)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS wishes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    wish_number INTEGER DEFAULT 0,
                    content TEXT NOT NULL,
                    media_type TEXT,
                    media_file_id TEXT,
                    media_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')

            # Пытаемся добавить новые колонки медиа к таблице желаний для уже существующей базы
            for column_def in [
                "media_type TEXT",
                "media_file_id TEXT",
                "media_path TEXT",
            ]:
                try:
                    conn.execute(f"ALTER TABLE wishes ADD COLUMN {column_def}")
                except Exception as e:
                    # Колонка уже существует или другая не критичная ошибка
                    logger.debug("Migration skipped for wishes column %r: %s", column_def, e)

            # Таблица ожидаемых событий (события с датой в произвольном формате, распознанные ИИ)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS scheduled_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    event_datetime TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notified_at TIMESTAMP,
                    is_recurring INTEGER DEFAULT 0,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_events_user ON scheduled_events(user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_events_datetime ON scheduled_events(event_datetime)')

            # Миграция: добавить колонки для раздельной отметки уведомлений (по часовому поясу каждого)
            for col in ["notified_to_creator INTEGER DEFAULT 0", "notified_to_ksusha INTEGER DEFAULT 0", "is_recurring INTEGER DEFAULT 0"]:
                try:
                    conn.execute(f"ALTER TABLE scheduled_events ADD COLUMN {col}")
                except Exception as e:
                    logger.debug("Migration skipped for scheduled_events column %r: %s", col, e)

            # Миграция: медиа и форматирование описания (как у воспоминаний)
            for col in ["media_type TEXT", "media_file_id TEXT", "media_path TEXT"]:
                try:
                    conn.execute(f"ALTER TABLE scheduled_events ADD COLUMN {col}")
                except Exception as e:
                    logger.debug("Migration skipped for scheduled_events media column %r: %s", col, e)

            # Таблица избранного: user_id, item_type (memory/scheduled_event/wish), item_id
            conn.execute('''
                CREATE TABLE IF NOT EXISTS favorites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_type TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, item_type, item_id),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorites(user_id)')

            # Таблица настроек (общие текстовые настройки бота)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')

            # История версий приложения (источник version/description — app_version.py)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS version_history (
                    version TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    git_commit TEXT,
                    status TEXT DEFAULT 'stable',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_version_history_created_at ON version_history(created_at)')
            # Миграция: добавляем git_commit если таблица уже существовала без неё
            try:
                conn.execute("ALTER TABLE version_history ADD COLUMN git_commit TEXT")
            except Exception as e:
                logger.debug("Migration skipped for version_history.git_commit: %s", e)
            # Миграция: добавляем status
            try:
                conn.execute("ALTER TABLE version_history ADD COLUMN status TEXT DEFAULT 'stable'")
            except Exception as e:
                logger.debug("Migration skipped for version_history.status: %s", e)
            
            # Настройки пользователей (время, и т.д.)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    PRIMARY KEY (user_id, key),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            
            # Создаём индексы для быстрого поиска
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_users_admin ON users(is_admin)')

            # Таблица просмотров воспоминаний для ограниченного доступа
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memory_views (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    views_count INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(memory_id, user_id),
                    FOREIGN KEY (memory_id) REFERENCES memories (id),
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_views_mem_user ON memory_views(memory_id, user_id)')

            # Таблица визитов на сайт (для статистики по сайту)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS site_visits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visited_at_utc TEXT NOT NULL,
                    timezone_id TEXT,
                    ip TEXT,
                    ua_pretty TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_site_visits_visited_at ON site_visits(visited_at_utc)')

            # Таблица сессий просмотра конкретных моментов (для любимых моментов и времени на сайте)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS memory_view_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    started_utc TEXT NOT NULL,
                    ended_utc TEXT,
                    duration_sec INTEGER,
                    FOREIGN KEY (memory_id) REFERENCES memories (id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_view_sessions_mem ON memory_view_sessions(memory_id)')

            # Миграция: visitor_id для персональной статистики
            for tbl, col in [('site_visits', 'visitor_id'), ('memory_view_sessions', 'visitor_id')]:
                cur = conn.execute(f'PRAGMA table_info({tbl})')
                if not any(r[1] == col for r in cur.fetchall()):
                    conn.execute(f'ALTER TABLE {tbl} ADD COLUMN {col} TEXT')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_site_visits_visitor ON site_visits(visitor_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_view_sessions_visitor ON memory_view_sessions(visitor_id)')

            # Открытия категорий (нажатие «Показать») — для счётчика «X раз из Y»
            conn.execute('''
                CREATE TABLE IF NOT EXISTS category_opens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT,
                    section_id TEXT NOT NULL,
                    opened_at_utc TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_category_opens_visitor ON category_opens(visitor_id)')

            # Устройства (уникальные по visitor_id, обновляются при каждом визите)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT UNIQUE NOT NULL,
                    first_seen_utc TEXT,
                    last_seen_utc TEXT,
                    visit_count INTEGER DEFAULT 0,
                    ua_pretty TEXT,
                    country TEXT, city TEXT, isp TEXT, coords TEXT, timezone_id TEXT,
                    os TEXT, os_version TEXT, browser TEXT, browser_version TEXT,
                    architecture TEXT, device_type TEXT, model TEXT,
                    gpu_vendor TEXT, gpu_renderer TEXT,
                    screen_w INTEGER, screen_h INTEGER, pixel_ratio REAL, color_depth INTEGER, orientation TEXT,
                    language TEXT, cookies_enabled INTEGER, do_not_track INTEGER,
                    ram_gb INTEGER, cpu_cores INTEGER, touch_points INTEGER, is_bot INTEGER,
                    connection_type TEXT, downlink_mbps REAL, rtt_ms INTEGER, save_data INTEGER,
                    battery_level INTEGER, battery_charging INTEGER,
                    ip_server TEXT, ip_webrtc TEXT, referrer TEXT, theme TEXT,
                    role TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_devices_visitor ON devices(visitor_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen_utc)')
            # Миграция: если старая БД создана без колонки role, добавляем её
            try:
                cur = conn.execute('PRAGMA table_info(devices)')
                cols = [r[1] for r in cur.fetchall()]
                if 'role' not in cols:
                    conn.execute('ALTER TABLE devices ADD COLUMN role TEXT')
            except Exception as e:
                logger.debug("Devices migration check skipped: %s", e)

            # Суммарное время на сайте по visitor_id (обновляется каждые 5 сек с фронта)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS visitor_site_time (
                    visitor_id TEXT PRIMARY KEY,
                    total_seconds INTEGER NOT NULL DEFAULT 0
                )
            ''')

            # Уведомления от создателя — показываются Ксюше при входе на сайт
            conn.execute('''
                CREATE TABLE IF NOT EXISTS site_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    couple_id INTEGER,
                    text TEXT NOT NULL,
                    media_path TEXT,
                    media_type TEXT,
                    media_items TEXT,
                    created_at_utc TEXT NOT NULL,
                    is_delivered INTEGER NOT NULL DEFAULT 0,
                    delivered_at_utc TEXT
                )
            ''')

            # Миграция: поле media_items в site_notifications
            try:
                cur = conn.execute("PRAGMA table_info(site_notifications)")
                cols = [r[1] for r in cur.fetchall()]
                if 'media_items' not in cols:
                    conn.execute("ALTER TABLE site_notifications ADD COLUMN media_items TEXT")
                if 'couple_id' not in cols:
                    conn.execute("ALTER TABLE site_notifications ADD COLUMN couple_id INTEGER")
            except Exception as e:
                logger.debug("Migration skipped for site_notifications.media_items: %s", e)

            # Таблица праздничных анимаций (события, годовщины)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS site_celebrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    celebration_type TEXT NOT NULL,
                    event_title TEXT,
                    event_id INTEGER,
                    created_at_utc TEXT NOT NULL,
                    delivered_to TEXT NOT NULL DEFAULT ''
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_celebrations_created ON site_celebrations(created_at_utc)')

            # Миграция wishes: status
            try:
                cur = conn.execute("PRAGMA table_info(wishes)")
                cols = [r[1] for r in cur.fetchall()]
                if 'status' not in cols:
                    conn.execute("ALTER TABLE wishes ADD COLUMN status TEXT NOT NULL DEFAULT 'created'")
            except Exception as e:
                logger.debug("Migration skipped for wishes.status: %s", e)

            # Миграция companion_messages: reaction и is_pinned
            try:
                cur = conn.execute("PRAGMA table_info(companion_messages)")
                cols = [r[1] for r in cur.fetchall()]
                if 'reaction' not in cols:
                    conn.execute("ALTER TABLE companion_messages ADD COLUMN reaction TEXT")
                if 'is_pinned' not in cols:
                    conn.execute("ALTER TABLE companion_messages ADD COLUMN is_pinned INTEGER DEFAULT 0")
            except Exception as e:
                logger.debug("Migration skipped for companion_messages reaction/is_pinned: %s", e)

            # Последняя активность пользователя в боте
            conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_last_active (
                    user_id INTEGER PRIMARY KEY,
                    last_active_utc TEXT NOT NULL,
                    action TEXT
                )
            ''')

            # История переписки с ИИ‑компаньоном (по visitor_id)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS companion_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_id TEXT,
                    role TEXT NOT NULL,
                    site_role TEXT,
                    message TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_companion_messages_visitor_created ON companion_messages(visitor_id, created_at_utc)')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_tokens (
                    user_id INTEGER PRIMARY KEY,
                    token TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            # Миграция: добавляем role если таблица уже существовала без неё
            try:
                conn.execute("ALTER TABLE user_tokens ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            except Exception as e:
                logger.debug("Migration skipped for user_tokens.role: %s", e)

            # Краткоживущие токены входа на сайт (без хранения открытого токена)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_login_tokens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'user',
                    expires_at_utc TEXT NOT NULL,
                    issued_at_utc TEXT NOT NULL,
                    used_at_utc TEXT,
                    consumed_by TEXT,
                    is_revoked INTEGER NOT NULL DEFAULT 0
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_login_tokens_user ON user_login_tokens(user_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_login_tokens_exp ON user_login_tokens(expires_at_utc)')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id      INTEGER PRIMARY KEY,
                    display_name TEXT,
                    description  TEXT,
                    onboarded    BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # === Мультипарная архитектура ===

            # Таблица пар (couples)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS couples (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user1_id    INTEGER NOT NULL,
                    user2_id    INTEGER,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_couples_user1 ON couples(user1_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_couples_user2 ON couples(user2_id)')
            # Миграция: добавляем met_date если таблица уже существовала без него
            try:
                conn.execute("ALTER TABLE couples ADD COLUMN met_date TEXT")
            except Exception as e:
                logger.debug("Migration skipped for couples.met_date: %s", e)

            # Таблица инвайт-кодов
            conn.execute('''
                CREATE TABLE IF NOT EXISTS invite_codes (
                    code        TEXT PRIMARY KEY,
                    couple_id   INTEGER NOT NULL,
                    creator_id  INTEGER NOT NULL,
                    used        BOOLEAN NOT NULL DEFAULT FALSE,
                    used_by     INTEGER,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_at     TIMESTAMP,
                    FOREIGN KEY (couple_id) REFERENCES couples(id)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_invite_codes_couple ON invite_codes(couple_id)')

            # Таблица пользовательских категорий
            conn.execute('''
                CREATE TABLE IF NOT EXISTS custom_categories (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    couple_id   INTEGER,
                    name        TEXT NOT NULL,
                    description TEXT,
                    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    color       TEXT,
                    emoji       TEXT
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_custom_categories_couple ON custom_categories(couple_id)')
            # Миграция: добавляем color если таблица уже существовала без него
            try:
                conn.execute("ALTER TABLE custom_categories ADD COLUMN color TEXT")
            except Exception as e:
                logger.debug("Migration skipped for custom_categories.color: %s", e)
            # Миграция: добавляем emoji если таблица уже существовала без него
            try:
                conn.execute("ALTER TABLE custom_categories ADD COLUMN emoji TEXT")
            except Exception as e:
                logger.debug("Migration skipped for custom_categories.emoji: %s", e)

            # Таблица уведомлений о событиях (динамическая: хранит user_id вместо фиксированных ролей)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS event_notifications (
                    event_id    INTEGER NOT NULL,
                    user_id     INTEGER NOT NULL,
                    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (event_id, user_id)
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS ai_usage_window (
                    user_key        TEXT PRIMARY KEY,
                    window_start    TEXT NOT NULL,
                    message_count   INTEGER NOT NULL DEFAULT 0
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS user_subscription_tier (
                    user_key    TEXT PRIMARY KEY,
                    tier        TEXT NOT NULL DEFAULT 'free',
                    expires_at  TEXT
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS unlink_requests (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    token               TEXT UNIQUE NOT NULL,
                    user_id             INTEGER NOT NULL,
                    couple_id           INTEGER NOT NULL,
                    status              TEXT NOT NULL DEFAULT 'pending',
                    ip                  TEXT,
                    ua                  TEXT,
                    country             TEXT,
                    city                TEXT,
                    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    responded_at        TIMESTAMP,
                    transfer_invite_code TEXT
                )
            ''')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS security_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    type       TEXT NOT NULL,
                    user_id    INTEGER,
                    ip         TEXT,
                    ua         TEXT,
                    country    TEXT,
                    city       TEXT,
                    detail     TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Миграция: добавляем pre_bound_user_id в invite_codes
            try:
                cur = conn.execute("PRAGMA table_info(invite_codes)")
                cols = [r[1] for r in cur.fetchall()]
                if 'pre_bound_user_id' not in cols:
                    conn.execute("ALTER TABLE invite_codes ADD COLUMN pre_bound_user_id INTEGER DEFAULT NULL")
            except Exception as e:
                logger.debug("Migration skipped for invite_codes.pre_bound_user_id: %s", e)

            # Миграция: создаём первую пару из config CREATOR_ID + KSUSHA_ID (если не созданы)
            self._migrate_to_couples(conn)

            # Привязываем существующие воспоминания (без couple_id) к первой паре
            try:
                conn.execute('''
                    UPDATE memories SET couple_id = (SELECT id FROM couples ORDER BY id ASC LIMIT 1)
                    WHERE couple_id IS NULL
                ''')
            except Exception as e:
                logger.debug("Migration skipped while backfilling memories.couple_id: %s", e)

            # Миграция старых media_items: дополняем thumb_path и восстанавливаем single-item payload.
            try:
                repaired = self._repair_legacy_memory_media_rows(conn)
                if repaired:
                    logger.info("Media migration repaired %d memory rows", repaired)
            except Exception as e:
                logger.exception("Media migration failed: %s", e)

            # Регистрируем текущую версию приложения в истории версий (идемпотентно)
            self._register_current_app_version(conn)

            # Добавляем создателя и компаньона как администраторов
            self._add_default_admins(conn)
            
            conn.commit()

    @staticmethod
    def _normalize_version_metadata(version: Any, description: Any) -> Tuple[str, str]:
        normalized_version = str(version or "").strip() or "0.0.0"
        normalized_description = str(description or "").strip()
        return normalized_version, normalized_description

    def _register_version_history_entry(
        self,
        conn: sqlite3.Connection,
        version: Any,
        description: Any,
        git_commit: Optional[str] = None,
    ) -> None:
        """Idempotent upsert for version history by unique version.

        Rules:
        - same version + same description + same commit -> no-op
        - same version + changed description             -> update description
        - same version + changed commit                  -> update commit
        - new version                                    -> insert
        """
        normalized_version, normalized_description = self._normalize_version_metadata(version, description)
        normalized_commit: Optional[str] = (git_commit or "").strip() or None

        row = conn.execute(
            "SELECT description, git_commit FROM version_history WHERE version = ? LIMIT 1",
            (normalized_version,),
        ).fetchone()

        if row is None:
            conn.execute(
                "INSERT INTO version_history (version, description, git_commit) VALUES (?, ?, ?)",
                (normalized_version, normalized_description, normalized_commit),
            )
            logger.info("Registered app version: %s (commit=%s)", normalized_version, normalized_commit)
            return

        current_description = str(row["description"] or "").strip()
        current_commit: Optional[str] = (row["git_commit"] or "").strip() or None

        changed_fields: list[str] = []
        if current_description != normalized_description:
            changed_fields.append("description")

        if not changed_fields:
            return

        conn.execute(
            "UPDATE version_history SET description = ? WHERE version = ?",
            (normalized_description, normalized_version),
        )
        logger.info(
            "Updated app version %s fields: %s",
            normalized_version,
            ", ".join(changed_fields),
        )

    def _register_current_app_version(self, conn: sqlite3.Connection) -> None:
        version, description = get_version_metadata()
        git_commit = get_git_commit()
        self._register_version_history_entry(conn, version, description, git_commit=git_commit)

    def get_version_history(self) -> list[dict]:
        """Returns all version_history rows, newest first."""
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT version, description, git_commit, created_at"
                " FROM version_history ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "version": row["version"],
                "description": row["description"],
                "git_commit": row["git_commit"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    @staticmethod
    def _hash_privacy_answer(answer: str) -> str:
        val = (answer or "").strip().lower()
        digest = hashlib.sha256(val.encode("utf-8")).hexdigest()
        return f"sha256${digest}"

    @staticmethod
    def verify_privacy_answer(stored_value: str, candidate: str) -> bool:
        stored = (stored_value or "").strip()
        cand = (candidate or "").strip().lower()
        if not stored or not cand:
            return False
        if stored.startswith("sha256$"):
            expected = Database._hash_privacy_answer(cand)
            return secrets.compare_digest(stored, expected)
        # legacy plaintext fallback
        return secrets.compare_digest(stored.lower(), cand)

    @staticmethod
    def _decode_media_items(raw: Any) -> Optional[List[Dict[str, Any]]]:
        if not raw:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, list):
                return None
            result: List[Dict[str, Any]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                mt = str(item.get("type") or "").strip()
                fid = str(item.get("file_id") or "").strip()
                mp = str(item.get("path") or "").strip()
                if not mt:
                    continue
                decoded: Dict[str, Any] = {"type": mt, "file_id": fid or None, "path": mp or None}
                thumb_path = str(item.get("thumb_path") or "").strip()
                thumb_url = str(item.get("thumb_url") or "").strip()
                original_url = str(item.get("original_url") or "").strip()
                width = item.get("width")
                height = item.get("height")
                if thumb_path:
                    decoded["thumb_path"] = thumb_path
                if thumb_url:
                    decoded["thumb_url"] = thumb_url
                if original_url:
                    decoded["original_url"] = original_url
                if isinstance(width, int) and width > 0:
                    decoded["width"] = width
                if isinstance(height, int) and height > 0:
                    decoded["height"] = height
                result.append(decoded)
            return result or None
        except Exception:
            return None
    
    def _migrate_to_couples(self, conn: sqlite3.Connection):
        """
        Однократная миграция: создаём первую пару из config.CREATOR_ID + config.KSUSHA_ID,
        если оба ненулевые и пара ещё не существует.
        """
        creator_id = config.CREATOR_ID
        ksusha_id = config.KSUSHA_ID
        if not creator_id and not ksusha_id:
            return
        existing = conn.execute('SELECT id FROM couples LIMIT 1').fetchone()
        if existing:
            return
        if creator_id and ksusha_id and ksusha_id != 0:
            conn.execute(
                'INSERT INTO couples (user1_id, user2_id) VALUES (?, ?)',
                (creator_id, ksusha_id)
            )
        elif creator_id:
            conn.execute(
                'INSERT INTO couples (user1_id, user2_id) VALUES (?, NULL)',
                (creator_id,)
            )

    def _add_default_admins(self, conn: sqlite3.Connection):
        """Добавляет создателя и компаньона в качестве администраторов.
        Username и имена не задаём статично — заполнятся при первом /start пользователя.
        """
        default_admins = [
            (config.CREATOR_ID, None, None, None),
            (self.get_ksusha_id(), None, None, None),
        ]
        
        for user_id, username, first_name, last_name in default_admins:
            if not user_id:
                continue
            conn.execute('''
                INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, is_admin)
                VALUES (?, ?, ?, ?, TRUE)
            ''', (user_id, username, first_name, last_name))
            
            # Добавляем в таблицу админов
            conn.execute('''
                INSERT OR IGNORE INTO admins (user_id, added_by)
                VALUES (?, ?)
            ''', (user_id, config.CREATOR_ID))
    
    # === Методы для работы с пользователями ===
    
    def add_or_update_user(self, user_id: int, username: str = None, 
                          first_name: str = None, last_name: str = None) -> bool:
        """Добавляет или обновляет информацию о пользователе"""
        try:
            with self._get_connection() as conn:
                conn.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name, last_seen)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = COALESCE(excluded.username, username),
                        first_name = COALESCE(excluded.first_name, first_name),
                        last_name = COALESCE(excluded.last_name, last_name),
                        last_seen = CURRENT_TIMESTAMP
                ''', (user_id, username, first_name, last_name))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при добавлении пользователя: {e}")
            return False
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получает информацию о пользователе"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT * FROM users WHERE user_id = ?', 
                    (user_id,)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении пользователя: {e}")
            return None
    
    def get_all_user_ids(self) -> List[int]:
        """Возвращает список всех user_id пользователей"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('SELECT user_id FROM users')
                return [row['user_id'] for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка при получении списка пользователей: {e}")
            return []

    # === Персональные токены пользователей ===

    def _hash_login_token(self, token: str) -> str:
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    def issue_user_login_token(self, user_id: int, role: str = "user", ttl_seconds: int = 43200) -> str:
        """
        Выдаёт одноразовый токен входа на сайт.
        - TTL по умолчанию 12 часов
        - Каждый выданный токен живёт до истечения TTL (или первого использования)
        - В БД хранится только SHA-256 hash токена
        """
        ttl = max(300, int(ttl_seconds or 43200))
        now_utc = datetime.now(timezone.utc)
        exp_utc = now_utc + timedelta(seconds=ttl)
        token = secrets.token_urlsafe(32)
        token_hash = self._hash_login_token(token)
        try:
            with self._get_connection() as conn:
                # Гигиена таблицы токенов
                conn.execute(
                    """
                    DELETE FROM user_login_tokens
                    WHERE (is_revoked = 1 OR used_at_utc IS NOT NULL)
                      AND issued_at_utc < datetime('now', '-2 days')
                    """
                )
                conn.execute(
                    """
                    UPDATE user_login_tokens
                    SET is_revoked = 1
                    WHERE used_at_utc IS NULL
                      AND is_revoked = 0
                      AND expires_at_utc < datetime('now')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO user_login_tokens
                        (user_id, token_hash, role, expires_at_utc, issued_at_utc, is_revoked)
                    VALUES (?, ?, ?, ?, ?, 0)
                    """,
                    (
                        user_id,
                        token_hash,
                        role or "user",
                        exp_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка выдачи login-token для {user_id}: {e}")
            return ""
        return token

    def consume_user_login_token(self, token: str, consumed_by: str = "") -> Optional[dict]:
        """
        Проверяет и атомарно помечает токен как использованный.
        Возвращает {user_id, role} при успехе, иначе None.
        """
        if not token:
            return None
        token_hash = self._hash_login_token(token)
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE user_login_tokens
                    SET is_revoked = 1
                    WHERE used_at_utc IS NULL
                      AND is_revoked = 0
                      AND expires_at_utc < datetime('now')
                    """
                )
                row = conn.execute(
                    """
                    SELECT id, user_id, role, expires_at_utc, used_at_utc, is_revoked
                    FROM user_login_tokens
                    WHERE token_hash = ?
                    LIMIT 1
                    """,
                    (token_hash,),
                ).fetchone()
                if not row:
                    return None
                if row["is_revoked"] or row["used_at_utc"]:
                    return None
                try:
                    exp_dt = datetime.strptime((row["expires_at_utc"] or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if exp_dt < now_utc:
                        conn.execute(
                            "UPDATE user_login_tokens SET is_revoked = 1 WHERE id = ?",
                            (row["id"],),
                        )
                        conn.commit()
                        return None
                except Exception:
                    return None

                cur = conn.execute(
                    """
                    UPDATE user_login_tokens
                    SET used_at_utc = ?, consumed_by = ?
                    WHERE id = ?
                      AND used_at_utc IS NULL
                      AND is_revoked = 0
                    """,
                    (now_str, (consumed_by or "")[:255], row["id"]),
                )
                conn.commit()
                if cur.rowcount != 1:
                    return None
                return {"user_id": row["user_id"], "role": row["role"] or "user"}
        except Exception as e:
            logger.exception(f"Ошибка consume login-token: {e}")
            return None

    def peek_user_login_token(self, token: str) -> Optional[dict]:
        """
        Только проверяет чей это токен, не расходуя его.
        Возвращает {user_id, role} при успехе, иначе None.
        """
        if not token:
            return None
        token_hash = self._hash_login_token(token)
        now_utc = datetime.now(timezone.utc)
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT user_id, role, expires_at_utc, used_at_utc, is_revoked
                    FROM user_login_tokens
                    WHERE token_hash = ?
                    LIMIT 1
                    """,
                    (token_hash,),
                ).fetchone()
                if not row:
                    return None
                if row["is_revoked"] or row["used_at_utc"]:
                    return None
                try:
                    exp_dt = datetime.strptime((row["expires_at_utc"] or "")[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if exp_dt < now_utc:
                        return None
                except Exception:
                    return None
                
                return {"user_id": row["user_id"], "role": row["role"] or "user"}
        except Exception as e:
            logger.exception(f"Ошибка peek login-token: {e}")
            return None

    def get_or_create_user_token(self, user_id: int, role: str = "user") -> str:
        """Совместимость: выдаёт новый login-token с комфортным TTL для ссылки из бота."""
        return self.issue_user_login_token(user_id, role=role, ttl_seconds=43200)

    def get_user_by_token(self, token: str) -> Optional[dict]:
        """Возвращает словарь {user_id, role} по токену. None если не найден."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT user_id, role FROM user_tokens WHERE token = ?', (token,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при поиске пользователя по токену: {e}")
            return None

    def get_user_id_by_token(self, token: str) -> Optional[int]:
        """Находит user_id по токену. Возвращает None если не найден."""
        result = self.get_user_by_token(token)
        return result['user_id'] if result else None

    def set_user_token_role(self, user_id: int, role: str) -> bool:
        """Обновляет роль пользователя в таблице токенов."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'UPDATE user_tokens SET role = ? WHERE user_id = ?', (role, user_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при обновлении роли токена для {user_id}: {e}")
            return False

    # === Профили пользователей (онбординг) ===

    def get_user_profile(self, user_id: int) -> Optional[Dict]:
        """Возвращает профиль пользователя или None если не существует."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT * FROM user_profiles WHERE user_id = ?', (user_id,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении профиля {user_id}: {e}")
            return None

    def is_user_onboarded(self, user_id: int) -> bool:
        """True если пользователь уже прошёл онбординг."""
        profile = self.get_user_profile(user_id)
        return bool(profile and profile.get("onboarded"))

    def save_user_profile(self, user_id: int, display_name: Optional[str] = None,
                          description: Optional[str] = None, onboarded: bool = False) -> bool:
        """Создаёт или обновляет профиль пользователя."""
        try:
            with self._get_connection() as conn:
                conn.execute('''
                    INSERT INTO user_profiles (user_id, display_name, description, onboarded, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id) DO UPDATE SET
                        display_name = COALESCE(excluded.display_name, display_name),
                        description  = COALESCE(excluded.description,  description),
                        onboarded    = excluded.onboarded,
                        updated_at   = CURRENT_TIMESTAMP
                ''', (user_id, display_name, description, onboarded))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при сохранении профиля {user_id}: {e}")
            return False

    def get_display_name(self, user_id: int, fallback: str = "") -> str:
        """Возвращает отображаемое имя пользователя: сначала из профиля, потом first_name из users."""
        profile = self.get_user_profile(user_id)
        if profile and profile.get("display_name"):
            return profile["display_name"]
        user = self.get_user(user_id)
        if user:
            return user.get("first_name") or user.get("username") or fallback
        return fallback

    # === Методы для общих настроек ===

    def get_setting(self, key: str) -> Optional[str]:
        """Получает значение настройки по ключу"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT value FROM settings WHERE key = ?',
                    (key,)
                )
                row = cursor.fetchone()
                return row['value'] if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении настройки {key}: {e}")
            return None

    def set_setting(self, key: str, value: str) -> bool:
        """Создаёт или обновляет текстовую настройку по ключу"""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO settings (key, value)
                    VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    ''',
                    (key, value)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при сохранении настройки {key}: {e}")
            return False

    def delete_setting(self, key: str) -> bool:
        """Удаляет настройку по ключу"""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'DELETE FROM settings WHERE key = ?',
                    (key,)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении настройки {key}: {e}")
            return False

    # === Настройки пользователей ===

    def get_user_setting(self, user_id: int, key: str) -> Optional[str]:
        """Получает значение настройки пользователя по ключу"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT value FROM user_settings WHERE user_id = ? AND key = ?',
                    (user_id, key)
                )
                row = cursor.fetchone()
                return row['value'] if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении настройки пользователя {user_id} {key}: {e}")
            return None

    def set_user_setting(self, user_id: int, key: str, value: str) -> bool:
        """Устанавливает настройку пользователя"""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?, ?, ?)',
                    (user_id, key, value)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при сохранении настройки пользователя {user_id} {key}: {e}")
            return False

    def get_user_all_settings(self, user_id: int) -> Dict[str, Any]:
        """Возвращает все настройки пользователя (user_settings) в виде словаря key -> value."""
        result: Dict[str, Any] = {}
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT key, value FROM user_settings WHERE user_id = ?',
                    (user_id,),
                )
                for row in cursor.fetchall():
                    k, v = row[0], row[1]
                    result[str(k)] = v
        except Exception as e:
            logger.exception(f"Ошибка при получении всех настроек пользователя {user_id}: {e}")
        return result

    def are_notifications_enabled(self, user_id: int) -> bool:
        """Проверяет, включены ли уведомления для пользователя. По умолчанию — включены."""
        val = self.get_user_setting(user_id, "notifications_enabled")
        if val is None:
            return True
        return str(val).lower() in ("1", "true", "on", "yes")

    # Ключи категорий уведомлений → ключи user_settings
    _NOTIF_CAT_KEYS = {
        "important_moments": "notif_cat_moments",
        "memories":          "notif_cat_memories",
        "important_dates":   "notif_cat_dates",
        "events":            "notif_cat_events",
        "wishes":            "notif_cat_wishes",
        "site_visits":       "notif_cat_site_visits",
        "logger":            "notif_cat_logger",
    }

    def is_category_notif_enabled(self, user_id: int, category: str) -> bool:
        """Включены ли уведомления для конкретной категории (по умолчанию True)."""
        setting_key = self._NOTIF_CAT_KEYS.get(category)
        if not setting_key:
            return True
        val = self.get_user_setting(user_id, setting_key)
        if val is None:
            return True
        return str(val).lower() in ("1", "true", "on", "yes")

    def set_category_notif(self, user_id: int, category: str, enabled: bool) -> None:
        """Сохраняет настройку уведомлений для конкретной категории."""
        setting_key = self._NOTIF_CAT_KEYS.get(category)
        if setting_key:
            self.set_user_setting(user_id, setting_key, "1" if enabled else "0")

    def get_all_category_notif(self, user_id: int) -> dict:
        """Возвращает словарь {category_key: bool} для всех категорий."""
        return {cat: self.is_category_notif_enabled(user_id, cat) for cat in self._NOTIF_CAT_KEYS}

    def is_favorites_enabled(self, user_id: int) -> bool:
        """Проверяет, включена ли система избранного для пользователя. По умолчанию — включена."""
        val = self.get_user_setting(user_id, "favorites_enabled")
        if val is None:
            return True
        return str(val).lower() in ("1", "true", "on", "yes")

    # === Методы для работы с избранным ===

    def get_user_favorites_raw(self, user_id: int) -> List[Dict[str, Any]]:
        """Возвращает список избранных элементов пользователя в «сыром» виде (тип + id + created_at)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT item_type, item_id, created_at FROM favorites WHERE user_id = ? ORDER BY created_at ASC',
                    (user_id,),
                )
                rows = cursor.fetchall()
            out: List[Dict[str, Any]] = []
            for row in rows:
                out.append(
                    {
                        "item_type": row[0],
                        "item_id": row[1],
                        "created_at": row[2],
                    }
                )
            return out
        except Exception as e:
            logger.exception(f"Ошибка при получении избранного пользователя {user_id}: {e}")
            return []

    def add_favorite(self, user_id: int, item_type: str, item_id: int) -> bool:
        """Добавляет элемент в избранное. item_type: memory, scheduled_event, wish."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'INSERT OR IGNORE INTO favorites (user_id, item_type, item_id) VALUES (?, ?, ?)',
                    (user_id, item_type, item_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при добавлении в избранное: {e}")
            return False

    def remove_favorite(self, user_id: int, item_type: str, item_id: int) -> bool:
        """Удаляет элемент из избранного."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'DELETE FROM favorites WHERE user_id = ? AND item_type = ? AND item_id = ?',
                    (user_id, item_type, item_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении из избранного: {e}")
            return False

    def is_in_favorites(self, user_id: int, item_type: str, item_id: int) -> bool:
        """Проверяет, есть ли элемент в избранном."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT 1 FROM favorites WHERE user_id = ? AND item_type = ? AND item_id = ?',
                    (user_id, item_type, item_id)
                )
                return cursor.fetchone() is not None
        except Exception as e:
            logger.exception(f"Ошибка при проверке избранного: {e}")
            return False

    def get_user_favorites_stats(self, user_id: int) -> Dict[str, int]:
        """Возвращает статистику избранного по типам."""
        stats = {
            "important_moments": 0,
            "memories": 0,
            "important_dates": 0,
            "scheduled_events": 0,
            "wishes": 0,
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT item_type, item_id FROM favorites WHERE user_id = ?',
                    (user_id,)
                )
                rows = cursor.fetchall()
                for row in rows:
                    it, iid = row[0], row[1]
                    if it == "memory":
                        m = self.get_memory(iid)
                        if m:
                            stats[m.category] = stats.get(m.category, 0) + 1
                    elif it == "scheduled_event":
                        stats["scheduled_events"] += 1
                    elif it == "wish":
                        stats["wishes"] += 1
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте избранного: {e}")
        return stats

    def get_user_favorites_paged(
        self,
        user_id: int,
        page: int = 1,
        per_page: int = 10,
        query: Optional[str] = None,
    ) -> Tuple[List[Dict], int]:
        """Возвращает (список избранных с title и type_label, всего)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT item_type, item_id, created_at FROM favorites WHERE user_id = ? ORDER BY created_at ASC',
                    (user_id,)
                )
                rows = cursor.fetchall()
            items = []
            query_clean = (query or "").strip()
            query_lower = query_clean.lower() if query_clean else None
            for row in rows:
                it, iid, _ = row[0], row[1], row[2]
                title = ""
                type_label = ""
                if it == "memory":
                    m = self.get_memory(iid)
                    if not m:
                        continue
                    title = (m.title or "").strip()
                    cat = config.CATEGORIES.get(m.category, {})
                    type_label = cat.get("title", m.category)
                    if isinstance(cat, dict) and cat.get("emoji"):
                        type_label = (type_label or "").replace(cat["emoji"], "", 1).strip() or type_label
                elif it == "scheduled_event":
                    e = self.get_scheduled_event(iid)
                    if not e:
                        continue
                    title = (e.title or "").strip()
                    type_label = "Событие на дату"
                elif it == "wish":
                    w = self.get_wish(iid)
                    if not w:
                        continue
                    title = (w.content or "").strip()[:50]
                    if len((w.content or "").strip()) > 50:
                        title += "..."
                    wn = f"желание #{w.id}"
                    type_label = f"{wn.capitalize()} желание"
                if query_lower and query_lower not in (title or "").lower() and query_lower not in (type_label or "").lower():
                    continue
                items.append({"item_type": it, "item_id": iid, "title": title, "type_label": type_label})
            total = len(items)
            offset = (page - 1) * per_page
            page_items = items[offset : offset + per_page]
            return page_items, total
        except Exception as e:
            logger.exception(f"Ошибка при получении избранного: {e}")
            return [], 0

    # === Методы для работы с желаниями ===

    def add_wish(
        self,
        user_id: int,
        content: str,
        media_type: Optional[str] = None,
        media_file_id: Optional[str] = None,
        media_path: Optional[str] = None,
    ) -> int:
        """Создаёт новое желание."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO wishes (user_id, content, media_type, media_file_id, media_path)
                    VALUES (?, ?, ?, ?, ?)
                ''', (user_id, content, media_type, media_file_id, media_path))
                conn.commit()
                wish_id = cursor.lastrowid
                logger.info(f"Добавлено желание #{wish_id} пользователя {user_id}")
                return wish_id
        except Exception as e:
            logger.exception(f"Ошибка при добавлении желания: {e}")
            return -1

    def update_wish(
        self,
        wish_id: int,
        content: str,
        media_type: Optional[str] = None,
        media_file_id: Optional[str] = None,
        media_path: Optional[str] = None,
    ) -> bool:
        """Обновляет текст и медиа желания"""
        try:
            with self._get_connection() as conn:
                conn.execute('''
                    UPDATE wishes
                    SET content = ?,
                        media_type = ?,
                        media_file_id = ?,
                        media_path = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (content, media_type, media_file_id, media_path, wish_id))
                conn.commit()
                logger.info(f"Обновлено желание #{wish_id}")
                return True
        except Exception as e:
            logger.exception(f"Ошибка при обновлении желания: {e}")
            return False

    def update_wish_status(self, wish_id: int, status: str) -> bool:
        """Обновляет статус желания. status: created | in_progress | done"""
        allowed = {"created", "in_progress", "done"}
        if status not in allowed:
            return False
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE wishes SET status = ? WHERE id = ?",
                    (status, wish_id)
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            logger.exception("Ошибка update_wish_status: %s", e)
            return False

    def delete_wish(self, wish_id: int) -> bool:
        """Удаляет желание и связанный медиафайл"""
        try:
            wish = self.get_wish(wish_id)
            if not wish:
                return False

            media_candidates: List[str] = []
            if wish.media_path:
                normalized = self._normalize_media_path(wish.media_path)
                if normalized:
                    media_candidates.append(normalized)

            with self._get_connection() as conn:
                conn.execute('DELETE FROM wishes WHERE id = ?', (wish_id,))
                conn.commit()

            self._delete_media_files_if_unreferenced(media_candidates)
            logger.info(f"Удалено желание #{wish_id}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении желания: {e}")
            return False

    def get_wish(self, wish_id: int) -> Optional[Wish]:
        """Получает желание по ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('SELECT * FROM wishes WHERE id = ?', (wish_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                keys = set(row.keys())
                return Wish(
                    id=row['id'],
                    user_id=row['user_id'],
                    content=row['content'],
                    media_type=row['media_type'] if 'media_type' in keys else None,
                    media_file_id=row['media_file_id'] if 'media_file_id' in keys else None,
                    media_path=row['media_path'] if 'media_path' in keys else None,
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    status=(row['status'] if 'status' in keys else None) or 'created',
                    wish_number=row['wish_number'] if 'wish_number' in keys else 0,
                )
        except Exception as e:
            logger.exception(f"Ошибка при получении желания: {e}")
            return None

    def get_user_wishes(self, user_id: int) -> List[Wish]:
        """Получает все желания пользователя."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT * FROM wishes WHERE user_id = ? ORDER BY created_at ASC',
                    (user_id,)
                )
                keys = None
                rows = cursor.fetchall()
                if rows:
                    keys = set(rows[0].keys())
                return [
                    Wish(
                        id=row['id'],
                        user_id=row['user_id'],
                        content=row['content'],
                        media_type=row['media_type'] if keys and 'media_type' in keys else None,
                        media_file_id=row['media_file_id'] if keys and 'media_file_id' in keys else None,
                        media_path=row['media_path'] if keys and 'media_path' in keys else None,
                        created_at=row['created_at'],
                        updated_at=row['updated_at'],
                        status=(row['status'] if keys and 'status' in keys else None) or 'created',
                        wish_number=row['wish_number'] if keys and 'wish_number' in keys else 0,
                    )
                    for row in rows
                ]
        except Exception as e:
            logger.exception(f"Ошибка при получении желаний пользователя: {e}")
            return []


    
    def is_admin(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь администратором"""
        # Создатель и компаньон всегда считаются администраторами
        if user_id in (self.get_creator_id(), self.get_ksusha_id()):
            return True
        
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT is_admin FROM users WHERE user_id = ?', 
                    (user_id,)
                )
                row = cursor.fetchone()
                return bool(row and row['is_admin'])
        except Exception as e:
            logger.exception(f"Ошибка при проверке админа: {e}")
            return False
    
    def is_creator(self, user_id: int) -> bool:
        """Проверяет, является ли пользователь создателем"""
        return user_id == self.get_creator_id()
    
    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        """Получает статистику пользователя (воспоминания, события, желания)"""
        result = {
            'total_memories': 0,
            'categories_count': 0,
            'photos_count': 0,
            'videos_count': 0,
            'audio_count': 0,
            'scheduled_events_count': 0,
            'wishes_count': 0,
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT 
                        COUNT(*) as total_memories,
                        COUNT(DISTINCT category) as categories_count,
                        SUM(CASE WHEN media_type = 'photo' THEN 1 ELSE 0 END) as photos_count,
                        SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END) as videos_count,
                        SUM(CASE WHEN media_type IN ('audio', 'voice') THEN 1 ELSE 0 END) as audio_count
                    FROM memories 
                    WHERE user_id = ?
                ''', (user_id,))
                row = cursor.fetchone()
                if row:
                    result.update(dict(row))
                c = conn.execute('SELECT COUNT(*) FROM scheduled_events WHERE user_id = ?', (user_id,))
                result['scheduled_events_count'] = c.fetchone()[0] or 0
                c = conn.execute('SELECT COUNT(*) FROM wishes WHERE user_id = ?', (user_id,))
                result['wishes_count'] = c.fetchone()[0] or 0
                return result
        except Exception as e:
            logger.exception(f"Ошибка при получении статистики пользователя: {e}")
            return result
    
    # === Методы для работы с администраторами ===
    
    def add_admin(
        self,
        user_id: int,
        added_by: int,
        *,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> bool:
        """Добавляет нового администратора. Опционально сохраняет username, first_name, last_name (например, из get_chat)."""
        try:
            with self._get_connection() as conn:
                user = self.get_user(user_id)
                if not user:
                    conn.execute('''
                        INSERT INTO users (user_id, username, first_name, last_name, is_admin)
                        VALUES (?, ?, ?, ?, TRUE)
                    ''', (user_id, username or None, first_name or None, last_name or None))
                else:
                    # Обновляем статус и при наличии — профиль (username, first_name, last_name)
                    if username is not None or first_name is not None or last_name is not None:
                        conn.execute('''
                            UPDATE users
                            SET is_admin = TRUE,
                                username = COALESCE(?, username),
                                first_name = COALESCE(?, first_name),
                                last_name = COALESCE(?, last_name)
                            WHERE user_id = ?
                        ''', (username, first_name, last_name, user_id))
                    else:
                        conn.execute(
                            'UPDATE users SET is_admin = TRUE WHERE user_id = ?',
                            (user_id,)
                        )
                
                # Добавляем в таблицу админов
                conn.execute('''
                    INSERT OR REPLACE INTO admins (user_id, added_by)
                    VALUES (?, ?)
                ''', (user_id, added_by))
                
                conn.commit()
                logger.info(f"Пользователь {user_id} добавлен как администратор")
                return True
        except Exception as e:
            logger.exception(f"Ошибка при добавлении администратора: {e}")
            return False
    
    def remove_admin(self, user_id: int) -> bool:
        """Удаляет администратора (кроме создателя и Ксюши)"""
        # Нельзя удалить создателя и Ксюшу из админов
        if user_id in (self.get_creator_id(), self.get_ksusha_id()):
            return False
        
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'UPDATE users SET is_admin = FALSE WHERE user_id = ?',
                    (user_id,)
                )
                
                conn.execute(
                    'DELETE FROM admins WHERE user_id = ?',
                    (user_id,)
                )
                
                conn.commit()
                logger.info(f"Пользователь {user_id} удалён из администраторов")
                return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении администратора: {e}")
            return False
    
    def get_all_admins(self) -> List[Dict]:
        """Получает список всех администраторов"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT u.user_id, u.username, u.first_name, u.last_name,
                           a.added_at, u.created_at, u.last_seen
                    FROM users u
                    LEFT JOIN admins a ON u.user_id = a.user_id
                    WHERE u.is_admin = TRUE
                    ORDER BY 
                        CASE 
                            WHEN u.user_id = ? THEN 1
                            WHEN u.user_id = ? THEN 2
                            ELSE 3
                        END,
                        a.added_at DESC
                ''', (self.get_creator_id(), self.get_ksusha_id()))
                return [dict(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка при получении списка админов: {e}")
            return []
    
    # === Методы для работы с воспоминаниями ===
    
    def add_memory(self, user_id: int, category: str, title: str, date: str,
                   content: str, media_type: str = None, media_file_id: str = None,
                   media_path: str = None, couple_id: int = None,
                   media_items: Optional[List[Dict[str, Any]]] = None) -> int:
        """Добавляет новое воспоминание, привязывая его к паре пользователя."""
        if couple_id is None:
            couple = self.get_couple_by_user(user_id)
            if couple:
                couple_id = couple['id']
        try:
            media_items_norm = media_items[:6] if media_items else None
            if media_items_norm:
                first = media_items_norm[0]
                media_type = first.get("type") or media_type
                media_file_id = first.get("file_id") or media_file_id
                media_path = first.get("path") or media_path
            media_items_json = json.dumps(media_items_norm, ensure_ascii=False) if media_items_norm else None
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO memories
                    (user_id, category, title, date, content, media_type, media_file_id, media_path, couple_id, media_items)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, category, title, date, content, media_type, media_file_id, media_path, couple_id, media_items_json))

                conn.commit()
                memory_id = cursor.lastrowid
                logger.info(f"Добавлено воспоминание #{memory_id} пользователем {user_id} (пара {couple_id})")
                return memory_id
        except Exception as e:
            logger.exception(f"Ошибка при добавлении воспоминания: {e}")
            return -1
    
    def get_memory(self, memory_id: int) -> Optional[Memory]:
        """Получает воспоминание по ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT m.*, u.username, u.first_name, u.last_name
                    FROM memories m 
                    LEFT JOIN users u ON m.user_id = u.user_id 
                    WHERE m.id = ?
                ''', (memory_id,))
                
                row = cursor.fetchone()
                if not row:
                    return None
                
                return Memory(
                    id=row['id'],
                    user_id=row['user_id'],
                    username=row['username'],
                    first_name=row['first_name'],
                    last_name=row['last_name'],
                    category=row['category'],
                    title=row['title'],
                    date=row['date'],
                    content=row['content'],
                    media_type=row['media_type'],
                    media_file_id=row['media_file_id'],
                    media_path=row['media_path'],
                    media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                    created_at=row['created_at'],
                    updated_at=row['updated_at'],
                    privacy_type=row['privacy_type'] if 'privacy_type' in row.keys() else None,
                    privacy_views_limit=row['privacy_views_limit'] if 'privacy_views_limit' in row.keys() else None,
                    privacy_question=row['privacy_question'] if 'privacy_question' in row.keys() else None,
                    privacy_answer=row['privacy_answer'] if 'privacy_answer' in row.keys() else None,
                )
        except Exception as e:
            logger.exception(f"Ошибка при получении воспоминания: {e}")
            return None
    
    def get_memories_by_category(self, category: str, limit: int = 50,
                                  couple_id: Optional[int] = None) -> List[Memory]:
        """Получает воспоминания указанной категории, изолированные по паре."""
        try:
            with self._get_connection() as conn:
                if couple_id is not None:
                    cursor = conn.execute('''
                        SELECT m.*, u.username, u.first_name, u.last_name
                        FROM memories m
                        LEFT JOIN users u ON m.user_id = u.user_id
                        WHERE m.category = ? AND (m.couple_id = ? OR m.couple_id IS NULL)
                        ORDER BY m.created_at ASC
                        LIMIT ?
                    ''', (category, couple_id, limit))
                else:
                    cursor = conn.execute('''
                        SELECT m.*, u.username, u.first_name, u.last_name
                        FROM memories m 
                        LEFT JOIN users u ON m.user_id = u.user_id 
                        WHERE m.category = ?
                        ORDER BY m.created_at ASC
                        LIMIT ?
                    ''', (category, limit))
                
                memories = []
                for row in cursor.fetchall():
                    memories.append(Memory(
                        id=row['id'],
                        user_id=row['user_id'],
                        username=row['username'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        category=row['category'],
                        title=row['title'],
                        date=row['date'],
                        content=row['content'],
                        media_type=row['media_type'],
                        media_file_id=row['media_file_id'],
                        media_path=row['media_path'],
                        media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                        created_at=row['created_at'],
                        updated_at=row['updated_at'],
                        privacy_type=row['privacy_type'] if 'privacy_type' in row.keys() else None,
                        privacy_views_limit=row['privacy_views_limit'] if 'privacy_views_limit' in row.keys() else None,
                        privacy_question=row['privacy_question'] if 'privacy_question' in row.keys() else None,
                        privacy_answer=row['privacy_answer'] if 'privacy_answer' in row.keys() else None,
                    ))
                
                return memories
        except Exception as e:
            logger.exception(f"Ошибка при получении воспоминаний по категории: {e}")
            return []
    
    def get_user_memories(self, user_id: int, limit: int = 100) -> List[Memory]:
        """Получает все воспоминания пользователя"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT m.*, u.username, u.first_name, u.last_name
                    FROM memories m 
                    LEFT JOIN users u ON m.user_id = u.user_id 
                    WHERE m.user_id = ?
                    ORDER BY m.created_at ASC
                    LIMIT ?
                ''', (user_id, limit))
                
                memories = []
                for row in cursor.fetchall():
                    memories.append(Memory(
                        id=row['id'],
                        user_id=row['user_id'],
                        username=row['username'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        category=row['category'],
                        title=row['title'],
                        date=row['date'],
                        content=row['content'],
                        media_type=row['media_type'],
                        media_file_id=row['media_file_id'],
                        media_path=row['media_path'],
                        media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                        created_at=row['created_at'],
                        updated_at=row['updated_at'],
                        privacy_type=row['privacy_type'] if 'privacy_type' in row.keys() else None,
                        privacy_views_limit=row['privacy_views_limit'] if 'privacy_views_limit' in row.keys() else None,
                        privacy_question=row['privacy_question'] if 'privacy_question' in row.keys() else None,
                        privacy_answer=row['privacy_answer'] if 'privacy_answer' in row.keys() else None,
                    ))
                
                return memories
        except Exception as e:
            logger.exception(f"Ошибка при получении воспоминаний пользователя: {e}")
            return []

    # === Приватность воспоминаний ===

    def set_memory_privacy_limited(self, memory_id: int, views_limit: int) -> bool:
        """Устанавливает приватность «ограниченное количество просмотров» для воспоминания."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE memories
                    SET privacy_type = ?, privacy_views_limit = ?, privacy_question = NULL, privacy_answer = NULL
                    WHERE id = ?
                    ''',
                    ("limited_views", views_limit, memory_id),
                )
                conn.execute('DELETE FROM memory_views WHERE memory_id = ?', (memory_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при установке ограниченного доступа для воспоминания {memory_id}: {e}")
            return False

    def set_memory_privacy_password(self, memory_id: int, question: str, answer: str) -> bool:
        """Устанавливает приватность «доступ по паролю» для воспоминания."""
        try:
            with self._get_connection() as conn:
                hashed_answer = self._hash_privacy_answer(answer)
                conn.execute(
                    '''
                    UPDATE memories
                    SET privacy_type = ?, privacy_views_limit = NULL, privacy_question = ?, privacy_answer = ?
                    WHERE id = ?
                    ''',
                    ("password", question, hashed_answer, memory_id),
                )
                conn.execute('DELETE FROM memory_views WHERE memory_id = ?', (memory_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при установке доступа по паролю для воспоминания {memory_id}: {e}")
            return False

    def clear_memory_privacy(self, memory_id: int) -> bool:
        """Сбрасывает приватность воспоминания и очищает счётчики просмотров."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    UPDATE memories
                    SET privacy_type = NULL,
                        privacy_views_limit = NULL,
                        privacy_question = NULL,
                        privacy_answer = NULL
                    WHERE id = ?
                    ''',
                    (memory_id,),
                )
                conn.execute('DELETE FROM memory_views WHERE memory_id = ?', (memory_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при сбросе приватности для воспоминания {memory_id}: {e}")
            return False

    def get_memory_privacy(self, memory_id: int) -> Dict[str, Any]:
        """Возвращает информацию о приватности воспоминания."""
        result = {
            "privacy_type": None,
            "privacy_views_limit": None,
            "privacy_question": None,
            "privacy_answer": None,
            "owner_id": None,
        }
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT user_id, privacy_type, privacy_views_limit, privacy_question, privacy_answer
                    FROM memories
                    WHERE id = ?
                    ''',
                    (memory_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return result
                result["owner_id"] = row["user_id"]
                result["privacy_type"] = row["privacy_type"]
                result["privacy_views_limit"] = row["privacy_views_limit"]
                result["privacy_question"] = row["privacy_question"]
                result["privacy_answer"] = row["privacy_answer"]
                return result
        except Exception as e:
            logger.exception(f"Ошибка при получении приватности воспоминания {memory_id}: {e}")
            return result

    def register_memory_view(self, memory_id: int, viewer_id: int) -> Tuple[int, Optional[int], bool]:
        """
        Регистрирует просмотр воспоминания пользователем.
        Возвращает кортеж (used, limit, allowed):
        - used: сколько просмотров уже было у этого пользователя (после текущего просмотра)
        - limit: установленный лимит (или None)
        - allowed: можно ли показать воспоминание (False, если лимит исчерпан)
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT user_id, privacy_type, privacy_views_limit
                    FROM memories
                    WHERE id = ?
                    ''',
                    (memory_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return 0, None, False
                owner_id = row["user_id"]
                privacy_type = row["privacy_type"]
                views_limit = row["privacy_views_limit"]

                # Нет приватности или лимита — ничего не ограничиваем
                if privacy_type != "limited_views" or not views_limit:
                    return 0, None, True

                # На создателя лимит не действует
                if viewer_id == owner_id:
                    return 0, views_limit, True

                cursor = conn.execute(
                    '''
                    SELECT views_count
                    FROM memory_views
                    WHERE memory_id = ? AND user_id = ?
                    ''',
                    (memory_id, viewer_id),
                )
                row = cursor.fetchone()
                current = row["views_count"] if row else 0

                if current >= views_limit:
                    return current, views_limit, False

                new_count = current + 1
                if row:
                    conn.execute(
                        '''
                        UPDATE memory_views
                        SET views_count = ?
                        WHERE memory_id = ? AND user_id = ?
                        ''',
                        (new_count, memory_id, viewer_id),
                    )
                else:
                    conn.execute(
                        '''
                        INSERT INTO memory_views (memory_id, user_id, views_count)
                        VALUES (?, ?, ?)
                        ''',
                        (memory_id, viewer_id, new_count),
                    )
                conn.commit()
                return new_count, views_limit, True
        except Exception as e:
            logger.exception(f"Ошибка при регистрации просмотра воспоминания {memory_id}: {e}")
            return 0, None, True
    def update_memory(self, memory_id: int, **kwargs) -> bool:
        """Обновляет воспоминание"""
        allowed_fields = ['title', 'date', 'content', 'media_type',
                         'media_file_id', 'media_path', 'media_items', 'category']

        # Фильтруем поля
        update_fields = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}

        if 'media_items' in update_fields and isinstance(update_fields['media_items'], list):
            update_fields['media_items'] = json.dumps(update_fields['media_items'][:6], ensure_ascii=False)
        
        if not update_fields:
            return False
        
        # Добавляем время обновления (UTC, формат как у SQLite CURRENT_TIMESTAMP)
        update_fields['updated_at'] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        
        # Формируем SQL запрос
        set_clause = ', '.join([f"{field} = ?" for field in update_fields.keys()])
        values = list(update_fields.values())
        values.append(memory_id)
        
        query = f'UPDATE memories SET {set_clause} WHERE id = ?'
        
        try:
            with self._get_connection() as conn:
                conn.execute(query, values)
                conn.commit()
                logger.info(f"Обновлено воспоминание #{memory_id}")
                return True
        except Exception as e:
            logger.exception(f"Ошибка при обновлении воспоминания: {e}")
            return False
    
    def delete_memory(self, memory_id: int) -> bool:
        """Удаляет воспоминание"""
        try:
            # Сначала получаем информацию о воспоминании
            memory = self.get_memory(memory_id)

            if not memory:
                return False

            media_candidates = self._memory_media_candidates(memory)

            with self._get_connection() as conn:
                # Сначала удаляем дочерние записи (FK → memories), иначе DELETE падает
                conn.execute('DELETE FROM memory_view_sessions WHERE memory_id = ?', (memory_id,))
                conn.execute('DELETE FROM memory_views WHERE memory_id = ?', (memory_id,))
                conn.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
                conn.commit()

            self._delete_media_files_if_unreferenced(media_candidates)

            logger.info(f"Удалено воспоминание #{memory_id}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении воспоминания: {e}")
            return False

    def _media_root_resolved(self) -> str:
        return os.path.abspath(self.media_folder)

    def _normalize_media_path(self, raw_path: Optional[str]) -> Optional[str]:
        p = (raw_path or "").strip()
        if not p:
            return None
        media_root = self._media_root_resolved()
        if os.path.isabs(p):
            full = os.path.abspath(p)
        else:
            full = os.path.abspath(os.path.join(media_root, p))
        try:
            common = os.path.commonpath([media_root, full])
        except Exception:
            return None
        if common != media_root:
            logger.warning("Skip media deletion outside media root: %s", p)
            return None
        return full

    def _memory_media_candidates(self, memory: Memory) -> List[str]:
        candidates: List[str] = []
        if memory.media_path:
            normalized = self._normalize_media_path(memory.media_path)
            if normalized:
                candidates.append(normalized)
        if memory.media_items:
            for item in memory.media_items:
                if not isinstance(item, dict):
                    continue
                path = item.get("path")
                normalized = self._normalize_media_path(path if isinstance(path, str) else None)
                if normalized:
                    candidates.append(normalized)
        dedup: List[str] = []
        seen = set()
        for c in candidates:
            if c in seen:
                continue
            dedup.append(c)
            seen.add(c)
        return dedup

    @staticmethod
    def _legacy_thumb_path(media_path: Optional[str]) -> Optional[str]:
        raw = (media_path or "").strip()
        if not raw:
            return None
        try:
            p = Path(raw)
            return str(p.with_stem(p.stem + "_thumb").with_suffix(".jpg"))
        except Exception:
            return None

    def _repair_legacy_memory_media_rows(self, conn: sqlite3.Connection) -> int:
        repaired = 0
        rows = conn.execute(
            """
            SELECT id, media_type, media_path, media_items
            FROM memories
            WHERE (media_path IS NOT NULL AND media_path != '')
               OR (media_items IS NOT NULL AND media_items != '')
            """
        ).fetchall()
        for row in rows:
            row_id = int(row["id"])
            media_type = str(row["media_type"] or "").strip()
            media_path = str(row["media_path"] or "").strip()
            media_items = self._decode_media_items(row["media_items"] if "media_items" in row.keys() else None)

            normalized_items: List[Dict[str, Any]] = []
            changed = False

            if media_items:
                for item in media_items[:6]:
                    if not isinstance(item, dict):
                        continue
                    item_type = str(item.get("type") or "").strip()
                    item_path = str(item.get("path") or "").strip()
                    if not item_type or not item_path:
                        continue

                    normalized: Dict[str, Any] = {"type": item_type, "path": item_path}
                    file_id = str(item.get("file_id") or "").strip()
                    if file_id:
                        normalized["file_id"] = file_id

                    thumb_path = str(item.get("thumb_path") or "").strip()
                    if not thumb_path and item_type == "photo":
                        candidate_thumb = self._legacy_thumb_path(item_path)
                        if candidate_thumb and os.path.isfile(candidate_thumb):
                            thumb_path = candidate_thumb
                    if thumb_path:
                        normalized["thumb_path"] = thumb_path

                    width = item.get("width")
                    height = item.get("height")
                    if isinstance(width, int) and width > 0:
                        normalized["width"] = width
                    if isinstance(height, int) and height > 0:
                        normalized["height"] = height

                    normalized_items.append(normalized)
                    if normalized != item:
                        changed = True

                if not media_path and normalized_items:
                    media_path = normalized_items[0]["path"]
                    changed = True
            elif media_path:
                item_type = media_type or "photo"
                normalized: Dict[str, Any] = {"type": item_type, "path": media_path}
                thumb_path = self._legacy_thumb_path(media_path)
                if item_type == "photo" and thumb_path and os.path.isfile(thumb_path):
                    normalized["thumb_path"] = thumb_path
                normalized_items = [normalized]
                changed = True

            if not changed:
                continue

            updates: Dict[str, Any] = {}
            if media_path and media_path != str(row["media_path"] or ""):
                updates["media_path"] = media_path
            if normalized_items:
                updates["media_items"] = json.dumps(normalized_items, ensure_ascii=False)

            if not updates:
                continue

            set_clause = ", ".join(f"{field} = ?" for field in updates.keys())
            values = list(updates.values())
            values.append(row_id)
            conn.execute(f"UPDATE memories SET {set_clause} WHERE id = ?", values)
            repaired += 1

        return repaired

    def _is_media_path_referenced_anywhere(self, path: str) -> bool:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT 1 FROM memories WHERE media_path = ? LIMIT 1", (path,)).fetchone()
                if row:
                    return True
                rows = conn.execute(
                    "SELECT media_items FROM memories WHERE media_items IS NOT NULL AND media_items != ''"
                ).fetchall()
                for r in rows:
                    parsed = self._decode_media_items(r["media_items"] if "media_items" in r.keys() else None)
                    if not parsed:
                        continue
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        item_path = self._normalize_media_path(item.get("path") if isinstance(item.get("path"), str) else None)
                        if item_path == path:
                            return True

                for tbl in ("wishes", "scheduled_events"):
                    row = conn.execute(f"SELECT 1 FROM {tbl} WHERE media_path = ? LIMIT 1", (path,)).fetchone()
                    if row:
                        return True

                row = conn.execute("SELECT 1 FROM site_notifications WHERE media_path = ? LIMIT 1", (path,)).fetchone()
                if row:
                    return True
                rows = conn.execute(
                    "SELECT media_items FROM site_notifications WHERE media_items IS NOT NULL AND media_items != ''"
                ).fetchall()
                for r in rows:
                    parsed = self._decode_media_items(r["media_items"] if "media_items" in r.keys() else None)
                    if not parsed:
                        continue
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        item_path = self._normalize_media_path(item.get("path") if isinstance(item.get("path"), str) else None)
                        if item_path == path:
                            return True
        except Exception:
            logger.exception("Failed to check media references for path=%s", path)
            return True
        return False

    def _delete_media_files_if_unreferenced(self, media_paths: List[str]) -> None:
        for path in media_paths:
            if not path:
                continue
            try:
                if self._is_media_path_referenced_anywhere(path):
                    logger.info("Media file kept (still referenced): %s", path)
                    continue
                if not os.path.exists(path):
                    logger.info("Media file already missing, skip delete: %s", path)
                    continue
                os.remove(path)
                logger.info("Removed unreferenced media file: %s", path)
            except Exception:
                logger.exception("Failed to remove media file path=%s", path)

    def _purge_media_folder_unreferenced(self) -> int:
        removed = 0
        media_root = self._media_root_resolved()
        if not os.path.isdir(media_root):
            return 0
        candidates: List[str] = []
        for root, _dirs, files in os.walk(media_root):
            for name in files:
                full = self._normalize_media_path(os.path.join(root, name))
                if full:
                    candidates.append(full)
        for path in candidates:
            try:
                if self._is_media_path_referenced_anywhere(path):
                    continue
                if os.path.exists(path):
                    os.remove(path)
                    removed += 1
            except Exception:
                logger.exception("Failed to purge media file path=%s", path)
        return removed

    # === Специальные пользователи (создатель и компаньон) ===

    def get_creator_id(self) -> int:
        """
        Текущий ID создателя.
        Может быть переопределён через settings (key='creator_id'), иначе берётся из config.
        """
        try:
            val = self.get_setting("creator_id")
            if val is None:
                return config.CREATOR_ID
            return int(str(val).strip())
        except Exception as e:
            logger.debug("Не удалось прочитать creator_id из settings, используем config: %s", e)
            return config.CREATOR_ID

    def get_ksusha_id(self) -> int:
        """
        Текущий ID компаньона (исторически Ксюша).
        Может быть переопределён через settings (key='ksusha_id'), иначе берётся из config.
        """
        try:
            val = self.get_setting("ksusha_id")
            if val is None:
                return config.KSUSHA_ID
            return int(str(val).strip())
        except Exception as e:
            logger.debug("Не удалось прочитать ksusha_id из settings, используем config: %s", e)
            return config.KSUSHA_ID

    # === Методы для работы с парами ===

    def get_couple_by_user(self, user_id: int) -> Optional[Dict]:
        """Возвращает пару, в которой состоит пользователь, или None.
        Предпочитает полные пары (user2_id IS NOT NULL) над незавершёнными.
        """
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    '''SELECT * FROM couples
                       WHERE user1_id = ? OR user2_id = ?
                       ORDER BY (user2_id IS NOT NULL) DESC
                       LIMIT 1''',
                    (user_id, user_id)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении пары для {user_id}: {e}")
            return None

    def get_partner_id(self, user_id: int) -> Optional[int]:
        """Возвращает user_id партнёра из пары, или None."""
        couple = self.get_couple_by_user(user_id)
        if not couple:
            return None
        u1 = couple.get("user1_id")
        u2 = couple.get("user2_id")
        if not u2:
            return None
        return u2 if u1 == user_id else u1

    def get_couple_by_id(self, couple_id: int) -> Optional[Dict]:
        """Возвращает пару по её ID."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT * FROM couples WHERE id = ?', (couple_id,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении пары {couple_id}: {e}")
            return None

    def get_couple_partner(self, user_id: int) -> Optional[int]:
        """Возвращает user_id партнёра или None, если пара не найдена или неполная."""
        couple = self.get_couple_by_user(user_id)
        if not couple:
            return None
        if couple['user1_id'] == user_id:
            return couple['user2_id'] or None
        return couple['user1_id'] or None

    def get_couple_members(self, couple_id: int) -> List[int]:
        """Возвращает список user_id обоих членов пары."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT user1_id, user2_id FROM couples WHERE id = ?', (couple_id,)
                ).fetchone()
                if not row:
                    return []
                return [uid for uid in (row['user1_id'], row['user2_id']) if uid]
        except Exception as e:
            logger.exception(f"Ошибка при получении членов пары {couple_id}: {e}")
            return []

    def get_all_couples(self) -> List[Dict]:
        """Возвращает все пары."""
        try:
            with self._get_connection() as conn:
                rows = conn.execute('SELECT * FROM couples ORDER BY id ASC').fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.exception(f"Ошибка при получении всех пар: {e}")
            return []

    def create_couple(self, user1_id: int, user2_id: Optional[int] = None) -> int:
        """Создаёт новую пару. Возвращает couple_id или -1 при ошибке."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    'INSERT INTO couples (user1_id, user2_id) VALUES (?, ?)',
                    (user1_id, user2_id)
                )
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            logger.exception(f"Ошибка при создании пары: {e}")
            return -1

    def join_couple(self, couple_id: int, user_id: int) -> bool:
        """Присоединяет пользователя на свободное место в паре, перенося данные при необходимости."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT user1_id, user2_id FROM couples WHERE id = ?',
                    (couple_id,)
                ).fetchone()
                if not row:
                    return False
                
                u1 = row['user1_id']
                u2 = row['user2_id']
                
                # Защита от дублирования участников в одной паре
                if u1 == user_id or u2 == user_id:
                    return True
                
                old_id = None
                updated = False
                
                if u1 == -user_id:
                    cur = conn.execute('UPDATE couples SET user1_id = ? WHERE id = ?', (user_id, couple_id))
                    updated = cur.rowcount > 0
                elif u2 is not None and u2 == -user_id:
                    cur = conn.execute('UPDATE couples SET user2_id = ? WHERE id = ?', (user_id, couple_id))
                    updated = cur.rowcount > 0
                elif u2 is None or u2 < 0:
                    if u2 is not None and u2 < 0:
                        old_id = abs(u2)
                    cur = conn.execute(
                        'UPDATE couples SET user2_id = ? WHERE id = ? AND (user2_id IS NULL OR user2_id < 0)',
                        (user_id, couple_id)
                    )
                    updated = cur.rowcount > 0
                elif u1 < 0:
                    old_id = abs(u1)
                    cur = conn.execute(
                        'UPDATE couples SET user1_id = ? WHERE id = ? AND user1_id < 0',
                        (user_id, couple_id)
                    )
                    updated = cur.rowcount > 0
                
                if updated:
                    if old_id is not None and old_id != user_id:
                        logger.info(f"Переносим данные пользователя с old_id={old_id} на new_id={user_id}")
                        
                        # Переносим профиль пользователя с учетом сохранения имени нового аккаунта
                        old_prof = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (old_id,)).fetchone()
                        new_prof = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
                        
                        if old_prof:
                            if new_prof:
                                # У нового аккаунта уже есть профиль (и свое display_name).
                                # Сохраняем имя нового, дополняем описание и статус онбординга (если нужно)
                                desc = new_prof['description'] if new_prof['description'] else old_prof['description']
                                onb = 1 if (new_prof['onboarded'] or old_prof['onboarded']) else 0
                                conn.execute(
                                    "UPDATE user_profiles SET description = ?, onboarded = ? WHERE user_id = ?",
                                    (desc, onb, user_id)
                                )
                            else:
                                # У нового аккаунта профиля нет. 
                                # Копируем данные старого, но ЯВНО обнуляем display_name (чтобы сработал фолбэк на Telegram first_name)
                                conn.execute(
                                    """INSERT INTO user_profiles 
                                       (user_id, display_name, description, onboarded, created_at, updated_at) 
                                       VALUES (?, NULL, ?, ?, ?, ?)""",
                                    (user_id, old_prof['description'], old_prof['onboarded'], old_prof['created_at'], old_prof['updated_at'])
                                )
                            # Удаляем профиль старого аккаунта
                            conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (old_id,))
                        
                        # Переносим настройки, токены и прочее
                        for table in ["user_settings", "favorites", "admins", "user_tokens", "user_login_tokens"]:
                            conn.execute(
                                f"UPDATE OR REPLACE {table} SET user_id = ? WHERE user_id = ?",
                                (user_id, old_id)
                            )
                        
                        for table in ["memories", "wishes", "scheduled_events"]:
                            conn.execute(
                                f"UPDATE {table} SET user_id = ? WHERE user_id = ?",
                                (user_id, old_id)
                            )
                        
                        conn.execute(
                            "UPDATE companion_messages SET visitor_id = REPLACE(visitor_id, ?, ?) WHERE visitor_id LIKE ?",
                            (f"{old_id}_", f"{user_id}_", f"{old_id}_%")
                        )
                        conn.execute(
                            "UPDATE devices SET visitor_id = REPLACE(visitor_id, ?, ?) WHERE visitor_id LIKE ?",
                            (f"{old_id}_", f"{user_id}_", f"{old_id}_%")
                        )
                    
                    conn.commit()
                    return True
                
                return False
        except Exception as e:
            logger.exception(f"Ошибка при присоединении к паре {couple_id}: {e}")
            return False

    def unlink_user_from_couple(self, couple_id: int, user_id: int) -> bool:
        """Отвязывает пользователя от пары (освобождает слот), делая ID отрицательным.
        Применяется ко всем парам, где участвует пользователь, чтобы избежать зависших старых связей."""
        try:
            with self._get_connection() as conn:
                cur1 = conn.execute(
                    'UPDATE couples SET user1_id = -abs(user1_id) WHERE user1_id = ?',
                    (user_id,)
                )
                cur2 = conn.execute(
                    'UPDATE couples SET user2_id = -abs(user2_id) WHERE user2_id = ?',
                    (user_id,)
                )
                conn.commit()
                return cur1.rowcount > 0 or cur2.rowcount > 0
        except Exception as e:
            logger.exception(f"Ошибка при отвязке пользователя {user_id} от пары {couple_id}: {e}")
            return False

    def set_couple_met_date(self, couple_id: int, met_date_str: str) -> bool:
        """Сохраняет дату знакомства пары (YYYY-MM-DD) в couples.met_date."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'UPDATE couples SET met_date = ? WHERE id = ?',
                    (met_date_str, couple_id)
                )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при сохранении met_date для пары {couple_id}: {e}")
            return False

    def get_couple_met_date(self, user_id: int):
        """Возвращает дату знакомства пары как объект date или None."""
        from datetime import date as _date
        couple = self.get_couple_by_user(user_id)
        if not couple:
            return None
        met_date_str = couple.get('met_date')
        if not met_date_str:
            return None
        try:
            parts = met_date_str[:10].split('-')
            return _date(int(parts[0]), int(parts[1]), int(parts[2]))
        except Exception as e:
            logger.debug("Не удалось распарсить met_date=%r: %s", met_date_str, e)
            return None

    def is_in_couple(self, user_id: int) -> bool:
        """Проверяет, состоит ли пользователь в полноценной паре (оба партнёра присоединились)."""
        couple = self.get_couple_by_user(user_id)
        if not couple:
            return False
        u1 = couple.get('user1_id')
        u2 = couple.get('user2_id')
        return bool(u1 and u1 > 0 and u2 and u2 > 0)

    # === Методы для работы с инвайт-кодами ===

    def create_invite_code(self, couple_id: int, creator_id: int) -> str:
        """Создаёт инвайт-код для пары. Возвращает код."""
        import secrets
        code = secrets.token_urlsafe(12)
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'INSERT INTO invite_codes (code, couple_id, creator_id) VALUES (?, ?, ?)',
                    (code, couple_id, creator_id)
                )
                conn.commit()
            return code
        except Exception as e:
            logger.exception(f"Ошибка при создании инвайт-кода: {e}")
            return ""

    def create_transfer_invite(self, couple_id: int, creator_id: int, pre_bound_user_id: Optional[int] = None) -> str:
        """Создаёт инвайт-код для переноса аккаунта."""
        import secrets
        code = secrets.token_urlsafe(12)
        try:
            with self._get_connection() as conn:
                conn.execute(
                    'INSERT INTO invite_codes (code, couple_id, creator_id, pre_bound_user_id) VALUES (?, ?, ?, ?)',
                    (code, couple_id, creator_id, pre_bound_user_id)
                )
                conn.commit()
            return code
        except Exception as e:
            logger.exception(f"Ошибка при создании transfer-инвайт-кода: {e}")
            return ""

    def get_invite_code(self, code: str) -> Optional[Dict]:
        """Возвращает данные инвайт-кода или None."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT * FROM invite_codes WHERE code = ?', (code,)
                ).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении инвайт-кода: {e}")
            return None

    def use_invite_code(self, code: str, user_id: int) -> Optional[int]:
        """
        Использует инвайт-код атомарно: UPDATE ... WHERE used=FALSE возвращает couple_id или None.
        """
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    '''UPDATE invite_codes
                       SET used = TRUE, used_by = ?, used_at = CURRENT_TIMESTAMP
                       WHERE code = ? AND used = FALSE''',
                    (user_id, code)
                )
                conn.commit()
                if cur.rowcount == 0:
                    return None
                row = conn.execute(
                    'SELECT couple_id FROM invite_codes WHERE code = ?', (code,)
                ).fetchone()
                return row['couple_id'] if row else None
        except Exception as e:
            logger.exception(f"Ошибка при использовании инвайт-кода: {e}")
            return None

    def get_active_invite_for_couple(self, couple_id: int) -> Optional[str]:
        """Возвращает активный (не использованный) инвайт-код для пары или None."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT code FROM invite_codes WHERE couple_id = ? AND used = FALSE ORDER BY created_at DESC LIMIT 1',
                    (couple_id,)
                ).fetchone()
                return row['code'] if row else None
        except Exception as e:
            logger.exception(f"Ошибка при получении инвайт-кода для пары {couple_id}: {e}")
            return None

    def is_event_notified_for_user(self, event_id: int, user_id: int) -> bool:
        """Проверяет, получил ли конкретный пользователь уведомление о событии для его последней прошедшей/текущей годовщины."""
        try:
            # 1. Получаем само событие из БД
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT event_datetime, user_id, is_recurring FROM scheduled_events WHERE id = ?',
                    (event_id,)
                ).fetchone()
            if not row:
                return True
            
            event_datetime_str = row[0]
            is_recurring = row[2] if len(row) > 2 else 0
            
            # Для разового события достаточно наличия любой записи в event_notifications
            if not is_recurring:
                with self._get_connection() as conn:
                    notif_row = conn.execute(
                        'SELECT notified_at FROM event_notifications WHERE event_id = ? AND user_id = ?',
                        (event_id, user_id)
                    ).fetchone()
                if notif_row:
                    return True
            
            # 2. Парсим исходную дату события
            is_full_dt = len(event_datetime_str) >= 19
            if is_full_dt:
                event_dt = datetime.strptime(event_datetime_str[:19], '%Y-%m-%d %H:%M:%S')
            else:
                event_dt = datetime.strptime(event_datetime_str[:10], '%Y-%m-%d')
                
            # 3. Вычисляем смещение часового пояса конкретного пользователя
            from utils import _tz_offset
            user_offset = 3
            if user_id:
                try:
                    tz_id = self.get_user_setting(user_id, "timezone")
                    user_offset = _tz_offset(tz_id, 3)
                except Exception:
                    pass
            
            # 4. Получаем текущее время в поясе этого пользователя
            now_user = datetime.now(timezone.utc) + timedelta(hours=user_offset)
            now_user = now_user.replace(tzinfo=None)
            
            # 5. Если исходное событие в будущем, уведомлять ещё рано
            if event_dt > now_user:
                return True
                
            # 6. Находим последнюю прошедшую или текущую годовщину (latest_occurrence) в часовом поясе пользователя
            y = now_user.year
            latest_occurrence = None
            while True:
                try:
                    candidate = event_dt.replace(year=y)
                except ValueError:
                    candidate = event_dt.replace(year=y, day=28)
                if candidate <= now_user:
                    latest_occurrence = candidate
                    break
                y -= 1
                
            if latest_occurrence is None:
                return True
                
            # 7. Переводим latest_occurrence в UTC для точного сравнения с notified_at в БД
            latest_occurrence_utc = latest_occurrence - timedelta(hours=user_offset)
            latest_occurrence_utc = latest_occurrence_utc.replace(tzinfo=timezone.utc)
            
            # 8. Проверяем наличие записи об уведомлении в БД
            with self._get_connection() as conn:
                notif_row = conn.execute(
                    'SELECT notified_at FROM event_notifications WHERE event_id = ? AND user_id = ?',
                    (event_id, user_id)
                ).fetchone()
                
            if notif_row:
                val = notif_row[0]
                if isinstance(val, str):
                    try:
                        # SQLite CURRENT_TIMESTAMP возвращает строку в UTC
                        notified_at_utc = datetime.strptime(val[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    except ValueError:
                        # На всякий случай fallback
                        return True
                elif isinstance(val, datetime):
                    notified_at_utc = val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
                else:
                    return True
                
                # Если дата последнего уведомления >= дате последней годовщины в UTC
                return notified_at_utc >= latest_occurrence_utc
            else:
                # 9. Если уведомления ещё не было, но последняя годовщина была в прошлых годах,
                # помечаем её как уведомлённую без отправки, чтобы исключить false-positive при создании старых дат.
                # Храним точное UTC-время пропущенного события, чтобы не перекрывать реальные срабатывания текущего года!
                if latest_occurrence.year < now_user.year:
                    self.mark_event_notified_for_user(event_id, user_id, latest_occurrence_utc)
                    return True
                return False
                
        except Exception as e:
            logger.exception("Ошибка в is_event_notified_for_user для event_id=%s user_id=%s: %s", event_id, user_id, e)
            return False

    def mark_event_notified_for_user(self, event_id: int, user_id: int, notified_at: Optional[datetime] = None) -> bool:
        """Помечает, что пользователь получил уведомление о событии."""
        try:
            val = notified_at.strftime('%Y-%m-%d %H:%M:%S') if notified_at else None
            with self._get_connection() as conn:
                if val:
                    conn.execute(
                        'INSERT OR REPLACE INTO event_notifications (event_id, user_id, notified_at) VALUES (?, ?, ?)',
                        (event_id, user_id, val)
                    )
                else:
                    conn.execute(
                        'INSERT OR IGNORE INTO event_notifications (event_id, user_id) VALUES (?, ?)',
                        (event_id, user_id)
                    )
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при отметке уведомления о событии: {e}")
            return False

    def get_scheduled_events_unnotified_for_couples(self) -> List[tuple]:
        """
        Возвращает список (event, couple_members) для событий, которые не уведомили всех членов пары.
        Возвращает события, не имеющие уведомлений (с учётом новой таблицы event_notifications).
        """
        events_map = {}
        try:
            couples = self.get_all_couples()
            for couple in couples:
                members = [uid for uid in (couple.get('user1_id'), couple.get('user2_id')) if uid]
                if not members:
                    continue
                events = self.get_scheduled_events_for_users(members)
                for event in events:
                    pending_users = [
                        uid for uid in members
                        if not self.is_event_notified_for_user(event.id, uid)
                    ]
                    if pending_users:
                        if event.id not in events_map:
                            events_map[event.id] = (event, set(pending_users))
                        else:
                            events_map[event.id][1].update(pending_users)
        except Exception as e:
            logger.exception(f"Ошибка при получении неуведомлённых событий: {e}")
        return [(event, list(users)) for event, users in events_map.values()]

    def get_scheduled_events_for_users(self, user_ids: List[int]) -> List['ScheduledEvent']:
        """Возвращает все события для пользователей из списка, отсортированные по ближайшему наступлению."""
        if not user_ids:
            return []
        try:
            with self._get_connection() as conn:
                placeholders = ','.join('?' * len(user_ids))
                cursor = conn.execute(
                    f'SELECT * FROM scheduled_events WHERE user_id IN ({placeholders})',
                    user_ids
                )
                events = [_row_to_scheduled_event(row) for row in cursor.fetchall()]
            events.sort(key=_get_event_sort_key)
            return events
        except Exception as e:
            logger.exception(f"Ошибка при получении событий для пользователей: {e}")
            return []

    # === Статистические методы ===
    
    def get_category_stats(self) -> Dict[str, int]:
        """Получает статистику по категориям"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT category, COUNT(*) as count 
                    FROM memories 
                    GROUP BY category
                    ORDER BY count DESC
                ''')
                
                stats = {}
                for row in cursor.fetchall():
                    stats[row['category']] = row['count']
                
                return stats
        except Exception as e:
            logger.exception(f"Ошибка при получении статистики по категориям: {e}")
            return {}
    
    def get_media_stats(self) -> Dict[str, int]:
        """Статистика по типам медиа: memories + scheduled_events + wishes (фото, видео, кружки и т.д.)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT 
                        SUM(CASE WHEN media_type = 'photo' THEN 1 ELSE 0 END) as photo_count,
                        SUM(CASE WHEN media_type = 'video' THEN 1 ELSE 0 END) as video_count,
                        SUM(CASE WHEN media_type = 'video_note' THEN 1 ELSE 0 END) as video_note_count,
                        SUM(CASE WHEN media_type = 'audio' THEN 1 ELSE 0 END) as audio_count,
                        SUM(CASE WHEN media_type = 'voice' THEN 1 ELSE 0 END) as voice_count,
                        SUM(CASE WHEN media_type = 'document' THEN 1 ELSE 0 END) as document_count,
                        SUM(CASE WHEN media_type IS NULL THEN 1 ELSE 0 END) as text_only_count
                    FROM (
                        SELECT media_type FROM memories
                        UNION ALL
                        SELECT media_type FROM scheduled_events
                        UNION ALL
                        SELECT media_type FROM wishes
                    )
                ''')
                row = cursor.fetchone()
                return dict(row) if row else {
                    'photo_count': 0, 'video_count': 0, 'video_note_count': 0, 'audio_count': 0,
                    'voice_count': 0, 'document_count': 0, 'text_only_count': 0
                }
        except Exception as e:
            logger.exception(f"Ошибка при получении статистики медиа: {e}")
            return {
                'photo_count': 0, 'video_count': 0, 'video_note_count': 0, 'audio_count': 0,
                'voice_count': 0, 'document_count': 0, 'text_only_count': 0
            }
    
    def get_days_active(self) -> int:
        """Получает количество дней активности бота"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT 
                        CASE 
                            WHEN COUNT(*) = 0 THEN 0
                            ELSE CAST(JULIANDAY('now') - JULIANDAY(MIN(created_at)) + 1 AS INTEGER)
                        END as days_active
                    FROM memories
                ''')
                row = cursor.fetchone()
                return int(row['days_active']) if row else 0
        except Exception as e:
            logger.exception(f"Ошибка при получении дней активности: {e}")
            return 0
    
    def get_recent_activity(self, days: int = 7) -> Dict[str, int]:
        """Получает активность за последние N дней"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT 
                        DATE(created_at) as date,
                        COUNT(*) as count
                    FROM memories 
                    WHERE created_at >= DATE('now', ?)
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                ''', (f'-{days} days',))
                
                activity = {}
                for row in cursor.fetchall():
                    activity[row['date']] = row['count']
                
                return activity
        except Exception as e:
            logger.exception(f"Ошибка при получении активности: {e}")
            return {}
    
    def get_total_stats(self) -> Dict[str, Any]:
        """Получает общую статистику с дополнительными данными"""
        try:
            with self._get_connection() as conn:
                # Основная статистика
                cursor = conn.execute('''
                    SELECT 
                        COUNT(*) as total_memories,
                        COUNT(DISTINCT user_id) as unique_users,
                        COUNT(DISTINCT category) as categories_used
                    FROM memories
                ''')
                
                row = cursor.fetchone()
                base_stats = dict(row) if row else {
                    'total_memories': 0,
                    'unique_users': 0,
                    'categories_used': 0
                }
                
                # Добавляем статистику медиа
                media_stats = self.get_media_stats()
                base_stats.update(media_stats)
                
                # Добавляем дни активности
                base_stats['days_active'] = self.get_days_active()
                
                # Добавляем среднее в день
                days_active = base_stats['days_active']
                if days_active > 0:
                    base_stats['avg_per_day'] = base_stats['total_memories'] / days_active
                else:
                    base_stats['avg_per_day'] = 0
                
                # Добавляем статистику по категориям
                category_stats = self.get_category_stats()
                base_stats['category_stats'] = category_stats
                
                # Добавляем активность за неделю
                recent_activity = self.get_recent_activity(7)
                base_stats['recent_activity'] = recent_activity
                base_stats['last_week_count'] = sum(recent_activity.values())
                
                # События на дату и желания
                try:
                    c = conn.execute('SELECT COUNT(*) FROM scheduled_events')
                    base_stats['scheduled_events_count'] = c.fetchone()[0] or 0
                except Exception as e:
                    logger.debug("Не удалось посчитать scheduled_events_count: %s", e)
                    base_stats['scheduled_events_count'] = 0
                try:
                    c = conn.execute('SELECT COUNT(*) FROM wishes')
                    base_stats['wishes_count'] = c.fetchone()[0] or 0
                except Exception as e:
                    logger.debug("Не удалось посчитать wishes_count: %s", e)
                    base_stats['wishes_count'] = 0
                
                return base_stats
        except Exception as e:
            logger.exception(f"Ошибка при получении общей статистики: {e}")
            return {
                'total_memories': 0,
                'unique_users': 0,
                'categories_used': 0,
                'photo_count': 0,
                'video_count': 0,
                'audio_count': 0,
                'voice_count': 0,
                'document_count': 0,
                'text_only_count': 0,
                'video_note_count': 0,
                'days_active': 0,
                'avg_per_day': 0,
                'category_stats': {},
                'recent_activity': {},
                'scheduled_events_count': 0,
                'wishes_count': 0,
                'last_week_count': 0
            }

    def add_site_visit(
        self,
        timezone_id: Optional[str],
        ip: str,
        ua_pretty: Optional[str],
        visitor_id: Optional[str] = None,
    ) -> None:
        """Регистрирует визит на сайт (для персональной статистики по visitor_id)."""
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO site_visits (visited_at_utc, timezone_id, ip, ua_pretty, visitor_id)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (utc_now, timezone_id or None, ip or None, ua_pretty or None, visitor_id or None),
                )
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка при регистрации визита на сайт: {e}")

    def get_site_visits_summary(self, visitor_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Краткая сводка по визитам на сайт (только для visitor_id; без visitor_id — пустая статистика).
        - total: всего визитов
        - first_utc, last_utc: первая и последняя дата (строки в UTC)
        - days_span: количество дней между первым и последним визитом (минимум 1)
        """
        result: Dict[str, Any] = {
            "total": 0,
            "first_utc": None,
            "last_utc": None,
            "days_span": 0,
        }
        if not visitor_id:
            return result
        base = visitor_id.split("_")[0] if "_" in visitor_id else visitor_id
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT
                        COUNT(*) AS total,
                        MIN(visited_at_utc) AS first_utc,
                        MAX(visited_at_utc) AS last_utc
                    FROM site_visits
                    WHERE visitor_id = ? OR visitor_id LIKE ?
                    ''',
                    (base, f"{base}_%"),
                )
                row = cursor.fetchone()
                if not row:
                    return result
                total = row["total"] or 0
                first_utc = row["first_utc"]
                last_utc = row["last_utc"]
                result["total"] = total
                result["first_utc"] = first_utc
                result["last_utc"] = last_utc
                if first_utc and last_utc:
                    try:
                        d1 = datetime.strptime(first_utc, "%Y-%m-%d %H:%M:%S")
                        d2 = datetime.strptime(last_utc, "%Y-%m-%d %H:%M:%S")
                        days = (d2.date() - d1.date()).days + 1
                        if days < 1:
                            days = 1
                        result["days_span"] = days
                    except Exception:
                        result["days_span"] = 0
                return result
        except Exception as e:
            logger.exception(f"Ошибка при получении статистики визитов сайта: {e}")
            return result

    def get_last_site_visit_info(self, visitor_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Возвращает информацию о самом последнем визите (для отображения устройства). Без visitor_id — None."""
        if not visitor_id:
            return None
        base = visitor_id.split("_")[0] if "_" in visitor_id else visitor_id
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT visited_at_utc, timezone_id, ip, ua_pretty, visitor_id
                    FROM site_visits
                    WHERE visitor_id = ? OR visitor_id LIKE ?
                    ORDER BY visited_at_utc DESC
                    LIMIT 1
                    ''',
                    (base, f"{base}_%"),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                names = [d[0] for d in cursor.description]
                return dict(zip(names, row))
        except Exception as e:
            logger.exception(f"Ошибка при получении последнего визита сайта: {e}")
            return None

    def get_last_site_visit_any(self) -> Optional[Dict[str, Any]]:
        """Последний визит на сайт (любой посетитель), с visitor_id — для кнопки «Обновить»."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT id, visited_at_utc, timezone_id, ip, ua_pretty, visitor_id
                    FROM site_visits
                    ORDER BY id DESC
                    LIMIT 1
                    '''
                )
                row = cursor.fetchone()
                if not row:
                    return None
                names = [d[0] for d in cursor.description]
                return dict(zip(names, row))
        except Exception as e:
            logger.exception("Ошибка при получении последнего визита: %s", e)
            return None

    def get_device_by_visitor_id(self, visitor_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Устройство по visitor_id для формирования сообщения «Обновить»."""
        if not visitor_id:
            return None
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT * FROM devices WHERE visitor_id = ?", (visitor_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                names = [d[0] for d in cursor.description]
                return dict(zip(names, row))
        except Exception as e:
            logger.exception("Ошибка при получении устройства по visitor_id: %s", e)
            return None

    def add_visitor_site_time(self, visitor_id: Optional[str], seconds: int = 5) -> None:
        """Добавляет секунды к суммарному времени на сайте для visitor_id (heartbeat раз в 5 сек)."""
        if not visitor_id or seconds <= 0:
            return
        try:
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO visitor_site_time (visitor_id, total_seconds)
                    VALUES (?, ?)
                    ON CONFLICT(visitor_id) DO UPDATE SET total_seconds = total_seconds + ?
                    ''',
                    (visitor_id, seconds, seconds),
                )
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка при обновлении времени на сайте: %s", e)

    def get_visitor_total_site_seconds(self, visitor_id: Optional[str]) -> int:
        """Возвращает суммарное время на сайте (секунды) для visitor_id. Без visitor_id — 0."""
        if not visitor_id:
            return 0
        base = visitor_id.split("_")[0] if "_" in visitor_id else visitor_id
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT SUM(total_seconds) FROM visitor_site_time WHERE visitor_id = ? OR visitor_id LIKE ?",
                    (base, f"{base}_%"),
                )
                row = cursor.fetchone()
                return int(row[0] or 0) if row and row[0] is not None else 0
        except Exception as e:
            logger.exception("Ошибка при получении времени на сайте: %s", e)
            return 0

    # === Методы для истории ИИ‑компаньона (чат на сайте) ===

    def add_companion_message(
        self,
        visitor_id: Optional[str],
        role: str,
        site_role: Optional[str],
        message: str,
    ) -> Optional[int]:
        """Сохраняет одно сообщение диалога ИИ‑компаньона. Возвращает id новой записи."""
        if visitor_id and "_" in visitor_id:
            visitor_id = visitor_id.split("_")[0]
        role = (role or "").strip()
        message = (message or "").strip()
        if not role or not message:
            return None
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    INSERT INTO companion_messages (visitor_id, role, site_role, message, created_at_utc)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (visitor_id or None, role, site_role or None, message, utc_now),
                )
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.exception("Ошибка при сохранении сообщения ИИ‑компаньона: %s", e)
            return None

    def clear_companion_history(self, visitor_id: str) -> int:
        """Удаляет всю историю чата с ИИ для visitor_id. Возвращает кол-во удалённых строк."""
        if visitor_id and "_" in visitor_id:
            visitor_id = visitor_id.split("_")[0]
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM companion_messages WHERE visitor_id = ?",
                    (visitor_id,)
                )
                conn.commit()
                return cur.rowcount
        except Exception as e:
            logger.exception("Ошибка clear_companion_history: %s", e)
            return 0

    def get_companion_history(
        self,
        visitor_id: Optional[str],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Возвращает полную историю переписки ИИ‑компаньона для visitor_id.

        В БД хранится вся история, но при необходимости можно ограничить количество
        возвращаемых записей параметром limit (по умолчанию — без ограничения).
        """
        if not visitor_id:
            return []
        if visitor_id and "_" in visitor_id:
            visitor_id = visitor_id.split("_")[0]
        try:
            with self._get_connection() as conn:
                # Добавляем колонки если их нет (на случай старой БД)
                existing = [r[1] for r in conn.execute("PRAGMA table_info(companion_messages)").fetchall()]
                if 'reaction' not in existing:
                    conn.execute("ALTER TABLE companion_messages ADD COLUMN reaction TEXT")
                if 'is_pinned' not in existing:
                    conn.execute("ALTER TABLE companion_messages ADD COLUMN is_pinned INTEGER DEFAULT 0")
                conn.commit()

                sql = '''
                    SELECT id, visitor_id, role, site_role, message, created_at_utc,
                           reaction, is_pinned
                    FROM companion_messages
                    WHERE visitor_id = ?
                    ORDER BY id ASC
                '''
                params: list[Any] = [visitor_id]
                if limit is not None and limit > 0:
                    sql = '''
                        SELECT id, visitor_id, role, site_role, message, created_at_utc,
                               reaction, is_pinned
                        FROM (
                            SELECT id, visitor_id, role, site_role, message, created_at_utc,
                                   reaction, is_pinned
                            FROM companion_messages
                            WHERE visitor_id = ?
                            ORDER BY id DESC
                            LIMIT ?
                        )
                        ORDER BY id ASC
                    '''
                    params = [visitor_id, limit]
                cursor = conn.execute(sql, params)
                rows = cursor.fetchall()
                if not rows:
                    return []
                names = [d[0] for d in cursor.description]
                result = []
                for row in rows:
                    d = dict(zip(names, row))
                    d['content'] = d.pop('message', '')
                    d['time'] = d.get('created_at_utc', '')
                    result.append(d)
                return result
        except Exception as e:
            logger.exception("Ошибка при получении истории ИИ‑компаньона: %s", e)
            return []


    # === Методы лимита сообщений ИИ-компаньона ===

    def get_user_tier(self, user_key: str) -> str:
        """Возвращает тарифный план пользователя ('free', 'plus', 'premium')."""
        if not user_key:
            return "free"
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT tier, expires_at FROM user_subscription_tier WHERE user_key = ?",
                    (user_key,),
                ).fetchone()
            if not row:
                return "free"
            tier, expires_at = row[0], row[1]
            if expires_at:
                exp = datetime.fromisoformat(expires_at).replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    return "free"
            return tier or "free"
        except Exception as e:
            logger.exception("Ошибка при получении тарифа пользователя: %s", e)
            return "free"

    def check_and_record_ai_usage(self, user_key: str, limit: Optional[int]) -> dict:
        """
        Атомарная проверка лимита и запись использования (lazy reset).

        Сначала проверяет, не истекло ли окно. Если истекло — начинает новый период.
        Если окна нет — создаёт первое.
        Если лимит превышен — возвращает allowed=False, не изменяя счётчик.

        Возвращает dict:
          {"allowed": True,  "used": 3,  "limit": 50, "window_start": "..."}
          {"allowed": False, "used": 50, "limit": 50, "resets_at": "..."}
        """
        from constants import COMPANION_WINDOW_HOURS
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
        window_seconds = COMPANION_WINDOW_HOURS * 3600

        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT window_start, message_count FROM ai_usage_window WHERE user_key = ?",
                    (user_key,),
                ).fetchone()

                if row:
                    window_start = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
                    count = int(row[1])
                    age_seconds = (now_utc - window_start).total_seconds()

                    if age_seconds >= window_seconds:
                        conn.execute(
                            "UPDATE ai_usage_window SET window_start = ?, message_count = 1 WHERE user_key = ?",
                            (now_str, user_key),
                        )
                        conn.commit()
                        return {"allowed": True, "used": 1, "limit": limit, "window_start": now_str}

                    if limit is not None and count >= limit:
                        resets_at = (window_start + timedelta(seconds=window_seconds))
                        return {
                            "allowed": False,
                            "used": count,
                            "limit": limit,
                            "resets_at": resets_at.strftime("%Y-%m-%dT%H:%M:%S"),
                        }

                    conn.execute(
                        "UPDATE ai_usage_window SET message_count = message_count + 1 WHERE user_key = ?",
                        (user_key,),
                    )
                    conn.commit()
                    return {"allowed": True, "used": count + 1, "limit": limit, "window_start": row[0]}

                conn.execute(
                    "INSERT INTO ai_usage_window (user_key, window_start, message_count) VALUES (?, ?, 1)",
                    (user_key, now_str),
                )
                conn.commit()
                return {"allowed": True, "used": 1, "limit": limit, "window_start": now_str}

        except Exception as e:
            logger.exception("Ошибка check_and_record_ai_usage для key=%s: %s", user_key, e)
            return {"allowed": True, "used": 0, "limit": limit, "window_start": now_str}

    def decrement_ai_usage(self, user_key: str) -> None:
        """Откат счётчика при ошибке ИИ. Не уходит ниже 0."""
        if not user_key:
            return
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE ai_usage_window SET message_count = MAX(0, message_count - 1) WHERE user_key = ?",
                    (user_key,),
                )
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка decrement_ai_usage для key=%s: %s", user_key, e)

    def get_ai_usage_status(self, user_key: str, limit: Optional[int]) -> dict:
        """
        Только чтение, без изменений состояния.

        Возвращает текущее состояние лимита:
          {
            "used": 33,
            "limit": 50,
            "remaining": 17,
            "window_start": "2026-05-31T10:00:00",
            "resets_at": "2026-06-01T10:00:00",
            "window_active": True
          }
        """
        from constants import COMPANION_WINDOW_HOURS
        now_utc = datetime.now(timezone.utc)
        window_seconds = COMPANION_WINDOW_HOURS * 3600
        no_window = {
            "used": 0,
            "limit": limit,
            "remaining": limit,
            "window_start": None,
            "resets_at": None,
            "window_active": False,
        }
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT window_start, message_count FROM ai_usage_window WHERE user_key = ?",
                    (user_key,),
                ).fetchone()

            if not row:
                return no_window

            window_start = datetime.fromisoformat(row[0]).replace(tzinfo=timezone.utc)
            count = int(row[1])
            age_seconds = (now_utc - window_start).total_seconds()

            if age_seconds >= window_seconds:
                return no_window

            resets_at = (window_start + timedelta(seconds=window_seconds))
            remaining = None if limit is None else max(0, limit - count)
            return {
                "used": count,
                "limit": limit,
                "remaining": remaining,
                "window_start": row[0],
                "resets_at": resets_at.strftime("%Y-%m-%dT%H:%M:%S"),
                "window_active": True,
            }
        except Exception as e:
            logger.exception("Ошибка get_ai_usage_status для key=%s: %s", user_key, e)
            return no_window

    # ── Site notifications ───────────────────────────────────────────────────


    def add_site_notification(self, text: str, media_path: str = None, media_type: str = None,
                               media_items: list = None, couple_id: Optional[int] = None) -> int:
        """Создаёт уведомление от создателя для Ксюши. Возвращает id.
        media_items — список {'path':..., 'type':...} для нескольких медиа."""
        import json as _json
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Если передан список — сериализуем в JSON
        media_items_json = _json.dumps(media_items, ensure_ascii=False) if media_items else None
        # Для обратной совместимости first_media
        first_path = (media_items[0]['path'] if media_items else media_path) or None
        first_type = (media_items[0]['type'] if media_items else media_type) or None
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    """INSERT INTO site_notifications
                       (couple_id, text, media_path, media_type, media_items, created_at_utc)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (couple_id, text, first_path, first_type, media_items_json, utc_now),
                )
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            logger.exception("Ошибка add_site_notification: %s", e)
            return 0

    def get_pending_site_notification(self, couple_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Возвращает первое непоказанное уведомление."""
        import json as _json
        try:
            with self._get_connection() as conn:
                if couple_id is not None:
                    row = conn.execute(
                        """SELECT id, couple_id, text, media_path, media_type, media_items, created_at_utc
                           FROM site_notifications
                           WHERE is_delivered = 0 AND couple_id = ?
                           ORDER BY id ASC LIMIT 1""",
                        (couple_id,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """SELECT id, couple_id, text, media_path, media_type, media_items, created_at_utc
                           FROM site_notifications
                           WHERE is_delivered = 0
                           ORDER BY id ASC LIMIT 1"""
                    ).fetchone()
                if not row:
                    return None
                media_items = None
                if row[5]:
                    try: media_items = _json.loads(row[5])
                    except Exception: pass
                return {"id": row[0], "couple_id": row[1], "text": row[2], "media_path": row[3],
                        "media_type": row[4], "media_items": media_items,
                        "created_at_utc": row[6]}
        except Exception as e:
            logger.exception("Ошибка get_pending_site_notification: %s", e)
            return None

    def mark_notification_delivered(self, notif_id: int, couple_id: Optional[int] = None) -> bool:
        """Помечает уведомление как доставленное."""
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                if couple_id is not None:
                    cur = conn.execute(
                        "UPDATE site_notifications SET is_delivered=1, delivered_at_utc=? WHERE id=? AND couple_id=?",
                        (utc_now, notif_id, couple_id),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE site_notifications SET is_delivered=1, delivered_at_utc=? WHERE id=?",
                        (utc_now, notif_id),
                    )
                conn.commit()
                return bool(cur.rowcount > 0)
        except Exception as e:
            logger.exception("Ошибка mark_notification_delivered: %s", e)
        return False

    # ── Bot last active ──────────────────────────────────────────────────────

    def update_bot_last_active(self, user_id: int, action: str = "") -> None:
        """Обновляет время последней активности пользователя в боте."""
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                conn.execute(
                    """INSERT INTO bot_last_active (user_id, last_active_utc, action)
                       VALUES (?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET
                           last_active_utc = excluded.last_active_utc,
                           action = excluded.action""",
                    (user_id, utc_now, (action or "")[:120]),
                )
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка update_bot_last_active: %s", e)

    def get_bot_last_active(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Возвращает последнюю активность пользователя в боте."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT user_id, last_active_utc, action FROM bot_last_active WHERE user_id = ?",
                    (user_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return {"user_id": row[0], "last_active_utc": row[1], "action": row[2]}
        except Exception as e:
            logger.exception("Ошибка get_bot_last_active: %s", e)
            return None

    def get_user_last_seen(self, user_id: int) -> Optional[str]:
        """Возвращает UTC-строку последнего онлайна пользователя (максимум из бота и сайта)."""
        try:
            bot_ts = None
            site_ts = None
            with self._get_connection() as conn:
                row_bot = conn.execute(
                    "SELECT last_active_utc FROM bot_last_active WHERE user_id = ?",
                    (user_id,)
                ).fetchone()
                if row_bot:
                    bot_ts = row_bot[0]
                special_ids = [str(user_id)]
                if user_id == self.get_creator_id():
                    special_ids.append("creator")
                if user_id == self.get_ksusha_id():
                    special_ids.extend(["ksyusha", "ksusha", "partner"])
                placeholders = ", ".join(["?"] * len(special_ids))
                query = f"""
                    SELECT MAX(last_seen_utc) FROM devices
                    WHERE visitor_id LIKE ? OR visitor_id IN ({placeholders})
                """
                params = [f"{user_id}_%"] + special_ids
                row_site = conn.execute(query, params).fetchone()
                if row_site and row_site[0]:
                    site_ts = row_site[0]
            if bot_ts and site_ts:
                return max(bot_ts, site_ts)
            return bot_ts or site_ts
        except Exception as e:
            logger.exception("Ошибка get_user_last_seen: %s", e)
            return None

    # ── Admin panel data ─────────────────────────────────────────────────────

    def get_admin_stats(self) -> Dict[str, Any]:
        """Сводная статистика для админ-панели."""
        try:
            with self._get_connection() as conn:
                total_devices = conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
                cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
                online_count = conn.execute(
                    "SELECT COUNT(*) FROM devices WHERE last_seen_utc >= ?", (cutoff,)
                ).fetchone()[0]
                ai_messages = conn.execute("SELECT COUNT(*) FROM companion_messages").fetchone()[0]
                memories_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
                site_visits = conn.execute("SELECT COUNT(*) FROM site_visits").fetchone()[0]
                return {
                    "total_devices": total_devices,
                    "online_count": online_count,
                    "ai_messages": ai_messages,
                    "memories": memories_count,
                    "site_visits": site_visits,
                }
        except Exception as e:
            logger.exception("Ошибка get_admin_stats: %s", e)
            return {}

    def get_ksusha_info(self, ksusha_visitor_id: str = "ksyusha", ksusha_user_id: int = 0) -> Dict[str, Any]:
        """Информация о Ксюше для админ-панели: последний онлайн = max(сайт, бот).
        ksusha_user_id игнорируется — всегда используем get_ksusha_id() для актуального ID."""
        try:
            from datetime import timedelta
            # Всегда берём актуальный ID Ксюши из БД (может быть переопределён через settings)
            real_ksusha_id = self.get_ksusha_id()
            with self._get_connection() as conn:
                dev = None
                row = conn.execute(
                    """SELECT last_seen_utc, ua_pretty, visit_count FROM devices
                       WHERE visitor_id IN ('ksyusha', 'ksusha', 'partner', ?)
                          OR visitor_id LIKE ?
                       ORDER BY last_seen_utc DESC LIMIT 1""",
                    (str(real_ksusha_id), f"{real_ksusha_id}_%")
                ).fetchone()
                if row:
                    dev = {"last_seen_utc": row[0], "ua_pretty": row[1], "visit_count": row[2]}

                bot_row = None
                if real_ksusha_id:
                    bot_row = conn.execute(
                        "SELECT last_active_utc, action FROM bot_last_active WHERE user_id = ?",
                        (real_ksusha_id,)
                    ).fetchone()

                site_ts = dev["last_seen_utc"] if dev else None
                bot_ts = bot_row[0] if bot_row else None
                bot_action = bot_row[1] if bot_row else None

                if site_ts and bot_ts:
                    last_online = max(site_ts, bot_ts)
                    last_source = "бот" if bot_ts > site_ts else "сайт"
                elif bot_ts:
                    last_online = bot_ts
                    last_source = "бот"
                elif site_ts:
                    last_online = site_ts
                    last_source = "сайт"
                else:
                    last_online = None
                    last_source = None

                last_cat_row = conn.execute(
                    """SELECT section_id, opened_at_utc FROM category_opens
                       WHERE visitor_id IN ('ksyusha', 'ksusha', 'partner', ?)
                          OR visitor_id LIKE ?
                       ORDER BY opened_at_utc DESC LIMIT 1""",
                    (str(real_ksusha_id), f"{real_ksusha_id}_%")
                ).fetchone()

                ai_row = conn.execute(
                    """SELECT created_at_utc FROM companion_messages
                       WHERE visitor_id IN ('ksyusha', 'ksusha', 'partner', ?)
                          OR visitor_id LIKE ?
                       ORDER BY id DESC LIMIT 1""",
                    (str(real_ksusha_id), f"{real_ksusha_id}_%")
                ).fetchone()

                visit_count = dev["visit_count"] if dev else 0

                return {
                    "last_online_utc": last_online,
                    "last_online_source": last_source,
                    "site_last_seen_utc": site_ts,
                    "bot_last_seen_utc": bot_ts,
                    "bot_last_action": bot_action,
                    "last_category": last_cat_row[0] if last_cat_row else None,
                    "last_category_utc": last_cat_row[1] if last_cat_row else None,
                    "last_ai_utc": ai_row[0] if ai_row else None,
                    "visit_count": visit_count,
                }
        except Exception as e:
            logger.exception("Ошибка get_ksusha_info: %s", e)
            return {}

    def get_partner_info(self, partner_visitor_id: str = "partner", partner_user_id: int = 0) -> Dict[str, Any]:
        """
        Нейтральный алиас для legacy get_ksusha_info.
        Для обратной совместимости partner_visitor_id="partner" маппится в legacy visitor_id "ksyusha".
        """
        legacy_vid = "ksyusha" if partner_visitor_id == "partner" else partner_visitor_id
        return self.get_ksusha_info(ksusha_visitor_id=legacy_vid, ksusha_user_id=partner_user_id)

    def add_memory_view_session_start(self, memory_id: int, visitor_id: Optional[str] = None) -> int:
        """Создаёт сессию просмотра момента и возвращает её ID."""
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    INSERT INTO memory_view_sessions (memory_id, started_utc, visitor_id)
                    VALUES (?, ?, ?)
                    ''',
                    (memory_id, utc_now, visitor_id or None),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except Exception as e:
            logger.exception(f"Ошибка при создании сессии просмотра момента {memory_id}: {e}")
            return -1

    def finish_memory_view_session(self, session_id: int) -> None:
        """Завершает сессию просмотра момента и считает длительность."""
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT started_utc FROM memory_view_sessions
                    WHERE id = ? AND ended_utc IS NULL
                    ''',
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return
                started_utc = row["started_utc"]
                try:
                    t1 = datetime.strptime(started_utc, "%Y-%m-%d %H:%M:%S")
                    t2 = datetime.strptime(utc_now, "%Y-%m-%d %H:%M:%S")
                    dur = int((t2 - t1).total_seconds())
                    if dur < 0:
                        dur = 0
                except Exception:
                    dur = None
                conn.execute(
                    '''
                    UPDATE memory_view_sessions
                    SET ended_utc = ?, duration_sec = ?
                    WHERE id = ?
                    ''',
                    (utc_now, dur, session_id),
                )
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка при завершении сессии просмотра #{session_id}: {e}")

    def get_memory_view_stats(self, visitor_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Агрегированные данные по просмотрам моментов (только для visitor_id).
        - total_duration_sec: суммарное время
        - favorite_memory_id: ID момента с максимальной суммарной длительностью
        - favorite_memory_duration_sec: его суммарная длительность
        """
        result: Dict[str, Any] = {
            "total_duration_sec": 0,
            "favorite_memory_id": None,
            "favorite_memory_duration_sec": 0,
        }
        if not visitor_id:
            return result
        try:
            with self._get_connection() as conn:
                where = "WHERE visitor_id = ?"
                params = (visitor_id,)
                cursor = conn.execute(
                    f'''
                    SELECT SUM(COALESCE(duration_sec, 0)) AS total_sec
                    FROM memory_view_sessions {where}
                    '''.strip(),
                    params,
                )
                row = cursor.fetchone()
                if row and row["total_sec"] is not None:
                    result["total_duration_sec"] = int(row["total_sec"])

                cursor = conn.execute(
                    f'''
                    SELECT memory_id, SUM(COALESCE(duration_sec, 0)) AS s
                    FROM memory_view_sessions {where}
                    GROUP BY memory_id
                    ORDER BY s DESC
                    LIMIT 1
                    '''.strip(),
                    params,
                )
                row = cursor.fetchone()
                if row and row["memory_id"] is not None:
                    result["favorite_memory_id"] = int(row["memory_id"])
                    result["favorite_memory_duration_sec"] = int(row["s"] or 0)

                # Количество открытий любимого момента
                if result["favorite_memory_id"]:
                    cur2 = conn.execute(
                        f"SELECT COUNT(*) AS c FROM memory_view_sessions WHERE memory_id = ? AND visitor_id = ?",
                        (result["favorite_memory_id"], visitor_id),
                    )
                    r2 = cur2.fetchone()
                    result["favorite_memory_views"] = int(r2["c"] or 0) if r2 else 0

                # Всего уникальных открытий всех моментов этим visitor_id
                cur3 = conn.execute(
                    "SELECT COUNT(*) AS c FROM memory_view_sessions WHERE visitor_id = ?",
                    (visitor_id,),
                )
                r3 = cur3.fetchone()
                result["total_memory_views"] = int(r3["c"] or 0) if r3 else 0

                return result
        except Exception as e:
            logger.exception(f"Ошибка при получении статистики просмотров моментов: {e}")
            return result

    def get_streak_days(self, visitor_id: Optional[str] = None) -> int:
        """Текущая серия дней подряд с хотя бы одним визитом (от «сегодня» назад). Без visitor_id — 0."""
        if not visitor_id:
            return 0
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT DISTINCT DATE(visited_at_utc) AS d
                        FROM site_visits
                        WHERE visitor_id = ?
                        ORDER BY d DESC
                        ''',
                        (visitor_id,),
                    )
                dates = [row["d"] for row in cursor.fetchall()]
            if not dates:
                return 0
            today_dt = datetime.now(timezone.utc).date()
            today = today_dt.isoformat()
            yesterday = (today_dt - timedelta(days=1)).isoformat()
            last_visit = dates[0]
            if last_visit not in (today, yesterday):
                return 0
            expect = last_visit
            streak = 0
            for d in dates:
                if d != expect:
                    break
                streak += 1
                dt = datetime.strptime(expect, "%Y-%m-%d").date()
                expect = (dt - timedelta(days=1)).isoformat()
            return streak
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте серии визитов: {e}")
            return 0

    def get_longest_streak(self, visitor_id: Optional[str] = None) -> Optional[Tuple[str, str, int]]:
        """Самая длинная серия подряд дней с визитами. Возвращает (start_iso, end_iso, days) или None. Без visitor_id — None."""
        if not visitor_id:
            return None
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT DISTINCT DATE(visited_at_utc) AS d
                        FROM site_visits
                        WHERE visitor_id = ?
                        ORDER BY d
                        ''',
                        (visitor_id,),
                    )
                dates = [row["d"] for row in cursor.fetchall()]
            if not dates:
                return None
            best_start = dates[0]
            best_end = dates[0]
            best_len = 1
            cur_start = dates[0]
            cur_end = dates[0]
            cur_len = 1
            for i in range(1, len(dates)):
                prev = datetime.strptime(dates[i - 1], "%Y-%m-%d").date()
                curr = datetime.strptime(dates[i], "%Y-%m-%d").date()
                if (curr - prev).days == 1:
                    cur_end = dates[i]
                    cur_len += 1
                else:
                    if cur_len > best_len:
                        best_len = cur_len
                        best_start = cur_start
                        best_end = cur_end
                    cur_start = dates[i]
                    cur_end = dates[i]
                    cur_len = 1
            if cur_len > best_len:
                best_len = cur_len
                best_start = cur_start
                best_end = cur_end
            return (best_start, best_end, best_len)
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте самой длинной серии: {e}")
            return None

    def get_visits_per_day_for_month(
        self, year: int, month: int, visitor_id: Optional[str] = None
    ) -> Dict[int, int]:
        """Возвращает для каждого дня месяца (1–31) количество визитов. Без visitor_id — пустой словарь."""
        result: Dict[int, int] = {}
        if not visitor_id:
            return result
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT DATE(visited_at_utc) AS d, COUNT(*) AS cnt
                        FROM site_visits
                        WHERE visitor_id = ? AND strftime('%Y', visited_at_utc) = ? AND strftime('%m', visited_at_utc) = ?
                        GROUP BY d
                        ''',
                        (visitor_id, str(year), f"{month:02d}"),
                    )
                for row in cursor.fetchall():
                    d = row["d"]
                    if d:
                        day = int(d.split("-")[2])
                        result[day] = row["cnt"]
            return result
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте визитов по дням: {e}")
            return result

    def get_visits_for_time_slots(self, visitor_id: Optional[str] = None) -> List[Tuple[str, Optional[str]]]:
        """Список (visited_at_utc, timezone_id) для расчёта распределения по времени суток. Без visitor_id — []."""
        if not visitor_id:
            return []
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        'SELECT visited_at_utc, timezone_id FROM site_visits WHERE visitor_id = ? ORDER BY visited_at_utc',
                        (visitor_id,),
                    )
                return [(row["visited_at_utc"], row["timezone_id"]) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка при получении визитов для слотов: {e}")
            return []

    def get_favorite_category(self, visitor_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Любимая категория по суммарному времени просмотра. Без visitor_id — None. Без дублирования эмодзи: только title из конфига."""
        if not visitor_id:
            return None
        try:
            with self._get_connection() as conn:
                where = "WHERE s.visitor_id = ?"
                params = (visitor_id,)
                cursor = conn.execute(
                    f'''
                    SELECT m.category, SUM(COALESCE(s.duration_sec, 0)) AS total_sec, COUNT(s.id) AS views
                    FROM memory_view_sessions s
                    JOIN memories m ON m.id = s.memory_id
                    {where}
                    GROUP BY m.category
                    ORDER BY total_sec DESC
                    LIMIT 1
                    '''.strip(),
                    params,
                )
                row = cursor.fetchone()
                if not row or not row["category"]:
                    return None
                cat = row["category"]
                # Только title — в конфиге уже есть эмодзи (💫 Важные моменты и т.д.)
                label = None
                if cat.startswith("custom_"):
                    try:
                        cat_id = int(cat.replace("custom_", ""))
                        cc_row = conn.execute("SELECT name FROM custom_categories WHERE id = ?", (cat_id,)).fetchone()
                        if cc_row:
                            label = "📁 " + cc_row["name"]
                    except Exception:
                        pass
                if not label:
                    label = (config.CATEGORIES.get(cat) or {}).get("title", cat)
                if isinstance(label, str):
                    label = label.strip()
                else:
                    label = str(cat)
                if visitor_id:
                    tot = conn.execute(
                        "SELECT COUNT(*) AS c FROM memory_view_sessions WHERE visitor_id = ?",
                        (visitor_id,),
                    ).fetchone()
                else:
                    tot = conn.execute("SELECT COUNT(*) AS c FROM memory_view_sessions").fetchone()
                total_views = int(tot["c"] or 0) if tot else 0
                return {
                    "category": cat,
                    "label": label,
                    "total_sec": int(row["total_sec"] or 0),
                    "views": row["views"] or 0,
                    "total_views": total_views,
                }
        except Exception as e:
            logger.exception(f"Ошибка при получении любимой категории: {e}")
            return None

    def get_most_visits_in_day(self, visitor_id: Optional[str] = None) -> Optional[Tuple[str, int]]:
        """День с максимальным числом визитов. Возвращает (date_iso, count) или None. Без visitor_id — None."""
        if not visitor_id:
            return None
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT DATE(visited_at_utc) AS d, COUNT(*) AS cnt
                        FROM site_visits
                        WHERE visitor_id = ?
                        GROUP BY d
                        ORDER BY cnt DESC
                        LIMIT 1
                        ''',
                        (visitor_id,),
                    )
                row = cursor.fetchone()
                if not row or not row["d"]:
                    return None
                return (row["d"], row["cnt"])
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте макс. визитов за день: {e}")
            return None

    def get_longest_viewing_day(self, visitor_id: Optional[str] = None) -> Optional[Tuple[str, int]]:
        """День с максимальной суммарной длительностью просмотра моментов. (date_iso, duration_sec). Без visitor_id — None."""
        if not visitor_id:
            return None
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT DATE(started_utc) AS d, SUM(COALESCE(duration_sec, 0)) AS total_sec
                        FROM memory_view_sessions
                        WHERE visitor_id = ?
                        GROUP BY d
                        ORDER BY total_sec DESC
                        LIMIT 1
                        ''',
                        (visitor_id,),
                    )
                row = cursor.fetchone()
                if not row or not row["d"]:
                    return None
                return (row["d"], int(row["total_sec"] or 0))
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте самого длинного дня просмотра: {e}")
            return None

    def get_photos_opened_count(self, visitor_id: Optional[str] = None) -> int:
        """Сколько уникальных моментов (фото/видео/кружок) пользователь просмотрел не менее 3 секунд. Без дубликатов. Без visitor_id — 0."""
        if not visitor_id:
            return 0
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT COUNT(DISTINCT s.memory_id) AS cnt
                    FROM memory_view_sessions s
                    JOIN memories m ON m.id = s.memory_id
                    WHERE s.visitor_id = ?
                      AND COALESCE(s.duration_sec, 0) >= 3
                      AND m.media_type IN ('photo', 'video', 'video_note')
                    ''',
                    (visitor_id,),
                )
                row = cursor.fetchone()
                return int(row[0] or 0)
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте открытий фото/видео/кружков: {e}")
            return 0

    def add_category_open(self, visitor_id: Optional[str], section_id: str) -> None:
        """Одно открытие секции (нажатие «Показать»). section_id: important_moments, memories, important_dates, events, wishes."""
        if visitor_id and "_" in visitor_id:
            visitor_id = visitor_id.split("_")[0]
        try:
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            with self._get_connection() as conn:
                conn.execute(
                    '''
                    INSERT INTO category_opens (visitor_id, section_id, opened_at_utc)
                    VALUES (?, ?, ?)
                    ''',
                    (visitor_id or None, section_id, utc_now),
                )
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка при записи открытия категории {section_id}: {e}")

    def get_category_opens(self, visitor_id: Optional[str]) -> Dict[str, Any]:
        """Счётчики открытий секций для «X раз из Y». Возвращает { total: int, by_section: { section_id: count } }. Без visitor_id — пусто."""
        result: Dict[str, Any] = {"total": 0, "by_section": {}}
        if not visitor_id:
            return result
        if visitor_id and "_" in visitor_id:
            visitor_id = visitor_id.split("_")[0]
        try:
            with self._get_connection() as conn:
                if True:
                    cursor = conn.execute(
                        '''
                        SELECT section_id, COUNT(*) AS cnt
                        FROM category_opens
                        WHERE visitor_id = ?
                        GROUP BY section_id
                        ''',
                        (visitor_id,),
                    )
                total = 0
                for row in cursor.fetchall():
                    sid = row["section_id"]
                    cnt = row["cnt"]
                    result["by_section"][sid] = cnt
                    total += cnt
                result["total"] = total
            return result
        except Exception as e:
            logger.exception(f"Ошибка при получении открытий категорий: {e}")
            return result

    _DEVICE_COLUMNS = (
        "ua_pretty", "country", "city", "isp", "coords", "timezone_id",
        "os", "os_version", "browser", "browser_version", "architecture", "device_type", "model",
        "gpu_vendor", "gpu_renderer",
        "screen_w", "screen_h", "pixel_ratio", "color_depth", "orientation",
        "language", "cookies_enabled", "do_not_track", "ram_gb", "cpu_cores", "touch_points", "is_bot",
        "connection_type", "downlink_mbps", "rtt_ms", "save_data",
        "battery_level", "battery_charging", "ip_server", "ip_webrtc", "referrer", "theme",
        "role",
    )

    def add_or_update_device(self, visitor_id: str, **kwargs: Any) -> None:
        """Создаёт запись устройства или обновляет её по visitor_id. Все поля опциональны."""
        if not visitor_id:
            return
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT id FROM devices WHERE visitor_id = ?", (visitor_id,))
                row = cursor.fetchone()
                if row:
                    dev_id = row["id"]
                    sets = ["last_seen_utc = ?"]
                    vals = [utc_now]
                    for col in self._DEVICE_COLUMNS:
                        if col in kwargs:
                            sets.append(f"{col} = ?")
                            vals.append(kwargs[col])
                    vals.append(dev_id)
                    conn.execute(f"UPDATE devices SET {', '.join(sets)} WHERE id = ?", vals)
                else:
                    cols = ["visitor_id", "first_seen_utc", "last_seen_utc"] + [c for c in self._DEVICE_COLUMNS if c in kwargs]
                    placeholders = ["?", "?", "?"] + ["?"] * (len(cols) - 3)
                    vals = [visitor_id, utc_now, utc_now] + [kwargs[c] for c in self._DEVICE_COLUMNS if c in kwargs]
                    conn.execute(
                        f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join(placeholders)})",
                        vals,
                    )
                conn.execute(
                    "UPDATE devices SET visit_count = (SELECT COUNT(*) FROM site_visits WHERE site_visits.visitor_id = devices.visitor_id) WHERE visitor_id = ?",
                    (visitor_id,),
                )
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка при добавлении/обновлении устройства: {e}")

    def get_devices_list(self) -> List[Dict[str, Any]]:
        """Список устройств для админки: id, visitor_id, ua_pretty, last_seen_utc, visit_count. По последней активности."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT id, visitor_id, ua_pretty, last_seen_utc, visit_count, role
                    FROM devices
                    ORDER BY last_seen_utc DESC
                    '''
                )
                names = [d[0] for d in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка при получении списка устройств: {e}")
            return []

    def get_user_devices(self, visitor_id: str) -> List[Dict[str, Any]]:
        """Возвращает список устройств для конкретного пользователя (по префиксу visitor_id)."""
        if not visitor_id:
            return []
        base = visitor_id.split("_")[0] if "_" in visitor_id else visitor_id
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    '''
                    SELECT id, visitor_id, ua_pretty, last_seen_utc, visit_count, country, city, ip_server, ip_webrtc, role
                    FROM devices
                    WHERE visitor_id = ? OR visitor_id LIKE ?
                    ORDER BY last_seen_utc DESC
                    ''',
                    (base, f"{base}_%")
                )
                names = [d[0] for d in cursor.description]
                return [dict(zip(names, row)) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка get_user_devices: {e}")
            return []

    def get_device_by_id(self, device_id: int) -> Optional[Dict[str, Any]]:
        """Полная информация об устройстве по id для экрана детали."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                names = [d[0] for d in cursor.description]
                return dict(zip(names, row))
        except Exception as e:
            logger.exception(f"Ошибка при получении устройства {device_id}: {e}")
            return None

    def delete_device_by_id(self, device_id: int) -> bool:
        """Удаляет устройство по id. Возвращает True, если запись была удалена."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM devices WHERE id = ?", (device_id,))
                conn.commit()
                if cur.rowcount <= 0:
                    logger.warning("При удалении устройства id=%s запись не найдена", device_id)
                    return False
                return True
        except Exception as e:
            logger.exception("Ошибка при удалении устройства %s: %s", device_id, e)
            return False

    def delete_device_by_visitor_id(self, visitor_id: str) -> bool:
        """Удаляет устройство по visitor_id (например при выходе)."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM devices WHERE visitor_id = ?", (visitor_id,))
                conn.commit()
                return cur.rowcount > 0
        except Exception as e:
            logger.exception("Ошибка при удалении устройства по visitor_id %s: %s", visitor_id, e)
            return False

    def get_recent_memories(self, limit: int = 10, couple_id: Optional[int] = None, offset: int = 0) -> List[Memory]:
        """Получает последние добавленные воспоминания, опционально фильтруя по паре."""
        try:
            with self._get_connection() as conn:
                if couple_id is not None:
                    cursor = conn.execute('''
                        SELECT m.*, u.username, u.first_name, u.last_name
                        FROM memories m
                        LEFT JOIN users u ON m.user_id = u.user_id
                        WHERE m.couple_id = ?
                        ORDER BY m.created_at ASC
                        LIMIT ? OFFSET ?
                    ''', (couple_id, limit, offset))
                else:
                    cursor = conn.execute('''
                        SELECT m.*, u.username, u.first_name, u.last_name
                        FROM memories m 
                        LEFT JOIN users u ON m.user_id = u.user_id 
                        ORDER BY m.created_at ASC
                        LIMIT ? OFFSET ?
                    ''', (limit, offset))
                
                memories = []
                for row in cursor.fetchall():
                    memories.append(Memory(
                        id=row['id'],
                        user_id=row['user_id'],
                        username=row['username'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        category=row['category'],
                        title=row['title'],
                        date=row['date'],
                        content=row['content'],
                        media_type=row['media_type'],
                        media_file_id=row['media_file_id'],
                        media_path=row['media_path'],
                        media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                        created_at=row['created_at'],
                        updated_at=row['updated_at'],
                        privacy_type=row['privacy_type'] if 'privacy_type' in row.keys() else None,
                        privacy_views_limit=row['privacy_views_limit'] if 'privacy_views_limit' in row.keys() else None,
                        privacy_question=row['privacy_question'] if 'privacy_question' in row.keys() else None,
                        privacy_answer=row['privacy_answer'] if 'privacy_answer' in row.keys() else None,
                    ))
                
                return memories
        except Exception as e:
            logger.exception(f"Ошибка при получении последних воспоминаний: {e}")
            return []

    def get_memories_count(self, couple_id: Optional[int] = None) -> int:
        """Получает общее количество воспоминаний, опционально фильтруя по паре."""
        try:
            with self._get_connection() as conn:
                if couple_id is not None:
                    cursor = conn.execute('SELECT COUNT(*) FROM memories WHERE couple_id = ?', (couple_id,))
                else:
                    cursor = conn.execute('SELECT COUNT(*) FROM memories')
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception:
            logger.exception("Ошибка при получении количества воспоминаний")
            return 0

    # ── Пользовательские категории ────────────────────────────────────────────

    def create_custom_category(self, couple_id: Optional[int], name: str,
                               description: Optional[str] = None,
                               color: Optional[str] = None,
                               emoji: Optional[str] = None) -> int:
        """Создаёт пользовательскую категорию. Возвращает id новой категории или -1 при ошибке."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'INSERT INTO custom_categories (couple_id, name, description, color, emoji) VALUES (?, ?, ?, ?, ?)',
                    (couple_id, name.strip(), (description or "").strip() or None, color, emoji)
                )
                conn.commit()
                return cursor.lastrowid
        except Exception:
            logger.exception("Ошибка при создании пользовательской категории")
            return -1

    def get_custom_categories(self, couple_id: Optional[int] = None) -> List[Dict]:
        """Возвращает список пользовательских категорий пары."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                if couple_id is None:
                    return []
                cursor = conn.execute(
                    'SELECT * FROM custom_categories WHERE couple_id = ? ORDER BY id ASC',
                    (couple_id,)
                )
                return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Ошибка при получении пользовательских категорий")
            return []

    def get_custom_category(self, category_id: int) -> Optional[Dict]:
        """Возвращает пользовательскую категорию по id или None."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    'SELECT * FROM custom_categories WHERE id = ?', (category_id,)
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            logger.exception("Ошибка при получении пользовательской категории")
            return None

    def delete_custom_category(self, category_id: int) -> bool:
        """Удаляет пользовательскую категорию (и все воспоминания этой категории)."""
        try:
            key = f"custom_{category_id}"
            with self._get_connection() as conn:
                conn.execute('DELETE FROM memories WHERE category = ?', (key,))
                conn.execute('DELETE FROM custom_categories WHERE id = ?', (category_id,))
                conn.commit()
            return True
        except Exception:
            logger.exception("Ошибка при удалении пользовательской категории")
            return False

    def get_last_added_item(self, couple_id: Optional[int] = None,
                            user_ids: Optional[List[int]] = None) -> Optional[tuple]:
        """Возвращает последний добавленный элемент среди memories, scheduled_events, wishes.
        Возвращает (kind, created_at, id), где kind — 'memory'|'event'|'wish', или None.
        Если couple_id/user_ids переданы — ищет только внутри пары."""
        try:
            candidates = []
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                # Последнее воспоминание
                if couple_id is not None:
                    cur = conn.execute(
                        'SELECT id, created_at FROM memories WHERE couple_id = ? ORDER BY created_at DESC LIMIT 1',
                        (couple_id,)
                    )
                else:
                    cur = conn.execute(
                        'SELECT id, created_at FROM memories ORDER BY created_at DESC LIMIT 1'
                    )
                row = cur.fetchone()
                if row:
                    candidates.append(('memory', (row['created_at'] or ''), row['id']))
                # Последнее событие
                if user_ids:
                    placeholders = ','.join('?' * len(user_ids))
                    cur = conn.execute(
                        f'SELECT id, created_at FROM scheduled_events WHERE user_id IN ({placeholders}) ORDER BY created_at DESC LIMIT 1',
                        tuple(user_ids)
                    )
                else:
                    cur = conn.execute(
                        'SELECT id, created_at FROM scheduled_events ORDER BY created_at DESC LIMIT 1'
                    )
                row = cur.fetchone()
                if row:
                    candidates.append(('event', (row['created_at'] or ''), row['id']))
                # Последнее желание
                if user_ids:
                    placeholders = ','.join('?' * len(user_ids))
                    cur = conn.execute(
                        f'SELECT id, created_at FROM wishes WHERE user_id IN ({placeholders}) ORDER BY created_at DESC LIMIT 1',
                        tuple(user_ids)
                    )
                else:
                    cur = conn.execute(
                        'SELECT id, created_at FROM wishes ORDER BY created_at DESC LIMIT 1'
                    )
                row = cur.fetchone()
                if row:
                    candidates.append(('wish', (row['created_at'] or ''), row['id']))
            if not candidates:
                return None
            return max(candidates, key=lambda x: x[1])
        except Exception as e:
            logger.exception(f"Ошибка при получении последнего добавленного: {e}")
            return None

    # === Методы для работы с поиском ===
    
    def search_memories(self, query: str, limit: int = 20) -> List[Memory]:
        """Ищет воспоминания по тексту"""
        try:
            search_query = f"%{query}%"
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT m.*, u.username, u.first_name, u.last_name
                    FROM memories m 
                    LEFT JOIN users u ON m.user_id = u.user_id 
                    WHERE m.title LIKE ? OR m.content LIKE ? OR m.date LIKE ?
                    ORDER BY m.created_at ASC
                    LIMIT ?
                ''', (search_query, search_query, search_query, limit))
                
                memories = []
                for row in cursor.fetchall():
                    memories.append(Memory(
                        id=row['id'],
                        user_id=row['user_id'],
                        username=row['username'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        category=row['category'],
                        title=row['title'],
                        date=row['date'],
                        content=row['content'],
                        media_type=row['media_type'],
                        media_file_id=row['media_file_id'],
                        media_path=row['media_path'],
                        media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                        created_at=row['created_at'],
                        updated_at=row['updated_at']
                    ))
                
                return memories
        except Exception as e:
            logger.exception(f"Ошибка при поиске воспоминаний: {e}")
            return []
    
    def search_memories_fuzzy(self, query: str, limit: int = 50) -> List[Memory]:
        """
        Поиск воспоминаний по схожести: название, дата, описание.
        Результаты отсортированы от самых релевантных к менее.
        Веса: название +3, дата +2, описание +1 за каждое совпавшее слово.
        """
        if not query or not str(query).strip():
            return []
        words = [w.lower().strip() for w in str(query).split() if len(w.strip()) >= 1]
        if not words:
            return []
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT m.*, u.username, u.first_name, u.last_name
                    FROM memories m 
                    LEFT JOIN users u ON m.user_id = u.user_id 
                    ORDER BY m.created_at DESC
                ''')
                scored: List[Tuple[int, Memory]] = []
                for row in cursor.fetchall():
                    mem = Memory(
                        id=row['id'],
                        user_id=row['user_id'],
                        username=row['username'],
                        first_name=row['first_name'],
                        last_name=row['last_name'],
                        category=row['category'],
                        title=row['title'] or '',
                        date=row['date'] or '',
                        content=row['content'] or '',
                        media_type=row['media_type'],
                        media_file_id=row['media_file_id'],
                        media_path=row['media_path'],
                        media_items=self._decode_media_items(row['media_items'] if 'media_items' in row.keys() else None),
                        created_at=row['created_at'],
                        updated_at=row['updated_at']
                    )
                    score = 0
                    title_l = (mem.title or '').lower()
                    date_l = (mem.date or '').lower()
                    content_l = (mem.content or '').lower()
                    for word in words:
                        if word in title_l:
                            score += 3
                        if word in date_l:
                            score += 2
                        if word in content_l:
                            score += 1
                    if score > 0:
                        scored.append((score, mem))
                scored.sort(key=lambda x: -x[0])
                return [m for _, m in scored[:limit]]
        except Exception as e:
            logger.exception(f"Ошибка при нечётком поиске воспоминаний: {e}")
            return []

    # === Методы для очистки ===
    
    def cleanup_orphaned_media(self):
        """Очищает медиа-файлы, не связанные с воспоминаниями"""
        try:
            with self._get_connection() as conn:
                # Получаем все используемые медиа-пути
                cursor = conn.execute('SELECT media_path FROM memories WHERE media_path IS NOT NULL')
                used_paths = {row['media_path'] for row in cursor.fetchall()}
                
                # Проверяем все файлы в медиа-папке
                if os.path.exists(self.media_folder):
                    for root, dirs, files in os.walk(self.media_folder):
                        for file in files:
                            file_path = os.path.join(root, file)
                            if file_path not in used_paths:
                                try:
                                    os.remove(file_path)
                                    logger.info(f"Удалён неиспользуемый медиа-файл: {file_path}")
                                except Exception as e:
                                    logger.exception(f"Ошибка при удалении файла {file_path}: {e}")
        except Exception as e:
            logger.exception(f"Ошибка при очистке медиа: {e}")

    # === Методы для ожидаемых событий (события на дату) ===

    def add_scheduled_event(
        self,
        user_id: int,
        title: str,
        description: Optional[str],
        event_datetime: str,
        media_type: Optional[str] = None,
        media_file_id: Optional[str] = None,
        media_path: Optional[str] = None,
        is_recurring: int = 0,
    ) -> int:
        """Добавляет ожидаемое событие (описание может быть HTML, с медиа как у воспоминаний)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    INSERT INTO scheduled_events
                    (user_id, title, description, event_datetime, media_type, media_file_id, media_path, is_recurring)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, title, description or "", event_datetime, media_type, media_file_id, media_path, is_recurring))
                conn.commit()
                event_id = cursor.lastrowid
                logger.info(f"Добавлено событие #{event_id} пользователем {user_id}")
                return event_id
        except Exception as e:
            logger.exception(f"Ошибка при добавлении события: {e}")
            return -1

    def get_scheduled_event(self, event_id: int) -> Optional['ScheduledEvent']:
        """Получает событие по ID"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    'SELECT * FROM scheduled_events WHERE id = ?',
                    (event_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                return ScheduledEvent(
                    id=d['id'],
                    user_id=d['user_id'],
                    title=d['title'],
                    description=d.get('description') or None,
                    event_datetime=d['event_datetime'],
                    created_at=d['created_at'],
                    notified_at=d.get('notified_at'),
                    media_type=d.get('media_type'),
                    media_file_id=d.get('media_file_id'),
                    media_path=d.get('media_path'),
                    is_recurring=d.get('is_recurring', 0) if d.get('is_recurring') is not None else 0,
                )
        except Exception as e:
            logger.exception(f"Ошибка при получении события: {e}")
            return None

    def get_scheduled_events(self, limit: int = 100, user_ids: Optional[List[int]] = None) -> List['ScheduledEvent']:
        """Получает ожидаемые события, отсортированные по ближайшему наступлению. Опционально фильтрует по user_ids."""
        try:
            with self._get_connection() as conn:
                if user_ids:
                    placeholders = ','.join('?' * len(user_ids))
                    cursor = conn.execute(
                        f'SELECT * FROM scheduled_events WHERE user_id IN ({placeholders})',
                        user_ids
                    )
                else:
                    cursor = conn.execute('SELECT * FROM scheduled_events')
                events = [_row_to_scheduled_event(row) for row in cursor.fetchall()]
            events.sort(key=_get_event_sort_key)
            return events[:limit]
        except Exception as e:
            logger.exception(f"Ошибка при получении событий: {e}")
            return []

    def get_scheduled_events_count(self) -> int:
        """Общее количество событий (для пагинации)."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('SELECT COUNT(*) FROM scheduled_events')
                row = cursor.fetchone()
                return row[0] if row else 0
        except Exception as e:
            logger.exception(f"Ошибка при подсчёте событий: {e}")
            return 0

    def get_scheduled_events_paged(self, page: int = 1, per_page: int = 10) -> Tuple[List['ScheduledEvent'], int]:
        """События по странице, отсортированные по ближайшему наступлению."""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('SELECT * FROM scheduled_events')
                all_events = [_row_to_scheduled_event(row) for row in cursor.fetchall()]
            all_events.sort(key=_get_event_sort_key)
            total = len(all_events)
            offset = (page - 1) * per_page
            events = all_events[offset:offset + per_page]
            return events, total
        except Exception as e:
            logger.exception(f"Ошибка при получении событий по странице: {e}")
            return [], 0

    def search_scheduled_events(self, query: str, page: int = 1, per_page: int = 10) -> Tuple[List['ScheduledEvent'], int]:
        """Поиск по названию и описанию, отсортированный по ближайшему наступлению."""
        if not (query or "").strip():
            return self.get_scheduled_events_paged(page, per_page)
        try:
            with self._get_connection() as conn:
                like = f"%{query.strip()}%"
                cursor = conn.execute(
                    'SELECT * FROM scheduled_events WHERE title LIKE ? OR description LIKE ?',
                    (like, like)
                )
                all_events = [_row_to_scheduled_event(row) for row in cursor.fetchall()]
            all_events.sort(key=_get_event_sort_key)
            total = len(all_events)
            offset = (page - 1) * per_page
            events = all_events[offset:offset + per_page]
            return events, total
        except Exception as e:
            logger.exception(f"Ошибка при поиске событий: {e}")
            return [], 0

    def get_scheduled_events_unnotified(self) -> List['ScheduledEvent']:
        """Получает события, для которых ещё не уведомлены оба (создатель или компаньон)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute('''
                    SELECT * FROM scheduled_events
                    WHERE (COALESCE(notified_to_creator, 0) = 0 OR COALESCE(notified_to_ksusha, 0) = 0)
                      AND (notified_at IS NULL OR COALESCE(notified_to_creator, 0) = 1 OR COALESCE(notified_to_ksusha, 0) = 1)
                    ORDER BY event_datetime ASC
                ''')
                return [_row_to_scheduled_event(row) for row in cursor.fetchall()]
        except Exception as e:
            logger.exception(f"Ошибка при получении истёкших событий: {e}")
            return []

    def mark_scheduled_event_notified(self, event_id: int) -> bool:
        """Отмечает событие как уведомлённое (для обратной совместимости)"""
        return self.mark_scheduled_event_notified_for(event_id, "creator") and \
               self.mark_scheduled_event_notified_for(event_id, "ksusha")

    def mark_scheduled_event_notified_for(self, event_id: int, target: str) -> bool:
        """Отмечает, что уведомление отправлено создателю или Ксюше. target: 'creator' | 'ksusha'"""
        col = "notified_to_creator" if target == "creator" else "notified_to_ksusha"
        try:
            with self._get_connection() as conn:
                conn.execute(f'''
                    UPDATE scheduled_events SET {col} = 1, notified_at = CURRENT_TIMESTAMP WHERE id = ?
                ''', (event_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при обновлении события: {e}")
            return False

    def is_scheduled_event_notified_for(self, event_id: int, target: str) -> bool:
        """Проверяет, отправлено ли уведомление создателю или Ксюше"""
        col = "notified_to_creator" if target == "creator" else "notified_to_ksusha"
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    f'SELECT {col} FROM scheduled_events WHERE id = ?',
                    (event_id,)
                )
                row = cursor.fetchone()
                return bool(row and row[0])
        except Exception:
            return False

    def update_scheduled_event(self, event_id: int, **kwargs) -> bool:
        """Обновляет событие (в т.ч. description, media_type, media_file_id, media_path)."""
        allowed = ['title', 'description', 'event_datetime', 'media_type', 'media_file_id', 'media_path', 'is_recurring']
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return False
        set_clause = ', '.join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [event_id]
        try:
            with self._get_connection() as conn:
                if 'event_datetime' in updates:
                    conn.execute('DELETE FROM event_notifications WHERE event_id = ?', (event_id,))
                conn.execute(f'UPDATE scheduled_events SET {set_clause} WHERE id = ?', values)
                conn.commit()
                return True
        except Exception as e:
            logger.exception(f"Ошибка при обновлении события: {e}")
            return False

    def delete_scheduled_event(self, event_id: int) -> bool:
        """Удаляет событие"""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    'SELECT media_path FROM scheduled_events WHERE id = ?', (event_id,)
                ).fetchone()

            if row is None:
                return False

            media_candidates: List[str] = []
            raw_path = row['media_path'] if 'media_path' in row.keys() else None
            if raw_path:
                normalized = self._normalize_media_path(raw_path)
                if normalized:
                    media_candidates.append(normalized)

            with self._get_connection() as conn:
                conn.execute('DELETE FROM event_notifications WHERE event_id = ?', (event_id,))
                conn.execute('DELETE FROM site_celebrations WHERE event_id = ?', (event_id,))
                conn.execute('DELETE FROM scheduled_events WHERE id = ?', (event_id,))
                conn.commit()

            self._delete_media_files_if_unreferenced(media_candidates)
            logger.info(f"Удалено событие #{event_id}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка при удалении события: {e}")
            return False

    # ── Celebrations ────────────────────────────────────────────────────────

    def add_celebration(self, celebration_type: str, event_title: str = "",
                        event_id: Optional[int] = None) -> int:
        """Создаёт новую праздничную анимацию для показа на сайте."""
        utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "INSERT INTO site_celebrations (celebration_type, event_title, event_id, created_at_utc) VALUES (?,?,?,?)",
                    (celebration_type, event_title or "", event_id, utc_now)
                )
                conn.commit()
                return cur.lastrowid
        except Exception as e:
            logger.exception("add_celebration error: %s", e)
            return 0

    def get_pending_celebration(self, visitor_id: str) -> Optional[Dict[str, Any]]:
        """Возвращает первую непоказанную анимацию для данного visitor_id."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT c.id, c.celebration_type, c.event_title, c.event_id, c.created_at_utc, c.delivered_to "
                    "FROM site_celebrations c "
                    "LEFT JOIN scheduled_events e ON c.event_id = e.id "
                    "WHERE c.event_id IS NULL OR e.id IS NOT NULL "
                    "ORDER BY c.id ASC"
                )
                for row in cur.fetchall():
                    delivered = (row[5] or "").split(",")
                    if visitor_id not in delivered:
                        return {
                            "id": row[0],
                            "celebration_type": row[1],
                            "event_title": row[2],
                            "event_id": row[3],
                            "created_at_utc": row[4],
                        }
            return None
        except Exception as e:
            logger.exception("get_pending_celebration error: %s", e)
            return None

    def mark_celebration_delivered(self, celebration_id: int, visitor_id: str) -> None:
        """Помечает анимацию как показанную для visitor_id."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT delivered_to FROM site_celebrations WHERE id=?", (celebration_id,)
                )
                row = cur.fetchone()
                if not row:
                    return
                existing = (row[0] or "").split(",")
                if visitor_id not in existing:
                    existing.append(visitor_id)
                conn.execute(
                    "UPDATE site_celebrations SET delivered_to=? WHERE id=?",
                    (",".join(e for e in existing if e), celebration_id)
                )
                conn.commit()
        except Exception as e:
            logger.exception("mark_celebration_delivered error: %s", e)

    def get_last_celebration_date(self, ctype: str) -> Optional[str]:
        """Последняя дата создания анимации данного типа (для дедупликации)."""
        try:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "SELECT created_at_utc FROM site_celebrations WHERE celebration_type=? "
                    "ORDER BY id DESC LIMIT 1", (ctype,)
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def get_export_data(self) -> Dict[str, Any]:
        """Данные для экспорта: воспоминания, события, желания, пользователи (все поля для восстановления)."""
        result = {
            "version": 1,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "memories": [],
            "scheduled_events": [],
            "wishes": [],
            "users": [],
        }
        try:
            with self._get_connection() as conn:
                for table, key in [
                    ("memories", "memories"),
                    ("scheduled_events", "scheduled_events"),
                    ("wishes", "wishes"),
                ]:
                    cursor = conn.execute(f"SELECT * FROM {table}")
                    cols = [d[0] for d in cursor.description]
                    for row in cursor.fetchall():
                        result[key].append(dict(zip(cols, row)))
                cursor = conn.execute(
                    "SELECT user_id, username, first_name, last_name FROM users"
                )
                cols = [d[0] for d in cursor.description]
                for row in cursor.fetchall():
                    result["users"].append(dict(zip(cols, row)))
        except Exception as e:
            logger.exception("Ошибка экспорта данных: %s", e)
        return result

    def import_from_export(self, data: Dict[str, Any]) -> Tuple[int, int, int]:
        """
        Восстанавливает данные из экспорта. Сначала создаёт пользователей (INSERT OR IGNORE),
        затем вставляет воспоминания, события, желания (без id — новые id назначает БД).
        Возвращает (memories_count, events_count, wishes_count).
        """
        mem_ok = ev_ok = wish_ok = 0
        users = data.get("users") or []
        memories = data.get("memories") or []
        events = data.get("scheduled_events") or []
        wishes = data.get("wishes") or []
        try:
            with self._get_connection() as conn:
                for u in users:
                    uid = u.get("user_id")
                    if uid is None:
                        continue
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            uid,
                            u.get("username"),
                            u.get("first_name"),
                            u.get("last_name"),
                        ),
                    )
                mem_cols = [
                    "user_id", "category", "title", "date", "content",
                    "media_type", "media_file_id", "media_path",
                    "created_at", "updated_at",
                    "privacy_type", "privacy_views_limit", "privacy_question", "privacy_answer",
                ]
                for m in memories:
                    try:
                        uid, cat, title, dt, content = (
                            m.get("user_id"), m.get("category"), m.get("title"), m.get("date"), (m.get("content") or "")
                        )
                        cur = conn.execute(
                            """
                            SELECT id FROM memories
                            WHERE user_id = ? AND category = ? AND title = ? AND date = ? AND content = ?
                            LIMIT 1
                            """,
                            (uid, cat, title, dt, content),
                        )
                        if cur.fetchone():
                            continue
                        vals = [
                            uid, cat, title, dt, content,
                            m.get("media_type"), m.get("media_file_id"), m.get("media_path"),
                            m.get("created_at"), m.get("updated_at"),
                            m.get("privacy_type"), m.get("privacy_views_limit"),
                            m.get("privacy_question"), m.get("privacy_answer"),
                        ]
                        conn.execute(
                            f"""
                            INSERT INTO memories ({", ".join(mem_cols)})
                            VALUES ({", ".join("?" * len(mem_cols))})
                            """,
                            vals,
                        )
                        mem_ok += 1
                    except Exception as e:
                        logger.warning("Пропуск воспоминания при импорте: %s", e)
                ev_cols = [
                    "user_id", "title", "description", "event_datetime",
                    "media_type", "media_file_id", "media_path",
                    "created_at", "notified_at",
                    "notified_to_creator", "notified_to_ksusha",
                    "is_recurring",
                ]
                for ev in events:
                    try:
                        euid, etitle, edt = ev.get("user_id"), ev.get("title"), ev.get("event_datetime")
                        cur = conn.execute(
                            """
                            SELECT id FROM scheduled_events
                            WHERE user_id = ? AND title = ? AND event_datetime = ?
                            LIMIT 1
                            """,
                            (euid, etitle, edt),
                        )
                        if cur.fetchone():
                            continue
                        vals = [
                            euid, etitle, ev.get("description") or "", edt,
                            ev.get("media_type"), ev.get("media_file_id"), ev.get("media_path"),
                            ev.get("created_at"), ev.get("notified_at"),
                            ev.get("notified_to_creator"), ev.get("notified_to_ksusha"),
                            ev.get("is_recurring", 0) if ev.get("is_recurring") is not None else 0,
                        ]
                        conn.execute(
                            f"""
                            INSERT INTO scheduled_events ({", ".join(ev_cols)})
                            VALUES ({", ".join("?" * len(ev_cols))})
                            """,
                            vals,
                        )
                        ev_ok += 1
                    except Exception as err:
                        logger.warning("Пропуск события при импорте: %s", err)
                wish_cols = [
                    "user_id", "wish_number", "content",
                    "media_type", "media_file_id", "media_path",
                    "created_at", "updated_at",
                ]
                for w in wishes:
                    try:
                        vals = [
                            w.get("user_id"), w.get("wish_number"), w.get("content") or "",
                            w.get("media_type"), w.get("media_file_id"), w.get("media_path"),
                            w.get("created_at"), w.get("updated_at"),
                        ]
                        cur = conn.execute(
                            f"""
                            INSERT OR IGNORE INTO wishes ({", ".join(wish_cols)})
                            VALUES ({", ".join("?" * len(wish_cols))})
                            """,
                            vals,
                        )
                        if cur.rowcount:
                            wish_ok += 1
                    except Exception as err:
                        logger.warning("Пропуск желания при импорте: %s", err)
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка импорта: %s", e)
        return (mem_ok, ev_ok, wish_ok)

    def backup_database(self, backup_path: str = None) -> bool:
        """Создаёт резервную копию базы данных через sqlite3.backup (безопасно при открытом файле)."""
        try:
            if not backup_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = f"memories_backup_{timestamp}.db"
            src = sqlite3.connect(self.db_path)
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
                src.close()
            logger.info(f"Создана резервная копия: {backup_path}")
            return True
        except Exception as e:
            logger.exception(f"Ошибка при создании резервной копии: {e}")
            return False

    def create_unlink_request(self, user_id: int, couple_id: int, token: str, ip: str, ua: str, country: str, city: str) -> str:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO unlink_requests (token, user_id, couple_id, ip, ua, country, city) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (token, user_id, couple_id, ip, ua, country, city)
                )
                conn.commit()
            return token
        except Exception as e:
            logger.exception("Ошибка create_unlink_request: %s", e)
            return ""

    def get_unlink_request(self, token: str) -> Optional[Dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT * FROM unlink_requests WHERE token = ?", (token,)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("Ошибка get_unlink_request: %s", e)
            return None

    def get_unlink_request_by_transfer_code(self, code: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT * FROM unlink_requests WHERE transfer_invite_code = ?", (code,)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("Ошибка get_unlink_request_by_transfer_code: %s", e)
            return None

    def get_rebound_account(self, old_user_id: int) -> Optional[dict]:
        """Проверяет, был ли этот аккаунт перепривязан, и возвращает данные нового аккаунта."""
        try:
            with self._get_connection() as conn:
                query = '''
                    SELECT u.user_id, u.first_name 
                    FROM unlink_requests ur
                    JOIN invite_codes i ON ur.transfer_invite_code = i.code
                    JOIN users u ON i.used_by = u.user_id
                    WHERE ur.user_id = ? AND i.used = 1
                    ORDER BY ur.created_at DESC LIMIT 1
                '''
                row = conn.execute(query, (old_user_id,)).fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.exception("Ошибка get_rebound_account: %s", e)
            return None

    def clear_rebound_status(self, old_user_id: int) -> bool:
        """Скрывает старую запись о переносе, чтобы пользователь мог начать с чистого листа."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE unlink_requests SET user_id = -abs(user_id) WHERE user_id = ?",
                    (old_user_id,)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.exception("Ошибка clear_rebound_status: %s", e)
            return False

    def set_unlink_status(self, token: str, status: str, transfer_invite_code: str = None) -> bool:
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE unlink_requests SET status = ?, responded_at = ?, transfer_invite_code = ? WHERE token = ?",
                    (status, now_str, transfer_invite_code, token)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.exception("Ошибка set_unlink_status: %s", e)
            return False

    def revoke_all_user_sessions(self, user_id: int, visitor_id_base: str) -> None:
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM devices WHERE visitor_id = ? OR visitor_id LIKE ?", (visitor_id_base, f"{visitor_id_base}_%"))
                conn.execute("UPDATE user_login_tokens SET is_revoked = 1 WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM user_tokens WHERE user_id = ?", (user_id,))
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка revoke_all_user_sessions: %s", e)

    def log_security_event(self, type: str, user_id: Optional[int], ip: str, ua: str, country: str, city: str, detail: str) -> None:
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO security_events (type, user_id, ip, ua, country, city, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (type, user_id, ip, ua, country, city, detail)
                )
                conn.commit()
        except Exception as e:
            logger.exception("Ошибка log_security_event: %s", e)

    def get_recent_unlink_denied(self, user_id: int, within_seconds: int = 3600) -> bool:
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    f"SELECT 1 FROM security_events WHERE type = 'unlink_denied' AND user_id = ? AND created_at >= datetime('now', '-{within_seconds} seconds') LIMIT 1",
                    (user_id,)
                ).fetchone()
                return bool(row)
        except Exception as e:
            logger.exception("Ошибка get_recent_unlink_denied: %s", e)
            return False

    def has_recent_unlink_request(self, user_id: int, within_seconds: int = 60) -> bool:
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    f"SELECT 1 FROM unlink_requests WHERE user_id = ? AND created_at >= datetime('now', '-{within_seconds} seconds') LIMIT 1",
                    (user_id,)
                ).fetchone()
                return bool(row)
        except Exception as e:
            logger.exception("Ошибка has_recent_unlink_request: %s", e)
            return False

    def delete_user_data(self, user_id: int) -> Dict[str, int]:
        """Удаляет все данные одного пользователя (воспоминания, события, желания, избранное и т.д.)."""
        result = {}
        media_paths: list = []
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                # Собираем пути к медиафайлам ДО удаления строк
                for tbl in ("memories", "wishes"):
                    try:
                        rows = conn.execute(
                            f"SELECT media_path FROM {tbl} WHERE user_id = ? AND media_path IS NOT NULL",
                            (user_id,),
                        ).fetchall()
                        media_paths.extend(r["media_path"] for r in rows if r["media_path"])
                    except Exception as e:
                        logger.debug("Не удалось собрать media_path из %s для user_id=%s: %s", tbl, user_id, e)
                # Удаляем дочерние записи для воспоминаний этого пользователя
                # (включая сессии/просмотры созданные другими пользователями → партнёром)
                for child_tbl in ("memory_view_sessions", "memory_views"):
                    try:
                        c = conn.execute(
                            f"DELETE FROM {child_tbl} WHERE memory_id IN "
                            f"(SELECT id FROM memories WHERE user_id = ?)",
                            (user_id,),
                        )
                        result[f"{child_tbl}_by_memory"] = c.rowcount
                    except Exception:
                        pass
                # Теперь удаляем собственные данные пользователя
                tables_user_id = [
                    "memory_view_sessions", "memory_views", "category_opens",
                    "companion_messages", "site_notifications", "bot_last_active",
                    "memories", "scheduled_events", "wishes", "favorites",
                ]
                for tbl in tables_user_id:
                    try:
                        c = conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
                        result[tbl] = c.rowcount
                    except Exception as e:
                        logger.debug("DELETE failed for table=%s user_id=%s: %s", tbl, user_id, e)
                        result[tbl] = 0
                for tbl in ("user_settings", "user_tokens", "user_profiles"):
                    try:
                        c = conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
                        result[tbl] = c.rowcount
                    except Exception as e:
                        logger.debug("DELETE failed for table=%s user_id=%s: %s", tbl, user_id, e)
                        result[tbl] = 0
                conn.execute("PRAGMA foreign_keys=ON")
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка delete_user_data({user_id}): {e}")
            return result
        # Удаляем медиафайлы с диска после успешного удаления из БД
        files_removed = 0
        for path in media_paths:
            try:
                if os.path.exists(path):
                    os.remove(path)
                    files_removed += 1
            except Exception as fe:
                logger.warning(f"Не удалось удалить медиафайл {path}: {fe}")
        if files_removed:
            result["media_files_removed"] = files_removed
        return result

    def delete_couple_data(self, user_id: int) -> Dict[str, int]:
        """Удаляет данные пользователя и его партнёра по паре."""
        couple = self.get_couple_by_user(user_id)
        result = {}
        if not couple:
            result = self.delete_user_data(user_id)
            return result
        user1 = couple.get("user1_id")
        user2 = couple.get("user2_id")
        for uid in [u for u in [user1, user2] if u]:
            r = self.delete_user_data(uid)
            for k, v in r.items():
                result[k] = result.get(k, 0) + v
        try:
            with self._get_connection() as conn:
                cc = conn.execute(
                    "DELETE FROM custom_categories WHERE couple_id = ?", (couple["id"],)
                )
                result["custom_categories"] = cc.rowcount
                # Сначала удаляем invite_codes (FK → couples), потом саму пару
                ci = conn.execute(
                    "DELETE FROM invite_codes WHERE couple_id = ?", (couple["id"],)
                )
                result["invite_codes"] = ci.rowcount
                c = conn.execute(
                    "DELETE FROM couples WHERE id = ?", (couple["id"],)
                )
                result["couples"] = c.rowcount
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка удаления пары: {e}")
        return result

    def delete_all_data(self) -> Dict[str, int]:
        """Удаляет абсолютно все пользовательские данные (кроме таблицы settings и admins)."""
        result = {}
        # Порядок важен: сначала дочерние таблицы (с FK), потом родительские.
        # invite_codes → couples, memory_view_sessions/memory_views → memories.
        tables = [
            "invite_codes",           # FK → couples (удаляем ДО couples!)
            "memory_view_sessions",   # FK → memories
            "memory_views",           # FK → memories
            "category_opens",
            "companion_messages",
            "site_notifications",
            "bot_last_active",
            "event_notifications",
            "site_visits",
            "site_celebrations",
            "visitor_site_time",
            "devices",
            "favorites",
            "custom_categories",
            "memories",               # родитель для memory_view_sessions / memory_views
            "scheduled_events",
            "wishes",
            "couples",                # родитель для invite_codes (уже пусто)
            "user_settings",
            "user_tokens",
            "user_profiles",
        ]
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                for tbl in tables:
                    try:
                        c = conn.execute(f"DELETE FROM {tbl}")
                        result[tbl] = c.rowcount
                    except Exception as ex:
                        logger.warning(f"delete_all_data: не удалось очистить {tbl}: {ex}")
                        result[tbl] = 0
                conn.execute("PRAGMA foreign_keys=ON")
                conn.commit()
        except Exception as e:
            logger.exception(f"Ошибка delete_all_data: {e}")
            return result
        try:
            removed = self._wipe_media_folder()
            result["media_files_removed"] = removed
            logger.info("delete_all_data: wiped media folder, removed files=%d", removed)
        except Exception as e:
            logger.exception("delete_all_data: media wipe failed: %s", e)
        return result

    def _wipe_media_folder(self) -> int:
        """Удаляет все файлы в папке media. Вызывается только при полном сбросе данных."""
        media_root = self._media_root_resolved()
        if not os.path.isdir(media_root):
            logger.warning("_wipe_media_folder: папка не найдена: %s", media_root)
            return 0
        removed = 0
        errors = 0
        for entry in os.scandir(media_root):
            if entry.is_file(follow_symlinks=False):
                try:
                    os.remove(entry.path)
                    removed += 1
                except Exception:
                    logger.exception("_wipe_media_folder: не удалось удалить %s", entry.path)
                    errors += 1
            elif entry.is_dir(follow_symlinks=False):
                try:
                    shutil.rmtree(entry.path)
                    removed += 1
                except Exception:
                    logger.exception("_wipe_media_folder: не удалось удалить директорию %s", entry.path)
                    errors += 1
        if errors:
            logger.warning("_wipe_media_folder: завершено с ошибками, удалено=%d, ошибок=%d", removed, errors)
        return removed


# Создаём глобальный экземпляр базы данных
db = Database()
