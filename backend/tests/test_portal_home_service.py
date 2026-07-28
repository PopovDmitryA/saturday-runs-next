from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.services.portal_home_service import (
    PORTAL_HOME_CACHE_KEY,
    _EventRow,
    _read_portal_home_cache,
    _week_attendance_records,
    _write_portal_home_cache,
    clean_time_display,
    format_finish_time,
    invalidate_portal_home_cache,
)

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


def _attendance(events: list[_EventRow]) -> list[dict]:
    return _week_attendance_records(
        events,
        WEEK_START,
        WEEK_END,
        lambda location_id: str(location_id),
        lambda location_id: f"loc-{location_id}",
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
