from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.releases import ReleaseLatestResponse, ReleaseListResponse, ReleaseResponse
from app.services.release_service import (
    RELEASES_MAX_PAGE_SIZE,
    RELEASES_PAGE_SIZE,
    latest_published_version,
    paginate_published_releases,
)

# Публичный раздел: страница «Обновления» — история релизов, авторизация не нужна.
router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("", response_model=ReleaseListResponse)
def releases_list(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1, description="Номер страницы, новые релизы на первой")] = 1,
    page_size: Annotated[int, Query(ge=1, le=RELEASES_MAX_PAGE_SIZE)] = RELEASES_PAGE_SIZE,
    version: Annotated[
        str | None,
        Query(description="Открыть страницу, на которой лежит эта версия (якоря вида #v2.5.0)"),
    ] = None,
) -> ReleaseListResponse:
    result = paginate_published_releases(db, page=page, page_size=page_size, version=version)
    return ReleaseListResponse(
        items=[ReleaseResponse.model_validate(release) for release in result.items],
        total=result.total,
        page=result.page,
        page_size=result.page_size,
        pages=result.pages,
        latest_version=result.latest_version,
    )


@router.get("/latest", response_model=ReleaseLatestResponse)
def releases_latest(db: Annotated[Session, Depends(get_db)]) -> ReleaseLatestResponse:
    """Лёгкий запрос для футера: номер последнего опубликованного релиза."""
    return ReleaseLatestResponse(version=latest_published_version(db))
