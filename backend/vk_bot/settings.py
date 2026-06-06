from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class VkBotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    vk_bot_group_token: str = ""
    vk_bot_group_id: int = 0
    vk_admin_user_id: int = 0
    vk_bot_internal_secret: str = ""
    api_base_url: str = "http://api:8000"
    redis_url: str = "redis://localhost:6379/0"


def get_vk_bot_settings() -> VkBotSettings:
    return VkBotSettings()


def vk_bot_headers(settings: VkBotSettings) -> dict[str, str]:
    return {"X-Bot-Secret": settings.vk_bot_internal_secret}
