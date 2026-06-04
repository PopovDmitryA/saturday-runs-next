from __future__ import annotations

from datetime import datetime, timezone

from app.models import Location, SyncStatus


def apply_location_registry_flags(
    row: Location,
    *,
    is_paused: bool,
    is_cancelled: bool,
) -> tuple[bool, bool, bool]:
    """Apply registry pause/cancel flags. Returns (changed, pause_changed, cancel_changed)."""
    changed = False
    pause_changed = False
    cancel_changed = False

    if row.is_cancelled != is_cancelled:
        row.is_cancelled = is_cancelled
        changed = True
        cancel_changed = True

    if row.is_paused != is_paused:
        row.is_paused = is_paused
        changed = True
        pause_changed = True

    if changed:
        row.fetched_at = datetime.now(timezone.utc)
        row.sync_status = SyncStatus.ok

    return changed, pause_changed, cancel_changed
