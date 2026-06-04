from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import get_settings
from app.platform_adapters.s95.url import athlete_url
from app.s95.fetch import fetch_page_html
from app.s95.parsers.athlete import (
    parse_athlete_html,
    parse_athlete_runs_html,
    parse_athlete_volunteering_html,
)
from app.s95.parsers.athlete_stats import parse_athlete_links_html, parse_trophies_html
from app.sync import upsert
from app.sync.s95_athlete_mismatch import (
    refetch_mismatched_protocols,
    refetch_protocols_for_profile_volunteering,
)
from app.sync.s95_participant_sync import apply_s95_participant_profile

logger = logging.getLogger(__name__)
PLATFORM_CODE = "s95"


@dataclass
class AthleteSyncResult:
    external_user_id: str
    saved: bool = False
    retried: bool = False
    friends_added: int = 0
    friends_of: int = 0
    awards: int = 0
    runs_imported: int = 0
    mismatches_found: int = 0
    protocols_refetched: int = 0
    errors: list[str] = field(default_factory=list)


def sync_s95_athlete(
    db: Session,
    external_user_id: str,
    *,
    retry_on_empty: bool = True,
    mismatch_check_limit: int | None = None,
) -> AthleteSyncResult:
    platform = upsert.get_platform(db, PLATFORM_CODE)
    settings = get_settings()
    result = AthleteSyncResult(external_user_id=external_user_id)
    profile_url = athlete_url(external_user_id)

    def _load_html() -> str:
        return fetch_page_html(
            profile_url,
            reason="athlete_profile",
            extra_wait_ms=settings.s95_playwright_athlete_wait_ms,
        )

    html = _load_html()
    if retry_on_empty and "Загрузка..." in html and html.count("/athletes/") < 3:
        result.retried = True
        html = _load_html()

    try:
        profile = parse_athlete_html(html, profile_url, external_user_id)
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    base = profile_url.rstrip("/")
    friends = parse_athlete_links_html(
        fetch_page_html(f"{base}/statistics/friends", reason="athlete_friends"),
        base,
        exclude_user_id=external_user_id,
    )
    friends_of = parse_athlete_links_html(
        fetch_page_html(f"{base}/statistics/followers", reason="athlete_followers"),
        base,
        exclude_user_id=external_user_id,
    )
    awards = parse_trophies_html(
        fetch_page_html(f"{base}/statistics/total_trophies", reason="athlete_trophies")
    )
    runs = parse_athlete_runs_html(
        html,
        external_user_id=external_user_id,
        display_name=profile.display_name,
        profile_url=profile_url,
    )
    volunteering = parse_athlete_volunteering_html(
        html,
        external_user_id=external_user_id,
        display_name=profile.display_name,
        profile_url=profile_url,
    )

    row = upsert.upsert_participant(
        db,
        platform,
        external_user_id=profile.external_user_id,
        display_name=profile.display_name,
        profile_url=profile.profile_url,
    )
    apply_s95_participant_profile(
        row,
        profile,
        profile_extra={
            "friends_added": friends,
            "friends_of": friends_of,
            "awards": awards,
        },
        parser_version=upsert.PARSER_VERSION,
    )
    db.flush()

    result.friends_added = len(friends)
    result.friends_of = len(friends_of)
    result.awards = len(awards)
    result.runs_imported = upsert.import_profile_run_results(db, platform, runs)
    upsert.import_profile_volunteer_results(db, platform, row.id, volunteering)

    check_limit = (
        mismatch_check_limit
        if mismatch_check_limit is not None
        else settings.s95_athlete_mismatch_check_runs
    )
    refetch_result = refetch_mismatched_protocols(
        db,
        platform,
        row,
        runs,
        limit=check_limit,
    )
    vol_refetch = refetch_protocols_for_profile_volunteering(
        db,
        platform,
        volunteering,
        limit=check_limit,
    )
    refetch_result.protocols_refetched += vol_refetch.protocols_refetched
    if vol_refetch.errors:
        refetch_result.errors.extend(vol_refetch.errors)
    result.mismatches_found = len(refetch_result.mismatches)
    result.protocols_refetched = refetch_result.protocols_refetched
    if refetch_result.errors:
        result.errors.extend(refetch_result.errors)

    from app.sync.parkrun_participant_discovery import discover_parkrun_participant_from_barcode

    discovery = discover_parkrun_participant_from_barcode(db, profile.barcode_id)
    if discovery.error:
        result.errors.append(f"parkrun discovery: {discovery.error}")

    result.saved = True
    db.commit()
    return result
