"""Уведомления сайта: доставка, сканирование активности, подметальщик.

Все задачи — в общей очереди (сервис worker): они упираются в сеть Telegram,
VK и SMTP, а не в очереди синков с concurrency=1.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_

from app.db.session import get_session_factory
from app.models import NotificationDelivery, Participant, PlatformLink, RunResult, UserNotificationChannel
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Строки старше суток подметальщик не трогает: новость о пробежке недельной
# давности уже не новость, а падающий канал за сутки не починится сам.
RETRY_MAX_AGE = timedelta(hours=24)
# Пауза перед повтором: сразу после падения канал, скорее всего, ещё лежит.
RETRY_MIN_AGE = timedelta(minutes=2)
RETRY_BATCH = 200
SCAN_CHUNK = 50
# После батч-синка сканер ждёт, пока прогреются рейтинги (leaderboards.warm_cache
# идёт с задержкой и своей очередью): иначе место в рейтинге посчитается по
# старому снимку и «поднялись на N позиций» уедет в следующее сообщение.
BATCH_SCAN_DELAY_SECONDS = 20 * 60
# Докуда beat-сканер разобрал run_results.created_at. В Redis: операционная
# отметка, потеря не страшна — следующий прогон возьмёт окно из fallback.
WATERMARK_KEY = "notifications:scan:covered_through"
WATERMARK_FALLBACK = timedelta(hours=2)


@celery_app.task(name="notifications.deliver")
def deliver(delivery_id: str) -> str:
    from app.services.notification_service import deliver_now

    db = get_session_factory()()
    try:
        return deliver_now(db, UUID(delivery_id))
    finally:
        db.close()


@celery_app.task(name="notifications.scan_activity")
def scan_activity(user_ids: list[str]) -> dict[str, int]:
    """Новые пробежки, рейтинги, уровни и вехи у перечисленных людей."""
    from app.services.activity_notification_service import scan_user_activity

    db = get_session_factory()()
    scanned = 0
    queued = 0
    failed = 0
    try:
        for raw in user_ids:
            try:
                result = scan_user_activity(db, UUID(raw))
                scanned += 1
                queued += int(bool(result.get("queued")))
            except Exception:
                logger.exception("notify: activity scan failed for user %s", raw)
                db.rollback()
                failed += 1
    finally:
        db.close()
    summary = {"scanned": scanned, "queued": queued, "failed": failed}
    if queued or failed:
        logger.info("notify: activity scan %s", summary)
    return summary


def schedule_activity_scan(user_ids: Iterable[UUID], *, delay_seconds: int = 0) -> int:
    """Отдать сканер воркеру пачками. Брокер лёг — просто пропускаем: beat-сканер
    подберёт тех же людей по водяному знаку, а пробежка не потеряется."""
    ids = [str(user_id) for user_id in user_ids]
    if not ids:
        return 0
    try:
        for start in range(0, len(ids), SCAN_CHUNK):
            scan_activity.apply_async(args=[ids[start : start + SCAN_CHUNK]], countdown=delay_seconds or None)
    except Exception:  # noqa: BLE001 — недоступность Redis не должна ронять синк
        logger.exception("notify: failed to schedule activity scan for %d users", len(ids))
        return 0
    return len(ids)


def _load_watermark(fallback: datetime) -> datetime:
    try:
        from app.core.redis_client import get_redis_client

        raw = get_redis_client().get(WATERMARK_KEY)
    except Exception:
        logger.exception("notify: watermark read failed")
        return fallback
    if not raw:
        return fallback
    try:
        stored = datetime.fromisoformat(str(raw))
    except ValueError:
        return fallback
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=UTC)
    return max(stored, fallback)


def _save_watermark(value: datetime) -> None:
    try:
        from app.core.redis_client import get_redis_client

        get_redis_client().set(WATERMARK_KEY, value.isoformat())
    except Exception:
        logger.exception("notify: watermark write failed")


@celery_app.task(name="notifications.scan_new_results")
def scan_new_results() -> dict[str, int]:
    """Раз в десять минут: у кого из включивших уведомления появились
    результаты после водяного знака — независимо от того, кто их записал.
    Так ловятся пробежки parkrun, которые пишет Mac-демон мимо воркеров,
    и любой путь, где забыли позвать schedule_activity_scan."""
    now = datetime.now(UTC)
    since = _load_watermark(now - WATERMARK_FALLBACK)
    # Курсор снимаем ДО запроса: записанное во время скана достанется следующему.
    cursor = now
    db = get_session_factory()()
    try:
        rows = (
            db.query(PlatformLink.user_id)
            .select_from(RunResult)
            .join(Participant, RunResult.participant_id == Participant.id)
            .join(
                PlatformLink,
                and_(
                    PlatformLink.platform_id == Participant.platform_id,
                    PlatformLink.external_user_id == Participant.external_user_id,
                ),
            )
            .join(
                UserNotificationChannel,
                and_(
                    UserNotificationChannel.user_id == PlatformLink.user_id, UserNotificationChannel.enabled.is_(True)
                ),
            )
            .filter(RunResult.created_at > since, RunResult.created_at <= cursor)
            .distinct()
            .all()
        )
    finally:
        db.close()
    user_ids = {row[0] for row in rows}
    scheduled = schedule_activity_scan(user_ids)
    _save_watermark(cursor)
    result = {"users": len(user_ids), "scheduled": scheduled}
    if user_ids:
        logger.info("notify: new results scan %s", result)
    return result


@celery_app.task(name="notifications.weekly_ratings")
def weekly_ratings() -> dict[str, int]:
    """Воскресенье днём: движение в рейтингах за неделю всем, у кого включены
    уведомления. Протоколы субботы к этому времени догружены."""
    from app.services.activity_notification_service import weekly_ratings_message

    db = get_session_factory()()
    scanned = 0
    queued = 0
    failed = 0
    try:
        user_ids = [
            row[0]
            for row in db.query(UserNotificationChannel.user_id)
            .filter(UserNotificationChannel.enabled.is_(True))
            .distinct()
            .all()
        ]
        for user_id in user_ids:
            try:
                result = weekly_ratings_message(db, user_id)
                scanned += 1
                queued += int(bool(result.get("queued")))
            except Exception:
                logger.exception("notify: weekly ratings failed for user %s", user_id)
                db.rollback()
                failed += 1
    finally:
        db.close()
    summary = {"scanned": scanned, "queued": queued, "failed": failed}
    logger.info("notify: weekly ratings %s", summary)
    return summary


@celery_app.task(name="notifications.retry_queued")
def retry_queued() -> dict[str, int]:
    """Подобрать застрявшие доставки: queued без задачи (брокер моргнул) и
    failed по временной ошибке, у которых не исчерпаны попытки."""
    from app.services.notification_service import MAX_ATTEMPTS, STATUS_FAILED, STATUS_QUEUED, deliver_now

    now = datetime.now(UTC)
    db = get_session_factory()()
    outcomes: dict[str, int] = {}
    try:
        rows = (
            db.query(NotificationDelivery.id)
            .filter(
                NotificationDelivery.status.in_((STATUS_QUEUED, STATUS_FAILED)),
                NotificationDelivery.attempts < MAX_ATTEMPTS,
                NotificationDelivery.created_at >= now - RETRY_MAX_AGE,
                NotificationDelivery.created_at <= now - RETRY_MIN_AGE,
            )
            .order_by(NotificationDelivery.created_at.asc())
            .limit(RETRY_BATCH)
            .all()
        )
        for (delivery_id,) in rows:
            try:
                status = deliver_now(db, delivery_id)
            except Exception:
                logger.exception("notify: retry failed for delivery %s", delivery_id)
                db.rollback()
                status = "error"
            outcomes[status] = outcomes.get(status, 0) + 1
    finally:
        db.close()
    if outcomes:
        logger.info("notify: retry sweep %s", outcomes)
    return outcomes
