from __future__ import annotations

from datetime import date
from uuid import uuid4

pytest_plugins = ["tests.test_dashboard_api"]


def test_unique_location_detail_separates_run_and_volunteer_platforms(
    authenticated_client,
    db_session,
) -> None:
    from app.models import (
        Event,
        Location,
        Participant,
        Platform,
        PlatformLink,
        RunResult,
        User,
        VolunteerResult,
    )

    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    suffix = uuid4().hex[:8]

    run_only_location = Location(
        platform_id=five_verst.id,
        external_key=f"run-only-{suffix}",
        name="Run Only Park",
        latitude=55.1,
        longitude=37.1,
    )
    vol_only_location = Location(
        platform_id=five_verst.id,
        external_key=f"vol-only-{suffix}",
        name="Vol Only Park",
        latitude=55.2,
        longitude=37.2,
    )
    db_session.add_all([run_only_location, vol_only_location])
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"detail-user-{suffix}",
        display_name="Detail Tester",
        profile_url=f"https://5verst.ru/userstats/detail-user-{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=participant.id,
            external_user_id=participant.external_user_id,
            external_url=participant.profile_url,
        )
    )

    for index, (location, kind) in enumerate(
        [(run_only_location, "run"), (vol_only_location, "vol")],
        start=1,
    ):
        event = Event(
            platform_id=five_verst.id,
            location_id=location.id,
            external_event_key=f"detail-event-{suffix}-{index}",
            event_date=date(2024, index, 6),
            event_number=910_000 + index,
            title=f"Detail Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        if kind == "run":
            db_session.add(
                RunResult(
                    event_id=event.id,
                    participant_id=participant.id,
                    external_result_key=f"detail-run-{suffix}-{index}",
                    position=1,
                    finish_time_sec=1200,
                    finish_time_display="00:20:00",
                    status="finished",
                )
            )
        else:
            db_session.add(
                VolunteerResult(
                    event_id=event.id,
                    participant_id=participant.id,
                    external_result_key=f"detail-vol-{suffix}-{index}",
                    role="Barcode",
                )
            )

    db_session.commit()

    response = authenticated_client.get("/api/locations/visited/detail")
    assert response.status_code == 200
    payload = response.json()
    run_only = next(item for item in payload["locations"] if item["name"] == "Run Only Park")
    vol_only = next(item for item in payload["locations"] if item["name"] == "Vol Only Park")

    assert run_only["platforms"][0]["run_dates"]
    assert run_only["platforms"][0]["volunteer_dates"] == []
    assert vol_only["platforms"][0]["volunteer_dates"]
    assert vol_only["platforms"][0]["run_dates"] == []
    assert vol_only["platforms"][0]["volunteer_roles"] == [{"role": "Barcode", "count": 1}]
