from __future__ import annotations

from datetime import datetime, timezone

from app.models import Participant, SyncStatus
from app.platform_adapters.canonical import CanonicalParticipant


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
        row.profile_extra = profile_extra
    if parser_version is not None:
        row.parser_version = parser_version
    row.fetched_at = observed_at
    row.sync_status = SyncStatus.ok
    row.error_message = None
