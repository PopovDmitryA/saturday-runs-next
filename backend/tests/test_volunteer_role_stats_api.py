from __future__ import annotations

from datetime import date
from uuid import uuid4

pytest_plugins = ["tests.test_dashboard_api"]


def test_volunteer_role_stats_groups_by_platform_and_role(
    authenticated_client,
    db_session,
) -> None:
    from app.models import (
        Event,
        Location,
        Participant,
        Platform,
        PlatformLink,
        User,
        VolunteerResult,
    )

    me = authenticated_client.get("/api/auth/me")
    user = db_session.query(User).filter(User.telegram_id == me.json()["telegram_id"]).one()
    five_verst = db_session.query(Platform).filter(Platform.code == "five_verst").one()
    parkrun = db_session.query(Platform).filter(Platform.code == "parkrun").one_or_none()
    if parkrun is None:
        parkrun = Platform(code="parkrun", name="parkrun")
        db_session.add(parkrun)
        db_session.flush()

    suffix = str(uuid4().int % 1_000_000)
    five_location = Location(
        platform_id=five_verst.id,
        external_key=f"vol-role-five-{suffix}",
        name="Five Vol Role Park",
        city="Москва",
        country="Россия",
    )
    parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"vol-role-parkrun-{suffix}",
        name="Parkrun Vol Role Park",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([five_location, parkrun_location])
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"vol-role-user-{suffix}",
        display_name="Vol Role Tester",
        profile_url=f"https://5verst.ru/userstats/vol-role-user-{suffix}/",
    )
    parkrun_participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"vol-role-parkrun-{suffix}",
        display_name="Vol Role Tester PR",
        profile_url=f"https://www.parkrun.ru/profile/vol-role-{suffix}/",
    )
    db_session.add_all([participant, parkrun_participant])
    db_session.flush()
    db_session.add_all(
        [
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url=participant.profile_url,
            ),
            PlatformLink(
                user_id=user.id,
                platform_id=parkrun.id,
                participant_id=parkrun_participant.id,
                external_user_id=parkrun_participant.external_user_id,
                external_url=parkrun_participant.profile_url,
            ),
        ]
    )

    seed = [
        (five_verst, five_location, participant, date(2024, 3, 9), "Маршал"),
        (five_verst, five_location, participant, date(2024, 4, 6), "Маршал"),
        (five_verst, five_location, participant, date(2024, 5, 4), "Сканер"),
        (parkrun, parkrun_location, parkrun_participant, date(2025, 1, 11), "Run Director"),
    ]
    for index, (platform, location, vol_participant, event_date, role) in enumerate(seed, start=1):
        event = Event(
            platform_id=platform.id,
            location_id=location.id,
            external_event_key=f"vol-role-event-{suffix}-{index}",
            event_date=event_date,
            event_number=950_000 + index,
            title=f"Vol Role Event {index}",
            finishers_count=10,
            runners_count=10,
        )
        db_session.add(event)
        db_session.flush()
        db_session.add(
            VolunteerResult(
                event_id=event.id,
                participant_id=vol_participant.id,
                external_result_key=f"vol-role-result-{suffix}-{index}",
                role=role,
            )
        )

    db_session.commit()

    response = authenticated_client.get("/api/volunteering/role-stats")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 3
    assert items[0] == {"platform_code": "five_verst", "role": "Маршал", "count": 2}
    assert items[1] == {"platform_code": "five_verst", "role": "Сканер", "count": 1}
    assert items[2] == {"platform_code": "parkrun", "role": "Run Director", "count": 1}
