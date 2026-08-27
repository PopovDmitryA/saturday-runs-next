from __future__ import annotations

from app.workers.celery_app import celery_app


def beat_queue(entry: dict) -> str:
    """Очередь, в которую beat реально поставит задачу этой записи.

    Судить по имени записи нельзя: очередь задаёт либо `options`, либо
    `task_routes`, и с префиксом имени они не обязаны совпадать. Именно так
    поминутный наблюдатель протоколов, заведённый «в общей очереди», молча
    уезжал в five_verst — под тот самый воркер с concurrency=1, от которого его
    и уводили (27.08.2026).
    """
    routed = celery_app.amqp.router.route(dict(entry.get("options") or {}), entry["task"])
    return routed["queue"].name


# Очереди, которые кто-то реально разбирает (docker-compose.yml, команды
# воркеров): worker без -Q слушает дефолтную celery, остальные — свои.
# Записать задачу в очередь вне этого списка — значит отправить её в никуда:
# kombu молча создаст очередь, сообщения будут копиться, и ошибки не будет
# нигде. Так с 20.08.2026 простаивало правило молчания с queue="default".
CONSUMED_QUEUES = frozenset(
    {"celery", "five_verst", "five_verst_user", "s95", "s95_user", "parkrun", "runpark"}
)


def test_every_beat_entry_lands_in_a_consumed_queue() -> None:
    for key, entry in celery_app.conf.beat_schedule.items():
        assert beat_queue(entry) in CONSUMED_QUEUES, f"{key}: очередь никто не слушает"


def test_activity_status_runs_in_the_default_queue() -> None:
    """Правило молчания — в общей очереди: сетевых запросов нет, занимать ею
    очередь синков незачем (так и написано в самой задаче)."""
    entry = celery_app.conf.beat_schedule["locations-activity-status"]
    assert beat_queue(entry) == celery_app.conf.task_default_queue
    assert entry["schedule"].hour == {21}
    assert entry["schedule"].minute == {10}


def test_s95_registry_every_3_days() -> None:
    schedule = celery_app.conf.beat_schedule
    reg = schedule["s95-registry-3days"]
    assert reg["task"] == "s95_sync.sync_locations_registry"
    assert reg["schedule"].minute == {30}
    assert reg["schedule"].hour == {20}
    assert reg["schedule"].day_of_month == set(range(1, 32, 3))
    assert "s95-registry-daily" not in schedule


def test_five_verst_schedule_intact() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["five-verst-latest-weekday"]["schedule"].hour == {0, 5, 10, 15, 20}
    assert schedule["five-verst-latest-weekday"]["schedule"].day_of_week == {1, 2, 3, 4, 5}


def test_five_verst_queue_no_same_minute_collisions() -> None:
    """Очередь five_verst — один воркер (concurrency=1): задачи разведены по
    минутам, чтобы beat не ставил несколько батчей в хвост в одну и ту же минуту.

    Проверяем по фактической очереди записи, а не по префиксу её имени: имя
    ничего не решает, а расхождение имени с очередью — как раз то, что этот
    инвариант и должен ловить.
    """
    schedule = celery_app.conf.beat_schedule
    assert schedule["five-verst-registry-daily"]["schedule"].minute == {50}
    assert schedule["five-verst-registry-daily"]["schedule"].hour == {20}
    assert schedule["five-verst-location-rotation"]["schedule"].minute == {30}

    # latest на минуте :00 — единственный: свежие протоколы не ждут пачку соседей.
    latest_keys = [key for key in schedule if key.startswith("five-verst-latest")]
    for key, entry in schedule.items():
        if beat_queue(entry) == "five_verst" and key not in latest_keys:
            assert 0 not in entry["schedule"].minute, key


def test_protocol_watch_stays_out_of_the_five_verst_queue() -> None:
    """Поминутный наблюдатель ходит в общей очереди — как обещает его docstring.

    Без явного options его забирало правило `five_verst_sync.*` из task_routes,
    и минутная задача вставала в хвост за многочасовым reconcile на воркере с
    concurrency=1 — ровно то, ради чего её оттуда и уводили.
    """
    schedule = celery_app.conf.beat_schedule
    for key in ("five-verst-protocol-watch-weekday", "five-verst-protocol-watch-weekend"):
        assert beat_queue(schedule[key]) == celery_app.conf.task_default_queue, key


def test_five_verst_reconcile_weekdays_only() -> None:
    """Прогон сверки занимает пару часов и в выходные задерживал часовой latest
    (duplicate_hour_slot на проде), поэтому ходит только по будням."""
    schedule = celery_app.conf.beat_schedule
    assert "five-verst-reconcile-protocols" not in schedule
    assert "five-verst-reconcile-protocols-weekend" not in schedule

    weekday = schedule["five-verst-reconcile-protocols-weekday"]
    assert weekday["schedule"].day_of_week == {1, 2, 3, 4, 5}
    assert weekday["schedule"].hour == {0, 3, 6, 9, 12, 15, 18, 21}


def test_five_verst_clubs_schedule() -> None:
    schedule = celery_app.conf.beat_schedule

    # Clubs list — twice a week (Mon & Thu, 21:30 MSK).
    registry = schedule["five-verst-clubs-registry"]
    assert registry["task"] == "five_verst_sync.sync_clubs_registry"
    assert registry["schedule"].hour == {21}
    assert registry["schedule"].minute == {30}
    assert registry["schedule"].day_of_week == {1, 4}

    # Club detail rotation — 3×/day at 9:30/15:30/23:30 MSK.
    details = schedule["five-verst-clubs-details"]
    assert details["task"] == "five_verst_sync.sync_club_details"
    assert details["schedule"].hour == {9, 15, 23}
    assert details["schedule"].minute == {30}


def test_s95_api_protocol_schedule() -> None:
    schedule = celery_app.conf.beat_schedule

    # New protocols scan — Sat & Sun at 11/17/23.
    new_scan = schedule["s95-api-new-protocols-weekend"]
    assert new_scan["task"] == "s95_sync.api_new_protocols"
    assert new_scan["schedule"].hour == {11, 17, 23}
    assert new_scan["schedule"].day_of_week == {6, 0}

    # Sync new + updated protocols (updated_at-aware) — Mon/Wed/Fri 03:00.
    sync_updated = schedule["s95-api-sync-updated"]
    assert sync_updated["task"] == "s95_sync.api_sync_updated"
    assert sync_updated["schedule"].hour == {3}
    assert sync_updated["schedule"].minute == {0}
    assert sync_updated["schedule"].day_of_week == {1, 3, 5}

    # Old per-Saturday reconcile schedule is retired in favour of updated_at.
    for removed in (
        "s95-api-reconcile-latest-mon",
        "s95-api-reconcile-latest-thu",
        "s95-api-reconcile-week-1-tue",
        "s95-api-reconcile-week-2-wed",
    ):
        assert removed not in schedule


def test_s95_playwright_batch_schedules_removed() -> None:
    """Old Playwright-based S95 batch jobs must no longer be scheduled."""
    schedule = celery_app.conf.beat_schedule
    for removed in (
        "s95-latest-weekday",
        "s95-latest-saturday-hourly",
        "s95-latest-sunday-hourly",
        "s95-location-rotation",
        "s95-reconcile-protocols",
        "s95-athletes-registry",
    ):
        assert removed not in schedule


def test_page_stats_rollup_schedule() -> None:
    """Агрегаты популярности страниц — каждый час на дефолтной очереди."""
    schedule = celery_app.conf.beat_schedule
    rollup = schedule["page-stats-rollup"]
    assert rollup["task"] == "page_stats.rollup"
    assert rollup["schedule"].minute == {35}
    # Дефолтная очередь "celery" — общий worker без -Q, никакой своей queue.
    assert "queue" not in rollup.get("options", {})


def test_daily_sync_summary_schedule() -> None:
    """Итоги автообновления — одно сообщение в ВК в сутки, 21:50 МСК."""
    schedule = celery_app.conf.beat_schedule
    digest = schedule["admin-sync-daily-summary"]
    assert digest["task"] == "admin_digest.daily_sync_summary"
    assert digest["schedule"].hour == {21}
    assert digest["schedule"].minute == {50}
    # После вечерних реестров 5 вёрст (20:00) и s95 (20:30) — попадают в сводку дня.
    assert schedule["five-verst-registry-daily"]["schedule"].hour == {20}
