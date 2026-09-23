"""Уведомления сайта: настройки человека, постановка в очередь, доставка.

Схема в три слоя:

1. Событие (комментарий, новая пробежка, смена статуса карточки) зовёт
   `notify_user(...)`. Тот проверяет, что у человека включён хоть один канал
   и этот вид не выключен, заводит строку notification_deliveries (queued) и
   ставит celery-задачу `notifications.deliver`. Повтор того же события
   (тот же dedupe_key) молча отбрасывается.
2. `deliver_now(...)` в воркере перебирает каналы: основной из настроек,
   затем резервные в порядке CHANNEL_ORDER. Первый удачный — стоп. Все
   упали — failed, задача-подметальщик повторит позже. После удачной
   доставки — копия админу (пока включено в конфиге).
3. Отписка — ссылкой в каждом сообщении, без входа на сайт: токен HMAC на
   app_secret_key (как у рассылки новостей), внутри user_id и вид или «all».

Умолчание — ВЫКЛЮЧЕНО: пока человек не включил уведомления на одном из
способов входа (или не нажал колокольчик / кнопку в баннере), ему ничего не
уходит. Текст уведомлений — в разметке app/notification_markup.py.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core.email_templates import notification_email
from app.models import NotificationDelivery, User, UserNotificationPrefs
from app.notification_kinds import KIND_BY_CODE, NOTIFICATION_KINDS, kind_enabled
from app.notification_markup import to_email_html, to_telegram_html
from app.services import notification_channels_service as channels
from app.services.notification_senders import SENDERS, OutgoingMessage, send_telegram_html
from app.services.platform_titles import PLATFORM_TITLES

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# Сколько раз подметальщик перезапускает доставку, упавшую по временной ошибке.
MAX_ATTEMPTS = 3

UNSUBSCRIBE_ALL = "all"
# Служебный вид для проверочного сообщения: в реестре его нет, переключателя
# у него нет, dedupe — по минуте.
KIND_TEST = "test"


class NotificationTokenError(Exception):
    """Ссылка отписки испорчена или подписана не нашим ключом."""


# ---------------------------------------------------------------------------
# Настройки


def get_prefs(db: Session, user_id: UUID) -> UserNotificationPrefs | None:
    return db.get(UserNotificationPrefs, user_id)


def ensure_prefs(db: Session, user_id: UUID) -> UserNotificationPrefs:
    prefs = get_prefs(db, user_id)
    if prefs is None:
        prefs = UserNotificationPrefs(user_id=user_id, kinds={})
        db.add(prefs)
        db.flush()
    return prefs


def is_enabled(db: Session, user_id: UUID) -> bool:
    return channels.is_enabled(db, user_id)


def _arm_watermarks(prefs: UserNotificationPrefs, now: datetime) -> None:
    """При включении — забыть прошлое: не рассказывать о пробежках, уровнях
    и вехах, которые случились, пока уведомления были выключены."""
    prefs.runs_notified_through = now
    prefs.challenge_levels = None
    prefs.ratings_snapshot = None
    prefs.milestones_seen = None


def touch_settings(db: Session, user_id: UUID) -> UserNotificationPrefs:
    prefs = ensure_prefs(db, user_id)
    prefs.settings_touched_at = datetime.now(UTC)
    return prefs


def _seed_after_enable(db: Session, user: User) -> None:
    """Снимок рейтингов сразу при включении — иначе первое воскресное
    сообщение о рейтингах пришло бы только через неделю после следующего."""
    from app.services.activity_notification_service import seed_ratings_snapshot

    seed_ratings_snapshot(db, user)


def set_channel_enabled(db: Session, user: User, channel: str, enabled: bool) -> None:
    """Переключатель у способа входа. Первое включение взводит водяные знаки."""
    was_enabled = channels.is_enabled(db, user.id)
    channels.set_channel_enabled(db, user, channel, enabled)
    prefs = touch_settings(db, user.id)
    if enabled and not was_enabled:
        _arm_watermarks(prefs, datetime.now(UTC))
        _seed_after_enable(db, user)
    db.flush()


def enable_if_untouched(db: Session, user: User) -> str | None:
    """Явное действие человека (колокольчик, кнопка в баннере) включает
    уведомления тому, кто настроек ещё не трогал. Уже выключившему — не
    навязываем. Возвращает включённый канал или None."""
    prefs = get_prefs(db, user.id)
    if channels.is_enabled(db, user.id):
        return None
    if prefs is not None and prefs.settings_touched_at is not None:
        return None
    channel = channels.enable_best_channel(db, user)
    if channel is None:
        return None
    prefs = ensure_prefs(db, user.id)
    _arm_watermarks(prefs, datetime.now(UTC))
    _seed_after_enable(db, user)
    db.flush()
    return channel


def enable_now(db: Session, user: User) -> str | None:
    """Кнопка «Включить уведомления»: лучший доступный канал, отметка
    «трогал настройки». None — включать нечего (нужно разрешить боту писать)."""
    was_enabled = channels.is_enabled(db, user.id)
    channel = channels.enable_best_channel(db, user)
    prefs = touch_settings(db, user.id)
    if channel is not None and not was_enabled:
        _arm_watermarks(prefs, datetime.now(UTC))
        _seed_after_enable(db, user)
    db.flush()
    return channel


def disable_all(db: Session, user: User) -> None:
    for channel in channels.CHANNEL_ORDER:
        channels.set_channel_enabled(db, user, channel, False)
    touch_settings(db, user.id)
    db.flush()


def update_prefs(
    db: Session,
    user_id: UUID,
    *,
    primary_channel: str | None | object = ...,
    kinds: dict[str, bool] | None = None,
    cancellation_platforms: list[str] | None = None,
) -> UserNotificationPrefs:
    prefs = touch_settings(db, user_id)
    if primary_channel is not ...:
        if primary_channel is not None and primary_channel not in channels.CHANNEL_ORDER:
            raise ValueError("Неизвестный канал")
        prefs.primary_channel = primary_channel
    if cancellation_platforms is not None:
        unknown = [code for code in cancellation_platforms if code not in PLATFORM_TITLES]
        if unknown:
            raise ValueError("Неизвестная система")
        # Порядок держим канонический (5 вёрст, S95, parkrun, RunPark), дубли
        # снимаем: список едет в интерфейс как есть.
        prefs.cancellation_platforms = [c for c in PLATFORM_TITLES if c in set(cancellation_platforms)]
    if kinds:
        merged = dict(prefs.kinds or {})
        for code, value in kinds.items():
            kind = KIND_BY_CODE.get(code)
            if kind is None or not kind.available:
                raise ValueError("Неизвестный вид уведомлений")
            merged[code] = bool(value)
        prefs.kinds = merged
    db.flush()
    return prefs


def kinds_state(prefs: UserNotificationPrefs | None) -> list[dict[str, Any]]:
    stored = (prefs.kinds if prefs is not None else None) or {}
    return [
        {
            "code": kind.code,
            "title": kind.title,
            "description": kind.description,
            "enabled": kind_enabled(stored, kind.code),
            "available": kind.available,
        }
        for kind in NOTIFICATION_KINDS
        if kind.available
    ]


def settings_state(db: Session, user: User) -> dict[str, Any]:
    """Всё, что нужно «Способам входа» и модалке «о чём присылать»."""
    prefs = get_prefs(db, user.id)
    return {
        "enabled": channels.is_enabled(db, user.id),
        "primary_channel": prefs.primary_channel if prefs is not None else None,
        "cancellation_platforms": list(prefs.cancellation_platforms or []) if prefs is not None else [],
        "channels": channels.channels_state(db, user),
        "kinds": kinds_state(prefs),
    }


def nudge_state(db: Session, user: User) -> dict[str, Any]:
    """Что показать человеку: призыв включить уведомления или тревогу о том,
    что включённые не доходят.

    `kind`:
      * `enable` — уведомления выключены, есть привязка, из которой можно
        сделать канал, и призыв ещё не закрывали навсегда;
      * `fix_delivery` — уведомления включены, но НИ ОДИН включённый канал не
        может доставить (бот заблокирован, сообщество без разрешения). Это
        не реклама, а поломка, поэтому «больше не напоминать» тут не слушаем;
      * `null` — всё в порядке.
    """
    prefs = get_prefs(db, user.id)
    enabled = channels.is_enabled(db, user.id)
    dismissed = prefs is not None and prefs.nudge_dismissed_at is not None
    linked = [c for c in channels.CHANNEL_ORDER if channels._address_for(db, user, c)]

    if not enabled:
        kind = "enable" if (linked and not dismissed) else None
        return {"kind": kind, "show": kind is not None, "enabled": False, "linked_channels": linked, "broken": []}

    state = channels.channels_state(db, user)
    on = [item for item in state if item["enabled"]]
    broken = [item for item in on if item["deliverable"] is False]
    # Хотя бы один рабочий канал — сообщение дойдёт, тревожить незачем.
    if not broken or len(broken) < len(on):
        return {"kind": None, "show": False, "enabled": True, "linked_channels": linked, "broken": []}
    for item in broken:
        if item["channel"] == channels.CHANNEL_TELEGRAM:
            # Персональная ссылка: по ней бот и спросит разрешение, и вернёт
            # актуальный chat_id, если человек пишет боту с другого аккаунта.
            item["bot_url"] = channels.telegram_connect_url(user) or item["bot_url"]
    return {
        "kind": "fix_delivery",
        "show": True,
        "enabled": True,
        "linked_channels": linked,
        "broken": [
            {
                "channel": item["channel"],
                "title": item["title"],
                "problem": item["problem"],
                "action_url": item["bot_url"] or item["allow_url"],
            }
            for item in broken
        ],
    }


def dismiss_nudge(db: Session, user_id: UUID) -> None:
    prefs = ensure_prefs(db, user_id)
    prefs.nudge_dismissed_at = datetime.now(UTC)
    db.flush()


# ---------------------------------------------------------------------------
# Ссылки отписки


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def make_unsubscribe_token(user_id: UUID, scope: str, secret: str) -> str:
    payload = f"{user_id}:{scope}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{encoded}.{_signature(payload, secret)}"


def parse_unsubscribe_token(token: str, secret: str) -> tuple[UUID, str]:
    """Вернуть (user_id, scope). scope — код вида или «all»."""
    if "." not in token:
        raise NotificationTokenError("Malformed token.")
    encoded, _, signature = token.rpartition(".")
    try:
        padding = "=" * (-len(encoded) % 4)
        payload = base64.urlsafe_b64decode(encoded + padding).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise NotificationTokenError("Malformed token.") from exc
    if not hmac.compare_digest(signature, _signature(payload, secret)):
        raise NotificationTokenError("Bad signature.")
    raw_user, _, scope = payload.partition(":")
    try:
        user_id = UUID(raw_user)
    except ValueError as exc:
        raise NotificationTokenError("Malformed token.") from exc
    if scope != UNSUBSCRIBE_ALL and scope not in KIND_BY_CODE and scope != KIND_TEST:
        raise NotificationTokenError("Unknown scope.")
    return user_id, scope


def unsubscribe_url(settings: Settings, user_id: UUID, scope: str) -> str:
    token = make_unsubscribe_token(user_id, scope, settings.app_secret_key)
    return f"{settings.app_base_url.rstrip('/')}/api/notifications/unsubscribe?token={token}"


def settings_url(settings: Settings) -> str:
    return f"{settings.app_base_url.rstrip('/')}/settings#notifications"


def apply_unsubscribe(db: Session, token: str, settings: Settings) -> tuple[str, str | None]:
    """Применить ссылку отписки. Возвращает (scope, название вида или None).

    Профиль мог исчезнуть — отписка всё равно выглядит успешной: человек своё
    действие совершил, писать ему больше не будем в любом случае.
    """
    user_id, scope = parse_unsubscribe_token(token, settings.app_secret_key)
    kind = KIND_BY_CODE.get(scope)
    user = db.get(User, user_id)
    if user is None:
        return scope, kind.title if kind else None
    if scope in (UNSUBSCRIBE_ALL, KIND_TEST):
        disable_all(db, user)
    else:
        update_prefs(db, user_id, kinds={scope: False})
    db.commit()
    logger.info("notify: unsubscribe %s for user %s", scope, user_id)
    return scope, kind.title if kind else None


# ---------------------------------------------------------------------------
# Постановка в очередь


def notify_user(
    db: Session,
    user: User,
    kind: str,
    *,
    title: str,
    text: str,
    dedupe_key: str,
    url: str | None = None,
    url_label: str = "Открыть на сайте",
    force: bool = False,
    commit: bool = True,
) -> NotificationDelivery | None:
    """Поставить уведомление в очередь. None — не нужно (выключено, повтор).

    text — в разметке notification_markup (`**жирный**`, `[подпись](url)`).
    force — минуя проверки настроек (проверочное сообщение из настроек).
    commit — зафиксировать и сразу отдать воркеру; False оставляет строку в
    транзакции вызывающего, тот сам вызовет enqueue_delivery после commit.
    """
    if not force:
        prefs = get_prefs(db, user.id)
        if not channels.is_enabled(db, user.id) or not kind_enabled(prefs.kinds if prefs else None, kind):
            return None
    delivery = NotificationDelivery(
        user_id=user.id,
        kind=kind,
        dedupe_key=dedupe_key[:128],
        status=STATUS_QUEUED,
        payload={"title": title, "text": text, "url": url, "url_label": url_label},
    )
    try:
        with db.begin_nested():
            db.add(delivery)
            db.flush()
    except IntegrityError:
        logger.info("notify: duplicate %s/%s for user %s skipped", kind, dedupe_key, user.id)
        return None
    if commit:
        db.commit()
        enqueue_delivery(delivery.id)
    return delivery


def enqueue_delivery(delivery_id: UUID) -> bool:
    """Отдать доставку воркеру. Брокер лёг — строка остаётся queued, её
    подберёт подметальщик `notifications.retry_queued`."""
    try:
        from app.workers.tasks.notifications import deliver

        deliver.delay(str(delivery_id))
    except Exception:  # noqa: BLE001 — недоступность Redis не повод ронять событие
        logger.exception("notify: failed to enqueue delivery %s", delivery_id)
        return False
    return True


# ---------------------------------------------------------------------------
# Доставка


def build_message(settings: Settings, delivery: NotificationDelivery) -> OutgoingMessage:
    payload = delivery.payload or {}
    scope = UNSUBSCRIBE_ALL if delivery.kind == KIND_TEST else delivery.kind
    unsub = unsubscribe_url(settings, delivery.user_id, scope)
    settings_link = settings_url(settings)
    title = str(payload.get("title") or "Уведомление run5k.run")
    text = str(payload.get("text") or "")
    url = payload.get("url") or None
    url_label = str(payload.get("url_label") or "Открыть на сайте")
    message = OutgoingMessage(
        title=title,
        text=text,
        url=url,
        url_label=url_label,
        unsubscribe_url=unsub,
        settings_url=settings_link,
    )
    html = notification_email(
        title=title,
        body_html=to_email_html(text),
        text_body=message.email_text(),
        url=url,
        url_label=url_label,
        unsubscribe_url=unsub,
        settings_url=settings_link,
    )
    return OutgoingMessage(
        title=title,
        text=text,
        url=url,
        url_label=url_label,
        unsubscribe_url=unsub,
        settings_url=settings_link,
        html=html,
    )


def deliver_now(db: Session, delivery_id: UUID) -> str:
    """Доставить одну строку: основной канал, затем резервные. Возвращает статус."""
    delivery = db.get(NotificationDelivery, delivery_id)
    if delivery is None:
        return "missing"
    if delivery.status == STATUS_SENT:
        return STATUS_SENT
    user = db.get(User, delivery.user_id)
    if user is None:
        delivery.status = STATUS_SKIPPED
        delivery.error = "user missing"
        db.commit()
        return STATUS_SKIPPED

    prefs = get_prefs(db, user.id)
    # Между событием и доставкой человек мог отписаться — проверяем ещё раз.
    if delivery.kind != KIND_TEST and (
        not channels.is_enabled(db, user.id) or not kind_enabled(prefs.kinds if prefs else None, delivery.kind)
    ):
        delivery.status = STATUS_SKIPPED
        delivery.error = "disabled by user"
        db.commit()
        return STATUS_SKIPPED

    primary = prefs.primary_channel if prefs is not None else None
    targets = channels.resolve_targets(db, user, primary=primary)
    delivery.attempts += 1
    if not targets:
        delivery.status = STATUS_SKIPPED
        delivery.error = "no channel"
        db.commit()
        return STATUS_SKIPPED

    message = build_message(get_settings(), delivery)
    errors: list[str] = []
    transient = False
    for target in targets:
        outcome = SENDERS[target.channel](target.target, message)
        channels.mark_delivery_result(db, user, target.channel, error=outcome.error if not outcome.ok else None)
        delivery.channel = target.channel
        if outcome.ok:
            delivery.status = STATUS_SENT
            delivery.sent_at = datetime.now(UTC)
            delivery.error = None
            db.commit()
            _admin_copy(user, target.channel, message)
            return STATUS_SENT
        errors.append(f"{target.channel}: {outcome.error}")
        transient = transient or not outcome.permanent

    delivery.status = STATUS_FAILED
    delivery.error = "; ".join(errors)[:1000]
    if not transient:
        # Все каналы отвергли навсегда — повторять нечего.
        delivery.attempts = MAX_ATTEMPTS
    db.commit()
    return STATUS_FAILED


def recipient_label(user: User) -> str:
    """Кому ушло — для копии админу: @ник Telegram, иначе имя и номер профиля."""
    if user.telegram_username:
        return f"@{user.telegram_username}"
    name = (user.display_name or "").strip() or "без имени"
    return f"{name} (№{user.serial_id})"


def admin_copy_html(user: User, channel: str, message: OutgoingMessage) -> str:
    """Копия админу: «Сообщение направлено @ник · канал», ниже само сообщение
    ровно в том виде, в каком его увидел человек."""
    head = f"📨 Сообщение направлено {to_telegram_html(recipient_label(user))} · {channels.CHANNEL_TITLES.get(channel, channel)}"
    return f"{head}\n\n{message.telegram_html()}"


def _admin_copy(user: User, channel: str, message: OutgoingMessage) -> None:
    """Дубль доставленного уведомления в админский Telegram (пока включено
    NOTIFICATIONS_ADMIN_COPY). Сбой копии доставку не откатывает."""
    settings = get_settings()
    if not settings.notifications_admin_copy or not settings.telegram_admin_chat_id or not settings.telegram_bot_token:
        return
    try:
        outcome = send_telegram_html(str(settings.telegram_admin_chat_id), admin_copy_html(user, channel, message))
        if not outcome.ok:
            logger.warning("notify: admin copy failed: %s", outcome.error)
    except Exception:  # noqa: BLE001 — копия админу не важнее самой доставки
        logger.exception("notify: admin copy failed for user %s", user.id)


def send_test_notification(db: Session, user: User) -> NotificationDelivery | None:
    """Проверочное сообщение (стенд и отладка) — по тем же каналам и правилам."""
    minute = datetime.now(UTC).strftime("%Y%m%d%H%M")
    return notify_user(
        db,
        user,
        KIND_TEST,
        title="🔔 Проверка уведомлений run5k.run",
        text="Так будут выглядеть уведомления сайта: **главное жирным**, ссылки кликабельны.\n"
        "Если это сообщение пришло — канал работает.",
        dedupe_key=f"test:{minute}",
        url=settings_url(get_settings()),
        url_label="Настройки уведомлений",
        force=True,
    )
