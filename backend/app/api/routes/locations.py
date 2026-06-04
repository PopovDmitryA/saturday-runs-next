from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.locations import (
    CatalogLocationsTableResponse,
    MapLocationsResponse,
    UniqueLocationsDetailResponse,
)
from app.services.location_catalog_table_service import build_catalog_locations_table
from app.services.location_map_service import list_catalog_map_locations, list_user_visited_map_locations
from app.services.user_unique_locations_detail import build_user_unique_location_details

router = APIRouter(prefix="/locations", tags=["locations"])


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
