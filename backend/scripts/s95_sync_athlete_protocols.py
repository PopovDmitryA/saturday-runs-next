#!/usr/bin/env python3
"""Fetch S95 protocols (via JSON API) for an athlete's most recent runs and upsert
into the global DB. Useful to backfill/repair specific events for one athlete."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db.session import get_session_factory
from app.models import PlatformLink, User
from app.platform_adapters.s95.parser import fetch_athlete_activity
from app.platform_adapters.s95.url import athlete_url
from app.services.dashboard_service import recompute_dashboard_cache
from app.sync import upsert
from app.sync.s95_protocol_api import fetch_and_upsert_activity_protocol_api
from app.sync.s95_protocol_lookup import resolve_s95_protocol

PLATFORM_CODE = "s95"


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
        location_key = upsert._normalize_location_slug(run.location_external_key, run.location_name)
        location_name = run.location_name or location_key

        resolved = resolve_s95_protocol(
            db,
            platform,
            location_slug=location_key,
            location_name=location_name,
            event_date=run.event_date,
        )
        if resolved is None:
            results.append(
                {
                    "event_date": run.event_date.isoformat(),
                    "location": location_name,
                    "status": "protocol_url_not_found",
                }
            )
            continue

        fetch_result = fetch_and_upsert_activity_protocol_api(
            db,
            platform,
            resolved.location,
            resolved.summary,
            resolved.summary_row,
            protocol_url=resolved.protocol_url,
        )
        results.append(
            {
                "event_date": run.event_date.isoformat(),
                "location": location_name,
                "protocol_url": resolved.protocol_url,
                "run_results_count": fetch_result.run_results_upserted,
                "volunteer_results_count": fetch_result.volunteer_results_upserted,
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
