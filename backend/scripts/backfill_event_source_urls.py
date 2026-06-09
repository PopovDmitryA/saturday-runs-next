#!/usr/bin/env python3
"""Backfill events.source_url from event_summaries protocol links (S95 /activities/)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_

from app.activity_url import prefer_event_source_url
from app.db.session import get_session_factory
from app.models import Event, EventSummary, Platform


def main() -> int:
    db = get_session_factory()()
    try:
        rows = (
            db.query(Event, EventSummary, Platform.code)
            .join(
                EventSummary,
                (EventSummary.platform_id == Event.platform_id)
                & (EventSummary.location_id == Event.location_id)
                & (EventSummary.event_date == Event.event_date),
            )
            .join(Platform, Platform.id == Event.platform_id)
            .filter(
                Platform.code.in_(("s95", "five_verst")),
                EventSummary.source_url.isnot(None),
                EventSummary.source_url != "",
                or_(
                    Event.source_url.is_(None),
                    Event.source_url == "",
                    Event.source_url != EventSummary.source_url,
                ),
            )
            .all()
        )
        updated = 0
        for event, summary, platform_code in rows:
            preferred = prefer_event_source_url(platform_code, event.source_url, summary.source_url)
            if preferred and preferred != (event.source_url or ""):
                event.source_url = preferred
                updated += 1
        db.commit()
        print(f"events_updated={updated}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
