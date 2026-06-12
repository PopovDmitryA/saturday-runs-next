from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ratings import RunCountRatingResponse
from app.services.five_verst_run_count_rating_service import get_five_verst_run_count_rating

router = APIRouter(prefix="/public/ratings", tags=["ratings"])


@router.get("/five-verst/run-count", response_model=RunCountRatingResponse)
def five_verst_run_count_rating(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> RunCountRatingResponse:
    payload = get_five_verst_run_count_rating(db, limit=limit)
    return RunCountRatingResponse.model_validate(payload)
