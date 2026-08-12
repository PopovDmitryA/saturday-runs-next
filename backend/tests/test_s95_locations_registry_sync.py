from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import Location, Platform
from app.s95.api_client import S95ApiLocation
from app.sync.s95_locations_registry import S95LocationRegistrySyncOptions, sync_s95_locations_registry


def _api_location(
    slug: str,
    name: str,
    active: bool,
    lat: float | None = None,
    lon: float | None = None,
    domain: str = "https://s95.ru",
) -> S95ApiLocation:
    return S95ApiLocation(
        domain=domain,
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


def test_s95_sync_registry_heals_official_map_flag(db_session: Session, s95_platform: Platform) -> None:
    """Координаты есть, а флага нет — синк реестра это чинит.

    Так выглядит локация, которую первой увидел не реестр, а протокол: строка
    рождается с координатами, но без флага, и потому не попадает ни на карту, ни
    в каталог. Прежде реестр трогал флаг только когда сам дозаполнял координаты,
    поэтому такая строка оставалась невидимой навсегда (так пропало Кратово).
    """
    slug = f"heal-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Кратово",
        is_paused=False,
        is_cancelled=False,
        latitude=55.586661,
        longitude=38.158979,
        is_official_map=False,
        source_url=f"https://s95.ru/events/{slug}",
    )
    db_session.add(location)
    db_session.commit()

    entries = [_api_location(slug, "Кратово", active=True, lat=55.586661, lon=38.158979)]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    db_session.refresh(location)
    assert location.is_official_map is True


def test_s95_sync_registry_keeps_flag_off_without_coords(db_session: Session, s95_platform: Platform) -> None:
    """Разъездным сериям («С95 и друзья») на карте по-прежнему не место."""
    slug = f"noheal-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="С95 и друзья",
        is_paused=False,
        is_cancelled=False,
        is_official_map=False,
        source_url=f"https://s95.ru/events/{slug}",
    )
    db_session.add(location)
    db_session.commit()

    entries = [_api_location(slug, "С95 и друзья", active=True)]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    db_session.refresh(location)
    assert location.is_official_map is False


def test_s95_sync_registry_sets_country_from_domain(db_session: Session, s95_platform: Platform) -> None:
    """Новая зарубежная локация рождается со своей страной, а не с «Россией»."""
    slug = f"belgrade-{uuid4().hex[:8]}"
    entries = [
        _api_location(slug, "Belgrade", active=True, lat=44.819492, lon=20.443871, domain="https://s95.rs")
    ]

    with (
        patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries),
        patch("app.geo.reverse_geocode.lookup_address", return_value={"city": "Белград", "region": "Город Белград"}),
    ):
        sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    row = db_session.query(Location).filter(
        Location.platform_id == s95_platform.id,
        Location.external_key == slug,
    ).one()
    assert row.country == "Сербия"


def test_s95_sync_registry_fixes_wrong_country(db_session: Session, s95_platform: Platform) -> None:
    """Домен реестра перебивает уже записанную страну.

    Так лечится Белград и Гродно: город с регионом геокод определил верно, а
    страна осталась «Россией» с тех времён, когда её никто не проставлял, и
    upsert_location дозаполняет только пустое.
    """
    slug = f"grodno-{uuid4().hex[:8]}"
    location = Location(
        platform_id=s95_platform.id,
        external_key=slug,
        name="Гродно",
        country="Россия",
        city="Гродно",
        region="Гродненская",
        latitude=53.688222,
        longitude=23.787598,
        is_official_map=True,
        source_url=f"https://s95.by/events/{slug}",
    )
    db_session.add(location)
    db_session.commit()

    entries = [
        _api_location(slug, "Гродно", active=True, lat=53.688222, lon=23.787598, domain="https://s95.by")
    ]

    with patch("app.sync.s95_locations_registry.fetch_all_locations", return_value=entries):
        result = sync_s95_locations_registry(db_session, S95LocationRegistrySyncOptions())

    db_session.refresh(location)
    assert location.country == "Беларусь"
    assert location.city == "Гродно"
    assert result.locations_updated == 1


def test_s95_protocol_discovery_marks_official_map(db_session: Session, s95_platform: Platform) -> None:
    """Локация, найденная через протокол, сразу попадает на карту.

    Координаты в этой ветке приходят из того же реестра s95, что и у синка
    локаций, — ждать его ближайшего прогона, чтобы показать точку, незачем.
    """
    from app.sync.s95_global_sync_api import _ensure_location

    slug = f"protocol-{uuid4().hex[:8]}"
    entry = _api_location(slug, "Кратово", active=True, lat=55.586661, lon=38.158979)

    with patch("app.geo.reverse_geocode.lookup_address", return_value={"country": "Россия", "region": "Московская", "city": "Раменское"}):
        row = _ensure_location(db_session, s95_platform, entry)

    assert row.is_official_map is True
    assert row.country == "Россия"
