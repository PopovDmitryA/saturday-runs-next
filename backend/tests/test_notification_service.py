"""Уведомления сайта: настройки, очередь, перебор каналов, отписка, разметка, бэклог, сканер."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    AuthIdentity,
    AuthProvider,
    BacklogCardStatus,
    BacklogCardSubscription,
    BacklogCardType,
    Event,
    Location,
    NotificationDelivery,
    Participant,
    Platform,
    PlatformLink,
    RunResult,
    User,
)
from app.notification_markup import to_email_html, to_plain, to_telegram_html
from app.services import activity_notification_service as activity
from app.services import notification_channels_service as channels
from app.services import notification_service as notify
from app.services.backlog_service import (
    create_card,
    create_comment,
    get_card,
    set_card_subscription,
    update_card_status,
)
from app.services.notification_channels_service import CheckOutcome
from app.services.notification_senders import OutgoingMessage, SendOutcome

SECRET = "test-secret-key"


@pytest.fixture(autouse=True)
def _no_broker(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    """Очередь celery не трогаем: запоминаем, что бы ушло воркеру."""
    queued: list[UUID] = []
    monkeypatch.setattr(notify, "enqueue_delivery", lambda delivery_id: queued.append(delivery_id) or True)
    return queued


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    settings = Settings(
        app_secret_key=SECRET,
        app_base_url="https://run5k.test",
        telegram_bot_token="t",
        telegram_bot_username="test_bot",
        telegram_admin_chat_id=4242,
        vk_bot_group_token="vk",
        vk_bot_group_id=1,
        vk_bot_group_screen_name="run5k",
        smtp_enabled=True,
        smtp_host="smtp.test",
        smtp_user="support@run5k.test",
        smtp_password="p",
    )
    monkeypatch.setattr(notify, "get_settings", lambda: settings)
    monkeypatch.setattr(channels, "get_settings", lambda: settings)
    monkeypatch.setattr(activity, "get_settings", lambda: settings)
    return settings


@pytest.fixture(autouse=True)
def _checks_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Проверки доставляемости по умолчанию зелёные — сеть под pytest молчит."""
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda target, settings: CheckOutcome(ok=True))
    monkeypatch.setitem(channels.CHECKERS, "vk", lambda target, settings: CheckOutcome(ok=True))


def _make_user(db: Session, *, name: str = "Тест", chat_id: int | None = None, email: str | None = None) -> User:
    user = User(display_name=name, telegram_id=chat_id, telegram_chat_id=chat_id)
    db.add(user)
    db.flush()
    if email:
        db.add(AuthIdentity(user_id=user.id, provider=AuthProvider.email, external_id=email, email=email))
    db.commit()
    return user


def _on(db: Session, user: User, channel: str = "telegram") -> None:
    notify.set_channel_enabled(db, user, channel, True)
    db.commit()


def _fake_senders(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, SendOutcome]) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    def _sender(channel: str):
        def _send(target: str, message):  # noqa: ANN001
            calls.append((channel, target))
            return outcomes.get(channel, SendOutcome(ok=False, error="down"))

        return _send

    monkeypatch.setattr(notify, "SENDERS", {c: _sender(c) for c in channels.CHANNEL_ORDER})
    return calls


# ---------------------------------------------------------------------------
# Разметка


def test_markup_renders_per_channel() -> None:
    text = "**Жирно** и [ссылка](https://run5k.test/x) <b>не тег</b>"
    assert (
        to_telegram_html(text) == '<b>Жирно</b> и <a href="https://run5k.test/x">ссылка</a> &lt;b&gt;не тег&lt;/b&gt;'
    )
    assert to_plain(text) == "Жирно и ссылка <b>не тег</b>\nhttps://run5k.test/x"
    assert to_email_html("a\nb") == "a<br>b"


def test_outgoing_message_telegram_html_has_title_link_and_settings() -> None:
    message = OutgoingMessage(
        title="🏃 Пробежка попала на сайт",
        text="**📍 Парк** · 20 сентября\n⏱ 24:31",
        url="https://run5k.test/users/1/runs",
        url_label="Мои пробежки",
        unsubscribe_url="https://run5k.test/api/notifications/unsubscribe?token=abc",
        settings_url="https://run5k.test/settings#notifications",
    )
    html = message.telegram_html()
    assert html.startswith("<b>🏃 Пробежка попала на сайт</b>\n\n<b>📍 Парк</b> · 20 сентября\n⏱ 24:31")
    assert '<a href="https://run5k.test/users/1/runs">Мои пробежки</a>' in html
    # Подвал ведёт в настройки: отписка одним кликом остаётся в письмах.
    assert html.endswith('──────────\n⚙️ <a href="https://run5k.test/settings#notifications">Настроить уведомления</a>')
    assert "unsubscribe" not in html

    plain = message.plain_text()
    assert "<b>" not in plain and "Мои пробежки: https://run5k.test/users/1/runs" in plain
    assert plain.endswith("──────────\n⚙️ Настроить уведомления: https://run5k.test/settings#notifications")


# ---------------------------------------------------------------------------
# Настройки и каналы


def test_default_is_silent(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100)
    assert notify.is_enabled(db_session, user.id) is False
    assert notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="a") is None
    assert db_session.query(NotificationDelivery).filter_by(user_id=user.id).count() == 0


def test_enabling_channel_creates_row_checks_and_arms_watermarks(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100)
    before = datetime.now(UTC)
    _on(db_session, user)
    row = channels.get_channel(db_session, user.id, "telegram")
    assert row is not None and row.enabled and row.external_id == "100" and row.check_ok is True
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None and prefs.runs_notified_through >= before and prefs.settings_touched_at is not None
    # Снимок рейтингов сеется сразу при включении (в тестовой базе рейтинги пустые).
    assert prefs.challenge_levels is None and prefs.ratings_snapshot == {} and prefs.milestones_seen is None
    by_code = {k["code"]: k for k in notify.kinds_state(prefs)}
    assert by_code["runs"]["enabled"] and by_code["backlog"]["enabled"] and by_code["ratings"]["enabled"]
    assert by_code["backlog_new_cards"]["enabled"] is False
    assert "volunteer_signup" not in by_code  # добавится вместе с фичей записи


def test_enabling_without_link_is_rejected(db_session: Session) -> None:
    user = _make_user(db_session)
    with pytest.raises(channels.ChannelError):
        notify.set_channel_enabled(db_session, user, "telegram", True)
    with pytest.raises(channels.ChannelError):
        notify.set_channel_enabled(db_session, user, "pigeon", True)


def test_update_prefs_rejects_unknown(db_session: Session) -> None:
    user = _make_user(db_session)
    with pytest.raises(ValueError):
        notify.update_prefs(db_session, user.id, kinds={"nope": True})
    with pytest.raises(ValueError):
        notify.update_prefs(db_session, user.id, primary_channel="pigeon")


def test_kind_toggle_off_skips_queue(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    notify.update_prefs(db_session, user.id, kinds={"runs": False})
    assert notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="a") is None
    assert notify.notify_user(db_session, user, "backlog", title="t", text="x", dedupe_key="a") is not None


def test_resolve_targets_primary_first_then_default_order(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100, email="runner@example.com")
    db_session.add(AuthIdentity(user_id=user.id, provider=AuthProvider.vk, external_id="777"))
    db_session.commit()
    for channel in ("telegram", "vk", "email"):
        _on(db_session, user, channel)

    default = [t.channel for t in channels.resolve_targets(db_session, user, primary=None)]
    assert default == ["telegram", "vk", "email"]
    with_primary = [t.channel for t in channels.resolve_targets(db_session, user, primary="email")]
    assert with_primary == ["email", "telegram", "vk"]

    notify.set_channel_enabled(db_session, user, "telegram", False)
    assert [t.channel for t in channels.resolve_targets(db_session, user, primary=None)] == ["vk", "email"]


def test_channels_state_flags_undeliverable_telegram(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100, email="runner@example.com")
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda t, s: CheckOutcome(ok=False, error="telegram 403"))
    state = {c["channel"]: c for c in channels.channels_state(db_session, user)}
    assert state["telegram"]["linked"] and state["telegram"]["enabled"] is False
    assert state["telegram"]["deliverable"] is False and "Start" in state["telegram"]["problem"]
    assert state["email"]["deliverable"] is True and state["email"]["label"] == "runner@example.com"
    assert state["vk"]["linked"] is False and state["vk"]["deliverable"] is None


def test_enable_best_channel_skips_undeliverable(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100, email="runner@example.com")
    monkeypatch.setitem(channels.CHECKERS, "telegram", lambda t, s: CheckOutcome(ok=False, error="telegram 403"))
    assert notify.enable_now(db_session, user) == "email"
    assert channels.enabled_channels(db_session, user.id) == ["email"]


def test_enable_if_untouched_respects_explicit_choice(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100)
    assert notify.enable_if_untouched(db_session, user) == "telegram"
    assert notify.enable_if_untouched(db_session, user) is None  # уже включено

    other = _make_user(db_session, chat_id=200)
    notify.set_channel_enabled(db_session, other, "telegram", True)
    notify.set_channel_enabled(db_session, other, "telegram", False)
    db_session.commit()
    assert notify.enable_if_untouched(db_session, other) is None  # выключил сам


def test_confirm_telegram_channel_by_token(db_session: Session, fake_redis) -> None:  # noqa: ANN001
    user = _make_user(db_session)
    url = channels.telegram_connect_url(user)
    assert url and url.startswith("https://t.me/test_bot?start=notify_")
    token = url.rsplit("notify_", 1)[1]

    assert channels.confirm_telegram_channel(db_session, "stale", telegram_id=1, chat_id=1) is None
    confirmed = channels.confirm_telegram_channel(db_session, token, telegram_id=555, chat_id=555)
    assert confirmed is not None and confirmed.id == user.id
    assert channels.enabled_channels(db_session, user.id) == ["telegram"]
    assert channels.confirm_telegram_channel(db_session, token, telegram_id=555, chat_id=555) is None


def test_nudge_shown_until_enabled_or_dismissed(db_session: Session) -> None:
    user = _make_user(db_session, chat_id=100)
    assert notify.nudge_state(db_session, user)["show"] is True
    notify.dismiss_nudge(db_session, user.id)
    assert notify.nudge_state(db_session, user)["show"] is False
    nobody = _make_user(db_session)
    assert notify.nudge_state(db_session, nobody)["show"] is False  # включать нечего


# ---------------------------------------------------------------------------
# Очередь и доставка


def test_notify_dedupes_by_key(db_session: Session, _no_broker: list[UUID]) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    first = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="same")
    second = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="same")
    assert first is not None and second is None
    assert _no_broker == [first.id]


def test_deliver_falls_back_to_next_channel(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100, email="runner@example.com")
    _on(db_session, user, "telegram")
    _on(db_session, user, "email")
    monkeypatch.setattr(notify, "send_telegram_html", lambda target, html: SendOutcome(ok=True))
    calls = _fake_senders(
        monkeypatch,
        {"telegram": SendOutcome(ok=False, error="403 blocked", permanent=True), "email": SendOutcome(ok=True)},
    )
    delivery = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="k")
    assert delivery is not None

    assert notify.deliver_now(db_session, delivery.id) == "sent"
    assert [c for c, _ in calls] == ["telegram", "email"]
    db_session.refresh(delivery)
    assert delivery.channel == "email" and delivery.sent_at is not None
    tg = channels.get_channel(db_session, user.id, "telegram")
    assert tg is not None and tg.last_error and "403" in tg.last_error
    state = {c["channel"]: c for c in channels.channels_state(db_session, user)}
    assert state["telegram"]["last_error"] and state["email"]["last_error"] is None


def test_deliver_all_failed_transient_leaves_retry(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    _fake_senders(monkeypatch, {"telegram": SendOutcome(ok=False, error="timeout")})
    delivery = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="k")
    assert delivery is not None
    assert notify.deliver_now(db_session, delivery.id) == "failed"
    db_session.refresh(delivery)
    assert delivery.attempts == 1 and "timeout" in (delivery.error or "")


def test_deliver_all_failed_permanent_exhausts_attempts(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    _fake_senders(monkeypatch, {"telegram": SendOutcome(ok=False, error="blocked", permanent=True)})
    delivery = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="k")
    assert delivery is not None
    notify.deliver_now(db_session, delivery.id)
    db_session.refresh(delivery)
    assert delivery.attempts == notify.MAX_ATTEMPTS


def test_deliver_skips_when_unsubscribed_meanwhile(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=200)
    _on(db_session, user)
    _fake_senders(monkeypatch, {"telegram": SendOutcome(ok=True)})
    delivery = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="k")
    assert delivery is not None
    notify.disable_all(db_session, user)
    db_session.commit()
    assert notify.deliver_now(db_session, delivery.id) == "skipped"


def test_delivered_notification_is_copied_to_admin_as_html(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, chat_id=100)
    user.telegram_username = "runner"
    _on(db_session, user)
    _fake_senders(monkeypatch, {"telegram": SendOutcome(ok=True)})
    copies: list[tuple[str, str]] = []
    monkeypatch.setattr(
        notify, "send_telegram_html", lambda target, html: copies.append((target, html)) or SendOutcome(ok=True)
    )
    delivery = notify.notify_user(db_session, user, "runs", title="🏃 Пробежка", text="**📍 Парк**", dedupe_key="k")
    assert delivery is not None

    assert notify.deliver_now(db_session, delivery.id) == "sent"
    assert len(copies) == 1
    target, html = copies[0]
    assert target == "4242"
    assert html.startswith("📨 Сообщение направлено @runner · Telegram\n\n<b>🏃 Пробежка</b>\n\n<b>📍 Парк</b>")

    copies.clear()
    settings = notify.get_settings()
    monkeypatch.setattr(notify, "get_settings", lambda: settings.model_copy(update={"notifications_admin_copy": False}))
    second = notify.notify_user(db_session, user, "runs", title="t", text="x", dedupe_key="k2")
    assert second is not None and notify.deliver_now(db_session, second.id) == "sent"
    assert copies == []


def test_message_carries_unsubscribe_and_settings_links(db_session: Session, _settings: Settings) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    delivery = notify.notify_user(
        db_session, user, "runs", title="Новая пробежка", text="📍 Парк", dedupe_key="k", url="https://run5k.test/x"
    )
    assert delivery is not None
    message = notify.build_message(_settings, delivery)
    assert "/api/notifications/unsubscribe?token=" in message.unsubscribe_url
    assert message.settings_url == "https://run5k.test/settings#notifications"
    assert message.html and "Отписаться" in message.html and "📍 Парк" in message.html


# ---------------------------------------------------------------------------
# Отписка ссылкой


def test_unsubscribe_token_roundtrip_and_apply(db_session: Session, _settings: Settings) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)

    token = notify.make_unsubscribe_token(user.id, "runs", SECRET)
    assert notify.parse_unsubscribe_token(token, SECRET) == (user.id, "runs")
    with pytest.raises(notify.NotificationTokenError):
        notify.parse_unsubscribe_token(token[:-2] + "zz", SECRET)
    with pytest.raises(notify.NotificationTokenError):
        notify.parse_unsubscribe_token(notify.make_unsubscribe_token(user.id, "bogus", SECRET), SECRET)

    scope, title = notify.apply_unsubscribe(db_session, token, _settings)
    assert scope == "runs" and title == "Мои пробежки"
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None and prefs.kinds == {"runs": False}
    assert notify.is_enabled(db_session, user.id)

    all_token = notify.make_unsubscribe_token(user.id, notify.UNSUBSCRIBE_ALL, SECRET)
    notify.apply_unsubscribe(db_session, all_token, _settings)
    assert notify.is_enabled(db_session, user.id) is False


def test_unsubscribe_for_missing_user_is_quiet(db_session: Session, _settings: Settings) -> None:
    token = notify.make_unsubscribe_token(uuid4(), "runs", SECRET)
    assert notify.apply_unsubscribe(db_session, token, _settings) == ("runs", "Мои пробежки")


# ---------------------------------------------------------------------------
# Бэклог: создание, комментарии, статус, новые карточки, колокольчик


def _card(db: Session, author: User, title: str = "Идея"):
    return create_card(
        db,
        author_id=author.id,
        type_=BacklogCardType.feature,
        category="other",
        title=title,
        description="Описание",
        is_anonymous=False,
    )


def test_backlog_author_gets_created_notice_and_follows(db_session: Session, _no_broker: list[UUID]) -> None:
    author = _make_user(db_session, name="Автор", chat_id=1)
    _on(db_session, author)
    card = _card(db_session, author)
    assert card.is_subscribed is True
    assert db_session.get(BacklogCardSubscription, (card.id, author.id)) is not None
    rows = db_session.query(NotificationDelivery).filter_by(user_id=author.id, kind="backlog").all()
    assert len(rows) == 1 and rows[0].payload["title"] == "✅ Карточка «Идея» принята в бэклог"
    assert rows[0].payload["url"].endswith(f"/backlog?card={card.id}")
    assert _no_broker == [rows[0].id]


def test_backlog_comment_goes_to_followers_not_author_of_reply(db_session: Session) -> None:
    author = _make_user(db_session, name="Автор", chat_id=1)
    talker = _make_user(db_session, name="Собеседник", chat_id=2)
    silent = _make_user(db_session, name="Молчун", chat_id=3)
    for user in (author, talker, silent):
        _on(db_session, user)
    card = _card(db_session, author)

    create_comment(db_session, card.id, author_id=talker.id, body="Первый", is_anonymous=False)
    rows = db_session.query(NotificationDelivery).filter(NotificationDelivery.dedupe_key.like("comment:%")).all()
    assert {r.user_id for r in rows} == {author.id}
    assert rows[0].payload["title"] == "💬 Новый комментарий к «Идея»"
    assert rows[0].payload["text"] == "**Собеседник:** Первый"

    create_comment(db_session, card.id, author_id=silent.id, body="Второй", is_anonymous=True)
    rows = db_session.query(NotificationDelivery).filter(NotificationDelivery.dedupe_key.like("comment:%")).all()
    second = [r for r in rows if r.payload["text"].startswith("**аноним:** Второй")]
    assert {r.user_id for r in second} == {author.id, talker.id}


def test_backlog_status_change_notifies_followers(db_session: Session) -> None:
    author = _make_user(db_session, name="Автор", chat_id=1)
    fan = _make_user(db_session, name="Фанат", chat_id=2)
    _on(db_session, author)
    _on(db_session, fan)
    card = _card(db_session, author)
    set_card_subscription(db_session, card.id, user_id=fan.id, subscribed=True)

    update_card_status(db_session, card.id, status=BacklogCardStatus.done)
    rows = db_session.query(NotificationDelivery).filter(NotificationDelivery.dedupe_key.like("status:%")).all()
    assert {r.user_id for r in rows} == {author.id, fan.id}
    assert rows[0].payload["title"] == "✅ Карточка «Идея»: реализовано"
    assert "**Реализовано**" in rows[0].payload["text"]

    # Тот же статус ещё раз — тишина.
    update_card_status(db_session, card.id, status=BacklogCardStatus.done)
    assert db_session.query(NotificationDelivery).filter(NotificationDelivery.dedupe_key.like("status:%")).count() == 2


def test_backlog_new_cards_go_only_to_opted_in(db_session: Session) -> None:
    author = _make_user(db_session, name="Автор", chat_id=1)
    curious = _make_user(db_session, name="Любопытный", chat_id=2)
    plain = _make_user(db_session, name="Обычный", chat_id=3)
    for user in (curious, plain):
        _on(db_session, user)
    notify.update_prefs(db_session, curious.id, kinds={"backlog_new_cards": True})
    db_session.commit()

    card = _card(db_session, author, title="Новая идея")
    rows = db_session.query(NotificationDelivery).filter_by(kind="backlog_new_cards").all()
    assert {r.user_id for r in rows} == {curious.id}
    assert rows[0].payload["title"] == "🆕 Новая карточка в бэклоге: «Новая идея»"
    assert rows[0].payload["url"].endswith(f"/backlog?card={card.id}")


def test_backlog_bell_toggle_enables_untouched_prefs(db_session: Session) -> None:
    author = _make_user(db_session, name="Автор")
    reader = _make_user(db_session, name="Читатель", chat_id=9)
    card = _card(db_session, author, title="Баг")
    assert get_card(db_session, card.id, viewer_id=reader.id).is_subscribed is False

    followed = set_card_subscription(db_session, card.id, user_id=reader.id, subscribed=True)
    assert followed.is_subscribed is True
    assert notify.is_enabled(db_session, reader.id) is True

    unfollowed = set_card_subscription(db_session, card.id, user_id=reader.id, subscribed=False)
    assert unfollowed.is_subscribed is False

    notify.disable_all(db_session, reader)
    db_session.commit()
    set_card_subscription(db_session, card.id, user_id=reader.id, subscribed=True)
    assert notify.is_enabled(db_session, reader.id) is False


# ---------------------------------------------------------------------------
# Сканер: пробежка + рейтинги + челленджи + вехи одним сообщением


def _platform(db: Session) -> Platform:
    platform = db.query(Platform).filter(Platform.code == "five_verst").one_or_none()
    if platform is None:
        platform = Platform(code="five_verst", name="5 вёрст", base_url="https://5verst.ru", is_active=True)
        db.add(platform)
        db.flush()
    return platform


def _run_for(db: Session, user: User, *, event_date, position: int = 5, finish: int = 1500, pr: bool = False):  # noqa: ANN001
    platform = _platform(db)
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
    location = Location(platform_id=platform.id, external_key=f"loc-{uuid4().hex[:8]}", name="Мещерский парк")
    db.add(location)
    db.flush()
    event = Event(
        platform_id=platform.id,
        location_id=location.id,
        external_event_key=f"ev-{uuid4().hex[:8]}",
        event_date=event_date,
        event_number=123,
    )
    db.add(event)
    db.flush()
    result = RunResult(
        event_id=event.id,
        participant_id=participant.id,
        external_result_key=f"r-{uuid4().hex[:8]}",
        position=position,
        finish_time_sec=finish,
        is_pr=pr,
    )
    db.add(result)
    db.commit()
    return result


def _fake_sources(
    monkeypatch: pytest.MonkeyPatch,
    *,
    levels: dict[str, dict[str, str | None]],
    ranks: dict[str, int],
    milestones: list[dict[str, object]],
) -> None:
    payload = {
        "challenges": [
            {
                "code": code,
                "title": {"seconds": "Секундомер", "positions": "Позиции"}.get(code, code),
                "icon": "⏱",
                "tiers": [{"tier": tier, "level": level} for tier, level in tiers.items()],
            }
            for code, tiers in levels.items()
        ]
    }
    monkeypatch.setattr(activity, "compute_challenges", lambda db, user_id: payload)
    monkeypatch.setattr(
        activity,
        "get_my_leaderboard_row",
        lambda db, metric, user: {"rank_overall": ranks.get(metric), "rank": ranks.get(metric)},
    )
    monkeypatch.setattr(
        activity, "get_my_history", lambda db, user_id: {"milestones": milestones, "total": len(milestones)}
    )


def _enable_with_seeds(db: Session, user: User, *, levels, ranks, keys) -> None:  # noqa: ANN001
    db_session = db
    _on(db_session, user)
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None
    prefs.runs_notified_through = datetime.now(UTC) - timedelta(minutes=5)
    prefs.challenge_levels = levels
    prefs.ratings_snapshot = ranks
    prefs.milestones_seen = keys
    db_session.commit()


def test_scan_composes_single_digest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, _no_broker: list[UUID]
) -> None:
    user = _make_user(db_session, chat_id=100)
    today = datetime.now(UTC).date()
    milestone = {
        "kind": "run_club",
        "number": 100,
        "event_date": today - timedelta(days=1),
        "platform_code": "five_verst",
        "location_name": "Мещерский парк",
    }
    _enable_with_seeds(
        db_session,
        user,
        levels={"seconds": {"easy": "bronze", "medium": None}},
        ranks={"runs": 48, "locations": 120},
        keys=[],
    )
    _run_for(db_session, user, event_date=today - timedelta(days=1), position=3, finish=1471, pr=True)
    _fake_sources(
        monkeypatch,
        levels={"seconds": {"easy": "silver", "medium": None}, "positions": {"easy": None}},
        ranks={"runs": 45, "locations": 120, "wins": 7},
        milestones=[milestone],
    )

    summary = activity.scan_user_activity(db_session, user.id)
    assert summary == {"runs": 1, "level_ups": 1, "milestones": 1, "queued": True}

    delivery = db_session.query(NotificationDelivery).filter_by(user_id=user.id).one()
    assert delivery.kind == "runs"
    assert delivery.payload["title"] == "🏃 Пробежка попала на сайт"
    text = delivery.payload["text"]
    assert "**📍 Мещерский парк (5 вёрст) №123 · " in text
    assert "⏱ 24:31 · 🏅 3-е место · 🔥 личный рекорд" in text
    assert "Рейтинги" not in text  # рейтинги — отдельным воскресным сообщением
    assert "🏆 **Челленджи:**\n⏱ Секундомер — 🥈 серебро (лёгкий уровень)" in text
    assert "🎖 **Вехи истории:**\n🏅 100-я пробежка — клуб 100!" in text
    assert "[Собрать постер о пробежке](https://run5k.test/share)" in text
    assert delivery.payload["url"].endswith(f"/users/{user.serial_id}/runs")
    assert _no_broker == [delivery.id]

    # Повторный скан: снимки сдвинуты, нового ничего.
    assert activity.scan_user_activity(db_session, user.id)["queued"] is False


def test_weekly_ratings_message_reports_moves_both_ways(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, _no_broker: list[UUID]
) -> None:
    user = _make_user(db_session, chat_id=100)
    _fake_sources(monkeypatch, levels={}, ranks={"runs": 48, "locations": 120}, milestones=[])
    _on(db_session, user)
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None and prefs.ratings_snapshot == {"runs": 48, "locations": 120}  # снимок при включении

    _fake_sources(monkeypatch, levels={}, ranks={"runs": 45, "locations": 122, "wins": 7}, milestones=[])
    assert activity.weekly_ratings_message(db_session, user.id) == {"rating_moves": 2, "queued": True}
    delivery = db_session.query(NotificationDelivery).filter_by(user_id=user.id, kind="ratings").one()
    assert delivery.payload["title"] == "📊 Рейтинги за неделю"
    assert delivery.payload["text"] == "«Пробежки» — **45-е место** (▲3)\n«Локации» — **122-е место** (▼2)"
    assert delivery.payload["url"].endswith("/ratings")

    # Без движения — тишина; вид можно выключить.
    assert activity.weekly_ratings_message(db_session, user.id) == {"rating_moves": 0, "queued": False}
    notify.update_prefs(db_session, user.id, kinds={"ratings": False})
    _fake_sources(monkeypatch, levels={}, ranks={"runs": 1}, milestones=[])
    assert activity.weekly_ratings_message(db_session, user.id) == {"skipped": "kind off"}


def test_scan_first_snapshots_are_silent_and_old_runs_ignored(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = _make_user(db_session, chat_id=100)
    _on(db_session, user)
    prefs = notify.get_prefs(db_session, user.id)
    assert prefs is not None
    prefs.runs_notified_through = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()
    today = datetime.now(UTC).date()
    _run_for(db_session, user, event_date=today - timedelta(days=60))
    _fake_sources(
        monkeypatch,
        levels={"seconds": {"easy": "gold"}},
        ranks={"runs": 3},
        milestones=[{"kind": "first_run", "event_date": today}],
    )

    assert activity.scan_user_activity(db_session, user.id)["queued"] is False
    db_session.refresh(prefs)
    assert prefs.challenge_levels == {"seconds": {"easy": "gold"}}
    assert prefs.milestones_seen == [activity.milestone_key({"kind": "first_run", "event_date": today})]


def test_scan_levels_only_message(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100)
    _enable_with_seeds(db_session, user, levels={"positions": {"easy": None}}, ranks={}, keys=[])
    _fake_sources(monkeypatch, levels={"positions": {"easy": "bronze"}}, ranks={}, milestones=[])
    assert activity.scan_user_activity(db_session, user.id)["queued"] is True
    delivery = db_session.query(NotificationDelivery).filter_by(user_id=user.id).one()
    assert delivery.payload["title"] == "🏆 Новый уровень в челлендже"
    assert "постер" not in delivery.payload["text"]


def test_scan_skips_disabled(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(db_session, chat_id=100)
    called: list[UUID] = []
    monkeypatch.setattr(activity, "compute_challenges", lambda db, user_id: called.append(user_id) or {})
    assert activity.scan_user_activity(db_session, user.id) == {"skipped": "disabled"}
    assert called == []


def test_markup_renders_bold_link_label() -> None:
    """Название площадки — и ссылка, и жирное: `[**Имя**](url)`."""
    text = "📍 [**Мещерский**](https://run5k.test/locations/m) · 5 вёрст"
    assert to_telegram_html(text) == ('📍 <a href="https://run5k.test/locations/m"><b>Мещерский</b></a> · 5 вёрст')
    # Адрес — отдельной строкой: иначе он разрывает фразу пополам.
    assert to_plain(text) == "📍 Мещерский · 5 вёрст\nhttps://run5k.test/locations/m"
