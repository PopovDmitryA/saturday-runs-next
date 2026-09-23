"""Уведомления об отмене ближайшего старта: кому уходят и что в них написано."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Event,
    Location,
    NotificationDelivery,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
    VolunteerResult,
)
from app.services import cancellation_notification_service as cancellations
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services.location_cancellation_notify import CancellationChange
from app.services.notification_channels_service import CheckOutcome


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    queued: list[UUID] = []
    monkeypatch.setattr(notify, "enqueue_delivery", lambda delivery_id: queued.append(delivery_id) or True)
    return queued


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        app_secret_key="test-secret-key",
        app_base_url="https://run5k.test",
        telegram_bot_token="t",
        telegram_bot_username="test_bot",
    )
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    monkeypatch.setattr(cancellations, "get_settings", lambda: settings)
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=True))
    return settings


def _platform(db: Session, code: str, name: str) -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=name, base_url=f"https://{code}.test", is_active=True)
        db.add(platform)
        db.flush()
    return platform


def _location(db: Session, platform: Platform, *, slug: str, name: str) -> Location:
    location = Location(platform_id=platform.id, external_key=slug, name=name)
    db.add(location)
    db.flush()
    return location


def _user(db: Session, *, name: str, chat_id: int) -> User:
    user = User(display_name=name, telegram_id=chat_id, telegram_chat_id=chat_id)
    db.add(user)
    db.commit()
    notify.set_channel_enabled(db, user, "telegram", True)
    db.commit()
    return user


def _ran_at(
    db: Session, user: User, location: Location, platform: Platform, *, when: date, volunteer: bool = False
) -> None:
    external = f"9{user.serial_id:011d}"
    participant = db.query(Participant).filter_by(platform_id=platform.id, external_user_id=external).one_or_none()
    if participant is None:
        participant = Participant(platform_id=platform.id, external_user_id=external, display_name=user.display_name)
        db.add(participant)
        db.flush()
        db.add(
            PlatformLink(
                user_id=user.id,
                platform_id=platform.id,
                external_user_id=external,
                external_url=f"https://5verst.ru/userstats/{external}/",
            )
        )
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"ev-{uuid4().hex[:8]}",
        event_date=when,
        event_number=42,
    )
    db.add(event)
    db.flush()
    if volunteer:
        db.add(
            VolunteerResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"v-{uuid4().hex[:8]}",
                role="Маршал",
            )
        )
    else:
        db.add(
            RunResult(
                event_id=event.id,
                participant_id=participant.id,
                external_result_key=f"r-{uuid4().hex[:8]}",
                position=7,
                finish_time_sec=1500,
            )
        )
    db.commit()


def _change(
    slug: str, *, name: str, cancelled: bool = True, platform: str = "five_verst", reason: str | None = None
) -> CancellationChange:
    return CancellationChange(platform_code=platform, slug=slug, name=name, cancelled=cancelled, reason=reason)


# ---------------------------------------------------------------------------
# Текст


def test_upcoming_saturday_rolls_over_on_sunday() -> None:
    assert cancellations.upcoming_saturday(date(2026, 9, 23)) == date(2026, 9, 26)  # среда
    assert cancellations.upcoming_saturday(date(2026, 9, 26)) == date(2026, 9, 26)  # сама суббота
    assert cancellations.upcoming_saturday(date(2026, 9, 27)) == date(2026, 10, 3)  # воскресенье


def test_compose_single_cancellation() -> None:
    title, text = cancellations.compose(
        [_change("meshcherskiy", name="Мещерский", reason="Работы в парке")],
        today=date(2026, 9, 23),
    )
    # Состояние сказано один раз — в заголовке; строка площадки его не повторяет.
    assert title == "🚫 Отмена старта 26.09"
    assert text.startswith("📍 **Мещерский** · 5 вёрст\nРаботы в парке")
    assert "отменён" not in text
    # Ссылка на площадку уходит кнопкой уведомления, в теле её нет.
    assert "[" not in text


def test_compose_batch_has_no_counter_and_marks_each_location() -> None:
    title, text = cancellations.compose(
        [_change("a", name="Лихославль"), _change("b", name="Иваново", platform="s95")],
        today=date(2026, 9, 23),
    )
    assert title == "🚫 Отмены стартов 26.09"
    assert text.startswith("📍 **Лихославль** · 5 вёрст\n\n📍 **Иваново** · С95")
    assert "[" not in text


def test_compose_mixed_marks_state_per_location() -> None:
    title, text = cancellations.compose(
        [_change("a", name="Лихославль"), _change("c", name="Серов", cancelled=False)],
        today=date(2026, 9, 23),
    )
    assert title == "🚫 Изменения по отменам стартов 26.09"
    assert "🚫 **Лихославль** · 5 вёрст" in text
    assert "✅ **Серов** · 5 вёрст" in text
    assert "[" not in text


def test_compose_restored_only() -> None:
    title, text = cancellations.compose([_change("a", name="Первый", cancelled=False)], today=date(2026, 9, 23))
    assert title == "✅ Отмена снята"
    assert text.startswith("📍 **Первый** · 5 вёрст")

    title, _ = cancellations.compose(
        [_change("a", name="Первый", cancelled=False), _change("b", name="Второй", cancelled=False)],
        today=date(2026, 9, 23),
    )
    assert title == "✅ Отмены сняты"


# ---------------------------------------------------------------------------
# Рассылка


def test_notify_goes_to_every_subscriber_regardless_of_location(db_session: Session, _no_broker: list[UUID]) -> None:
    """Отмены рассылаются по всей стране: бегал человек там или нет — неважно."""
    platform = _platform(db_session, "five_verst", "5 вёрст")
    far_away = _location(db_session, platform, slug="vladivostok", name="Владивосток")
    local = _user(db_session, name="Москвич", chat_id=111)
    _ran_at(
        db_session,
        local,
        _location(db_session, platform, slug="meshcherskiy", name="Мещерский"),
        platform,
        when=date.today() - timedelta(days=14),
    )
    newcomer = _user(db_session, name="Новичок без пробежек", chat_id=112)
    silent = User(display_name="Без уведомлений", telegram_id=113, telegram_chat_id=113)
    db_session.add(silent)
    db_session.commit()

    change = _change(far_away.external_key, name="Владивосток", reason="Штормовое предупреждение")
    assert cancellations.notify_cancellation_subscribers(db_session, [change]) == 2
    rows = db_session.query(NotificationDelivery).filter_by(kind="cancellations").all()
    assert {row.user_id for row in rows} == {local.id, newcomer.id}
    assert rows[0].payload["title"].startswith("🚫 Отмена старта ")
    assert rows[0].payload["url"].endswith("/locations/vladivostok")
    assert set(_no_broker) == {row.id for row in rows}


def test_batch_is_one_message_and_dedupes(db_session: Session) -> None:
    _platform(db_session, "five_verst", "5 вёрст")
    user = _user(db_session, name="Подписчик", chat_id=121)
    batch = [_change("a", name="Первый"), _change("b", name="Второй")]

    assert cancellations.notify_cancellation_subscribers(db_session, batch) == 1
    row = db_session.query(NotificationDelivery).filter_by(user_id=user.id, kind="cancellations").one()
    day = cancellations.upcoming_saturday(date.today()).strftime("%d.%m")
    assert row.payload["title"] == f"🚫 Отмены стартов {day}"
    assert "Первый" in row.payload["text"] and "Второй" in row.payload["text"]

    # Тот же набор на той же неделе (реестр и наблюдатель видят его по очереди).
    assert cancellations.notify_cancellation_subscribers(db_session, batch) == 0
    # Другой набор — новое сообщение.
    assert cancellations.notify_cancellation_subscribers(db_session, [_change("c", name="Третий")]) == 1


def test_platform_filter_and_kind_toggle(db_session: Session) -> None:
    _platform(db_session, "five_verst", "5 вёрст")
    _platform(db_session, "s95", "S95")
    user = _user(db_session, name="Только 5 вёрст", chat_id=131)
    notify.update_prefs(db_session, user.id, cancellation_platforms=["five_verst"])
    db_session.commit()

    # Из смешанной пачки в сообщение попадает только своя система.
    mixed = [_change("park-5v", name="Парк 5в"), _change("park-s95", name="Парк S95", platform="s95")]
    assert cancellations.notify_cancellation_subscribers(db_session, mixed) == 1
    row = db_session.query(NotificationDelivery).filter_by(user_id=user.id, kind="cancellations").one()
    assert "Парк 5в" in row.payload["text"] and "Парк S95" not in row.payload["text"]

    # Только чужая система — молчим.
    assert (
        cancellations.notify_cancellation_subscribers(
            db_session, [_change("other-s95", name="Другой S95", platform="s95")]
        )
        == 0
    )

    # Пустой список систем = все.
    notify.update_prefs(db_session, user.id, cancellation_platforms=[])
    db_session.commit()
    assert (
        cancellations.notify_cancellation_subscribers(
            db_session, [_change("other-s95", name="Другой S95", platform="s95")]
        )
        == 1
    )

    # Вид выключен — молчим.
    notify.update_prefs(db_session, user.id, kinds={"cancellations": False})
    db_session.commit()
    assert (
        cancellations.notify_cancellation_subscribers(db_session, [_change("park-5v", name="Парк 5в", cancelled=False)])
        == 0
    )


def test_update_prefs_rejects_unknown_platform(db_session: Session) -> None:
    user = _user(db_session, name="Тест", chat_id=141)
    with pytest.raises(ValueError):
        notify.update_prefs(db_session, user.id, cancellation_platforms=["strava"])


# ---------------------------------------------------------------------------
# Модалка «не можем написать»


def test_nudge_reports_broken_delivery(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _user(db_session, name="Заблокировал бота", chat_id=141)
    assert notify.nudge_state(db_session, user)["kind"] is None  # всё работает

    monkeypatch.setitem(
        channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=False, error="telegram 403")
    )
    row = channels.get_channel(db_session, user.id, "telegram")
    assert row is not None
    channels.ensure_checked(db_session, row, force=True)
    db_session.commit()

    state = notify.nudge_state(db_session, user)
    assert state["kind"] == "fix_delivery" and state["show"] is True
    assert [item["channel"] for item in state["broken"]] == ["telegram"]
    assert "Start" in state["broken"][0]["problem"]
    assert state["broken"][0]["action_url"]


def test_nudge_stays_quiet_when_one_channel_works(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import AuthIdentity, AuthProvider

    user = _user(db_session, name="Два канала", chat_id=151)
    db_session.add(
        AuthIdentity(user_id=user.id, provider=AuthProvider.email, external_id="a@example.com", email="a@example.com")
    )
    db_session.commit()
    notify.set_channel_enabled(db_session, user, "email", True)
    db_session.commit()

    monkeypatch.setitem(
        channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=False, error="telegram 403")
    )
    row = channels.get_channel(db_session, user.id, "telegram")
    assert row is not None
    channels.ensure_checked(db_session, row, force=True)
    db_session.commit()

    # Почта доставляет — тревожить незачем.
    assert notify.nudge_state(db_session, user)["kind"] is None


def test_nudge_enable_kind_for_fresh_user(db_session: Session) -> None:
    user = User(display_name="Новичок", telegram_id=161, telegram_chat_id=161)
    db_session.add(user)
    db_session.commit()
    state = notify.nudge_state(db_session, user)
    assert state["kind"] == "enable" and state["show"] is True

    notify.dismiss_nudge(db_session, user.id)
    db_session.commit()
    assert notify.nudge_state(db_session, user)["kind"] is None
