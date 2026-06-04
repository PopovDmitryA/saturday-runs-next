from __future__ import annotations

from uuid import uuid4

import pytest

pytest_plugins = ["tests.test_dashboard_api"]


def test_catalog_map_excludes_unofficial_locations(authenticated_client, db_session) -> None:
    from app.models import Location, Platform

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    suffix = uuid4().hex[:8]
    unofficial = Location(
        platform_id=five_verst.id,
        external_key=f"map-garbage-{suffix}",
        name="Map Garbage Park",
        latitude=55.75,
        longitude=37.60,
        city="Москва",
        is_official_map=False,
    )
    db_session.add(unofficial)
    db_session.commit()

    response = authenticated_client.get("/api/locations/catalog/map")
    assert response.status_code == 200
    names = {point["name"] for point in response.json()["points"]}
    assert "Map Garbage Park" not in names


def test_catalog_map_includes_paused_strelka_when_imported(authenticated_client, db_session) -> None:
    from app.models import Location, Platform

    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    location = (
        db_session.query(Location)
        .filter(
            Location.platform_id == five_verst.id,
            Location.external_key == "strelka",
        )
        .one_or_none()
    )
    if location is None:
        location = Location(
            platform_id=five_verst.id,
            external_key="strelka",
            name="Стрелка",
            city="Ярославль",
            latitude=57.620778,
            longitude=39.903974,
            is_paused=True,
            is_official_map=True,
        )
        db_session.add(location)
    else:
        location.latitude = 57.620778
        location.longitude = 39.903974
        location.is_paused = True
        location.is_official_map = True
        location.name = "Стрелка"
    db_session.commit()

    response = authenticated_client.get("/api/locations/catalog/map")
    assert response.status_code == 200
    strelka = next(
        (point for point in response.json()["points"] if point["name"] == "Стрелка"),
        None,
    )
    assert strelka is not None
    assert strelka["is_paused"] is True
