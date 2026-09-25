#!/usr/bin/env python3
"""Перенести состояние Redis сайта с одного сервера на другой (переезд, откат).

Зачем. В Redis лежит не только кэш. Только там живут:
  * сессии (`session:*`) — без них при переезде разлогинятся все;
  * дневная статистика сайта (`stats:day:*`, хранится 400 дней) — её история
    из «Статистики» в админке пропала бы целиком;
  * водяной знак рассылки уведомлений (`notifications:scan:covered_through`),
    прогревов (`dashboard:warm:covered_through`), курсоры ротации 5 вёрст и S95,
    лестница охлаждения parkrun, блокировки abuse, лимиты регистраций.

Что НЕ переносим: очереди и служебные ключи Celery (у каждого брокера свои:
чужие задачи выполнились бы второй раз) и кэш прогрессии рекордов
`locrec:prog:*` (~144 тыс. ключей, больше всего объёма; пересчитается сам).
Остальной кэш едет — сайт на новом месте стартует тёплым, а посчитан он по
той же замороженной базе, что уезжает дампом.

Запуск (внутри контейнера api — там есть пакет redis), источник и приёмник
явно, чтобы не перепутать направление:

    docker compose ... run --rm -T --no-deps \\
        -e SRC_REDIS_URL=redis://100.93.200.8:6379/0 \\
        -e DST_REDIS_URL=redis://redis:6379/0 \\
        api python scripts/copy_redis_state.py

TTL переносится как есть (PTTL → RESTORE), существующие ключи приёмника с
тем же именем заменяются.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlsplit

import redis

# Очереди Celery и служебное kombu/celery. Списки задач — по имени очередей
# (app/workers/queues.py плюс дефолтная «celery» и мёртвая «default»).
QUEUE_NAMES = {
    "celery",
    "default",
    "warm",
    "five_verst",
    "five_verst_fresh",
    "five_verst_user",
    "s95",
    "s95_user",
    "parkrun",
    "runpark",
    "unacked",
    "unacked_index",
    "unacked_mutex",
}
SKIP_PREFIXES = ("_kombu.", "celery-task-meta-", "celery-taskset-meta-", "locrec:prog:")
BATCH = 500


def skipped(key: str) -> bool:
    if key in QUEUE_NAMES or key.startswith(SKIP_PREFIXES):
        return True
    # Очереди с приоритетами kombu хранит как «<имя>\x06\x16<приоритет>».
    return key.split("\x06\x16", 1)[0] in QUEUE_NAMES


def main() -> int:
    src_url = os.environ.get("SRC_REDIS_URL", "").strip()
    dst_url = os.environ.get("DST_REDIS_URL", "").strip()
    if not src_url or not dst_url:
        print("нужны SRC_REDIS_URL и DST_REDIS_URL", file=sys.stderr)
        return 2
    if urlsplit(src_url)._replace(path="") == urlsplit(dst_url)._replace(path=""):
        print(f"источник и приёмник — один и тот же Redis ({src_url})", file=sys.stderr)
        return 2

    src = redis.Redis.from_url(src_url, socket_timeout=30)
    dst = redis.Redis.from_url(dst_url, socket_timeout=30)
    src.ping()
    dst.ping()

    copied = skipped_n = vanished = 0
    sessions = 0
    batch: list[bytes] = []

    def flush(keys: list[bytes]) -> None:
        nonlocal copied, vanished, sessions
        pipe = src.pipeline(transaction=False)
        for key in keys:
            pipe.pttl(key)
            pipe.dump(key)
        values = pipe.execute()
        out = dst.pipeline(transaction=False)
        for i, key in enumerate(keys):
            ttl, payload = values[2 * i], values[2 * i + 1]
            if payload is None or ttl == -2:
                vanished += 1  # истёк между SCAN и DUMP
                continue
            out.restore(key, max(int(ttl), 0), payload, replace=True)
            copied += 1
            if key.startswith(b"session:"):
                sessions += 1
        out.execute()

    for key in src.scan_iter(count=1000):
        name = key.decode("utf-8", errors="replace")
        if skipped(name):
            skipped_n += 1
            continue
        batch.append(key)
        if len(batch) >= BATCH:
            flush(batch)
            batch = []
    if batch:
        flush(batch)

    src_sessions = sum(1 for _ in src.scan_iter(match="session:*", count=1000))
    print(
        f"перенесено ключей: {copied} (сессий {sessions} из {src_sessions}), "
        f"пропущено служебных и кэша рекордов: {skipped_n}, истекло по дороге: {vanished}"
    )
    # Сессий может стать меньше на истёкшие по дороге, но не на порядок.
    if src_sessions and sessions < src_sessions * 0.9:
        print("перенесено заметно меньше сессий, чем есть в источнике", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
