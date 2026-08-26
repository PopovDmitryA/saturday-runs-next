from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    app_env: str = "development"
    app_debug: bool = False
    app_secret_key: str = "change-me-in-production"
    app_base_url: str = "http://localhost:8080"

    # OG-превью (Л19): куда celery-задача складывает готовые PNG (nginx раздаёт
    # /og/locations/* из этой папки) и по какому адресу Playwright открывает
    # внутренний рендер-роут фронта (docker-сеть).
    og_image_dir: str = "/data/og"
    og_render_base_url: str = "http://nginx"

    database_url: str = "postgresql+psycopg://saturday_runs:saturday_runs@localhost:5433/saturday_runs_lk"
    redis_url: str = "redis://localhost:6379/0"

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Хранилище аватарок пользователей (том ./data:/data в docker-compose).
    # В БД лежит только имя файла (users.avatar_path), файлы — здесь.
    avatars_dir: str = "/data/avatars"

    # Публичное S3-совместимое хранилище пользовательских картинок (фото в
    # отзывах на локации, фото в карточках бэклога, аватарки). Пустой
    # s3_bucket = режим локального диска (media_dir + отдача через
    # /api/media/...), чтобы dev и тесты работали без облака —
    # см. app/core/media_storage.py.
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "ru-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # База для публичных ссылок. Пусто — собирается из endpoint + bucket.
    s3_public_base_url: str = ""
    # Разные папки в бакете под каждого потребителя (решение Дмитрия 28.07.2026).
    s3_prefix_location_reviews: str = "location-reviews"
    s3_prefix_backlog: str = "backlog"
    s3_prefix_avatars: str = "avatars"
    # Локальный фолбэк, когда S3 не сконфигурирован (том ./data:/data).
    media_dir: str = "/data/media"

    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_bot_internal_secret: str = ""
    telegram_admin_chat_id: int = 0
    admin_telegram_id: int = 0
    # Прокси для ВСЕГО трафика к api.telegram.org (long-poll бота + уведомления):
    # с прод-сервера в РФ прямые запросы не проходят вообще, бот без прокси
    # уходит в крэш-луп на get_me(). Держим http://, а не socks5://: его понимают
    # нативно и httpx, и aiohttp (aiogram) — socks5 у aiohttp требует aiohttp_socks.
    # Поднимается сервисом tg-proxy, см. deploy/tg-proxy/. Пусто — напрямую.
    telegram_proxy_url: str = ""
    # Comma-separated emails (OAuth). Grants admin if any linked auth_identity matches (case-insensitive).
    admin_emails: str = ""
    demo_telegram_id: int = 0
    demo_user_id: str = ""

    # Internal read-only reporting API (/api/internal/reports/*).
    # Пусто — эндпоинт выключен (503). Bearer-токен передаётся в заголовке.
    report_api_token: str = ""
    report_query_max_rows: int = 5000
    report_query_timeout_seconds: int = 15
    # Отдельный коннект под read-only ролью. Пусто — используется database_url.
    report_database_url: str = ""

    session_cookie_name: str = "sr_session"
    # Долгоживущая метка устройства для лимита регистраций (core/signup_guard.py).
    # К сессии отношения не имеет: живёт и у анонима, и после выхода.
    device_cookie_name: str = "sr_device"
    # «На этом устройстве уже входили». Ставится после успешного входа любым
    # способом и переживает выход. Нужна экрану входа: тому, кто уже принимал
    # условия обработки данных, галочку показывать не за чем — согласие
    # хранится в профиле. Читается фронтом, поэтому без HttpOnly; ничего,
    # кроме факта «здесь уже входили», в ней нет.
    known_device_cookie_name: str = "sr_known"
    # Скользящая сессия: TTL отсчитывается заново при каждом визите (см. core/session.py),
    # поэтому вылет только после месяца полного отсутствия.
    session_ttl_seconds: int = 30 * 24 * 3600
    magic_link_ttl_seconds: int = 300
    login_request_ttl_seconds: int = 600

    auth_rate_limit_login_per_ip: int = 5
    auth_rate_limit_login_window_seconds: int = 600
    auth_rate_limit_magic_per_telegram: int = 10
    auth_rate_limit_magic_window_seconds: int = 3600

    vk_oauth_client_id: str = ""
    vk_oauth_client_secret: str = ""
    vk_oauth_redirect_uri: str = ""

    yandex_oauth_client_id: str = ""
    yandex_oauth_client_secret: str = ""
    yandex_oauth_redirect_uri: str = ""
    # Empty = omit scope param; Yandex uses rights from app registration (recommended).
    yandex_oauth_scopes: str = ""

    user_login_auto_sync_interval_seconds: int = 86400
    sync_refresh_rate_limit_per_user: int = 1
    sync_refresh_rate_limit_window_seconds: int = 1800

    # Прогрев дашбордов идёт от водяного знака «докуда уже разобрано». Если знак
    # потерян (Redis перезапустили) — не сканируем всю историю, а откатываемся на
    # это окно: дальше в прошлое кэш всё равно чинит dashboard_cache_max_age_hours.
    dashboard_warm_max_lookback_hours: int = 168
    # Страховка на чтении: кэш старше этого срока пересчитывается при заходе,
    # даже если прогрев его почему-то не тронул. Без неё промах прогрева жил вечно
    # (08.08.2026: у 27 человек на «Обзоре» не хватало забегов S95).
    dashboard_cache_max_age_hours: int = 24

    # Рейтинги пересчитываются от протоколов, а не только по расписанию: каждый
    # синк, записавший результаты, будит прогрев. Окно склейки — чтобы серия
    # синков подряд (суббота: 5 вёрст ежечасно + S95 + RunPark) не выстроила
    # очередь из полных пересчётов: пока окно не истекло, новые не планируются.
    leaderboards_warm_debounce_seconds: int = 1800
    # Пауза перед прогревом: за неё успевают дописаться протоколы соседних
    # систем, синхронизирующихся следом, и они попадают в тот же пересчёт.
    leaderboards_warm_delay_seconds: int = 120

    # Сырые события page_view_events живут столько дней; вечная история — в page_stats_daily.
    page_events_retention_days: int = 90
    # Журнал входов держим дольше сырых просмотров: его ценность как раз в
    # длинной истории — потеря сессии случается редко и ловится ретроспективно.
    login_events_retention_days: int = 365

    # Исходящая почта (ящик support@run5k.run на почтовом кластере Timeweb).
    # Сервер сайта писем не принимает и своего MTA не поднимает: отправляем
    # через чужой SMTP с авторизацией — домашний или хостерский IP без PTR
    # почтовые провайдеры не любят.
    #
    # Логин и адрес отправителя разделены нарочно: пока ящик один, во всех трёх
    # полях он же, но появится второй — разведём правкой .env, без правки кода.
    # Пустой smtp_from_email/smtp_reply_to означает «взять smtp_user».
    smtp_enabled: bool = False
    smtp_host: str = "smtp.timeweb.ru"
    smtp_port: int = 465
    # 465 — сразу SSL, 587 — STARTTLS поверх открытого соединения.
    smtp_use_ssl: bool = True
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    # Имя отправителя в письме. Здесь цифры разрешены (в отличие от поля «ФИО»
    # в панели Timeweb), поэтому показываем человеку домен сайта.
    smtp_from_name: str = "run5k.run"
    smtp_reply_to: str = ""
    smtp_timeout_seconds: float = 20.0

    # Вход по одноразовому коду на почту (app/services/email_auth_service.py).
    # Код шестизначный: миллион вариантов против пяти попыток — перебор
    # безнадёжен, а переписать шесть цифр с телефона на компьютер человек ещё
    # готов. Десять минут жизни кода покрывают задержку почты, не превращая
    # письмо в вечный ключ от профиля.
    email_login_enabled: bool = True
    email_login_code_length: int = 6
    email_login_code_ttl_seconds: int = 600
    email_login_max_attempts: int = 5
    # Сколько кодов на ящик действуют одновременно. Новый не отменяет старые:
    # почта приходит с задержкой, и человек часто вводит цифры из письма,
    # которое открыл первым. Каждый код по-прежнему одноразовый и живёт свои
    # десять минут, а счётчик попыток общий — перебор от этого не легче.
    email_login_active_codes: int = 3
    # Ведро на адрес бережёт чужой ящик от того, кто вписал его ради спама;
    # ведро на IP — нас от перебора адресов с одной машины. Пять, а не три:
    # три упираются в потолок раньше, чем человек успевает разобраться с
    # задержкой почты.
    email_login_code_per_address: int = 5
    email_login_code_per_ip: int = 10
    email_login_code_window_seconds: int = 3600
    # Потолок на все письма с кодами за сутки — предохранитель против затопления
    # с тысячи адресов: почтовый кластер Timeweb принимает 2000 писем в сутки со
    # всех ящиков сразу, и выжечь этот лимит нельзя — иначе замолчит и остальная
    # почта, а домен наберёт репутацию рассыльщика. При нынешних 16 регистрациях
    # в сутки на весь сайт запас двадцатикратный.
    email_login_codes_per_day: int = 300

    abuse_protection_enabled: bool = True
    abuse_whitelist_ips: str = ""
    abuse_global_limit_per_ip: int = 180
    abuse_global_window_seconds: int = 60
    abuse_public_limit_per_ip: int = 90
    abuse_public_window_seconds: int = 60
    abuse_default_limit_per_ip: int = 120
    abuse_default_window_seconds: int = 60
    abuse_auth_limit_per_ip: int = 80
    abuse_auth_window_seconds: int = 600
    abuse_expensive_limit_per_ip: int = 20
    abuse_expensive_window_seconds: int = 60
    abuse_block_score_threshold: int = 200
    abuse_block_duration_seconds: int = 900
    abuse_severe_block_score_threshold: int = 400
    abuse_severe_block_duration_seconds: int = 86400
    abuse_score_window_seconds: int = 3600
    abuse_global_violation_score: int = 15
    abuse_tier_violation_score: int = 5
    abuse_blocked_retry_score: int = 5

    # Сколько новых профилей можно завести за сутки с одного адреса и с одного
    # устройства. Вход существующим профилем не ограничен вовсе — счётчик
    # трогает только рождение нового пользователя.
    #
    # Пороги выбраны по журналу входов: за всю историю сайта максимум два
    # разных аккаунта приходили с одного IP, и таких адресов было двадцать из
    # трёхсот с лишним. То есть тройка на адрес не задевает ни семьи за общим
    # роутером, ни операторский NAT, но обрывает попытку наплодить профили.
    signup_guard_enabled: bool = True
    signup_limit_per_ip_daily: int = 3
    signup_limit_per_device_daily: int = 2
    signup_guard_window_seconds: int = 86400

    s95_fetch_min_interval_seconds: float = 15.0
    s95_fetch_max_interval_seconds: float = 30.0
    s95_fetch_lock_timeout_seconds: int = 180
    s95_fetch_lock_blocking_seconds: int = 300
    s95_http_timeout_seconds: float = 30.0
    s95_ban_cooldown_seconds: int = 3600
    s95_parkrun_barcode_max_length: int = 8
    parkrun_participant_discovery_enabled: bool = True
    parkrun_participant_discovery_min_refetch_days: int = 7
    s95_global_sync_locations: str = ""
    s95_global_sync_protocol_limit: int = 3

    five_verst_fetch_min_interval_seconds: float = 20.0
    five_verst_fetch_max_interval_seconds: float = 30.0
    five_verst_protocol_min_interval_seconds: float = 30.0
    five_verst_protocol_max_interval_seconds: float = 40.0
    five_verst_fetch_lock_timeout_seconds: int = 120
    five_verst_fetch_lock_blocking_seconds: int = 600
    # Приоритет пользовательских синков: батч замирает между фетчами, пока
    # пользователь качается (см. app/five_verst/fetch/priority.py).
    # TTL отметки — страховка от умершего воркера: батч разморозится сам.
    five_verst_user_sync_active_ttl_seconds: int = 180
    # Потолок паузы батча. Если воркер пользовательской очереди не поднят,
    # очередь копится и разбирать её некому — батч не должен вставать навсегда.
    five_verst_user_sync_pause_max_seconds: int = 300
    five_verst_ban_cooldown_seconds: int = 600
    five_verst_sync_protocol_limit: int | None = None
    five_verst_sync_latest_update_limit: int | None = None
    five_verst_fetch_all_protocols_on_change: bool = True
    five_verst_location_batch_summaries_limit: int = 20
    # Как часто ротация всё же перечитывает саму страницу локации и /course/
    # (имя, координаты), а не только таблицу результатов. Между этими проходами
    # хватает ежедневного реестра /events/, который следит за именем и статусом.
    five_verst_location_refresh_interval_days: int = 7
    s95_sync_protocol_limit: int = 3
    s95_sync_latest_update_limit: int = 20
    s95_fetch_all_protocols_on_change: bool = True
    s95_reconcile_batch_limit: int = 10
    s95_reconcile_min_check_interval_days: int = 7
    s95_location_batch_summaries_limit: int = 20
    s95_athlete_mismatch_check_runs: int = 10
    # Протоколов за один заход воркера. Общая норма прогона — 200 (см.
    # five_verst_reconcile_chunks_per_run): пачка режется пополам, чтобы
    # пользовательский синк не ждал два часа за спиной батча.
    five_verst_reconcile_batch_limit: int = 100
    # Сколько заходов подряд делает один запуск по расписанию. Каждый заход —
    # отдельная celery-задача, и между ними воркер успевает взять задачу из
    # приоритетной очереди five_verst_user.
    five_verst_reconcile_chunks_per_run: int = 2
    # 0 = перечитывать по кругу без пауз (как было до 08.2026). При пуле в 31 тыс.
    # протоколов полный круг и так занимает недели — это страховка от повторных
    # проверок, если поднять лимит пачки или частоту.
    five_verst_reconcile_min_check_interval_days: int = 7
    # Протоколы с доказанным расхождением идут вне очереди и мимо фильтра выше:
    # расхождение, найденное сегодня, чинить надо сегодня. Этот интервал —
    # потолок на случай, когда расхождение перекачкой не лечится (у 5 вёрст
    # бывают старты, где сводка вечно обещает не то число финишёров). Считается
    # от последней ЗАКАЧКИ протокола: потолок «от проверки» спрятал бы сам
    # случай Серова — там расхождение возникло через 8 часов после закачки.
    five_verst_reconcile_mismatch_retry_hours: int = 6
    five_verst_clubs_batch_limit: int = 20

    # Fallback-канал для admin-уведомлений (см. app/services/admin_telegram_notify.py) —
    # используется только когда Telegram-прокси недоступна.
    vk_bot_group_token: str = ""
    vk_admin_user_id: int = 0

    # Секретный токен для скрытой страницы-табло обхода атлетов (/hq/<token>).
    sweep_hq_token: str = ""

    parkrun_base_url: str = "https://www.parkrun.org.uk"
    parkrun_fetch_min_interval_seconds: float = 25.0
    parkrun_fetch_max_interval_seconds: float = 55.0
    parkrun_fetch_between_pages_delay_seconds: float = 10.0
    parkrun_fetch_lock_timeout_seconds: int = 240
    parkrun_fetch_lock_blocking_seconds: int = 600
    parkrun_playwright_headless: bool = True
    parkrun_playwright_page_wait_ms: int = 8000
    parkrun_playwright_navigation_timeout_ms: int = 90000
    # Optional JSON from playwright context.storage_state() after manual captcha (see .env.example)
    parkrun_playwright_storage_state_path: str = ""
    # Chrome remote debugging (Mac: host.docker.internal from Docker api)
    parkrun_cdp_url: str = "http://host.docker.internal:9222"
    # Mac dev: fetch via Chrome CDP (127.0.0.1:9222) instead of headless Docker — avoids WAF captcha loop
    parkrun_use_cdp_for_fetch: bool = False
    # Эскалация охлаждения после капчи/бана parkrun: 1ч → 5ч → 24ч → 72ч → неделя.
    # После истечения ступени следующий фетч — «проба»: успех сбрасывает лестницу,
    # новая капча поднимает на ступень выше.
    parkrun_ban_cooldown_steps_seconds: str = "3600,18000,86400,259200,604800"
    # Серверная обработка очереди profile_fetch_pending (только строки с user_id),
    # когда Mac-демон не запущен и охлаждение истекло.
    parkrun_server_queue_enabled: bool = True
    parkrun_server_queue_batch_size: int = 5

    runpark_mssql_server: str = "runpark.ru"
    runpark_mssql_database: str = "ParkrunLive"
    runpark_mssql_user: str = ""
    runpark_mssql_password: str = ""
    runpark_mssql_timeout: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
