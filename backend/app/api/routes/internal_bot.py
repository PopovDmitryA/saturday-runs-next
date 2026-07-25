from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.admin import is_admin_telegram_id
from app.db.session import get_db
from app.schemas.admin_stats import AdminSiteStatsResponse
from app.services.admin_pipeline_status_service import get_admin_pipeline_status
from app.services.admin_protocol_sync_service import sync_protocol_from_url
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.admin_sweep_status_service import sweep_report_text
from app.services.admin_sync_service import enqueue_pipeline, list_pipelines
from app.services.broadcast_compose_state import (
    clear_broadcast_state,
    get_broadcast_draft,
    is_awaiting_broadcast_text,
    save_broadcast_draft,
    start_broadcast_compose,
)
from app.services.location_coordinate_service import handle_admin_coordinate_message
from app.services.news_broadcast_service import list_news_subscribers, send_news_broadcast

router = APIRouter(prefix="/internal/bot", tags=["internal-bot"])


def _verify_bot_secret(
    x_bot_secret: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Settings:
    if not settings.telegram_bot_internal_secret:
        raise HTTPException(status_code=503, detail="Bot internal API disabled")
    if x_bot_secret != settings.telegram_bot_internal_secret:
        raise HTTPException(status_code=403, detail="Invalid bot secret")
    return settings


def _require_admin_telegram_id(telegram_id: int, settings: Settings) -> None:
    if not is_admin_telegram_id(telegram_id, settings):
        raise HTTPException(status_code=403, detail="Admin access required")


class BotCoordinateMessage(BaseModel):
    telegram_chat_id: int
    text: str
    reply_to_message_id: int | None = None


class BotCoordinateResponse(BaseModel):
    handled: bool
    reply: str | None = None


@router.post("/coordinate-message", response_model=BotCoordinateResponse)
def bot_coordinate_message(
    body: BotCoordinateMessage,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BotCoordinateResponse:
    reply = handle_admin_coordinate_message(
        db,
        body.telegram_chat_id,
        body.text,
        reply_to_message_id=body.reply_to_message_id,
    )
    if reply is None:
        return BotCoordinateResponse(handled=False)
    return BotCoordinateResponse(handled=True, reply=reply)


class BroadcastAdminRequest(BaseModel):
    telegram_id: int


class BroadcastSubscriberItem(BaseModel):
    telegram_id: int
    telegram_username: str | None = None
    display_name: str | None = None
    label: str


class BroadcastSubscribersResponse(BaseModel):
    count: int
    subscribers: list[BroadcastSubscriberItem] = Field(default_factory=list)


def _subscribers_response(db: Session) -> BroadcastSubscribersResponse:
    subscribers = list_news_subscribers(db)
    return BroadcastSubscribersResponse(
        count=len(subscribers),
        subscribers=[
            BroadcastSubscriberItem(
                telegram_id=item.telegram_id,
                telegram_username=item.telegram_username,
                display_name=item.display_name,
                label=item.label,
            )
            for item in subscribers
        ],
    )


class BroadcastDraftSaveRequest(BaseModel):
    telegram_id: int
    message: str = Field(min_length=1, max_length=4096)


class BroadcastDraftResponse(BaseModel):
    has_draft: bool
    awaiting_text: bool
    message: str | None = None


class BroadcastSendRequest(BaseModel):
    telegram_id: int


class BroadcastFailureItem(BaseModel):
    telegram_id: int
    label: str
    error: str


class BroadcastSendResponse(BaseModel):
    recipients: int
    sent: int
    failed: int
    failures: list[BroadcastFailureItem] = Field(default_factory=list)


@router.post("/broadcast/start")
def broadcast_start(
    body: BroadcastAdminRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BroadcastSubscribersResponse:
    _require_admin_telegram_id(body.telegram_id, settings)
    start_broadcast_compose(None, body.telegram_id)
    return _subscribers_response(db)


@router.get("/broadcast/subscribers", response_model=BroadcastSubscribersResponse)
def broadcast_subscribers(
    telegram_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BroadcastSubscribersResponse:
    _require_admin_telegram_id(telegram_id, settings)
    return _subscribers_response(db)


@router.post("/broadcast/draft", response_model=BroadcastDraftResponse)
def broadcast_save_draft(
    body: BroadcastDraftSaveRequest,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BroadcastDraftResponse:
    _require_admin_telegram_id(body.telegram_id, settings)
    save_broadcast_draft(None, body.telegram_id, body.message.strip())
    return BroadcastDraftResponse(has_draft=True, awaiting_text=False, message=body.message.strip())


@router.get("/broadcast/draft", response_model=BroadcastDraftResponse)
def broadcast_get_draft(
    telegram_id: int,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BroadcastDraftResponse:
    _require_admin_telegram_id(telegram_id, settings)
    message = get_broadcast_draft(None, telegram_id)
    awaiting = is_awaiting_broadcast_text(None, telegram_id)
    return BroadcastDraftResponse(
        has_draft=message is not None,
        awaiting_text=awaiting,
        message=message,
    )


@router.post("/broadcast/cancel")
def broadcast_cancel(
    body: BroadcastAdminRequest,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> dict[str, bool]:
    _require_admin_telegram_id(body.telegram_id, settings)
    clear_broadcast_state(None, body.telegram_id)
    return {"cancelled": True}


@router.post("/broadcast/send", response_model=BroadcastSendResponse)
def broadcast_send(
    body: BroadcastSendRequest,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> BroadcastSendResponse:
    _require_admin_telegram_id(body.telegram_id, settings)
    message = get_broadcast_draft(None, body.telegram_id)
    if not message:
        raise HTTPException(status_code=400, detail="No broadcast draft")
    try:
        result = send_news_broadcast(db, message, settings=settings)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    clear_broadcast_state(None, body.telegram_id)
    return BroadcastSendResponse(
        recipients=result.recipients,
        sent=result.sent,
        failed=result.failed,
        failures=[
            BroadcastFailureItem(
                telegram_id=item.telegram_id,
                label=item.label,
                error=item.error,
            )
            for item in result.failures
        ],
    )


class AdminSyncProtocolRequest(BaseModel):
    url: str


class AdminSyncEnqueueRequest(BaseModel):
    pipeline: str
    location_slug: str | None = None


@router.get("/admin/stats", response_model=AdminSiteStatsResponse)
def admin_stats(
    telegram_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
    period_days: int = 30,
) -> AdminSiteStatsResponse:
    _require_admin_telegram_id(telegram_id, settings)
    payload = get_admin_site_stats(db, period_days=period_days)
    return AdminSiteStatsResponse.model_validate(payload)


@router.get("/admin/sync-status")
def admin_sync_status(
    telegram_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> dict[str, object]:
    _require_admin_telegram_id(telegram_id, settings)
    payload = get_admin_pipeline_status(db)
    running = []
    for item in payload["running"]:
        started_at = item.get("started_at")
        running.append(
            {
                **item,
                "started_at": started_at.isoformat() if started_at is not None else None,
            }
        )
    checked_at = payload["checked_at"]
    return {
        "checked_at": checked_at.isoformat() if isinstance(checked_at, datetime) else checked_at,
        "running": running,
        "queue_depths": payload["queue_depths"],
        "parkrun_local_worker": payload["parkrun_local_worker"],
    }


@router.get("/admin/sweep-status")
def admin_sweep_status(
    telegram_id: int,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> dict[str, str]:
    _require_admin_telegram_id(telegram_id, settings)
    return {"text": sweep_report_text()}


@router.get("/admin/sync-pipelines")
def admin_sync_pipelines(
    telegram_id: int,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> list[dict[str, str]]:
    _require_admin_telegram_id(telegram_id, settings)
    return list_pipelines()


@router.post("/admin/sync-enqueue")
def admin_sync_enqueue(
    body: AdminSyncEnqueueRequest,
    telegram_id: int,
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> dict[str, str]:
    _require_admin_telegram_id(telegram_id, settings)
    try:
        message = enqueue_pipeline(body.pipeline, location_slug=body.location_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.post("/admin/sync-protocol")
def admin_sync_protocol(
    body: AdminSyncProtocolRequest,
    telegram_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_bot_secret)],
) -> dict[str, object]:
    _require_admin_telegram_id(telegram_id, settings)
    try:
        return sync_protocol_from_url(db, body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
