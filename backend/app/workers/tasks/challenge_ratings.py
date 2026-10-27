from __future__ import annotations

import json
import logging
import subprocess
import sys

from app.workers.celery_app import celery_app
from app.workers.queues import WARM_QUEUE
from app.workers.tasks.sync_task_reporting import run_reported_sync
from app.workers.time_limits import LIMITS_MEDIUM

logger = logging.getLogger(__name__)

# Склейка: пока ключ жив, новый синк пересчёт не планирует — субботняя серия
# синков иначе выстроила бы очередь одинаковых проходов (как у прогрева
# рейтингов, leaderboards_warm._DEBOUNCE_KEY).
_DEBOUNCE_KEY = "challenge_ratings:rebuild:scheduled"
# «Пересчёт идёт»: два прохода разом заменили бы одни и те же снимки, и второй
# упал бы на ключе таблицы. TTL — страховка от потерянного замка.
_RUNNING_KEY = "challenge_ratings:rebuild:running"
_RUNNING_TTL_SECONDS = 30 * 60
# Потолок самого прохода: на проде 27.10.2026 вся сетка считалась ~2,5 мин.
# Ниже soft-лимита задачи (20 мин), чтобы Celery не убил её раньше нас.
_SUBPROCESS_TIMEOUT_SECONDS = 18 * 60


def schedule_challenge_ratings_rebuild() -> bool:
    """Разбудить пересчёт после синка, записавшего протоколы.

    Зовётся из schedule_leaderboards_warm — рядом с прогревом остальных
    рейтингов: до этого (аудит 27.10.2026) таблицы челленджей ждали расписания
    до двух часов, а карточка в «Моих достижениях» уже показывала новое число.
    """
    from app.config import get_settings

    settings = get_settings()
    window = settings.leaderboards_warm_debounce_seconds
    try:
        from app.core.redis_client import get_redis_client

        claimed = get_redis_client().set(_DEBOUNCE_KEY, "1", nx=True, ex=window)
    except Exception:
        logger.exception("challenge ratings: debounce check failed, scheduling anyway")
        claimed = True
    if not claimed:
        return False
    rebuild_challenge_ratings_task.apply_async(
        queue=WARM_QUEUE,
        countdown=settings.leaderboards_warm_delay_seconds,
        expires=window,
    )
    return True


# Очередь warm — к остальным пересчётам рейтингов: проход по протоколам всех
# участников тяжёлый по базе и живёт рядом с ней.
@celery_app.task(name="challenge_ratings.rebuild", queue=WARM_QUEUE, **LIMITS_MEDIUM)
def rebuild_challenge_ratings_task() -> dict[str, object]:
    return run_reported_sync("пересчёт рейтингов челленджей", _rebuild)


def _acquire_running_lock() -> bool:
    try:
        from app.core.redis_client import get_redis_client

        return bool(get_redis_client().set(_RUNNING_KEY, "1", nx=True, ex=_RUNNING_TTL_SECONDS))
    except Exception:
        logger.exception("challenge ratings: lock failed, running without it")
        return True


def _release_running_lock() -> None:
    try:
        from app.core.redis_client import get_redis_client

        get_redis_client().delete(_RUNNING_KEY)
    except Exception:
        logger.exception("challenge ratings: lock release failed")


def _rebuild() -> dict[str, object]:
    """Проход — отдельным процессом.

    Он держит ~0,4 ГБ, и процесс воркера Celery после задачи эту память не
    отдал бы ОС: каждый из трёх процессов, слушающих warm, так и висел бы с
    ней (аудит 27.10.2026). Отдельный процесс отдаёт всё, завершаясь.
    """
    if not _acquire_running_lock():
        logger.info("challenge ratings rebuild skipped: another run in progress")
        return {"skipped": True, "reason": "already_running"}
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "app.services.challenge_rating_service"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        _release_running_lock()
    if completed.returncode != 0:
        tail = (completed.stderr or "").strip().splitlines()[-20:]
        raise RuntimeError("пересчёт рейтингов челленджей упал:\n" + "\n".join(tail))
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    result = json.loads(lines[-1]) if lines else {}
    logger.info("challenge ratings rebuilt: %s", result)
    return {
        "challenge_snapshots": int(result.get("snapshots", 0)),
        "p_index_entrants": int(result.get("p_index_entrants", 0)),
    }
