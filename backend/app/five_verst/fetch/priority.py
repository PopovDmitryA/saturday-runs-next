"""Приоритет пользовательских синков 5 вёрст над батчами.

Батч (сверка истории, ротация локаций) идёт часами, и пользователь, нажавший
«обновить», не должен ждать его окончания. У S95 та же задача решена
прерыванием: батч бросает исключение и переставляется в конец очереди
(`app/s95/fetch/priority.py`). Здесь другой приём — **пауза**: батч не теряет
прогресс, а замирает между фетчами, пока пользовательский синк не отработает.

Работает это только потому, что у пользовательской очереди свой воркер
(`worker-five-verst-user`): внутри одного процесса с concurrency=1 пауза была
бы бесполезна — процесс всё равно не пошёл бы за следующей задачей. Оба
воркера ходят к 5verst.ru через общий Redis-лок, так что одновременных
запросов к сайту по-прежнему не бывает.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.core.redis_client import get_redis_client
from app.core.request_cancel import interruptible_sleep

logger = logging.getLogger(__name__)

FIVE_VERST_USER_QUEUE = "five_verst_user"
# Живой пользовательский синк. Ключ с TTL, а не счётчик: если воркер умрёт
# посреди синка, батч разморозится сам, без ручной чистки.
USER_ACTIVE_KEY = "five_verst:user_sync:active"

_user_sync_active: ContextVar[bool] = ContextVar("five_verst_user_sync_active", default=False)


def five_verst_user_sync_active() -> bool:
    """Текущая задача — пользовательский синк (её саму тормозить нельзя)."""
    return _user_sync_active.get()


def mark_user_sync_alive(ttl_seconds: int) -> None:
    """Продлить отметку «идёт пользовательский синк»."""
    get_redis_client().set(USER_ACTIVE_KEY, "1", ex=ttl_seconds)


@contextmanager
def five_verst_user_sync_context(ttl_seconds: int = 180) -> Iterator[None]:
    """Пометить блок как пользовательский синк — батчи встают на паузу."""
    token = _user_sync_active.set(True)
    mark_user_sync_alive(ttl_seconds)
    try:
        yield
    finally:
        _user_sync_active.reset(token)
        try:
            get_redis_client().delete(USER_ACTIVE_KEY)
        except Exception:  # noqa: BLE001 — снятие отметки не должно ронять синк
            logger.warning("5verst: не удалось снять отметку пользовательского синка", exc_info=True)


def user_sync_in_progress() -> bool:
    return bool(get_redis_client().exists(USER_ACTIVE_KEY))


def user_queue_has_pending() -> bool:
    """Пользовательские задачи ждут своего воркера — тоже повод уступить."""
    try:
        return int(get_redis_client().llen(FIVE_VERST_USER_QUEUE) or 0) > 0
    except Exception:  # noqa: BLE001 — недоступный Redis не должен блокировать батч
        logger.warning("5verst: не удалось прочитать длину очереди пользователей", exc_info=True)
        return False


def wait_for_user_sync_window(*, max_wait_seconds: int, reason: str = "batch") -> float:
    """Пауза батча, пока идут (или ждут очереди) пользовательские синки.

    Возвращает, сколько секунд простояли. Пауза берётся ДО захвата фетч-лока —
    иначе батч морозил бы сам себя: пользователь ждал бы лок, который держит
    ждущий пользователя батч.

    `max_wait_seconds` — потолок на случай, если воркер пользовательской очереди
    не поднят или залип: батч не должен вставать навсегда из-за копящейся
    очереди, которую некому разобрать.
    """
    if _user_sync_active.get():
        return 0.0

    started = time.time()
    deadline = started + max_wait_seconds
    logged = False
    while time.time() < deadline:
        if not user_sync_in_progress() and not user_queue_has_pending():
            break
        if not logged:
            logger.info("5verst batch (%s): пауза — идёт пользовательский синк", reason)
            logged = True
        interruptible_sleep(1.0)
    else:
        logger.warning(
            "5verst batch (%s): пользовательский синк не уступил за %s с — продолжаем",
            reason,
            max_wait_seconds,
        )

    waited = time.time() - started
    if logged and waited > 0:
        logger.info("5verst batch (%s): продолжаем после паузы %.0f с", reason, waited)
    return waited
