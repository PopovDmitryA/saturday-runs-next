from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProfileFetchPending, ProfileFetchPendingStatus
from app.parkrun.fetch.captcha_state import (
    captcha_pending_message,
    is_captcha_pending,
    platform_cooldown_remaining,
)
from app.services.parkrun_local_worker import get_local_worker_status
from app.services.profile_fetch_pending_service import (
    count_stuck_done_parkrun_pending,
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
