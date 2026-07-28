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


def test_unknown_participant_names_excluded_from_list(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    runpark = _get_platform(db_session, "runpark", "RunPark")
    s95 = _get_platform(db_session, "s95", "C95")

    location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-unknown-{suffix}",
        name="Unknown Filter Location",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_participant = _make_participant(db_session, five_verst, f"me3-{suffix}", "Me")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=me_participant.id,
            external_user_id=me_participant.external_user_id,
            external_url=me_participant.profile_url,
        )
    )

    real_rival = _make_participant(db_session, five_verst, f"real-{suffix}", "Реальный Соперник")
    unknown_s95 = _make_participant(db_session, s95, f"unk-s95-{suffix}", "НЕИЗВЕСТНЫЙ")
    unknown_runpark = _make_participant(db_session, runpark, f"unk-rp-{suffix}", "Runner Unknown")

    my_event = _make_event(db_session, five_verst, location, f"unk-my-{suffix}", date(2024, 6, 1), 900_201)
    db_session.add(
        RunResult(
            event_id=my_event.id,
            participant_id=me_participant.id,
            external_result_key=f"unk-me-{suffix}",
            position=1,
            finish_time_sec=18 * 60,
            finish_time_display="00:18:00",
            status="finished",
        )
    )
    for i, rival in enumerate((real_rival, unknown_s95, unknown_runpark), start=1):
        db_session.add(
            RunResult(
                event_id=my_event.id,
                participant_id=rival.id,
                external_result_key=f"unk-rival-{i}-{suffix}",
                position=i + 1,
                finish_time_sec=19 * 60,
                finish_time_display="00:19:00",
                status="finished",
            )
        )
    db_session.commit()

    items = list_co_runners(db_session, user.id)
    names = {item["display_name"] for item in items}
    assert "Реальный Соперник" in names
    assert "НЕИЗВЕСТНЫЙ" not in names
    assert "Runner Unknown" not in names


def test_runpark_crosslink_duplicate_merges_into_primary_rival_bucket(db_session: Session) -> None:
    """RunPark republishes the primary event's finishers as a secondary event.

    A rival without a unified site account shows up as two unrelated
    Participant rows (one per platform) — without dedup each shared race
    would be counted twice: once in the "5 вёрст" bucket, once in "RunPark".
    """
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    runpark = _get_platform(db_session, "runpark", "RunPark")

    location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-dup-{suffix}",
        name="Druzhba",
        city="Москва",
        country="Россия",
    )
    runpark_location = Location(
        platform_id=runpark.id,
        external_key=f"loc-dup-runpark-{suffix}",
        name="Druzhba RunPark",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([location, runpark_location])
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_participant = _make_participant(db_session, five_verst, f"me4-{suffix}", "Me")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=me_participant.id,
            external_user_id=me_participant.external_user_id,
            external_url=me_participant.profile_url,
        )
    )

    rival_fv = _make_participant(db_session, five_verst, f"rival-fv-{suffix}", "Сергей ГОРИНОВ")
    rival_rp = _make_participant(db_session, runpark, f"rival-rp-{suffix}", "Сергей Горинов")

    # Races that only exist on the primary platform (no RunPark crosslink).
    for i in range(2):
        event = _make_event(
            db_session, five_verst, location, f"dup-fv-only-{i}-{suffix}", date(2022, 4, 9 + i * 7), 900_300 + i
        )
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=me_participant.id,
                external_result_key=f"dup-me-fv-{i}-{suffix}",
                position=1,
                finish_time_sec=18 * 60,
                finish_time_display="00:18:00",
                status="finished",
            )
        )
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=rival_fv.id,
                external_result_key=f"dup-rival-fv-{i}-{suffix}",
                position=2,
                finish_time_sec=19 * 60,
                finish_time_display="00:19:00",
                status="finished",
            )
        )

    # A race that RunPark republishes as a crosslinked duplicate: same
    # position and time for the rival, but recorded under their separate
    # RunPark Participant row — must not be counted as a second meeting.
    primary_event = _make_event(db_session, five_verst, location, f"dup-primary-{suffix}", date(2022, 5, 14), 900_310)
    runpark_event = _make_event(
        db_session, runpark, runpark_location, f"dup-runpark-{suffix}", date(2022, 5, 14), 900_311
    )
    db_session.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=runpark_event.id))
    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=me_participant.id,
            external_result_key=f"dup-me-primary-{suffix}",
            position=1,
            finish_time_sec=18 * 60 + 30,
            finish_time_display="00:18:30",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=rival_fv.id,
            external_result_key=f"dup-rival-primary-{suffix}",
            position=2,
            finish_time_sec=19 * 60 + 7,
            finish_time_display="00:19:07",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=runpark_event.id,
            participant_id=rival_rp.id,
            external_result_key=f"dup-rival-runpark-{suffix}",
            position=2,
            finish_time_sec=19 * 60 + 7,
            finish_time_display="00:19:07",
            status="finished",
        )
    )
    db_session.commit()

    items = list_co_runners(db_session, user.id)
    matching = [item for item in items if item["display_name"] == "Сергей ГОРИНОВ"]
    assert len(matching) == 1
    item = matching[0]
    assert item["meetings"] == 3
    # RunPark-запись здесь — дубликат уже засчитанной 5-вёрстной встречи (то же
    # место и время), поэтому своей системы в бейджах не добавляет.
    assert item["platform_codes"] == ["five_verst"]
    assert item["my_wins"] == 3
    assert item["their_wins"] == 0
    # Регрессия: раньше расхождение profile_url между RunPark-дублем и основной
    # записью соперника обнуляло ссылку целиком, даже когда бейдж один
    # (five_verst). Теперь ссылки хранятся по платформам отдельно и ни одна
    # не теряется — включая ссылку в RunPark, хоть её бейдж и не показан.
    assert item["profile_urls"]["five_verst"] == rival_fv.profile_url
    # У RunPark ссылка всегда каноническая, из external_user_id (сохранённый
    # profile_url там ненадёжен) — см. co_runners_service.
    assert item["profile_urls"]["runpark"] == f"https://runpark.ru/Account/Karmas/{rival_rp.external_user_id}"
