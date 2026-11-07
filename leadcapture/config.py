from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "change-me-in-production"
DEFAULT_SALT = "change-me-too"


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables or a `.env` file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    database_url: str = "sqlite+aiosqlite:///./leadcapture.db"
    secret_key: str = DEFAULT_SECRET
    ip_hash_salt: str = DEFAULT_SALT
    trust_proxy: bool = False
    max_body_bytes: int = 256 * 1024
    form_token_ttl_seconds: int = 86400
    docs_enabled: bool = True
    export_max_rows: int = 50000

    log_level: str = "INFO"
    log_format: str = "json"

    redis_url: str | None = None
    rate_limit_window_seconds: int = 60

    outbox_max_attempts: int = 6
    outbox_backoff_base_seconds: float = 30.0
    outbox_backoff_max_seconds: float = 3600.0
    outbox_poll_seconds: float = 15.0
    outbox_worker_enabled: bool = True
    outbox_lock_timeout_seconds: float = 300.0
    http_timeout_seconds: float = 10.0

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = "leads@example.com"
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False

    telegram_bot_token: str = ""

    @property
    def async_database_url(self) -> str:
        """Normalise common URL spellings to async SQLAlchemy drivers."""
        url = self.database_url
        if url.startswith("postgres://"):
            return "postgresql+asyncpg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        if url.startswith("sqlite://"):
            return "sqlite+aiosqlite://" + url[len("sqlite://") :]
        return url

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def assert_safe_for_production(self) -> None:
        if not self.is_production:
            return
        if self.secret_key == DEFAULT_SECRET or self.ip_hash_salt == DEFAULT_SALT:
            raise RuntimeError(
                "SECRET_KEY and IP_HASH_SALT must be changed from their defaults in production"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
