import enum
from pathlib import Path
from tempfile import gettempdir
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL

TEMP_DIR = Path(gettempdir())


class LogLevel(str, enum.Enum):
    """Possible log levels."""

    NOTSET = "NOTSET"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"


class Settings(BaseSettings):
    """
    Application settings.

    These parameters can be configured
    with environment variables.
    """

    host: str = "127.0.0.1"
    port: int = 8000
    # quantity of workers for uvicorn
    workers_count: int = 1
    # Enable uvicorn reloading
    reload: bool = False

    # Current environment
    environment: str = "local"

    log_level: LogLevel = LogLevel.INFO
    # Variables for the database
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "saver_backend"
    db_pass: str = "saver_backend"
    db_base: str = "saver_backend"
    db_echo: bool = False

    # Variables for Redis
    redis_host: str = "saver_backend-redis"
    redis_port: int = 6379
    redis_user: Optional[str] = None
    redis_pass: Optional[str] = None
    redis_base: Optional[int] = None

    # Sentry's configuration.
    sentry_dsn: Optional[str] = None
    sentry_sample_rate: float = 1.0

    # Telegram bot configuration.
    telegram_bot_token: str = "42:TOKEN"
    telegram_secret_token: str = "verysecrettoken"
    telegram_filename_sufix: str = " [@saver]"
    subscription_channels: list[str] = ["channel_username"]
    admin_chat_id: int = -4816121008
    instagram_account: str = "username:password"

    # VK Configuration
    vk_service_token: list[str] = ["vk_token"]

    # Yandex music Configuration
    ym_token: list[str] = ["ym_token"]

    # Telegram bot API URL
    telegram_bot_api_url: str = "http://bot-api:8081"

    # Webhook Settings
    webhook_base_url: str = "http://saver_backend-api:8000/api/webhook"
    webhook_telegram_path: str = "/telegram"

    # Downloader settings
    proxies: list[str] = []
    proxies_ru: list[str] = []

    # chrome headless settings
    chrome_host: str = "saver_backend-chrome"
    chrome_port: int = 9222
    _chrome_cdp_url: Optional[str] = None

    @property
    def webhook_telegram_url(self) -> str:
        """
        URL for telegram webhook.

        :return: URL for telegram webhook.
        """
        return self.webhook_base_url + self.webhook_telegram_path

    @property
    def db_url(self) -> URL:
        """
        Assemble database URL from settings.

        :return: database URL.
        """
        return URL.build(
            scheme="postgresql+asyncpg",
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_pass,
            path=f"/{self.db_base}",
        )

    @property
    def redis_url(self) -> URL:
        """
        Assemble REDIS URL from settings.

        :return: redis URL.
        """
        path = ""
        if self.redis_base is not None:
            path = f"/{self.redis_base}"
        return URL.build(
            scheme="redis",
            host=self.redis_host,
            port=self.redis_port,
            user=self.redis_user,
            password=self.redis_pass,
            path=path,
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="SAVER_BACKEND_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def chrome_cdp_url(self) -> str:
        """
        Assemble chrome CDP URL from settings.

        :return: chrome CDP URL.
        """
        if self._chrome_cdp_url is None:
            import socket

            def get_chrome_ip(chrome_host: str = "saver_backend-chrome") -> str:
                """
                Get IP of chrome container.

                :param chrome_host:
                :return: ip of chrome container.
                """
                try:
                    # try resolve host name
                    return socket.gethostbyname(chrome_host)
                except socket.gaierror:
                    return "172.18.0.2"

            chrome_ip = get_chrome_ip(self.chrome_host)
            self._chrome_cdp_url = f"http://{chrome_ip}:{self.chrome_port}"
        return self._chrome_cdp_url


settings = Settings()
