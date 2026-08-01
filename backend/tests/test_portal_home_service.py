from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.models import Participant, Platform, PlatformLink, User
from app.services.portal_home_service import (
    PORTAL_HOME_CACHE_KEY,
    _EventRow,
    _read_portal_home_cache,
    _runner_handles,
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
        lambda location_id: f"slug-{location_id}",
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
