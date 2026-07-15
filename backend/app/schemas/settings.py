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


class ProfileSlugResponse(BaseModel):
    slug: str | None = None
    public_url: str | None = None
    min_length: int
    max_length: int


class ProfileSlugCheckResponse(BaseModel):
    normalized: str
    available: bool
    reason: str | None = None


class ProfileSlugUpdateRequest(BaseModel):
    # None или пустая строка — очистить ссылку.
    slug: str | None = None


class HistoryMilestoneKindSetting(BaseModel):
    kind: str
    label: str
    description: str
    enabled: bool


class HistoryMilestoneSettingsResponse(BaseModel):
    description: str = (
        "Выберите, какие виды вех показывать в вашей «Моей истории». Снятая галочка "
        "убирает вид из вашего таймлайна (и с публичного профиля, если он открыт). "
        "На подсчёт остальных вех это не влияет — вернуть можно в любой момент."
    )
    kinds: list[HistoryMilestoneKindSetting] = Field(default_factory=list)


class HistoryMilestoneKindUpdateRequest(BaseModel):
    enabled: bool
