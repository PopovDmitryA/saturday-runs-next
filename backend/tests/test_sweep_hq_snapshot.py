"""Нарезка окна из полной истории темпа обхода."""

from __future__ import annotations

from app.services.sweep_hq_snapshot_service import slice_hours


def _payload(now_hour: str, hours: list[str]) -> dict:
    return {"now_hour": now_hour,
            "hours": [{"hour": h, "collected": i} for i, h in enumerate(hours)]}


HOURS = [
    "2026-08-24T10:00:00Z",
    "2026-08-24T11:00:00Z",
    "2026-08-24T12:00:00Z",
    "2026-08-24T13:00:00Z",
]


def test_zero_hours_returns_everything() -> None:
    data = slice_hours(_payload("2026-08-24T13:00:00Z", HOURS), 0)
    assert [r["hour"] for r in data["hours"]] == HOURS


def test_window_counted_from_snapshot_now_not_local_clock() -> None:
    """Метки часов — в поясе БД, поэтому окно отсчитываем от now_hour снимка.

    Если бы отсчёт шёл от UTC этой машины, при другом поясе базы окно уехало бы
    на разницу поясов и график терял или добавлял часы.
    """
    data = slice_hours(_payload("2026-08-24T13:00:00Z", HOURS), 2)
    assert [r["hour"] for r in data["hours"]] == HOURS[2:]


def test_window_wider_than_data_keeps_all() -> None:
    data = slice_hours(_payload("2026-08-24T13:00:00Z", HOURS), 999)
    assert len(data["hours"]) == len(HOURS)


def test_snapshot_without_now_hour_is_returned_as_is() -> None:
    """Снимок старого образца (до появления now_hour) не должен ломать страницу."""
    data = slice_hours({"hours": [{"hour": HOURS[0], "collected": 1}]}, 24)
    assert len(data["hours"]) == 1
