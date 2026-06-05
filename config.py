import os
import sys
import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Dict, Any, Optional
from dotenv import load_dotenv


load_dotenv()

# Single canonical Production database path.
# This is the ONLY path that Production will ever use.
# To change it you must edit this constant explicitly — there is no automatic selection.
PRODUCTION_DB_PATH = "/workspace/data/memories.db"


@dataclass
class Config:
    CREATOR_ID: int = int(os.getenv("CREATOR_ID", "0"))
    KSUSHA_ID: int = int(os.getenv("KSUSHA_ID", "0"))

    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_SITE_URL: str = os.getenv("BOT_SITE_URL", "")
    SITE_DIRECT_URL: str = os.getenv("SITE_DIRECT_URL", "")
    HTTP_HOST: str = os.getenv("HTTP_HOST", "0.0.0.0")  # 0.0.0.0 needed for cloudflared container; port NOT published to host (see docker-compose.yml)
    HTTP_PORT: int = int(os.getenv("HTTP_PORT", "25086"))
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "Akimova_Ksysha_love_bot")
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "")
    AI_SESSION_SECRET: str = os.getenv("AI_SESSION_SECRET", "")
    MEDIA_ACCESS_TOKEN: str = os.getenv("MEDIA_ACCESS_TOKEN", "")
    DEPLOYER_SECRET: str = os.getenv("DEPLOYER_SECRET", "")
    DEPLOYER_URL: str = os.getenv("DEPLOYER_URL", "http://deployer:25100/deploy")
    SITE_AUTH_PASSWORD_KSYUSHA: str = os.getenv("SITE_AUTH_PASSWORD_KSYUSHA", "")
    SITE_AUTH_PASSWORD_CREATOR: str = os.getenv("SITE_AUTH_PASSWORD_CREATOR", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    SECRETS_STRICT: bool = os.getenv("SECRETS_STRICT", "1").strip().lower() not in ("0", "false", "no", "off")
    
    
    TEXT_MARCH_8: str = """Привет! С праздником! 🎉
Здесь собрано всё что нас связывает.
Ты можешь добавлять/изменять или удалять любые моменты и в любой момент их пересматривать.
Приятного просмотра! ✨☺️"""

    TEXT_OTHER_DATE: str = """Привет ✨
Тут собраны все (или почти все) наши воспоминания. Ты можешь нажать на каждый из них,
посмотреть, удалить, изменить или даже добавить своё!
Приятного просмотра! 🎁"""
    
    TEXT_FOR_OTHER_ADMINS: str = """Привет, администратор! ✨
Добро пожаловать в панель управления воспоминаниями.
Здесь ты можешь добавлять, редактировать и удалять воспоминания.
Приятного использования! 💫"""
    
    TEXT_FOR_OTHER_USERS: str = """Этот бот имеет ограниченный доступ.
Если хочешь чтобы тебе открыли доступ — обратись к разработчику."""
    
    # DATABASE_PATH is always PRODUCTION_DB_PATH.
    # It is a field (not a constant) so that future TEST MODE can override it
    # explicitly via an admin command — never automatically.
    DATABASE_PATH: str = PRODUCTION_DB_PATH
    HOT_BACKUP_ENABLED: bool = os.getenv("HOT_BACKUP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
    HOT_BACKUP_PATH: str = os.getenv("HOT_BACKUP_PATH", "")
    SITE_OPEN_DATE: Optional[date] = None
    CATEGORIES: Dict[str, Dict[str, str]] = None
    MEDIA_FOLDER: str = os.getenv("MEDIA_FOLDER", "media")
    EXTERNAL_IP: str = ""

    @staticmethod
    def _file_sha256(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    
    def _check_required_secrets(self) -> None:
        import logging
        _REQUIRED = [
            ("BOT_TOKEN",      self.BOT_TOKEN,      "Telegram Bot API token"),
            ("API_SECRET_KEY", self.API_SECRET_KEY,  "API secret key for session signing"),
        ]
        # AI_SESSION_SECRET is optional only if API_SECRET_KEY is set (used as HMAC fallback)
        hmac_ok = bool(self.AI_SESSION_SECRET.strip() or self.API_SECRET_KEY.strip())
        missing = [name for name, val, _ in _REQUIRED if not (val or "").strip()]
        if not hmac_ok:
            missing.append("AI_SESSION_SECRET or API_SECRET_KEY (needed for HMAC token signing)")

        if missing:
            msg = (
                "FATAL: Required secrets are not set:\n"
                + "\n".join(f"  - {m}" for m in missing)
                + "\nSet them in .env and restart. Refusing to start with SECRETS_STRICT=1."
            )
            print(msg, file=sys.stderr)
            logging.critical(msg)
            sys.exit(1)

    def __post_init__(self):
        import logging

        if self.SECRETS_STRICT:
            self._check_required_secrets()

        os.makedirs(self.MEDIA_FOLDER, exist_ok=True)

        if self.SITE_OPEN_DATE is None:
            self.SITE_OPEN_DATE = date(2026, 2, 8)

        # DATABASE_PATH is unconditionally set to the single canonical Production path.
        # There is no environment variable override, no docker/host detection,
        # no whitelist of multiple paths, and no automatic fallback.
        # Future TEST MODE must set self.DATABASE_PATH explicitly via an admin command.
        self.DATABASE_PATH = PRODUCTION_DB_PATH

        print(f"ACTIVE DATABASE: {self.DATABASE_PATH}")
        print(f"DATABASE MODE: PRODUCTION")
        logging.info(f"ACTIVE DATABASE: {self.DATABASE_PATH}")
        logging.info(f"DATABASE MODE: PRODUCTION")

        # Abort if the canonical path is somehow overridden to something unexpected.
        # This is a defence-in-depth check — it should never fire under normal operation.
        if os.path.abspath(self.DATABASE_PATH) != os.path.abspath(PRODUCTION_DB_PATH):
            msg = (
                f"FATAL: DATABASE_PATH was mutated to '{self.DATABASE_PATH}' "
                f"which differs from PRODUCTION_DB_PATH '{PRODUCTION_DB_PATH}'. "
                "This is not allowed. Exiting."
            )
            print(msg, file=sys.stderr)
            logging.critical(msg)
            sys.exit(1)

        # Warn about any other memories.db files found outside the canonical path.
        # These are treated as orphans — they are never used, never switched to.
        _ghost_candidates = [
            "/app/data/memories.db",
            "/root/KsyshaTest/data/memories.db",
            "/root/KsyshaTest/memories.db",
        ]
        canonical_abs = os.path.abspath(self.DATABASE_PATH)
        for ghost_path in _ghost_candidates:
            try:
                ghost_abs = os.path.abspath(ghost_path)
                if ghost_abs == canonical_abs:
                    continue
                if not (os.path.exists(ghost_abs) and os.path.isfile(ghost_abs)):
                    continue
                ghost_size = os.path.getsize(ghost_abs)
                if ghost_size == 0:
                    continue
                msg = (
                    f"WARNING: Orphan database file detected at '{ghost_path}' "
                    f"(size={ghost_size} bytes). "
                    f"This file is NOT used. Canonical DB is '{canonical_abs}'."
                )
                print(msg)
                logging.warning(msg)
            except Exception:
                pass

        self.CATEGORIES = {
            "important_moments": {
                "title": "💫 Важные моменты",
                "description": "Самые значимые события в нашей истории",
                "emoji": "💫"
            },
            "memories": {
                "title": "📖 Воспоминания",
                "description": "Все то что я вспомнил и что тебе было бы тоже хорошо напомнить",
                "emoji": "📖"
            },
            "important_dates": {
                "title": "📅 Важные даты",
                "description": "Даты которые стоит помнить и отмечать вместе 🎉",
                "emoji": "📅"
            }
        }

config = Config()
