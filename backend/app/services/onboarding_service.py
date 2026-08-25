"""Онбординг первичного входа: куда вести пользователя после логина.

Нового пользователя (и любого без привязок, кто ещё не проходил /welcome)
после входа ведём на страницу онбординга с поиском по ФИО. Кнопки «Готово» и
«Пропустить» ставят users.onboarding_completed_at — больше не предлагаем.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.models import PlatformLink, User

ONBOARDING_TARGET = "welcome"
DASHBOARD_TARGET = "dashboard"


def post_login_redirect_target(db: Session, user_id: UUID) -> str:
    user = db.query(User).filter(User.id == user_id).one_or_none()
    if user is None or user.onboarding_completed_at is not None:
        return DASHBOARD_TARGET
    has_links = (
        db.query(PlatformLink.id).filter(PlatformLink.user_id == user_id).first() is not None
    )
    return DASHBOARD_TARGET if has_links else ONBOARDING_TARGET


def complete_onboarding(db: Session, user: User) -> None:
    if user.onboarding_completed_at is None:
        user.onboarding_completed_at = datetime.now(timezone.utc)
        db.commit()


ONBOARDING_PLATFORM_CODES = frozenset({"five_verst", "s95", "parkrun", "runpark"})


class OnboardingError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def set_platform_no_account(db: Session, user: User, platform_code: str, no_account: bool) -> list[str]:
    """Отметка «в этой системе у меня нет аккаунта» — честное закрытие прогресса без привязки."""
    if platform_code not in ONBOARDING_PLATFORM_CODES:
        raise OnboardingError("Неизвестная платформа", 404)
    current = set(user.onboarding_no_account_platforms or [])
    if no_account:
        current.add(platform_code)
    else:
        current.discard(platform_code)
    user.onboarding_no_account_platforms = sorted(current)
    db.commit()
    return user.onboarding_no_account_platforms
