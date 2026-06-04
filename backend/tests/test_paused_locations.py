from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

pytest_plugins = ["tests.test_dashboard_api"]


def test_paused_location_uses_catalog_coordinates_in_detail(
    authenticated_client,
    db_session,
) -> None:
    from app.models import (
        Event,
        Location,
        LocationCatalog,
        LocationCatalogLink,
        Participant,
        Platform,
        PlatformLink,
        RunResult,
        User,
    )

    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()

    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = uuid4().hex[:8]
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"paused-parkrun-{suffix}",
        name="Paused Parkrun Only",
        city="Москва",
    )
    five_verst_location = Location(
        platform_id=five_verst.id,
        external_key=f"paused-fiveverst-{suffix}",
        name="Paused Five Verst",
        city="Москва",
        latitude=55.75,
        longitude=37.60,
    )
    db_session.add_all([parkrun_location, five_verst_location])
    db_session.flush()

    catalog = LocationCatalog(
        canonical_name="Paused Test Park",
        legacy_parkrun_slug=f"paused-parkrun-{suffix}",
        active_platform=None,
        is_closed=True,
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

    participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"paused-user-{suffix}",
        display_name="Paused Tester",
        profile_url=f"https://www.parkrun.ru/parkrunner/paused-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=parkrun.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    event = Event(
        platform_id=parkrun.id,
        location_id=parkrun_location.id,
        external_event_key=f"paused-event-{suffix}",
        event_date=date(2020, 6, 6),
        event_number=920_000,
        title="Paused Event",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add(event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"paused-result-{suffix}",
            position=1,
            finish_time_sec=1200,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.commit()

    detail = authenticated_client.get("/api/locations/visited/detail")
    assert detail.status_code == 200
    location = next(
        item
        for item in detail.json()["locations"]
        if item["catalog_identity_key"] == f"catalog:{catalog.id}"
    )
    assert location["has_coordinates"] is True
    assert location["is_paused"] is True
    assert location["latitude"] == pytest.approx(55.75)
    assert location["longitude"] == pytest.approx(37.60)

    visited_map = authenticated_client.get("/api/locations/visited/map")
    assert visited_map.status_code == 200
    point = next(
        item
        for item in visited_map.json()["points"]
        if item["catalog_identity_key"] == f"catalog:{catalog.id}"
    )
    assert point["is_paused"] is True
    assert point["latitude"] == pytest.approx(55.75)
