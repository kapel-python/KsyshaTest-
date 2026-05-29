import os
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
    
    def __post_init__(self):
        os.makedirs(self.MEDIA_FOLDER, exist_ok=True)

        if self.SITE_OPEN_DATE is None:
            self.SITE_OPEN_DATE = date(2026, 2, 8)

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
