from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import User
from app.schemas.dashboard import (
    DashboardFocusResponse,
    DashboardFocusUpdateRequest,
    DashboardResponse,
    DashboardStatsResponse,
    MyHistoryResponse,
    OnThisDayResponse,
)
from app.services.dashboard_focus import detect_focus_profiles, normalize_focus_selection
from app.services.dashboard_service import get_dashboard_payload
from app.services.my_history_service import get_my_history
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
    stats = payload["stats"]
    suggested = detect_focus_profiles(
        stats.get("analytics") or {},
        total_volunteering=stats.get("total_volunteering") or 0,
    )
    # Новыми считаются профили, которых не было ни в автонаборе на момент
    # последнего подтверждения, ни в текущем выборе: снятую вручную галку
    # не переспрашиваем, пока новая привязка не даст для неё свежий повод.
    known = set(user.dashboard_focus_auto or []) | set(user.dashboard_focus or [])
    newly = [p for p in suggested if p not in known] if user.dashboard_focus is not None else []
    return DashboardResponse(
        stats=DashboardStatsResponse.model_validate(stats),
        computed_at=payload["computed_at"],
        platform_links=payload["platform_links"],  # type: ignore[arg-type]
        sync_enqueued=sync_enqueued,
        serial_id=user.serial_id,
        public_slug=user.public_slug,
        focus=DashboardFocusResponse(
            selected=user.dashboard_focus,
            suggested=suggested,
            newly_suggested=newly,
        ),
    )


@router.get("/focus", response_model=DashboardFocusResponse)
def get_dashboard_focus(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> DashboardFocusResponse:
    """Текущий выбор и автонабор — для секции в настройках."""
    payload = get_dashboard_payload(db, user)
    stats = payload["stats"]
    suggested = detect_focus_profiles(
        stats.get("analytics") or {},
        total_volunteering=stats.get("total_volunteering") or 0,
    )
    return DashboardFocusResponse(
        selected=user.dashboard_focus,
        suggested=suggested,
        newly_suggested=[],
    )


@router.put("/focus", response_model=DashboardFocusResponse)
def update_dashboard_focus(
    body: DashboardFocusUpdateRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> DashboardFocusResponse:
    """Сохранить выбор профилей.

    Автонабором фиксируем seen_suggested — то, что человек видел в модалке
    в момент подтверждения (клиент получил его из GET). Пересчитывать
    аналитику здесь нельзя: на холодном кэше полный расчёт дашборда
    занимает десятки секунд и «Сохранить» упирается в клиентский таймаут.
    """
    try:
        selection = normalize_focus_selection(body.profiles)
        seen = normalize_focus_selection(body.seen_suggested)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user.dashboard_focus = selection if selection is not None else []
    user.dashboard_focus_auto = seen if seen is not None else []
    db.add(user)
    db.commit()
    return DashboardFocusResponse(
        selected=user.dashboard_focus,
        suggested=user.dashboard_focus_auto or [],
        newly_suggested=[],
    )


@router.get("/on-this-day", response_model=OnThisDayResponse)
def dashboard_on_this_day(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> OnThisDayResponse:
    return OnThisDayResponse.model_validate(get_on_this_day(db, user.id))


@router.get("/my-history", response_model=MyHistoryResponse)
def dashboard_my_history(
    db: Annotated[Session, Depends(get_db)],
    user: Annotated[User, Depends(get_current_user)],
) -> MyHistoryResponse:
    return MyHistoryResponse.model_validate(get_my_history(db, user.id))
