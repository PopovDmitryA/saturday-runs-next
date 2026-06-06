from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, LocationMergeRequest, LocationMergeRequestStatus, Platform
from app.platform_adapters.canonical import CanonicalLocation
from app.platform_adapters.five_verst.bulk_parser import (
    LocationRegistryStatus,
    ParsedEventsPage,
    ParsedRegistryEntry,
)
from app.sync.five_verst_locations import LocationRegistrySyncOptions, sync_locations_registry


def _entry(slug: str, name: str, status: LocationRegistryStatus) -> ParsedRegistryEntry:
    return ParsedRegistryEntry(
        slug=slug,
        name=name,
        source_url=f"https://5verst.ru/{slug}/",
        city_group="Test City",
        status=status,
    )


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def test_sync_registry_updates_pause_and_name(db_session: Session, five_verst_platform: Platform) -> None:
    slug = f"testpark-{uuid4().hex[:8]}"
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=slug,
        name="Old Name",
        is_paused=False,
        source_url=f"https://5verst.ru/{slug}/",
    )
    db_session.add(location)
    db_session.commit()

    page = ParsedEventsPage(
        entries=[_entry(slug, "Мемориальный парк", LocationRegistryStatus.paused)],
        saturday_cancellations=[],
    )

    with patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")):
        result = sync_locations_registry(
            db_session,
            LocationRegistrySyncOptions(fetch_missing_coordinates=False, detect_duplicates=False),
        )

    db_session.refresh(location)
    assert result.entries_total == 1
    assert result.locations_updated == 1
    assert result.pause_status_changed == 1
    assert result.cancel_status_changed == 0
    assert location.name == "Мемориальный парк"
    assert location.is_paused is True


def test_sync_registry_updates_cancel_status(db_session: Session, five_verst_platform: Platform) -> None:
    slug = f"cancelpark-{uuid4().hex[:8]}"
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=slug,
        name="Cancel Park",
        is_paused=False,
        source_url=f"https://5verst.ru/{slug}/",
    )
    db_session.add(location)
    db_session.commit()

    page = ParsedEventsPage(
        entries=[_entry(slug, "Cancel Park", LocationRegistryStatus.cancelled)],
        saturday_cancellations=[],
    )

    with patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")):
        result = sync_locations_registry(
            db_session,
            LocationRegistrySyncOptions(fetch_missing_coordinates=False, detect_duplicates=False),
        )

    assert result.cancel_status_changed == 1


def test_sync_registry_backfills_region_for_existing_location(
    db_session: Session, five_verst_platform: Platform
) -> None:
    slug = f"region-backfill-{uuid4().hex[:8]}"
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=slug,
        name="Region Backfill Park",
        latitude=55.75,
        longitude=37.62,
        is_paused=False,
        is_official_map=True,
        source_url=f"https://5verst.ru/{slug}/",
    )
    db_session.add(location)
    db_session.commit()

    from app.platform_adapters.five_verst.bulk_parser import ParsedEventsPage

    page = ParsedEventsPage(
        entries=[_entry(slug, "Region Backfill Park", LocationRegistryStatus.active)],
        saturday_cancellations=[],
    )

    with patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")):
        with patch(
            "app.services.location_geo_service.lookup_address",
            return_value={"country": "Россия", "region": "Москва", "city": "Москва"},
        ):
            result = sync_locations_registry(
                db_session,
                LocationRegistrySyncOptions(fetch_missing_coordinates=False, detect_duplicates=False),
            )

    db_session.refresh(location)
    assert result.regions_backfilled == 1
    assert location.region == "Москва"
    assert location.city == "Москва"


def test_sync_registry_skips_new_location_without_coords(db_session: Session, five_verst_platform: Platform) -> None:
    page = ParsedEventsPage(
        entries=[_entry("newparknosite", "New Park", LocationRegistryStatus.active)],
        saturday_cancellations=[],
    )
    canonical = CanonicalLocation(
        external_key="newparknosite",
        name="New Park",
        source_url="https://5verst.ru/newparknosite/",
    )

    with patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")):
        with patch("app.sync.five_verst_locations.bulk_parser.fetch_location", return_value=(canonical, "<html></html>")):
            result = sync_locations_registry(
                db_session,
                LocationRegistrySyncOptions(detect_duplicates=False),
            )

    row = (
        db_session.query(Location)
        .filter(
            Location.platform_id == five_verst_platform.id,
            Location.external_key == "newparknosite",
        )
        .one_or_none()
    )
    assert row is None
    assert result.locations_skipped_no_coords == 1
    assert result.locations_created == 0


def test_sync_registry_creates_merge_request_for_duplicate(db_session: Session, five_verst_platform: Platform) -> None:
    old_slug = f"oldslug-{uuid4().hex[:8]}"
    new_slug = f"newslug-{uuid4().hex[:8]}"
    existing = Location(
        platform_id=five_verst_platform.id,
        external_key=old_slug,
        name="Old Slug Park",
        latitude=55.0,
        longitude=37.0,
        is_official_map=True,
    )
    db_session.add(existing)
    db_session.flush()

    from app.models import EventSummary

    overlap_dates = (
        date(2099, 1, 6),
        date(2099, 1, 13),
        date(2099, 1, 20),
        date(2099, 1, 27),
        date(2099, 2, 3),
    )
    for event_date in overlap_dates:
        db_session.add(
            EventSummary(
                platform_id=five_verst_platform.id,
                location_id=existing.id,
                external_event_key=f"{old_slug}:1:{event_date.isoformat()}",
                event_date=event_date,
                event_number=1,
                summary_hash="abc",
            )
        )
    db_session.commit()

    page = ParsedEventsPage(
        entries=[_entry(new_slug, "New Slug Park", LocationRegistryStatus.active)],
        saturday_cancellations=[],
    )
    canonical = CanonicalLocation(
        external_key=new_slug,
        name="New Slug Park",
        latitude=55.1,
        longitude=37.1,
        source_url=f"https://5verst.ru/{new_slug}/",
    )

    from app.platform_adapters.canonical import CanonicalEventSummary

    summaries = [
        CanonicalEventSummary(
            external_event_key=f"{new_slug}:1:{event_date.isoformat()}",
            event_date=event_date,
            event_number=1,
            location_external_key=new_slug,
            location_name="New Slug Park",
            finishers_count=10,
            volunteers_count=5,
            avg_time_sec=1800,
            avg_time_display="00:30:00",
            best_female_time_sec=1500,
            best_female_time_display="00:25:00",
            best_male_time_sec=1400,
            best_male_time_display="00:23:20",
            summary_hash=f"hash-{event_date.isoformat()}",
            source_url=f"https://5verst.ru/{new_slug}/results/{event_date.strftime('%d.%m.%Y')}/",
        )
        for event_date in overlap_dates
    ]

    with patch("app.sync.five_verst_locations.bulk_parser.fetch_events_page", return_value=(page, "<html></html>")):
        with patch("app.sync.five_verst_locations.bulk_parser.fetch_location", return_value=(canonical, "<html></html>")):
            with patch(
                "app.sync.five_verst_locations.bulk_parser.fetch_event_summaries",
                return_value=(summaries, "<html></html>"),
            ):
                with patch("app.sync.five_verst_locations._notify_merge_request", return_value=True):
                    result = sync_locations_registry(db_session, LocationRegistrySyncOptions())

    merge = (
        db_session.query(LocationMergeRequest)
        .filter(LocationMergeRequest.candidate_slug == new_slug)
        .one_or_none()
    )
    assert result.merge_requests_created == 1
    assert merge is not None
    assert merge.matched_slug == old_slug
    assert merge.overlap_count == 5
    assert merge.status == LocationMergeRequestStatus.pending
