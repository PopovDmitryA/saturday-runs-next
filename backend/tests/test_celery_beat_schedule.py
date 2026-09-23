from __future__ import annotations

import re
from pathlib import Path

from app.workers.celery_app import celery_app, schedule_interval_seconds
from app.workers.queues import (
    FIVE_VERST_BATCH_QUEUE,
    FIVE_VERST_FRESH_QUEUE,
    FIVE_VERST_WORKER_QUEUES,
    WARM_QUEUE,
)

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


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
    {
        "celery",
        "five_verst",
        "five_verst_fresh",
        "five_verst_user",
        "s95",
        "s95_user",
        "parkrun",
        "runpark",
        "warm",
    }
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
    # Записей три (суббота / воскресенье / будни) — проверяем все по префиксу,
    # чтобы новая запись не проскочила мимо проверки.
    keys = [key for key in schedule if key.startswith("five-verst-protocol-watch-")]
    assert len(keys) == 3, keys
    for key in keys:
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

    # Club detail rotation — каждые 3 часа в :45. До 12.09.2026 было 3 захода
    # по 20 клубов, но заход шёл 12,3 минуты и столько же ждала приоритетная
    # очередь; теперь 8 заходов по 8 клубов — объём тот же, кусок втрое короче.
    details = schedule["five-verst-clubs-details"]
    assert details["task"] == "five_verst_sync.sync_club_details"
    assert details["schedule"].hour == {0, 3, 6, 9, 12, 15, 18, 21}
    assert details["schedule"].minute == {45}


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


def _worker_command(service: str) -> str:
    """Строка command сервиса из docker-compose.yml.

    Читаем файл, а не конфиг Celery: очередь можно объявить в коде сколько
    угодно раз, но если её нет в -Q, разбирать её никто не будет.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    block = re.search(rf"^  {re.escape(service)}:$(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    assert block, f"сервис {service} не найден в {COMPOSE}"
    command = re.search(r"^    command: (.+)$", block.group(1), re.M)
    assert command, f"у сервиса {service} нет command"
    return command.group(1)


def test_latest_goes_to_the_priority_queue() -> None:
    """Свежие протоколы — в приоритетной очереди, а не в общей с батчами.

    12.09.2026 на проде latest не отрабатывал 18 часов подряд: он стоял в
    очереди five_verst за тридцатью задачами сверки, и суббота держалась на
    13 локациях из 212.
    """
    schedule = celery_app.conf.beat_schedule
    keys = [key for key in schedule if key.startswith("five-verst-latest")]
    assert len(keys) == 3, keys
    for key in keys:
        assert beat_queue(schedule[key]) == FIVE_VERST_FRESH_QUEUE, key
        # Срок годности: не взяли вовремя — задача умирает, а не копится.
        assert schedule[key]["options"]["expires"] > 0, key


def test_background_five_verst_entries_stay_in_the_batch_queue() -> None:
    """Фон остаётся фоном: сверка, ротация и реестр не лезут в приоритет."""
    schedule = celery_app.conf.beat_schedule
    for key in (
        "five-verst-reconcile-protocols-weekday",
        "five-verst-location-rotation",
        "five-verst-registry-daily",
    ):
        assert beat_queue(schedule[key]) == FIVE_VERST_BATCH_QUEUE, key
        assert schedule[key]["options"]["expires"] > 0, key


def test_fresh_and_batch_queues_have_their_own_workers() -> None:
    """У свежести и у фона 5 вёрст — по своему контейнеру, и ни один не слушает чужое.

    До 17.09.2026 оба сидели в одном воркере с `-Q five_verst_fresh,five_verst`
    и надеялись на queue_order_strategy=priority. Обещанный порядок kombu не
    держит: очереди лежат в set, порядок задаёт хеш строки — свой на каждый
    запуск. Воркер поднялся «сначала фон» и двое суток не брал latest.
    """
    fresh = _worker_command("worker-five-verst-fresh")
    batch = _worker_command("worker-five-verst")
    def queues_of(command: str) -> set[str]:
        return set(command.split("-Q ")[1].split(" ")[0].split(","))

    assert queues_of(fresh) == {FIVE_VERST_FRESH_QUEUE}, fresh
    assert queues_of(batch) == {FIVE_VERST_BATCH_QUEUE}, batch
    # Без этого воркер резервирует фоновую задачу «про запас» при acks_late,
    # и после рестарта брокер вернёт её вторым экземпляром.
    assert "--prefetch-multiplier=1" in batch, batch
    assert set(FIVE_VERST_WORKER_QUEUES) == {FIVE_VERST_FRESH_QUEUE, FIVE_VERST_BATCH_QUEUE}


def test_cache_warmups_do_not_share_a_queue_with_syncs() -> None:
    """Прогревы кэша — на своей очереди и своём воркере.

    До 17.09.2026 они ехали в runpark «к самому свободному воркеру». Когда
    воркеры синков уехали на домашний сервер, очередь runpark оказалась забита
    четырьмя десятками прогревов, а пользовательский runpark_sync.user_sync —
    в хвосте за ними. Плюс прогрев считал агрегаты через WAN до базы.
    """
    schedule = celery_app.conf.beat_schedule
    for key in ("leaderboards-warm-cache", "locations-warm-cache"):
        assert beat_queue(schedule[key]) == WARM_QUEUE, key
    assert beat_queue({"task": "runpark_sync.user_sync"}) == "runpark"
    command = _worker_command("worker-warm")
    assert "-Q warm" in command, command


def test_user_sync_keeps_its_own_worker() -> None:
    """Пользовательское «обновить» — первый приоритет: своим воркером, чтобы
    человек у экрана не ждал даже одного фонового куска."""
    command = _worker_command("worker-five-verst-user")
    assert "-Q five_verst_user" in command, command


# ------------------------------------------------------------------ expires


def test_every_beat_entry_has_expires() -> None:
    """CEL-04: без expires простой воркера превращается в долг очереди.

    12.09.2026 в очереди five_verst лежало 26 просроченных latest и 30 сверок,
    и субботние протоколы ждали своей минуты полутора суток.
    """
    for key, entry in celery_app.conf.beat_schedule.items():
        expires = (entry.get("options") or {}).get("expires")
        assert expires and expires > 0, f"{key}: нет expires"


def test_expires_never_outlives_the_interval() -> None:
    """Срок годности не длиннее промежутка между запусками.

    Иначе к моменту, когда задачу наконец возьмут, beat уже поставил следующую
    такую же — а это и есть долг очереди, от которого expires спасает.
    """
    for key, entry in celery_app.conf.beat_schedule.items():
        interval = schedule_interval_seconds(entry["schedule"])
        assert interval, f"{key}: не удалось вычислить интервал"
        expires = entry["options"]["expires"]
        assert expires <= interval, f"{key}: expires {expires} длиннее интервала {interval}"


def test_schedule_interval_takes_the_shortest_gap() -> None:
    """Интервал считается по самому короткому промежутку, а не по среднему."""
    from celery.schedules import crontab

    assert schedule_interval_seconds(crontab(minute="*/3")) == 180
    assert schedule_interval_seconds(crontab(minute="7,37")) == 30 * 60
    # 20:00 → 00:00 следующего дня — четыре часа, а не пять.
    assert schedule_interval_seconds(crontab(hour="0,5,10,15,20", minute=0)) == 4 * 3600


# ------------------------------------------------------------- лимиты (CEL-01)

MIN = 60


def _effective_time_limits(task) -> tuple[float | None, float | None]:
    """Лимиты, с которыми задача реально пойдёт в работу.

    У задачи без своих kwargs атрибуты пустые — действует глобальный дефолт из
    conf. Проверять надо именно эту пару, иначе сторож пропустит задачу, у
    которой лимита нет вовсе.
    """
    conf = celery_app.conf
    soft = task.soft_time_limit if task.soft_time_limit is not None else conf.task_soft_time_limit
    hard = task.time_limit if task.time_limit is not None else conf.task_time_limit
    return soft, hard


def _registered_tasks() -> dict[str, object]:
    celery_app.loader.import_default_modules()
    # Служебные задачи самого Celery (celery.backend_cleanup и т.п.) — не наши.
    return {name: task for name, task in celery_app.tasks.items() if not name.startswith("celery.")}


def test_every_task_has_a_finite_time_limit() -> None:
    """CEL-01: воркеры синков однопоточные, и задача без лимита держит очередь.

    Зависший сетевой сокет не снимут ни expires (он про НЕ начатые задачи), ни
    Redis-лок с TTL, ни visibility_timeout: исполняющий процесс остаётся занят.
    """
    for name, task in _registered_tasks().items():
        soft, hard = _effective_time_limits(task)
        assert soft and soft > 0, f"{name}: нет soft_time_limit"
        assert hard and hard > 0, f"{name}: нет time_limit"
        assert soft < hard, f"{name}: soft {soft} должен быть меньше hard {hard}"


def test_acks_late_tasks_stay_under_the_visibility_timeout() -> None:
    """Задача с acks_late обязана быть убита воркером раньше, чем брокер вернёт
    её сообщение в очередь: иначе та же работа пойдёт вторым экземпляром."""
    visibility = celery_app.conf.broker_transport_options["visibility_timeout"]
    checked = 0
    for name, task in _registered_tasks().items():
        if not task.acks_late:
            continue
        _, hard = _effective_time_limits(task)
        assert hard < visibility, f"{name}: hard-лимит {hard} ≥ visibility_timeout {visibility}"
        checked += 1
    assert checked, "acks_late-задач не нашлось — сторож проверяет пустоту"


# Согласовано с Дмитрием 21.09.2026 по журналу прода за 60 дней (минуты,
# soft/hard). Лимит ловит зависание, а не медленный день, поэтому стоит
# заметно выше максимума прогона; менять — вместе с app/workers/time_limits.py
# и осознанно, а не «чтобы тест прошёл».
AGREED_LIMITS_MINUTES: dict[str, tuple[int, int]] = {
    "five_verst_sync.sync_latest_results": (180, 185),
    "five_verst_sync.reconcile_stale_protocols": (110, 115),
    "five_verst_sync.sweep_week_protocols": (110, 115),
    "five_verst_sync.sync_location": (60, 65),
    "five_verst_sync.sync_location_rotation": (40, 45),
    "five_verst_sync.sync_club_details": (30, 35),
    "five_verst_sync.sync_locations_registry": (20, 25),
    "five_verst_sync.sync_clubs_registry": (20, 25),
    "five_verst_sync.sync_community_events": (20, 25),
    "five_verst_sync.sync_location_summaries": (20, 25),
    "five_verst_sync.fetch_protocol_from_profile": (20, 25),
    "five_verst_sync.protocol_upload_watch": (15, 20),
    "five_verst_sync.enqueue_all_location_summaries": (5, 6),
    "five_verst_sync.enqueue_recent_protocols": (5, 6),
    "five_verst_sync.enqueue_locations_registry": (5, 6),
    "five_verst_sync.enqueue_latest_results": (5, 6),
    "five_verst_sync.enqueue_reconcile_protocols": (5, 6),
    "s95_sync.api_new_protocols": (180, 185),
    "s95_sync.api_sync_updated": (180, 185),
    "s95_sync.api_reconcile_date": (180, 185),
    "s95_sync.api_full_backfill": (360, 365),
    "s95_sync.sync_locations_registry": (20, 25),
    "s95_sync.watch_cancellations": (20, 25),
    "s95_sync.sync_location_descriptions": (20, 25),
    "s95_sync.reconcile_events": (20, 25),
    "s95_sync.fetch_protocol_from_profile": (20, 25),
    "s95_sync.enqueue_locations_registry": (5, 6),
    "s95_sync.run_user_sync": (20, 25),
    "s95_sync.run_admin_resync": (20, 25),
    "user_sync.run": (20, 25),
    "user_sync.admin_resync": (20, 25),
    "runpark_sync.sync_latest": (20, 25),
    "runpark_sync.backfill_crosslinks": (20, 25),
    "runpark_sync.user_sync": (20, 25),
    "parkrun_sync.process_pending_queue": (20, 25),
    "parkrun_sync.run_user_sync": (20, 25),
    "leaderboards.warm_cache": (90, 95),
    "locations.warm_cache": (60, 65),
    "portal_cache.warm_home": (40, 45),
    "dashboard_warm.after_sync": (40, 45),
    "og_render.render_location_images": (120, 125),
    "og_render.render_user_images": (120, 125),
    # Погода: лимиты стояли и до CEL-01 (бэкфил гоняется, пока отдаёт квота
    # Open-Meteo), запас у них — минута.
    "weather.collect_start_weather": (179, 180),
    "weather.collect_preliminary": (29, 30),
    "weather.collect_forecast": (59, 60),
    "page_stats.rollup": (20, 25),
    "admin_digest.daily_sync_summary": (20, 25),
    "user_names.refresh": (20, 25),
    "locations.refresh_activity_status": (20, 25),
    "sync_runs.close_stale": (5, 6),
    "queues.watch_priority": (5, 6),
    "email_send.deliver": (5, 6),
    # Уведомления сайта: доставка и выборка новых результатов — один запрос
    # к каналу или базе; сканы и воскресные рейтинги считают челленджи и
    # рейтинги пачкой до 50 человек, отсюда средняя ступень.
    "notifications.deliver": (5, 6),
    "notifications.scan_new_results": (5, 6),
    "notifications.scan_activity": (20, 25),
    "notifications.weekly_ratings": (20, 25),
    "notifications.retry_queued": (20, 25),
}


def test_time_limits_match_the_agreed_table() -> None:
    """Каждая зарегистрированная задача — в согласованной таблице, и с теми же
    числами. Новая задача обязана попасть сюда явно: дефолт conf — страховка,
    а не способ не думать о потолке."""
    tasks = _registered_tasks()
    assert set(tasks) == set(AGREED_LIMITS_MINUTES), (
        f"не в таблице: {sorted(set(tasks) - set(AGREED_LIMITS_MINUTES))}; "
        f"лишние в таблице: {sorted(set(AGREED_LIMITS_MINUTES) - set(tasks))}"
    )
    for name, (soft_min, hard_min) in AGREED_LIMITS_MINUTES.items():
        soft, hard = _effective_time_limits(tasks[name])
        assert (soft, hard) == (soft_min * MIN, hard_min * MIN), (
            f"{name}: {soft / MIN:g}/{hard / MIN:g} мин, согласовано {soft_min}/{hard_min}"
        )
