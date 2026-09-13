"""Сверка состава и нумерации событий S95 со списком площадки.

Оба обычных синка S95 ходят по `updated_at` и потому слепы к двум вещам:
S95 может удалить событие (про удаление нам никто не скажет), а от удаления
едет нумерация — номер мы считаем как хронологический ранг активности в списке
площадки. 14.09.2026 так нашлось в Великом Новгороде: лишняя пустая активность
550 за 01.04.2023 и сбитый номер у 165 событий.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Platform
from app.s95.api_client import S95ApiActivityRef
from app.sync.s95_events_reconcile import S95EventsReconcileResult, reconcile_location_events


def _platform(db: Session, code: str) -> Platform:
    row = db.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        row = Platform(code=code, name=code)
        db.add(row)
        db.flush()
    return row


def _location(db: Session, platform: Platform, key: str) -> Location:
    row = Location(platform_id=platform.id, external_key=key, name="Великий Новгород")
    db.add(row)
    db.flush()
    return row


def _event(db: Session, platform: Platform, location: Location, day: date, number: int, activity: int) -> Event:
    row = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"{location.external_key}:{day.isoformat()}",
        event_date=day,
        event_number=number,
        title=f"{location.name} #{number}",
        source_url=f"https://s95.ru/activities/{activity}.json",
    )
    db.add(row)
    db.flush()
    return row


def _refs(pairs: list[tuple[str, int]]) -> list[S95ApiActivityRef]:
    return [
        S95ApiActivityRef(date=day, url=f"https://s95.ru/activities/{act}.json")
        for day, act in pairs
    ]


def test_deleted_empty_event_is_removed_and_numbers_shift(db_session: Session) -> None:
    """Пустое событие, которого нет у источника, уходит — и номера встают на место."""
    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")

    first = _event(db_session, s95, loc, date(2023, 3, 25), 1, 549)
    phantom = _event(db_session, s95, loc, date(2023, 4, 1), 2, 550)
    third = _event(db_session, s95, loc, date(2023, 4, 8), 3, 547)

    # У источника активности 550 больше нет.
    refs = _refs([("2023-03-25", 549), ("2023-04-08", 547)])
    result = S95EventsReconcileResult()
    reconcile_location_events(db_session, s95, loc, refs, result)

    assert result.phantoms_deleted == 1
    assert result.numbers_fixed == 1
    assert db_session.query(Event).filter(Event.id == phantom.id).one_or_none() is None
    assert first.event_number == 1
    assert third.event_number == 2
    assert third.title.endswith("#2")


def test_phantom_release_frees_the_crosslinked_protocol(db_session: Session) -> None:
    """Кросслинк уходит вместе с фантомом — парный протокол снова в зачёте.

    Пустое событие стояло primary над RunPark-протоколом того же дня, а
    secondary исключается из подсчётов: 61 финишёр не считался нигде.
    """
    s95 = _platform(db_session, "s95")
    runpark = _platform(db_session, "runpark")
    suffix = uuid4().hex[:8]
    s95_loc = _location(db_session, s95, f"novgorod-{suffix}")
    rp_loc = _location(db_session, runpark, f"runpark-novgorod-{suffix}")

    phantom = _event(db_session, s95, s95_loc, date(2023, 4, 1), 2, 550)
    paired = _event(db_session, runpark, rp_loc, date(2023, 4, 1), 55, 999)
    db_session.add(EventCrosslink(primary_event_id=phantom.id, secondary_event_id=paired.id))
    db_session.flush()

    result = S95EventsReconcileResult()
    reconcile_location_events(db_session, s95, s95_loc, _refs([("2023-03-25", 549)]), result)

    assert result.crosslinks_released == 1
    assert db_session.query(EventCrosslink).filter(
        EventCrosslink.secondary_event_id == paired.id
    ).count() == 0
    # Парный протокол цел — он и должен стать записью того дня.
    assert db_session.query(Event).filter(Event.id == paired.id).one_or_none() is not None


def test_event_with_results_is_never_deleted(db_session: Session) -> None:
    """Если у источника события нет, а у нас есть результаты — это к человеку."""
    from app.models import Participant, RunResult

    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")
    event = _event(db_session, s95, loc, date(2023, 4, 1), 2, 550)
    participant = Participant(platform_id=s95.id, external_user_id=f"p-{suffix}", display_name="Тест")
    db_session.add(participant)
    db_session.flush()
    db_session.add(
        RunResult(
            event_id=event.id,
            participant_id=participant.id,
            external_result_key=f"k-{suffix}",
            finish_time_sec=1200,
        )
    )
    db_session.flush()

    result = S95EventsReconcileResult()
    reconcile_location_events(db_session, s95, loc, _refs([("2023-03-25", 549)]), result)

    assert result.phantoms_deleted == 0
    assert len(result.kept_with_results) == 1
    assert db_session.query(Event).filter(Event.id == event.id).one_or_none() is not None


def test_empty_source_list_changes_nothing(db_session: Session) -> None:
    """Пустой ответ источника — это сбой, а не «площадка без стартов»."""
    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")
    event = _event(db_session, s95, loc, date(2023, 4, 1), 2, 550)

    result = S95EventsReconcileResult()
    reconcile_location_events(db_session, s95, loc, [], result)

    assert result.phantoms_deleted == 0
    assert result.errors
    assert db_session.query(Event).filter(Event.id == event.id).one_or_none() is not None


def test_missing_protocol_is_reported_not_fetched(db_session: Session) -> None:
    """Чего нет у нас — показываем в сводке, качать протокол здесь не дело."""
    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")
    _event(db_session, s95, loc, date(2023, 3, 25), 1, 549)

    refs = _refs([("2023-03-25", 549), ("2026-09-12", 4807)])
    result = S95EventsReconcileResult()
    reconcile_location_events(db_session, s95, loc, refs, result)

    assert result.missing_protocols == [f"{loc.external_key}:2026-09-12"]
    assert result.phantoms_deleted == 0


def test_dry_run_counts_but_changes_nothing(db_session: Session) -> None:
    """apply=False считает расхождения, но в базу не пишет — режим «посмотреть»."""
    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")
    phantom = _event(db_session, s95, loc, date(2023, 4, 1), 2, 550)
    third = _event(db_session, s95, loc, date(2023, 4, 8), 3, 547)

    result = S95EventsReconcileResult()
    reconcile_location_events(
        db_session, s95, loc, _refs([("2023-04-08", 547)]), result, apply=False
    )

    assert result.phantoms_deleted == 1
    assert result.numbers_fixed == 1
    # А в базе всё как было.
    assert db_session.query(Event).filter(Event.id == phantom.id).one_or_none() is not None
    assert third.event_number == 3


def test_transaction_is_released_before_every_fetch(db_session: Session) -> None:
    """Перед каждым походом в сеть транзакция должна быть отпущена.

    На проде стоит idle_in_transaction_session_timeout, и висящая транзакция
    рвёт соединение — падает при этом первый SELECT ПОСЛЕ фетча, на невиновном
    запросе. 14.09.2026 я поймал это дважды подряд: сперва забыл отпустить
    перед списками площадок, потом перед списком активностей.
    """
    from unittest.mock import patch

    from app.sync import s95_events_reconcile as mod

    s95 = _platform(db_session, "s95")
    suffix = uuid4().hex[:8]
    loc = _location(db_session, s95, f"novgorod-{suffix}")
    _event(db_session, s95, loc, date(2023, 3, 25), 1, 549)
    db_session.commit()

    api_loc = SimpleNamespace(slug=loc.external_key, domain="https://s95.ru")
    in_transaction_at_fetch: list[str] = []

    def _locations():
        if db_session.in_transaction():
            in_transaction_at_fetch.append("fetch_all_locations")
        return [api_loc]

    def _activities(url: str):
        if db_session.in_transaction():
            in_transaction_at_fetch.append("fetch_event_activities")
        return _refs([("2023-03-25", 549)])

    with (
        patch.object(mod, "fetch_all_locations", _locations),
        patch.object(mod, "fetch_event_activities", _activities),
    ):
        mod.reconcile_s95_events(db_session, apply=False)

    assert in_transaction_at_fetch == [], (
        "транзакция открыта во время сетевого вызова: " + ", ".join(in_transaction_at_fetch)
    )
