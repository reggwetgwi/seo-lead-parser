from typing import Optional, List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Конфигурация приложения через переменные окружения"""

    # Gemini AI (поддержка нескольких ключей через запятую)
    gemini_api_key: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # HH.ru
    hh_app_name: str = "SEOLeadParser/1.0"
    hh_client_id: Optional[str] = None
    hh_client_secret: Optional[str] = None
    hh_access_token: Optional[str] = None

    # Парсер
    min_score_for_alert: int = 7
    parse_interval_hours: int = 2
    request_timeout: int = 30
    max_retries: int = 3

    # Прокси (опционально)
    proxy_server: Optional[str] = None

    # База данных
    database_url: str = "sqlite+aiosqlite:///leads.db"
    leads_retention_days: int = 30

    # Дашборд
    flask_port: int = 5000
    flask_secret_key: str = "change-me-in-production"

    # Логирование
    log_level: str = "INFO"
    log_file: str = "logs/parser.log"

    @property
    def gemini_api_keys(self) -> List[str]:
        """Получить список API ключей (разделенных запятой)"""
        return [key.strip() for key in self.gemini_api_key.split(",") if key.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
