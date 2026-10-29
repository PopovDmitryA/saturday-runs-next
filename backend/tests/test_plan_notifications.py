"""Уведомления планировщика: напоминание накануне по поясу локации и «старт отменён» —
два вида уведомлений со своими тумблерами."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Event,
    Location,
    NotificationDelivery,
    Platform,
    RunPlan,
    StartSpecialDate,
    User,
    UserCancellationPlace,
    UserNotificationPrefs,
)
from app.notification_markup import to_plain, to_telegram_html
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services import plan_notification_service as plans
from app.services.location_cancellation_notify import CancellationChange, notify_cancellation_changes
from app.services.notification_channels_service import CheckOutcome
from app.services.run_plan_service import admin_delete_special_date, admin_update_special_date

SAT = date(2026, 10, 31)
NEXT_SAT = date(2026, 11, 7)
NEW_YEAR = date(2027, 1, 1)  # пятница; сид миграции 116 — «Новогодний старт»
NEW_YEAR_SAT = date(2027, 1, 2)


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """Очередь celery не трогаем: запоминаем, что бы ушло воркеру."""
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
    monkeypatch.setattr(plans, "get_settings", lambda: settings)
    return settings


@pytest.fixture(autouse=True)
def _checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверки доставляемости зелёные — сеть под pytest молчит."""
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=True))


def _platform(db: Session, code: str = "five_verst") -> Platform:
    platform = db.query(Platform).filter(Platform.code == code).one_or_none()
    if platform is None:
        platform = Platform(code=code, name=code, base_url=f"https://{code}.test", is_active=True)
        db.add(platform)
        db.flush()
    return platform


# Старт без номера: локация жива (последний старт моложе ста дней), а ≈№
# от него не считается — опора только у нумерованных стартов.
RECENT_START = date(2026, 10, 17)


def _location(db: Session, *, name: str, timezone: str | None = "Europe/Moscow", **fields: object) -> Location:
    location = Location(
        platform_id=_platform(db).id,
        external_key=f"plan-{uuid4().hex[:8]}",
        name=name,
        timezone=timezone,
        **fields,
    )
    db.add(location)
    db.flush()
    _event(db, location, RECENT_START, None)
    return location


def _event(db: Session, location: Location, on_date: date, number: int | None) -> None:
    db.add(
        Event(
            platform_id=location.platform_id,
            location_id=location.id,
            external_event_key=f"ev-{location.external_key}-{on_date.isoformat()}",
            event_date=on_date,
            event_number=number,
        )
    )
    db.flush()


def _user(db: Session, name: str) -> User:
    chat_id = int(uuid4().int % 10_000_000_000)
    user = User(display_name=name, telegram_id=chat_id, telegram_chat_id=chat_id)
    db.add(user)
    db.commit()
    notify.set_channel_enabled(db, user, "telegram", True)
    db.commit()
    return user


def _plan(db: Session, user: User, location: Location, plan_date: date) -> RunPlan:
    plan = RunPlan(
        user_id=user.id,
        plan_date=plan_date,
        location_key=f"location:{location.id}",
        location_id=location.id,
        platform_code="five_verst",
    )
    db.add(plan)
    db.commit()
    return plan


PLAN_KINDS = (plans.REMINDER_KIND, plans.CANCELLATION_KIND)
OPT_OUT = "Напоминания не нужны? [Выключить](https://run5k.test/plans#plan-notifications)"


def _sent(db: Session, user: User, kind: str | None = None) -> list[NotificationDelivery]:
    kinds = (kind,) if kind else PLAN_KINDS
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.user_id == user.id, NotificationDelivery.kind.in_(kinds))
        .all()
    )


def _new_year(db: Session) -> StartSpecialDate:
    """Особая дата 01.01 — из сида миграции; на пустой базе без сида заводим сами."""
    special = db.query(StartSpecialDate).filter_by(start_date=NEW_YEAR).one_or_none()
    if special is None:
        special = StartSpecialDate(start_date=NEW_YEAR, title="Новогодний старт", platforms=["five_verst"])
        db.add(special)
    elif "five_verst" not in (special.platforms or []):
        special.platforms = [*(special.platforms or []), "five_verst"]
    db.flush()
    return special


def _cancel(location: Location, reason: str | None = "Субботник в парке") -> CancellationChange:
    return CancellationChange(
        platform_code="five_verst", slug=location.external_key, name=location.name, cancelled=True, reason=reason
    )


# ---------------------------------------------------------------------------
# Напоминание накануне


def test_reminder_goes_on_local_evening_of_the_plan_location(db_session: Session) -> None:
    """Вечер — по поясу локации: в Екатеринбурге он наступает на два часа раньше."""
    ekb = _location(db_session, name="Екатеринбург", timezone="Asia/Yekaterinburg")
    msk = _location(db_session, name="Кузьминки")
    _event(db_session, msk, date(2026, 10, 24), 213)
    traveller = _user(db_session, "Едет в Екатеринбург")
    local = _user(db_session, "Москвич")
    _plan(db_session, traveller, ekb, SAT)
    _plan(db_session, local, msk, SAT)
    # Как на проде: после коммита одного человека поля следующего протухают.
    db_session.expire_on_commit = True

    # Четверг, 19:00 МСК: старт не завтра.
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 29, 16, 0, tzinfo=UTC)) == 0
    # Пятница, 18:30 в Екатеринбурге (16:30 МСК): пора только ему.
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 13, 30, tzinfo=UTC)) == 1
    assert len(_sent(db_session, traveller)) == 1
    assert _sent(db_session, local) == []
    # 18:30 МСК: москвичу пора, а Екатеринбургу повтор не уходит.
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 15, 30, tzinfo=UTC)) == 1
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 17, 30, tzinfo=UTC)) == 0
    assert len(_sent(db_session, traveller)) == 1
    # 21:30 МСК: окно закрыто.
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 18, 30, tzinfo=UTC)) == 0

    (delivery,) = _sent(db_session, local)
    assert delivery.kind == "plan_reminders"
    payload = delivery.payload
    assert payload["title"] == "Завтра старт"
    assert payload["text"].startswith("Завтра, сб 31 октября — ")
    assert "Кузьминки" in payload["text"]
    assert "старт в 9:00 (≈№214). Не опаздывайте!\n\n" in payload["text"]
    # Последняя строка — выключить только напоминания, кнопка — «Мои планы».
    assert payload["text"].endswith(OPT_OUT)
    assert payload["url_label"] == "Мои планы"
    # План всегда «пробежать» — что человек собирался делать, текст не повторяет.
    assert "планировали" not in payload["text"] and "волонт" not in payload["text"]
    assert payload["url"] == "https://run5k.test/plans"
    assert delivery.dedupe_key == "plan-eve:2026-10-31"


def test_new_year_reminder_goes_on_december_31_in_one_message(db_session: Session) -> None:
    """Перед 01.01 напоминают 31.12; два плана на дату — одно сообщение списком."""
    _new_year(db_session)
    first = _location(db_session, name="Кузьминки")
    second = _location(db_session, name="Коломенское")
    user = _user(db_session, "Два старта")
    _plan(db_session, user, first, NEW_YEAR)
    _plan(db_session, user, second, NEW_YEAR)

    assert plans.send_plan_reminders(db_session, now=datetime(2026, 12, 30, 16, 0, tzinfo=UTC)) == 0
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 12, 31, 16, 0, tzinfo=UTC)) == 1
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 12, 31, 17, 0, tzinfo=UTC)) == 0

    (delivery,) = _sent(db_session, user)
    text = delivery.payload["text"]
    assert text.startswith("Завтра, пт 1 января, у вас в планах:")
    assert "Кузьминки" in text and "Коломенское" in text
    assert "время уточняйте у организаторов" in text
    assert "📍" in text and "· бег" not in text and "волонт" not in text
    assert text.endswith("Не опаздывайте!\n\n" + OPT_OUT)
    assert delivery.dedupe_key == "plan-eve:2027-01-01"


def test_reminder_for_cancelled_start_says_so(db_session: Session) -> None:
    location = _location(db_session, name="Кузьминки", is_cancelled=True, cancel_reason="Субботник в парке.")
    user = _user(db_session, "Планирует")
    _plan(db_session, user, location, SAT)

    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 1
    (delivery,) = _sent(db_session, user)
    assert delivery.payload["title"] == "Завтра старт отменён"
    assert delivery.payload["text"].endswith("старт отменён.\nСубботник в парке.\n\n" + OPT_OUT)
    assert "Не опаздывайте" not in delivery.payload["text"]


def test_no_reminder_for_paused_location(db_session: Session) -> None:
    location = _location(db_session, name="На паузе", is_paused=True)
    user = _user(db_session, "Планирует")
    _plan(db_session, user, location, SAT)

    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 0
    assert _sent(db_session, user) == []


def test_reminders_off_keeps_cancellations(db_session: Session) -> None:
    """«Хочет только планировать, а напоминания ему не нужны» (Дмитрий
    26.10.2026): выключил напоминания — об отмене всё равно узнает."""
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Без напоминаний")
    notify.update_prefs(db_session, user.id, kinds={"plan_reminders": False})
    db_session.commit()
    _plan(db_session, user, location, SAT)

    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 0
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=now) == 1
    assert [item.kind for item in _sent(db_session, user)] == ["plan_cancellations"]


def test_cancellations_off_keeps_reminders(db_session: Session) -> None:
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Без отмен")
    notify.update_prefs(db_session, user.id, kinds={"plan_cancellations": False})
    db_session.commit()
    _plan(db_session, user, location, SAT)

    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=now) == 0
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 1
    assert [item.kind for item in _sent(db_session, user)] == ["plan_reminders"]


def test_both_plan_kinds_off_means_no_plan_messages(db_session: Session) -> None:
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Выключил планы")
    notify.update_prefs(db_session, user.id, kinds={"plan_reminders": False, "plan_cancellations": False})
    db_session.commit()
    _plan(db_session, user, location, SAT)

    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 0
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=now) == 0
    assert _sent(db_session, user) == []


def test_plan_kinds_are_on_by_default_and_old_code_is_ignored(db_session: Session) -> None:
    """Оба вида включены по умолчанию. Прежний общий код «plans», оставшийся
    в prefs (стенд, тумблер до разделения), ничего не ломает: настройки его не
    показывают, напоминание и отмена уходят по новым видам."""
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Старый тумблер")
    prefs = db_session.get(UserNotificationPrefs, user.id) or UserNotificationPrefs(user_id=user.id, kinds={})
    prefs.kinds = {"plans": False}
    db_session.add(prefs)
    db_session.commit()
    _plan(db_session, user, location, SAT)

    state = {item["code"]: item for item in notify.settings_state(db_session, user)["kinds"]}
    assert "plans" not in state
    assert state["plan_reminders"]["enabled"] is True
    assert state["plan_reminders"]["title"] == "Напоминание накануне"
    assert state["plan_cancellations"]["enabled"] is True
    assert state["plan_cancellations"]["title"] == "Отмена запланированного старта"
    # Тумблер нового вида сохраняется рядом со старым ключом без ошибки.
    notify.update_prefs(db_session, user.id, kinds={"plan_cancellations": True})
    db_session.commit()

    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=now) == 1
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 1
    assert sorted(item.kind for item in _sent(db_session, user)) == ["plan_cancellations", "plan_reminders"]


def test_reminder_opt_out_link_renders_for_every_channel(db_session: Session) -> None:
    """«Выключить» — ссылка на тумблеры планировщика в настройках: в Telegram —
    тегом, в VK и тексте письма — адресом строкой ниже."""
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Планирует")
    _plan(db_session, user, location, SAT)
    assert plans.send_plan_reminders(db_session, now=datetime(2026, 10, 30, 16, 0, tzinfo=UTC)) == 1
    (delivery,) = _sent(db_session, user)
    text = delivery.payload["text"]

    html = to_telegram_html(text)
    assert html.endswith(
        'Напоминания не нужны? <a href="https://run5k.test/plans#plan-notifications">Выключить</a>'
    )
    plain = to_plain(text)
    assert plain.endswith("Напоминания не нужны? Выключить\nhttps://run5k.test/plans#plan-notifications")
    # У отмены строки «Выключить» нет: это не напоминание.
    now = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    other = _user(db_session, "Узнаёт об отмене")
    _plan(db_session, other, location, SAT)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=now) >= 1
    (cancel,) = _sent(db_session, other, plans.CANCELLATION_KIND)
    assert "Напоминания не нужны" not in cancel.payload["text"]


# ---------------------------------------------------------------------------
# Старт отменён


def test_cancellation_goes_only_to_planners_of_the_nearest_start(db_session: Session) -> None:
    """Отмена — тем, у кого план на эту локацию и её ближайший старт; фильтры
    систем и мест вида «Отмены стартов» здесь не действуют."""
    location = _location(db_session, name="Кузьминки")
    other = _location(db_session, name="Коломенское")
    planner = _user(db_session, "Собрался")
    later = _user(db_session, "Через неделю")
    elsewhere = _user(db_session, "В другом месте")
    notify.update_prefs(db_session, planner.id, kinds={"cancellations": False}, cancellation_platforms=["s95"])
    db_session.add(UserCancellationPlace(user_id=planner.id, place_key="city:владивосток", label="Владивосток"))
    db_session.commit()
    _plan(db_session, planner, location, SAT)
    _plan(db_session, later, location, NEXT_SAT)
    _plan(db_session, elsewhere, other, SAT)
    db_session.expire_on_commit = True

    wednesday = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=wednesday) == 1
    (delivery,) = _sent(db_session, planner)
    assert delivery.kind == "plan_cancellations"
    assert delivery.payload["title"] == "Старт отменён"
    text = delivery.payload["text"]
    assert "Кузьминки" in text
    assert "сб 31 октября: старт отменён." in text
    assert "Субботник в парке" in text
    assert text.endswith("Этот старт был у вас в планах.")
    assert "волонт" not in text and "бег." not in text
    assert delivery.payload["url"] == "https://run5k.test/plans"
    assert _sent(db_session, later) == []
    assert _sent(db_session, elsewhere) == []

    # Повтор той же отмены (реестр и наблюдатель видят её по очереди) не уходит.
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=wednesday) == 0
    # Снятие отмены планирующим не рассылаем.
    restored = CancellationChange(
        platform_code="five_verst", slug=location.external_key, name=location.name, cancelled=False
    )
    assert plans.notify_plan_cancellations(db_session, [restored], now=wednesday) == 0
    assert len(_sent(db_session, planner)) == 1


def test_cancellation_after_the_start_is_not_sent(db_session: Session) -> None:
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Собрался")
    _plan(db_session, user, location, SAT)

    # Суббота, 10:00 МСК: старт уже идёт, отмена — запись задним числом.
    late = datetime(2026, 10, 31, 7, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=late) == 0
    # 8:00 МСК — ещё успевает узнать.
    early = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=early) == 1


def test_far_east_cancellation_date_is_by_moscow_day(db_session: Session) -> None:
    """Барнаул (МСК+4): реестр 5 вёрст в сб 20:51 МСК видит флаг, а там уже
    вс 00:51. Флаг — о сегодняшней субботе, как у календаря и общей
    рассылки: она прошла, и планирующим следующую субботу «старт отменён» не
    уходит (ревью 28.10.2026, NOTIFY-1). В пятницу тот же час — о завтрашней
    субботе, и она ещё впереди."""
    location = _location(db_session, name="Заринск", timezone="Asia/Barnaul")
    this_week = _user(db_session, "Эта суббота")
    next_week = _user(db_session, "Следующая суббота")
    _plan(db_session, this_week, location, SAT)
    _plan(db_session, next_week, location, NEXT_SAT)

    saturday_evening = datetime(2026, 10, 31, 17, 51, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=saturday_evening) == 0
    assert _sent(db_session, next_week) == []

    friday_evening = datetime(2026, 10, 30, 17, 51, tzinfo=UTC)
    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=friday_evening) == 1
    (delivery,) = _sent(db_session, this_week)
    assert "сб 31 октября: старт отменён." in delivery.payload["text"]
    assert _sent(db_session, next_week) == []


def test_cancellation_monitor_notifies_planners(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Общий вход отмен зовёт рассылку планирующим вместе с подписчиками."""
    calls: list[list[CancellationChange]] = []
    monkeypatch.setattr(
        "app.services.cancellation_notification_service.notify_cancellation_subscribers", lambda db, changes: 0
    )
    monkeypatch.setattr(plans, "notify_plan_cancellations", lambda db, changes: calls.append(changes) or 0)
    monkeypatch.setattr("app.services.location_cancellation_notify.notify_admin", lambda text: True)
    change = CancellationChange(platform_code="five_verst", slug="kuzminki", name="Кузьминки", cancelled=True)

    assert notify_cancellation_changes([change], db_session) is True
    assert calls == [[change]]


# ---------------------------------------------------------------------------
# Дата отменённого старта около Нового года (NT-1, NT-2)


def _msk(day: date, hour: int) -> datetime:
    """Момент по Москве (UTC+3) — в UTC."""
    return datetime(day.year, day.month, day.day, hour - 3, 0, tzinfo=UTC)


def test_cancellation_before_new_year_is_about_saturday(db_session: Session) -> None:
    """Локация 01.01 не бегает (год назад старта не было) и отменила 02.01:
    отмена — тем, кто планирует 02.01, а не 01.01."""
    _new_year(db_session)
    location = _location(db_session, name="Кузьминки", is_cancelled=True)
    friday = _user(db_session, "Планирует 01.01")
    saturday = _user(db_session, "Планирует 02.01")
    _plan(db_session, friday, location, NEW_YEAR)
    _plan(db_session, saturday, location, NEW_YEAR_SAT)

    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=_msk(date(2026, 12, 28), 15)) == 1
    assert _sent(db_session, friday) == []
    (delivery,) = _sent(db_session, saturday)
    assert "сб 2 января: старт отменён." in delivery.payload["text"]


def test_cancellation_on_new_year_day_reaches_saturday_planners(db_session: Session) -> None:
    """01.01 в 15:00 отменили субботу: «старт уже идёт» про 01.01 тут ни при
    чём — сообщение уходит планирующим 02.01."""
    _new_year(db_session)
    location = _location(db_session, name="Кузьминки", is_cancelled=True)
    saturday = _user(db_session, "Планирует 02.01")
    _plan(db_session, saturday, location, NEW_YEAR_SAT)

    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=_msk(NEW_YEAR, 15)) == 1
    assert len(_sent(db_session, saturday)) == 1


def test_cancellation_goes_to_new_year_if_it_ran_last_year(db_session: Session) -> None:
    """Год назад 01.01 здесь бежали — флаг до Нового года о старте 01.01."""
    _new_year(db_session)
    location = _location(db_session, name="Кузьминки", is_cancelled=True)
    _event(db_session, location, date(2026, 1, 1), 50)
    friday = _user(db_session, "Планирует 01.01")
    saturday = _user(db_session, "Планирует 02.01")
    _plan(db_session, friday, location, NEW_YEAR)
    _plan(db_session, saturday, location, NEW_YEAR_SAT)

    assert plans.notify_plan_cancellations(db_session, [_cancel(location)], now=_msk(date(2026, 12, 28), 15)) == 1
    (delivery,) = _sent(db_session, friday)
    assert "пт 1 января: старт отменён." in delivery.payload["text"]
    assert _sent(db_session, saturday) == []


def test_new_year_reminder_does_not_claim_the_saturday_cancellation(db_session: Session) -> None:
    """31.12 вечером: флаг стоит, но он о субботе — про 01.01 «Завтра старт»."""
    _new_year(db_session)
    location = _location(db_session, name="Кузьминки", is_cancelled=True)
    user = _user(db_session, "Планирует 01.01")
    _plan(db_session, user, location, NEW_YEAR)

    assert plans.send_plan_reminders(db_session, now=_msk(date(2026, 12, 31), 19)) == 1
    (delivery,) = _sent(db_session, user)
    assert delivery.payload["title"] == "Завтра старт"
    assert "отменён" not in delivery.payload["text"]


def test_saturday_reminder_after_new_year_says_cancelled(db_session: Session) -> None:
    """01.01 вечером о плане на 02.01 при отменённой субботе — «отменён», а не
    «Не опаздывайте!», с опорным стартом и без него (NT-2)."""
    _new_year(db_session)
    with_anchor = _location(db_session, name="С опорой", is_cancelled=True)
    _event(db_session, with_anchor, date(2026, 12, 26), 100)
    without_anchor = _location(db_session, name="Без опоры", is_cancelled=True)
    users = []
    for location in (with_anchor, without_anchor):
        user = _user(db_session, f"Планирует {location.name}")
        _plan(db_session, user, location, NEW_YEAR_SAT)
        users.append(user)

    assert plans.send_plan_reminders(db_session, now=_msk(NEW_YEAR, 19)) == 2
    for user in users:
        (delivery,) = _sent(db_session, user)
        assert delivery.payload["title"] == "Завтра старт отменён"
        assert "Не опаздывайте" not in delivery.payload["text"]


# ---------------------------------------------------------------------------
# Даты больше нет в календаре — напоминания нет (NT-3, INTEG-1)


@pytest.mark.parametrize("change", ["delete", "move", "other_systems"])
def test_no_reminder_when_special_date_is_gone(db_session: Session, change: str) -> None:
    special = _new_year(db_session)
    location = _location(db_session, name="Кузьминки")
    user = _user(db_session, "Планирует 01.01")
    _plan(db_session, user, location, NEW_YEAR)
    if change == "delete":
        admin_delete_special_date(db_session, special.id)
    elif change == "move":
        admin_update_special_date(
            db_session, special.id, start_date=date(2027, 1, 3), title=special.title, platforms=["five_verst"]
        )
    else:
        admin_update_special_date(db_session, special.id, start_date=NEW_YEAR, title=special.title, platforms=["s95"])
    db_session.commit()

    assert plans.send_plan_reminders(db_session, now=_msk(date(2026, 12, 31), 19)) == 0
    assert _sent(db_session, user) == []
    # План на обычную субботу по-прежнему напоминается.
    _plan(db_session, user, location, NEW_YEAR_SAT)
    assert plans.send_plan_reminders(db_session, now=_msk(NEW_YEAR, 19)) == 1
