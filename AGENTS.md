# Saturday Runs — справочник для агентов

Документ для AI-агентов и разработчиков: архитектура, prod, синхронизация, типичные задачи.  
Обновлено: **21 сентября 2026**.

**Секреты и пароли:** локальный файл [`PROJECT_HANDOFF.local.md`](PROJECT_HANDOFF.local.md) (в `.gitignore`, не коммитить). Там SSH, prod PG, OAuth, нюансы деплоя.

Связанные документы:

| Документ | Содержание |
|----------|------------|
| [README.md](README.md) | Quick start, auth flow |
| [PROJECT_HANDOFF.local.md](PROJECT_HANDOFF.local.md) | **Credentials, SSH, prod нюансы** (gitignored) |
| [docs/five_verst_sync_plan.md](docs/five_verst_sync_plan.md) | Конвейеры 5 вёрst |
| [docs/s95_sync_plan.md](docs/s95_sync_plan.md) | Конвейеры S95 |
| [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md) | Parkrun (Mac + Chromium) |
| [docs/release_management.md](docs/release_management.md) | Версии и записи релизов |
| [deploy/DOMAINS.md](deploy/DOMAINS.md) | Домены: run5k.run, app.run5k.run, закрытая grafana.run5k.run |
| [docs/archive/](docs/archive/) | Выполненный план переезда с легаси и маппинг ETL — история |
| [docs/runpark/README.md](docs/runpark/README.md) | **RunPark** — локации, маппинг, контракт view |

---

## 1. Назначение проекта

**Saturday Runs** (`run5k.run`) — личный кабинет участника субботних парковых пробежек + global data core.

Платформы:

- **5 вёрst** (`five_verst`) — 5verst.ru
- **S95** (`s95`) — s95.ru / s95.by
- **parkrun** — parkrun.org.uk (fetch не из prod API: очередь профилей разбирает домашний сервер)
- **RunPark** (`runpark`) — внешняя БД, см. [docs/runpark/README.md](docs/runpark/README.md)

Стек: Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery, Redis, PostgreSQL 16, React 19, Vite, Nginx, Docker Compose.

---

## 2. Структура репозитория

```
backend/
  app/                    # FastAPI, models, sync, parsers, services
  app/workers/celery_app.py
  app/workers/tasks/      # five_verst_sync, s95_sync, parkrun_sync, sync_task_reporting
  app/s95/fetch/          # Playwright + Redis lock + priority yield
  app/services/           # dashboard, location_catalog, sync_job, personal_record
  bot_app/                # Telegram: вход + admin-бот (/stats, /status, /sweep, /sync)
  scripts/                # CLI: синки, перечитки, релизы; archive/ — отработавшие бэкфилы
  tests/
  alembic/versions/       # миграции (голова — 090+; см. `ls backend/alembic/versions | tail`)
frontend/src/
  features/               # runs, admin, settings, queue, about, …
  components/             # ActivityDateCell, PlatformBadge, …
data/
  location_catalog.json   # cross-platform location mapping (русские названия parkrun)
docs/
docker-compose.yml        # Dev
docker-compose.prod.yml   # Prod overlay (host PG)
docker-compose.home.yml   # Воркеры синков на домашнем сервере (база и Redis — прод по tailnet)
docker-compose.home-site.yml # Боевой стек целиком на домашнем сервере (переезд)
docker-compose.dev.yml    # VK OAuth на :80
scripts/deploy_prod.sh    # git-based деплой (см. remote_deploy.sh)
scripts/archive/          # отработавшие скрипты Grafana и переезда доменов
Makefile
AGENTS.md                 # этот файл
PROJECT_HANDOFF.local.md  # credentials (gitignored)
```

GitHub: `PopovDmitryA/saturday-runs-next`, branch `main`.

---

## 3. Production

| Параметр | Значение |
|----------|----------|
| Хост | `195.58.34.112` |
| Путь | `/opt/saturday-runs-next` |
| **Основной сайт** | **https://run5k.run** |
| grafana.run5k.run | заглушка «дашборды переехали»; Grafana закрыта 04.09.2026 |
| Старый URL ЛK | `app.run5k.run` → 301 на run5k.run |
| SSH user | `viewer` (пароль в PROJECT_HANDOFF.local.md) |
| Compose | `docker compose -f docker-compose.yml -f docker-compose.prod.yml` |
| БД | Host PostgreSQL (`DATABASE_URL` в prod `.env`) |
| Web | Host nginx (TLS) → Docker nginx :8080 → API + static |

### Контейнеры (prod)

| Сервис | Назначение |
|--------|------------|
Команды — в `docker-compose.yml`, тест `tests/test_celery_beat_schedule.py` и
`tests/test_worker_queue_isolation.py` читают их оттуда же.

| Сервис | Назначение |
|--------|------------|
| `api` | FastAPI, 2 uvicorn workers |
| `nginx` | React `frontend/dist` + proxy `/api` |
| `redis` | Sessions, Celery, locks, cooldown — **не публиковать :6379** (на проде публикуется только на tailnet-адрес для домашних воркеров) |
| `beat` | Celery Beat (Europe/Moscow) |
| `worker` | `-Q celery,warm --concurrency=2`: наблюдатель протоколов, прогрев главной, агрегаты популярности, погода, admin-дайджест |
| `worker-warm` | `-Q warm --concurrency=1`: прогрев рейтингов и страниц локаций (рядом с базой) |
| `worker-five-verst-user` | `-Q five_verst_user --concurrency=1` (синки по кнопке) |
| `worker-parkrun` | `-Q parkrun --max-tasks-per-child=100`: очередь parkrun + og_render (Chromium; пишет картинки в `./data`, которые раздаёт nginx) |
| `bot` | Telegram long poll (вход + admin: /stats /status /sweep /sync); профиль `telegram` |
| `tg-proxy` | xray, выход к api.telegram.org (см. §10) |

Воркеры, которые ходят наружу по HTTP, с 17.09.2026 живут **на домашнем
сервере** (`docker-compose.home.yml`; база и Redis — прод по tailnet). На проде
`remote_deploy.sh` их гасит (`HOME_SERVICES`), а домашний сервер поднимает
`scripts/home_workers_sync.sh` по маркеру деплоя:

| Сервис (дом) | Назначение |
|--------|------------|
| `worker-five-verst` | `-Q five_verst --concurrency=1 --prefetch-multiplier=1` (фон: сверка, ротация, реестр, клубы) |
| `worker-five-verst-fresh` | `-Q five_verst_fresh --concurrency=1` (свежесть: latest) — отдельный контейнер, потому что порядок очередей в `-Q` kombu не гарантирует |
| `worker-s95` | `-Q s95_user,s95 --concurrency=1 --prefetch-multiplier=1 -O fair` |
| `worker-runpark` | `-Q runpark --concurrency=1 --max-tasks-per-child=1` |

### Деплой

```bash
# С Mac (нужны TEMP_SSH_* в .env, sshpass):
bash scripts/deploy_prod.sh
```

Git-based (не rsync, см. scripts/remote_deploy.sh): прод переводится на вершину `origin/main` одним SSH,
затем `npm ci && npm run build` в Docker node, alembic upgrade, rebuild workers + api + bot + nginx.

**Если фронт не обновился** — remote build мог не выполниться; см. PROJECT_HANDOFF.local.md §1.

**Версия релиза.** Каждый деплой получает версию X.Y.Z (или X.Y.Z-fixN) по
протоколу docs/release_management.md: ПЕРЕД деплоем согласовать номер с
Дмитрием (`scripts/add_release.py --suggest` печатает кандидатов), ПОСЛЕ —
внести скрытую запись релиза на проде (`scripts/add_release.py`). Публикация
и правки — в админке `/admin/releases`.

**Домашний сервер обязан догнать прод.** Код сайта исполняется не только в
контейнерах на проде: домашний сервер (`saturday-run`, 192.168.1.26) разбирает
очередь профилей `profile_fetch_pending` — тем же кодом и в ТУ ЖЕ базу. Значит
после каждого деплоя он должен встать на тот же коммит, иначе схема и код
разойдутся. 02.09.2026 это уже случилось и проявилось как
`column users.display_name_style does not exist` посреди разбора очереди.

Толкать обновление с деплоя нельзя: домашняя сеть за NAT, наружу открыты только
80 и 443, SSH намеренно не проброшен. Поэтому направление — изнутри:

- `remote_deploy.sh` после успешного health-check пишет на проде `.deployed_sha`;
- на домашнем сервере ДВЕ копии кода: `~/saturday-runs-next` (разбор очереди
  профилей) и `~/srs-prod` (docker-контейнеры воркеров сбора — 5 вёрст, S95,
  RunPark, см. `docker-compose.home.yml`). Обе обязаны совпадать с продом;
- на домашнем сервере таймер `pm-home-sync` запускает `scripts/home_sync.sh`:
  читает маркер, и если коммит сменился — останавливает разбор очереди,
  подтягивает код, при изменении `pyproject.toml` обновляет venv, запускает
  очередь обратно.

Ориентир — именно маркер, а не `git rev-parse HEAD` на проде: HEAD на диске и
код внутри контейнеров расходятся, если сделать `git pull` без деплоя.

Предохранитель: `app/db/schema_guard.py` сверяет головную миграцию кода с
`alembic_version` в базе и останавливает разбор очереди с внятным сообщением,
если версии разъехались.

**Фронт локально:**

```bash
make frontend-build   # или docker run node:22-alpine …
docker compose restart nginx
```

### Запросы к prod-БД (read-only reporting API)

Для агента без прямого доступа к prod (например, cloud-сессия без SSH/туннеля):
внутренний HTTPS-эндпоинт `/api/internal/reports/*` выполняет **только read-only**
`SELECT`/`WITH` запросы. Код: `backend/app/api/routes/reports.py`,
`backend/app/services/report_query.py`.

- **Токен** — в переменной окружения `REPORT_API_TOKEN` (bearer). В код/репозиторий
  **не коммитить**. Если переменной нет в окружении — попросить её у пользователя.
- **Доступ на чтение** — эндпоинт ходит под ролью `report_ro` (`GRANT SELECT`,
  `REPORT_DATABASE_URL`); плюс транзакция `READ ONLY` + `statement_timeout` + лимит
  строк. Запись/DDL невозможны. Скрипт роли: `scripts/create_report_ro_role.sql`.
- **Из cloud-сессии** нужен egress на `run5k.run` (Custom network access → Allowed
  domains).

```bash
# Схема БД (таблицы/колонки/типы) — прочитать перед составлением ad-hoc запроса:
curl -s -H "Authorization: Bearer $REPORT_API_TOKEN" \
     https://run5k.run/api/internal/reports/named/schema

# Ad-hoc SELECT:
curl -s -H "Authorization: Bearer $REPORT_API_TOKEN" -H "Content-Type: application/json" \
     -d '{"sql":"SELECT count(*) FROM users","limit":100}' \
     https://run5k.run/api/internal/reports/query

# Список именованных отчётов:
#   schema | foreign_keys | indexes | db_size | table_sizes | connections
curl -s -H "Authorization: Bearer $REPORT_API_TOKEN" \
     https://run5k.run/api/internal/reports/
```

Ответ: `{columns, rows, row_count, truncated, elapsed_ms}`. `truncated=true` — упёрлись
в лимит строк, повторить с бóльшим `limit` или сузить запрос.

---

## 4. Celery: очереди и beat

Конфиг: `backend/app/workers/celery_app.py`, timezone `Europe/Moscow`.

Имена очередей — `backend/app/workers/queues.py`; маршруты `task_routes` — в
`celery_app.py`. Новая задача по умолчанию едет в ФОН своей системы.

| Очередь | Worker | Задачи |
|---------|--------|--------|
| `five_verst_user` | worker-five-verst-user | `user_sync.*` — синк профиля по кнопке (приоритетная) |
| `five_verst_fresh` | worker-five-verst-fresh | `sync_latest_results` — сегодняшние протоколы |
| `five_verst` | worker-five-verst | остальные `five_verst_sync.*`: registry, rotation, reconcile, обход недели, клубы, сообщества |
| `s95_user` | worker-s95 | `run_user_sync`, `run_admin_resync` (приоритетная, уступка через LLEN) |
| `s95` | worker-s95 | остальные `s95_sync.*`: реестр, JSON-протоколы, отмены, описания |
| `parkrun` | worker-parkrun | `parkrun_sync.*` (очередь профилей), `og_render.*` |
| `runpark` | worker-runpark | `runpark_sync.*`: latest, кросслинки, user_sync |
| `warm` | worker-warm, worker | `leaderboards.warm_cache`, `locations.warm_cache` |
| `celery` (default) | worker | наблюдатель протоколов, прогрев главной, page_stats, погода, user_names, sync_runs.close_stale, дайджест |

У **каждой** записи beat есть `expires`: не взяли вовремя — задача умирает, а не
копится долгом после простоя воркера. Где срок не задан руками, он считается из
расписания (`schedule_interval_seconds` в `celery_app.py`, потолок 6 ч); тест
`test_expires_never_outlives_the_interval` не даст задать срок длиннее интервала.

### S95 user priority (cooperative yield)

Один worker, один поток fetch (Redis lock). Batch-задачи **не параллелят** запросы.

При появлении задачи в `s95_user` batch прерывается (`S95YieldForUserSync` в `app/s95/fetch/priority.py`), ставится обратно в очередь, user sync выполняется первым.

Код: `coordinator.py`, `rate_limit.py`, `s95_athletes_registry.py`, `workers/s95_batch_yield.py`.

### 5 вёрст user priority (пауза батча)

Два воркера: батчи и пользовательские синки. Пока идёт синк по кнопке (или его
задача ждёт в `five_verst_user`), батч **замирает между фетчами** и продолжает
с того же места — прогресс не теряется, в отличие от прерывания у S95. К
5verst.ru по-прежнему ходит один запрос за раз: общий Redis-лок
`five_verst:fetch:global_lock` и общий rate limit соблюдают оба воркера.

Отметка `five_verst:user_sync:active` живёт по TTL, а пауза имеет потолок
(`five_verst_user_sync_pause_max_seconds`) — умерший user-воркер или копящаяся
очередь не заморозят батч навсегда.

Код: `app/five_verst/fetch/priority.py`, `coordinator.py`, `workers/tasks/user_sync.py`.

### Beat schedule (MSK)

Источник истины — `beat_schedule` в `celery_app.py` (там же комментарии, почему
именно так). Сводка на 21.09.2026:

**5 вёрст**:

| Task | Расписание |
|------|------------|
| registry | 20:50 ежедневно |
| latest | пн–пт 0,5,10,15,20 (`:00`); сб/вс ежечасно |
| protocol_upload_watch (очередь `celery`) | сб — каждую минуту, вс — каждые 5 мин, пн–пт — каждые 30 мин |
| rotation | `:30` каждые 4 ч |
| reconcile | `:10` каждые 3 ч, **только пн–пт** |
| week sweep | 02:20 пн/ср/чт/пт (окна w0/w1/w2) |
| clubs registry / details | пн, чт 21:30 / `:45` каждые 3 ч по 8 клубов |
| community events | ср, сб 22:40 |

**S95** (JSON-API, Playwright-батчи сняты):

| Task | Расписание |
|------|------------|
| registry | 20:30 раз в 3 дня |
| api_new_protocols | сб, вс 11:00, 17:00, 23:00 |
| api_sync_updated | пн, ср, пт 03:00 |
| reconcile_events | вт 04:10 |
| cancellations watch | `:40` каждые 6 ч |
| location_descriptions | `:50` каждые 4 ч |

**RunPark**: latest 3,8,13,18,23 (`:00`); backfill crosslinks 03:30.
**parkrun**: очередь профилей `:07`/`:37`; og_render — выходные и понедельник.
**Прогревы**: главная — выходные ежечасно `:15`, будни 4,9,14,19; рейтинги `:20`
и локации `:40` каждые 2 ч. **Служебное**: page_stats `:35`, sync_runs.close_stale
`:05`, статусы локаций 21:10, дайджест 21:50, погода 03:05/03:20 (+ пятница 8,14,20).

---

## 5. S95: блокировка IP и cooldown

**Prod IP `195.58.34.112` может быть заблокирован s95.ru (HTTP 403).**

| Механизм | Где |
|----------|-----|
| Детект 403 / Forbidden | `app/s95/ban.py`, `fetch/browser.py`, `fetch/coordinator.py` |
| Cooldown 1 час | Redis `s95:fetch:ban_cooldown_until`, `s95_ban_cooldown_seconds=3600` |
| Пропуск batch при cooldown | `sync_task_reporting.py` |
| Текст ошибки пользователю | `app/s95/messages.py`, `sync_error_format.py` |

Диагностика:

```bash
scripts/check_failed_sync_jobs.py   # полные тексты ошибок sync_jobs
redis-cli GET s95:fetch:ban_cooldown_until
```

---

## 6. Синхронизация: три уровня

### 6.1 Bulk sync (Celery, автомат)

Latest, registry, reconcile, rotation — см. docs по платформам. Трекинг: таблица `sync_runs`.

### 6.2 User sync (по запросу пользователя)

`platform_links.last_user_sync_at` — кнопка «Обновить», OAuth-привязка, auto-sync.

Код: `app/sync/user_sync.py`, `s95_user_sync.py`, `parkrun_user_sync.py`.

Admin queue: `/admin/queue`, API `/api/admin/sync-queue`.

**Reconcile «задача не была обработана воркером»:** задача dequeued, но worker занят long batch; исправлено проверкой `task_is_worker_reserved` + S95 yield.

### 6.3 S95 athletes registry

Перепроверка профилей без штрихкода / stale `profile_checked_at`. До 50 за прогон, ~30 мин.

---

## 7. Personal records (PR)

| Тип | Логика | UI |
|-----|--------|-----|
| PR | Улучшение на платформе, метка протокола 5v «Личный рекорд!», первая пробежка | плашка **PR** |
| Global PR | Первое улучшение лучшего времени среди всех систем | **оранжевый жирный** финиш |

Код: `personal_record_service.py`, `dashboard_service.py`, `GlobalPrFinishTime.tsx`.

Backfill: `scripts/recalculate_personal_records.py --platform all`.

**Не вызывать** старый reset PR всей платформы по participant — исправлено.

---

## 8. Location catalog (русские названия parkrun)

`data/location_catalog.json` + таблицы `location_catalog`, `location_catalog_links`.

Для parkrun-локаций с аналогом в 5v/s95 показывается `canonical_name` (русское).

**Slug mismatch:** parkrun parser даёт `readovsky-park`, каталог — `readovskypark`. Lookup нормализует slug (`normalize_location_slug` в `location_catalog_service.py`).

Import: `make location-catalog-import-docker`.

---

## 9. Privacy, UI (июнь 2026)

| Фича | Файлы |
|------|-------|
| Приватный профиль | migration `027`, `PrivacySettingsSection.tsx`, `profile_private` |
| PR badge alignment | `ActivityDateCell.tsx`, grid в `index.css` |
| Admin queue error tooltip | `QueuePage.tsx` + `StatHintTooltip` |
| About: legacy site link сверху | `AboutPage.tsx` |
| Убран раздел «Справочник» (/insights) | routes в `App.tsx` |

### Вход по коду на почту

`services/email_auth_service.py` + `core/mailer.py`. Код шестизначный, живёт в
Redis **хэшем** (не открытым текстом), 10 минут, 5 попыток, одноразовый. Письма
шлёт celery-задача `email_send.deliver` через ящик `support@run5k.run` на
кластере Timeweb (`SMTP_*` в `.env`; порты 465/587/25 с прода открыты).

Один человек — один профиль: адреса сравниваются нормализованными
(`core/email_address.py` — плюс-алиасы, точки в Gmail, синонимы Яндекса; но
`mail.ru`/`inbox.ru`/`bk.ru` НЕ склеиваются, это разные ящики). Если ящик уже
подтверждён другим провайдером, вход добавляет способ входа к существующему
профилю, а не заводит второй — и то же в обратную сторону, в
`oauth_service._user_by_verified_email`. VK адрес не отдаёт, поэтому его
объединяют вручную в «Способах входа».

Проверка почты с прода: `docker compose exec api python scripts/send_test_email.py --to …`.

**Доставка и воронка писем.** Главная потеря на этом входе — не интерфейс, а
спам-папка: письмо от малознакомого отправителя почтовик прячет, человек его не
находит и уходит. Поэтому на шаге ввода кода висит подсказка про «Спам» с
адресом отправителя (он приходит в ответе `POST /auth/email/request-code`), а
каждое отправленное письмо пишется строкой в `email_login_requests`
(`services/email_login_journal_service.py`). Адреса там нет — только sha256
нормализованного ящика (`mailbox_hash`, по нему ищутся письма конкретного
человека по жалобе) и домен.

Отчёт — «Статистика» → «Вход по почте» (`GET /api/admin/email-login`). Читается
ПО ЯЩИКАМ, а не по письмам: запросивший три кода и вошедший — одна победа.
Ключевая колонка «Не открыли письмо» — ящики без единой попытки ввода кода,
верхняя оценка того, сколько писем осело в спаме; по доменам видно, у какого
почтовика хуже. `purpose` отделяет вход (`login`) от привязки почты в
настройках (`link`) — эндпоинт один, различает по наличию сессии.

Внешние панели: постмастеры mail.ru и Google подключены ссылками в шапке
админки. У Яндекса постмастера нет — свой он закрыл в 2020 году и замены не
дал, так что по яндексовым ящикам наша воронка по доменам — единственный
источник.

### Вход через Telegram: подтверждение в боте или виджет

Два пути под одной кнопкой «Войти через Telegram», выбирает сервер по
`GET /auth/telegram/config` → `bot_login`:

- **Бот жив** — `POST /auth/login-request` (контекст запроса: IP, User-Agent,
  согласие, время — в Redis `login_req_ctx:*`), deep link `t.me/<бот>?start=login_<token>`.
  Бот через `POST /auth/bot/login-context` показывает, откуда вход (браузер и
  ОС — `core/user_agent.py`, город по IP — `services/ip_geo_service.py`,
  ip-api.com с кэшем на сутки), кнопки «Подтвердить вход» / «Это не я»
  (`POST /auth/bot/deny`). После confirm статус `confirmed`, user_id лежит в
  `login_req_claim:<token>`; вкладка сайта опрашивает статус раз в 2 с и
  забирает сессию `POST /auth/login-request/{token}/claim`. Ссылка-страховка
  с magic link в боте остаётся (вкладку могли закрыть).
- **Бот молчит** — Telegram Login Widget (`telegram_login_service.py`), подпись
  проверяется локально, сервер в Telegram не ходит.

«Жив» = метка `bot:heartbeat` в Redis (`core/bot_heartbeat.py`): бот раз в 30 с
делает `get_me()` и зовёт `POST /internal/bot/heartbeat`, TTL 90 с. Упала
прокси или контейнер — метка гаснет, сайт ведёт в виджет; если бот умер
посреди входа, вкладка по `bot_alive=false` в статусе предлагает виджет сама.
Фронт: `features/auth/TelegramBotLogin.tsx` (страница входа и «Способы входа»).

### Лимит новых профилей

`core/signup_guard.py` — суточный потолок на **создание** аккаунта: 3 с одного
IP, 2 с одного устройства (`SIGNUP_LIMIT_PER_*`). Вход существующим профилем не
ограничен: проверка стоит только в ветке `create_oauth_user` внутри
`handle_oauth_callback`. Устройство — подписанная кука `sr_device`, её выдаёт
`/auth/oauth/{provider}/start` перед уходом к провайдеру.

Счётчики в Redis (`signup:ip:*`, `signup:device:*`), инкремент после успешного
создания — сорванные попытки лимит не съедают. Отказы видны в админке на
«Защита» (`/admin/abuse`, поле `signup_blocks`): один и тот же адрес подряд —
мультиаккаунт, разные — общий NAT и повод поднять порог.

Пороги выбраны по журналу входов: за всю историю максимум 2 разных аккаунта
приходили с одного IP. Вход через ТГ-бота (`bot_confirm_login`) лимитом не
накрыт — там запрос идёт от бота, IP чужой; у него свой лимит на telegram_id.

---

## 10. Admin-бот (Telegram, fallback ВК)

Env: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID`, `ADMIN_TELEGRAM_ID`, `TELEGRAM_PROXY_URL`.

**Прокси обязательна на проде.** С сервера в РФ `api.telegram.org` недоступен напрямую
совсем: без прокси aiogram падает на `get_me()` и контейнер `bot` уходит в крэш-луп
(так и было 26.07.2026 — 545 рестартов). Через прокси идёт ВЕСЬ трафик: long-poll бота
(`AiohttpSession(proxy=...)`) и admin-уведомления (httpx). Схема именно `http://` —
её понимают нативно и httpx, и aiohttp; для `socks5://` aiohttp требует `aiohttp_socks`.
Прокси даёт сервис `tg-proxy` (xray-core, VLESS+Reality, два профиля под балансировщиком),
см. `deploy/tg-proxy/`. Реальный `config.json` gitignored, в репо — `config.example.json`.

Fallback на ВК (`VK_BOT_GROUP_TOKEN`, `VK_ADMIN_USER_ID`) — только когда прокси легла;
пока всё работает, в ВК ничего не приходит.

Scheduled sync → лог в `scheduled_run_logs` через `run_reported_sync()` + dedup
`scheduled_sync_guard.py`; суточная сводка (`admin_digest.daily_sync_summary`) уходит
в Telegram.

Все уведомления админу идут через `services/admin_notify.py` — прямых вызовов
`send_vk_admin_message()` в фичах не осталось (03.08.2026), ВК живёт только внутри
фолбэка `send_admin_report()`:

- `notify_admin(text) -> bool` — в один конец: карточки/голоса/комментарии бэклога,
  алерты синков (дубль локации 5 вёрст, смена slug клуба). Возвращает признак
  доставки: алерты помечают заявку отправленной только после успеха.
- `notify_admin_dialog(text, reply_to_message_id=None) -> (chat_id, message_id)` —
  для диалогов Reply (заявки на координаты новых локаций, `location_coordinate_service`).
  Только Telegram, без фолбэка: ответы разбирает бот через
  `/internal/bot/coordinate-message`, а слушателя ВК нет с перевода бота на Telegram —
  до 03.08.2026 запрос уходил в ВК, и ответить на него было некому.

На прогоне тестов admin-уведомления не уходят никуда: `core/runtime_env.is_test_run()`
глушит их в `notify_admin()`, `notify_admin_dialog()` и в самом `send_vk_admin_message()`.
Локальный pytest работает с боевым `.env`, и до 03.08.2026 каждый прогон тестов бэклога
прилетал админу в ВК живыми сообщениями («Новая карточка бэклога: [фича] «Идея»»,
«Комментарий … Первый»).

Команды (bot_app, admin-only): `/stats`, `/status`, `/sweep`, `/sync registry|latest|…`,
`/sync s95-latest|…`.

---

## 10a. Уведомления сайта (Telegram, VK, почта)

Основа: `app/notification_kinds.py` (реестр видов — в коде, без миграции),
`app/notification_markup.py` (разметка `**жирный**` и `[подпись](url)` → HTML
Telegram / HTML письма / текст VK), `services/notification_service.py`
(настройки, очередь, доставка, отписка, копия админу),
`services/notification_channels_service.py` (каналы и проверка
доставляемости), `services/notification_senders.py` (отправители),
`services/activity_notification_service.py` (единое сообщение о пробежке),
`services/admin_notifications_service.py` (админка), `workers/tasks/notifications.py`.
Миграция `099_notifications`: `user_notification_prefs`, `user_notification_channels`,
`notification_deliveries`, `backlog_card_subscriptions`.

Правила:

- **Канал = способ входа.** Тумблер «Уведомления» стоит в карточке каждой
  привязки (Telegram, VK, почта; адрес от Яндекса — в его карточке). Нет ни
  одного включённого канала — сайт молчит: **умолчание выключено**. Первое
  включение взводит водяные знаки на «сейчас»: о прошлом не рассказываем.
  Виды («о чём присылать») — в модалке, по умолчанию все включены, кроме
  «Новые карточки в бэклоге».
- **Проверка доставляемости без сообщения человеку:** Telegram —
  `sendChatAction` (403/400 = бот заблокирован или не запущен), VK —
  `messages.isMessagesFromGroupAllowed`; результат на строке канала
  (`check_ok`), перепроверка не чаще 10 минут. Не доходит — в настройках
  красная подсказка «откройте бота и нажмите Start».
- **Призывы включить:** модалка в кабинете (`NotificationsPromptModal`, один
  раз за вход через sessionStorage; «Больше не напоминать» — `nudge_dismissed_at`)
  и модалка после создания карточки бэклога; кнопка включает лучший доступный
  канал (`POST /settings/notifications/enable`). Та же модалка во втором
  режиме (`kind=fix_delivery` в `GET /settings/notifications/nudge`) поднимает
  тревогу, когда уведомления включены, но НИ ОДИН включённый канал не
  доставляет: ссылка ведёт в бота или диалог сообщества. Пока хоть один канал
  живой — молчим.
  Колокольчик на карточке включает уведомления только тому, кто настроек
  не трогал (`settings_touched_at`).
- **Событие → `notify_user(db, user, kind, title=…, text=…, dedupe_key=…, url=…)`.**
  Кладёт строку `notification_deliveries` (queued) и ставит
  `notifications.deliver`. Повтор по `dedupe_key` отбрасывается. Из
  транзакции события — `commit=False` и `enqueue_delivery()` после commit.
- **Резерв.** Основной канал из настроек, потом остальные (`CHANNEL_ORDER`:
  telegram → vk → email). Постоянная ошибка — канал пропускается; временная —
  строка failed, `notifications.retry_queued` (beat, каждые 10 мин) повторит
  до 3 раз в течение суток.
- **Единое сообщение о пробежке** (`kind=runs`): новые `run_results` после
  водяного знака и не старше `NOTIFICATIONS_RUNS_WINDOW_DAYS` + уровни
  челленджей (`challenge_levels`) + новые вехи «Моей истории»
  (`milestones_seen`) + ссылка на постер (`/share`). Первый снимок каждой
  части молчит. Сканер зовётся из `dashboard_warm.after_sync` (с задержкой
  20 мин), из user sync и раз в 10 минут по beat
  (`notifications.scan_new_results` — ловит результаты, записанные мимо
  воркеров, в т.ч. parkrun с Mac-демона).
- **Рейтинги отдельно** (`kind=ratings`): раз в неделю, воскресенье 14:00 МСК
  (`notifications.weekly_ratings`) — место в рейтингах runs/locations/wins
  против `ratings_snapshot` недельной давности, вверх и вниз. Сразу после
  пробежки не пишем: протоколы субботы догружаются до воскресенья, и место
  за день меняется. Снимок сеется при включении уведомлений.
- **Отмены стартов** (`kind=cancellations`,
  `services/cancellation_notification_service.py`): рассылаются ВСЕМ
  подписчикам по всей стране, а не только по «своим» площадкам — человек
  может собираться в другой город (решение Дмитрия 23.09.2026). Единственный
  фильтр — системы: `user_notification_prefs.cancellation_platforms` (пустой
  список = все, миграция 101; в интерфейсе только 5 вёрст и S95, у
  parkrun/RunPark недельных отмен нет). Весь набор изменений одного синка —
  ОДНО сообщение (в зимнюю субботу отмен бывает несколько), список у каждого
  свой по его системам. Зовётся из общего входа
  `location_cancellation_notify.notify_cancellation_changes(changes, db)`, то
  есть из всех трёх источников: реестры 5 вёрст и S95 и наблюдатель S95.
  Ключ повтора — хэш набора + номер недели: реестр и наблюдатель видят одну
  отмену по очереди.
- **Бэклог** (`kind=backlog`): автору — «карточка принята», следящим —
  комментарии и смена статуса; автор и комментаторы следят автоматически,
  остальные — колокольчиком (`PUT /backlog/cards/{id}/subscription`).
  `kind=backlog_new_cards` — каждая новая карточка тем, кто включил.
- **Подвал сообщения.** В Telegram и VK — «⚙️ Настроить уведомления»
  со ссылкой на `/settings#notifications`: чаще нужно донастроить, а не
  отрезать всё (решение Дмитрия 24.09.2026). Мгновенная отписка
  `/api/notifications/unsubscribe?token=…` (HMAC на `app_secret_key`, внутри
  user_id и вид или `all`) остаётся в ПИСЬМАХ — её требуют почтовые
  провайдеры, там же `List-Unsubscribe`. Первый клик выключает вид,
  «выключить всё» гасит все каналы.
- **Ссылку-действие не дублировать в теле:** она уходит кнопкой уведомления
  (`url`/`url_label` в `notify_user`), и повтор в тексте читается как два
  одинаковых линка подряд.
- **Копия админу** каждого доставленного сообщения («📨 Сообщение направлено
  @ник · канал» + тот же HTML) — пока `NOTIFICATIONS_ADMIN_COPY=true`.
- Админка: `/admin/notifications` — подписчики по каналам, сводка по
  статусам/видам/каналам, лента доставок с ошибками.
- Под pytest отправители и проверки молчат (`is_test_run()`), тесты подменяют
  `SENDERS`, `CHECKERS` и `enqueue_delivery` — см. `tests/test_notification_service.py`.

---

## 11. Fetch locks

| Платформа | Lock | Интервал |
|-----------|------|----------|
| 5 verst | Redis | 20–30 с |
| S95 | Playwright + Redis | 15–30 с, concurrency=1 cluster-wide |
| parkrun | Mac Chromium | см. docs/parkrun_pipeline.md |

---

## 12. Dashboard / API

- Cache: `dashboard_cache`, `ANALYTICS_VERSION` — инкрементировать при смене полей аналитики
- Admin: `/admin/stats`, `/admin/queue`, `/admin/users`, `/admin/page-analytics`
- Settings: `/api/settings/privacy`

### Аналитика страниц (посещаемость)

`page_view_events` (сырые, 90 дней) → beat `page_stats.rollup` (ежечасно) →
`page_stats_daily` (вечно) → `/admin/page-analytics`.

**Новая страница на сайте = новая строка в аналитике.** При добавлении роута в
`STATIC_ROUTES` (`App.tsx`) обязательно дописать:

1. `_STATIC_PAGE_TYPES` — `page_analytics_service.py`
2. `PAGE_TYPE_LABELS` — `AdminPageAnalyticsPage.tsx`

Третьего пункта больше нет: список роутов для теста **читается прямо из
`App.tsx`** (`_static_routes_from_app` в `tests/test_page_analytics_service.py`),
поэтому забыть его обновить нельзя. Раньше там лежала копия списка, и её
забывали ровно так же, как классификатор: тест зеленел, а раздел молча уезжал
в «Прочее» — так пропали `/backlog` и победные рейтинги.

Проверить: `pytest tests/test_page_analytics_service.py` — упадёт с текстом
«Раздел X не попадает в статистику». Тесту нужен доступ к `frontend/src`, в
контейнере он примонтирован в `/frontend-src` (см. `docker-compose.yml`).

Динамические адреса (`/users/{хендл}`, `/locations/{slug}`, `/hq/{токен}`)
разбираются регулярками в `classify_page` — их сторож не покрывает, добавлять
руками. Вкладки профиля (`/users/{хендл}/{вкладка}`) считаются обычным
просмотром профиля: в `entity_key` едет хендл, а не вкладка.

---

## 13. Локальная разработка

```bash
cp .env.example .env   # + PROJECT_HANDOFF.local.md для prod credentials
docker compose up --build
docker compose exec api alembic upgrade head
make frontend-build && docker compose restart nginx
```

| URL | Назначение |
|-----|------------|
| http://localhost:8080 | сайт |
| http://localhost:8080/about | О проекте |
| http://localhost/login | VK нужен порт 80 (`docker-compose.dev.yml`) |
| localhost:5433 | Postgres (DBeaver) |

Тесты:

```bash
docker compose run --rm api pytest
cd backend && ruff check app tests
```

---

## 14. Типичные задачи агента

| Задача | Действие |
|--------|----------|
| S95 все sync failed | Проверить 403 с prod IP, cooldown Redis, писать админам S95 |
| Очередь admin — длинные ошибки | tooltip в QueuePage; полный текст в `error_message` |
| Parkrun English names | catalog link + norm slug; import catalog |
| PR не показывается | `is_pr`, five_verst protocol label, backfill PR |
| Новая страница на сайте | Роут в `App.tsx` + раздел в аналитике: `_STATIC_PAGE_TYPES`, `PAGE_TYPE_LABELS`, `APP_ROUTES` (см. §12) |
| Deploy | `deploy_prod.sh`; verify build + health |
| API 502 | `docker compose logs api` — часто SyntaxError после деплоя |
| Prod DB query | SSH + `docker compose exec api python -c "…"` |

---

## 15. Git и prod

- Коммиты — **только по просьбе** пользователя
- Prod `.env` не в git; локальный `.env` и `PROJECT_HANDOFF.local.md` — gitignored
- На сервере руками `git pull` не делать: прод переводится на вершину `origin/main`
  только деплоем (`scripts/deploy_prod.sh` → `remote_deploy.sh`), иначе код на
  диске и в контейнерах разъедутся

---

## 16. Parkrun (отдельно)

Prod API **не** fetch'ит parkrun.org.uk. Очередь `profile_fetch_pending` → **`make parkrun`** на Mac.

Подробнее: [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md).

---

## 17. Полезные скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/check_failed_sync_jobs.py` | failed sync_jobs за 7 дней |
| `scripts/recalculate_personal_records.py` | backfill PR |
| `scripts/import_location_catalog.py` | catalog → DB |
| `scripts/backfill_location_descriptions.py` | первый сбор описаний площадок (5 вёрст, S95) |
| `scripts/deploy_prod.sh` | git-based деплой на прод (одним SSH, см. `remote_deploy.sh`) |
| `scripts/dev_prod_db.sh` | локальный сайт на prod DB (read-only tunnel) |
| `scripts/add_release.py` / `update_release.py` | записи релизов для «Обновлений» |
| `scripts/prune_server_disk.sh` | чистка диска на сервере (образы без `-a`, кэш сборки, journald) |
| `make parkrun` | parkrun fetch daemon (Mac; на домашнем сервере — `scripts/home_queue_run.sh`) |
| `backend/scripts/archive/` | отработавшие бэкфилы — не запускать, см. README там |

---

## 18. Контакт

Автор: **Дмитрий ПОПОВ** (@Popov_Dmitry, popov.dmitii@yandex.ru).  
При блокерах в Claude Code — пользователь может вернуться в Cursor с вопросами.
