"""Имена очередей Celery.

Задачи 5 вёрст разведены на две группы, которые обслуживает ОДИН воркер
(`worker-five-verst`, `-Q five_verst_fresh,five_verst`):

* `five_verst_fresh` — свежесть: то, что показывает людям результаты сегодняшнего
  дня. Сюда попадает только `sync_latest_results` и точечная перечитка локации по
  команде админа. Очередь обязана оставаться короткой.
* `five_verst` — фон: сверка истории, обход недели, ротация локаций, реестр,
  клубы, доборка протоколов из профилей. Часы работы, никто не ждёт.

Воркер забирает очереди строго по порядку (`queue_order_strategy: priority` в
broker_transport_options), поэтому фоновая задача берётся, только когда в
`five_verst_fresh` пусто. Второй воркер не заводим намеренно: к 5verst.ru должен
ходить один запрос за раз, и общий Redis-лок — не повод удваивать число фетчеров.

Новая задача по умолчанию едет в ФОН. В `five_verst_fresh` попадает только то,
без чего сайт показывает людям вчерашний день.
"""

from __future__ import annotations

FIVE_VERST_FRESH_QUEUE = "five_verst_fresh"
FIVE_VERST_BATCH_QUEUE = "five_verst"
FIVE_VERST_USER_QUEUE = "five_verst_user"
S95_BATCH_QUEUE = "s95"
S95_USER_QUEUE = "s95_user"
PARKRUN_SYNC_QUEUE = "parkrun"
RUNPARK_SYNC_QUEUE = "runpark"

# Порядок = приоритет: воркер five_verst стартует с этим списком в -Q.
FIVE_VERST_WORKER_QUEUES: tuple[str, ...] = (FIVE_VERST_FRESH_QUEUE, FIVE_VERST_BATCH_QUEUE)
