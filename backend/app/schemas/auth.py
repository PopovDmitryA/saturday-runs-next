from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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


class UserResponse(BaseModel):
    id: UUID
    telegram_id: int | None = None
    telegram_username: str | None = None
    telegram_first_name: str | None = None
    telegram_last_name: str | None = None
    display_name: str | None = None
    display_name_customized: bool = False
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


class UserDisplayNameUpdate(BaseModel):
    display_name: str = Field(default="", max_length=128)


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


class EmailCodeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # Согласие на обработку данных — как и у OAuth, без него вход не начинается.
    consent: bool = False


class EmailCodeResponse(BaseModel):
    expires_in: int


class EmailVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    code: str = Field(min_length=4, max_length=12)


class EmailVerifyResponse(BaseModel):
    redirect: str


class EmailLinkResponse(BaseModel):
    # merge_token не пуст, когда ящиком владеет другой профиль: тогда человек
    # решает, объединять ли их, — как при привязке VK или Яндекса.
    merge_token: str | None = None
