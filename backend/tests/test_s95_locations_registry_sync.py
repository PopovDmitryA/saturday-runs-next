from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.s95.api_client import S95ApiLocation
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry


def _api_location(slug: str, name: str, active: bool, lat: float | None = None, lon: float | None = None) -> S95ApiLocation:
    return S95ApiLocation(
        domain="https://s95.ru",
        slug=slug,
        name=name,
        town="Москва",
        place="Парк",
        active=active,
        latitude=lat,
        longitude=lon,
    )


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def test_s95_sync_registry_sets_cancelled_when_inactive(db_session: Session, s95_platform: Platform) -> None:
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

    entries = [_api_location(slug, "Новое имя", active=False)]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        result = sync_s95_locations_registry(
            db_session,
            S95LocationRegistrySyncOptions(),
        )

    db_session.refresh(location)
    assert result.entries_total == 1
    assert result.cancel_status_changed == 1
    assert location.name == "Новое имя"
    assert location.is_cancelled is True
    assert location.is_paused is False


def test_s95_sync_registry_creates_new_location(db_session: Session, s95_platform: Platform) -> None:
    slug = f"new-{uuid4().hex[:8]}"
    entries = [_api_location(slug, "Новая локация", active=True, lat=55.75, lon=37.62)]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        result = sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    assert result.locations_created == 1
    row = db_session.query(Location).filter(
        Location.platform_id == s95_platform.id,
        Location.external_key == slug,
    ).one()
    assert row.latitude == 55.75
    assert row.is_cancelled is False


def test_s95_sync_registry_creates_location_without_coords(db_session: Session, s95_platform: Platform) -> None:
    slug = f"nocoords-{uuid4().hex[:8]}"
    entries = [_api_location(slug, "С95 и друзья", active=True)]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        result = sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    assert result.locations_created == 1
    row = db_session.query(Location).filter(
        Location.platform_id == s95_platform.id,
        Location.external_key == slug,
    ).one()
    assert row.latitude is None
