# Saturday Runs — справочник для агентов

Документ для AI-агентов и разработчиков: архитектура, prod, синхронизация, типичные задачи.  
Обновлено: **июнь 2026**.

**Секреты и пароли:** локальный файл [`PROJECT_HANDOFF.local.md`](PROJECT_HANDOFF.local.md) (в `.gitignore`, не коммитить). Там SSH, prod PG, OAuth, нюансы деплоя.

Связанные документы:

| Документ | Содержание |
|----------|------------|
| [README.md](README.md) | Quick start, auth flow |
| [PROJECT_HANDOFF.local.md](PROJECT_HANDOFF.local.md) | **Credentials, SSH, prod нюансы** (gitignored) |
| [docs/five_verst_sync_plan.md](docs/five_verst_sync_plan.md) | Конвейеры 5 вёрst |
| [docs/s95_sync_plan.md](docs/s95_sync_plan.md) | Конвейеры S95 |
| [docs/deploy_and_migration_plan.md](docs/deploy_and_migration_plan.md) | Legacy ETL |
| [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md) | Parkrun (Mac + Chromium) |
| [docs/legacy_etl_mapping.md](docs/legacy_etl_mapping.md) | Маппинг legacy → новая схема |
| [deploy/DOMAINS.md](deploy/DOMAINS.md) | run5k.run / grafana.run5k.run |
| [docs/runpark/README.md](docs/runpark/README.md) | **RunPark** — локации, маппинг, контракт view |

---

## 1. Назначение проекта

**Saturday Runs** (`run5k.run`) — личный кабинет участника субботних парковых пробежек + global data core.

Платформы:

- **5 вёрst** (`five_verst`) — 5verst.ru
- **S95** (`s95`) — s95.ru / s95.by
- **parkrun** — parkrun.org.uk (fetch с Mac, не из prod API)

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
  scripts/                # CLI, backfill, check_failed_sync_jobs.py
  tests/
  alembic/versions/       # миграции (027 — profile_private)
frontend/src/
  features/               # runs, admin, settings, queue, about, …
  components/             # ActivityDateCell, PlatformBadge, …
data/
  location_catalog.json   # cross-platform location mapping (105 parkrun RU)
docs/
docker-compose.yml        # Dev
docker-compose.prod.yml   # Prod overlay (host PG)
docker-compose.dev.yml    # VK OAuth на :80
scripts/deploy_prod.sh
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
| Legacy Grafana | **https://grafana.run5k.run** |
| Старый URL ЛK | `app.run5k.run` → 301 на run5k.run |
| SSH user | `viewer` (пароль в PROJECT_HANDOFF.local.md) |
| Compose | `docker compose -f docker-compose.yml -f docker-compose.prod.yml` |
| БД | Host PostgreSQL (`DATABASE_URL` в prod `.env`) |
| Web | Host nginx (TLS) → Docker nginx :8080 → API + static |

### Контейнеры (prod)

| Сервис | Назначение |
|--------|------------|
| `api` | FastAPI, 2 uvicorn workers |
| `nginx` | React `frontend/dist` + proxy `/api` |
| `redis` | Sessions, Celery, locks, cooldown — **не публиковать :6379** |
| `beat` | Celery Beat (Europe/Moscow) |
| `worker-five-verst` | `-Q five_verst --concurrency=1` (батчи) |
| `worker-five-verst-user` | `-Q five_verst_user --concurrency=1` (синки по кнопке) |
| `worker-s95` | `-Q s95_user,s95 --concurrency=1` |
| `worker-parkrun` | `-Q parkrun` |
| `bot` | Telegram long poll (вход + admin: /stats /status /sweep /sync) |
| `worker` | default очередь: прогрев главной, агрегаты популярности, admin-дайджест |

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

| Очередь | Worker | Задачи |
|---------|--------|--------|
| `five_verst_user` | worker-five-verst-user | user profile sync (приоритетная) |
| `five_verst` | worker-five-verst | registry, latest, rotation, reconcile |
| `s95_user` | worker-s95 | user profile sync (приоритетная) |
| `s95` | worker-s95 | batch S95 + athletes_registry |
| `parkrun` | worker-parkrun | parkrun user sync |

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

**5 verst**:

| Task | Расписание |
|------|------------|
| registry | 20:50 daily |
| latest | пн–пт 0,5,10,15,20 (`:00`); сб/вс hourly |
| rotation | `:30` каждые 4 ч |
| reconcile | `:10` каждые 3 ч, **только пн–пт**; 200 протоколов цепочкой 2×100 |

**S95** — **+30 мин** к 5verst:

| Task | Расписание |
|------|------------|
| registry | 20:30 |
| latest | :30 (аналогично дням) |
| rotation | :30 каждые 4 ч |
| reconcile | :30 каждые 3 ч |
| athletes_registry | :30 каждые 2 ч, batch 50 |
| location_descriptions | :50 каждые 4 ч, batch 5 |

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
- Перед `git pull` на сервере — сверка с rsync-состоянием

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
| `scripts/deploy_prod.sh` | rsync + prod deploy |
| `scripts/dev_prod_db.sh` | локальный сайт на prod DB (read-only tunnel) |
| `make parkrun` | Mac parkrun fetch daemon |

---

## 18. Контакт

Автор: **Дмитрий ПОПОВ** (@Popov_Dmitry, popov.dmitii@yandex.ru).  
При блокерах в Claude Code — пользователь может вернуться в Cursor с вопросами.
