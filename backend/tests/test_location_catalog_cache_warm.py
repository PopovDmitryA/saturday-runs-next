"""Сброс снимков каталога локаций ставит прогрев, а не оставляет дыру.

QRY-LOCATIONS-01: раньше синк гасил ключ каталога и уходил, и сборку индекса
(5–15 с) оплачивал первый же посетитель. Теперь после сброса в очередь warm
уходит задача прогрева — с дебаунсом, чтобы серия сбросов за один прогон синка
не наплодила одинаковых задач.
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis

from app.services.location_catalog_cache import (
    WARM_COUNTDOWN_SECONDS,
    WARM_DEBOUNCE_KEY,
    flush_location_catalog_caches,
)


def test_flush_schedules_warm_once(fake_redis: fakeredis.FakeRedis) -> None:
    with patch("app.workers.tasks.locations_warm.warm_locations_cache.apply_async") as apply_async:
        flush_location_catalog_caches("тест: реестр")
        flush_location_catalog_caches("тест: отмены")

    assert apply_async.call_count == 1
    # countdown: к старту задачи транзакция синка уже закоммичена.
    assert apply_async.call_args.kwargs["countdown"] == WARM_COUNTDOWN_SECONDS
    assert fake_redis.get(WARM_DEBOUNCE_KEY) == "тест: реестр"


def test_flush_survives_broker_failure(fake_redis: fakeredis.FakeRedis) -> None:
    """Брокер недоступен — синк не падает, просто прогрева не будет."""
    with patch(
        "app.workers.tasks.locations_warm.warm_locations_cache.apply_async",
        side_effect=RuntimeError("broker down"),
    ):
        flush_location_catalog_caches("тест: брокер лежит")
