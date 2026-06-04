from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.s95.parsers.events_registry import ParsedS95EventsPage, S95LocationRegistryStatus, S95RegistryEntry
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry


def _entry(slug: str, name: str, status: S95LocationRegistryStatus) -> S95RegistryEntry:
    return S95RegistryEntry(
        slug=slug,
        name=name,
        venue_name=name,
        source_url=f"https://s95.ru/events/{slug}",
        status=status,
    )


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_s95_sync_registry_updates_cancel_and_pause(db_session: Session, s95_platform: Platform) -> None:
    slug = f"registry-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Old Name",
        is_paused=False,
        is_cancelled=False,
        latitude=55.75,
        longitude=37.62,
        source_url=f"https://s95.ru/events/{slug}",
    )
    db_session.add(location)
    db_session.commit()

    page = ParsedS95EventsPage(
        entries=[_entry(slug, "Новое имя", S95LocationRegistryStatus.cancelled)],
    )

    with patch("app.sync.s95_locations_registry.fetch_page_html", return_value="<html></html>"):
        with patch("app.sync.s95_locations_registry.parse_events_registry_html", return_value=page):
            result = sync_s95_locations_registry(
                db_session,
                S95LocationRegistrySyncOptions(fetch_missing_coordinates=False, fetch_new_location_details=False),
            )

    db_session.refresh(location)
    assert result.entries_total == 1
    assert result.cancel_status_changed == 1
    assert location.name == "Новое имя"
    assert location.is_cancelled is True
    assert location.is_paused is False
