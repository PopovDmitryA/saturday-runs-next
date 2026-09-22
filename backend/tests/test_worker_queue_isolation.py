"""Приоритетная очередь не делит воркера с фоном — и у каждой очереди есть едок.

19.09.2026: воркер 5 вёрст слушал `-Q five_verst_fresh,five_verst` и двое суток
не заглядывал в свежесть. Виноват не приоритет задач, а его отсутствие: kombu
держит очереди воркера в set (`transport/redis.py`, `active_queues`), поэтому
`queue_order_strategy: priority` выстраивает их в порядке хеша строки — своём на
каждый запуск процесса. Порядок из `-Q` тут ничего не решает, и «строгий
приоритет» оказывается подбрасыванием монетки при старте контейнера.

Отсюда два инварианта ниже: очередь, где кто-то ждёт (человек у экрана или
сегодняшний протокол), слушает отдельный контейнер, и ни одна очередь из
маршрутов не остаётся без воркера.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.workers.celery_app import celery_app

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.yml"

# Очереди, у которых есть ждущий: свежие протоколы субботы и запросы из кабинета.
# s95_user сюда не входит намеренно: S95 требует одного фетчера, и уступка там
# сделана иначе — батч сам смотрит на LLEN(s95_user) и притормаживает
# (app/s95/fetch/priority.py). Развести их по контейнерам значило бы удвоить
# число запросов к s95.ru, который уже блокировал нас по IP.
PRIORITY_QUEUES = {"five_verst_fresh", "five_verst_user"}

_COMMAND_RE = re.compile(r"^\s*command:\s*celery\s+.*?worker\b(?P<rest>.*)$", re.MULTILINE)
_QUEUES_RE = re.compile(r"-Q\s+(?P<queues>[\w,]+)")


def _worker_queues() -> list[set[str]]:
    if not COMPOSE_PATH.exists():  # pragma: no cover — прогон вне клона репозитория
        pytest.skip(f"{COMPOSE_PATH} рядом не лежит")
    groups: list[set[str]] = []
    for match in _COMMAND_RE.finditer(COMPOSE_PATH.read_text()):
        queues = _QUEUES_RE.search(match.group("rest"))
        groups.append(set(queues.group("queues").split(",")) if queues else {celery_app.conf.task_default_queue})
    return groups


def test_priority_queue_never_shares_a_worker_with_background() -> None:
    for queues in _worker_queues():
        shared = queues & PRIORITY_QUEUES
        assert not shared or queues == shared, f"{sorted(shared)} слушает воркер с фоном: {sorted(queues)}"


def test_each_priority_queue_has_exactly_one_worker() -> None:
    groups = _worker_queues()
    for queue in PRIORITY_QUEUES:
        consumers = [item for item in groups if queue in item]
        assert len(consumers) == 1, f"{queue}: воркеров {len(consumers)}, а нужен ровно один"


def test_every_routed_queue_has_a_consumer() -> None:
    """Очередь без воркера — тихая потеря задач: они копятся, пока не протухнут."""

    consumed = set().union(*_worker_queues())
    routed = {
        str(entry["options"]["queue"])
        for entry in celery_app.conf.beat_schedule.values()
        if entry.get("options", {}).get("queue")
    }
    routed |= {str(route["queue"]) for route in celery_app.conf.task_routes.values() if route.get("queue")}

    assert routed - consumed == set()
