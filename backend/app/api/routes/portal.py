from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, get_current_user
from app.core.rate_limit import check_rate_limit
from app.db.session import get_db
from app.models import User
from app.schemas.portal import PortalHomeResponse, PortalMeResponse, PortalTeaserResponse
from app.services.portal_home_service import build_portal_home
from app.services.portal_me_service import build_portal_me
from app.services.portal_teaser_service import build_portal_teaser

router = APIRouter(prefix="/portal", tags=["portal"])


@router.get("/home", response_model=PortalHomeResponse)
def get_portal_home(db: Session = Depends(get_db)) -> PortalHomeResponse:
    """Публичные агрегаты для главной портала (без авторизации)."""
    return build_portal_home(db)


@router.get("/me", response_model=PortalMeResponse)
def get_portal_me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PortalMeResponse:
    """Личная плашка главной: последняя пробежка и живая серия суббот.

    Отдельно от /portal/home: та кэшируется и одна на всех, эта персональная.
    Фронт грузит её вторым запросом, поэтому анонимная главная от неё не
    зависит и не ждёт.
    """
    return PortalMeResponse.model_validate(build_portal_me(db, user))


@router.get("/teaser", response_model=PortalTeaserResponse)
def get_portal_teaser(
    request: Request,
    platform: str = Query(min_length=1, max_length=32),
    athlete_id: str = Query(min_length=1, max_length=32),
    db: Session = Depends(get_db),
) -> PortalTeaserResponse:
    """Тизер Т10: предпросмотр карточки по ID системы, анонимно, из нашей БД.

    Rate-limit жёстче обычного: эндпоинт позволяет перебирать чужие ID,
    30 запросов в минуту с IP достаточно для живого человека.
    """
    client_ip = get_client_ip(request)
    if client_ip != "unknown" and not check_rate_limit(f"portal:teaser:ip:{client_ip}", 30, 60):
        raise HTTPException(status_code=429, detail="Слишком много запросов, попробуйте через минуту.")
    teaser = build_portal_teaser(db, platform, athlete_id)
    if teaser is None:
        raise HTTPException(status_code=404, detail="Участник не найден или у него ещё нет финишей.")
    return PortalTeaserResponse.model_validate(teaser)
