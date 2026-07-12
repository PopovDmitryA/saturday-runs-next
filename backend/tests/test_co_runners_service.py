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
from app.services.co_runners_service import list_co_runner_meetings, list_co_runners


def _get_platform(db_session: Session, code: str, name: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db_session.add(platform)
        db_session.flush()
    return platform


def _make_participant(db_session: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"co-runner-{suffix}",
        display_name=name,
        profile_url=f"https://example.test/{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _make_event(
    db_session: Session,
    platform: Platform,
    location: Location,
    suffix: str,
    event_date: date,
    event_number: int,
) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"co-runner-event-{suffix}",
        event_date=event_date,
        event_number=event_number,
        title=f"Event {suffix}",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add(event)
    db_session.flush()
    return event


def test_tied_finish_time_broken_by_position(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")

    location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-{suffix}",
        name="Tie Location",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_participant = _make_participant(db_session, five_verst, f"me-{suffix}", "Me")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=me_participant.id,
            external_user_id=me_participant.external_user_id,
            external_url=me_participant.profile_url,
        )
    )
    rival_participant = _make_participant(db_session, five_verst, f"rival-{suffix}", "Rival")

    event = _make_event(db_session, five_verst, location, suffix, date(2024, 6, 1), 800_001)
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=me_participant.id,
            external_result_key=f"co-runner-me-{suffix}",
            position=3,
            finish_time_sec=19 * 60 + 38,
            finish_time_display="00:19:38",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=rival_participant.id,
            external_result_key=f"co-runner-rival-{suffix}",
            position=4,
            finish_time_sec=19 * 60 + 38,
            finish_time_display="00:19:38",
            status="finished",
        )
    )
    db_session.commit()

    items = list_co_runners(db_session, user.id)
    assert len(items) == 1
    item = items[0]
    assert item["meetings"] == 1
    assert item["my_wins"] == 1
    assert item["their_wins"] == 0
    assert item["my_wins"] + item["their_wins"] == item["meetings"]


def test_meeting_detail_prefers_primary_platform_over_runpark_crosslink(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    runpark = _get_platform(db_session, "runpark", "RunPark")

    location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-primary-{suffix}",
        name="Primary Location",
        city="Москва",
        country="Россия",
    )
    runpark_location = Location(
        platform_id=runpark.id,
        external_key=f"loc-runpark-{suffix}",
        name="RunPark Location",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([location, runpark_location])
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_participant = _make_participant(db_session, five_verst, f"me2-{suffix}", "Me")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=me_participant.id,
            external_user_id=me_participant.external_user_id,
            external_url=me_participant.profile_url,
        )
    )
    rival_participant = _make_participant(db_session, five_verst, f"rival2-{suffix}", "Rival")

    primary_event = _make_event(db_session, five_verst, location, f"primary-{suffix}", date(2024, 6, 1), 800_101)
    runpark_event = _make_event(db_session, runpark, runpark_location, f"runpark-{suffix}", date(2024, 6, 1), 800_102)
    db_session.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=runpark_event.id))

    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=me_participant.id,
            external_result_key=f"co-runner-me2-{suffix}",
            position=3,
            finish_time_sec=19 * 60 + 38,
            finish_time_display="00:19:38",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=rival_participant.id,
            external_result_key=f"co-runner-rival2-primary-{suffix}",
            position=4,
            finish_time_sec=19 * 60 + 39,
            finish_time_display="00:19:39",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=runpark_event.id,
            participant_id=rival_participant.id,
            external_result_key=f"co-runner-rival2-runpark-{suffix}",
            position=4,
            finish_time_sec=19 * 60 + 39,
            finish_time_display="00:19:39",
            status="finished",
        )
    )
    db_session.commit()

    key = f"p:{rival_participant.id}"
    meetings = list_co_runner_meetings(db_session, user.id, key)
    assert len(meetings) == 1
    assert meetings[0]["platform_code"] == "five_verst"
    assert meetings[0]["location_name"] == "Primary Location"
