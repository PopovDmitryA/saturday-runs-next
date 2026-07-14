from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.my_history_service import get_my_history


def _get_platform(db_session: Session, code: str, name: str) -> Platform:
    platform = db_session.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db_session.add(platform)
        db_session.flush()
    return platform


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
        external_event_key=f"my-history-event-{suffix}",
        event_date=event_date,
        event_number=event_number,
        title=f"Event {suffix}",
        finishers_count=10,
        runners_count=10,
    )
    db_session.add(event)
    db_session.flush()
    return event


def _make_run_result(db_session: Session, event: Event, participant: Participant, suffix: str) -> RunResult:
    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"my-history-result-{suffix}",
        position=1,
        finish_time_sec=20 * 60,
        finish_time_display="00:20:00",
        status="finished",
    )
    db_session.add(run)
    db_session.flush()
    return run


def test_new_city_milestone_fires_for_parkrun_location_with_known_city(db_session: Session) -> None:
    """A parkrun venue's own country field is an unreliable ingestion stub
    ("Великобритания" for everyone regardless of the real country), but its
    city/region are only ever set from the location catalog (former Russian
    venues) or a manual one-off correction (backend/scripts/set_location_geo.py)
    — never stubbed. So a parkrun run in a genuinely new, known city must still
    fire the "new_city" milestone, keeping it in sync with the "Локаций с
    пробежками" tile which already counts it."""
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    parkrun = _get_platform(db_session, "parkrun", "parkrun")

    user = User()
    db_session.add(user)
    db_session.flush()

    participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"mh-user-{suffix}",
        display_name="History Tester",
        profile_url=f"https://5verst.ru/userstats/mh-user-{suffix}/",
    )
    parkrun_participant = Participant(
        platform_id=parkrun.id,
        external_user_id=f"mh-parkrun-user-{suffix}",
        display_name="History Tester",
        profile_url=f"https://www.parkrun.org.uk/parkrunner/mh-parkrun-user-{suffix}/",
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

    # region left unset everywhere so the (pre-existing, unrelated) new_region
    # milestone never fires and suppresses the new_city assertion below.
    moscow_location = Location(
        platform_id=five_verst.id,
        external_key=f"druzhba-{suffix}",
        name="Druzhba",
        city="Москва",
        country="Россия",
    )
    kazan_location = Location(
        platform_id=five_verst.id,
        external_key=f"kazan-central-{suffix}",
        name="Kazan Central",
        city="Казань",
        country="Россия",
    )
    # No location_catalog link — uncatalogued foreign parkrun venue, exactly
    # like the reported case (Spring Rock parkrun, Chicago). country is the
    # generic parkrun stub; city carries a real, manually-verified value.
    chicago_parkrun_location = Location(
        platform_id=parkrun.id,
        external_key=f"spring-rock-{suffix}",
        name="Spring Rock",
        city="Чикаго",
        country="Великобритания",
    )
    db_session.add_all([moscow_location, kazan_location, chicago_parkrun_location])
    db_session.flush()

    event1 = _make_event(db_session, five_verst, moscow_location, f"1-{suffix}", date(2025, 1, 11), 900_500)
    event2 = _make_event(db_session, five_verst, kazan_location, f"2-{suffix}", date(2025, 7, 11), 900_501)
    event3 = _make_event(db_session, parkrun, chicago_parkrun_location, f"3-{suffix}", date(2025, 10, 18), 900_502)

    _make_run_result(db_session, event1, participant, f"1-{suffix}")
    _make_run_result(db_session, event2, participant, f"2-{suffix}")
    _make_run_result(db_session, event3, parkrun_participant, f"3-{suffix}")
    db_session.commit()

    history = get_my_history(db_session, user.id)
    new_city_milestones = [m for m in history["milestones"] if m["kind"] == "new_city"]
    new_city_by_name = {m["location_city"]: m for m in new_city_milestones}

    assert "Казань" in new_city_by_name
    assert new_city_by_name["Казань"]["number"] == 2
    assert "Чикаго" in new_city_by_name
    assert new_city_by_name["Чикаго"]["number"] == 3

    # The unreliable parkrun country stub must never surface as a milestone.
    assert all(m["kind"] != "new_country" for m in history["milestones"])
