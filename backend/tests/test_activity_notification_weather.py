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


def test_bold_link_and_plain_render() -> None:
    from app.notification_markup import to_plain, to_telegram_html
    from app.services.activity_notification_service import bold_link

    assert bold_link("Челленджи:", None) == "**Челленджи:**"
    # «]» в подписи сломал бы разбор ссылки — остаётся просто жирным.
    assert bold_link("a]b", "https://x.test") == "**a]b**"
    text = "🏆 " + bold_link("Челленджи:", "https://x.test/users/7/achievements")
    assert to_telegram_html(text) == '🏆 <a href="https://x.test/users/7/achievements"><b>Челленджи:</b></a>'
    # VK: подпись в строке, адрес — строкой ниже.
    assert to_plain(text) == "🏆 Челленджи:\nhttps://x.test/users/7/achievements"
