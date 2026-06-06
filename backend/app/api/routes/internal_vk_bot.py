from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.admin import is_admin_vk_user_id
from app.db.session import get_db
from app.schemas.admin_stats import AdminSiteStatsResponse
from app.services.admin_pipeline_status_service import get_admin_pipeline_status
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.location_coordinate_service import handle_admin_coordinate_message

router = APIRouter(prefix="/internal/vk-bot", tags=["internal-vk-bot"])


def _verify_vk_bot_secret(
    x_bot_secret: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Settings:
    secret = settings.vk_bot_internal_secret or settings.telegram_bot_internal_secret
    if not secret:
        raise HTTPException(status_code=503, detail="VK bot internal API disabled")
    if x_bot_secret != secret:
        raise HTTPException(status_code=403, detail="Invalid bot secret")
    return settings


def _require_admin_vk_user(vk_user_id: int, settings: Settings) -> None:
    if not is_admin_vk_user_id(vk_user_id, settings):
        raise HTTPException(status_code=403, detail="Admin access required")


class VkCoordinateMessage(BaseModel):
    vk_peer_id: int
    text: str
    reply_to_message_id: int | None = None


class VkCoordinateResponse(BaseModel):
    handled: bool
    reply: str | None = None


@router.get("/stats", response_model=AdminSiteStatsResponse)
def vk_bot_stats(
    vk_user_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
    period_days: int = 30,
) -> AdminSiteStatsResponse:
    _require_admin_vk_user(vk_user_id, settings)
    payload = get_admin_site_stats(db, period_days=period_days)
    return AdminSiteStatsResponse.model_validate(payload)


@router.get("/sync-status")
def vk_bot_sync_status(
    vk_user_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> dict[str, object]:
    _require_admin_vk_user(vk_user_id, settings)
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


@router.post("/coordinate-message", response_model=VkCoordinateResponse)
def vk_coordinate_message(
    body: VkCoordinateMessage,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> VkCoordinateResponse:
    if settings.vk_admin_user_id and body.vk_peer_id != settings.vk_admin_user_id:
        return VkCoordinateResponse(handled=False)
    reply = handle_admin_coordinate_message(
        db,
        body.vk_peer_id,
        body.text,
        reply_to_message_id=body.reply_to_message_id,
        messenger="vk",
    )
    if reply is None:
        return VkCoordinateResponse(handled=False)
    return VkCoordinateResponse(handled=True, reply=reply)
