from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, Location, Participant, Platform, RunResult, VolunteerResult
from app.services.admin_event_report_service import build_event_report, list_report_event_dates


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
        external_event_key=f"report-event-{suffix}-{event_number}",
        event_date=event_date,
        event_number=event_number,
        title=f"Event {event_number}",
    )
    db_session.add(event)
    db_session.flush()
    return event


def _make_participant(db_session: Session, platform: Platform, suffix: str, name: str) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"report-{suffix}",
        display_name=name,
        profile_url=f"https://example.test/{suffix}/",
    )
    db_session.add(participant)
    db_session.flush()
    return participant


def _add_run(
    db_session: Session,
    event: Event,
    participant: Participant,
    *,
    finish_time_sec: int | None,
    position: int | None = None,
    age_category: str | None = None,
    status: str = "finished",
) -> None:
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"run-{uuid4()}",
            position=position,
            finish_time_sec=finish_time_sec,
            finish_time_display=None if finish_time_sec is None else "00:20:00",
            age_category=age_category,
            status=status,
        )
    )


def _setup_location(db_session: Session, suffix: str) -> tuple[Platform, Location]:
    platform = _get_platform(db_session, "five_verst", "5 вёрст")
    location = Location(
        platform_id=platform.id,
        external_key=f"report-loc-{suffix}",
        name="Report Location",
        city="Москва",
        country="Россия",
    )
    db_session.add(location)
    db_session.flush()
    return platform, location


def test_age_group_rank_ignores_place_suffix(db_session: Session) -> None:
    """5 вёрст пишет в age_category «М40-44 (2)» — место в группе не должно дробить группу."""
    suffix = str(uuid4().int % 1_000_000)
    platform, location = _setup_location(db_session, suffix)

    past = _make_event(db_session, platform, location, suffix, date(2024, 6, 1), 1)
    today = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 2)

    rivals = [_make_participant(db_session, platform, f"{suffix}-r{i}", f"Rival {i}") for i in range(3)]
    # Прошлые забеги той же группы, но с другими суффиксами места.
    for index, rival in enumerate(rivals):
        _add_run(db_session, past, rival, finish_time_sec=1100 + index, age_category=f"М40-44 ({index + 1})")

    hero = _make_participant(db_session, platform, f"{suffix}-hero", "Иван Быстрый")
    _add_run(db_session, today, hero, finish_time_sec=1000, position=1, age_category="М40-44 (1)")
    db_session.commit()

    report = build_event_report(db_session, today.id)
    assert report is not None
    top = report["top_finishes"]
    assert len(top) == 1
    # Группа схлопнута в «М40-44»: 4 результата, наш — быстрейший.
    assert top[0]["age_category"] == "М40-44"
    assert (top[0]["age_rank"], top[0]["age_total"]) == (1, 4)
    assert top[0]["finish_time_display"] == "16:40"


def test_results_without_finish_time_are_not_finishers(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform, location = _setup_location(db_session, suffix)
    event = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 1)

    finisher = _make_participant(db_session, platform, f"{suffix}-fin", "Финишёр")
    unknown = _make_participant(db_session, platform, f"{suffix}-unk", "Без времени")
    _add_run(db_session, event, finisher, finish_time_sec=1200, position=1, age_category="М30-34 (1)")
    _add_run(db_session, event, unknown, finish_time_sec=None, status="unknown")
    db_session.commit()

    report = build_event_report(db_session, event.id)
    assert report is not None
    assert report["header"]["finishers"] == 1

    dates = list_report_event_dates(db_session, location.id)
    assert [item["finishers_count"] for item in dates] == [1]

    # «НЕИЗВЕСТНЫЙ» без времени не должен попадать в новички системы —
    # иначе каждая неопознанная строка протокола раздувает статистику.
    assert [person["name"] for person in report["stats"]["newcomers"]] == ["Финишёр"]


def test_newcomer_is_not_counted_as_personal_best(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform, location = _setup_location(db_session, suffix)
    event = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 1)

    rookie = _make_participant(db_session, platform, f"{suffix}-new", "Новичок")
    _add_run(db_session, event, rookie, finish_time_sec=1500, position=1, age_category="М30-34 (1)")
    db_session.commit()

    report = build_event_report(db_session, event.id)
    assert report is not None
    stats = report["stats"]
    assert [person["name"] for person in stats["newcomers"]] == ["Новичок"]
    assert stats["personal_bests"] == []
    assert stats["guests"] == []


def test_course_record_and_attendance_record(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform, location = _setup_location(db_session, suffix)
    past = _make_event(db_session, platform, location, suffix, date(2024, 6, 1), 1)
    today = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 2)

    old_holder = _make_participant(db_session, platform, f"{suffix}-old", "Старый Рекордсмен")
    _add_run(db_session, past, old_holder, finish_time_sec=1080, position=1, age_category="М30-34 (1)")

    new_holder = _make_participant(db_session, platform, f"{suffix}-new", "Новый Рекордсмен")
    guest = _make_participant(db_session, platform, f"{suffix}-guest", "Гость")
    _add_run(db_session, today, new_holder, finish_time_sec=1000, position=1, age_category="М30-34 (1)")
    _add_run(db_session, today, guest, finish_time_sec=1400, position=2, age_category="М30-34 (2)")
    db_session.commit()

    report = build_event_report(db_session, today.id)
    assert report is not None
    records = report["course_records"]
    assert len(records) == 1
    assert records[0]["name"] == "Новый Рекордсмен"
    assert (records[0]["time_sec"], records[0]["previous_sec"]) == (1000, 1080)
    # Сегодня 2 финишёра против 1 в прошлый раз — рекорд посещаемости.
    assert report["header"]["attendance_record"] is True
    assert "Рекорд трассы среди мужчин" in report["post_text"]


def test_first_volunteering_and_new_role(db_session: Session) -> None:
    suffix = str(uuid4().int % 1_000_000)
    platform, location = _setup_location(db_session, suffix)
    past = _make_event(db_session, platform, location, suffix, date(2024, 6, 1), 1)
    today = _make_event(db_session, platform, location, suffix, date(2024, 6, 8), 2)

    veteran = _make_participant(db_session, platform, f"{suffix}-vet", "Ветеран")
    rookie = _make_participant(db_session, platform, f"{suffix}-rookie", "Дебютант")
    db_session.add(
        VolunteerResult(
            event_id=past.id,
            participant_id=veteran.id,
            external_result_key=f"vol-{uuid4()}",
            role="Маршал",
        )
    )
    db_session.add(
        VolunteerResult(
            event_id=today.id,
            participant_id=veteran.id,
            external_result_key=f"vol-{uuid4()}",
            role="Хронометрист",
        )
    )
    db_session.add(
        VolunteerResult(
            event_id=today.id,
            participant_id=rookie.id,
            external_result_key=f"vol-{uuid4()}",
            role="Маршал",
        )
    )
    db_session.commit()

    report = build_event_report(db_session, today.id)
    assert report is not None
    stats = report["stats"]
    assert [person["name"] for person in stats["first_volunteers"]] == ["Дебютант"]
    assert [person["name"] for person in stats["new_role_volunteers"]] == ["Ветеран"]
    assert report["header"]["volunteers"] == 2
