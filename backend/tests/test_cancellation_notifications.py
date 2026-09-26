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


@pytest.fixture(autouse=True)
def _mid_week(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Общие тесты рассылки не зависят от дня прогона: в субботу после 9:00 фильтр
    «старт уже прошёл» иначе съедал бы их отмены. Сам фильтр проверяют тесты
    с пометкой real_start_filter."""
    if "real_start_filter" in request.keywords:
        return
    monkeypatch.setattr(cancellations, "still_ahead", lambda db, changes, now: changes)


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
                participant_id=participant.id,
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
        base_url="https://run5k.test",
        today=date(2026, 9, 23),
    )
    # Состояние сказано один раз — в заголовке; строка площадки его не повторяет.
    assert title == "🚫 Отмена старта 26.09"
    assert text.startswith("📍 [**Мещерский**](https://run5k.test/locations/meshcherskiy) · 5 вёрст\nРаботы в парке")
    assert "отменён" not in text


def test_compose_batch_has_no_counter_and_marks_each_location() -> None:
    title, text = cancellations.compose(
        [_change("a", name="Лихославль"), _change("b", name="Иваново", platform="s95")],
        base_url="https://run5k.test",
        today=date(2026, 9, 23),
    )
    assert title == "🚫 Отмены стартов 26.09"
    # Каждая площадка кликабельна своей ссылкой, общей внизу нет.
    assert text == (
        "📍 [**Лихославль**](https://run5k.test/locations/a) · 5 вёрст\n\n"
        "📍 [**Иваново**](https://run5k.test/locations/b) · С95"
    )


def test_compose_mixed_marks_state_per_location() -> None:
    title, text = cancellations.compose(
        [_change("a", name="Лихославль"), _change("c", name="Серов", cancelled=False)],
        base_url="https://run5k.test",
        today=date(2026, 9, 23),
    )
    assert title == "🚫 Изменения по отменам стартов 26.09"
    assert "🚫 [**Лихославль**](https://run5k.test/locations/a) · 5 вёрст" in text
    assert "✅ [**Серов**](https://run5k.test/locations/c) · 5 вёрст" in text


def test_compose_restored_only() -> None:
    title, text = cancellations.compose(
        [_change("a", name="Первый", cancelled=False)],
        base_url="https://run5k.test",
        today=date(2026, 9, 23),
    )
    assert title == "✅ Отмена снята"
    assert text.startswith("📍 [**Первый**](https://run5k.test/locations/a) · 5 вёрст")

    title, _ = cancellations.compose(
        [_change("a", name="Первый", cancelled=False), _change("b", name="Второй", cancelled=False)],
        base_url="https://run5k.test",
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
    assert rows[0].payload["url"] is None  # ссылка — в названии площадки
    assert "[**Владивосток**](" in rows[0].payload["text"]
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


def test_start_already_passed_by_local_time() -> None:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    moscow = ZoneInfo("Europe/Moscow")
    # Суббота 26.09.2026: до 9:00 старт впереди, после — прошёл.
    assert not cancellations.start_already_passed(moscow, None, datetime(2026, 9, 26, 5, 30, tzinfo=UTC))
    assert cancellations.start_already_passed(moscow, None, datetime(2026, 9, 26, 18, 0, tzinfo=UTC))
    # Пятница вечером — старт завтра, впереди.
    assert not cancellations.start_already_passed(moscow, None, datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    # Омск (+3 к Москве): 06:30 по Москве — там уже 9:30, старт прошёл.
    assert cancellations.start_already_passed(ZoneInfo("Asia/Omsk"), None, datetime(2026, 9, 26, 3, 30, tzinfo=UTC))
    # Расписание с поздним стартом: в 9:30 старт в 10:00 ещё впереди.
    late = [{"from_month": 1, "to_month": 12, "time": "10:00"}]
    assert not cancellations.start_already_passed(moscow, late, datetime(2026, 9, 26, 6, 30, tzinfo=UTC))


@pytest.mark.real_start_filter
def test_cancellation_after_start_is_not_sent(db_session: Session) -> None:
    """Лихославль 26.09.2026: отмена в 21:00 о прошедшей субботе людям не уходит."""
    from datetime import UTC, datetime

    _platform(db_session, "five_verst", "5 вёрст")
    _user(db_session, name="Подписчик", chat_id=151)
    db_session.commit()
    late = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)  # 21:00 МСК, суббота
    friday = datetime(2026, 9, 25, 18, 0, tzinfo=UTC)
    change = _change("likhoslavl-test", name="Лихославль", reason="все на Кроссе нации")
    assert cancellations.notify_cancellation_subscribers(db_session, [change], today=date(2026, 9, 26), now=late) == 0
    assert cancellations.notify_cancellation_subscribers(db_session, [change], today=date(2026, 9, 25), now=friday) == 1


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


# ---------------------------------------------------------------------------
# Пятничная сводка


def _cancelled(slug: str, name: str, *, platform: str = "five_verst", reason: str | None = None):
    return cancellations.CancelledLocation(platform_code=platform, slug=slug, name=name, reason=reason)


def test_summary_text_lists_cancellations_with_count() -> None:
    title, text = cancellations.compose_summary(
        [_cancelled("murom", "Муромец", reason="Кросс нации"), _cancelled("tobolsk", "Тобольск")],
        saturday=date(2026, 9, 26),
        base_url="https://run5k.test",
    )
    assert title == "🗓 Отмены стартов на завтра, 26.09"
    assert text == (
        "Завтра старта не будет на 2 локациях:\n\n"
        "📍 [**Муромец**](https://run5k.test/locations/murom) · 5 вёрст\nКросс нации\n\n"
        "📍 [**Тобольск**](https://run5k.test/locations/tobolsk) · 5 вёрст"
    )


def test_summary_text_without_cancellations_and_with_platform_filter() -> None:
    title, text = cancellations.compose_summary(
        [], saturday=date(2026, 9, 26), base_url="https://run5k.test", platforms=["s95"]
    )
    assert title == "✅ Отмен на завтра, 26.09, нет"
    assert text == "Ни одна локация не сообщила об отмене субботнего старта.\n\nПо системам: С95."


def test_summary_text_plurals_and_long_list_is_cut() -> None:
    one = cancellations.compose_summary([_cancelled("a", "А")], saturday=date(2026, 9, 26), base_url="u")[1]
    assert one.startswith("Завтра старта не будет на 1 локации:")
    many = [_cancelled(f"l{i}", f"Локация {i}") for i in range(cancellations.SUMMARY_MAX_LISTED + 3)]
    text = cancellations.compose_summary(many, saturday=date(2026, 9, 26), base_url="u")[1]
    assert text.startswith(f"Завтра старта не будет на {len(many)} локациях:")
    assert text.endswith("… и ещё 3")


def test_summary_due_only_friday_evening() -> None:
    from datetime import datetime

    assert cancellations.summary_due(datetime(2026, 9, 25, 21, 0))
    assert cancellations.summary_due(datetime(2026, 9, 25, 22, 59))
    assert not cancellations.summary_due(datetime(2026, 9, 25, 20, 59))
    assert not cancellations.summary_due(datetime(2026, 9, 25, 23, 0))
    assert not cancellations.summary_due(datetime(2026, 9, 24, 21, 0))


def test_friday_summary_goes_at_local_nine_pm_by_home_location(db_session: Session, _no_broker: list[UUID]) -> None:
    from datetime import UTC, datetime

    platform = _platform(db_session, "five_verst", "5 вёрст")
    ekb = _location(db_session, platform, slug=f"ekb-{uuid4().hex[:6]}", name="Екатеринбург")
    ekb.timezone = "Asia/Yekaterinburg"
    cancelled = _location(db_session, platform, slug=f"murom-{uuid4().hex[:6]}", name="Муромец")
    cancelled.is_cancelled = True
    cancelled.cancel_reason = "Кросс нации"
    db_session.flush()
    moscow_user = _user(db_session, name="Москвич", chat_id=501)  # дома нет — Москва
    ural_user = _user(db_session, name="Уралец", chat_id=502)
    _ran_at(db_session, ural_user, ekb, platform, when=date(2026, 9, 19))

    def summaries(user: User) -> list[NotificationDelivery]:
        return (
            db_session.query(NotificationDelivery)
            .filter(NotificationDelivery.user_id == user.id, NotificationDelivery.dedupe_key.like("cancel-friday:%"))
            .all()
        )

    # 16:00 UTC пятницы: в Екатеринбурге 21:00, в Москве 19:00.
    cancellations.send_friday_summaries(db_session, now=datetime(2026, 9, 25, 16, 0, tzinfo=UTC))
    assert len(summaries(ural_user)) == 1 and summaries(moscow_user) == []
    # 18:00 UTC: в Москве 21:00, в Екатеринбурге уже 23:00 — окно закрыто.
    cancellations.send_friday_summaries(db_session, now=datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    assert len(summaries(moscow_user)) == 1 and len(summaries(ural_user)) == 1
    # Повторный заход в том же окне ничего не шлёт.
    cancellations.send_friday_summaries(db_session, now=datetime(2026, 9, 25, 19, 0, tzinfo=UTC))
    assert len(summaries(moscow_user)) == 1

    delivery = summaries(moscow_user)[0]
    assert delivery.kind == "cancellations"
    assert delivery.dedupe_key == "cancel-friday:2026-09-26"
    assert delivery.payload["title"] == "🗓 Отмены стартов на завтра, 26.09"
    assert "Муромец" in delivery.payload["text"] and "Кросс нации" in delivery.payload["text"]


def test_friday_summary_respects_kind_and_platforms(db_session: Session) -> None:
    from datetime import UTC, datetime

    platform = _platform(db_session, "five_verst", "5 вёрст")
    cancelled = _location(db_session, platform, slug=f"murom-{uuid4().hex[:6]}", name="Муромец")
    cancelled.is_cancelled = True
    db_session.flush()
    off = _user(db_session, name="Выключил", chat_id=601)
    notify.update_prefs(db_session, off.id, kinds={"cancellations": False})
    s95_only = _user(db_session, name="Только С95", chat_id=602)
    notify.update_prefs(db_session, s95_only.id, cancellation_platforms=["s95"])
    db_session.commit()

    cancellations.send_friday_summaries(db_session, now=datetime(2026, 9, 25, 18, 0, tzinfo=UTC))
    rows = {
        row.user_id: row
        for row in db_session.query(NotificationDelivery).filter(
            NotificationDelivery.dedupe_key == "cancel-friday:2026-09-26",
            NotificationDelivery.user_id.in_([off.id, s95_only.id]),
        )
    }
    assert off.id not in rows
    # Отмена у 5 вёрст, а человек смотрит только С95 — для него отмен нет.
    assert rows[s95_only.id].payload["title"] == "✅ Отмен на завтра, 26.09, нет"
