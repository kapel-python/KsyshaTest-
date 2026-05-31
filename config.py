import os
import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Dict, Any, Optional
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Config:
    CREATOR_ID: int = int(os.getenv("CREATOR_ID", "0"))
    KSUSHA_ID: int = int(os.getenv("KSUSHA_ID", "0"))

    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_SITE_URL: str = os.getenv("BOT_SITE_URL", "")
    SITE_DIRECT_URL: str = os.getenv("SITE_DIRECT_URL", "")
    HTTP_HOST: str = os.getenv("HTTP_HOST", "0.0.0.0")
    HTTP_PORT: int = int(os.getenv("HTTP_PORT", "25086"))
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "Akimova_Ksysha_love_bot")
    API_SECRET_KEY: str = os.getenv("API_SECRET_KEY", "")
    AI_SESSION_SECRET: str = os.getenv("AI_SESSION_SECRET", "")
    MEDIA_ACCESS_TOKEN: str = os.getenv("MEDIA_ACCESS_TOKEN", "")
    DEPLOYER_SECRET: str = os.getenv("DEPLOYER_SECRET", "")
    DEPLOYER_URL: str = os.getenv("DEPLOYER_URL", "http://deployer:25100/deploy")
    
    
    TEXT_MARCH_8: str = """Привет! С праздником! ❤️🎉
Здесь собрано всё что нас связывает.
Ты можешь добавлять/изменять или удалять любые моменты и в любой момент их пересматривать.
Приятного просмотра! ✨☺️"""

    TEXT_OTHER_DATE: str = """Привет ✨❤️
Тут собраны все (или почти все) наши воспоминания. Ты можешь нажать на каждый из них,
посмотреть, удалить, изменить или даже добавить своё!
Приятного просмотра! 🎁"""
    
    TEXT_FOR_OTHER_ADMINS: str = """Привет, администратор! ✨
Добро пожаловать в панель управления воспоминаниями.
Здесь ты можешь добавлять, редактировать и удалять воспоминания.
Приятного использования! 💫"""
    
    TEXT_FOR_OTHER_USERS: str = """Этот бот имеет ограниченный доступ.
Если хочешь чтобы тебе открыли доступ — обратись к создателю."""
    
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "memories.db")
    HOT_BACKUP_ENABLED: bool = os.getenv("HOT_BACKUP_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
    HOT_BACKUP_PATH: str = os.getenv("HOT_BACKUP_PATH", "")
    DATE_MET: Optional[date] = None
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
    
    def __post_init__(self):
        os.makedirs(self.MEDIA_FOLDER, exist_ok=True)

        if self.SITE_OPEN_DATE is None:
            self.SITE_OPEN_DATE = date(2026, 2, 8)

        # Standardize single canonical database location based on environment
        import logging
        is_docker = os.path.exists("/.dockerenv")
        canonical_host_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data/memories.db"))
        
        # If the host path is exactly the container path (unlikely but possible), they match.
        # Otherwise, if we're in Docker, use /app/data, if on host, use the absolute host path.
        if is_docker:
            resolved_path = "/app/data/memories.db"
        else:
            resolved_path = canonical_host_path
            
        self.DATABASE_PATH = resolved_path

        # Logging output for audit verification
        print(f"Database path: {self.DATABASE_PATH}")
        
        # Check for ghost databases and issue a warning
        potential_ghosts = [
            "/app/data/memories.db",
            "/root/KsyshaTest/data/memories.db",
            "/workspace/data/memories.db",
            canonical_host_path
        ]
        
        found_dbs = []
        for p in set(potential_ghosts):
            try:
                if os.path.exists(p) and os.path.isfile(p):
                    found_dbs.append(p)
            except Exception:
                pass
                
        if len(found_dbs) > 1:
            details = []
            for p in sorted(found_dbs):
                try:
                    st = os.stat(p)
                    details.append({
                        "path": p,
                        "size": int(st.st_size),
                        "mtime": int(st.st_mtime),
                        "sha256": self._file_sha256(p),
                    })
                except Exception:
                    details.append({"path": p, "error": "stat_failed"})

            unique_fingerprints = {
                (d.get("size"), d.get("sha256"))
                for d in details
                if d.get("sha256")
            }

            if len(unique_fingerprints) > 1:
                msg = (
                    "CRITICAL: Multiple divergent memories.db files detected. "
                    f"Canonical DATABASE_PATH={self.DATABASE_PATH}. Details={details}."
                )
                print(msg)
                logging.critical(msg)
            else:
                msg = (
                    "INFO: Multiple memories.db paths detected but contents match. "
                    f"Canonical DATABASE_PATH={self.DATABASE_PATH}. Details={details}"
                )
                print(msg)
                logging.warning(msg)

        self.CATEGORIES = {
            "important_moments": {
                "title": "💫 Важные моменты",
                "description": "Самые значимые события в нашей истории ❤️",
                "emoji": "💫"
            },
            "memories": {
                "title": "📖 Воспоминания",
                "description": "Все то что я вспомнил и что тебе было бы тоже хорошо напомнить ❤️",
                "emoji": "📖"
            },
            "important_dates": {
                "title": "📅 Важные даты",
                "description": "Даты которые стоит помнить и отмечать вместе 🎉",
                "emoji": "📅"
            }
        }

config = Config()
