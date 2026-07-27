"""География локации при синке: страна/регион/город заполняются в upsert.

Кратово (s95) приехало 26.07.2026 без страны: s95 её не отдаёт, а геокод в
upsert доставал только регион. Страну заполнял лишь путь через реестр локаций,
а Кратово нашлось через протокол — там этого вызова не было.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.platform_adapters.canonical import CanonicalLocation
from app.sync import upsert

МОСКОВСКИЙ_АДРЕС = {"country": "Россия", "region": "Московская", "city": "Раменское"}


@pytest.fixture
def s95_platform(db_session: Session) -> Platform:
    row = db_session.query(Platform).filter(Platform.code == "s95").one_or_none()
    if row is None:
        pytest.skip("s95 platform not seeded")
    return row


def _canonical(**kwargs) -> CanonicalLocation:
    payload = {
        "external_key": f"geo-{uuid4().hex[:8]}",
        "name": "Кратово",
        "latitude": 55.586661,
        "longitude": 38.158979,
    }
    payload.update(kwargs)
    return CanonicalLocation(**payload)


def test_new_location_gets_country_from_geocode(db_session: Session, s95_platform: Platform) -> None:
    """s95 отдаёт только город — страну и регион добираем по координатам."""
    canonical = _canonical(city="Раменское")

    with patch("app.geo.reverse_geocode.lookup_address", return_value=МОСКОВСКИЙ_АДРЕС):
        row, created = upsert.upsert_location(db_session, s95_platform, canonical)

    assert created is True
    assert row.country == "Россия"
    assert row.region == "Московская"
    assert row.city == "Раменское"


def test_geocode_skipped_when_everything_known(db_session: Session, s95_platform: Platform) -> None:
    """Полный адрес от системы — в сеть не ходим."""
    canonical = _canonical(country="Россия", region="Московская", city="Раменское")

    with patch("app.geo.reverse_geocode.lookup_address") as геокод:
        row, _ = upsert.upsert_location(db_session, s95_platform, canonical)

    геокод.assert_not_called()
    assert row.country == "Россия"


def test_update_keeps_known_country(db_session: Session, s95_platform: Platform) -> None:
    """Повторный синк без страны в данных не должен её стирать.

    Город и регион и раньше переживали обновление, страна — нет: она
    присваивалась напрямую из канонической записи.
    """
    key = f"geo-{uuid4().hex[:8]}"
    existing = Location(
        platform_id=s95_platform.id,
        external_key=key,
        name="Кратово",
        country="Россия",
        region="Московская",
        city="Раменское",
        latitude=55.586661,
        longitude=38.158979,
    )
    db_session.add(existing)
    db_session.flush()

    canonical = _canonical(external_key=key, city="Раменское")
    with patch("app.geo.reverse_geocode.lookup_address", return_value=МОСКОВСКИЙ_АДРЕС):
        row, _ = upsert.upsert_location(db_session, s95_platform, canonical, source_hash="new-hash")

    assert row.country == "Россия"


def test_unchanged_row_backfills_geo_gaps(db_session: Session, s95_platform: Platform) -> None:
    """Строка без страны дозаполняется, даже если хэш источника прежний."""
    key = f"geo-{uuid4().hex[:8]}"
    existing = Location(
        platform_id=s95_platform.id,
        external_key=key,
        name="Кратово",
        region="Московская",
        city="Раменское",
        latitude=55.586661,
        longitude=38.158979,
        source_hash="same-hash",
    )
    db_session.add(existing)
    db_session.flush()

    canonical = _canonical(external_key=key, city="Раменское")
    with patch("app.geo.reverse_geocode.lookup_address", return_value=МОСКОВСКИЙ_АДРЕС):
        row, changed = upsert.upsert_location(db_session, s95_platform, canonical, source_hash="same-hash")

    assert changed is False
    assert row.country == "Россия"
