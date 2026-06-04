from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.services.location_geo_service import apply_reverse_geocode_to_location, backfill_location_geo


@pytest.fixture
def five_verst_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if row is None:
        pytest.skip("five_verst platform not seeded")
    return row


def test_apply_reverse_geocode_to_location_fills_region(db_session: Session, five_verst_platform: Platform) -> None:
    location = Location(
        platform_id=five_verst_platform.id,
        external_key="geo-test-park",
        name="Geo Test Park",
        latitude=55.75,
        longitude=37.62,
        is_official_map=True,
    )
    db_session.add(location)
    db_session.flush()

    with patch(
        "app.services.location_geo_service.lookup_address",
        return_value={"country": "Россия", "region": "Москва", "city": "Москва"},
    ):
        assert apply_reverse_geocode_to_location(location) is True

    assert location.region == "Москва"
    assert location.city == "Москва"
    assert location.country == "Россия"


def test_backfill_location_geo_fills_missing_region(db_session: Session, five_verst_platform: Platform) -> None:
    location = Location(
        platform_id=five_verst_platform.id,
        external_key=f"geo-backfill-{__import__('uuid').uuid4().hex[:8]}",
        name="Backfill Target Park",
        latitude=55.75,
        longitude=37.62,
        is_official_map=True,
    )
    db_session.add(location)
    db_session.commit()

    with patch(
        "app.services.location_geo_service.lookup_address",
        return_value={"country": "Россия", "region": "Московская область", "city": "Мытищи"},
    ):
        result = backfill_location_geo(db_session, limit=200)

    db_session.refresh(location)
    assert location.region == "Московская область"
    assert location.city == "Мытищи"
    assert result.regions_backfilled >= 1
