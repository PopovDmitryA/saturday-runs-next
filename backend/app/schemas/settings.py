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


class PrivacySettingsResponse(BaseModel):
    enabled: bool
    description: str = (
        "По умолчанию ваш профиль виден другим участникам: вас можно будет найти в общем поиске, "
        "а в рейтингах появится ссылка на профиль. Если включить приватный профиль, вы не будете "
        "отображаться в поиске по участникам и не попадёте в выдачу для других пользователей; "
        "в рейтингах ссылка на ваш профиль показываться не будет. Отключить приватность можно в любой момент."
    )


class PrivacySettingsUpdateRequest(BaseModel):
    enabled: bool


class HomeLocationCandidateResponse(BaseModel):
    model_config = {"from_attributes": True}

    catalog_identity_key: str
    name: str
    city: str | None = None
    region: str | None = None
    run_count: int = 0
    volunteer_count: int = 0
    platform_codes: list[str] = Field(default_factory=list)


class HomeLocationResponse(BaseModel):
    location: HomeLocationCandidateResponse | None = None
    is_auto: bool = True


class HomeLocationUpdateRequest(BaseModel):
    catalog_identity_key: str | None = None
