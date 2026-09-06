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
from app.services.co_runners_service import (
    list_co_runner_meetings,
    list_co_runners,
    parse_platform_codes,
)


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
    # Пользователь привязал только 5 вёрст, RunPark — нет: встреч на RunPark у
    # него быть не может (там он как участник не бегал), а RunPark-строка — лишь
    # дубль уже засчитанной 5-вёрстной встречи. Поэтому RunPark не даёт ни своей
    # системы в бейджах, ни ссылки — см. _user_platform_codes.
    assert item["platform_codes"] == ["five_verst"]
    assert item["my_wins"] == 3
    assert item["their_wins"] == 0
    # Ссылки хранятся по платформам отдельно; у отфильтрованного RunPark её нет.
    assert item["profile_urls"]["five_verst"] == rival_fv.profile_url
    assert "runpark" not in item["profile_urls"]


def test_unlinked_runpark_self_copy_is_not_a_meeting(db_session: Session) -> None:
    """Свой же непривязанный RunPark-профиль не должен становиться «встречей».

    Локации публикуют один протокол сразу в 5 вёрст и RunPark. Если человек
    привязал 5 вёрст, но не привязал RunPark, его RunPark-строка — это дубль
    его собственного забега под отдельным Participant. Такой дубль попадает в
    user_event_ids (кросслинк-секондари) и без фильтра по привязанным системам
    засчитывался бы как встреча с самим собой (баг Андрея Кошкина)."""
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    runpark = _get_platform(db_session, "runpark", "RunPark")

    location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-self-{suffix}",
        name="Druzhba",
        city="Москва",
        country="Россия",
    )
    runpark_location = Location(
        platform_id=runpark.id,
        external_key=f"loc-self-runpark-{suffix}",
        name="Druzhba RunPark",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([location, runpark_location])
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    # Привязан только 5 вёрст; RunPark-профиль того же человека НЕ привязан.
    me_fv = _make_participant(db_session, five_verst, f"me-fv-{suffix}", "Андрей КОШКИН")
    me_rp = _make_participant(db_session, runpark, f"me-rp-{suffix}", "Андрей Кошкин")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=me_fv.id,
            external_user_id=me_fv.external_user_id,
            external_url=me_fv.profile_url,
        )
    )

    primary_event = _make_event(db_session, five_verst, location, f"self-primary-{suffix}", date(2025, 5, 17), 900_400)
    runpark_event = _make_event(
        db_session, runpark, runpark_location, f"self-runpark-{suffix}", date(2025, 5, 17), 900_401
    )
    db_session.add(EventCrosslink(primary_event_id=primary_event.id, secondary_event_id=runpark_event.id))
    db_session.add(
        RunResult(
            event_id=primary_event.id,
            participant_id=me_fv.id,
            external_result_key=f"self-me-fv-{suffix}",
            position=8,
            finish_time_sec=19 * 60 + 57,
            finish_time_display="00:19:57",
            status="finished",
        )
    )
    # Тот же человек в RunPark-дубле события — то же место и время.
    db_session.add(
        RunResult(
            event_id=runpark_event.id,
            participant_id=me_rp.id,
            external_result_key=f"self-me-rp-{suffix}",
            position=8,
            finish_time_sec=19 * 60 + 57,
            finish_time_display="00:19:57",
            status="finished",
        )
    )
    db_session.commit()

    items = list_co_runners(db_session, user.id)
    # Единственный «другой» участник — собственный RunPark-профиль; встреч нет.
    assert [item["display_name"] for item in items] == []


def test_platform_filter_recounts_meetings_and_score(db_session: Session) -> None:
    """Фильтр «Система» пересчитывает встречи и счёт внутри выбранных систем.

    Клиентский отбор строк оставил бы «5 вёрст» в бейджах со счётом по всем
    системам сразу — 2:1, хотя на «5 вёрст» соперник не выигрывал ни разу.
    """
    suffix = str(uuid4().int % 1_000_000)
    five_verst = _get_platform(db_session, "five_verst", "5 верст")
    s95 = _get_platform(db_session, "s95", "S95")

    fv_location = Location(
        platform_id=five_verst.id,
        external_key=f"loc-pf-fv-{suffix}",
        name="Filter FV",
        city="Москва",
        country="Россия",
    )
    s95_location = Location(
        platform_id=s95.id,
        external_key=f"loc-pf-s95-{suffix}",
        name="Filter S95",
        city="Москва",
        country="Россия",
    )
    db_session.add_all([fv_location, s95_location])
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_fv = _make_participant(db_session, five_verst, f"pf-me-fv-{suffix}", "Me FV")
    me_s95 = _make_participant(db_session, s95, f"pf-me-s95-{suffix}", "Me S95")
    for platform, participant in ((five_verst, me_fv), (s95, me_s95)):
        db_session.add(
            PlatformLink(
                user_id=user.id,
                platform_id=platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url=participant.profile_url,
            )
        )

    # Соперник — зарегистрированный на сайте участник с открытым профилем: его
    # платформенные записи склеиваются в одну строку, поэтому у него встречи
    # сразу на двух системах (ради этого фильтр и нужен).
    rival_user = User(display_name="Пётр ФИЛЬТРОВ")
    db_session.add(rival_user)
    db_session.flush()
    rival_fv = _make_participant(db_session, five_verst, f"pf-rival-fv-{suffix}", "Пётр ФИЛЬТРОВ")
    rival_s95 = _make_participant(db_session, s95, f"pf-rival-s95-{suffix}", "Пётр ФИЛЬТРОВ")
    for platform, participant in ((five_verst, rival_fv), (s95, rival_s95)):
        db_session.add(
            PlatformLink(
                user_id=rival_user.id,
                platform_id=platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url=participant.profile_url,
            )
        )

    # Две встречи на «5 вёрст» — обе выиграны пользователем.
    for i in range(2):
        event = _make_event(
            db_session, five_verst, fv_location, f"pf-fv-{i}-{suffix}", date(2023, 3, 4 + i * 7), 901_100 + i
        )
        db_session.add(
            RunResult(
                event_id=event.id,
                participant_id=me_fv.id,
                external_result_key=f"pf-me-fv-{i}-{suffix}",
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
                external_result_key=f"pf-rival-fv-{i}-{suffix}",
                position=2,
                finish_time_sec=19 * 60,
                finish_time_display="00:19:00",
                status="finished",
            )
        )

    # Одна встреча на S95 — её выиграл соперник.
    s95_event = _make_event(db_session, s95, s95_location, f"pf-s95-{suffix}", date(2023, 6, 10), 901_200)
    db_session.add(
        RunResult(
            event_id=s95_event.id,
            participant_id=me_s95.id,
            external_result_key=f"pf-me-s95-{suffix}",
            position=2,
            finish_time_sec=20 * 60,
            finish_time_display="00:20:00",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=s95_event.id,
            participant_id=rival_s95.id,
            external_result_key=f"pf-rival-s95-{suffix}",
            position=1,
            finish_time_sec=17 * 60,
            finish_time_display="00:17:00",
            status="finished",
        )
    )
    db_session.commit()

    def _rival(items: list[dict[str, object]]) -> dict[str, object]:
        matching = [item for item in items if item["display_name"] == "Пётр ФИЛЬТРОВ"]
        assert len(matching) == 1
        return matching[0]

    unfiltered = _rival(list_co_runners(db_session, user.id))
    assert unfiltered["meetings"] == 3
    assert (unfiltered["my_wins"], unfiltered["their_wins"]) == (2, 1)
    assert sorted(unfiltered["platform_codes"]) == ["five_verst", "s95"]
    assert unfiltered["first_meeting_date"] == date(2023, 3, 4)
    assert unfiltered["last_meeting_date"] == date(2023, 6, 10)

    only_fv = _rival(list_co_runners(db_session, user.id, platform_codes={"five_verst"}))
    assert only_fv["meetings"] == 2
    assert (only_fv["my_wins"], only_fv["their_wins"]) == (2, 0)
    assert only_fv["platform_codes"] == ["five_verst"]
    assert only_fv["last_meeting_date"] == date(2023, 3, 11)

    only_s95 = _rival(list_co_runners(db_session, user.id, platform_codes={"s95"}))
    assert only_s95["meetings"] == 1
    assert (only_s95["my_wins"], only_s95["their_wins"]) == (0, 1)
    assert only_s95["platform_codes"] == ["s95"]

    both = _rival(list_co_runners(db_session, user.id, platform_codes={"five_verst", "s95"}))
    assert both["meetings"] == 3

    # Детали встреч обязаны сходиться со свёрнутой строкой — иначе в раскрытой
    # строке было бы больше встреч, чем в колонке «Встреч».
    fv_key = only_fv["participant_key"]
    meetings = list_co_runner_meetings(db_session, user.id, str(fv_key), platform_codes={"five_verst"})
    assert len(meetings) == 2
    assert {meeting["platform_code"] for meeting in meetings} == {"five_verst"}

    s95_key = only_s95["participant_key"]
    s95_meetings = list_co_runner_meetings(db_session, user.id, str(s95_key), platform_codes={"s95"})
    assert [meeting["platform_code"] for meeting in s95_meetings] == ["s95"]


def test_platform_filter_drops_people_met_only_elsewhere(db_session: Session) -> None:
    """Соперник, встреченный только на S95, не попадает в отбор по «5 вёрст»."""
    suffix = str(uuid4().int % 1_000_000)
    s95 = _get_platform(db_session, "s95", "S95")

    s95_location = Location(
        platform_id=s95.id,
        external_key=f"loc-only-s95-{suffix}",
        name="Only S95",
        city="Москва",
        country="Россия",
    )
    db_session.add(s95_location)
    db_session.flush()

    user = User()
    db_session.add(user)
    db_session.flush()

    me_s95 = _make_participant(db_session, s95, f"only-me-s95-{suffix}", "Me Only S95")
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=s95.id,
            participant_id=me_s95.id,
            external_user_id=me_s95.external_user_id,
            external_url=me_s95.profile_url,
        )
    )
    rival = _make_participant(db_session, s95, f"only-rival-s95-{suffix}", "Анна ТОЛЬКОС95")

    event = _make_event(db_session, s95, s95_location, f"only-s95-{suffix}", date(2023, 7, 15), 901_300)
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=me_s95.id,
            external_result_key=f"only-me-{suffix}",
            position=1,
            finish_time_sec=18 * 60,
            finish_time_display="00:18:00",
            status="finished",
        )
    )
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=rival.id,
            external_result_key=f"only-rival-{suffix}",
            position=2,
            finish_time_sec=19 * 60,
            finish_time_display="00:19:00",
            status="finished",
        )
    )
    db_session.commit()

    assert any(
        item["display_name"] == "Анна ТОЛЬКОС95" for item in list_co_runners(db_session, user.id)
    )
    assert not any(
        item["display_name"] == "Анна ТОЛЬКОС95"
        for item in list_co_runners(db_session, user.id, platform_codes={"five_verst"})
    )


def test_parse_platform_codes_keeps_known_codes_only() -> None:
    assert parse_platform_codes(None) is None
    assert parse_platform_codes("") is None
    assert parse_platform_codes("nonsense") is None
    assert parse_platform_codes("five_verst") == {"five_verst"}
    assert parse_platform_codes(" Five_Verst , s95 , nonsense ") == {"five_verst", "s95"}
