"""Погода в уведомлении «пробежка попала на сайт»."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.services.activity_notification_service import NewRun, run_block


def _run(weather: str | None) -> NewRun:
    return NewRun(
        result_id=uuid4(),
        event_date=date(2026, 9, 26),
        location_name="Мещерский",
        platform_code="five_verst",
        event_number=233,
        finish_time_sec=1500,
        position=12,
        is_pr=False,
        is_first_run_at_location=False,
        weather=weather,
    )


def test_run_block_puts_weather_under_the_result() -> None:
    lines = run_block(_run("🌤️ +12°, малооблачно, ветер 3 м/с")).split("\n")
    assert "№233" in lines[0]
    assert lines[1].startswith("⏱")
    assert lines[2] == "🌤️ +12°, малооблачно, ветер 3 м/с"


def test_run_block_without_weather_keeps_two_lines() -> None:
    assert len(run_block(_run(None)).split("\n")) == 2
