from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.platform_adapters.registry import ensure_adapters_registered

settings = get_settings()

celery_app = Celery("saturday_runs", broker=settings.redis_url, backend=settings.redis_url)
ensure_adapters_registered()
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    task_track_started=True,
    imports=(
        "app.workers.tasks.global_sync",
        "app.workers.tasks.five_verst_sync",
        "app.workers.tasks.user_sync",
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
        "app.workers.tasks.sweep_hq_snapshot",
        "app.workers.tasks.email_send",
        "app.workers.tasks.user_names",
    ),
    task_routes={
        "five_verst_sync.*": {"queue": "five_verst"},
        "global_sync.*": {"queue": "five_verst"},
        "user_sync.*": {"queue": "five_verst_user"},
        "s95_sync.run_user_sync": {"queue": "s95_user"},
        "s95_sync.*": {"queue": "s95"},
        "parkrun_sync.*": {"queue": "parkrun"},
        "runpark_sync.*": {"queue": "runpark"},
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
        # Табло обхода /hq и /world: пересчёт тяжёлых агрегатов раз в 3 минуты.
        # Считать на каждый показ нельзя — один только count(*) по runs (124 млн
        # строк) занимал 5.5 с из 6.7 с ответа.
        "sweep-hq-snapshot": {
            "task": "sweep_hq.refresh_snapshot",
            "schedule": crontab(minute="*/3"),
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
        "locations-activity-status": {
            "task": "locations.refresh_activity_status",
            "schedule": crontab(hour=21, minute=10),
            "options": {"queue": "default"},
        },
        "five-verst-registry-daily": {
            "task": "five_verst_sync.sync_locations_registry",
            # 20:50 — после latest 20:00; до сводки 21:50 успевает (~1.5 мин).
            "schedule": crontab(hour=20, minute=50),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-weekday": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0,5,10,15,20", minute=0, day_of_week="1-5"),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-saturday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="1-23", minute=0, day_of_week=6),
            "options": {"queue": "five_verst"},
        },
        "five-verst-latest-sunday-hourly": {
            "task": "five_verst_sync.sync_latest_results",
            "schedule": crontab(hour="0-23", minute=0, day_of_week=0),
            "options": {"queue": "five_verst"},
        },
        "five-verst-location-rotation": {
            "task": "five_verst_sync.sync_location_rotation",
            "schedule": crontab(minute=30, hour="*/4"),
            "options": {"queue": "five_verst"},
        },
        # Наблюдатель выгрузки протоколов (наследник легаси-крона из
        # /root/scripts): сб и вс — каждую минуту (01:00–23:59 MSK, чтобы
        # поймать и Дальний Восток, и вечерние догрузки), будни — раз в
        # 30 минут (спецзабеги 1 января и переносы). Очередь общая: запрос
        # один и крошечный, а в five_verst он вставал бы за reconcile.
        "five-verst-protocol-watch-weekend": {
            "task": "five_verst_sync.protocol_upload_watch",
            "schedule": crontab(minute="*", hour="1-23", day_of_week="6,0"),
        },
        "five-verst-protocol-watch-weekday": {
            "task": "five_verst_sync.protocol_upload_watch",
            "schedule": crontab(minute="0,30", day_of_week="1-5"),
        },
        # Сверка истории протоколов — только по будням: прогон занимает пару
        # часов (200 протоколов через паузы между фетчами), и в выходные он
        # задерживал часовой latest, из-за чего свежие субботние протоколы
        # опаздывали (скипы duplicate_hour_slot на проде). В будни новых
        # результатов нет — латентность latest там не важна.
        "five-verst-reconcile-protocols-weekday": {
            "task": "five_verst_sync.reconcile_stale_protocols",
            "schedule": crontab(minute=10, hour="*/3", day_of_week="1-5"),
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
            "schedule": crontab(hour="9,15,23", minute=30),
            "options": {"queue": "five_verst"},
        },
        # S95 location registry — every 3 days at 20:30 MSK via JSON API (s95.ru/by/rs).
        "s95-registry-3days": {
            "task": "s95_sync.sync_locations_registry",
            "schedule": crontab(hour=20, minute=30, day_of_month="*/3"),
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
        # Прогрев кэша рейтингов (TTL 6ч): каждые 2 часа, со сдвигом от :00,
        # чтобы не толкаться с runpark-latest на том же воркере. Это страховка и
        # обещанный витриной срок пересчёта (REFRESH_INTERVAL_HOURS в
        # app/services/leaderboard_service.py — парное место, менять вместе);
        # свежие протоколы доезжают быстрее: каждый синк, записавший результаты,
        # будит тот же прогрев сам (schedule_leaderboards_warm).
        "leaderboards-warm-cache": {
            "task": "leaderboards.warm_cache",
            "schedule": crontab(minute=20, hour="*/2"),
            "options": {"queue": "runpark"},
        },
        # Прогрев кэша локаций (TTL 3ч): каждые 2 часа, со сдвигом от рейтингов
        # (:20) — чтобы два тяжёлых прогрева не шли одновременно на одном воркере.
        "locations-warm-cache": {
            "task": "locations.warm_cache",
            "schedule": crontab(minute=40, hour="*/2"),
            "options": {"queue": "runpark"},
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
