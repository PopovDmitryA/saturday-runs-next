"""Свежесть витрин локации после записи новых результатов."""

from __future__ import annotations

from uuid import uuid4

import fakeredis
import pytest

from app.services.location_freshness import (
    STALE_AFTER_WRITE_SECONDS,
    STALE_AFTER_WRITE_SECONDS_SHOWCASE,
    _cap_ttl,
    identity_slugs_for_locations,
    mark_location_results_changed,
)
from app.services.location_page_service import (
    LAST_RESULTS_CACHE_KEY,
    LOCATIONS_INDEX_CACHE_KEY,
    location_events_cache_key,
    location_leaders_cache_key,
    location_page_cache_key,
)


def test_cap_ttl_shortens_only_long_lived_keys(fake_redis: fakeredis.FakeRedis) -> None:
    fake_redis.setex("long", 3 * 60 * 60, "1")
    fake_redis.setex("short", 30, "1")

    assert _cap_ttl(["long", "short", "missing"], STALE_AFTER_WRITE_SECONDS) == 1

    assert fake_redis.ttl("long") <= STALE_AFTER_WRITE_SECONDS
    # Уже почти протухший снимок не продлеваем — только укорачиваем.
    assert 0 < fake_redis.ttl("short") <= 30
    assert fake_redis.ttl("missing") == -2


def test_cap_ttl_touches_keys_without_expiry(fake_redis: fakeredis.FakeRedis) -> None:
    fake_redis.set("forever", "1")

    assert _cap_ttl(["forever"], STALE_AFTER_WRITE_SECONDS) == 1
    assert 0 < fake_redis.ttl("forever") <= STALE_AFTER_WRITE_SECONDS


def _five_verst_platform(db_session):
    from app.models import Platform

    try:
        platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    except Exception:
        pytest.skip("Database not available")
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()
    return platform


def test_identity_slugs_collect_all_platform_addresses(db_session) -> None:
    """Страница у площадки одна, а адресов столько, сколько систем на ней было."""
    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

    five_verst = _five_verst_platform(db_session)
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = uuid4().hex[:8]
    fresh = Location(
        platform_id=five_verst.id,
        external_key=f"izmailovo-{suffix}",
        name="Измайлово",
    )
    legacy = Location(
        platform_id=parkrun.id,
        external_key=f"izmailovsky-{suffix}",
        name="Izmailovsky",
    )
    db_session.add_all([fresh, legacy])
    db_session.flush()

    catalog = LocationCatalog(canonical_name=f"Измайлово {suffix}", active_platform="five_verst")
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=fresh.external_key,
                location_id=fresh.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=legacy.external_key,
                location_id=legacy.id,
            ),
        ]
    )
    db_session.flush()

    slugs = identity_slugs_for_locations(db_session, [fresh.id])
    assert fresh.external_key in slugs
    assert legacy.external_key in slugs


def test_mark_shortens_ttl_after_commit(db_session, fake_redis: fakeredis.FakeRedis) -> None:
    from app.models import Location

    five_verst = _five_verst_platform(db_session)
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=five_verst.id,
        external_key=f"kuzminki-{suffix}",
        name="Кузьминки",
    )
    db_session.add(location)
    db_session.flush()

    page_keys = [
        location_page_cache_key(location.external_key),
        location_events_cache_key(location.external_key),
        location_leaders_cache_key(location.external_key),
    ]
    showcase_keys = [LOCATIONS_INDEX_CACHE_KEY, LAST_RESULTS_CACHE_KEY]
    for key in page_keys + showcase_keys:
        fake_redis.setex(key, 3 * 60 * 60, "{}")

    mark_location_results_changed(db_session, [location.id], reason="тест")

    # До коммита данных для читателя ещё нет — снимок не трогаем.
    for key in page_keys + showcase_keys:
        assert fake_redis.ttl(key) > STALE_AFTER_WRITE_SECONDS_SHOWCASE, key

    db_session.commit()

    for key in page_keys:
        assert 0 < fake_redis.ttl(key) <= STALE_AFTER_WRITE_SECONDS, key
    # Каталог и «последняя суббота» считаются по всем системам — им отмерено
    # больше, чтобы массовый синк не пересчитывал их каждую минуту.
    for key in showcase_keys:
        assert STALE_AFTER_WRITE_SECONDS < fake_redis.ttl(key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE, key
