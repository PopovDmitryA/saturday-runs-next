"""Рейтинг регионов по числу локаций.

Смысл фичи: одна таблица «где сколько субботних площадок», сквозная по
действующим системам. Тесты закрепляют границы счёта, из-за которых число в
таблице расходится с наивным `count(*)` по locations: площадка в двух системах
считается один раз, «не действует» из счёта уходит, а зарубежные площадки
собираются по странам, а не по регионам.

Рейтинг считается по всей базе сразу, поэтому в тестах регионы и страны —
с уникальным суффиксом: соседство с настоящими данными dev-БД ничего не меняет.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Location, LocationCatalog, LocationCatalogLink, Platform
from app.services.location_regions_rating_service import build_regions_rating


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(
    db: Session,
    platform: Platform,
    suffix: str,
    *,
    region: str | None = None,
    country: str = "Россия",
    city: str | None = "Тестоград",
    is_paused: bool = False,
    is_official_map: bool = True,
) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"regions-{suffix}",
        name=f"Площадка {suffix}",
        city=city,
        region=region,
        country=country,
        is_paused=is_paused,
        is_official_map=is_official_map,
    )
    db.add(location)
    db.flush()
    return location


def _link_to_catalog(db: Session, name: str, *pairs: tuple[Platform, Location]) -> None:
    """Свести площадки разных систем в один каталожный узел."""
    catalog = LocationCatalog(canonical_name=name, active_platform="five_verst")
    db.add(catalog)
    db.flush()
    for platform, location in pairs:
        db.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=platform.id,
                external_key=location.external_key,
                location_id=location.id,
            )
        )
    db.flush()


def _row(payload: dict[str, Any], name: str, key: str = "regions") -> dict[str, Any] | None:
    rows = payload[key]
    assert isinstance(rows, list)
    return next((row for row in rows if row["name"] == name), None)


def test_region_counts_locations_of_all_live_systems(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    region = f"Тестовская-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    s95 = _platform(db_session, "s95", "С95")
    runpark = _platform(db_session, "runpark", "RunPark")
    _location(db_session, five_verst, f"fv-a-{suffix}", region=region)
    _location(db_session, five_verst, f"fv-b-{suffix}", region=region, city="Второград")
    _location(db_session, s95, f"s95-{suffix}", region=region)
    _location(db_session, runpark, f"rp-{suffix}", region=region)
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    row = _row(payload, region)
    assert row is not None
    assert row["locations"] == 4
    assert row["by_platform"] == {"five_verst": 2, "s95": 1, "runpark": 1}
    # Два города: «Тестоград» у трёх площадок и «Второград» у одной.
    assert row["cities"] == 2


def test_one_park_in_two_systems_counts_once(db_session: Session) -> None:
    """Сумма колонок систем больше общего числа — это не ошибка, а связка."""
    suffix = uuid4().hex[:8]
    region = f"Связкинская-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    runpark = _platform(db_session, "runpark", "RunPark")
    fv_location = _location(db_session, five_verst, f"pair-fv-{suffix}", region=region)
    rp_location = _location(db_session, runpark, f"pair-rp-{suffix}", region=region)
    _link_to_catalog(db_session, f"Общий парк {suffix}", (five_verst, fv_location), (runpark, rp_location))
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    row = _row(payload, region)
    assert row is not None
    assert row["locations"] == 1
    assert row["by_platform"]["five_verst"] == 1
    assert row["by_platform"]["runpark"] == 1


def test_paused_locations_leave_the_count(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    region = f"Паузинская-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    _location(db_session, five_verst, f"live-{suffix}", region=region)
    _location(db_session, five_verst, f"dead-{suffix}", region=region, is_paused=True)
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    row = _row(payload, region)
    assert row is not None
    assert row["locations"] == 1
    assert row["paused"] == 1


def test_foreign_locations_are_counted_by_country(db_session: Session) -> None:
    """Как на карте: Россия — по регионам, зарубежье — по странам."""
    suffix = uuid4().hex[:8]
    country = f"Тестляндия-{suffix}"
    region = f"Заграничная-{suffix}"
    s95 = _platform(db_session, "s95", "С95")
    _location(db_session, s95, f"foreign-a-{suffix}", region=region, country=country)
    _location(db_session, s95, f"foreign-b-{suffix}", region=region, country=country)
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    assert _row(payload, region) is None
    country_row = _row(payload, country, key="countries")
    assert country_row is not None
    assert country_row["locations"] == 2
    assert country_row["scope"] == "country"


def test_region_name_is_shown_in_full(db_session: Session) -> None:
    """В БД регион сокращён («Московская») — в витрине он с типом."""
    suffix = uuid4().hex[:8]
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    _location(db_session, five_verst, f"moscow-obl-{suffix}", region="Московская")
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    assert _row(payload, "Московская область") is not None
    assert _row(payload, "Московская") is None


def test_platform_filter_leaves_one_system(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    region = f"Фильтрская-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    s95 = _platform(db_session, "s95", "С95")
    _location(db_session, five_verst, f"filter-fv-{suffix}", region=region)
    _location(db_session, s95, f"filter-s95-{suffix}", region=region)
    db_session.commit()

    payload = build_regions_rating(db_session, platform="s95", use_cache=False)
    row = _row(payload, region)
    assert row is not None
    assert row["locations"] == 1
    assert row["by_platform"]["five_verst"] == 0
    assert row["by_platform"]["s95"] == 1


def test_unknown_platform_falls_back_to_all(db_session: Session) -> None:
    """parkrun и мусор из ссылки открывают общий зачёт, а не ошибку."""
    payload = build_regions_rating(db_session, platform="parkrun", use_cache=False)
    assert payload["platform"] == "all"
    assert build_regions_rating(db_session, platform="кто-то-набрал-руками", use_cache=False)["platform"] == "all"


def test_places_follow_the_number_of_locations(db_session: Session) -> None:
    suffix = uuid4().hex[:8]
    big = f"Большая-{suffix}"
    small = f"Малая-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    for index in range(3):
        _location(db_session, five_verst, f"big-{index}-{suffix}", region=big)
    _location(db_session, five_verst, f"small-{suffix}", region=small)
    db_session.commit()

    payload = build_regions_rating(db_session, use_cache=False)
    big_row = _row(payload, big)
    small_row = _row(payload, small)
    assert big_row is not None and small_row is not None
    assert big_row["place"] < small_row["place"]
    # Регионы с одинаковым числом площадок делят одно место.
    same_place = [row["place"] for row in payload["regions"] if row["locations"] == small_row["locations"]]
    assert len(set(same_place)) == 1


def test_locations_outside_the_official_map_are_ignored(db_session: Session) -> None:
    """Каталог и карта показывают только официальные площадки — рейтинг тоже."""
    suffix = uuid4().hex[:8]
    region = f"Неофициальная-{suffix}"
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    _location(db_session, five_verst, f"hidden-{suffix}", region=region, is_official_map=False)
    db_session.commit()

    assert _row(build_regions_rating(db_session, use_cache=False), region) is None
