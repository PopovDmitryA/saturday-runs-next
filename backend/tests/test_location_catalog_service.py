from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest_plugins = ["tests.test_dashboard_api"]

from app.services.location_catalog_service import (
    LocationCatalogIndex,
    resolve_location_display_name,
    should_use_catalog_display,
)
from app.services.user_location_stats import count_unique_locations_from_rows


def _catalog(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


def test_display_parkrun_via_five_verst_catalog() -> None:
    catalog = _catalog(canonical_name="Тимирязевский", active_platform="five_verst", is_closed=False)
    assert should_use_catalog_display(catalog, "parkrun") is True
    assert (
        resolve_location_display_name(catalog, platform_code="parkrun", source_name="Timiryazevsky")
        == "Тимирязевский"
    )


def test_active_platform_name_wins_over_legacy_canonical() -> None:
    # У части узлов canonical_name — транслитерация времён parkrun; актуальное
    # название площадки живёт в локации действующей системы, его и показываем.
    catalog = _catalog(
        canonical_name="Ufa Botanichesky Sad", active_platform="five_verst", is_closed=False
    )
    assert (
        resolve_location_display_name(
            catalog,
            platform_code="parkrun",
            source_name="Ufa Botanichesky Sad",
            active_platform_name="Парк Лесоводов",
        )
        == "Парк Лесоводов"
    )


def test_canonical_name_is_fallback_without_active_location() -> None:
    # Действующей локации у узла нет (осталась одна parkrun-история) —
    # тогда каталожное имя всё ещё лучше сырого латинского.
    catalog = _catalog(canonical_name="Тимирязевский", active_platform="five_verst", is_closed=False)
    assert (
        resolve_location_display_name(
            catalog,
            platform_code="parkrun",
            source_name="Timiryazevsky",
            active_platform_name=None,
        )
        == "Тимирязевский"
    )


def test_closed_keeps_source_name() -> None:
    catalog = _catalog(canonical_name="Memorial", active_platform="five_verst", is_closed=True)
    assert should_use_catalog_display(catalog, "parkrun") is False
    assert resolve_location_display_name(catalog, platform_code="parkrun", source_name="Memorialny Park") == "Memorialny Park"


def test_runpark_is_a_valid_name_source() -> None:
    """Площадка, которая сегодня бегает только в runpark, называется по нему.

    Раньше runpark отсеивался наравне с parkrun, и «Покровское-Стрешнево» с
    «Лесопарк Северный» оставались латиницей: действующей системы, чьё имя
    разрешено брать, у этих узлов просто не было.
    """
    catalog = _catalog(canonical_name="Покровское-Стрешнево", active_platform="runpark", is_closed=False)
    assert should_use_catalog_display(catalog, "parkrun") is True
    assert (
        resolve_location_display_name(
            catalog,
            platform_code="parkrun",
            source_name="Pokrovskoe-Streshnevo",
            active_platform_name="Покровское-Стрешнево",
        )
        == "Покровское-Стрешнево"
    )


def test_parkrun_never_overrides_name() -> None:
    """Узел, где действующей осталась parkrun-строка, имя не переопределяет —
    её латиница и есть то, от чего мы уходим."""
    catalog = _catalog(canonical_name="Тимирязевский", active_platform="parkrun", is_closed=False)
    assert should_use_catalog_display(catalog, "parkrun") is False


def test_catalog_lookup_matches_hyphenated_parkrun_slug(db_session) -> None:
    from uuid import uuid4

    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

    try:
        parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    except Exception:
        pytest.skip("Database not available")

    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = uuid4().hex[:8]
    hyphenated = Location(
        platform_id=parkrun.id,
        external_key=f"readovsky-park-{suffix}",
        name="Readovsky Park",
    )
    db_session.add(hyphenated)
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Реадовский парк",
        legacy_parkrun_slug=f"readovskypark{suffix}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id,
            platform_id=parkrun.id,
            external_key=f"readovskypark{suffix}",
        )
    )
    db_session.commit()

    index = LocationCatalogIndex(db_session)
    assert index.display_name(hyphenated, "parkrun") == "Реадовский парк"
    assert index.canonical_identity_key(hyphenated, "parkrun") == f"catalog:{catalog.id}"


def test_canonical_identity_key_uses_catalog_id(db_session) -> None:
    from uuid import uuid4

    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

    try:
        platform = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    except Exception:
        pytest.skip("Database not available")

    external_key = f"catalog-test-{uuid4().hex[:8]}"
    location = Location(
        platform_id=platform.id,
        external_key=external_key,
        name="Catalog Test Location",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Catalog Test Location",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add(
        LocationCatalogLink(
            catalog_id=catalog.id,
            platform_id=platform.id,
            external_key=external_key,
            location_id=location.id,
        )
    )
    db_session.commit()

    index = LocationCatalogIndex(db_session)
    assert index.canonical_identity_key(location, "five_verst") == f"catalog:{catalog.id}"

    other_location = Location(
        platform_id=platform.id,
        external_key=f"other-{uuid4().hex[:8]}",
        name="Other Location",
    )
    assert index.canonical_identity_key(other_location, "five_verst").startswith("location:")


def test_unique_location_counts_merge_catalog_and_coords(db_session) -> None:
    from uuid import uuid4

    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

    try:
        parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
        five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    except Exception:
        pytest.skip("Database not available")

    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = uuid4().hex[:8]
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"parkrun-count-{suffix}",
        name="Count Test Parkrun",
    )
    five_verst_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-count-{suffix}",
        name="Count Test Five Verst",
        latitude=55.1,
        longitude=37.1,
    )
    solo_location = Location(
        platform_id=five_verst.id,
        external_key=f"solo-count-{suffix}",
        name="Count Test Solo",
        latitude=55.2,
        longitude=37.2,
    )
    db_session.add_all([parkrun_location, five_verst_location, solo_location])
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Count Test Park",
        legacy_parkrun_slug=f"parkrun-count-{suffix}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=parkrun_location.external_key,
                location_id=parkrun_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=five_verst_location.external_key,
                location_id=five_verst_location.id,
            ),
        ]
    )
    db_session.commit()

    index = LocationCatalogIndex(db_session)
    counts = count_unique_locations_from_rows(
        index,
        [
            (parkrun_location, "parkrun"),
            (five_verst_location, "five_verst"),
            (solo_location, "five_verst"),
        ],
    )
    assert counts.unique_total == 2
    assert counts.unique_with_coordinates == 2
    assert counts.unique_without_coordinates == 0


def test_coordinates_come_from_active_platform_not_first_link(db_session) -> None:
    """Мытищи: точка сбора — старт действующих 5 вёрст, а не runpark-лавочки.

    runpark-связку добавляем первой: раньше побеждала любая связка, пришедшая из БД
    раньше остальных, и на карте оказывалась точка в ~3 км от реального старта.
    """
    from uuid import uuid4

    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform

    try:
        five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
        runpark = db_session.query(Platform).filter(Platform.code == "runpark").one_or_none()
    except Exception:
        pytest.skip("Database not available")

    if runpark is None:
        runpark = Platform(code="runpark", name="runpark")
        db_session.add(runpark)
        db_session.flush()

    suffix = uuid4().hex[:8]
    runpark_location = Location(
        platform_id=runpark.id,
        external_key=f"runpark-coords-{suffix}",
        name="Лавочки у зелёного моста",
        latitude=55.8984,
        longitude=37.713,
    )
    five_verst_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-coords-{suffix}",
        name="Мытищи Центральный парк",
        latitude=55.910941,
        longitude=37.742125,
    )
    db_session.add_all([runpark_location, five_verst_location])
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name=f"Coords Test Park {suffix}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=runpark.id,
                external_key=runpark_location.external_key,
                location_id=runpark_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=five_verst_location.external_key,
                location_id=five_verst_location.id,
            ),
        ]
    )
    db_session.commit()

    index = LocationCatalogIndex(db_session)
    expected = (five_verst_location.latitude, five_verst_location.longitude)
    # Через любую связку узла — координаты действующей платформы.
    assert index.coordinates_for(runpark_location, "runpark") == expected
    assert index.coordinates_for(five_verst_location, "five_verst") == expected
    assert index.coordinates_for_identity_key(f"catalog:{catalog.id}") == expected


def test_backfill_region_from_catalog_fills_gap_from_sibling_platform(db_session) -> None:
    from uuid import uuid4

    from app.models import Location, LocationCatalog, LocationCatalogLink, Platform
    from app.services.location_catalog_service import backfill_region_from_catalog

    try:
        parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
        five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    except Exception:
        pytest.skip("Database not available")

    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = uuid4().hex[:8]
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"parkrun-region-{suffix}",
        name="Region Test Parkrun",
        region=None,
    )
    five_verst_location = Location(
        platform_id=five_verst.id,
        external_key=f"fiveverst-region-{suffix}",
        name="Region Test Five Verst",
        region="Воронежская",
    )
    db_session.add_all([parkrun_location, five_verst_location])
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Region Test Park",
        legacy_parkrun_slug=f"parkrun-region-{suffix}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()
    db_session.add_all(
        [
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=parkrun.id,
                external_key=parkrun_location.external_key,
                location_id=parkrun_location.id,
            ),
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=five_verst.id,
                external_key=five_verst_location.external_key,
                location_id=five_verst_location.id,
            ),
        ]
    )
    db_session.commit()

    updated = backfill_region_from_catalog(db_session, parkrun_location)
    assert updated is True
    assert parkrun_location.region == "Воронежская"

    # No-op when region already set.
    assert backfill_region_from_catalog(db_session, five_verst_location) is False


def _catalog_with_links(db_session, entries: list[tuple[str, str, bool, bool]]):
    """entries: (platform_code, name, is_paused, has_events) → каталожный узел со связками."""
    from datetime import date
    from uuid import uuid4

    from app.models import EventSummary, Location, LocationCatalog, LocationCatalogLink, Platform

    catalog = LocationCatalog(
        canonical_name=f"Pause Test {uuid4().hex[:6]}",
        active_platform="five_verst",
        is_closed=False,
    )
    db_session.add(catalog)
    db_session.flush()

    created: dict[str, object] = {}
    for platform_code, name, is_paused, has_events in entries:
        platform = db_session.query(Platform).filter(Platform.code == platform_code).one_or_none()
        if platform is None:
            platform = Platform(code=platform_code, name=platform_code)
            db_session.add(platform)
            db_session.flush()
        slug = f"pause-{platform_code}-{uuid4().hex[:8]}"
        location = Location(
            platform_id=platform.id,
            external_key=slug,
            name=name,
            is_paused=is_paused,
        )
        db_session.add(location)
        db_session.flush()
        if has_events:
            db_session.add(
                EventSummary(
                    platform_id=platform.id,
                    location_id=location.id,
                    external_event_key=f"{slug}:1",
                    event_date=date.today(),
                    event_number=1,
                    summary_hash=uuid4().hex,
                )
            )
        db_session.add(
            LocationCatalogLink(
                catalog_id=catalog.id,
                platform_id=platform.id,
                external_key=slug,
                location_id=location.id,
            )
        )
        created[platform_code] = location
    db_session.commit()
    return catalog, created


def test_paused_runpark_link_does_not_pause_active_five_verst(db_session) -> None:
    """Мытищи: runpark-связка на паузе не гасит локацию, где 5 вёрст бегают еженедельно."""
    try:
        _catalog, locations = _catalog_with_links(
            db_session,
            [
                ("five_verst", "Мытищи Центральный парк", False, True),
                ("runpark", "Лавочки у зелёного моста", True, False),
            ],
        )
    except Exception:
        pytest.skip("Database not available")

    index = LocationCatalogIndex(db_session)
    assert index.is_paused(locations["five_verst"], "five_verst") is False
    assert index.is_paused(locations["runpark"], "runpark") is False


def test_paused_active_platform_pauses_whole_catalog(db_session) -> None:
    """Если бегающая платформа встала на паузу, узел на паузе целиком."""
    try:
        _catalog, locations = _catalog_with_links(
            db_session,
            [
                ("five_verst", "Шуваловский парк", True, True),
                ("parkrun", "Shuvalovsky Park", False, False),
            ],
        )
    except Exception:
        pytest.skip("Database not available")

    index = LocationCatalogIndex(db_session)
    assert index.is_paused(locations["five_verst"], "five_verst") is True
    assert index.is_paused(locations["parkrun"], "parkrun") is True


def test_pause_falls_back_to_any_link_without_events(db_session) -> None:
    """Событий нет ни на одной платформе — считаем по флагам всех связок."""
    try:
        _catalog, locations = _catalog_with_links(
            db_session,
            [
                ("five_verst", "Мемориальный парк", True, False),
                ("parkrun", "Memorialny Park", False, False),
            ],
        )
    except Exception:
        pytest.skip("Database not available")

    index = LocationCatalogIndex(db_session)
    assert index.is_paused(locations["five_verst"], "five_verst") is True
    assert index.is_paused(locations["parkrun"], "parkrun") is True
