from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProfileFetchPending, ProfileFetchPendingStatus
from app.parkrun.fetch.captcha_state import (
    captcha_pending_message,
    clear_captcha_pending,
    is_captcha_pending,
    platform_cooldown_remaining,
)
from app.parkrun.fetch.cdp_session import ParkrunCdpSessionError, save_session_from_chrome_cdp
from app.platform_fetch.cooldown import clear_platform_cooldown, is_platform_in_cooldown
from app.services.parkrun_local_worker import get_local_worker_status
from app.services.profile_fetch_pending_service import (
    count_stuck_done_parkrun_pending,
    list_pending_rows,
    process_pending_row,
)


def get_parkrun_session_status(db: Session) -> dict[str, object]:
    settings = get_settings()
    storage_path = settings.parkrun_playwright_storage_state_path.strip()
    pending_count = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.pending,
        )
        .count()
    )
    failed_count = (
        db.query(ProfileFetchPending)
        .filter(
            ProfileFetchPending.platform_code == "parkrun",
            ProfileFetchPending.status == ProfileFetchPendingStatus.failed,
        )
        .count()
    )
    return {
        "captcha_pending": is_captcha_pending(),
        "captcha_message": captcha_pending_message(),
        "cooldown_remaining_seconds": platform_cooldown_remaining(),
        "storage_state_configured": bool(storage_path),
        "storage_state_exists": bool(storage_path and Path(storage_path).is_file()),
        "storage_state_path": storage_path or "/data/parkrun_playwright_state.json",
        "default_cdp_url": settings.parkrun_cdp_url,
        "pending_queue_count": pending_count,
        "failed_queue_count": failed_count,
        "stuck_done_queue_count": count_stuck_done_parkrun_pending(db),
        **get_local_worker_status(),
    }


def save_parkrun_session_from_chrome(cdp_url: str | None = None) -> dict[str, object]:
    settings = get_settings()
    url = (cdp_url or "").strip() or settings.parkrun_cdp_url
    return save_session_from_chrome_cdp(url)


def clear_parkrun_captcha_wait() -> None:
    clear_captcha_pending()
    clear_platform_cooldown("parkrun")


def process_parkrun_pending_queue(db: Session, *, limit: int = 10) -> dict[str, object]:
    if is_captcha_pending():
        raise ParkrunCdpSessionError(
            "Сначала пройдите капчу и сохраните сессию из Chrome (кнопка в админке).",
            400,
        )
    rows = list_pending_rows(db, platform_code="parkrun", limit=limit)
    summary: dict[str, int] = {}
    details: list[str] = []
    for row in rows:
        if is_platform_in_cooldown("parkrun"):
            summary["skipped_cooldown"] = summary.get("skipped_cooldown", 0) + 1
            details.append(f"skip cooldown: {row.external_user_id or row.profile_input}")
            break
        outcome = process_pending_row(db, row)
        db.refresh(row)
        summary[outcome] = summary.get(outcome, 0) + 1
        label = row.external_user_id or row.profile_input
        detail = f"{outcome}: {label}"
        if row.last_error:
            detail = f"{detail} — {row.last_error[:300]}"
        details.append(detail)
    return {"summary": summary, "details": details}
