from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class LoginRequestResponse(BaseModel):
    request_token: str
    bot_url: str
    expires_in: int


class LoginRequestStatusResponse(BaseModel):
    status: str
    merge_token: str | None = None


class BotConfirmRequest(BaseModel):
    request_token: str
    telegram_id: int
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    telegram_chat_id: int
    consent_accepted: bool = False


class BotConfirmResponse(BaseModel):
    magic_link: str


class BotLoginStatusRequest(BaseModel):
    telegram_id: int


class BotLoginStatusResponse(BaseModel):
    needs_consent: bool


class AuthIdentityResponse(BaseModel):
    provider: str
    external_id: str
    display_name: str | None = None
    email: str | None = None
    linked_at: datetime
    label: str


class DisplayNameSuggestion(BaseModel):
    """Алгоритм расходится с текущим выбором — предлагаем сменить источник."""

    name: str
    platform_code: str | None = None
    source_title: str


class UserResponse(BaseModel):
    id: UUID
    telegram_id: int | None = None
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    display_name: str | None = None
    # Как показывать имя: "auto" — полное, "initial" — «Иван П.». Свободного
    # ввода нет — имя считается из профилей беговых систем.
    display_name_style: str = "auto"
    # Прежнее имя для одноразовой плашки «имя теперь из профиля». NULL — не нужна.
    display_name_notice: str | None = None
    # Алгоритм расходится с зафиксированным источником: имя молча не меняем, а
    # показываем баннер со ссылкой в настройки. NULL — расхождения нет.
    display_name_suggestion: DisplayNameSuggestion | None = None
    consent_accepted: bool = False
    is_admin: bool = False
    avatar_url: str | None = None
    # Оригинал аватарки без пережатия — открывается по клику на аватарку.
    # NULL у аватарок, загруженных до появления оригиналов.
    avatar_full_url: str | None = None
    # Публичный адрес участника: /users/{public_slug или serial_id}. Нужен
    # фронту, чтобы кабинет жил на собственном публичном URL (26.07.2026).
    serial_id: int | None = None
    public_slug: str | None = None
    auth_identities: list[AuthIdentityResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("display_name_style", mode="before")
    @classmethod
    def _default_style(cls, value: object) -> object:
        # У ещё не записанного в БД пользователя server_default не сработал, и
        # в поле лежит None. Для ответа это то же самое, что «auto».
        return "auto" if value is None else value


class DisplayNamePreferencesUpdate(BaseModel):
    """Выбор из селектора имени: стиль и, при желании, система-источник."""

    style: str = Field(default="auto", max_length=16)
    # Код системы («five_verst», «s95», …), из профиля которой брать имя.
    # None — авто-приоритет: 5 вёрст → S95 → RunPark → parkrun.
    platform_code: str | None = Field(default=None, max_length=32)


class DisplayNameSourceItem(BaseModel):
    platform_code: str | None = None
    source_title: str
    name: str
    name_initial: str
    last_run: str | None = None


class DisplayNameOptionsResponse(BaseModel):
    current: str | None = None
    style: str = "auto"
    # Зафиксированная система-источник; None — выбирается автоматически.
    source: str | None = None
    # Источник выбран человеком, а не алгоритмом.
    source_manual: bool = False
    auto_name: str | None = None
    auto_source: str | None = None
    suggestion: DisplayNameSuggestion | None = None
    notice: str | None = None
    sources: list[DisplayNameSourceItem] = Field(default_factory=list)


class MessageResponse(BaseModel):
    message: str = Field(default="ok")


class PlatformLinkPreviewItem(BaseModel):
    platform_code: str
    external_user_id: str


class MergePreviewResponse(BaseModel):
    merge_token: str
    merged_user_id: str
    platform_links_to_reset: list[PlatformLinkPreviewItem]
    conflicting_platform_codes: list[str]
    warning: str


class MergeConfirmRequest(BaseModel):
    merge_token: str


class OAuthFinishRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=8)
    device_id: str | None = None
    payload: str | None = None


class OAuthFinishResponse(BaseModel):
    redirect: str
    merge_token: str | None = None
