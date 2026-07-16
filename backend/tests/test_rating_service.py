"""Юнит-тесты чистой логики rating_service (без БД)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services.rating_service import (
    RATING_EDIT_WINDOW_DAYS,
    _is_editable,
    _select_legacy_entries,
)


def _entry(
    entry_id: str = "run:1",
    event_date: date = date(2024, 5, 4),
    platform_code: str = "five_verst",
    identity: str = "fili",
) -> dict[str, object]:
    return {
        "entry_id": entry_id,
        "event_date": event_date,
        "platform_code": platform_code,
        "_identity": identity,
    }


def test_legacy_picks_latest_start_per_location() -> None:
    # Пять пробежек в Филях за пределами месяца — оценить даём только последнюю.
    older = [
        _entry(entry_id=f"run:{i}", event_date=date(2024, 5, 4) + timedelta(days=7 * i))
        for i in range(5)
    ]
    selected = _select_legacy_entries(older, set())
    assert [e["entry_id"] for e in selected] == ["run:4"]


def test_legacy_gives_one_slot_per_unique_location() -> None:
    older = [
        _entry(entry_id="run:fili", identity="fili"),
        _entry(entry_id="run:kuzminki", identity="kuzminki"),
        _entry(entry_id="run:sokolniki", identity="sokolniki"),
    ]
    selected = _select_legacy_entries(older, set())
    assert {e["entry_id"] for e in selected} == {"run:fili", "run:kuzminki", "run:sokolniki"}


def test_legacy_skips_already_rated_location() -> None:
    # Локация уже оценена — второго шанса по истории не даём, иначе слот
    # возрождался бы каждый раз, когда очередной старт выпадает из окна.
    older = [
        _entry(entry_id="run:fili", identity="fili"),
        _entry(entry_id="run:kuzminki", identity="kuzminki"),
    ]
    selected = _select_legacy_entries(older, {"fili"})
    assert [e["entry_id"] for e in selected] == ["run:kuzminki"]


def test_legacy_excludes_parkrun() -> None:
    older = [
        _entry(entry_id="run:parkrun", platform_code="parkrun", identity="bushy"),
        _entry(entry_id="run:fili", platform_code="five_verst", identity="fili"),
    ]
    selected = _select_legacy_entries(older, set())
    assert [e["entry_id"] for e in selected] == ["run:fili"]


def test_legacy_parkrun_does_not_shadow_older_non_parkrun_start() -> None:
    # На одной площадке был и parkrun (свежее), и 5 вёрст (старее). parkrun в
    # добор не входит и не должен «занимать» слот локации — оцениваем 5 вёрст.
    older = [
        _entry(
            entry_id="run:parkrun",
            platform_code="parkrun",
            event_date=date(2024, 6, 1),
            identity="fili",
        ),
        _entry(
            entry_id="run:five_verst",
            platform_code="five_verst",
            event_date=date(2024, 1, 1),
            identity="fili",
        ),
    ]
    selected = _select_legacy_entries(older, set())
    assert [e["entry_id"] for e in selected] == ["run:five_verst"]


def test_legacy_empty_when_no_old_starts() -> None:
    assert _select_legacy_entries([], set()) == []


def test_editable_within_event_window() -> None:
    today = date(2026, 7, 17)
    assert _is_editable(today - timedelta(days=1), today) is True
    assert _is_editable(today - timedelta(days=RATING_EDIT_WINDOW_DAYS), today) is True
    assert _is_editable(today - timedelta(days=RATING_EDIT_WINDOW_DAYS + 1), today) is False


def test_legacy_rating_editable_from_its_creation_date() -> None:
    # Старт двухлетней давности из добора: без учёта даты оценки он фиксировался
    # бы в ту же секунду, что и поставлен, — опечатку не исправить.
    today = date(2026, 7, 17)
    old_event = date(2024, 5, 4)
    just_rated = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    assert _is_editable(old_event, today, just_rated) is True

    long_ago = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    assert _is_editable(old_event, today, long_ago) is False
