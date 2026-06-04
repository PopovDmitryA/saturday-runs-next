from __future__ import annotations

from app.activity_date import has_real_activity_date
from app.pace import resolve_run_pace
from app.platform_adapters.canonical import (
    CanonicalRunResult,
    CanonicalVolunteerResult,
    ProfilePreviewActivity,
)

PREVIEW_RECENT_ACTIVITY_LIMIT = 3


def build_recent_preview_activities(
    runs: list[CanonicalRunResult],
    volunteering: list[CanonicalVolunteerResult],
    *,
    limit: int = PREVIEW_RECENT_ACTIVITY_LIMIT,
) -> list[ProfilePreviewActivity]:
    items: list[ProfilePreviewActivity] = []

    for run in runs:
        location = (run.location_name or "").strip() or "—"
        finish_display = run.finish_time_display
        if finish_display is None and run.finish_time_sec is not None:
            _, finish_display = resolve_run_pace(None, None, run.finish_time_sec)
        items.append(
            ProfilePreviewActivity(
                kind="run",
                event_date=run.event_date,
                location_name=location,
                finish_time_display=finish_display,
            )
        )

    for vol in volunteering:
        if not has_real_activity_date(vol.event_date):
            continue
        location = (vol.location_name or "").strip() or "—"
        role = (vol.role or "").strip() or None
        items.append(
            ProfilePreviewActivity(
                kind="volunteer",
                event_date=vol.event_date,
                location_name=location,
                role=role,
            )
        )

    items.sort(key=lambda item: item.event_date, reverse=True)
    return items[:limit]
