from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.db.session import get_db
from app.models import User
from app.schemas.challenge_ratings import (
    ChallengeHubResponse,
    ChallengeRatingResponse,
    MyChallengeRatingResponse,
)
from app.services.challenge_rating_service import (
    TOP_LIMIT,
    RatingChallenge,
    get_challenge_hub,
    get_challenge_rating,
    get_my_challenge_row,
    resolve,
)

router = APIRouter(prefix="/challenge-ratings", tags=["challenge-ratings"])


def _challenge(slug: str) -> RatingChallenge:
    item = resolve(slug)
    if item is None:
        raise HTTPException(status_code=404, detail="Неизвестный рейтинг")
    return item


# Карточки хаба /ratings — все челленджи одним запросом, а не девятью.
@router.get("/hub", response_model=ChallengeHubResponse)
def challenge_hub(
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    platform: str = "all",
) -> ChallengeHubResponse:
    return ChallengeHubResponse.model_validate(get_challenge_hub(db, viewer, platform))


# Таблицы открыты без логина — как остальные рейтинги (решение Дмитрия 25.07.2026).
@router.get("/{slug}", response_model=ChallengeRatingResponse)
def challenge_rating(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    # Неизвестную систему сервис молча приводит к общему зачёту: старая или
    # набранная руками ссылка должна открывать рейтинг, а не ошибку.
    platform: str = "all",
    # Вариант счёта (серия только по пробежкам, «Нумератор ПРО»); неизвестный —
    # первый вариант челленджа.
    variant: str | None = None,
    # all — все участники, active — только выходившие на старт за последний год.
    activity: str = "all",
    limit: Annotated[int | None, Query(ge=1, le=TOP_LIMIT)] = None,
) -> ChallengeRatingResponse:
    payload = get_challenge_rating(db, _challenge(slug), platform, limit, variant, activity)
    return ChallengeRatingResponse.model_validate(payload)


# Строка «Вы» — только своя, поэтому логин обязателен.
@router.get("/{slug}/me", response_model=MyChallengeRatingResponse)
def my_challenge_row(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    platform: str = "all",
    variant: str | None = None,
    activity: str = "all",
) -> MyChallengeRatingResponse:
    payload = get_my_challenge_row(db, _challenge(slug), user, platform, variant, activity)
    return MyChallengeRatingResponse.model_validate(payload)
