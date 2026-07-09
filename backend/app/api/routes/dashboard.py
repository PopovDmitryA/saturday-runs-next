from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import DashboardResponse, DashboardStatsResponse, OnThisDayResponse
from app.services.dashboard_service import get_dashboard_payload
from app.services.on_this_day_service import get_on_this_day
from app.services.sync_trigger_service import maybe_enqueue_login_auto_sync

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DashboardResponse:
    payload = get_dashboard_payload(db, user)
    sync_enqueued = maybe_enqueue_login_auto_sync(
        db,
        user.id,
        interval_seconds=settings.user_login_auto_sync_interval_seconds,
    )
    return DashboardResponse(
        stats=DashboardStatsResponse.model_validate(payload["stats"]),
        computed_at=payload["computed_at"],
        platform_links=payload["platform_links"],  # type: ignore[arg-type]
        sync_enqueued=sync_enqueued,
        serial_id=user.serial_id,
    )


@router.get("/on-this-day", response_model=OnThisDayResponse)
def dashboard_on_this_day(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> OnThisDayResponse:
    return OnThisDayResponse.model_validate(get_on_this_day(db, user.id))
