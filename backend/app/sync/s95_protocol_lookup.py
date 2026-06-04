from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform
from app.platform_adapters.canonical import CanonicalEventSummary
from app.s95.fetch import fetch_page_html
from app.s95.parsers.summary import parse_location_summary_html

ACTIVITY_URL_RE = re.compile(r"/activities/(\d+)")


@dataclass(frozen=True)
class ResolvedS95Protocol:
    protocol_url: str
    summary: CanonicalEventSummary
    summary_row: EventSummary
    location: Location


def _s95_external_event_key(slug: str, event_date) -> str:
    return f"{slug}:{event_date.isoformat()}"


def _summary_to_canonical(summary_row: EventSummary, location: Location) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=summary_row.external_event_key,
        event_date=summary_row.event_date,
        event_number=summary_row.event_number,
        location_external_key=location.external_key,
        location_name=location.name,
        finishers_count=summary_row.finishers_count,
        volunteers_count=summary_row.volunteers_count,
        avg_time_sec=summary_row.avg_time_sec,
        avg_time_display=summary_row.avg_time_display,
        best_female_time_sec=summary_row.best_female_time_sec,
        best_female_time_display=summary_row.best_female_time_display,
        best_male_time_sec=summary_row.best_male_time_sec,
        best_male_time_display=summary_row.best_male_time_display,
        is_test_event=summary_row.is_test_event,
        source_url=summary_row.source_url or "",
        summary_hash=summary_row.summary_hash,
        summary_extra=summary_row.summary_extra or {},
    )


def _lookup_summary_from_location_page(
    db: Session,
    platform: Platform,
    location: Location,
    event_date,
) -> EventSummary | None:
    page_url = location.source_url or f"https://s95.ru/events/{location.external_key}"
    html = fetch_page_html(page_url, reason="athlete_protocol_lookup")
    summaries = parse_location_summary_html(
        html,
        page_url,
        location_external_key=location.external_key,
        location_name=location.name,
    )
    for summary in summaries:
        if summary.event_date != event_date:
            continue
        from app.sync import upsert

        summary_row, _ = upsert.upsert_event_summary(db, platform, location, summary)
        db.flush()
        return summary_row
    return None


def resolve_s95_protocol(
    db: Session,
    platform: Platform,
    *,
    location_slug: str,
    location_name: str,
    event_date,
) -> ResolvedS95Protocol | None:
    from app.sync import upsert
    from app.platform_adapters.canonical import CanonicalLocation

    location = (
        db.query(Location)
        .filter(
            Location.platform_id == platform.id,
            Location.external_key == location_slug,
        )
        .one_or_none()
    )
    if location is None:
        location, _ = upsert.upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=location_slug,
                name=location_name or location_slug,
                source_url=f"https://s95.ru/events/{location_slug}",
            ),
        )

    summary_row = (
        db.query(EventSummary)
        .filter(
            EventSummary.platform_id == platform.id,
            EventSummary.location_id == location.id,
            EventSummary.event_date == event_date,
        )
        .one_or_none()
    )
    if summary_row is None:
        external_event_key = _s95_external_event_key(location.external_key, event_date)
        summary_row = (
            db.query(EventSummary)
            .filter(
                EventSummary.platform_id == platform.id,
                EventSummary.external_event_key == external_event_key,
            )
            .one_or_none()
        )

    protocol_url = summary_row.source_url if summary_row and summary_row.source_url else None
    if not protocol_url or not ACTIVITY_URL_RE.search(protocol_url):
        summary_row = _lookup_summary_from_location_page(db, platform, location, event_date)
        protocol_url = summary_row.source_url if summary_row else None

    if summary_row is None or not protocol_url or not ACTIVITY_URL_RE.search(protocol_url):
        return None

    summary = _summary_to_canonical(summary_row, location)
    summary.source_url = protocol_url
    return ResolvedS95Protocol(
        protocol_url=protocol_url,
        summary=summary,
        summary_row=summary_row,
        location=location,
    )
