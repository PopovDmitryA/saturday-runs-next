"""Свежесть витрин локации после записи новых результатов."""

from __future__ import annotations

from datetime import date
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


def test_mark_shortens_protocol_page_ttl(db_session, fake_redis: fakeredis.FakeRedis) -> None:
    """Страница протокола тоже обязана протухнуть после перезаписи.

    У неё свой ключ кэша на три часа, и без подрезки исправленный протокол
    доезжал бы до читателя только к следующему протуханию. Проверка родилась
    13.09.2026: Плотинка за 29.08 разлипла в базе, а страница ещё показывала
    старое — надо было убедиться, что дело в 60-секундном окне, а не в дыре.
    """
    from datetime import date

    from app.models import Location
    from app.services.location_protocol_service import location_protocol_cache_key

    five_verst = _five_verst_platform(db_session)
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=five_verst.id,
        external_key=f"plotinka-{suffix}",
        name="Плотинка",
    )
    db_session.add(location)
    db_session.flush()

    event_date = date(2026, 8, 29)
    protocol_key = location_protocol_cache_key(location.external_key, "five_verst", event_date)
    other_date_key = location_protocol_cache_key(location.external_key, "five_verst", date(2026, 9, 5))
    for key in (protocol_key, other_date_key):
        fake_redis.setex(key, 3 * 60 * 60, "{}")

    mark_location_results_changed(
        db_session,
        [location.id],
        reason="тест",
        protocols=[("five_verst", event_date)],
    )
    db_session.commit()

    assert 0 < fake_redis.ttl(protocol_key) <= STALE_AFTER_WRITE_SECONDS
    # Соседняя суббота не менялась — её снимок трогать незачем.
    assert fake_redis.ttl(other_date_key) > STALE_AFTER_WRITE_SECONDS


def test_organizer_cabinet_caches_expire_on_new_protocol(
    db_session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Кабинет обязан протухнуть вместе с локацией, когда приехал протокол.

    До 14.09.2026 его кэши не сбрасывал никто: страница локации освежалась за
    минуту, а «Посещаемость» и «Протоколы» держали прошлую субботу ещё три
    часа. Организаторы на это и жаловались — протокол выложили, сайт подтянул,
    а в разделах старое.
    """
    from types import SimpleNamespace

    from app.models import Location
    from app.services.location_freshness import (
        organizer_index_key,
        write_organizer_cache,
    )

    five_verst = _five_verst_platform(db_session)
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=five_verst.id,
        external_key=f"izmailovo-{suffix}",
        name="Измайлово",
    )
    db_session.add(location)
    db_session.flush()

    identity = SimpleNamespace(locations=[(location, "five_verst")])
    attendance_key = f"organizer:attendance:v2:location:{location.id}"
    protocols_key = f"organizer:protocols:v3:location:{location.id}"
    for key in (attendance_key, protocols_key):
        write_organizer_cache(identity, key, {"stub": True}, 3 * 60 * 60)

    # Снимки записаны и попали в индекс площадки.
    assert fake_redis.ttl(attendance_key) > STALE_AFTER_WRITE_SECONDS
    assert fake_redis.smembers(organizer_index_key(location.id)) == {
        attendance_key,
        protocols_key,
    }

    mark_location_results_changed(db_session, [location.id], reason="тест")
    # До коммита читателю нового ещё нет — снимок не трогаем.
    assert fake_redis.ttl(attendance_key) > STALE_AFTER_WRITE_SECONDS

    db_session.commit()

    for key in (attendance_key, protocols_key):
        assert 0 < fake_redis.ttl(key) <= STALE_AFTER_WRITE_SECONDS, key


def test_organizer_index_does_not_touch_other_locations(
    db_session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Соседняя площадка от чужого протокола протухать не должна."""
    from types import SimpleNamespace

    from app.models import Location
    from app.services.location_freshness import write_organizer_cache

    five_verst = _five_verst_platform(db_session)
    suffix = uuid4().hex[:8]
    mine = Location(platform_id=five_verst.id, external_key=f"mine-{suffix}", name="Моя")
    theirs = Location(platform_id=five_verst.id, external_key=f"theirs-{suffix}", name="Чужая")
    db_session.add_all([mine, theirs])
    db_session.flush()

    my_key = f"organizer:health:v8:location:{mine.id}"
    their_key = f"organizer:health:v8:location:{theirs.id}"
    write_organizer_cache(SimpleNamespace(locations=[(mine, "five_verst")]), my_key, {}, 3 * 60 * 60)
    write_organizer_cache(
        SimpleNamespace(locations=[(theirs, "five_verst")]), their_key, {}, 3 * 60 * 60
    )

    mark_location_results_changed(db_session, [mine.id], reason="тест")
    db_session.commit()

    assert 0 < fake_redis.ttl(my_key) <= STALE_AFTER_WRITE_SECONDS
    assert fake_redis.ttl(their_key) > STALE_AFTER_WRITE_SECONDS


def test_mark_shortens_protocol_and_records_caches(
    db_session, fake_redis: fakeredis.FakeRedis
) -> None:
    """Пришёл субботний протокол — /protocol и рейтинг рекордов не ждут три часа.

    DEAD-01: функции сброса единого протокола, протокола локации и рейтинга
    рекордов существовали, но их никто не звал.
    """
    from app.models import Location
    from app.services.location_protocol_service import location_protocol_cache_key
    from app.services.location_records_rating_service import RATING_CACHE_KEY
    from app.services.unified_protocol_service import (
        unified_protocol_cache_key,
        unified_protocol_weeks_cache_key,
    )

    five_verst = _five_verst_platform(db_session)
    suffix = uuid4().hex[:8]
    location = Location(
        platform_id=five_verst.id,
        external_key=f"sokolniki-{suffix}",
        name="Сокольники",
    )
    db_session.add(location)
    db_session.flush()

    saturday = date(2026, 3, 7)
    protocol_key = location_protocol_cache_key(location.external_key, "five_verst", saturday)
    week_key = unified_protocol_cache_key(saturday)
    weeks_key = unified_protocol_weeks_cache_key()
    for key in (protocol_key, week_key, weeks_key, RATING_CACHE_KEY):
        fake_redis.setex(key, 6 * 60 * 60, "{}")

    mark_location_results_changed(
        db_session,
        [location.id],
        reason="тест: протокол",
        protocols=[("five_verst", saturday)],
    )
    db_session.commit()

    assert 0 < fake_redis.ttl(protocol_key) <= STALE_AFTER_WRITE_SECONDS
    for key in (week_key, weeks_key, RATING_CACHE_KEY):
        assert 0 < fake_redis.ttl(key) <= STALE_AFTER_WRITE_SECONDS_SHOWCASE, key
