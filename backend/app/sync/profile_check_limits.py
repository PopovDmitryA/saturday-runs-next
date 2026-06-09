from __future__ import annotations

from app.models import SyncJobTrigger

FULL_PROFILE_CHECK_TRIGGERS = frozenset({SyncJobTrigger.manual, SyncJobTrigger.linking})


def profile_activity_check_limit(trigger: SyncJobTrigger | None, *, default: int) -> int | None:
    """Manual sync and profile linking verify the full activity list from the profile."""
    if trigger is not None and trigger in FULL_PROFILE_CHECK_TRIGGERS:
        return None
    return default
