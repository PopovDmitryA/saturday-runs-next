from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    telegram_bot_token: str
    telegram_bot_internal_secret: str
    api_base_url: str = "http://api:8000"
    app_base_url: str = "http://localhost:8080"
    admin_telegram_id: int = 0
    telegram_admin_chat_id: int = 0
    admin_telegram_proxy_url: str = ""


def bot_headers(settings: BotSettings) -> dict[str, str]:
    return {"X-Bot-Secret": settings.telegram_bot_internal_secret}
