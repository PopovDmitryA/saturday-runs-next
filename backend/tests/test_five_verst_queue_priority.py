"""Приоритет очередей 5 вёрст: свежесть не должна ждать фон.

12.09.2026 на проде задача `latest` не отрабатывала 18 часов: она стояла в общей
очереди `five_verst` за тридцатью задачами сверки по 63 минуты каждая. Суббота
держалась на 13 локациях из 212. Инварианты ниже описывают устройство, которое
это чинит: две группы задач, строгий порядок разбора и кусок фона, который
короче терпения приоритетной очереди.
"""

from __future__ import annotations

from app.config import get_settings
from app.workers.celery_app import celery_app
from app.workers.queues import FIVE_VERST_BATCH_QUEUE, FIVE_VERST_FRESH_QUEUE
from app.workers.tasks import five_verst_sync as fv
from app.workers.tasks import sync_task_reporting as reporting

# Секунд на один протокол: пауза five_verst_protocol_min_interval_seconds плюс
# сам фетч. Замер по проду 12.09.2026: 5203 секунды на 100 протоколов.
SECONDS_PER_PROTOCOL = 52
# Секунд на один клуб: 738 секунд на 20 клубов, тот же замер.
SECONDS_PER_CLUB = 37
# Потолок ожидания приоритетной очереди. Задачу с concurrency=1 прервать нельзя,
# поэтому свежесть ждёт ровно один фоновый кусок.
MAX_PRIORITY_WAIT_MINUTES = 15


def test_latest_is_the_only_five_verst_task_in_the_fresh_queue() -> None:
    """В приоритетной очереди живёт только свежесть.

    Фан-ауты (все сводки локаций, доборка протоколов из профилей) обязаны
    остаться в фоне: пачкой на сотни задач они затопили бы приоритет и вернули
    бы ровно ту же болезнь.
    """
    fresh = {
        name
        for name, task in celery_app.tasks.items()
        if name.startswith("five_verst_sync.") and getattr(task, "queue", None) == FIVE_VERST_FRESH_QUEUE
    }
    assert fresh == {"five_verst_sync.sync_latest_results"}


def test_background_five_verst_tasks_acknowledge_late() -> None:
    """Позднее подтверждение — то, что не даёт воркеру резервировать фон.

    При раннем подтверждении сообщение считается обработанным ещё до конца
    задачи, окно prefetch освобождается, и воркер забирает следующую фоновую
    задачу себе в резерв. Подоспевший latest ждал бы тогда два куска вместо
    одного.
    """
    for name, task in celery_app.tasks.items():
        if not name.startswith("five_verst_sync."):
            continue
        if getattr(task, "queue", None) != FIVE_VERST_BATCH_QUEUE:
            continue
        assert task.acks_late is True, name


def test_background_chunk_is_shorter_than_priority_patience() -> None:
    """Кусок фона = потолок ожидания свежести.

    Клубы считаются по своей скорости: страница клуба легче протокола, замер на
    проде 12.09.2026 — 738 секунд на 20 клубов.
    """
    settings = get_settings()
    for limit, seconds_each in (
        (settings.five_verst_reconcile_batch_limit, SECONDS_PER_PROTOCOL),
        (settings.five_verst_week_sweep_batch_limit, SECONDS_PER_PROTOCOL),
        (settings.five_verst_clubs_batch_limit, SECONDS_PER_CLUB),
    ):
        minutes = limit * seconds_each / 60
        assert minutes <= MAX_PRIORITY_WAIT_MINUTES, limit


def test_reconcile_daily_budget_leaves_the_worker_free() -> None:
    """Сверка не имеет права занимать воркер круглые сутки.

    Согласовано с Дмитрием 12.09.2026: потолок ~11 часов в будние сутки. До
    правки выходило 15,6 часа — 88 % всего времени воркера при том, что latest
    тратит 10 минут в сутки.
    """
    settings = get_settings()
    per_run = settings.five_verst_reconcile_batch_limit * settings.five_verst_reconcile_chunks_per_run
    runs_per_day = 8  # crontab(minute=10, hour="*/3") по будням
    hours_per_day = per_run * runs_per_day * SECONDS_PER_PROTOCOL / 3600
    assert hours_per_day <= 11


def test_week_sweep_keeps_its_volume_after_the_cut() -> None:
    """Резали длину куска, а не объём работы: обход недели остаётся полным."""
    settings = get_settings()
    per_run = settings.five_verst_week_sweep_batch_limit * settings.five_verst_week_sweep_chunks_per_run
    assert per_run == 240


def test_latest_watches_its_own_queue_depth(monkeypatch) -> None:
    """Клапан глубины очереди больше не может отменить свежесть.

    `run_reported_sync` пропускает задачу, если её очередь глубже 120. Пока
    latest жил в общей очереди, разросшийся фон отменял бы именно его — самое
    ценное вместе с самым терпеливым.
    """
    asked: list[str] = []

    def _fake_capacity(queue_name: str, *, max_depth: int = 120) -> bool:
        asked.append(queue_name)
        return False

    monkeypatch.setattr(reporting, "batch_queue_has_capacity", _fake_capacity)
    monkeypatch.setattr(reporting, "record_run", lambda *a, **kw: None)

    payload = fv.sync_latest_results_task.run()

    assert asked == [FIVE_VERST_FRESH_QUEUE]
    assert payload["skipped"] is True


def test_clubs_keep_their_daily_volume_after_the_cut() -> None:
    """Клубы порезаны по длине куска, а не по объёму: круг по 519 клубам
    остаётся недельным, просто заходов стало больше."""
    settings = get_settings()
    entry = celery_app.conf.beat_schedule["five-verst-clubs-details"]
    runs_per_day = len(entry["schedule"].hour)
    assert settings.five_verst_clubs_batch_limit * runs_per_day >= 60


def test_stale_sync_runs_are_closed_on_schedule() -> None:
    """Гашение висяков не должно зависеть от того, зашёл ли кто-то в админку.

    До 12.09.2026 `close_stale_sync_runs` звался только из
    `get_admin_pipeline_status`, и записи в статусе running жили неделями.
    """
    entry = celery_app.conf.beat_schedule["sync-runs-close-stale"]
    assert entry["task"] == "sync_runs.close_stale"
    assert entry["schedule"].minute == {5}
    assert entry["options"]["queue"] == celery_app.conf.task_default_queue


def test_latest_stale_threshold_covers_a_full_saturday_catch_up() -> None:
    """Порог висяка у latest обязан быть больше самого долгого честного прогона:
    догон субботы 12.09.2026 занял 152 минуты."""
    from app.services.sync_run_maintenance import stale_after

    assert stale_after("five_verst:latest").total_seconds() / 60 > 152
