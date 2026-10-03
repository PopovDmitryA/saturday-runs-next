"""Сверка протоколов 5 вёрст: норма прогона берётся цепочкой пачек.

Воркер five_verst один и с concurrency=1 — задачу не прервать, поэтому норма
(200 протоколов) режется на звенья по batch_limit: между ними воркер успевает
взять пользовательский синк из приоритетной очереди.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.workers.tasks.five_verst_sync import reconcile_stale_protocols_task


@pytest.fixture(autouse=True)
def freshness_window() -> Iterator[MagicMock]:
    """По умолчанию — будний день: в выходные по Москве цепочка звено не ставит.

    Без подмены тесты зависели от дня запуска: в субботу и воскресенье падал
    `test_full_batch_enqueues_next_chunk`, а тесты «звена нет» проходили бы и
    со сломанным условием — их выручали выходные. Выходные — отдельный тест.
    """
    with patch("app.workers.tasks.five_verst_sync.is_freshness_window", return_value=False) as window:
        yield window


def _payload(candidates: int) -> dict[str, object]:
    return {"candidates_total": candidates, "protocols_fetched": candidates, "errors": []}


@patch("app.workers.tasks.five_verst_sync.reconcile_stale_protocols_task.apply_async")
@patch("app.workers.tasks.five_verst_sync.run_reported_sync")
def test_full_batch_enqueues_next_chunk(run_sync: MagicMock, apply_async: MagicMock) -> None:
    run_sync.return_value = _payload(100)

    result = reconcile_stale_protocols_task.run(limit=100, chunks_left=2)

    assert result["next_chunk_enqueued"] is True
    apply_async.assert_called_once()
    kwargs = apply_async.call_args.kwargs["kwargs"]
    assert kwargs["chunks_left"] == 1
    # force=True: часовой слот уже занят этим же прогоном, иначе звено
    # отвалилось бы как duplicate_hour_slot.
    assert kwargs["force"] is True


@patch("app.workers.tasks.five_verst_sync.reconcile_stale_protocols_task.apply_async")
@patch("app.workers.tasks.five_verst_sync.run_reported_sync")
def test_weekend_stops_chain(run_sync: MagicMock, apply_async: MagicMock, freshness_window: MagicMock) -> None:
    """Полная пачка в выходные: хвост нормы ждёт понедельника, суббота — свежим протоколам."""
    run_sync.return_value = _payload(100)
    freshness_window.return_value = True

    result = reconcile_stale_protocols_task.run(limit=100, chunks_left=2)

    assert result["next_chunk_skipped"] == "freshness_window"
    assert "next_chunk_enqueued" not in result
    apply_async.assert_not_called()


@patch("app.workers.tasks.five_verst_sync.reconcile_stale_protocols_task.apply_async")
@patch("app.workers.tasks.five_verst_sync.run_reported_sync")
def test_last_chunk_does_not_enqueue(run_sync: MagicMock, apply_async: MagicMock) -> None:
    run_sync.return_value = _payload(100)

    result = reconcile_stale_protocols_task.run(limit=100, chunks_left=1)

    assert "next_chunk_enqueued" not in result
    apply_async.assert_not_called()


@patch("app.workers.tasks.five_verst_sync.reconcile_stale_protocols_task.apply_async")
@patch("app.workers.tasks.five_verst_sync.run_reported_sync")
def test_partial_batch_stops_chain(run_sync: MagicMock, apply_async: MagicMock) -> None:
    """Кандидатов меньше пачки — очередь исчерпана, продолжать нечего."""
    run_sync.return_value = _payload(12)

    reconcile_stale_protocols_task.run(limit=100, chunks_left=2)

    apply_async.assert_not_called()


@patch("app.workers.tasks.five_verst_sync.reconcile_stale_protocols_task.apply_async")
@patch("app.workers.tasks.five_verst_sync.run_reported_sync")
def test_skipped_run_stops_chain(run_sync: MagicMock, apply_async: MagicMock) -> None:
    """Скип (кулдаун, занятая очередь) не должен тянуть за собой второе звено."""
    run_sync.return_value = {"skipped": True, "reason": "batch_queue_full", "errors": []}

    reconcile_stale_protocols_task.run(limit=100, chunks_left=2)

    apply_async.assert_not_called()
