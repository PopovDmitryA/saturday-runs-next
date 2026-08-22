"""Прогрев дашбордов после батч-синка.

Раньше любой упсерт результатов 5 вёрст сносил dashboard_cache всем 500+
пользователям, а пересчёт (десятки секунд) доставался первому, кто откроет
профиль — вплоть до таймаута фронта. Теперь синк говорит «вот с какого момента
я трогал данные», а эта задача сама решает, кого это касается, сносит кэш
только им и тут же пересчитывает в воркере.

Момент старта синка — это floor, а не источник истины: докуда данные уже
разобраны, помнит водяной знак (см. `_covered_through`). Без него окно прогрева
было равно времени работы самого синка, и всё записанное между двумя синками
не попадало ни в одно окно — см. комментарий у `_load_watermark`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db.session import get_session_factory
from app.services.dashboard_service import (
    invalidate_dashboard_cache_for_users,
    locations_touched_since,
    order_users_by_recent_login,
    recompute_dashboard_cache,
    users_holding_location_records,
    users_with_touched_results,
)
from app.services.location_records_service import warm_location_progressions
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Потолок на один прогон: синк, перезабравший разом много протоколов, не должен
# занимать воркер часами. Кэш всё равно снесён всем затронутым — непрогретые
# просто пересчитаются при заходе (секунды, а не десятки секунд, потому что
# прогрессии площадок к тому моменту уже в общем кэше).
MAX_USERS_PER_RUN = 200

# Докуда прогрев уже разобрал run_results.fetched_at. Redis, а не БД: значение
# чисто операционное, и его потеря не ломает данные — следующий прогон просто
# возьмёт окно пошире (dashboard_warm_max_lookback_hours).
WATERMARK_KEY = "dashboard:warm:covered_through"


def schedule_dashboard_warm(started_at: datetime) -> None:
    """Отдать прогрев дашбордов в фоновую задачу.

    Зовётся из каждого батч-синка, который пишет результаты, — а не только из
    5 вёрст, как было до 08.08.2026. Тогда S95/RunPark прогрев не планировали
    вовсе, и их забеги попадали в чужое окно только по случайности.
    """
    warm_dashboards_after_sync.delay(started_at.isoformat())


def _load_watermark(fallback: datetime) -> datetime:
    """Момент, с которого читать run_results.fetched_at.

    Водяной знак делает окна прогрева непрерывными. Раньше `since` был моментом
    старта синка, то есть окно = «пока синк работал», а промежутки между синками
    не покрывал никто: результат, записанный в такой промежуток (а результаты
    S95/RunPark пишутся именно там — своим расписанием), не сбрасывал кэш уже
    никогда. 08.08.2026 строку у 27 человек так и потеряли — ближайшее окно
    начиналось на 37 секунд позже её fetched_at.
    """
    from app.config import get_settings

    floor = fallback - timedelta(hours=get_settings().dashboard_warm_max_lookback_hours)
    try:
        from app.core.redis_client import get_redis_client

        raw = get_redis_client().get(WATERMARK_KEY)
    except Exception:
        logger.exception("dashboard warm: watermark read failed, falling back to sync start")
        return fallback
    if not raw:
        return fallback
    try:
        stored = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("dashboard warm: broken watermark %r, falling back to sync start", raw)
        return fallback
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    # Знак может отставать надолго (воркер стоял) — не даём ему развернуть скан
    # на всю историю. Хвост подберёт ленивый пересчёт по возрасту кэша.
    return max(stored, floor)


def _save_watermark(covered_through: datetime) -> None:
    try:
        from app.core.redis_client import get_redis_client

        get_redis_client().set(WATERMARK_KEY, covered_through.isoformat())
    except Exception:
        # Не откатываем прогон: кэш уже снесён и пересчитан. Следующий прогон
        # просто возьмёт окно от старта своего синка, как до водяного знака.
        logger.exception("dashboard warm: watermark write failed")


@celery_app.task(name="dashboard_warm.after_sync")
def warm_dashboards_after_sync(since_iso: str) -> dict[str, object]:
    """Пересчитать дашборды тех, кого затронул синк.

    Очередь по умолчанию (сервис worker): задача упирается в БД и CPU, а не в
    сеть, и не должна вставать в хвост за фетчами в five_verst — там
    concurrency=1 и приоритет у пользовательских синков.
    """
    since = _load_watermark(datetime.fromisoformat(since_iso))
    # Курсор снимаем ДО запросов: строки, записанные пока мы считаем, получат
    # fetched_at >= cursor и достанутся следующему прогону, а не потеряются.
    cursor = datetime.now(timezone.utc)
    db = get_session_factory()()
    warmed = 0
    failed = 0
    skipped = 0
    try:
        locations = locations_touched_since(db, since)
        # Свои тронутые результаты плюс держатели рекордов: последним рекорд
        # мог перебить кто-то другой, и их дашборд устареет без их участия.
        user_ids = users_with_touched_results(db, since)
        if locations:
            user_ids |= users_holding_location_records(db)

        # Прогрессии — общие для всех, кто бегал на площадке, поэтому греем их
        # один раз до пересчёта дашбордов, а не внутри каждого.
        scopes = warm_location_progressions(db, locations)
        db.rollback()

        invalidate_dashboard_cache_for_users(db, user_ids)
        db.commit()
        # Двигаем знак только после успешного сброса кэша: пересчёт ниже — это
        # уже оптимизация, его падение чинится ленивым пересчётом при заходе.
        _save_watermark(cursor)

        ordered = order_users_by_recent_login(db, user_ids)
        skipped = max(0, len(ordered) - MAX_USERS_PER_RUN)
        if skipped:
            logger.warning(
                "dashboard warm capped at %d users, %d left for lazy recompute",
                MAX_USERS_PER_RUN,
                skipped,
            )
        for user_id in ordered[:MAX_USERS_PER_RUN]:
            try:
                recompute_dashboard_cache(db, user_id)
                db.commit()
                warmed += 1
            except Exception:
                logger.exception("dashboard warm failed for user %s", user_id)
                db.rollback()
                failed += 1
    finally:
        db.close()

    result: dict[str, object] = {
        "since": since.isoformat(),
        "locations": len(locations),
        "scopes_computed": scopes,
        "users": len(user_ids),
        "warmed": warmed,
        "failed": failed,
        "skipped": skipped,
    }
    logger.info("dashboards warmed after sync: %s", result)
    return result
