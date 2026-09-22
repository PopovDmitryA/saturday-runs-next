"""Пустой протокол не стирает сохранённый: сторож в upsert.replace_event_*.

Воспроизведение аудита 13.09.2026: страница техработ (200 без таблицы) у
5 вёрст, JSON s95 без results, пустая выборка вьюх RunPark — каждый такой
разбор раньше считался авторитетным и удалял все строки старта, а состояние
протокола получало новый хэш, так что перечитки не случалось.
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Event,
    Location,
    Platform,
    ProtocolSyncState,
    RunparkLocationMapping,
    RunResult,
    SyncStatus,
    VolunteerResult,
)
from app.platform_adapters.canonical import (
    CanonicalEventSummary,
    CanonicalRunResult,
    CanonicalVolunteerResult,
)
from app.sync import upsert
from app.sync.upsert import SuspectEmptyProtocolError

DAY = date(2027, 3, 13)


def _platform(db: Session, code: str) -> Platform:
    row = db.query(Platform).filter(Platform.code == code).one_or_none()
    if row is None:
        pytest.skip(f"{code} platform not seeded")
    return row


def _location(db: Session, platform: Platform, prefix: str) -> Location:
    location = Location(
        platform_id=platform.id,
        external_key=f"{prefix}-{uuid4().hex[:8]}",
        name="Guard Park",
        source_url=f"https://example.test/{prefix}/",
    )
    db.add(location)
    db.flush()
    return location


def _runs(slug: str, count: int) -> list[CanonicalRunResult]:
    return [
        CanonicalRunResult(
            external_result_key=f"{slug}:{DAY.isoformat()}:{900000 + i}",
            event_date=DAY,
            external_user_id=str(900000 + i),
            participant_name=f"Runner {i}",
            position=i + 1,
            finish_time_sec=1200 + i,
            finish_time_display=f"00:{20 + i // 60:02d}:{i % 60:02d}",
            age_category="М30-34",
            location_external_key=slug,
            event_number=10,
        )
        for i in range(count)
    ]


def _vols(slug: str, count: int) -> list[CanonicalVolunteerResult]:
    return [
        CanonicalVolunteerResult(
            external_result_key=f"{slug}:{DAY.isoformat()}:vol:{800000 + i}:marshal",
            event_date=DAY,
            external_user_id=str(800000 + i),
            participant_name=f"Vol {i}",
            role="Маршал",
            source_url="",
            location_external_key=slug,
            event_number=10,
        )
        for i in range(count)
    ]


def _summary(slug: str, *, finishers: int | None, volunteers: int | None) -> CanonicalEventSummary:
    return CanonicalEventSummary(
        external_event_key=f"{slug}:10:{DAY.isoformat()}",
        event_date=DAY,
        event_number=10,
        location_external_key=slug,
        location_name="Guard Park",
        finishers_count=finishers,
        volunteers_count=volunteers,
        source_url=f"https://5verst.ru/{slug}/results/{DAY.strftime('%d.%m.%Y')}/",
        summary_hash="h1",
    )


def _counts(db: Session, event_id) -> tuple[int, int]:
    return (
        db.query(RunResult).filter(RunResult.event_id == event_id).count(),
        db.query(VolunteerResult).filter(VolunteerResult.event_id == event_id).count(),
    )


# --- писатель -----------------------------------------------------------------


def test_replace_refuses_empty_list_when_rows_are_stored(db_session: Session) -> None:
    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard")
    summary = _summary(location.external_key, finishers=3, volunteers=None)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)
    event = upsert.upsert_event_for_summary(db_session, platform, location, summary, summary_row)
    upsert.replace_event_run_results(db_session, event, platform, _runs(location.external_key, 3))
    assert _counts(db_session, event.id)[0] == 3

    with pytest.raises(SuspectEmptyProtocolError):
        upsert.replace_event_run_results(db_session, event, platform, [])
    assert _counts(db_session, event.id)[0] == 3

    # Источник не знает, сколько было (None), но в базе строки есть — тоже отказ.
    with pytest.raises(SuspectEmptyProtocolError):
        upsert.replace_event_run_results(db_session, event, platform, [], expected_count=None)

    # Явный ноль от источника — старт действительно пустой, стирать можно.
    upsert.replace_event_run_results(db_session, event, platform, [], expected_count=0)
    assert _counts(db_session, event.id)[0] == 0


def test_replace_refuses_empty_list_when_summary_promises_rows(db_session: Session) -> None:
    """Строк ещё нет, но саммари обещает финишёров: «0 строк — ок» не пишем."""
    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard")
    summary = _summary(location.external_key, finishers=5, volunteers=None)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)
    event = upsert.upsert_event_for_summary(db_session, platform, location, summary, summary_row)

    with pytest.raises(SuspectEmptyProtocolError):
        upsert.replace_event_run_results(db_session, event, platform, [], expected_count=5)
    # Пустой старт без обещаний — нормальный случай (новая площадка без строк).
    assert upsert.replace_event_run_results(db_session, event, platform, []) == 0


def test_replace_volunteers_allows_empty_when_nothing_is_stored(db_session: Session) -> None:
    """Саммари обещает волонтёров, а в базе их ещё нет: стирать нечего.

    Отказ здесь откатил бы вместе с волонтёрами уже разобранных финишёров
    (пишутся раньше в той же транзакции), и субботний старт остался бы без
    результатов. Расхождение с саммари подхватит reconcile — volunteers_mismatch.
    """
    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard")
    summary = _summary(location.external_key, finishers=1, volunteers=2)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)
    event = upsert.upsert_event_for_summary(db_session, platform, location, summary, summary_row)

    assert upsert.replace_event_volunteer_results(db_session, event, platform, [], expected_count=2) == 0
    assert _counts(db_session, event.id)[1] == 0

    # Но как только строки появились, пустой список снова подозрителен.
    upsert.replace_event_volunteer_results(db_session, event, platform, _vols(location.external_key, 2))
    with pytest.raises(SuspectEmptyProtocolError):
        upsert.replace_event_volunteer_results(db_session, event, platform, [], expected_count=2)
    assert _counts(db_session, event.id)[1] == 2


def test_replace_volunteers_trusts_caller_when_allowed(db_session: Session) -> None:
    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard")
    summary = _summary(location.external_key, finishers=1, volunteers=2)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)
    event = upsert.upsert_event_for_summary(db_session, platform, location, summary, summary_row)
    upsert.replace_event_volunteer_results(db_session, event, platform, _vols(location.external_key, 2))

    with pytest.raises(SuspectEmptyProtocolError):
        upsert.replace_event_volunteer_results(db_session, event, platform, [], expected_count=2)
    assert _counts(db_session, event.id)[1] == 2

    upsert.replace_event_volunteer_results(db_session, event, platform, [], allow_empty=True)
    assert _counts(db_session, event.id)[1] == 0


# --- 5 вёрст --------------------------------------------------------------------


def test_five_verst_empty_page_keeps_protocol_and_hash(db_session: Session) -> None:
    """Страница техработ: 50+5 → 0/0 больше не случается, хэш не двигается."""
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
    from app.sync.protocol_content_hash import protocol_content_hash

    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard5v")
    slug = location.external_key
    summary = _summary(slug, finishers=4, volunteers=2)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)

    good_html = "<html>protocol</html>"
    with patch(
        "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
        return_value=(_runs(slug, 4), _vols(slug, 2), good_html),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)
    db_session.commit()

    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    assert _counts(db_session, event.id) == (4, 2)

    with (
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
            return_value=([], [], "<html><body>maintenance</body></html>"),
        ),
        pytest.raises(SuspectEmptyProtocolError),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)
    db_session.rollback()

    assert _counts(db_session, event.id) == (4, 2)
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    # Хеш — по разобранным строкам, а не по HTML (SYNC-5V-02, 21.09.2026).
    assert state.protocol_source_hash == protocol_content_hash(_runs(slug, 4), _vols(slug, 2))
    assert state.run_results_count == 4


def test_five_verst_volunteers_section_missing_is_suspect(db_session: Session) -> None:
    """Результаты разобрались, а «Команда организаторов» — нет: саммари знает,
    что волонтёры были, значит это вёрстка, а не пустой список."""
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol

    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guard5v")
    slug = location.external_key
    summary = _summary(slug, finishers=2, volunteers=2)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)

    with patch(
        "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
        return_value=(_runs(slug, 2), _vols(slug, 2), "<html>a</html>"),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)
    db_session.commit()

    with (
        patch(
            "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
            return_value=(_runs(slug, 2), [], "<html>b</html>"),
        ),
        pytest.raises(SuspectEmptyProtocolError),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)
    db_session.rollback()

    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    assert _counts(db_session, event.id) == (2, 2)


def test_week_sweep_records_summary_error_and_moves_check_mark(db_session: Session) -> None:
    """Вызывающий цикл: ошибка в отчёте, саммари в error, отметка проверки
    сдвинута, строки и хэш на месте."""
    from app.sync.five_verst_protocol import fetch_and_upsert_event_protocol
    from app.sync.five_verst_week_sweep import WeekSweepOptions, sweep_week_protocols

    platform = _platform(db_session, "five_verst")
    location = _location(db_session, platform, "guardsweep")
    slug = location.external_key
    summary = _summary(slug, finishers=3, volunteers=1)
    summary_row, _ = upsert.upsert_event_summary(db_session, platform, location, summary)
    with patch(
        "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
        return_value=(_runs(slug, 3), _vols(slug, 1), "<html>a</html>"),
    ):
        fetch_and_upsert_event_protocol(db_session, platform, location, summary, summary_row)
    db_session.commit()
    event = db_session.query(Event).filter(Event.external_event_key == summary.external_event_key).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    state.last_protocol_check_at = datetime(2020, 1, 1)
    db_session.commit()

    with patch(
        "app.sync.five_verst_protocol.bulk_parser.fetch_event_protocol",
        return_value=([], [], "<html>maintenance</html>"),
    ):
        result = sweep_week_protocols(
            db_session,
            WeekSweepOptions(weeks_back=0, limit=5, min_refetch_interval_hours=0, today=DAY),
        )

    assert result.protocols_fetched == 0
    assert len(result.errors) == 1 and "0 строк" in result.errors[0]
    assert _counts(db_session, event.id) == (3, 1)
    db_session.refresh(summary_row)
    assert summary_row.sync_status == SyncStatus.error
    db_session.refresh(state)
    assert state.last_protocol_check_at.year > 2020
    assert state.run_results_count == 3


# --- S95 ------------------------------------------------------------------------


def _s95_activity(results: list[dict] | None, volunteers: list[dict] | None) -> dict:
    payload: dict = {"date": DAY.strftime("%d.%m.%Y"), "event": {"name": "Guard", "code_name": "guard"}}
    if results is not None:
        payload["results"] = results
    if volunteers is not None:
        payload["volunteers"] = volunteers
    return payload


def test_s95_activity_without_results_keeps_protocol(db_session: Session) -> None:
    from app.s95.api_client import S95ApiActivityRef
    from app.sync.s95_protocol_api import upsert_activity_protocol_api

    platform = _platform(db_session, "s95")
    location = _location(db_session, platform, "guards95")
    ref = S95ApiActivityRef(date=DAY.isoformat(), url="https://s95.ru/activities/777777.json")
    full = _s95_activity(
        [
            {"total_time": "24:27", "position": 1, "athlete": {"id": 915512, "name": "Анна ТЕСТ"}},
            {"total_time": "25:00", "position": 2, "athlete": {"id": 915513, "name": "Иван ТЕСТ"}},
        ],
        [{"role": "timer", "athlete": {"id": 915514, "name": "Пётр ТЕСТ"}}],
    )
    first = upsert_activity_protocol_api(db_session, platform, location, ref, activity_json=full)
    db_session.commit()
    assert first.run_results_count == 2

    event = db_session.query(Event).filter(Event.external_event_key == first.external_event_key).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    hash_before = state.protocol_source_hash

    for payload in (_s95_activity(None, None), _s95_activity([], []), _s95_activity(None, [])):
        with pytest.raises(SuspectEmptyProtocolError):
            upsert_activity_protocol_api(db_session, platform, location, ref, activity_json=payload)
        db_session.rollback()
        assert _counts(db_session, event.id) == (2, 1)

    db_session.refresh(state)
    assert state.protocol_source_hash == hash_before
    assert state.run_results_count == 2

    # Волонтёров убрали, результаты на месте — ответ настоящий, принимаем.
    edited = _s95_activity(full["results"], [])
    upsert_activity_protocol_api(db_session, platform, location, ref, activity_json=edited)
    db_session.flush()
    assert _counts(db_session, event.id) == (2, 0)


# --- RunPark --------------------------------------------------------------------

EVENT_ID = "AAAA1111-2222-3333-4444-5555666677AA"
LOCATION_ID = "BBBB1111-2222-3333-4444-5555666677AA"
RESULT_ID = "CCCC1111-2222-3333-4444-5555666677AA"
PARTICIPANT_ID = "DDDD1111-2222-3333-4444-5555666677AA"


@pytest.fixture
def runpark_location(db_session: Session) -> Location:
    platform = _platform(db_session, "runpark")
    location = _location(db_session, platform, "guardrunpark")
    db_session.add(
        RunparkLocationMapping(
            runpark_location_id=LOCATION_ID,
            runpark_name="Guard Park",
            decision="load_history",
            show_on_map=True,
            runpark_location_row_id=location.id,
            source_batch="test",
        )
    )
    db_session.flush()
    return location


def _runpark_rows(run_rows: list[dict], *, finishers_count: int | None) -> dict[str, list[dict]]:
    event_date = datetime(2027, 3, 13, 9, 0)
    return {
        "vw_events": [
            {
                "event_id": EVENT_ID,
                "location_id": LOCATION_ID,
                "event_date": event_date,
                "event_number": 12,
                "is_test_event": False,
                "finishers_count": finishers_count,
            }
        ],
        "vw_run_results": run_rows,
        "vw_volunteer_results": [],
    }


def _runpark_run_row() -> dict:
    return {
        "result_id": RESULT_ID,
        "event_id": EVENT_ID,
        "event_date": datetime(2027, 3, 13, 9, 0),
        "participant_id": PARTICIPANT_ID,
        "participant_name": "Guard Runner",
        "barcode_id": "A98765",
        "position": 1,
        "finish_time_sec": 1300,
        "finish_time_display": "00:21:40",
        "age_category": "М30-34",
        "status": "finished",
        "is_pr": False,
    }


def _patched_runpark(rows: dict[str, list[dict]]):
    def fake_query(sql: str, params: tuple = ()) -> list[dict]:
        if "SELECT DISTINCT event_id" in sql:
            # Список стартов участника — только там, где он бежал.
            return [{"event_id": EVENT_ID}] if "vw_run_results" in sql else []
        for view, payload in rows.items():
            if view in sql:
                return payload
        raise AssertionError(f"Unexpected runpark query: {sql}")

    return patch("app.sync.runpark_global_sync.runpark_query", side_effect=fake_query)


def _runpark_event(db_session: Session) -> Event:
    return db_session.query(Event).filter(Event.external_event_key == EVENT_ID).one()


def test_runpark_batch_empty_view_keeps_rows(db_session: Session, runpark_location: Location) -> None:
    from app.sync.runpark_global_sync import sync_runpark_batch

    with _patched_runpark(_runpark_rows([_runpark_run_row()], finishers_count=1)):
        first = sync_runpark_batch(db_session, date(2027, 3, 6))
    assert first.errors == [] and first.events_upserted == 1
    event = _runpark_event(db_session)
    row_before = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    state = db_session.query(ProtocolSyncState).filter(ProtocolSyncState.event_id == event.id).one()
    hash_before = state.protocol_source_hash

    with _patched_runpark(_runpark_rows([], finishers_count=1)):
        second = sync_runpark_batch(db_session, date(2027, 3, 6))
    assert second.events_upserted == 0
    assert len(second.errors) == 1 and "0 строк" in second.errors[0]
    row_after = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert row_after.id == row_before.id
    db_session.refresh(state)
    assert state.protocol_source_hash == hash_before

    # vw_events честно говорит «0 финишёров» — старт пустой, стирать можно.
    with _patched_runpark(_runpark_rows([], finishers_count=0)):
        third = sync_runpark_batch(db_session, date(2027, 3, 6))
    assert third.errors == [] and third.events_upserted == 1
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).count() == 0


def test_runpark_participant_sync_updates_in_place_and_refuses_empty(
    db_session: Session, runpark_location: Location
) -> None:
    """Синк профиля: строка обновляется на месте (id сохраняется — на нём
    висят оценки), неизменное пропускается, пустая выборка не стирает."""
    from app.sync.runpark_global_sync import sync_runpark_for_participant

    with _patched_runpark(_runpark_rows([_runpark_run_row()], finishers_count=1)):
        first = sync_runpark_for_participant(db_session, PARTICIPANT_ID)
    assert first.errors == [] and first.events_upserted == 1
    event = _runpark_event(db_session)
    row_before = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()

    with _patched_runpark(_runpark_rows([_runpark_run_row()], finishers_count=1)):
        second = sync_runpark_for_participant(db_session, PARTICIPANT_ID)
    assert second.events_unchanged == 1 and second.events_upserted == 0

    faster = dict(_runpark_run_row(), finish_time_sec=1250, finish_time_display="00:20:50")
    with _patched_runpark(_runpark_rows([faster], finishers_count=1)):
        third = sync_runpark_for_participant(db_session, PARTICIPANT_ID)
    assert third.events_upserted == 1
    row_after = db_session.query(RunResult).filter(RunResult.event_id == event.id).one()
    assert row_after.id == row_before.id
    assert row_after.finish_time_sec == 1250

    with _patched_runpark(_runpark_rows([], finishers_count=1)):
        fourth = sync_runpark_for_participant(db_session, PARTICIPANT_ID)
    assert len(fourth.errors) == 1 and "0 строк" in fourth.errors[0]
    assert db_session.query(RunResult).filter(RunResult.event_id == event.id).one().id == row_before.id
