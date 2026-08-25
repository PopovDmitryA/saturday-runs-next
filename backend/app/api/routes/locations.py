from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_optional_user
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import HomeDistanceDetailResponse
from app.schemas.locations import (
    CatalogLocationsTableResponse,
    LastResultsResponse,
    LocationEventsResponse,
    LocationLeadersResponse,
    LocationPageResponse,
    LocationPersonalStatsResponse,
    LocationProtocolResponse,
    LocationsIndexResponse,
    MapGeoPingRequest,
    MapLocationsResponse,
    MapPointContextResponse,
    UniqueLocationsDetailResponse,
)
from app.services.home_distance_service import build_home_distance_detail
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_catalog_map_locations, list_user_visited_map_locations
from app.services.location_page_service import (
    build_last_results,
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_location_personal_stats,
    build_locations_index,
)
from app.services.location_protocol_service import build_location_protocol
from app.services.map_point_context_service import build_map_point_context
from app.services.user_geo_ping_service import record_geo_ping
from app.services.user_unique_locations_detail import build_user_unique_location_details

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/locations", tags=["locations"])

# Каталог, страницы локаций и журнал протоколов открыты БЕЗ логина
# (решение Дмитрия 25.07.2026: локации и рейтинги — публичная витрина сайта).
# Логин нужен только личным данным: /visited/* и /page/{slug}/me.


@router.get("/index", response_model=LocationsIndexResponse)
def locations_index(
    db: Annotated[Session, Depends(get_db)],
) -> LocationsIndexResponse:
    """Каталог локаций — входная точка раздела «Локации»."""
    payload = build_locations_index(db)
    return LocationsIndexResponse.model_validate(payload)


@router.get("/last-results", response_model=LastResultsResponse)
def locations_last_results(
    db: Annotated[Session, Depends(get_db)],
) -> LastResultsResponse:
    """«Результаты последней субботы»: последний старт каждой локации по всем системам."""
    payload = build_last_results(db)
    return LastResultsResponse.model_validate(payload)


@router.get("/page/{slug}", response_model=LocationPageResponse)
def location_page(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
) -> LocationPageResponse:
    """Страница локации: сводные цифры, таймлайн систем, гистограмма, инфо-карточка."""
    payload = build_location_page(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationPageResponse.model_validate(payload)


@router.get("/page/{slug}/leaders", response_model=LocationLeadersResponse)
def location_leaders(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
) -> LocationLeadersResponse:
    """Рейтинги внутри локации: топ по пробежкам и волонтёрствам."""
    payload = build_location_leaders(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationLeadersResponse.model_validate(payload)


@router.get("/page/{slug}/events", response_model=LocationEventsResponse)
def location_events(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
) -> LocationEventsResponse:
    """Журнал протоколов локации сквозь все системы."""
    payload = build_location_events(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationEventsResponse.model_validate(payload)


@router.get("/page/{slug}/protocol/{platform_code}/{event_date}", response_model=LocationProtocolResponse)
def location_protocol(
    slug: str,
    platform_code: str,
    event_date: date,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> LocationProtocolResponse:
    """Протокол одного старта: состав забега, места по полу и возрастной группе, волонтёры.

    Открыт без логина, как и вся витрина локаций. Логин добавляет ровно одно —
    подсветку своей строки в протоколе.
    """
    payload = build_location_protocol(db, slug, platform_code, event_date, viewer=user)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Протокол не найден")
    return LocationProtocolResponse.model_validate(payload)


@router.get("/page/{slug}/me", response_model=LocationPersonalStatsResponse)
def location_personal_stats(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> LocationPersonalStatsResponse:
    """Личная статистика на локации — блок «Вы на этой локации» (только свои данные)."""
    payload = build_location_personal_stats(db, user, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationPersonalStatsResponse.model_validate(payload)


@router.get("/visited/home-distance", response_model=HomeDistanceDetailResponse)
def visited_home_distance(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> HomeDistanceDetailResponse:
    """Детали плитки «Дальность от дома»: посещённые площадки с их вкладом в
    зачёт и действующие площадки, где человек ещё не был."""
    payload = build_home_distance_detail(db, user, include_test_events=include_test)
    return HomeDistanceDetailResponse.model_validate(payload)


@router.get("/visited/map", response_model=MapLocationsResponse)
def visited_locations_map(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> MapLocationsResponse:
    payload = list_user_visited_map_locations(db, user.id, include_test_events=include_test)
    return MapLocationsResponse.model_validate(payload)


@router.get("/visited/detail", response_model=UniqueLocationsDetailResponse)
def visited_locations_detail(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> UniqueLocationsDetailResponse:
    payload = build_user_unique_location_details(db, user.id, include_test_events=include_test)
    return UniqueLocationsDetailResponse.model_validate(payload)


@router.get("/catalog/map", response_model=MapLocationsResponse)
def catalog_locations_map(
    db: Annotated[Session, Depends(get_db)],
) -> MapLocationsResponse:
    payload = list_catalog_map_locations(db)
    return MapLocationsResponse.model_validate(payload)


@router.get("/map/point-context", response_model=MapPointContextResponse)
def map_point_context(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
    identity_key: Annotated[str, Query()],
) -> MapPointContextResponse:
    """Подробности одной точки карты: номер ближайшего старта, даст ли он +1 в
    «Нумераторе» и сколько отсюда до дома.

    Отдельным запросом по клику, а не полем в /catalog/map: на карте каталога
    почти три тысячи точек, и считать это для всех разом незачем. Аноним видит
    только прогноз номера — остальное личное.
    """
    payload = build_map_point_context(db, user, identity_key)
    return MapPointContextResponse.model_validate(payload)


@router.post("/map/geo-ping", status_code=status.HTTP_204_NO_CONTENT)
def map_geo_ping(
    payload: MapGeoPingRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
) -> None:
    """Огрублённая отметка «где был участник, когда открывал карту».

    Приходит с той же карты, что показывает точку «вы здесь», и только если
    человек сам разрешил браузеру определять положение. Аноним — молча мимо:
    отметку не к чему привязать. Ошибки внутрь не пускаем, это запись на
    обочине — карта не должна ломаться из-за неё.
    """
    if user is None:
        return
    try:
        record_geo_ping(
            db,
            user,
            latitude=payload.latitude,
            longitude=payload.longitude,
            accuracy_m=payload.accuracy_m,
        )
    except Exception:  # noqa: BLE001 — фоновая запись не должна ронять ответ
        logger.warning("Не удалось записать геоотметку", exc_info=True)


@router.get("/catalog/table", response_model=CatalogLocationsTableResponse)
def catalog_locations_table(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
    include_test: Annotated[bool, Query()] = False,
) -> CatalogLocationsTableResponse:
    """Таблица каталога. Аноним видит её без отметок «посещено» (нет user_id)."""
    payload = build_catalog_locations_table(
        db, user.id if user is not None else None, include_test_events=include_test
    )
    return CatalogLocationsTableResponse.model_validate(payload)
