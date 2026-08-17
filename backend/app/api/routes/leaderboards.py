from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.config import Settings, get_settings
from app.core.admin import is_admin_user
from app.db.session import get_db
from app.models import User
from app.schemas.leaderboards import (
    LeaderboardResponse,
    MyLeaderboardRowResponse,
    TouristMapResponse,
    VolunteerRoleCatalogResponse,
    VolunteerRoleItem,
)
from app.services.leaderboard_service import (
    ADMIN_ONLY_METRICS,
    LEADERBOARD_GENDERS,
    LEADERBOARD_METRICS,
    MAX_MIN_VISITS,
    ROLE_FILTER_METRICS,
    TOURIST_MAP_METRICS,
    LeaderboardMetric,
    get_leaderboard,
    get_my_leaderboard_row,
    get_tourist_map,
    used_role_keys,
)
from app.volunteer_role_taxonomy import (
    CANONICAL_ROLE_LABELS,
    ROLE_PRESETS,
    role_is_on_site,
    role_is_runnable,
)

router = APIRouter(prefix="/leaderboards", tags=["leaderboards"])


def _validate_metric(
    metric: str, viewer: User | None = None, settings: Settings | None = None
) -> LeaderboardMetric:
    if metric not in LEADERBOARD_METRICS:
        raise HTTPException(status_code=404, detail="Неизвестный рейтинг")
    # Ещё не открытый рейтинг ведёт себя ровно как несуществующий: пока данные
    # не выверены, о нём не должно быть видно даже того, что он есть.
    if metric in ADMIN_ONLY_METRICS and not (
        viewer is not None and settings is not None and is_admin_user(viewer, settings)
    ):
        raise HTTPException(status_code=404, detail="Неизвестный рейтинг")
    return metric  # type: ignore[return-value]


def _validate_gender(gender: str) -> str:
    # Незнакомый пол молча трактуем как «all» — сервис сам игнорирует разрез у
    # метрик без женского зачёта (см. _normalize_gender), так что 400 тут был бы
    # избыточен. Сюда же попадает gender=male из старых ссылок: мужского зачёта
    # больше нет, и старая ссылка открывает абсолют, а не ошибку.
    return gender if gender in LEADERBOARD_GENDERS else "all"


# Справочник ролей для модалки «какие роли считать». Отдаём вместе с
# признаками, чтобы витрина сама рисовала группы и пресеты и не держала
# собственную копию разметки.
@router.get("/volunteer-roles/catalog", response_model=VolunteerRoleCatalogResponse)
def volunteer_role_catalog(
    db: Annotated[Session, Depends(get_db)],
) -> VolunteerRoleCatalogResponse:
    # Показываем только роли, которыми реально выходили: в таксономии есть
    # объявленные системами, но ни разу не использованные — в списке фильтра
    # они лишь путают.
    used = used_role_keys(db)
    return VolunteerRoleCatalogResponse(
        presets=list(ROLE_PRESETS),
        metrics=list(ROLE_FILTER_METRICS),
        roles=[
            VolunteerRoleItem(
                key=key,
                label=label,
                on_site=role_is_on_site(key),
                runnable=role_is_runnable(key),
            )
            for key, label in CANONICAL_ROLE_LABELS.items()
            if key in used
        ],
    )


# Таблицы рейтингов открыты БЕЗ логина (решение Дмитрия 25.07.2026:
# локации и рейтинги — публичная витрина сайта; до этого с 16.07.2026
# раздел был только для залогиненных).
@router.get("/{metric}", response_model=LeaderboardResponse)
def leaderboard(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    viewer: Annotated[User | None, Depends(get_optional_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    gender: str = "all",
    # Порог визитов есть только у туристических рейтингов; фильтр «по одной
    # системе» — у всех, но набор систем зависит от рейтинга и зачёта.
    # Неприменимое значение сервис молча приводит к базовому (см.
    # _normalize_min_visits / _normalize_platform_filter), 400 тут был бы избыточен.
    min_visits: Annotated[int, Query(ge=1, le=MAX_MIN_VISITS)] = 1,
    platform: str = "all",
    # Единица зачёта туристических рейтингов: площадки / города / регионы.
    count_by: str = "locations",
    # Какие волонтёрские роли считать. Пустой список = все роли; неизвестные
    # ключи и неприменимые метрики сервис отбрасывает сам.
    roles: Annotated[list[str] | None, Query()] = None,
    # Спрятать участников с неочевидной домашней локацией (только у дальности).
    hide_ambiguous_home: bool = False,
) -> LeaderboardResponse:
    payload = get_leaderboard(
        db,
        _validate_metric(metric, viewer, settings),
        _validate_gender(gender),
        limit=limit,
        min_visits=min_visits,
        platform=platform,
        count_by=count_by,
        roles=roles,
        hide_ambiguous_home=hide_ambiguous_home,
    )
    return LeaderboardResponse.model_validate(payload)


# Карта туристов — спойлер туристических рейтингов: числа у точек карты и
# светофоры «был / не был» в таблице. Публичная, как и сама таблица.
@router.get("/{metric}/tourist-map", response_model=TouristMapResponse)
def tourist_map(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    min_visits: Annotated[int, Query(ge=1, le=MAX_MIN_VISITS)] = 1,
    platform: str = "all",
    count_by: str = "locations",
    roles: Annotated[list[str] | None, Query()] = None,
    # Ключ площадки (тот же catalog_identity_key, что у точек карты): с ним
    # ответ несёт ещё и визиты этой площадки по строкам таблицы. Без него —
    # только числа у точек: матрица целиком по проводу не ездит.
    location_key: str | None = None,
) -> TouristMapResponse:
    validated = _validate_metric(metric)
    if validated not in TOURIST_MAP_METRICS:
        raise HTTPException(status_code=404, detail="У этого рейтинга нет карты туристов")
    payload = get_tourist_map(
        db,
        validated,
        min_visits=min_visits,
        platform=platform,
        count_by=count_by,
        roles=roles,
        location_key=location_key,
    )
    return TouristMapResponse.model_validate(payload)


# Строка «Вы» — только своя, поэтому логин обязателен и здесь.
@router.get("/{metric}/me", response_model=MyLeaderboardRowResponse)
def my_leaderboard_row(
    metric: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
    gender: str = "all",
    min_visits: Annotated[int, Query(ge=1, le=MAX_MIN_VISITS)] = 1,
    platform: str = "all",
    count_by: str = "locations",
) -> MyLeaderboardRowResponse:
    payload = get_my_leaderboard_row(
        db,
        _validate_metric(metric, user, settings),
        user,
        _validate_gender(gender),
        min_visits,
        platform,
        count_by,
    )
    return MyLeaderboardRowResponse.model_validate(payload)
