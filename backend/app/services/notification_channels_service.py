"""Каналы личных уведомлений: Telegram-бот, сообщество VK и почта.

Канал привязан к способу входа: включил уведомления на Telegram — появилась
строка канала с chat_id, на VK — с id пользователя VK, на почте — с адресом.
Нет ни одной включённой строки — сайт молчит (умолчание выключено).

Отдельно от «включил» живёт «дойдёт ли»: бот может быть заблокирован или ни
разу не запущен, сообщество VK — без разрешения писать. Это проверяется без
сообщения человеку: Telegram — sendChatAction (короткое «печатает…», ошибка
403/400 значит «писать нельзя»), VK — messages.isMessagesFromGroupAllowed.
Результат лежит на строке канала (check_ok / check_error) и перепроверяется
не чаще раза в CHECK_TTL, чтобы настройки не долбили внешние API.

Порядок доставки: основной канал из настроек, затем остальные в порядке
CHANNEL_ORDER (мессенджеры быстрее почты). Доставку по этому порядку делает
notification_service.deliver_now.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import redis
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.core import email_address
from app.core.mailer import is_configured as mailer_is_configured
from app.core.redis_client import get_redis_client
from app.core.runtime_env import is_test_run
from app.core.security import generate_token
from app.models import AuthIdentity, AuthProvider, User, UserNotificationChannel
from app.services import vk_client

logger = logging.getLogger(__name__)

CHANNEL_TELEGRAM = "telegram"
CHANNEL_VK = "vk"
CHANNEL_EMAIL = "email"
# Порядок по умолчанию = порядок резервов: мессенджер доходит быстрее письма.
CHANNEL_ORDER: tuple[str, ...] = (CHANNEL_TELEGRAM, CHANNEL_VK, CHANNEL_EMAIL)
CHANNEL_TITLES: dict[str, str] = {
    CHANNEL_TELEGRAM: "Telegram",
    CHANNEL_VK: "VK",
    CHANNEL_EMAIL: "Почта",
}

# Перепроверка доставляемости не чаще раза в десять минут — настройки
# открывают часто, а ответ бота и VK за это время не меняется.
CHECK_TTL = timedelta(minutes=10)

_TG_TOKEN_PREFIX = "notify:tg:v1:"
_TG_TOKEN_TTL_SECONDS = 30 * 60
_VK_GROUP_CACHE_KEY = "notify:vk:group:v1"
_VK_GROUP_CACHE_TTL = 24 * 3600


class ChannelError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ChannelTarget:
    """Куда слать: канал и адрес в нём (chat_id, id VK, почта)."""

    channel: str
    target: str


@dataclass(frozen=True)
class CheckOutcome:
    ok: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Чтение


def get_channel(db: Session, user_id: UUID, channel: str) -> UserNotificationChannel | None:
    return (
        db.query(UserNotificationChannel)
        .filter(UserNotificationChannel.user_id == user_id, UserNotificationChannel.channel == channel)
        .one_or_none()
    )


def list_channels(db: Session, user_id: UUID) -> dict[str, UserNotificationChannel]:
    rows = db.query(UserNotificationChannel).filter(UserNotificationChannel.user_id == user_id).all()
    return {row.channel: row for row in rows}


def _identities(db: Session, user_id: UUID) -> list[AuthIdentity]:
    return db.query(AuthIdentity).filter(AuthIdentity.user_id == user_id).order_by(AuthIdentity.linked_at.asc()).all()


def _vk_identity(db: Session, user_id: UUID) -> AuthIdentity | None:
    return next((i for i in _identities(db, user_id) if i.provider == AuthProvider.vk), None)


def user_email(db: Session, user_id: UUID) -> str | None:
    """Адрес для писем: почтовая привязка, иначе адрес от Яндекса."""
    identities = _identities(db, user_id)
    for identity in identities:
        if identity.provider == AuthProvider.email and identity.email:
            return identity.email
    for identity in identities:
        if identity.email:
            return identity.email
    return None


def email_source_provider(db: Session, user_id: UUID) -> str | None:
    """Какой способ входа дал адрес: email | yandex | None."""
    identities = _identities(db, user_id)
    for identity in identities:
        if identity.provider == AuthProvider.email and identity.email:
            return "email"
    for identity in identities:
        if identity.email:
            return identity.provider.value
    return None


def telegram_address(db: Session, user: User) -> str | None:
    """Куда бот может писать: chat_id, если бот его знает, иначе id самого
    пользователя — у личного чата в Telegram они совпадают, и если человек
    хоть раз запускал бота, сообщение дойдёт. Проверка покажет."""
    row = get_channel(db, user.id, CHANNEL_TELEGRAM)
    if row is not None:
        return row.external_id
    if user.telegram_chat_id:
        return str(user.telegram_chat_id)
    if user.telegram_id:
        return str(user.telegram_id)
    return None


def _address_for(db: Session, user: User, channel: str) -> str | None:
    if channel == CHANNEL_TELEGRAM:
        return telegram_address(db, user)
    if channel == CHANNEL_VK:
        identity = _vk_identity(db, user.id)
        return identity.external_id if identity is not None else None
    if channel == CHANNEL_EMAIL:
        address = user_email(db, user.id)
        return email_address.normalize(address) if address else None
    return None


def resolve_targets(db: Session, user: User, *, primary: str | None) -> list[ChannelTarget]:
    """Каналы в порядке доставки: включённые человеком, основной первым.

    Проверка доставляемости здесь не учитывается: воркер попробует сам, а
    неудача честно ляжет в last_error и уведёт сообщение в следующий канал.
    """
    rows = list_channels(db, user.id)
    order = list(CHANNEL_ORDER)
    if primary in order:
        order.remove(primary)
        order.insert(0, primary)
    targets: list[ChannelTarget] = []
    for channel in order:
        row = rows.get(channel)
        if row is None or not row.enabled:
            continue
        targets.append(ChannelTarget(channel=channel, target=row.external_id))
    return targets


def enabled_channels(db: Session, user_id: UUID) -> list[str]:
    return [c for c, row in list_channels(db, user_id).items() if row.enabled]


def is_enabled(db: Session, user_id: UUID) -> bool:
    """Уведомления включены = есть хоть один включённый канал."""
    return bool(enabled_channels(db, user_id))


# ---------------------------------------------------------------------------
# Проверка доставляемости без сообщения человеку


def check_telegram(chat_id: str, settings: Settings) -> CheckOutcome:
    """sendChatAction: бот на секунду показывает «печатает…», в ответ 200 —
    писать можно; 403 (заблокирован) или 400 (чата нет — Start не нажимали)
    — нельзя. Сообщения человек не видит."""
    if is_test_run():
        return CheckOutcome(ok=False, error="test run")
    if not settings.telegram_bot_token:
        return CheckOutcome(ok=False, error="bot token not configured")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendChatAction"
    try:
        response = httpx.post(
            url,
            json={"chat_id": chat_id, "action": "typing"},
            proxy=settings.telegram_proxy_url or None,
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        return CheckOutcome(ok=False, error=f"network: {exc}"[:200])
    if response.status_code == 200:
        return CheckOutcome(ok=True)
    return CheckOutcome(ok=False, error=f"telegram {response.status_code}")


def check_vk(user_vk_id: str, settings: Settings) -> CheckOutcome:
    if is_test_run():
        return CheckOutcome(ok=False, error="test run")
    if not settings.vk_bot_group_token:
        return CheckOutcome(ok=False, error="vk group token not configured")
    group = vk_group()
    if group is None:
        return CheckOutcome(ok=False, error="vk group unavailable")
    try:
        allowed = vk_client.vk_messages_allowed(
            settings.vk_bot_group_token, group_id=int(group["id"]), user_id=int(user_vk_id)
        )
    except Exception as exc:  # noqa: BLE001 — сеть/VK: фиксируем и не роняем настройки
        return CheckOutcome(ok=False, error=f"vk: {exc}"[:200])
    return CheckOutcome(ok=allowed, error=None if allowed else "vk messages not allowed")


CHECKERS = {CHANNEL_TELEGRAM: check_telegram, CHANNEL_VK: check_vk}


def _apply_check(row: UserNotificationChannel, outcome: CheckOutcome, now: datetime) -> None:
    row.checked_at = now
    row.check_ok = outcome.ok
    row.check_error = None if outcome.ok else (outcome.error or "unavailable")[:300]


def ensure_checked(db: Session, row: UserNotificationChannel, *, force: bool = False) -> None:
    """Обновить check_ok у строки, если проверка устарела. Почта — всегда ок."""
    now = datetime.now(UTC)
    if row.channel == CHANNEL_EMAIL:
        if row.check_ok is not True:
            _apply_check(row, CheckOutcome(ok=True), now)
        return
    if not force and row.checked_at is not None and now - row.checked_at < CHECK_TTL:
        return
    checker = CHECKERS.get(row.channel)
    if checker is None:
        return
    _apply_check(row, checker(row.external_id, get_settings()), now)


def probe(db: Session, user: User, channel: str) -> CheckOutcome:
    """Проверка канала, у которого ещё нет строки (в настройках до включения)."""
    address = _address_for(db, user, channel)
    if not address:
        return CheckOutcome(ok=False, error="not linked")
    if channel == CHANNEL_EMAIL:
        return CheckOutcome(ok=True)
    key = f"notify:probe:v1:{user.id}:{channel}:{address}"
    client = get_redis_client()
    try:
        cached = client.get(key)
    except redis.RedisError:
        cached = None
    if isinstance(cached, str) and cached:
        ok, _, error = cached.partition("|")
        return CheckOutcome(ok=ok == "1", error=error or None)
    outcome = CHECKERS[channel](address, get_settings())
    try:
        client.setex(key, int(CHECK_TTL.total_seconds()), f"{'1' if outcome.ok else '0'}|{outcome.error or ''}")
    except redis.RedisError:
        pass
    return outcome


# ---------------------------------------------------------------------------
# Включение и выключение


def set_channel_enabled(db: Session, user: User, channel: str, enabled: bool) -> UserNotificationChannel | None:
    """Переключатель «уведомления» у способа входа. Включение заводит строку
    по адресу привязки и сразу проверяет доставляемость."""
    if channel not in CHANNEL_ORDER:
        raise ChannelError("Неизвестный канал")
    row = get_channel(db, user.id, channel)
    if not enabled:
        if row is not None:
            row.enabled = False
        db.flush()
        return row
    address = _address_for(db, user, channel)
    if not address:
        raise ChannelError(_not_linked_message(channel))
    if row is None:
        row = UserNotificationChannel(user_id=user.id, channel=channel, external_id=address)
        db.add(row)
    row.external_id = address
    row.enabled = True
    ensure_checked(db, row, force=True)
    db.flush()
    return row


def _not_linked_message(channel: str) -> str:
    if channel == CHANNEL_TELEGRAM:
        return "Сначала привяжите Telegram в «Способах входа»"
    if channel == CHANNEL_VK:
        return "Сначала привяжите VK в «Способах входа»"
    return "Сначала добавьте почту в «Способах входа»"


def enable_best_channel(db: Session, user: User) -> str | None:
    """Включить лучший доступный канал: Telegram, если бот может писать,
    иначе VK с разрешением, иначе почта. Для баннера «Включить уведомления»
    и колокольчика. None — включать нечего."""
    for channel in CHANNEL_ORDER:
        address = _address_for(db, user, channel)
        if not address:
            continue
        if channel != CHANNEL_EMAIL and not probe(db, user, channel).ok:
            continue
        set_channel_enabled(db, user, channel, True)
        return channel
    return None


def mark_delivery_result(db: Session, user: User, channel: str, *, error: str | None) -> None:
    row = get_channel(db, user.id, channel)
    if row is None:
        return
    now = datetime.now(UTC)
    if error is None:
        row.last_sent_at = now
        row.check_ok = True
        row.check_error = None
        row.checked_at = now
    else:
        row.last_error = error[:500]
        row.last_error_at = now


# ---------------------------------------------------------------------------
# Состояние для настроек


def _bot_username(settings: Settings) -> str:
    return (settings.telegram_login_bot_username or settings.telegram_bot_username).lstrip("@")


def channels_state(db: Session, user: User) -> list[dict[str, Any]]:
    """Карточки каналов для «Способов входа» — в порядке CHANNEL_ORDER.

    `linked` — привязка есть, канал можно включить; `enabled` — включён;
    `deliverable` — проверка прошла (для включённых по строке, для
    выключенных — пробной проверкой, чтобы подсказать красным заранее).
    """
    settings = get_settings()
    rows = list_channels(db, user.id)
    group = vk_group() if settings.vk_bot_group_token else None
    email = user_email(db, user.id)
    items: list[dict[str, Any]] = []
    for channel in CHANNEL_ORDER:
        row = rows.get(channel)
        address = _address_for(db, user, channel)
        available = {
            CHANNEL_TELEGRAM: bool(_bot_username(settings)) and bool(settings.telegram_bot_token),
            CHANNEL_VK: bool(settings.vk_bot_group_token),
            CHANNEL_EMAIL: mailer_is_configured(settings),
        }[channel]
        enabled = row is not None and row.enabled
        deliverable: bool | None = None
        problem: str | None = None
        if available and address:
            if row is not None:
                ensure_checked(db, row)
                deliverable = row.check_ok
            else:
                deliverable = probe(db, user, channel).ok
            if deliverable is False:
                problem = _problem_text(channel, settings)
        items.append(
            {
                "channel": channel,
                "title": CHANNEL_TITLES[channel],
                "available": available,
                "linked": address is not None,
                "enabled": enabled,
                "deliverable": deliverable,
                "problem": problem,
                "label": _label(channel, user, email, group),
                "email_source": email_source_provider(db, user.id) if channel == CHANNEL_EMAIL else None,
                "allow_url": (
                    f"https://vk.me/{group['screen_name']}"
                    if channel == CHANNEL_VK and group and group.get("screen_name")
                    else None
                ),
                "bot_url": f"https://t.me/{_bot_username(settings)}"
                if channel == CHANNEL_TELEGRAM and available
                else None,
                "last_error": _visible_error(row),
            }
        )
    db.flush()
    return items


def _label(channel: str, user: User, email: str | None, group: dict[str, Any] | None) -> str | None:
    if channel == CHANNEL_TELEGRAM:
        return f"@{user.telegram_username}" if user.telegram_username else None
    if channel == CHANNEL_VK:
        return (group or {}).get("name") or None
    return email


def _problem_text(channel: str, settings: Settings) -> str:
    if channel == CHANNEL_TELEGRAM:
        bot = _bot_username(settings)
        return f"Бот @{bot} не может вам писать: откройте его и нажмите «Start», иначе уведомления работать не будут."
    if channel == CHANNEL_VK:
        return "Сообщество не может вам писать: откройте диалог и нажмите «Разрешить сообщения», иначе уведомления работать не будут."
    return "Почта недоступна."


def _visible_error(row: UserNotificationChannel | None) -> str | None:
    """Последняя ошибка доставки — только если после неё не было удачной отправки."""
    if row is None or not row.last_error:
        return None
    if row.last_sent_at and row.last_error_at and row.last_sent_at >= row.last_error_at:
        return None
    return row.last_error


# ---------------------------------------------------------------------------
# Telegram: подключение по ссылке из настроек (для тех, кто входил не ботом)


def telegram_connect_url(user: User) -> str | None:
    """Ссылка t.me/<бот>?start=notify_<токен>; токен живёт полчаса в Redis."""
    settings = get_settings()
    bot = _bot_username(settings)
    if not bot:
        return None
    token = generate_token()
    try:
        get_redis_client().setex(f"{_TG_TOKEN_PREFIX}{token}", _TG_TOKEN_TTL_SECONDS, str(user.id))
    except redis.RedisError:
        logger.exception("notify: failed to store telegram connect token")
        return None
    return f"https://t.me/{bot}?start=notify_{token}"


def confirm_telegram_channel(db: Session, token: str, *, telegram_id: int, chat_id: int) -> User | None:
    """Бот сообщил chat_id по ссылке из настроек: канал включён и проверен."""
    key = f"{_TG_TOKEN_PREFIX}{token}"
    try:
        raw = get_redis_client().get(key)
    except redis.RedisError:
        raw = None
    if not raw:
        return None
    user = db.get(User, UUID(str(raw)))
    if user is None:
        return None
    now = datetime.now(UTC)
    row = get_channel(db, user.id, CHANNEL_TELEGRAM)
    if row is None:
        row = UserNotificationChannel(user_id=user.id, channel=CHANNEL_TELEGRAM, external_id=str(chat_id))
        db.add(row)
    row.external_id = str(chat_id)
    row.enabled = True
    _apply_check(row, CheckOutcome(ok=True), now)
    row.last_error = None
    row.last_error_at = None
    if user.telegram_chat_id is None and user.telegram_id in (None, telegram_id):
        user.telegram_chat_id = chat_id
    db.commit()
    try:
        get_redis_client().delete(key)
    except redis.RedisError:
        pass
    return user


# ---------------------------------------------------------------------------
# VK: сообщество


def vk_group() -> dict[str, Any] | None:
    settings = get_settings()
    if settings.vk_bot_group_id and settings.vk_bot_group_screen_name:
        return {"id": settings.vk_bot_group_id, "screen_name": settings.vk_bot_group_screen_name, "name": ""}
    if not settings.vk_bot_group_token:
        return None
    client = get_redis_client()
    try:
        cached = client.hgetall(_VK_GROUP_CACHE_KEY)
    except redis.RedisError:
        cached = None
    if isinstance(cached, dict) and cached.get("id"):
        return {"id": int(cached["id"]), "screen_name": cached.get("screen_name", ""), "name": cached.get("name", "")}
    try:
        info = vk_client.vk_group_info(settings.vk_bot_group_token)
    except Exception:  # noqa: BLE001 — VK недоступен: канал просто не предлагаем
        logger.warning("notify: vk group info failed", exc_info=True)
        return None
    if info is None:
        return None
    try:
        client.hset(_VK_GROUP_CACHE_KEY, mapping={k: str(v) for k, v in info.items()})
        client.expire(_VK_GROUP_CACHE_KEY, _VK_GROUP_CACHE_TTL)
    except redis.RedisError:
        pass
    return info


def recheck_channel(db: Session, user: User, channel: str) -> CheckOutcome:
    """Кнопка «Проверить ещё раз»: сбросить кэш проверки и спросить заново."""
    row = get_channel(db, user.id, channel)
    if row is not None:
        ensure_checked(db, row, force=True)
        db.flush()
        return CheckOutcome(ok=bool(row.check_ok), error=row.check_error)
    address = _address_for(db, user, channel)
    if not address:
        return CheckOutcome(ok=False, error="not linked")
    try:
        get_redis_client().delete(f"notify:probe:v1:{user.id}:{channel}:{address}")
    except redis.RedisError:
        pass
    return probe(db, user, channel)
