from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_optional_user
from app.db.session import get_db
from app.models import User
from app.schemas.unified_protocol import UnifiedProtocolResponse, UnifiedProtocolWeeksResponse
from app.services.unified_protocol_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    build_unified_protocol,
    latest_protocol_saturday,
    list_protocol_weeks,
    saturday_of,
)

router = APIRouter(prefix="/protocol", tags=["protocol"])

# Единый протокол открыт БЕЗ логина, как и вся витрина локаций и рейтингов.
# Логин добавляет ровно одно — свои строки недели в блоке «Ваш результат».


@router.get("/weeks", response_model=UnifiedProtocolWeeksResponse)
def protocol_weeks(db: Annotated[Session, Depends(get_db)]) -> UnifiedProtocolWeeksResponse:
    """Недели, по которым есть протоколы, — для выбора недели на странице."""
    weeks = list_protocol_weeks(db)
    return UnifiedProtocolWeeksResponse.model_validate(
        {"weeks": weeks, "latest_saturday": weeks[-1]["saturday"] if weeks else None}
    )


@router.get("/week", response_model=UnifiedProtocolResponse)
@router.get("/week/{event_date}", response_model=UnifiedProtocolResponse)
def protocol_week(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User | None, Depends(get_optional_user)],
    event_date: date | None = None,
    platform: Annotated[str | None, Query()] = None,
    gender: Annotated[str | None, Query()] = None,
    age_group: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=128)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> UnifiedProtocolResponse:
    """Единый протокол недели: все площадки всех систем в порядке финиша.

    Дата в адресе — любой день недели; неделя всегда подписывается своей
    субботой (воскресный RunPark и пятничные 5 вёрст входят в ту же неделю).
    Без даты отдаём последнюю неделю с данными.
    """
    if event_date is None:
        latest = latest_protocol_saturday(db)
        if latest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Протоколов пока нет")
        event_date = latest

    payload = build_unified_protocol(
        db,
        saturday_of(event_date),
        platform=platform,
        gender=gender,
        age_group=age_group,
        query=q,
        page=page,
        per_page=per_page,
        viewer=user,
    )
    return UnifiedProtocolResponse.model_validate(payload)
