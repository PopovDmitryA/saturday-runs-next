from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.rating import (
    MyRatingsResponse,
    RatingEligibilityResponse,
    RatingResponse,
    RatingUpsertRequest,
)
from app.services.rating_service import (
    RatingError,
    delete_rating,
    list_eligible_runs,
    list_my_ratings,
    upsert_rating,
)

router = APIRouter(prefix="/ratings", tags=["ratings"])


@router.get("/eligible-runs", response_model=RatingEligibilityResponse)
def get_eligible_runs(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RatingEligibilityResponse:
    return RatingEligibilityResponse.model_validate(list_eligible_runs(db, user.id))


@router.get("/mine", response_model=MyRatingsResponse)
def get_my_ratings(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MyRatingsResponse:
    return MyRatingsResponse.model_validate(list_my_ratings(db, user.id))


@router.put("/run/{run_result_id}", response_model=RatingResponse)
def put_rating(
    run_result_id: UUID,
    payload: RatingUpsertRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> RatingResponse:
    try:
        result = upsert_rating(
            db,
            user,
            run_result_id,
            score_overall=payload.score_overall,
            score_organization=payload.score_organization,
            score_route=payload.score_route,
            score_community=payload.score_community,
            comment=payload.comment,
            is_public=payload.is_public,
        )
    except RatingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return RatingResponse.model_validate(result)


@router.delete("/run/{run_result_id}", status_code=204)
def remove_rating(
    run_result_id: UUID,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    try:
        deleted = delete_rating(db, user.id, run_result_id)
    except RatingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Оценка не найдена")
    db.commit()
