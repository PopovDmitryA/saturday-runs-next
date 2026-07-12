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


def test_closed_keeps_source_name() -> None:
    catalog = _catalog(canonical_name="Memorial", active_platform="five_verst", is_closed=True)
    assert should_use_catalog_display(catalog, "parkrun") is False
    assert resolve_location_display_name(catalog, platform_code="parkrun", source_name="Memorialny Park") == "Memorialny Park"


def test_runpark_keeps_source_name() -> None:
    catalog = _catalog(canonical_name="Покровское-Стрешнево", active_platform="runpark", is_closed=False)
    assert should_use_catalog_display(catalog, "parkrun") is False
    assert (
        resolve_location_display_name(catalog, platform_code="parkrun", source_name="Pokrovskoe-Streshnevo")
        == "Pokrovskoe-Streshnevo"
    )


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
