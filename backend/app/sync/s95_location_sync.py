from __future__ import annotations

import hashlib
import logging
import random

from sqlalchemy.orm import Session

from app.models import EventSummary
from app.s95.fetch import fetch_page_html
from app.s95.parsers.location import parse_event_location_page, parse_location_coordinates, parse_map_url
from app.s95.parsers.summary import parse_location_summary_html
from app.services.location_coordinate_service import maybe_request_coordinates_for_new_location
from app.sync import upsert
from app.sync.s95_global_sync import S95LocationSyncResult, _finish_sync_run, _start_sync_run
from app.sync.s95_protocol import fetch_and_upsert_activity_protocol
from app.sync.s95_summary_plan import SummarySyncAction, classify_event_summary, plan_protocol_queue

logger = logging.getLogger(__name__)

PLATFORM_CODE = "s95"

# Smoke / dev pool — same slugs as on https://s95.ru/events
LOCATION_POOL: list[tuple[str, str]] = [
    ("angarskie_prudy", "Ангарские пруды"),
    ("golyanovo", "Гольяново"),
    ("izmailovo", "Измайлово"),
    ("kuzminki", "Кузьминки"),
    ("zil", "ЗИЛ"),
    ("shhyolkovo", "Щёлково"),
]


def pick_random_location() -> tuple[str, str, str]:
    key, name = random.choice(LOCATION_POOL)
    url = f"https://s95.ru/events/{key}"
    return key, name, url


def sync_s95_event_location(
    db: Session,
    *,
    location_external_key: str,
    location_name: str,
    location_source_url: str,
    summaries_limit: int | None = 5,
    protocol_fetch_limit: int = 1,
    fetch_all_protocols_on_change: bool = True,
) -> S95LocationSyncResult:
    """Location page: coords + summary table + optional protocols (new/changed only)."""
    platform = upsert.get_platform(db, PLATFORM_CODE)
    result = S95LocationSyncResult(location_external_key=location_external_key)
    sync_run = _start_sync_run(db, platform, f"s95:event:{location_external_key}")

    try:
        map_html = fetch_page_html(
            location_source_url,
            reason="location_map",
            extra_wait_ms=8000,
            click_map_tab=True,
        )
        canonical = parse_event_location_page(
            map_html,
            location_source_url,
            location_external_key=location_external_key,
        )
        canonical.name = location_name or canonical.name
        lat, lon = parse_location_coordinates(map_html)
        if lat is not None:
            canonical.latitude = lat
            canonical.longitude = lon

        location_row, location_is_new = upsert.upsert_location(
            db,
            platform,
            canonical,
            source_hash=hashlib.sha256(map_html.encode()).hexdigest()[:32],
        )
        if location_row.latitude is None and location_row.longitude is None:
            maybe_request_coordinates_for_new_location(
                db,
                location_row,
                is_new=location_is_new,
                map_url=canonical.map_url or parse_map_url(map_html),
            )

        summary_html = fetch_page_html(location_source_url, reason="location_summary")
        summaries = parse_location_summary_html(
            summary_html,
            location_source_url,
            location_external_key=location_external_key,
            location_name=location_name,
        )
        if summaries_limit is not None:
            summaries = summaries[:summaries_limit]

        result.summaries_total = len(summaries)
        apply_items = []
        for summary in summaries:
            plan_item = classify_event_summary(db, platform, summary)
            summary_row, changed = upsert.upsert_event_summary(db, platform, location_row, summary)
            if changed or plan_item.action != SummarySyncAction.unchanged:
                result.summaries_upserted += 1
            elif plan_item.action == SummarySyncAction.unchanged:
                result.summaries_unchanged += 1
            if plan_item.action != SummarySyncAction.unchanged:
                apply_items.append(plan_item)

        protocol_queue = plan_protocol_queue(
            apply_items,
            protocol_fetch_limit=protocol_fetch_limit,
            fetch_all_protocols_on_change=fetch_all_protocols_on_change,
        )

        for item in protocol_queue:
            summary = item.summary
            summary_row = (
                db.query(EventSummary)
                .filter(
                    EventSummary.platform_id == platform.id,
                    EventSummary.external_event_key == summary.external_event_key,
                )
                .one()
            )
            if not summary.source_url:
                continue
            try:
                upsert_result = fetch_and_upsert_activity_protocol(
                    db,
                    platform,
                    location_row,
                    summary,
                    summary_row,
                )
                result.run_results_upserted += upsert_result.run_results_upserted
                result.volunteer_results_upserted += upsert_result.volunteer_results_upserted
                result.protocols_fetched += 1
            except Exception as exc:
                result.errors.append(f"{summary.external_event_key}: {exc}")

        sync_run.records_fetched = result.summaries_total
        sync_run.records_upserted = result.summaries_upserted + result.protocols_fetched
        sync_run.records_unchanged = result.summaries_unchanged
        _finish_sync_run(db, sync_run, success=not result.errors, error="; ".join(result.errors) or None)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        failed_run = _start_sync_run(db, platform, f"s95:event:{location_external_key}")
        _finish_sync_run(db, failed_run, success=False, error=str(exc))
        db.commit()
        raise
