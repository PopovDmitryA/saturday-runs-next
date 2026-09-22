from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationChannelState(BaseModel):
    channel: str
    title: str
    # Канал вообще настроен на сайте (есть бот / токен группы / SMTP).
    available: bool
    # У человека есть привязка, из которой канал можно включить.
    linked: bool
    # Человек включил уведомления на этом способе входа.
    enabled: bool
    # Проверка «дойдёт ли»: True/False, None — не проверяли (нет привязки).
    deliverable: bool | None = None
    # Что нужно сделать, если не дойдёт (красным в настройках).
    problem: str | None = None
    label: str | None = None
    # Откуда взят адрес почты: email | yandex.
    email_source: str | None = None
    allow_url: str | None = None
    bot_url: str | None = None
    last_error: str | None = None


class NotificationKindState(BaseModel):
    code: str
    title: str
    description: str
    enabled: bool
    available: bool


class NotificationSettingsState(BaseModel):
    enabled: bool
    primary_channel: str | None = None
    channels: list[NotificationChannelState] = Field(default_factory=list)
    kinds: list[NotificationKindState] = Field(default_factory=list)


class NotificationSettingsUpdate(BaseModel):
    # Отсутствие поля — не менять; null — сбросить на порядок по умолчанию.
    primary_channel: str | None = None
    kinds: dict[str, bool] | None = None
    model_config = {"extra": "forbid"}


class NotificationChannelToggle(BaseModel):
    enabled: bool


class NotificationCheckResponse(BaseModel):
    ok: bool
    error: str | None = None


class NotificationConnectUrlResponse(BaseModel):
    connect_url: str


class NotificationTestResponse(BaseModel):
    queued: bool
    channels: list[str] = Field(default_factory=list)


class NotificationNudgeState(BaseModel):
    # Показывать ли баннер «Включите уведомления».
    show: bool
    enabled: bool
    linked_channels: list[str] = Field(default_factory=list)


class NotificationEnableResponse(BaseModel):
    # Какой канал включился; None — включать нечего, нужно разрешить боту писать.
    channel: str | None = None
    state: NotificationSettingsState


class NewsletterSettingsResponse(BaseModel):
    enabled: bool
    description: str = (
        "Редкие письма о крупных обновлениях сайта и итогах сезона. Отписаться можно здесь или ссылкой в самом письме."
    )
    # Куда придут письма. None — почта к профилю не привязана, и включать
    # рассылку не на что: сначала нужно добавить почту в «Способах входа».
    email: str | None = None


class NewsletterSettingsUpdateRequest(BaseModel):
    enabled: bool


class BotNotifyConfirmRequest(BaseModel):
    token: str
    telegram_id: int
    telegram_chat_id: int


class BotNotifyConfirmResponse(BaseModel):
    ok: bool
    message: str


# --- админка: журнал доставок ------------------------------------------------


class AdminNotificationCount(BaseModel):
    key: str
    count: int


class AdminNotificationDelivery(BaseModel):
    id: UUID
    user_serial_id: int | None = None
    user_label: str
    kind: str
    status: str
    channel: str | None = None
    attempts: int
    title: str
    error: str | None = None
    created_at: datetime
    sent_at: datetime | None = None


class AdminNotificationsResponse(BaseModel):
    period_days: int
    generated_at: datetime
    total: int
    by_status: list[AdminNotificationCount] = Field(default_factory=list)
    by_kind: list[AdminNotificationCount] = Field(default_factory=list)
    by_channel: list[AdminNotificationCount] = Field(default_factory=list)
    # Сколько людей включили уведомления, по каналам.
    subscribers_by_channel: list[AdminNotificationCount] = Field(default_factory=list)
    items: list[AdminNotificationDelivery] = Field(default_factory=list)
    items_total: int
    limit: int
    offset: int
