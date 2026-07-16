from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import (
    Event,
    EventCrosslink,
    Location,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.services.user_unique_locations_detail import build_user_unique_location_details


def _get_platform(db_session: Session, code: str, name: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db_session.add(platform)
        db_session.flush()
    return platform


def test_secondary_crosslinked_run_excluded_from_unique_locations(db_session: Session) -> None:
    """A run that is a secondary crosslink duplicate ("не в зачёте", e.g. an unofficial

    S95 event republishing the same physical race the user already ran on the
    primary platform) must not create its own separate row in "Локации с
    пробежками" — it's the same race, not a new location visit.
    """
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    s95 = _get_platform(db_session, "s95", "C95")

    primary_location = Location(
        platform_id=five_verst.id,
        external_key=f"gorky-{suffix}",
        name="Парк Горького",
        city="Москва",
        country="Россия",
    )
    secondary_location = Location(
        platform_id=s95.id,
        external_key=f"s95-and-friends-{suffix}",
        name="С95 и друзья",
        city=None,
        country="Россия",
    )
    db_session.add_all([primary_location, secondary_location])
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    fv_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"me-fv-{suffix}",
        display_name="Me",
        profile_url=f"https://5verst.ru/userstats/me-fv-{suffix}/",
    )
    s95_participant = Participant(
        platform_id=s95.id,
        external_user_id=f"me-s95-{suffix}",
        display_name="Me",
        profile_url=f"https://s95.ru/athletes/me-s95-{suffix}/",
    )
    db_session.add_all([fv_participant, s95_participant])
    db_session.flush()
    db_session.add_all(
        [
            PlatformLink(
                user_id=user.id,
                platform_id=five_verst.id,
                participant_id=fv_participant.id,
                external_user_id=fv_participant.external_user_id,
                external_url=fv_participant.profile_url,
            ),
            PlatformLink(
                user_id=user.id,
                platform_id=s95.id,
                participant_id=s95_participant.id,
                external_user_id=s95_participant.external_user_id,
                external_url=s95_participant.profile_url,
            ),
        ]
    )

    primary_event = Event(
        platform_id=five_verst.id,
        location_id=primary_location.id,
        external_event_key=f"primary-event-{suffix}",
        event_date=date(2023, 4, 15),
        event_number=37,
        title="Парк Горького #37",
        finishers_count=10,
        runners_count=10,
    )
    secondary_event = Event(
        platform_id=s95.id,
        location_id=secondary_location.id,
        external_event_key=f"secondary-event-{suffix}",
        event_date=date(2023, 4, 15),
        event_number=2,
        title="С95 и друзья #2",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add_all([primary_event, secondary_event])
    db_session.flush()
    db_session.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=secondary_event.id))

    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=fv_participant.id,
            external_result_key=f"primary-result-{suffix}",
            position=1,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=secondary_event.id,
            participant_id=s95_participant.id,
            external_result_key=f"secondary-result-{suffix}",
            position=1,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.commit()

    result = build_user_unique_location_details(db_session, user.id)
    location_names = {loc["name"] for loc in result["locations"]}
    assert "С95 и друзья" not in location_names
    assert "Парк Горького" in location_names
    assert result["total_locations"] == 1
    assert result["unique_run_locations"] == 1

    platform_summary = {row["platform_code"]: row["location_count"] for row in result["platform_summary"]}
    assert "s95" not in platform_summary
    assert platform_summary["five_verst"] == 1
