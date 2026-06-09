# Saturday Runs — справочник для агентов

Документ для AI-агентов и разработчиков: архитектура, prod, синхронизация, типичные задачи.  
Обновлено: **июнь 2026**.

Связанные документы:

| Документ | Содержание |
|----------|------------|
| [README.md](README.md) | Quick start, auth flow |
| [docs/five_verst_sync_plan.md](docs/five_verst_sync_plan.md) | Конвейеры 5 вёрst (детали парсеров) |
| [docs/s95_sync_plan.md](docs/s95_sync_plan.md) | Конвейеры S95 |
| [docs/deploy_and_migration_plan.md](docs/deploy_and_migration_plan.md) | Legacy ETL, миграция данных |
| [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md) | Parkrun (Mac + Chromium) |
| [docs/legacy_etl_mapping.md](docs/legacy_etl_mapping.md) | Маппинг legacy → новая схема |

---

## 1. Назначение проекта

**Saturday Runs** — личный кабинет участника субботних парковых пробежек + global data core.

Платформы:

- **5 вёрst** (`five_verst`) — 5verst.ru
- **S95** (`s95`) — s95.ru / s95.by
- **parkrun** — parkrun.org.uk (fetch с Mac, не из prod API)

Стек: Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery, Redis, PostgreSQL 16, React 19, Nginx, Docker Compose.

---

## 2. Структура репозитория

```
backend/
  app/                    # FastAPI, модели, sync, parsers, services
  app/workers/            # Celery app + tasks (five_verst_sync, s95_sync, …)
  app/workers/tasks/      # sync_task_reporting.py — VK-репорты + dedup
  vk_bot/                 # VK admin bot (long poll, /sync, /stats)
  bot_app/                # Telegram auth bot (legacy auth-only)
  scripts/                # CLI sync-скрипты (dry-run, prod-run)
  tests/
frontend/src/             # React dashboard
docs/                     # Планы и маппинги
docker-compose.yml        # Dev
docker-compose.prod.yml   # Prod overlay (host PG, no exposed ports)
scripts/run_prod_script.sh
Makefile                  # backend-test, prod-run, sync menu
```

---

## 3. Production

| Параметр | Значение |
|----------|----------|
| Хост | `195.58.34.112` |
| Путь | `/opt/saturday-runs-next` |
| **Основной сайт** | **https://run5k.run** |
| Legacy Grafana | **https://grafana.run5k.run** (Grafana :9000) |
| Старый URL ЛК | `app.run5k.run` → 301 на run5k.run |
| SSH user | `viewer` |
| Compose | `docker compose -f docker-compose.yml -f docker-compose.prod.yml` |
| БД | Host PostgreSQL (`DATABASE_URL` в `.env`), не контейнер postgres |
| Web | Nginx → React dist + API |

**Контейнеры (prod):**

| Сервис | Назначение |
|--------|------------|
| `api` | FastAPI, 2 workers |
| `nginx` | Статика + reverse proxy |
| `redis` | Sessions, Celery broker, locks, dedup slots — **не публиковать :6379 наружу** (см. `deploy/redis/redis.conf`) |

### PR (`is_pr`) по платформам

| Платформа | Источник PR | После синка |
|-----------|-------------|-------------|
| parkrun | пересчёт по `finish_time_sec` | `recalculate_parkrun_personal_records(participant_id=…)` |
| s95, five_verst | пересчёт по времени (протокол может затереть флаг) | автоматически в `upsert_run_results` / `import_profile_run_results` |

Скрипты: `scripts/recalculate_parkrun_prs.py`, `scripts/recalculate_s95_prs.py`, `scripts/recalculate_personal_records.py --platform all`.

**Не вызывать** `recalculate_personal_records(..., participant_id=X)` со старым кодом, который сбрасывал PR всей платформы — исправлено: reset только по participant.

| `beat` | Celery Beat (MSK timezone) |
| `worker-five-verst` | Очередь `five_verst`, concurrency=1 (+ user_sync 5verst) |
| `worker-s95` | Очередь `s95`, concurrency=1 |
| `worker-parkrun` | Очередь `parkrun` |
| ~~`worker`~~ | **Отключён на prod** — очередь `celery` не используется |
| `vk-bot` | VK admin long poll |

**Деплой доменов и стека:**

```bash
bash scripts/deploy_run5k_domains.sh   # на сервере в /opt/saturday-runs-next
```

Конфиги host nginx: `deploy/nginx/run5k.run.conf`, `grafana.run5k.run.conf`, `app.run5k.run.conf`.  
Grafana `root_url`: `deploy/grafana/systemd-override.conf`.

**Деплой (типичный, точечный):** rsync изменённых файлов + rebuild нужных контейнеров.  
Фронт на prod часто собирают на сервере: `docker run node:22-alpine npm ci && npm run build`.

```bash
# Пример: backend + workers
sshpass -e rsync -avz -e "ssh …" backend/app/ viewer@195.58.34.112:/opt/saturday-runs-next/backend/app/
ssh viewer@195.58.34.112 'cd /opt/saturday-runs-next && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build worker-five-verst worker-s95 beat vk-bot'
```

**Логи:**

```bash
docker compose … logs worker-five-verst --since 1h | grep -E "received|succeeded|VK admin|Skip duplicate"
docker compose … logs worker-s95 --since 1h
docker compose … logs beat --since 1h | grep "Scheduler: Sending"
```

**Важно:** часть изменений может быть на prod через rsync, но **не закоммичена** в git — перед `git pull` на сервере сверять состояние.

---

## 4. Celery: очереди и beat

Конфиг: `backend/app/workers/celery_app.py`, timezone `Europe/Moscow`.

| Очередь | Worker | Задачи |
|---------|--------|--------|
| `five_verst` | worker-five-verst | registry, latest, rotation, reconcile, location |
| `s95` | worker-s95 | то же для S95 + athletes_registry |
| `parkrun` | worker-parkrun | parkrun fetch |
| default | worker | user_sync, прочее |

### Beat schedule (MSK)

**5 verst** — минуты `:00`:

| Beat key | Task | Расписание |
|----------|------|------------|
| `five-verst-registry-daily` | `sync_locations_registry` | 20:00 ежедневно |
| `five-verst-latest-weekday-morning` | `sync_latest_results` | пн–пт 05:00 |
| `five-verst-latest-saturday-hourly` | `sync_latest_results` | сб 01:00–23:00 |
| `five-verst-latest-sunday-hourly` | `sync_latest_results` | вс 00:00–23:00 |
| `five-verst-location-rotation` | `sync_location_rotation` | каждые 4 ч |
| `five-verst-reconcile-protocols` | `reconcile_stale_protocols` | каждые 3 ч |

**S95** — те же слоты, **+30 мин** (чтобы не биться с 5verst):

| Beat key | Task | Расписание |
|----------|------|------------|
| `s95-registry-daily` | `sync_locations_registry` | 20:30 |
| `s95-latest-*` | `sync_latest` | :30 (аналогично 5v по дням) |
| `s95-location-rotation` | `sync_location_rotation` | каждые 4 ч, :30 |
| `s95-reconcile-protocols` | `reconcile_stale_protocols` | каждые 3 ч, :30 |
| `s95-athletes-registry` | `sync_athletes_registry` | каждые 2 ч, :30, batch 50 |

---

## 5. VK admin: уведомления и управление

**Код:** `backend/app/services/vk_admin_notify.py`, `backend/app/workers/tasks/sync_task_reporting.py`, `backend/vk_bot/`.

Env (prod `.env`):

- `VK_BOT_GROUP_TOKEN`, `VK_BOT_GROUP_ID`, `VK_ADMIN_USER_ID`, `VK_BOT_INTERNAL_SECRET`

Поведение:

- Scheduled sync-задачи оборачиваются в `run_reported_sync()` → **▶️ старт** и **✅/⚠️ финиш** в VK.
- Работает для **5verst и S95** (registry, latest, rotation, reconcile, athletes).
- Мелкие задачи (`fetch_protocol_from_profile`, user sync) — **без** VK-репортов.

**VK-бот команды** (`vk_bot/main.py`, `vk_bot/pipeline.py`):

```
/stats [дней]
/status
/sync registry | latest | rotation | reconcile | location <slug>
/sync s95-latest | s95-rotation | s95-reconcile | s95-athletes | s95-registry
/sync protocol <url>
```

Ручной `/sync` ставит задачу с `force=True` (обходит dedup, см. ниже).

---

## 6. Dedup scheduled sync (защита от дублей)

**Проблема:** один worker на очередь + длинные задачи (athletes_registry ~1–2 ч) → latest копится в очереди; Celery redelivery → один task id может выполниться дважды → лишние HTTP-запросы.

**Решение:** `backend/app/services/scheduled_sync_guard.py`

- Redis-ключ `sync-hour-slot:{pipeline_key}:{YYYY-MM-DDTHH}` (MSK, TTL 3 ч)
- Не больше **одного успешного** scheduled-прогона на pipeline за час
- Повтор → `{"skipped": true, "reason": "duplicate_hour_slot"}` без запросов к сайту
- `force=True` — для ручного `/sync` из VK

Pipeline keys: `five_verst:latest`, `five_verst:reconcile`, `five_verst:rotation`, `five_verst:registry`, `s95:latest`, `s95:reconcile`, `s95:rotation`, `s95:registry`, `s95:athletes`.

**Beat шлёт ровно одну задачу в час** — дубли в worker-логах почти всегда backlog/redelivery, не двойной cron.

---

## 7. Синхронизация: три уровня данных

### 7.1. Bulk sync (протоколы, summaries) — автоматический Celery

| Что | 5 verst | S95 |
|-----|---------|-----|
| Latest | `/results/latest/` | `/activities` |
| Registry | `/events/` | `/events` |
| Протокол | `/{slug}/results/{date}/` | `/activities/{id}` |
| Reconcile | `protocol_sync_states.last_protocol_check_at` | то же |
| Участники из протокола | `upsert_participant()` inline | то же |

Трекинг прогона: таблица `sync_runs` (status, sync_type, started_at).

### 7.2. User sync — только по запросу пользователя ЛК

**Не** массовый обход профилей.

| Поле | Таблица | Когда обновляется |
|------|---------|-------------------|
| `last_user_sync_at` | `platform_links` | Пользователь нажал «Обновить», auto-sync при логине, OAuth-привязка |

Код: `app/sync/user_sync.py`, `app/sync/s95_user_sync.py`, `app/sync/parkrun_user_sync.py`.  
5verst user sync качает `/user/{id}/` (userstats). **Массового crawl юзеров 5verst нет.**

### 7.3. S95 athletes registry — периодическая перепроверка профилей

Код: `app/sync/s95_athletes_registry.py`, `app/sync/s95_participant_sync.py`.

- В `participants.profile_extra` пишется **`profile_checked_at`** при каждой проверке (в т.ч. 404, «Регистрация», нет штрихкода)
- Очередь: numeric athlete id без штрихкода или с `profile_checked_at` старше **`s95_athlete_profile_recheck_interval_days`** (default 30)
- Beat: каждые 2 ч, до **50** профилей (`s95_athletes_registry_batch_limit`)

### 7.4. Profile → protocol queue

Если при парсинге профиля участника нет полного протокола в БД — ставится задача `fetch_protocol_from_profile` (5v / s95).

Код: `app/sync/profile_protocol_queue.py`.

---

## 8. Fetch locks и rate limit

| Платформа | Lock | Интервал между запросами |
|-----------|------|--------------------------|
| 5 verst | `app/five_verst/fetch/` Redis | 20–30 с |
| S95 | `app/s95/fetch/lock.py` Playwright + Redis | 15–30 с |

Один активный fetch на платформу cluster-wide. S95 — headless Chromium.

---

## 9. Dashboard и API

- Analytics cache: `dashboard_cache`, версия в `ANALYTICS_VERSION` (`dashboard_service.py`)
- При изменении полей аналитики — **инкрементировать версию** (иначе stale cache на клиентах)
- Поля: `last_pr_date`, `last_global_pr_date`, и др. — см. `app/schemas/dashboard.py`

Admin sync queue: `/api/admin/sync-queue` (нужен admin telegram/vk id).

---

## 10. Ключевые env-переменные

См. `.env.example`. Важные для sync:

```bash
# 5 verst
FIVE_VERST_SYNC_PROTOCOL_LIMIT=
FIVE_VERST_SYNC_LATEST_UPDATE_LIMIT=
FIVE_VERST_RECONCILE_BATCH_LIMIT=100

# S95
S95_SYNC_PROTOCOL_LIMIT=3
S95_SYNC_LATEST_UPDATE_LIMIT=20
S95_ATHLETES_REGISTRY_BATCH_LIMIT=50
S95_ATHLETE_PROFILE_RECHECK_INTERVAL_DAYS=30
S95_RECONCILE_BATCH_LIMIT=10

# VK admin
VK_BOT_GROUP_TOKEN=
VK_ADMIN_USER_ID=
```

Prod DB: `PROD_DB_TARGET=1` включает `lock_timeout=30s` в SQLAlchemy session.

---

## 11. Тесты и локальная разработка

```bash
cd backend && pytest                          # или: docker compose run --rm api pytest
cd backend && ruff check app tests
make backend-test
```

Релевантные тесты для sync/VK:

- `tests/test_scheduled_sync_guard.py`
- `tests/test_vk_admin_notify.py`
- `tests/test_celery_beat_schedule.py`
- `tests/test_s95_athletes_registry.py`
- `tests/test_profile_protocol_queue.py`

CLI dry-run:

```bash
make prod-run ARGS="scripts/five_verst_sync_latest.py --dry-run -v"
make prod-run ARGS="scripts/s95_sync_latest.py --dry-run -v"
```

---

## 12. Типичные задачи агента

| Задача | Действие |
|--------|----------|
| Проверить, отработал ли latest | `logs worker-* --since 1h`, beat logs, `sync_runs` в БД |
| Дубли latest | Искать `Skip duplicate` или один task id × N succeeded; проверить очередь athletes |
| Нет VK по S95 | Проверить env, логи `VK admin message sent` в worker-s95 |
| Добавить VK для новой задачи | Обернуть в `run_reported_sync`, добавить label в `sync_report_labels.py` |
| Deploy fix | rsync → rebuild worker/beat/vk-bot; frontend — build на сервере |
| S95 phantom athletes | 404 / «Регистрация» — `classify_athlete_page()`, не ломать display_name |

---

## 13. Git и prod drift

- Origin/main может отставать от prod (деploy через rsync без commit).
- **Не делать `git pull` на prod** без сверки — можно затереть rsync-изменения.
- Коммиты создавать **только по явной просьбе** пользователя.

---

## 14. Parkrun (отдельно)

Prod API **не** ходит на parkrun.org.uk. Очередь `profile_fetch_pending` → fetch на Mac (`make parkrun`).  
Подробнее: [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md).
