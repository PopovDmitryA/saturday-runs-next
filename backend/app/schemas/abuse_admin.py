from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AbuseIpBlockItem(BaseModel):
    ip: str
    ttl_seconds: int | None = None
    score: int = 0
    source: str
    reason: str = ""
    created_at: datetime | None = None
    created_by: str | None = None


class AbuseTelegramBanItem(BaseModel):
    telegram_id: int
    telegram_username: str | None = None
    display_name: str | None = None
    last_ip: str | None = None
    ttl_seconds: int | None = None
    source: str
    reason: str = ""
    created_at: datetime | None = None
    created_by: str | None = None


class SignupBlockItem(BaseModel):
    """Отказ в создании нового профиля: сработал суточный лимит регистраций."""

    ip: str = ""
    device_ref: str = ""
    reason: str = ""
    provider: str = ""
    created_at: datetime | None = None


class AbuseBlockListResponse(BaseModel):
    ip_blocks: list[AbuseIpBlockItem]
    telegram_bans: list[AbuseTelegramBanItem]
    signup_blocks: list[SignupBlockItem] = Field(default_factory=list)


class AbuseBanCreateRequest(BaseModel):
    target: str = Field(min_length=1, max_length=128)
    duration_seconds: int = Field(default=86400, ge=60, le=315_360_000)
    reason: str = Field(default="", max_length=500)
    ban_ip: bool = True
    ban_account: bool = True


class AbuseBanCreateResponse(BaseModel):
    created: list[dict[str, object]]
    label: str


class AbuseMessageResponse(BaseModel):
    message: str
