from __future__ import annotations

import hmac
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
from app.services.admin_protocol_sync_service import sync_protocol_from_url
from app.services.admin_site_stats_service import get_admin_site_stats
from app.services.admin_sync_service import enqueue_pipeline, list_pipelines
from app.services.location_coordinate_service import handle_admin_coordinate_message

router = APIRouter(prefix="/internal/vk-bot", tags=["internal-vk-bot"])


def _verify_vk_bot_secret(
    x_bot_secret: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> Settings:
    secret = settings.vk_bot_internal_secret or settings.telegram_bot_internal_secret
    if not secret:
        raise HTTPException(status_code=503, detail="VK bot internal API disabled")
    if x_bot_secret is None or not hmac.compare_digest(x_bot_secret, secret):
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


class VkSyncProtocolRequest(BaseModel):
    url: str


class VkSyncEnqueueRequest(BaseModel):
    pipeline: str
    location_slug: str | None = None


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


def _sweep_flag(name: str) -> str:
    import re

    if name.startswith("mac"):
        return "💻"
    m = re.match(r"([a-z])([a-z])", name)
    if not m:
        return "🏳️"
    return chr(0x1F1E6 + ord(m.group(1)) - 97) + chr(0x1F1E6 + ord(m.group(2)) - 97)


def _sweep_report_text() -> str:
    """Статус мирового обхода атлетов parkrun (staging pm-postgres)."""
    import os

    dsn = os.getenv("PM_WORLD_DSN")
    if not dsn:
        return "Обход атлетов не настроен (PM_WORLD_DSN)."
    import psycopg

    try:
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            by = dict(conn.execute("SELECT status, count(*) FROM crawl_queue GROUP BY status").fetchall())
            crawled = conn.execute("SELECT count(*) FROM athletes WHERE source='crawl'").fetchone()[0]
            runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
            exits = conn.execute(
                "SELECT name, delay_sec, ban_level, "
                "GREATEST(0, EXTRACT(EPOCH FROM (cooldown_until - now())) / 60) "
                "FROM sweep_exits WHERE enabled ORDER BY (cooldown_until > now()), name"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return f"Обход: БД недоступна ({exc!r})"[:160]
    total = sum(by.values())
    pending = by.get("pending", 0)
    working = sum(1 for *_, cd in exits if cd <= 0)
    lines = [
        "🌍 Обход атлетов parkrun",
        f"📊 пройдено {total - pending:,}/{total:,}, собрано {crawled:,}, забегов {runs:,}",
        f"🔌 рабочих {working}/{len(exits)}",
        "",
    ]
    for name, delay, bl, cd in exits:
        flag = _sweep_flag(name)
        if cd > 0:
            h, m = divmod(int(cd), 60)
            left = f"{h}ч{m:02d}м" if h else f"{m}м"
            lines.append(f"{flag} {name}: 💤 {left} (ур.{bl}, {delay:.0f}с)")
        else:
            lines.append(f"{flag} {name}: ✅ ({delay:.0f}с)")
    return "\n".join(lines)


@router.get("/sweep-status")
def vk_bot_sweep_status(
    vk_user_id: int,
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> dict[str, str]:
    _require_admin_vk_user(vk_user_id, settings)
    return {"text": _sweep_report_text()}


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


@router.get("/sync-pipelines")
def vk_bot_sync_pipelines(
    vk_user_id: int,
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> list[dict[str, str]]:
    _require_admin_vk_user(vk_user_id, settings)
    return list_pipelines()


@router.post("/sync-enqueue")
def vk_bot_sync_enqueue(
    body: VkSyncEnqueueRequest,
    vk_user_id: int,
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> dict[str, str]:
    _require_admin_vk_user(vk_user_id, settings)
    try:
        message = enqueue_pipeline(body.pipeline, location_slug=body.location_slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": message}


@router.post("/sync-protocol")
def vk_sync_protocol(
    body: VkSyncProtocolRequest,
    vk_user_id: int,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(_verify_vk_bot_secret)],
) -> dict[str, object]:
    _require_admin_vk_user(vk_user_id, settings)
    try:
        return sync_protocol_from_url(db, body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
