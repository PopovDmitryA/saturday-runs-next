#!/usr/bin/env python3
"""Fetch S95 protocols for an athlete's most recent runs and upsert into global DB."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import PlatformLink, User
from app.platform_adapters.canonical import CanonicalLocation
from app.platform_adapters.s95.parser import fetch_athlete_activity
from app.platform_adapters.s95.url import athlete_url
from app.services.dashboard_service import recompute_dashboard_cache
from app.s95.fetch import fetch_page_html
from app.s95.parsers.protocol import parse_protocol_html
from app.s95.parsers.summary import parse_location_summary_html
from app.sync import upsert

PLATFORM_CODE = "s95"


def _location_summary_protocol_url(
    location_key: str,
    location_name: str,
    event_date: date,
) -> tuple[str | None, int | None]:
    if not location_key:
        return None, None
    page_url = f"https://s95.ru/events/{location_key}"
    html = fetch_page_html(page_url, reason="athlete_protocol_lookup")
    summaries = parse_location_summary_html(
        html,
        page_url,
        location_external_key=location_key,
        location_name=location_name,
    )
    for summary in summaries:
        if summary.event_date == event_date:
            return summary.source_url, summary.event_number
    return None, None


def sync_athlete_protocols(
    db,
    *,
    external_user_id: str,
    limit: int = 3,
    recompute_user_id: str | None = None,
) -> dict[str, object]:
    profile, runs, _volunteering = fetch_athlete_activity(external_user_id)
    if not runs:
        return {"error": "no_runs_on_profile", "athlete_id": external_user_id}

    recent = sorted(runs, key=lambda item: item.event_date, reverse=True)[:limit]
    platform = upsert.get_platform(db, PLATFORM_CODE)
    results: list[dict[str, object]] = []

    for run in recent:
        location_key = (run.location_external_key or "").strip()
        location_name = run.location_name or location_key or "unknown"
        protocol_url, event_number = _location_summary_protocol_url(
            location_key,
            location_name,
            run.event_date,
        )
        if not protocol_url:
            results.append(
                {
                    "event_date": run.event_date.isoformat(),
                    "location": location_name,
                    "status": "protocol_url_not_found",
                }
            )
            continue

        event_number = event_number or run.event_number
        location_row, _ = upsert.upsert_location(
            db,
            platform,
            CanonicalLocation(
                external_key=location_key or location_name.lower().replace(" ", "_"),
                name=location_name,
                source_url=f"https://s95.ru/events/{location_key}" if location_key else None,
            ),
        )
        external_event_key = f"{location_row.external_key}:{run.event_date.isoformat()}"
        summary_hash = f"athlete-protocol:{external_user_id}:{external_event_key}"
        from app.platform_adapters.canonical import CanonicalEventSummary

        summary = CanonicalEventSummary(
            external_event_key=external_event_key,
            event_date=run.event_date,
            event_number=event_number,
            location_external_key=location_row.external_key,
            location_name=location_name,
            source_url=protocol_url,
            summary_hash=summary_hash,
        )
        summary_row, _changed = upsert.upsert_event_summary(db, platform, location_row, summary)
        protocol_html = fetch_page_html(protocol_url, reason="athlete_protocol")
        run_results, volunteer_results = parse_protocol_html(
            protocol_html,
            protocol_url,
            location_external_key=location_row.external_key,
            location_name=location_name,
            event_date=run.event_date,
            event_number=event_number,
        )
        event_row = upsert.upsert_event_for_summary(db, platform, location_row, summary, summary_row)
        runs_upserted = upsert.upsert_run_results(db, event_row, platform, run_results)
        vols_upserted = upsert.upsert_volunteer_results(db, event_row, platform, volunteer_results)
        athlete_run = next(
            (
                item
                for item in run_results
                if item.external_user_id == external_user_id
                or item.external_user_id == profile.external_user_id
            ),
            None,
        )
        results.append(
            {
                "event_date": run.event_date.isoformat(),
                "location": location_name,
                "protocol_url": protocol_url,
                "event_number": event_number,
                "parsed_runs": len(run_results),
                "runs_upserted": runs_upserted,
                "volunteers_upserted": vols_upserted,
                "athlete_in_protocol": athlete_run is not None,
                "athlete_position": athlete_run.position if athlete_run else None,
                "athlete_time": athlete_run.finish_time_display if athlete_run else None,
                "status": "ok",
            }
        )

    if recompute_user_id:
        from uuid import UUID

        recompute_dashboard_cache(db, UUID(recompute_user_id))

    db.commit()
    return {
        "athlete_id": external_user_id,
        "display_name": profile.display_name,
        "profile_url": athlete_url(external_user_id),
        "protocols": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("athlete_id", help="S95 athlete id, e.g. 5207")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--recompute-for-linked-user",
        action="store_true",
        help="Recompute dashboard cache for users linked to this athlete",
    )
    args = parser.parse_args()

    db = get_session_factory()()
    try:
        user_ids: list[str] = []
        if args.recompute_for_linked_user:
            platform = upsert.get_platform(db, PLATFORM_CODE)
            links = (
                db.query(PlatformLink, User)
                .join(User, PlatformLink.user_id == User.id)
                .filter(
                    PlatformLink.platform_id == platform.id,
                    PlatformLink.external_user_id == args.athlete_id,
                )
                .all()
            )
            user_ids = [str(link.user_id) for link, _user in links]

        payload = sync_athlete_protocols(
            db,
            external_user_id=args.athlete_id,
            limit=args.limit,
            recompute_user_id=user_ids[0] if user_ids else None,
        )
        for uid in user_ids[1:]:
            from uuid import UUID

            recompute_dashboard_cache(db, UUID(uid))
            db.commit()

        print(payload)
        if any(item.get("status") != "ok" for item in payload.get("protocols", [])):
            return 2
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
