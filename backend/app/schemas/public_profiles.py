"""Ответы публичного профиля участника (/users/{хендл}) — их видит любой гость.

До 23.10.2026 шапка профиля отдавалась схемой админки
(AdminUserPreviewDashboardResponse), и гостю уезжали почта и способы входа,
Telegram ID и ник, штрихкоды привязок. Здесь — только то, что страница
действительно рисует: имя, аватар, цифры и бейджи систем.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PublicProfileUser(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    avatar_full_url: str | None = None


class PublicProfilePlatformLink(BaseModel):
    """Бейдж системы под именем — ссылка в профиль человека на ней (решение
    Дмитрия 26.09.2026). Поля — ровно то, из чего фронт собирает адрес
    (lib/platformProfileUrl.ts); лишнее сервер оставляет пустым."""

    platform_code: str
    external_user_id: str = ""
    external_url: str = ""
    run_count: int = 0
    volunteer_count: int = 0


class PublicProfileDashboardResponse(BaseModel):
    user: PublicProfileUser
    stats: dict[str, object]
    computed_at: datetime | None = None
    platform_links: list[PublicProfilePlatformLink] = Field(default_factory=list)
