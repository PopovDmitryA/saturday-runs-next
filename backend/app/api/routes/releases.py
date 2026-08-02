from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.releases import ReleaseLatestResponse, ReleaseListResponse, ReleaseResponse
from app.services.release_service import latest_published_version, list_published_releases

# Публичный раздел: страница «Обновления» — история релизов, авторизация не нужна.
router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("", response_model=ReleaseListResponse)
def releases_list(db: Annotated[Session, Depends(get_db)]) -> ReleaseListResponse:
    releases = list_published_releases(db)
    return ReleaseListResponse(
        items=[ReleaseResponse.model_validate(release) for release in releases],
        total=len(releases),
        latest_version=releases[0].version if releases else None,
    )


@router.get("/latest", response_model=ReleaseLatestResponse)
def releases_latest(db: Annotated[Session, Depends(get_db)]) -> ReleaseLatestResponse:
    """Лёгкий запрос для футера: номер последнего опубликованного релиза."""
    return ReleaseLatestResponse(version=latest_published_version(db))
