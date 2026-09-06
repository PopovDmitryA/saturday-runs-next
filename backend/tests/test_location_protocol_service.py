from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

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
from app.services.location_protocol_service import (
    _age_grade,
    build_location_protocol,
)


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, suffix: str, name: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"proto-{suffix}",
        name=name,
        city="Тюмень",
    )
    db.add(location)
    db.flush()
    return location


def _event(
    db: Session, platform: Platform, location: Location, day: date, *, number: int | None = None
) -> Event:
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"proto-event-{uuid4().hex[:10]}",
        event_date=day,
        event_number=number,
        title=location.name,
    )
    db.add(event)
    db.flush()
    return event


def _participant(
    db: Session,
    platform: Platform,
    suffix: str,
    name: str,
    *,
    gender: str | None = None,
    age_category: str | None = None,
) -> Participant:
    participant = Participant(
        platform_id=platform.id,
        external_user_id=f"proto-{suffix}",
        display_name=name,
        gender=gender,
        age_category=age_category,
    )
    db.add(participant)
    db.flush()
    return participant


def _result(
    db: Session,
    event: Event,
    participant: Participant | None,
    *,
    position: int | None,
    finish_time_sec: int | None,
    age_category: str | None = None,
    gender_position: int | None = None,
    is_pr: bool = False,
    is_first_run: bool = False,
    is_first_run_at_location: bool = False,
    club_name: str | None = None,
) -> None:
    db.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id if participant else None,
            external_result_key=f"{event.external_event_key}:{uuid4().hex[:8]}",
            position=position,
            gender_position=gender_position,
            finish_time_sec=finish_time_sec,
            age_category=age_category,
            is_pr=is_pr,
            is_first_run=is_first_run,
            is_first_run_at_location=is_first_run_at_location,
            club_name=club_name,
            status="finished",
        )
    )
    db.flush()


def _volunteer(db: Session, event: Event, participant: Participant, role: str) -> None:
    db.add(
        VolunteerResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"{event.external_event_key}:vol:{uuid4().hex[:8]}",
            role=role,
        )
    )
    db.flush()


def _build(db: Session, slug: str, platform_code: str, day: date, **kwargs: Any) -> dict[str, Any]:
    payload = build_location_protocol(db, slug, platform_code, day, use_cache=False, **kwargs)
    assert payload is not None
    return payload


def test_missing_protocol_returns_none(db_session: Session) -> None:
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    _location(db_session, five_verst, "empty", "Пустой парк")
    assert (
        build_location_protocol(
            db_session, "proto-empty", "five_verst", date(2026, 1, 3), use_cache=False
        )
        is None
    )


def test_places_by_gender_and_age_category(db_session: Session) -> None:
    """Места по полу и внутри возрастной категории — как в панели Grafana."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "main", "Затюменский")
    event = _event(db_session, five_verst, location, date(2026, 8, 15), number=218)

    fast = _participant(db_session, five_verst, "fast", "Первый БЫСТРЫЙ")
    older = _participant(db_session, five_verst, "older", "Второй СТАРШИЙ")
    peer = _participant(db_session, five_verst, "peer", "Третий РОВЕСНИК")
    woman = _participant(db_session, five_verst, "woman", "Первая БЫСТРАЯ")

    _result(db_session, event, fast, position=1, finish_time_sec=1026, age_category="М40-44")
    _result(db_session, event, older, position=2, finish_time_sec=1100, age_category="М45-49")
    _result(db_session, event, peer, position=3, finish_time_sec=1200, age_category="М40-44")
    _result(db_session, event, woman, position=4, finish_time_sec=1300, age_category="Ж35-39")

    payload = _build(db_session, "proto-main", "five_verst", date(2026, 8, 15))

    rows = {row["name"]: row for row in payload["results"]}
    assert rows["Первый БЫСТРЫЙ"]["gender_position"] == 1
    assert rows["Первый БЫСТРЫЙ"]["gender_total"] == 3
    assert rows["Первый БЫСТРЫЙ"]["age_group_position"] == 1
    assert rows["Первый БЫСТРЫЙ"]["age_group_total"] == 2
    assert rows["Третий РОВЕСНИК"]["age_group_position"] == 2
    assert rows["Третий РОВЕСНИК"]["gender_position"] == 3
    assert rows["Первая БЫСТРАЯ"]["gender_position"] == 1
    assert rows["Первая БЫСТРАЯ"]["gender_total"] == 1

    summary = payload["summary"]
    assert summary["finishers"] == 4
    assert summary["male"] == 3
    assert summary["female"] == 1
    assert summary["best_male_runner_name"] == "Первый БЫСТРЫЙ"
    assert summary["best_female_runner_name"] == "Первая БЫСТРАЯ"

    groups = {group["age_group"]: group for group in payload["age_groups"]}
    assert groups["40–44"]["male"] == 2
    assert groups["35–39"]["female"] == 1


def test_platform_gender_position_wins_over_recount(db_session: Session) -> None:
    """В неполном протоколе пересчёт мест по полу не затирает места платформы.

    Зарубежный parkrun: в БД только строки наших участников, пересчёт дал бы
    «1-я среди женщин» каждой женщине.
    """
    parkrun = _platform(db_session, "parkrun", "parkrun")
    location = _location(db_session, parkrun, "abroad", "Urheilupuisto")
    event = _event(db_session, parkrun, location, date(2026, 6, 20), number=55)

    runner = _participant(
        db_session, parkrun, "vm", "Alexei KUKHARENKO", gender="male", age_category="VM50-54"
    )
    _result(
        db_session,
        event,
        runner,
        position=5,
        gender_position=4,
        finish_time_sec=1362,
        age_category="64.98%",
    )

    payload = _build(db_session, "proto-abroad", "parkrun", date(2026, 6, 20))

    assert payload["is_partial"] is True
    row = payload["results"][0]
    assert row["gender"] == "male"
    assert row["gender_position"] == 4  # место платформы, не пересчёт
    assert row["gender_total"] == 4
    # В age_category у parkrun лежит age grade, а не категория.
    assert row["age_category"] is None
    assert row["age_grade"] == 64.98


def test_history_rank_spans_platforms(db_session: Session) -> None:
    """Место в истории площадки считается сквозь все системы идентичности."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "hist", "Исторический")

    old_event = _event(db_session, five_verst, location, date(2026, 8, 8), number=1)
    legend = _participant(db_session, five_verst, "legend", "Легенда ПАРКА")
    _result(db_session, old_event, legend, position=1, finish_time_sec=1000, age_category="М30-34")

    event = _event(db_session, five_verst, location, date(2026, 8, 15), number=2)
    runner = _participant(db_session, five_verst, "runner", "Сегодняшний БЕГУН")
    _result(db_session, event, runner, position=1, finish_time_sec=1050, age_category="М30-34")

    payload = _build(db_session, "proto-hist", "five_verst", date(2026, 8, 15))
    row = payload["results"][0]
    assert row["history_rank"] == 2
    assert row["history_total"] == 2
    # Обе пробежки — в одной группе М30-34: место и в истории группы.
    assert row["age_group_history_rank"] == 2
    assert row["age_group_history_total"] == 2
    # Вторая пробежка бегуна? Нет — первая: неделей раньше бежала легенда.
    assert row["run_number"] == 1
    # Рекорд группы устоял: 1050 медленнее прежних 1000 в М30-34.
    assert row["is_age_group_record"] is False

    # Соседние старты: у второго события предыдущее — первое, с его цифрами
    # для дельт на плитках.
    assert payload["previous"] is not None
    assert payload["previous"]["event_number"] == 1
    assert payload["previous"]["finishers"] == 1
    assert payload["next"] is None


def test_run_number_and_age_group_record(db_session: Session) -> None:
    """Номер пробежки в системе и отметка нового рекорда возрастной группы."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "rec", "Рекордный")

    old_event = _event(db_session, five_verst, location, date(2026, 8, 8), number=1)
    veteran = _participant(db_session, five_verst, "vet", "Ветеран ГРУППЫ")
    _result(db_session, old_event, veteran, position=1, finish_time_sec=1200, age_category="М40-44")

    event = _event(db_session, five_verst, location, date(2026, 8, 15), number=2)
    _result(db_session, event, veteran, position=1, finish_time_sec=1100, age_category="М40-44")
    slower = _participant(db_session, five_verst, "slow", "Тоже БЫСТРЫЙ")
    # Тоже быстрее прежнего рекорда 1200, но медленнее лучшего дня — рекорд
    # достаётся только лучшему в группе.
    _result(db_session, event, slower, position=2, finish_time_sec=1150, age_category="М40-44")

    payload = _build(db_session, "proto-rec", "five_verst", date(2026, 8, 15))
    rows = {row["name"]: row for row in payload["results"]}
    assert rows["Ветеран ГРУППЫ"]["run_number"] == 2
    assert rows["Ветеран ГРУППЫ"]["is_age_group_record"] is True
    assert rows["Тоже БЫСТРЫЙ"]["run_number"] == 1
    assert rows["Тоже БЫСТРЫЙ"]["is_age_group_record"] is False


def test_run_number_counts_all_linked_systems(db_session: Session) -> None:
    """У зарегистрированного на сайте счёт пробежек сквозной по всем системам."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    s95 = _platform(db_session, "s95", "С95")
    location = _location(db_session, five_verst, "cross", "Кроссистемный")
    s95_location = _location(db_session, s95, "cross-s95", "Другая система")

    runner = _participant(db_session, five_verst, "cross-5v", "Кросс БЕГУН")
    twin = _participant(db_session, s95, "cross-s95", "Кросс БЕГУН", gender="male")
    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Кросс")
    db_session.add(user)
    db_session.flush()
    for platform, participant in ((five_verst, runner), (s95, twin)):
        db_session.add(
            PlatformLink(
                user_id=user.id,
                platform_id=platform.id,
                participant_id=participant.id,
                external_user_id=participant.external_user_id,
                external_url="https://example.com",
            )
        )
    db_session.flush()

    # По одной прошлой пробежке в каждой системе.
    _result(
        db_session,
        _event(db_session, five_verst, location, date(2026, 8, 1)),
        runner,
        position=1,
        finish_time_sec=1300,
        age_category="М30-34",
    )
    _result(
        db_session,
        _event(db_session, s95, s95_location, date(2026, 8, 8)),
        twin,
        position=1,
        finish_time_sec=1310,
    )

    event = _event(db_session, five_verst, location, date(2026, 8, 15))
    _result(db_session, event, runner, position=1, finish_time_sec=1290, age_category="М30-34")
    stranger = _participant(db_session, five_verst, "cross-solo", "Один В СИСТЕМЕ")
    _result(db_session, event, stranger, position=2, finish_time_sec=1400, age_category="М30-34")

    payload = _build(db_session, "proto-cross", "five_verst", date(2026, 8, 15))
    rows = {row["name"]: row for row in payload["results"]}
    # Две прошлые пробежки в разных системах + эта.
    assert rows["Кросс БЕГУН"]["run_number"] == 3
    assert rows["Кросс БЕГУН"]["run_number_all_systems"] is True
    # Без аккаунта сквозного счёта взяться неоткуда.
    assert rows["Один В СИСТЕМЕ"]["run_number"] == 1
    assert rows["Один В СИСТЕМЕ"]["run_number_all_systems"] is False


def test_volunteers_grouped_by_person(db_session: Session) -> None:
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "vol", "Волонтёрский")
    past = _event(db_session, five_verst, location, date(2026, 8, 8))
    event = _event(db_session, five_verst, location, date(2026, 8, 15))
    busy = _participant(db_session, five_verst, "busy", "Занятой ВОЛОНТЁР")
    # Неделей раньше уже секундомерил здесь — на этом старте Маршал для него
    # новая роль, а волонтёрство второе по счёту.
    _volunteer(db_session, past, busy, "Секундомер")
    _volunteer(db_session, event, busy, "Секундомер")
    _volunteer(db_session, event, busy, "Маршал")
    runner = _participant(db_session, five_verst, "also-runs", "Бегущий ВОЛОНТЁР")
    _result(db_session, event, runner, position=1, finish_time_sec=1200, age_category="М30-34")
    _volunteer(db_session, event, runner, "Фотограф")

    payload = _build(db_session, "proto-vol", "five_verst", date(2026, 8, 15))
    volunteers = {person["name"]: person for person in payload["volunteers"]}
    busy_row = volunteers["Занятой ВОЛОНТЁР"]
    assert busy_row["roles"] == ["Маршал", "Секундомер"]
    assert busy_row["new_roles"] == ["Маршал"]
    assert busy_row["volunteer_number"] == 2
    assert busy_row["is_first_volunteering"] is False
    assert busy_row["is_first_here"] is False
    debutant = volunteers["Бегущий ВОЛОНТЁР"]
    assert debutant["roles"] == ["Фотограф"]
    assert debutant["new_roles"] == ["Фотограф"]
    assert debutant["volunteer_number"] == 1
    assert debutant["is_first_volunteering"] is True
    assert payload["summary"]["volunteers"] == 2


def test_viewer_row_marked_after_cache(db_session: Session) -> None:
    """Подсветка «вы» работает и на кэшированном протоколе."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "me", "Свой парк")
    event = _event(db_session, five_verst, location, date(2026, 8, 15))
    runner = _participant(db_session, five_verst, "self", "Это Я")
    _result(db_session, event, runner, position=1, finish_time_sec=1500, age_category="М30-34")

    user = User(telegram_id=int(uuid4().int % 1_000_000_000), display_name="Я")
    db_session.add(user)
    db_session.flush()
    db_session.add(
        PlatformLink(
            user_id=user.id,
            platform_id=five_verst.id,
            participant_id=runner.id,
            external_user_id=runner.external_user_id,
            external_url="https://example.com",
        )
    )
    db_session.flush()

    anonymous = _build(db_session, "proto-me", "five_verst", date(2026, 8, 15))
    assert anonymous["results"][0]["is_me"] is False

    own = _build(db_session, "proto-me", "five_verst", date(2026, 8, 15), viewer=user)
    assert own["results"][0]["is_me"] is True


def test_unknown_runner_marked(db_session: Session) -> None:
    """Финишёр без штрихкода приглушается витриной — и по статусу, и по имени."""
    five_verst = _platform(db_session, "five_verst", "5 вёрст")
    location = _location(db_session, five_verst, "unk", "Парк с неизвестными")
    event = _event(db_session, five_verst, location, date(2026, 8, 15))
    runner = _participant(db_session, five_verst, "known", "Известный БЕГУН")
    _result(db_session, event, runner, position=1, finish_time_sec=1200, age_category="М30-34")
    nobody = _participant(db_session, five_verst, "nobody", "НЕИЗВЕСТНЫЙ")
    _result(db_session, event, nobody, position=2, finish_time_sec=None)

    payload = _build(db_session, "proto-unk", "five_verst", date(2026, 8, 15))
    rows = {row["name"]: row for row in payload["results"]}
    assert rows["Известный БЕГУН"]["is_unknown"] is False
    assert rows["НЕИЗВЕСТНЫЙ"]["is_unknown"] is True


def test_age_grade_parsing() -> None:
    assert _age_grade("70.13%") == 70.13
    assert _age_grade("54,38%") == 54.38
    assert _age_grade("М35-39") is None
    assert _age_grade(None) is None
    assert _age_grade("") is None


def test_newcomers_split_debutants_and_location_guests(db_session: Session) -> None:
    """Дебютант системы не должен попадать ещё и в гостей площадки.

    У parkrun/RunPark/S95 флаги проставляет пересчёт по хронологии, и у первого
    в жизни старта верны оба: он же первый и на этой площадке. Пока сводка
    складывала «дебютанты + впервые здесь», такой человек считался дважды, и
    «новичков» на старте выходило больше, чем финишёров-новичков.
    """
    parkrun = _platform(db_session, "parkrun", "parkrun")
    location = _location(db_session, parkrun, "newcomers", "Мытищи Центральный парк")
    event = _event(db_session, parkrun, location, date(2026, 8, 15), number=42)

    debutant = _participant(db_session, parkrun, "newcomers-deb", "Дебютант Системы")
    guest = _participant(db_session, parkrun, "newcomers-guest", "Гость Площадки")
    regular = _participant(db_session, parkrun, "newcomers-reg", "Постоянный Участник")

    # Первый старт в жизни: пересчёт ставит ОБА флага.
    _result(
        db_session,
        event,
        debutant,
        position=1,
        finish_time_sec=1200,
        is_first_run=True,
        is_first_run_at_location=True,
    )
    # Бегал в системе, сюда приехал впервые.
    _result(
        db_session,
        event,
        guest,
        position=2,
        finish_time_sec=1300,
        is_first_run_at_location=True,
    )
    _result(db_session, event, regular, position=3, finish_time_sec=1400)

    summary = _build(db_session, "proto-newcomers", "parkrun", date(2026, 8, 15))["summary"]
    assert summary["debutants"] == 1
    assert summary["first_at_location"] == 1
    # Новичков на старте ровно двое, а не трое.
    assert summary["debutants"] + summary["first_at_location"] == 2
