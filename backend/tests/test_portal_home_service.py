from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, PlatformLink, RunResult, User
from app.services.portal_home_service import (
    CHART_WEEKS,
    PORTAL_HOME_CACHE_KEY,
    _city_label,
    _course_minima_before,
    _EventRow,
    _read_portal_home_cache,
    _runner_handles,
    _week_attendance_records,
    _write_portal_home_cache,
    chart_window,
    clean_time_display,
    format_finish_time,
    invalidate_portal_home_cache,
)
from app.services.unified_protocol_service import saturday_of

WEEK_START = date(2026, 7, 20)
WEEK_END = date(2026, 7, 25)


def _event(
    location_id: UUID,
    event_date: date,
    finishers: int,
    event_number: int | None = None,
    is_test_event: bool = False,
) -> _EventRow:
    return _EventRow(
        uuid4(),
        location_id,
        "five_verst",
        event_date,
        finishers,
        event_number,
        is_test_event,
    )


def _attendance(events: list[_EventRow], city: str | None = None) -> list[dict]:
    return _week_attendance_records(
        events,
        WEEK_START,
        WEEK_END,
        lambda location_id: str(location_id),
        lambda location_id: f"loc-{location_id}",
        lambda location_id: f"slug-{location_id}",
        lambda location_id: city,
    )


def test_format_finish_time_minutes() -> None:
    assert format_finish_time(16 * 60 + 21) == "16:21"
    assert format_finish_time(59) == "0:59"


def test_format_finish_time_hours() -> None:
    assert format_finish_time(3600 + 5 * 60 + 7) == "1:05:07"


def test_clean_time_display_prefers_seconds() -> None:
    assert clean_time_display("00:16:21", 16 * 60 + 21) == "16:21"


def test_clean_time_display_strips_leading_hours() -> None:
    assert clean_time_display("00:16:21", None) == "16:21"
    assert clean_time_display("00:09:59", None) == "9:59"
    assert clean_time_display(None, None) == ""


def test_cache_round_trip() -> None:
    payload = {"hero": {"finishes_total": 1}, "generated_at": "2026-07-13T00:00:00"}
    assert _read_portal_home_cache() is None
    _write_portal_home_cache(payload)
    assert _read_portal_home_cache() == payload


def test_attendance_record_beats_previous_max() -> None:
    loc = uuid4()
    rows = _attendance(
        [
            _event(loc, date(2026, 5, 2), 120),
            _event(loc, date(2026, 6, 6), 150),
            _event(loc, date(2026, 7, 25), 180),
        ]
    )
    assert len(rows) == 1
    assert rows[0]["finishers"] == 180
    assert rows[0]["previous_record"] == 150
    assert rows[0]["previous_record_date"] == date(2026, 6, 6)
    assert rows[0]["is_debut"] is False


def test_attendance_ignores_result_below_previous_max() -> None:
    loc = uuid4()
    assert (
        _attendance(
            [
                _event(loc, date(2026, 6, 6), 150),
                _event(loc, date(2026, 7, 25), 149),
            ]
        )
        == []
    )


def test_debut_counts_as_record() -> None:
    loc = uuid4()
    rows = _attendance([_event(loc, date(2026, 7, 25), 427, 1)])
    assert len(rows) == 1
    assert rows[0]["is_debut"] is True
    assert rows[0]["finishers"] == 427
    assert rows[0]["previous_record"] == 0
    assert rows[0]["previous_record_date"] is None


def test_debut_without_event_number_still_counts() -> None:
    """У RunPark и части архивов номера старта нет — опираемся на пустую историю."""
    loc = uuid4()
    rows = _attendance([_event(loc, date(2026, 7, 25), 54, None)])
    assert [row["is_debut"] for row in rows] == [True]


def test_newly_connected_location_is_not_a_debut() -> None:
    """Площадка подключена к сайту сейчас, но бежит давно (старт №137) —
    это не открытие, и рекордом такой старт не считаем."""
    loc = uuid4()
    assert _attendance([_event(loc, date(2026, 7, 25), 90, 137)]) == []


def test_debut_ranked_by_finishers_against_improvements() -> None:
    debut_big, debut_small, improved = uuid4(), uuid4(), uuid4()
    rows = _attendance(
        [
            _event(improved, date(2026, 6, 6), 300),
            _event(improved, date(2026, 7, 25), 400),  # +100
            _event(debut_big, date(2026, 7, 25), 427, 1),
            _event(debut_small, date(2026, 7, 25), 54, 1),
        ]
    )
    assert [(row["finishers"], row["is_debut"]) for row in rows] == [
        (427, True),
        (400, False),
        (54, True),
    ]


def test_test_run_is_not_a_debut() -> None:
    """Пробный забег 5 вёрст («Мирный (тестовый)», №0) — ещё не открытие."""
    loc = uuid4()
    assert _attendance([_event(loc, date(2026, 7, 25), 17, 0, is_test_event=True)]) == []


def test_test_run_does_not_block_the_real_opening() -> None:
    loc = uuid4()
    rows = _attendance(
        [
            _event(loc, date(2026, 7, 18), 17, 0, is_test_event=True),
            _event(loc, date(2026, 7, 25), 120, 1),
        ]
    )
    assert [(row["finishers"], row["is_debut"]) for row in rows] == [(120, True)]


def test_prior_events_without_finishers_are_not_a_debut() -> None:
    """Пустой протокол в истории — дыра в данных, а не отсутствие площадки."""
    loc = uuid4()
    assert (
        _attendance(
            [
                _event(loc, date(2026, 6, 6), 0),
                _event(loc, date(2026, 7, 25), 80, 1),
            ]
        )
        == []
    )


def test_cache_invalidate(fake_redis) -> None:  # type: ignore[no-untyped-def]
    _write_portal_home_cache({"hero": {}})
    assert fake_redis.get(PORTAL_HOME_CACHE_KEY) is not None
    invalidate_portal_home_cache()
    assert fake_redis.get(PORTAL_HOME_CACHE_KEY) is None
    assert _read_portal_home_cache() is None


def test_attendance_record_carries_location_slug() -> None:
    """Слаг едет рядом с названием — из него на главной строится ссылка."""
    loc = uuid4()
    rows = _attendance([_event(loc, date(2026, 7, 25), 120, 1)])
    assert rows[0]["location_slug"] == f"slug-{loc}"


def test_attendance_record_carries_city() -> None:
    """Город едет рядом с названием — на главной он стоит перед датой."""
    loc = uuid4()
    rows = _attendance([_event(loc, date(2026, 7, 25), 120, 1)], city="Ижевск")
    assert rows[0]["location_city"] == "Ижевск"


def test_city_label_hides_city_already_in_name() -> None:
    """Город не дублируем: половина названий и так начинается с города."""
    assert _city_label("Тюмень", "Тюмень Парк Гагарина") is None
    assert _city_label("тюмень", "Тюмень Комарово") is None
    assert _city_label("Москва", "Кусково") == "Москва"
    assert _city_label(None, "Кусково") is None
    assert _city_label("", "Кусково") is None


def test_course_minima_before_ignores_test_events(db_session: Session) -> None:
    """Пробный старт не должен становиться «прежним рекордом» трассы.

    Баг на Мирном (02.08.2026): результат тестового прогона попадал в минимум
    до недели, и первый настоящий рекорд трассы выглядел как «побитие»
    рекорда, которого на самом деле не было.
    """
    suffix = uuid4().hex[:8]
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()
    location = Location(
        platform_id=platform.id,
        external_key=f"mirny-{suffix}",
        name="Мирный",
        city="Мирный",
    )
    db_session.add(location)
    db_session.flush()
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"course-test-{suffix}",
        display_name="Тестовый бегун",
        gender="male",
    )
    db_session.add(participant)
    db_session.flush()

    test_event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"course-test-event-{suffix}",
        event_date=date(2026, 7, 4),
        event_number=None,
        is_test_event=True,
        title=location.name,
    )
    db_session.add(test_event)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=test_event.id,
            participant_id=participant.id,
            external_result_key=f"{test_event.external_event_key}:{participant.external_user_id}",
            finish_time_sec=900,
            status="finished",
        )
    )
    db_session.flush()

    rows = _course_minima_before(db_session, WEEK_START)
    assert [row for row in rows if row[0] == location.id] == []


def _linked_participant(
    db_session: Session, suffix: str, *, private: bool = False, slug: str | None = None
) -> tuple[Participant, User]:
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"portal-link-{suffix}",
        display_name="Рекордсмен",
    )
    db_session.add(participant)
    user = User(display_name="Рекордсмен", public_slug=slug, profile_private=private)
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=platform.id,
            # participant_id намеренно пуст: связь ищется по
            # (platform_id, external_user_id), как в проде.
            external_user_id=participant.external_user_id,
            external_url=f"https://example.test/{suffix}/",
        )
    )
    db_session.flush()
    return participant, user


def test_runner_handle_prefers_public_slug(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    participant, _user = _linked_participant(db_session, suffix, slug=f"runner-{suffix}")
    assert _runner_handles(db_session, [participant.id]) == {
        participant.id: f"runner-{suffix}"
    }


def test_runner_handle_falls_back_to_serial_id(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    participant, user = _linked_participant(db_session, suffix)
    assert _runner_handles(db_session, [participant.id]) == {
        participant.id: str(user.serial_id)
    }


def test_runner_handle_skips_private_profile(db_session: Session) -> None:
    """Скрытый профиль отдал бы анониму 403 — ссылку на него не ставим."""
    suffix = str(uuid4().int % 1_000_000)
    participant, _user = _linked_participant(db_session, suffix, private=True)
    assert _runner_handles(db_session, [participant.id]) == {}


def test_runner_handles_empty_input_makes_no_query() -> None:
    assert _runner_handles(None, []) == {}  # type: ignore[arg-type]


def test_chart_window_closes_on_sunday_of_the_pulse_week() -> None:
    """Воскресный старт принадлежит неделе своей субботы.

    Кулибин 30.08.2026 (воскресенье, 14 финишёров) выпадал из последней недели
    графика, хотя единый протокол за субботу 29.08 его считал — расхождение
    RunPark 82 против 68 (Дмитрий 03.09.2026).
    """
    _, week_end = chart_window(date(2026, 8, 29))
    assert week_end == date(2026, 8, 30)


def test_chart_window_starts_on_a_monday() -> None:
    """Левый край окна — понедельник, иначе крайний столбец собирался бы из
    одного воскресенья предыдущей недели."""
    week_start, _ = chart_window(date(2026, 8, 29))
    assert week_start.weekday() == 0
    assert week_start == date(2026, 8, 24) - timedelta(days=7 * (CHART_WEEKS - 1))


def test_chart_window_labels_weeks_by_their_saturday() -> None:
    """Подпись недели на графиках совпадает с адресом единого протокола."""
    week_start, _ = chart_window(date(2026, 8, 29))
    assert saturday_of(week_start + timedelta(days=7 * (CHART_WEEKS - 1))) == date(2026, 8, 29)


def test_counted_events_skip_test_runs_like_the_protocol(db_session: Session) -> None:
    """Главная не считает тестовые старты — как единый протокол.

    10.10.2026 на графике «Локации по системам» у 5 вёрст стояло 216 вместо
    215: тест Чехова (№0, 9 финишёров) — репорт Егора 20.10.2026. Дубль
    кросслинка отсекается, только если его первичное событие само считается:
    тест 5 вёрст с RunPark-копией (Белгород, весна 2022) оставляет RunPark.
    """
    from app.models import EventCrosslink
    from app.services.portal_home_service import _counted_events

    suffix = uuid4().hex[:8]
    platforms = {}
    for code, name in (("five_verst", "5 вёрст"), ("runpark", "RunPark")):
        row = db_session.query(Platform).filter(Platform.code == code).one_or_none()
        if row is None:
            row = Platform(code=code, name=name)
            db_session.add(row)
            db_session.flush()
        platforms[code] = row

    def event(code: str, key: str, *, test: bool = False) -> Event:
        location = Location(platform_id=platforms[code].id, external_key=f"{key}-{suffix}", name=key)
        db_session.add(location)
        db_session.flush()
        row = Event(
            platform_id=platforms[code].id,
            location_id=location.id,
            external_event_key=f"{key}-{suffix}:1",
            event_date=date(2026, 10, 10),
            event_number=0 if test else 1,
            is_test_event=test,
            title=key,
        )
        db_session.add(row)
        db_session.flush()
        return row

    regular = event("five_verst", "regular")
    chekhov_test = event("five_verst", "chekhov", test=True)
    belgorod_test = event("five_verst", "belgorod", test=True)
    belgorod_runpark = event("runpark", "belgorod-rp")
    dup_primary = event("five_verst", "dup")
    dup_secondary = event("runpark", "dup-rp")
    db_session.add_all(
        [
            EventCrosslink(primary_event_id=belgorod_test.id, secondary_event_id=belgorod_runpark.id),
            EventCrosslink(primary_event_id=dup_primary.id, secondary_event_id=dup_secondary.id),
        ]
    )
    db_session.flush()

    mine = [regular, chekhov_test, belgorod_test, belgorod_runpark, dup_primary, dup_secondary]
    query = db_session.query(Event.id).filter(Event.id.in_([row.id for row in mine]))
    counted = {row[0] for row in _counted_events(query).all()}
    assert counted == {regular.id, belgorod_runpark.id, dup_primary.id}


def test_home_counts_places_without_test_runs_and_series(db_session: Session) -> None:
    """Локации на главной — как в едином протоколе: без тестов и без серий.

    Репорт Егора 20.10.2026: 10.10 у 5 вёрст на графике 216 локаций при 215
    в протоколе (тест Чехова), 08.08 — 207 против 206 («Старты сообществ»).
    Финиши серии в суммах остаются, финиши теста — нет.
    """
    from app.services.portal_home_service import _compute_portal_home

    suffix = uuid4().hex[:8]
    platform = db_session.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст")
        db_session.add(platform)
        db_session.flush()
    day = date(2026, 10, 10)

    def start(key: str, finishers: int, *, test: bool = False, series: bool = False) -> None:
        location = Location(
            platform_id=platform.id,
            external_key=f"{key}-{suffix}",
            name=key,
            country="Россия",
            is_series=series,
        )
        db_session.add(location)
        db_session.flush()
        db_session.add(
            Event(
                platform_id=platform.id,
                location_id=location.id,
                external_event_key=f"{key}-{suffix}:1",
                event_date=day,
                event_number=0 if test else 1,
                is_test_event=test,
                finishers_count=finishers,
                title=key,
            )
        )
        db_session.flush()

    start("park", 100)
    start("chekhov", 9, test=True)
    start("community", 50, series=True)

    payload = _compute_portal_home(db_session)
    week = next(item for item in payload["locations_by_week"] if item["saturday"] == day)
    finishes = next(item for item in payload["chart_weeks"] if item["saturday"] == day)
    assert week["platforms"]["five_verst"] == 1
    assert finishes["platforms"]["five_verst"] == 150
    assert payload["pulse"]["finishes"] == 150
