from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Event, EventSummary, Platform, RunResult, SyncStatus, VolunteerResult
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalLocation,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.platform_adapters.five_verst import bulk_parser
from app.sync import upsert
from app.sync.global_sync import LocationSyncOptions, sync_location


def _location(slug: str) -> CanonicalLocation:
    name = f"Test Park {slug}"
    return CanonicalLocation(
        external_key=slug,
        name=name,
        city="Москва",
        latitude=55.0,
        longitude=37.0,
        source_url=f"https://5verst.ru/{slug}/",
    )


def _summary(*, slug: str, event_number: int, finishers: int = 48) -> CanonicalEventSummary:
    location_name = f"Test Park {slug}"
    summary_hash = bulk_parser.compute_summary_hash(
        event_number=event_number,
        event_date=date(2026, 5, 23),
        finishers_count=finishers,
        volunteers_count=22,
        avg_time_sec=1728,
        best_female_time_sec=1412,
        best_male_time_sec=1139,
    )
    return CanonicalEventSummary(
        external_event_key=f"{slug}:2026-05-23:{event_number}",
        event_date=date(2026, 5, 23),
        event_number=event_number,
        location_external_key=slug,
        location_name=location_name,
        finishers_count=finishers,
        volunteers_count=22,
        avg_time_sec=1728,
        avg_time_display="00:28:48",
        best_female_time_sec=1412,
        best_female_time_display="00:23:32",
        best_male_time_sec=1139,
        best_male_time_display="00:18:59",
        source_url=f"https://5verst.ru/{slug}/results/23.05.2026/",
        summary_hash=summary_hash,
    )


@pytest.fixture
def sync_slug() -> str:
    return f"testpark-{uuid4().hex[:8]}"

@pytest.fixture
def platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def test_upsert_event_summary_skips_unchanged_hash(db_session: Session, platform: Platform, sync_slug: str) -> None:
    location = _location(sync_slug)
    location_row, _ = upsert.upsert_location(db_session, platform, location, source_hash="loc-hash-1")
    summary = _summary(slug=sync_slug, event_number=1)

    first_row, first_changed = upsert.upsert_event_summary(db_session, platform, location_row, summary)
    db_session.commit()

    second_row, second_changed = upsert.upsert_event_summary(db_session, platform, location_row, summary)

    assert first_changed is True
    assert second_changed is False
    assert second_row.id == first_row.id
    assert second_row.sync_status == SyncStatus.unchanged


def test_upsert_event_summary_updates_when_hash_changes(db_session: Session, platform: Platform, sync_slug: str) -> None:
    location = _location(sync_slug)
    location_row, _ = upsert.upsert_location(db_session, platform, location, source_hash="loc-hash-2")
    summary = _summary(slug=sync_slug, event_number=2, finishers=48)
    upsert.upsert_event_summary(db_session, platform, location_row, summary)
    db_session.commit()

    updated = _summary(slug=sync_slug, event_number=2, finishers=50)
    row, changed = upsert.upsert_event_summary(db_session, platform, location_row, updated)

    assert changed is True
    assert row.finishers_count == 50
    assert row.sync_status == SyncStatus.ok


def test_sync_location_fetches_protocol_only_for_pending_summaries(
    db_session: Session, platform: Platform, sync_slug: str
) -> None:
    location = _location(sync_slug)
    summary = _summary(slug=sync_slug, event_number=3)
    run_result = CanonicalRunResult(
        external_result_key=f"{sync_slug}:2026-05-23:3:790087870",
        event_date=date(2026, 5, 23),
        external_user_id="790087870",
        participant_name="Ali PAPAKHOV",
        position=1,
        finish_time_sec=1139,
        finish_time_display="00:18:59",
        location_external_key=sync_slug,
        event_number=3,
    )
    volunteer = CanonicalVolunteerResult(
        external_result_key=f"{sync_slug}:2026-05-23:3:790115702:organizer",
        event_date=date(2026, 5, 23),
        external_user_id="790115702",
        participant_name="Елена МУХАНОВА",
        role="Организатор",
        location_external_key=sync_slug,
        event_number=3,
    )

    with (
        patch(
            "app.sync.global_sync.bulk_parser.fetch_location",
            return_value=(location, "<html></html>"),
        ),
        patch(
            "app.sync.global_sync.bulk_parser.fetch_event_summaries",
            return_value=([summary], "<table></table>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_run_protocol",
            return_value=([run_result], "<table></table>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_volunteers",
            return_value=([volunteer], "<table></table>"),
        ),
        patch(
            "app.sync.five_verst_protocol.bulk_parser.source_hash",
            return_value="protocol-hash",
        ),
    ):
        first = sync_location(
            db_session,
            LocationSyncOptions(location_slug=sync_slug, summaries_limit=1, protocol_fetch_limit=1),
        )
        db_session.commit()

        second = sync_location(
            db_session,
            LocationSyncOptions(location_slug=sync_slug, summaries_limit=1, protocol_fetch_limit=1),
        )
        db_session.commit()

    assert first.protocols_fetched == 1
    assert first.run_results_upserted == 1
    assert second.summaries_unchanged == 1
    assert second.protocols_fetched == 0

    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 1
    assert db_session.query(VolunteerResult).filter(VolunteerResult.event_id == event.id).count() == 1


def test_sync_location_summaries_only_skips_protocol(db_session: Session, platform: Platform, sync_slug: str) -> None:
    location = _location(sync_slug)
    summary = _summary(slug=sync_slug, event_number=4)

    with (
        patch(
            "app.sync.global_sync.bulk_parser.fetch_location",
            return_value=(location, "<html></html>"),
        ),
        patch(
            "app.sync.global_sync.bulk_parser.fetch_event_summaries",
            return_value=([summary], "<table></table>"),
        ),
        patch("app.sync.global_sync.fetch_and_upsert_event_protocol") as fetch_protocol,
    ):
        result = sync_location(
            db_session,
            LocationSyncOptions(location_slug=sync_slug, summaries_limit=1, protocol_fetch_limit=0),
        )
        db_session.commit()

    assert result.summaries_upserted == 1
    assert result.protocols_fetched == 0
    fetch_protocol.assert_not_called()

    summary_row = (
        db_session.query(EventSummary)
        .filter(EventSummary.external_event_key == summary.external_event_key)
        .one()
    )
    assert summary_row.event_id is None
