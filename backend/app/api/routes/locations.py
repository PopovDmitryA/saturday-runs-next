from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.locations import (
    CatalogLocationsTableResponse,
    LocationEventsResponse,
    LocationLeadersResponse,
    LocationPageResponse,
    LocationsIndexResponse,
    MapLocationsResponse,
    UniqueLocationsDetailResponse,
)
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_catalog_map_locations, list_user_visited_map_locations
from app.services.location_page_service import (
    build_location_events,
    build_location_leaders,
    build_location_page,
    build_locations_index,
)
from app.services.user_unique_locations_detail import build_user_unique_location_details

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/index", response_model=LocationsIndexResponse)
def locations_index(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_admin_user)],
) -> LocationsIndexResponse:
    """Каталог локаций — входная точка раздела «Локации».

    Раздел пока admin-only: не шарим на всех до отдельного решения
    (Дмитрий, 13.07.2026). Ниже visited/* и catalog/* — это страница «Карта»,
    она доступна всем авторизованным, её гейтить нельзя.
    """
    _ = user
    payload = build_locations_index(db)
    return LocationsIndexResponse.model_validate(payload)


@router.get("/page/{slug}", response_model=LocationPageResponse)
def location_page(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_admin_user)],
) -> LocationPageResponse:
    """Страница локации: сводные цифры, таймлайн систем, гистограмма, инфо-карточка."""
    _ = user
    payload = build_location_page(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationPageResponse.model_validate(payload)


@router.get("/page/{slug}/leaders", response_model=LocationLeadersResponse)
def location_leaders(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_admin_user)],
) -> LocationLeadersResponse:
    """Рейтинги внутри локации: топ по пробежкам и волонтёрствам."""
    _ = user
    payload = build_location_leaders(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationLeadersResponse.model_validate(payload)


@router.get("/page/{slug}/events", response_model=LocationEventsResponse)
def location_events(
    slug: str,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_admin_user)],
) -> LocationEventsResponse:
    """Журнал протоколов локации сквозь все системы."""
    _ = user
    payload = build_location_events(db, slug)
    if payload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Локация не найдена")
    return LocationEventsResponse.model_validate(payload)


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
    user: Annotated[User, Depends(get_current_user)],
) -> MapLocationsResponse:
    _ = user
    payload = list_catalog_map_locations(db)
    return MapLocationsResponse.model_validate(payload)


@router.get("/catalog/table", response_model=CatalogLocationsTableResponse)
def catalog_locations_table(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    include_test: Annotated[bool, Query()] = False,
) -> CatalogLocationsTableResponse:
    payload = build_catalog_locations_table(db, user.id, include_test_events=include_test)
    return CatalogLocationsTableResponse.model_validate(payload)
