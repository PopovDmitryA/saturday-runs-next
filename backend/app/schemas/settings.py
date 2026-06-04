from __future__ import annotations

from pydantic import BaseModel, Field


class AutoSyncPlatformPreference(BaseModel):
    platform_code: str
    enabled: bool
    linked: bool = False


class AutoSyncSettingsResponse(BaseModel):
    interval_hours: int
    last_login_auto_sync_at: object | None = None
    platforms: list[AutoSyncPlatformPreference] = Field(default_factory=list)


class AutoSyncSettingsUpdateRequest(BaseModel):
    auto_sync_by_platform: dict[str, bool] = Field(default_factory=dict)


class NotificationSettingsResponse(BaseModel):
    enabled: bool
    description: str = (
        "Рассылка через Telegram-бота: новости сервиса «Статистика парковых пробежек», "
        "обновления функций и другие сообщения от проекта. Отключить можно в любой момент."
    )


class NotificationSettingsUpdateRequest(BaseModel):
    enabled: bool
