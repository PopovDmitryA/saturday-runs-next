from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models import Participant, SyncStatus
from app.platform_adapters.canonical import CanonicalParticipant

BARCODE_LOOKUP_STATUS_KEY = "barcode_lookup_status"
PROFILE_CHECKED_AT_KEY = "profile_checked_at"
# Legacy alias — kept in sync with profile_checked_at for older rows.
BARCODE_LOOKUP_CHECKED_AT_KEY = "barcode_lookup_checked_at"

CORRUPTED_NAME_MARKERS = (
    "404",
    "не существует",
    "not found",
    "регистрация",
    "registration",
)


def merge_profile_extra(row: Participant, updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(row.profile_extra or {})
    merged.update(updates)
    row.profile_extra = merged
    return merged


def profile_checked_at(profile_extra: dict[str, Any] | None) -> datetime | None:
    raw = (profile_extra or {}).get(PROFILE_CHECKED_AT_KEY) or (profile_extra or {}).get(
        BARCODE_LOOKUP_CHECKED_AT_KEY
    )
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def mark_profile_checked(row: Participant, *, now: datetime | None = None) -> datetime:
    observed_at = now or datetime.now(timezone.utc)
    iso = observed_at.isoformat()
    merge_profile_extra(
        row,
        {
            PROFILE_CHECKED_AT_KEY: iso,
            BARCODE_LOOKUP_CHECKED_AT_KEY: iso,
        },
    )
    row.fetched_at = observed_at
    return observed_at


def set_barcode_lookup_status(
    row: Participant,
    status: str,
    *,
    now: datetime | None = None,
    error_message: str | None = None,
) -> None:
    mark_profile_checked(row, now=now)
    merge_profile_extra(
        row,
        {
            BARCODE_LOOKUP_STATUS_KEY: status,
            "friends_added": [],
            "friends_of": [],
            "awards": [],
        },
    )
    row.sync_status = SyncStatus.ok if status in {"ok", "absent"} else SyncStatus.error
    row.error_message = error_message


def restore_corrupted_display_name(row: Participant) -> None:
    name = (row.display_name or "").strip()
    lowered = name.lower()
    if not name or any(marker in lowered for marker in CORRUPTED_NAME_MARKERS):
        if row.external_user_id.isdigit():
            row.display_name = f"S95 {row.external_user_id}"


def apply_s95_participant_profile(
    row: Participant,
    profile: CanonicalParticipant,
    *,
    club_name: str | None = None,
    barcode_id: str | None = None,
    profile_extra: dict | None = None,
    parser_version: str | None = None,
    now: datetime | None = None,
) -> None:
    observed_at = now or datetime.now(timezone.utc)
    row.display_name = profile.display_name
    if profile.profile_url:
        row.profile_url = profile.profile_url
    row.club_name = club_name if club_name is not None else profile.club_name
    row.barcode_id = barcode_id if barcode_id is not None else profile.barcode_id
    row.planning_location = profile.planning_location
    if profile.planning_location:
        row.planning_location_seen_at = observed_at
    else:
        row.planning_location_seen_at = None
    if profile.source_url:
        row.source_url = profile.source_url
    if profile_extra is not None:
        merge_profile_extra(row, profile_extra)
    if parser_version is not None:
        row.parser_version = parser_version
    lookup_status = "ok" if row.barcode_id else "absent"
    mark_profile_checked(row, now=observed_at)
    merge_profile_extra(row, {BARCODE_LOOKUP_STATUS_KEY: lookup_status})
    row.sync_status = SyncStatus.ok
    row.error_message = None
