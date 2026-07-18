from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone

from app.platform_fetch.cooldown import is_platform_in_cooldown, platform_cooldown_until
from app.services.batch_queue_guard import batch_queue_has_capacity
from app.services.scheduled_run_log_service import record_run
from app.services.scheduled_sync_guard import release_hourly_sync_slot, try_claim_hourly_sync_slot

logger = logging.getLogger(__name__)


def run_reported_sync(
    name: str,
    fn: Callable[[], dict[str, object]],
    *,
    details: str | None = None,
    hour_slot_key: str | None = None,
    force: bool = False,
    batch_queue_name: str | None = None,
    batch_queue_max_depth: int = 120,
) -> dict[str, object]:
    """Запускает задачу автообновления и пишет её итог в историю запусков.

    Раньше отсюда уходили два сообщения в ВК на каждый запуск; теперь запуск
    попадает в `scheduled_run_logs` (админка «Автообновление»), а в ВК уходит
    одна сводка в сутки — admin_digest.daily_sync_summary.
    """
    started_at = datetime.now(timezone.utc)

    def _log(payload: dict[str, object], *, failed: bool = False) -> None:
        try:
            record_run(
                name,
                payload,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                failed=failed,
            )
        except Exception:
            logger.exception("Не удалось записать историю запуска %s", name)

    if batch_queue_name and not batch_queue_has_capacity(
        batch_queue_name,
        max_depth=batch_queue_max_depth,
    ):
        payload = {
            "skipped": True,
            "reason": "batch_queue_full",
            "errors": [],
        }
        _log(payload)
        return payload

    if batch_queue_name == "s95" and is_platform_in_cooldown("s95"):
        until = platform_cooldown_until("s95")
        payload = {
            "skipped": True,
            "reason": "s95_fetch_cooldown",
            "cooldown_until": until,
            "errors": [],
        }
        _log(payload)
        return payload

    if hour_slot_key and not try_claim_hourly_sync_slot(hour_slot_key, force=force):
        payload = {
            "skipped": True,
            "reason": "duplicate_hour_slot",
            "errors": [],
        }
        _log(payload)
        return payload

    if details:
        logger.info("Запуск %s: %s", name, details)

    payload: dict[str, object] = {"errors": ["task did not complete"]}
    failed = False
    try:
        payload = fn()
    except Exception as exc:
        if hour_slot_key:
            release_hourly_sync_slot(hour_slot_key)
        payload = {"errors": [str(exc)]}
        failed = True
        raise
    finally:
        _log(payload, failed=failed)
    return payload
