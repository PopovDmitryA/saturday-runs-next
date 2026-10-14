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


def test_renamed_location_does_not_resurrect_milestones() -> None:
    """Раменское Городской парк → Раменское: старые вехи не должны стать «новыми»."""
    from app.services.activity_notification_service import milestone_key, new_milestones

    old = {
        "kind": "volunteer_club",
        "event_date": "2026-04-04",
        "platform_code": "five_verst",
        "location_name": "Раменское Городской парк",
        "number": 100,
    }
    renamed = {**old, "location_name": "Раменское"}
    assert new_milestones([milestone_key(old)], [milestone_key(renamed)], [renamed]) == []


def test_old_milestones_are_not_announced() -> None:
    """Новый ключ, но веха полугодовой давности — пересчёт истории, не новость."""
    from app.services.activity_notification_service import milestone_key, new_milestones

    old = {"kind": "run_club", "event_date": "2026-03-07", "number": 100}
    today = {"kind": "saturday_volunteer_streak", "event_date": date(2026, 9, 26), "number": 110}
    items = [old, today]
    fresh = new_milestones([], [milestone_key(i) for i in items], items, since=date(2026, 9, 12))
    assert fresh == [today]
    # Первый снимок по-прежнему молчит.
    assert new_milestones(None, [milestone_key(today)], [today], since=date(2026, 9, 12)) == []


def test_quiet_snapshot_refresh_hides_only_backfilled_challenge(db_session, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Дозаливка покрова не приходит новостью, а настоящий прогресс — приходит.

    Решение Дмитрия 14.10.2026: вернувшиеся клетки «По снегу» за прошлые зимы
    двигаем в снимке молча, остальные челленджи ждут своего сообщения.
    """
    from app.models import User, UserNotificationPrefs
    from app.services import activity_notification_service as ans

    payload = {
        "challenges": [
            {"code": "all_weather", "current": 6, "tiers": [{"tier": "solo", "level": "silver"}]},
            {"code": "rain_runner", "current": 4, "tiers": [{"tier": "solo", "level": "bronze"}]},
        ]
    }
    monkeypatch.setattr(ans, "compute_challenges", lambda db, user_id: payload)
    user = User(display_name=f"Снежный {uuid4().hex[:6]}")
    fresh = User(display_name=f"Без снимка {uuid4().hex[:6]}")
    db_session.add_all([user, fresh])
    db_session.flush()
    db_session.add_all(
        [
            UserNotificationPrefs(
                user_id=user.id,
                challenge_levels={"all_weather": {"solo": "bronze"}, "rain_runner": {"solo": None}},
                challenge_counts={"all_weather": 3, "rain_runner": 2},
            ),
            UserNotificationPrefs(user_id=fresh.id),
        ]
    )
    db_session.flush()

    assert ans.refresh_challenge_snapshots_quietly(db_session, user.id, {"all_weather"})
    prefs = db_session.get(UserNotificationPrefs, user.id)
    assert prefs.challenge_levels == {"all_weather": {"solo": "silver"}, "rain_runner": {"solo": None}}
    assert prefs.challenge_counts == {"all_weather": 6, "rain_runner": 2}
    # Ближайшее сообщение: «Всепогодного» в нём нет, «Под дождём» — есть.
    current_levels = ans.challenge_levels_snapshot(payload)
    assert [u.code for u in ans.level_ups(prefs.challenge_levels, current_levels, payload)] == ["rain_runner"]
    progress = ans.challenge_progress(
        prefs.challenge_counts, ans.challenge_counts_snapshot(payload), payload, exclude=set()
    )
    assert [p.code for p in progress] == ["rain_runner"]

    # Снимка ещё нет — первый скан и так молчит, трогать нечего.
    assert not ans.refresh_challenge_snapshots_quietly(db_session, fresh.id, {"all_weather"})
    assert db_session.get(UserNotificationPrefs, fresh.id).challenge_levels is None
