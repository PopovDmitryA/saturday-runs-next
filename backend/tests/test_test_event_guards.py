"""Тестовый старт (№0) не считается нигде — защиты в синках (разбор 21.10.2026)."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Event, EventCrosslink, Location, Platform, RunResult
from app.platform_adapters.canonical import CanonicalEventSummary, CanonicalLocation, CanonicalRunResult
from app.sync import upsert


def _location(db: Session, platform: Platform, key: str) -> Location:
    location, _ = upsert.upsert_location(db, platform, CanonicalLocation(external_key=f"{key}-{uuid4().hex[:8]}", name=key))
    db.flush()
    return location


def _summary(location: Location, day: date, *, test: bool, number: int | None) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=f"{location.external_key}:{number or 0}:{day.isoformat()}",
        event_date=day,
        event_number=number,
        location_external_key=location.external_key,
        location_name=location.name,
        source_url=f"https://5verst.ru/{location.external_key}/results/{day:%d.%m.%Y}/",
        summary_hash=f"h-{uuid4().hex[:8]}",
        is_test_event=test,
    )


def _event_for_summary(db: Session, platform: Platform, location: Location, summary: CanonicalEventSummary) -> Event:
    row, _ = upsert.upsert_event_summary(db, platform, location, summary)
    event = upsert.upsert_event_for_summary(db, platform, location, summary, row)
    db.flush()
    return event


def test_event_turned_test_stops_being_crosslink_primary(db_session: Session) -> None:
    """5 вёрст пометили старт тестом — связка с RunPark снимается.

    Иначе вторичный старт RunPark выбрасывается как дубль, а тест не считается,
    и настоящий старт пропадает везде (Михалково 2022: 47 финишей).
    """
    five = upsert.get_platform(db_session, "five_verst")
    runpark = upsert.get_platform(db_session, "runpark")
    day = date(2022, 5, 21)
    location = _location(db_session, five, "Михалково")
    primary = _event_for_summary(db_session, five, location, _summary(location, day, test=False, number=1))
    rp_location = _location(db_session, runpark, "RUNtonda")
    secondary = Event(
        platform_id=runpark.id,
        location_id=rp_location.id,
        external_event_key=f"rp-{uuid4().hex[:8]}",
        event_date=day,
        event_number=11,
        title="RUNtonda #11",
    )
    db_session.add(secondary)
    db_session.flush()
    db_session.add(EventCrosslink(primary_event_id=primary.id, secondary_event_id=secondary.id))
    db_session.flush()

    test_summary = _summary(location, day, test=True, number=None)
    test_summary.external_event_key = primary.external_event_key
    retested = _event_for_summary(db_session, five, location, test_summary)

    assert retested.id == primary.id and retested.is_test_event
    assert db_session.query(EventCrosslink).filter(EventCrosslink.secondary_event_id == secondary.id).count() == 0


def test_test_event_results_get_no_debut_or_record_flags(db_session: Session) -> None:
    """Иконки «первый финиш» и «рекорд» на тестовом протоколе не засчитываются."""
    five = upsert.get_platform(db_session, "five_verst")
    location = _location(db_session, five, "Губайловский")
    day = date(2022, 6, 18)
    event = _event_for_summary(db_session, five, location, _summary(location, day, test=True, number=None))
    item = CanonicalRunResult(
        external_result_key=f"r-{uuid4().hex[:8]}",
        event_date=day,
        external_user_id=f"u-{uuid4().hex[:8]}",
        participant_name="Алексей ВЫБОРНОВ",
        position=2,
        finish_time_sec=1500,
        finish_time_display="25:00",
        is_pr=True,
        is_first_run=True,
        is_first_run_at_location=True,
    )
    upsert.upsert_run_results(db_session, event, five, [item], recalculate_pr=False)
    row = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert (row.is_pr, row.is_first_run, row.is_first_run_at_location) == (False, False, False)


def test_profile_import_keeps_known_test_flag(db_session: Session) -> None:
    """Импорт профиля о тесте не знает — и не превращает тест в обычный старт."""
    five = upsert.get_platform(db_session, "five_verst")
    location = _location(db_session, five, "Видное")
    day = date(2026, 3, 14)
    event = _event_for_summary(db_session, five, location, _summary(location, day, test=True, number=None))

    again = upsert.upsert_event_for_profile(
        db_session,
        five,
        location,
        external_event_key=event.external_event_key,
        event_date=day,
        event_number=None,
        location_name=location.name,
        location_slug=location.external_key,
        source_url=f"https://5verst.ru/{location.external_key}/results/{day:%d.%m.%Y}/",
    )
    assert again.id == event.id
    assert again.is_test_event is True
    assert again.title.endswith("(тестовый)")
    assert upsert.known_test_event(db_session, location.id, day) is True


def test_runpark_event_follows_source_test_flag(db_session: Session) -> None:
    """RunPark поменял признак теста задним числом — у нас он и подпись тоже меняются."""
    from app.sync.runpark_global_sync import _ensure_event

    runpark = upsert.get_platform(db_session, "runpark")
    location = _location(db_session, runpark, "Кулибин")
    row = {"event_id": uuid4().hex, "event_date": date(2025, 1, 4), "event_number": -1, "is_test_event": False}
    event = _ensure_event(db_session, runpark, location, row)
    assert event.is_test_event is False

    event = _ensure_event(db_session, runpark, location, {**row, "is_test_event": True})
    assert event.is_test_event is True
    assert event.title == f"{location.name} (тестовый)"


def test_event_marked_test_later_loses_debut_flags(db_session: Session) -> None:
    """5 вёрст пометили загруженный старт тестом, а строки протокола не изменились.

    Протокол тогда идёт коротким путём и строки не переписывает — флаги и метки
    из иконок снимаются в момент смены признака.
    """
    five = upsert.get_platform(db_session, "five_verst")
    location = _location(db_session, five, "Лес")
    day = date(2026, 9, 26)
    event = _event_for_summary(db_session, five, location, _summary(location, day, test=False, number=1))
    item = CanonicalRunResult(
        external_result_key=f"r-{uuid4().hex[:8]}",
        event_date=day,
        external_user_id=f"u-{uuid4().hex[:8]}",
        participant_name="Иван ДЕБЮТОВ",
        position=1,
        finish_time_sec=1400,
        finish_time_display="23:20",
        is_first_run=True,
        achievement_labels=["Первый финиш на 5 вёрст"],
    )
    upsert.upsert_run_results(db_session, event, five, [item], recalculate_pr=False)
    row = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert row.is_first_run is True

    retest = _summary(location, day, test=True, number=None)
    retest.external_event_key = event.external_event_key
    _event_for_summary(db_session, five, location, retest)
    db_session.refresh(row)
    assert row.is_first_run is False
    assert row.achievement_labels == []


def test_profile_import_creates_five_verst_zero_as_test(db_session: Session) -> None:
    """Профиль раньше протокола: забег №0 у 5 вёрст сразу заводится тестом."""
    five = upsert.get_platform(db_session, "five_verst")
    location = _location(db_session, five, "Новая")
    day = date(2026, 10, 10)
    event = upsert.upsert_event_for_profile(
        db_session,
        five,
        location,
        external_event_key=f"{location.external_key}:0:{day.isoformat()}",
        event_date=day,
        event_number=0,
        location_name=location.name,
        location_slug=location.external_key,
        source_url=f"https://5verst.ru/{location.external_key}/results/{day:%d.%m.%Y}/",
    )
    assert event.is_test_event is True
    assert event.title == f"{location.name} (тестовый)"
