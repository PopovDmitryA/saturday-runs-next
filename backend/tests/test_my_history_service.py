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


def _make_run_result(
    db_session: Session,
    event: Event,
    participant: Participant,
    suffix: str,
    finish_time_sec: int = 20 * 60,
) -> RunResult:
    minutes, seconds = divmod(finish_time_sec, 60)
    run = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"my-history-result-{suffix}",
        position=1,
        finish_time_sec=finish_time_sec,
        finish_time_display=f"00:{minutes:02d}:{seconds:02d}",
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


def test_global_pr_fires_on_platform_debut_faster_than_global_best(db_session: Session) -> None:
    """A runner's FIRST run in a new system is normally not a personal record
    (it's the baseline for that platform). But if that debut run is faster than
    the runner's all-time best across every other platform, it IS a genuine
    global record and must appear in the timeline — matching the stored
    is_global_pr flag (recalculate_cross_platform_personal_records). Regression
    for the reported case: runs 5 верст, then debuts on S95 and beats the global
    record on the faster course — the milestone used to be silently dropped
    because the debut had no prior time on S95 to compare against."""
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    s95 = _get_platform(db_session, "s95", "S95")

    user = User()
    db_session.add(user)
    db_session.flush()

    fv_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"gpr-fv-{suffix}",
        display_name="Global PR Tester",
        profile_url=f"https://5verst.ru/userstats/gpr-fv-{suffix}/",
    )
    s95_participant = Participant(
        platform_id=s95.id,
        external_user_id=f"gpr-s95-{suffix}",
        display_name="Global PR Tester",
        profile_url=f"https://s95.ru/athletes/gpr-s95-{suffix}",
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

    fv_location = Location(
        platform_id=five_verst.id,
        external_key=f"druzhba-gpr-{suffix}",
        name="Дружба",
        city="Москва",
        country="Россия",
    )
    s95_location = Location(
        platform_id=s95.id,
        external_key=f"kuzminki-gpr-{suffix}",
        name="Кузьминки",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([fv_location, s95_location])
    db_session.flush()

    # 5 верст first (global baseline 23:30), then debut on S95 at 22:08 — faster
    # than the global best, so a global record set on the very first S95 run.
    fv_event = _make_event(db_session, five_verst, fv_location, f"fv-{suffix}", date(2022, 6, 11), 900_600)
    s95_event = _make_event(db_session, s95, s95_location, f"s95-{suffix}", date(2022, 6, 18), 900_601)
    _make_run_result(db_session, fv_event, fv_participant, f"fv-{suffix}", finish_time_sec=23 * 60 + 30)
    _make_run_result(db_session, s95_event, s95_participant, f"s95-{suffix}", finish_time_sec=22 * 60 + 8)
    db_session.commit()

    history = get_my_history(db_session, user.id)
    global_prs = [m for m in history["milestones"] if m["kind"] == "global_pr"]

    assert len(global_prs) == 1
    debut = global_prs[0]
    assert debut["platform_code"] == "s95"
    assert debut["finish_time_sec"] == 22 * 60 + 8
    assert debut["is_global_pr"] is True
    # No prior S95 time — delta is measured against the previous global best.
    assert debut["delta_sec"] == (23 * 60 + 30) - (22 * 60 + 8)
    # The debut is not double-counted as a plain platform PR.
    assert all(m["kind"] != "pr" for m in history["milestones"])


def test_global_pr_delta_measured_against_previous_global_not_platform(db_session: Session) -> None:
    """The delta on a "Глобальный рекорд" card is the improvement over the
    PREVIOUS GLOBAL record, not over the runner's best on that same platform.
    Reported case (Егор Свиридов): S95 19:56 → 5 верст 18:59 → S95 18:19 must
    show −40 сек (18:59 − 18:19), not −1:37 (against the older S95 19:56)."""
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    s95 = _get_platform(db_session, "s95", "S95")

    user = User()
    db_session.add(user)
    db_session.flush()

    fv_participant = Participant(
        platform_id=five_verst.id,
        external_user_id=f"gd-fv-{suffix}",
        display_name="Global Delta Tester",
        profile_url=f"https://5verst.ru/userstats/gd-fv-{suffix}/",
    )
    s95_participant = Participant(
        platform_id=s95.id,
        external_user_id=f"gd-s95-{suffix}",
        display_name="Global Delta Tester",
        profile_url=f"https://s95.ru/athletes/gd-s95-{suffix}",
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

    fv_location = Location(
        platform_id=five_verst.id,
        external_key=f"pobedy-gd-{suffix}",
        name="Парк Победы",
        city="Москва",
        country="Россия",
    )
    s95_kuzminki = Location(
        platform_id=s95.id,
        external_key=f"kuzminki-gd-{suffix}",
        name="Кузьминки",
        city="Москва",
        country="Россия",
    )
    s95_troitsk = Location(
        platform_id=s95.id,
        external_key=f"troitsk-gd-{suffix}",
        name="Троицк",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([fv_location, s95_kuzminki, s95_troitsk])
    db_session.flush()

    # S95 19:56 (global), then 5 верст 18:59 (global), then S95 18:19 (global).
    e1 = _make_event(db_session, s95, s95_kuzminki, f"gd1-{suffix}", date(2024, 4, 20), 900_700)
    e2 = _make_event(db_session, five_verst, fv_location, f"gd2-{suffix}", date(2024, 5, 18), 900_701)
    e3 = _make_event(db_session, s95, s95_troitsk, f"gd3-{suffix}", date(2024, 5, 25), 900_702)
    _make_run_result(db_session, e1, s95_participant, f"gd1-{suffix}", finish_time_sec=19 * 60 + 56)
    _make_run_result(db_session, e2, fv_participant, f"gd2-{suffix}", finish_time_sec=18 * 60 + 59)
    _make_run_result(db_session, e3, s95_participant, f"gd3-{suffix}", finish_time_sec=18 * 60 + 19)
    db_session.commit()

    history = get_my_history(db_session, user.id)
    troitsk = next(
        m
        for m in history["milestones"]
        if m["kind"] == "global_pr" and m["finish_time_sec"] == 18 * 60 + 19
    )
    # Previous global was 18:59 (5 верст), NOT the older S95 19:56.
    assert troitsk["delta_sec"] == (18 * 60 + 59) - (18 * 60 + 19)  # 40 сек
