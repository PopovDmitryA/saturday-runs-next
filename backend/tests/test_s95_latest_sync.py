from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import EventSummary, Location, Platform
from app.platform_adapters.canonical import CanonicalEventSummary
from app.sync.s95_latest import S95LatestSyncOptions, sync_s95_latest
from app.sync.s95_protocol import S95ProtocolUpsertResult
from app.sync.s95_summary_plan import SummarySyncAction, classify_event_summary


def _summary(slug: str, event_date: date, activity_id: int, finishers: int) -> CanonicalEventSummary:
    external_event_key = f"{slug}:{event_date.isoformat()}"
    return CanonicalEventSummary(
        external_event_key=external_event_key,
        event_date=event_date,
        event_number=activity_id,
        location_external_key=slug,
        location_name="Test Park",
        finishers_count=finishers,
        volunteers_count=5,
        source_url=f"https://s95.ru/activities/{activity_id}",
        summary_hash=f"hash-{activity_id}-{finishers}",
    )


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_sync_s95_latest_fetches_protocol_for_changed_summary(
    db_session: Session,
    s95_platform: Platform,
) -> None:
    slug = f"latest-{uuid4().hex[:8]}"
    event_date = date(2099, 6, 1)
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Test Park",
        source_url=f"https://s95.ru/events/{slug}",
    )
    db_session.add(location)
    db_session.flush()
    summary = _summary(slug, event_date, 9001, finishers=10)
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=event_date,
            event_number=9001,
            finishers_count=10,
            volunteers_count=5,
            summary_hash="old-hash",
            source_url=summary.source_url,
        )
    )
    db_session.commit()

    changed = _summary(slug, event_date, 9001, finishers=12)
    protocol_result = S95ProtocolUpsertResult(
        event_id="test",
        run_results_upserted=1,
        volunteer_results_upserted=0,
        run_results_count=1,
        volunteer_results_count=0,
        protocol_source_hash="abc",
        protocol_changed=True,
    )

    with patch("app.sync.s95_latest.fetch_latest_activity_summaries", return_value=[changed]):
        with patch(
            "app.sync.s95_latest.fetch_and_upsert_activity_protocol",
            return_value=protocol_result,
        ) as fetch_protocol:
            result = sync_s95_latest(
                db_session,
                S95LatestSyncOptions(protocol_fetch_limit=3, update_limit=5),
            )

    assert result.changed_summaries == 1
    assert result.protocols_fetched == 1
    assert fetch_protocol.called


def test_classify_missing_protocol(db_session: Session, s95_platform: Platform) -> None:
    slug = f"missing-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Missing Protocol Park",
    )
    db_session.add(location)
    db_session.flush()
    summary = _summary(slug, date(2099, 7, 1), 9002, finishers=8)
    db_session.add(
        EventSummary(
            platform_id=s95_platform.id,
            location_id=location.id,
            external_event_key=summary.external_event_key,
            event_date=summary.event_date,
            event_number=9002,
            finishers_count=8,
            volunteers_count=2,
            summary_hash=summary.summary_hash,
            source_url=summary.source_url,
        )
    )
    db_session.commit()

    item = classify_event_summary(db_session, s95_platform, summary)
    assert item.action == SummarySyncAction.missing_protocol
