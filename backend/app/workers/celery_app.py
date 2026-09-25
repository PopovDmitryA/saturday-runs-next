from __future__ import annotations

from datetime import date, timedelta

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.platform_adapters.registry import ensure_adapters_registered
from app.workers.queues import FIVE_VERST_BATCH_QUEUE, FIVE_VERST_FRESH_QUEUE, WARM_QUEUE
from app.workers.time_limits import (
    BROKER_VISIBILITY_TIMEOUT,
    DEFAULT_SOFT_TIME_LIMIT,
    DEFAULT_TIME_LIMIT,
)

settings = get_settings()

celery_app = Celery("saturday_runs", broker=settings.redis_url, backend=settings.redis_url)
ensure_adapters_registered()
celery_app.conf.update(
    # Воркер с несколькими очередями (-Q a,b) по умолчанию обходит их
    # round-robin: очередь b получает слот, даже когда в a есть работа. Нам
    # нужен строгий приоритет — five_verst_fresh перед five_verst, s95_user
    # перед s95, — поэтому просим redis-транспорт уважать порядок из -Q.
    # visibility_timeout поднят выше самого долгого жёсткого лимита: у задач
    # 5 вёрст acks_late=True (см. app/workers/tasks/five_verst_sync.py), и
    # если брокер перестанет ждать раньше, чем воркер убьёт задачу по лимиту,
    # он вернёт в очередь ещё живую задачу вторым экземпляром. Два часа
    # перестали хватать 21.09.2026, когда latest получил лимит 180/185 минут
    # (тяжёлая суббота на проде — 153 минуты); теперь четыре. Сторож
    # «hard-лимит acks_late-задачи < visibility_timeout» — в тестах.
    broker_transport_options={
        "queue_order_strategy": "priority",
        "visibility_timeout": BROKER_VISIBILITY_TIMEOUT,
    },
    # Потолок для задачи без явного лимита (CEL-01, app/workers/time_limits.py):
    # воркеры однопоточные, зависший сокет без лимита держит очередь вечно.
    # У всех существующих задач лимит задан в декораторе; это — на случай новой.
    task_soft_time_limit=DEFAULT_SOFT_TIME_LIMIT,
    task_time_limit=DEFAULT_TIME_LIMIT,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    imports=(
        "app.workers.tasks.five_verst_sync",
        "app.workers.tasks.user_sync",
        "app.workers.tasks.admin_resync",
        "app.workers.tasks.s95_sync",
        "app.workers.tasks.parkrun_sync",
        "app.workers.tasks.runpark_sync",
        "app.workers.tasks.leaderboards_warm",
        "app.workers.tasks.portal_cache",
        "app.workers.tasks.locations_status",
        "app.workers.tasks.locations_warm",
        "app.workers.tasks.dashboard_warm",
        "app.workers.tasks.page_stats",
        "app.workers.tasks.admin_digest",
        "app.workers.tasks.og_render",
        "app.workers.tasks.email_send",
        "app.workers.tasks.user_names",
        "app.workers.tasks.weather_collect",
        "app.workers.tasks.weather_forecast",
        "app.workers.tasks.sync_runs_maintenance",
        "app.workers.tasks.notifications",
        "app.workers.tasks.queue_watch",
    ),
    task_routes={
        # Точные имена идут до шаблона `five_verst_sync.*` — первое совпадение
        # выигрывает. Свежесть (сегодняшние протоколы) живёт в отдельной
        # приоритетной очереди, всё остальное по 5 вёрст — фон.
        "five_verst_sync.sync_latest_results": {"queue": FIVE_VERST_FRESH_QUEUE},
        "five_verst_sync.*": {"queue": FIVE_VERST_BATCH_QUEUE},
        "user_sync.*": {"queue": "five_verst_user"},
        "s95_sync.run_user_sync": {"queue": "s95_user"},
        "s95_sync.run_admin_resync": {"queue": "s95_user"},
        "s95_sync.*": {"queue": "s95"},
        "parkrun_sync.*": {"queue": "parkrun"},
        "runpark_sync.*": {"queue": "runpark"},
        # Прогревы кэша — своя очередь рядом с базой, см. app/workers/queues.py.
        "leaderboards.warm_cache": {"queue": WARM_QUEUE},
        "locations.warm_cache": {"queue": WARM_QUEUE},
        # OG-картинки рендерит Playwright — Chromium есть только в образе
        # worker-parkrun (Dockerfile.parkrun), поэтому очередь parkrun.
        "og_render.*": {"queue": "parkrun"},
    },
    beat_schedule={
        # Имена пользователей берутся из профилей беговых систем — раз в сутки
        # после ночных синков сверяем их заново (смена фамилии, новая привязка,
        # правка имени в самой системе). Часы — Europe/Moscow.
        "user-names-refresh": {
            "task": "user_names.refresh",
            "schedule": crontab(minute=10, hour=5),
        },
        # OG-картинки локаций (Л19): обновить после субботних/воскресных синков
        # протоколов + полный прогон в понедельник ночью (часы — Europe/Moscow).
        "og-render-weekend": {
            "task": "og_render.render_location_images",
            "schedule": crontab(minute=0, hour="14,22", day_of_week="6,0"),
            "options": {"queue": "parkrun"},
        },
        "og-render-weekly": {
            "task": "og_render.render_location_images",
            "schedule": crontab(minute=30, hour=4, day_of_week=1),
            "options": {"queue": "parkrun"},
        },
        # Карточки участников: после субботних синков (цифры за неделю уже
        # приехали) и полным прогоном в понедельник ночью.
        "og-render-users-weekend": {
            "task": "og_render.render_user_images",
            "schedule": crontab(minute=20, hour=22, day_of_week="6,0"),
            "options": {"queue": "parkrun"},
        },
        "og-render-users-weekly": {
            "task": "og_render.render_user_images",
            "schedule": crontab(minute=50, hour=4, day_of_week=1),
            "options": {"queue": "parkrun"},
        },
        # Страховочные вечерние прогоны в понедельник: если субботний или
        # ночной прошли по неполным данным (синк опоздал, парк был под
        # кулдауном), к началу недели картинки всё равно свежие.
        "og-render-monday-evening": {
            "task": "og_render.render_location_images",
            "schedule": crontab(minute=0, hour=21, day_of_week=1),
            "options": {"queue": "parkrun"},
        },
        "og-render-users-monday-evening": {
            "task": "og_render.render_user_images",
            "schedule": crontab(minute=20, hour=21, day_of_week=1),
            "options": {"queue": "parkrun"},
        },
        # Очередь five_verst обслуживает один воркер с concurrency=1, поэтому
        # задачи разведены по минутам: старт в одну и ту же минуту не даёт
        # параллельности, а только выстраивает пачку в хвост (до 08.2026 в 20:00
        # будней стартовали разом latest + реестр + ротация). Приоритет минуты
        # :00 — у latest: это свежие субботние протоколы.
        # Правило молчания: после реестра 5 вёрст, чтобы догадка по датам
        # ложилась поверх свежих заявлений систем, а не спорила с ними.
        # Очередь celery, а не "default": так называется дефолтная очередь
        # (task_default_queue), и только её слушает сервис worker — он стартует
        # без -Q. Очередь с буквальным именем "default" не разбирает никто, и
        # правило молчания с 20.08.2026 копилось в ней невостребованным
        # (проверено на проде 27.08.2026: ни один статус пересчитан не был).
        # Погода на стартах: лимит Open-Meteo сбрасывается в полночь UTC (03:00
        # МСК); пока идёт бэкфил — по прогону в сутки, потом докачка суббот.
        # Прогноз на ближайшую субботу (решения Дмитрия 17.09.2026): начинаем в
        # понедельник — в воскресенье до старта ещё шесть дней, и прогноз был бы
        # гаданием. Ночной час выбран сразу после сброса суточного лимита
        # Open-Meteo (полночь UTC), чтобы архивная пересборка не съела бюджет:
        # прогнозу нужен один вызов на локацию.
        "weather-forecast-weekdays": {
            "task": "weather.collect_forecast",
            # Пн–Чт и суббота: в саму субботу утром прогноз ещё нужен — люди
            # смотрят его перед выходом.
            "schedule": crontab(hour=3, minute=5, day_of_week="1-4,6"),
            "options": {"queue": "celery"},
        },
        # Пятница — день, когда выбирают, куда ехать: обновляем трижды, утром,
        # днём и вечером, чтобы к вечеру прогноз был свежим.
        "weather-forecast-friday": {
            "task": "weather.collect_forecast",
            "schedule": crontab(hour="8,14,20", minute=0, day_of_week="5"),
            "options": {"queue": "celery"},
        },
        "weather-collect-daily": {
            "task": "weather.collect_start_weather",
            "schedule": crontab(hour=3, minute=20),
            "options": {"queue": "celery"},
        },
        # Субботним вечером — предварительная погода из прогнозной модели, чтобы
        # «последняя пробежка» показывала её в тот же день; архив заменит в понедельник.
        "weather-collect-preliminary-saturday": {
            "task": "weather.collect_preliminary",
            "schedule": crontab(hour=17, minute=0, day_of_week="6"),
            "options": {"queue": "celery"},
        },
        # Зависшие sync_runs: раньше их гасило только открытие админской
        # страницы, и висяки жили неделями. Задача внутри базы, сети нет.
        # Сторож приоритетных очередей: 17–19.09.2026 очередь свежести стояла
        # двое суток, и заметил это человек глазами на сайте, а не мониторинг.
        # Общая очередь обязательна: сторож не может стоять в той очереди, за
        # которой следит.
        "priority-queues-watch": {
            "task": "queues.watch_priority",
            "schedule": crontab(minute="*/15"),
            "options": {"queue": "celery", "expires": 14 * 60},
        },
        "sync-runs-close-stale": {
            "task": "sync_runs.close_stale",
            "schedule": crontab(minute=5),
            "options": {"queue": "celery", "expires": 55 * 60},
        },
        "locations-activity-status": {
            "task": "locations.refresh_activity_status",
            "schedule": crontab(hour=21, minute=10),
            "options": {"queue": "celery"},
        },
        # Уведомления, застрявшие в queued/failed (брокер моргнул, канал лежал):
        # подметальщик раз в 10 минут, внутри базы и сети каналов.
        "notifications-retry-queued": {
            "task": "notifications.retry_queued",
            "schedule": crontab(minute="*/10"),
            "options": {"queue": "celery", "expires": 9 * 60},
        },
        # Копии уведомлений админу копятся в Redis и уходят сводкой на текст:
        # раз в минуту отправляем рассылки, которые затихли.
        "notifications-flush-admin-copies": {
            "task": "notifications.flush_admin_copies",
            "schedule": crontab(),
            "options": {"queue": "celery", "expires": 55},
        },
        # Результаты, записанные мимо воркеров (parkrun с Mac-демона): раз в
        # десять минут ищем у включивших уведомления новые строки run_results.
        "notifications-scan-new-results": {
            "task": "notifications.scan_new_results",
            "schedule": crontab(minute="3-59/10"),
            "options": {"queue": "celery", "expires": 9 * 60},
        },
        # Движение в рейтингах — раз в неделю, в воскресенье в 14:00 МСК:
        # протоколы субботы к этому часу догружены, место уже не прыгает.
        "notifications-weekly-ratings": {
            "task": "notifications.weekly_ratings",
            "schedule": crontab(hour=14, minute=0, day_of_week="0"),
            "options": {"queue": "celery", "expires": 6 * 3600},
        },
        "five-verst-registry-daily": {
            "task": "five_verst_sync.sync_locations_registry",
            # 20:50 — после latest 20:00; до сводки 21:50 успевает (~1.5 мин).
            "schedule": crontab(hour=20, minute=50),
            "options": {"queue": FIVE_VERST_BATCH_QUEUE, "expires": 6 * 3600},
        },
        # expires у периодических задач — прививка от долгов очереди: не взяли
        # вовремя — задача умирает, а не копится. 12.09.2026 в очереди five_verst
        # лежало 26 просроченных latest и 30 сверок, и субботние протоколы
        # ждали своей минуты полутора суток.
        "five-verst-latest-weekday": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0,5,10,15,20", minute=0, day_of_week="1-5"),
            "options": {"queue": FIVE_VERST_FRESH_QUEUE, "expires": 4 * 3600},
        },
        "five-verst-latest-saturday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="1-23", minute=0, day_of_week=6),
            "options": {"queue": FIVE_VERST_FRESH_QUEUE, "expires": 55 * 60},
        },
        "five-verst-latest-sunday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0-23", minute=0, day_of_week=0),
            "options": {"queue": FIVE_VERST_FRESH_QUEUE, "expires": 55 * 60},
        },
        "five-verst-location-rotation": {
            "task": "five_verst_sync.sync_location_rotation",
            "schedule": crontab(minute=30, hour="*/4"),
            "options": {"queue": FIVE_VERST_BATCH_QUEUE, "expires": 4 * 3600},
        },
        # Наблюдатель выгрузки протоколов (наследник легаси-крона из
        # /root/scripts): сб и вс — каждую минуту (01:00–23:59 MSK, чтобы
        # поймать и Дальний Восток, и вечерние догрузки), будни — раз в
        # 30 минут (спецзабеги 1 января и переносы). Очередь общая: запрос
        # один и крошечный, а в five_verst он вставал бы за reconcile.
        # Очередь приходится задавать явно: без options задачу забирает правило
        # `five_verst_sync.*` из task_routes и уводит ровно в ту очередь, от
        # которой её здесь и отводят (обнаружено 27.08.2026 — падал тест
        # test_five_verst_queue_no_same_minute_collisions).
        # Ритм согласован с Дмитрием 03.09.2026: суббота с 01:00 МСК — раз в
        # минуту (основная волна выгрузок), воскресенье — раз в 5 минут
        # (догрузки и Дальний Восток), будни — раз в 30 минут (1 января,
        # переносы). Порог непрерывности наблюдения в five_verst_protocol_watch
        # подобран под самый редкий шаг — менять вместе.
        "five-verst-protocol-watch-saturday": {
            "task": "five_verst_sync.protocol_upload_watch",
            "schedule": crontab(minute="*", hour="1-23", day_of_week="6"),
            "options": {"queue": "celery"},
        },
        "five-verst-protocol-watch-sunday": {
            "task": "five_verst_sync.protocol_upload_watch",
            "schedule": crontab(minute="*/5", day_of_week="0"),
            "options": {"queue": "celery"},
        },
        "five-verst-protocol-watch-weekday": {
            "task": "five_verst_sync.protocol_upload_watch",
            "schedule": crontab(minute="0,30", day_of_week="1-5"),
            "options": {"queue": "celery"},
        },
        # Сверка истории протоколов — только по будням: прогон занимает пару
        # часов (200 протоколов через паузы между фетчами), и в выходные он
        # задерживал часовой latest, из-за чего свежие субботние протоколы
        # опаздывали (скипы duplicate_hour_slot на проде). В будни новых
        # результатов нет — латентность latest там не важна.
        "five-verst-reconcile-protocols-weekday": {
            "task": "five_verst_sync.reconcile_stale_protocols",
            "schedule": crontab(minute=10, hour="*/3", day_of_week="1-5"),
            "options": {"queue": FIVE_VERST_BATCH_QUEUE, "expires": 3 * 3600},
        },
        # Обход протоколов недели — третья страховка легаси-схемы (см.
        # app/sync/five_verst_week_sweep.py). Сводка знает только число
        # финишёров, число волонтёров и три времени; правку внутри протокола
        # (роль волонтёра, привязка к атлету, имя, позиция) не видит ни сверка,
        # ни ротация — обе сравнивают наш протокол с нашей же сводкой. Здесь
        # протоколы субботы перекачиваются целиком, без оглядки на сводку.
        # График Дмитрия: пн и чт — последняя суббота W (догрузки приезжают в
        # начале недели, поэтому два взгляда), ср — W−1, пт — W−2.
        # Минута :20 свободна: сверка на :10, ротация на :30, latest на :00.
        "five-verst-week-sweep-w0-monday": {
            "task": "five_verst_sync.sweep_week_protocols",
            "schedule": crontab(hour=2, minute=20, day_of_week="1"),
            "kwargs": {"weeks_back": 0},
            "options": {"queue": "five_verst"},
        },
        "five-verst-week-sweep-w1-wednesday": {
            "task": "five_verst_sync.sweep_week_protocols",
            "schedule": crontab(hour=2, minute=20, day_of_week="3"),
            "kwargs": {"weeks_back": 1},
            "options": {"queue": "five_verst"},
        },
        "five-verst-week-sweep-w0-thursday": {
            "task": "five_verst_sync.sweep_week_protocols",
            "schedule": crontab(hour=2, minute=20, day_of_week="4"),
            "kwargs": {"weeks_back": 0},
            "options": {"queue": "five_verst"},
        },
        "five-verst-week-sweep-w2-friday": {
            "task": "five_verst_sync.sweep_week_protocols",
            "schedule": crontab(hour=2, minute=20, day_of_week="5"),
            "kwargs": {"weeks_back": 2},
            "options": {"queue": "five_verst"},
        },
        # Старты сообществ (/starti-soobshchestv/) — суббота вечером и среда
        # (ритм Дмитрия 07.09.2026). Раздел крошечный (два старта на
        # 07.09.2026) и пополняется редко, но его не видит ни один другой
        # синк: там нет ни реестровой строки, ни таблицы площадки. Суббота —
        # чтобы свежий старт приехал в тот же день, среда — чтобы поймать
        # поздний разбор протокола. Минута :40 свободна: latest на :00,
        # сверка на :10, обход недели на :20, ротация на :30.
        "five-verst-community-events-saturday": {
            "task": "five_verst_sync.sync_community_events",
            "schedule": crontab(hour=22, minute=40, day_of_week="6"),
            "options": {"queue": "five_verst"},
        },
        "five-verst-community-events-wednesday": {
            "task": "five_verst_sync.sync_community_events",
            "schedule": crontab(hour=22, minute=40, day_of_week="3"),
            "options": {"queue": "five_verst"},
        },
        # Clubs list (/clubs/) — twice a week; changed rows are queued for detail re-sync.
        "five-verst-clubs-registry": {
            "task": "five_verst_sync.sync_clubs_registry",
            "schedule": crontab(hour=21, minute=30, day_of_week="1,4"),
            "options": {"queue": "five_verst"},
        },
        # Club detail rotation — 3×/day, 20 stalest clubs per run (changed ones jump the queue).
        "five-verst-clubs-details": {
            "task": "five_verst_sync.sync_club_details",
            # Каждые 3 часа вместо 3 раз в сутки: куски стали втрое короче
            # (8 клубов вместо 20), и суточный объём добирается числом заходов.
            # Минута :45 свободна: latest на :00, сверка на :10, ротация на :30.
            "schedule": crontab(hour="*/3", minute=45),
            "options": {"queue": FIVE_VERST_BATCH_QUEUE, "expires": 3 * 3600},
        },
        # S95 location registry — every 3 days at 20:30 MSK via JSON API (s95.ru/by/rs).
        "s95-registry-3days": {
            "task": "s95_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=30, day_of_month="*/3"),
            "options": {"queue": "s95"},
        },
        # Отмены ближайшего старта у S95 — четыре раза в сутки, отдельно от
        # реестра. Реестр ходит раз в трое суток: объявление про субботу
        # (Иваново, 27.08.2026) при таком темпе могло доехать до сайта уже
        # после старта. Проход дешёвый: два JSON-запроса на домен и страница
        # только у тех площадок, что реестр объявил неработающими.
        "s95-cancellations-watch": {
            "task": "s95_sync.watch_cancellations",
            "schedule": crontab(minute=40, hour="0,6,12,18"),
            "options": {"queue": "s95"},
        },
        # Описания площадок S95 (HTML /events/{slug}) — каждые 4 часа по 5 самых
        # давно не обновлявшихся. Локаций у S95 ~35, полный круг ≈ сутки.
        # :50 — подальше от реестра (:30) и от протоколов (:00), чтобы не
        # толкаться за общий лок загрузок S95.
        "s95-location-descriptions": {
            "task": "s95_sync.sync_location_descriptions",
            "schedule": crontab(minute=50, hour="*/4"),
            "options": {"queue": "s95"},
        },
        # New protocols scan (JSON API, updated_at-aware) — Sat & Sun at 11:00 / 17:00 / 23:00 MSK.
        "s95-api-new-protocols-weekend": {
            "task": "s95_sync.api_new_protocols",
            "schedule": crontab(hour="11,17,23", minute=0, day_of_week="6,0"),
            "options": {"queue": "s95"},
        },
        # Сверка состава событий со списками площадок — раз в неделю, вторник
        # 04:10 МСК. Оба синка выше ходят по updated_at и потому слепы к тому,
        # что S95 удалил событие: про удаление узнать неоткуда, а нумерация от
        # него едет у всех последующих стартов (Великий Новгород, 165 номеров,
        # 14.09.2026). Протоколы не качает — только списки, поэтому дёшево.
        # Вторник: подальше от выходных скана и понедельничного синка.
        "s95-reconcile-events-weekly": {
            "task": "s95_sync.reconcile_events",
            "schedule": crontab(hour=4, minute=10, day_of_week="2"),
            "options": {"queue": "s95"},
        },
        # Sync new + updated protocols across all locations via updated_at — Mon/Wed/Fri 03:00 MSK.
        "s95-api-sync-updated": {
            "task": "s95_sync.api_sync_updated",
            "schedule": crontab(hour=3, minute=0, day_of_week="1,3,5"),
            "options": {"queue": "s95"},
        },
        # Серверный разбор очереди parkrun (только user-запросы), когда Mac-демон
        # не запущен и охлаждение после капчи истекло. Смещение от :00, чтобы не
        # толкаться с другими задачами.
        "parkrun-pending-queue": {
            "task": "parkrun_sync.process_pending_queue",
            "schedule": crontab(minute="7,37"),
            "options": {"queue": "parkrun"},
        },
        "runpark-latest": {
            "task": "runpark_sync.sync_latest",
            "schedule": crontab(hour="3,8,13,18,23", minute=0),
            "options": {"queue": "runpark"},
        },
        # Досвязка отставших протоколов RunPark — раз в сутки, через полчаса
        # после ночного батча. Обычно связывать нечего; работа появляется, когда
        # протокол основной платформы доехал позже семидневного окна батча.
        "runpark-backfill-crosslinks": {
            "task": "runpark_sync.backfill_crosslinks",
            "schedule": crontab(hour=3, minute=30),
            "options": {"queue": "runpark"},
        },
        # Прогрев кэша рейтингов (TTL 6ч): каждые 2 часа, со сдвигом от :00.
        # Очередь warm, её разбирает отдельный воркер рядом с базой. Это страховка и
        # обещанный витриной срок пересчёта (REFRESH_INTERVAL_HOURS в
        # app/services/leaderboard_service.py — парное место, менять вместе);
        # свежие протоколы доезжают быстрее: каждый синк, записавший результаты,
        # будит тот же прогрев сам (schedule_leaderboards_warm).
        "leaderboards-warm-cache": {
            "task": "leaderboards.warm_cache",
            "schedule": crontab(minute=20, hour="*/2"),
            "options": {"queue": WARM_QUEUE},
        },
        # Прогрев кэша локаций (TTL 3ч): каждые 2 часа, со сдвигом от рейтингов
        # (:20) — чтобы два тяжёлых прогрева не шли одновременно на одном воркере.
        "locations-warm-cache": {
            "task": "locations.warm_cache",
            "schedule": crontab(minute=40, hour="*/2"),
            "options": {"queue": WARM_QUEUE},
        },
        # Прогрев Redis-кэша главной портала (TTL 24ч) — раз в час, чтобы ни один
        # запрос не попадал на холодный пересчёт (~2 мин на проде) и данные не
        # успевали устаревать. Дефолтная очередь "celery": её обслуживает сервис
        # worker (без -Q) — в проде он до 18.07.2026 был выключен профилем, и
        # прогрев не выполнялся ни разу.
        # expires: просроченный прогрев бессмысленно догонять — свежий всё равно
        # придёт по расписанию. Без этого простой воркера копит в очереди десятки
        # одинаковых задач, и после старта он часами гоняет их подряд по БД
        # (18.07.2026 так накопился 41 таск).
        # Частота прогрева привязана к тому, когда данные реально меняются, а не
        # «на всякий случай ежечасно»: TTL кэша 24ч, и до 24.07.2026 прогрев шёл
        # каждый час — 23 из 24 запусков пересчитывали заведомо свежий кэш. Один
        # прогрев стоит ~59 сек времени БД и ~300 МБ временных файлов, то есть
        # впустую уходило ~24 мин работы базы и ~7 ГБ спилла в сутки.
        # Выходные — ежечасно: субботние/воскресные протоколы приезжают каждый час
        # (five-verst-latest-*-hourly), и главная должна их показывать сразу.
        "portal-home-cache-warm-weekend": {
            "task": "portal_cache.warm_home",
            "schedule": crontab(minute=15, day_of_week="6,0"),
            "options": {"expires": 30 * 60},
        },
        # Будни — 4 раза в сутки, каждый раз через час после runpark-latest
        # (3,8,13,18): это единственный будний источник новых результатов.
        "portal-home-cache-warm-weekday": {
            "task": "portal_cache.warm_home",
            "schedule": crontab(minute=15, hour="4,9,14,19", day_of_week="1-5"),
            "options": {"expires": 30 * 60},
        },
        # Агрегаты популярности страниц (сегодня+вчера, upsert) + чистка сырых
        # событий старше retention. Дефолтная очередь "celery" — общий worker;
        # :35 — в стороне от прогревов (:15/:20/:40).
        "page-stats-rollup": {
            "task": "page_stats.rollup",
            "schedule": crontab(minute=35),
            "options": {"expires": 30 * 60},
        },
        # Единственное регулярное сообщение админу в ВК: итоги автообновления за
        # сутки (раньше на каждый запуск уходило по два сообщения). 21:50 МСК —
        # после вечернего реестра 5 вёрст (20:00) и реестра s95 (20:30), чтобы
        # они попали в сводку того же дня. Заодно чистит старую историю запусков.
        "admin-sync-daily-summary": {
            "task": "admin_digest.daily_sync_summary",
            "schedule": crontab(hour=21, minute=50),
            # Сводка за сутки, отправленная через сутки, — спам, а не сводка.
            "options": {"expires": 6 * 60 * 60},
        },
    },
)


# --------------------------------------------------------------- срок годности
#
# `expires` — прививка от долгов очереди: не взяли вовремя — задача умирает, а
# не копится. 12.09.2026 в очереди five_verst лежало 26 просроченных latest и
# 30 сверок, и субботние протоколы ждали своей минуты полутора суток; 18.07.2026
# так же накопился 41 прогрев главной. До 21.09.2026 срок стоял у половины
# записей, остальные (sweep-hq, наблюдатель протоколов, og-render, s95-*,
# runpark-*, реестры) копились молча.
#
# Считаем его из самого расписания, а не пишем руками: правило «expires ≈
# интервал запуска» тогда держится само, даже если кто-то поменяет крон. Явно
# заданный в записи срок не трогаем — там, где выбрано короче интервала (прогрев
# главной, агрегаты страниц), это осознанное решение.

MAX_EXPIRES_SECONDS = 6 * 3600
"""Потолок срока годности.

Задача, опоздавшая больше чем на шесть часов, не догоняет ничего: свежие данные
принесёт следующий запуск. Столько же выбрано руками у суточных записей
(реестр 5 вёрст, сводка в ВК) — держим одну планку.
"""


def schedule_interval_seconds(schedule: crontab, *, window_days: int = 70) -> int | None:
    """Наименьший промежуток между двумя срабатываниями crontab-записи.

    Именно наименьший: у записи вроде «часы 0,5,10,15,20 по будням» промежутки
    разные (5 часов внутри дня, 4 часа через полночь, 76 часов через выходные),
    и срок годности надо мерить по самому короткому — иначе две соседние задачи
    успеют встретиться в очереди.

    Считаем по разобранным множествам crontab, а не гоняем
    `crontab.remaining_estimate` вперёд по времени: он считает относительно
    «сейчас» и на датах дальше сегодняшней начинает возвращать прошлое.
    Окно в 70 дней покрывает и day_of_month="*/3", и месячные записи.
    """
    minutes = sorted(schedule.minute)
    hours = sorted(schedule.hour)
    if not minutes or not hours:
        return None

    today = date.today()
    days = [
        day
        for day in (today + timedelta(days=shift) for shift in range(window_days))
        if day.month in schedule.month_of_year
        and day.day in schedule.day_of_month
        and day.isoweekday() % 7 in schedule.day_of_week
    ]

    first = hours[0] * 60 + minutes[0]  # первое срабатывание внутри суток
    last = hours[-1] * 60 + minutes[-1]  # последнее срабатывание внутри суток
    gaps_minutes: list[int] = []
    gaps_minutes += [b - a for a, b in zip(minutes, minutes[1:], strict=False)]
    if len(hours) > 1:
        step = min(b - a for a, b in zip(hours, hours[1:], strict=False))
        gaps_minutes.append(step * 60 - (minutes[-1] - minutes[0]))
    if len(days) > 1:
        step_days = min((b - a).days for a, b in zip(days, days[1:], strict=False))
        gaps_minutes.append(step_days * 24 * 60 - last + first)
    if not gaps_minutes:
        return None
    return min(gaps_minutes) * 60


def _fill_missing_expires(app: Celery) -> None:
    for entry in app.conf.beat_schedule.values():
        options = entry.setdefault("options", {})
        if "expires" in options:
            continue
        interval = schedule_interval_seconds(entry["schedule"])
        if interval:
            options["expires"] = min(interval, MAX_EXPIRES_SECONDS)


_fill_missing_expires(celery_app)
