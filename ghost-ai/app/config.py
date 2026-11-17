import re
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    webhook_secret: SecretStr
    encryption_key: SecretStr
    session_secret: SecretStr

    owner_telegram_id: int = 0
    public_base_url: str = ""
    auto_set_webhook: bool = True
    database_url: str = "postgresql+asyncpg://ghost:ghost@db:5432/ghost"

    anthropic_api_key: SecretStr = SecretStr("")
    claude_model: str = "claude-sonnet-5-5"
    claude_max_tokens: int = 1024

    default_retention_days: int = 30
    history_window: int = 30
    humanize_delay: bool = True
    log_level: str = "INFO"

    @field_validator("webhook_secret")
    @classmethod
    def _secret_charset(cls, v: SecretStr) -> SecretStr:
        # Telegram only accepts A-Z a-z 0-9 _ - for secret_token, 1..256 chars
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", v.get_secret_value()):
            raise ValueError("WEBHOOK_SECRET must match [A-Za-z0-9_-]{1,256}")
        return v

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @field_validator("public_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def cookie_secure(self) -> bool:
        return self.public_base_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
