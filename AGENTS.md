# Saturday Runs — справочник для агентов

Документ для AI-агентов и разработчиков: архитектура, prod, синхронизация, типичные задачи.  
Обновлено: **21 октября 2026** (прод переехал на домашний сервер).

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
- **S95** (`s95`) — s95.ru / s95.by / s95.rs / s95.kz (одна база, страна — по домену)
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
  app/s95/fetch/          # httpx (HTML + JSON API), Redis lock, пауза, лестница охлаждения, priority yield
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
docker-compose.prod.yml   # Оверрайд VPS (host PG) — до 21.10.2026, теперь только для отката
docker-compose.home.yml   # Воркеры сбора дома при сайте на VPS (до 21.10.2026 и после отката)
docker-compose.home-site.yml # БОЕВОЙ стек на домашнем сервере (srs_home) с 21.10.2026
docker-compose.dev.yml    # VK OAuth на :80
scripts/deploy_home.sh    # деплой прода (дома); deploy_prod.sh — VPS, только для отката
scripts/dev_stack.sh      # стенд под worktree на домашнем сервере (§13)
scripts/archive/          # отработавшие скрипты Grafana и переезда доменов
Makefile
AGENTS.md                 # этот файл
PROJECT_HANDOFF.local.md  # credentials (gitignored)
```

GitHub: `PopovDmitryA/saturday-runs-next`, branch `main`.

---

## 3. Production

**С 21.10.2026 (DNS переключён ~18:20 МСК) боевой сайт работает на домашнем
сервере `saturday-run`.** VPS 195.58.34.112 заморожен и остаётся точкой отката
до ~20.11.2026 (см. «VPS после переезда» ниже). Всё, что раньше делалось «на
проде по SSH», теперь делается на домашнем сервере.

| Параметр | Значение |
|----------|----------|
| **Основной сайт** | **https://run5k.run** (A → 95.165.143.93, статический IP; IPv6 нет — AAAA у апекса удалена) |
| Хост | домашний сервер `saturday-run` (LAN 192.168.1.26, tailnet `saturday-run`), Ubuntu 24.04 |
| Код прода | клон `~/srs-prod`, коммит прода — `~/srs-prod/.deployed_sha` |
| Compose | `cd ~/srs-prod && docker compose -p srs_home -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram` |
| БД | контейнер `postgres` (postgres:16 Debian, TZ Europe/Moscow, контрольные суммы), с хоста — `127.0.0.1:5433` |
| Web | роутер 80/443 → `edge` (nginx + TLS, `deploy/nginx/home/edge.conf`, certbot в контейнере) → контейнерный `nginx` → `api` |
| Маркер «сайт дома» | `~/.srs-site-at-home` — по нему синхронизаторы, бэкап и очередь профилей работают с домашней базой |
| grafana.run5k.run | заглушка «дашборды переехали»; Grafana закрыта 04.09.2026 |
| Старый URL ЛK | `app.run5k.run` → 301 на run5k.run |

### Контейнеры (prod)

Все 17 сервисов — в одном стеке `srs_home`. Команды — в `docker-compose.yml`,
домашние отличия — в `docker-compose.home-site.yml`; тесты
`tests/test_celery_beat_schedule.py` и `tests/test_worker_queue_isolation.py`
читают команды оттуда же.

| Сервис | Назначение |
|--------|------------|
| `api` | FastAPI, 4 uvicorn workers |
| `nginx` | React `frontend/dist` + proxy `/api` |
| `edge` | TLS и домены, редиректы www/app, заглушка grafana; перечитывает сертификат раз в 6 ч |
| `certbot` | продление сертификатов (`renew --webroot` раз в сутки) |
| `postgres`, `redis` | база и брокер; наружу — только postgres на 127.0.0.1:5433 |
| `beat` | Celery Beat (Europe/Moscow) |
| `worker` | `-Q celery,warm --concurrency=2`: наблюдатель протоколов, прогрев главной, агрегаты популярности, погода, admin-дайджест |
| `worker-warm` | `-Q warm --concurrency=1`: прогрев рейтингов и страниц локаций, пересчёт рейтингов челленджей |
| `worker-five-verst-user` | `-Q five_verst_user --concurrency=1` (синки по кнопке) |
| `worker-five-verst` | `-Q five_verst --concurrency=1 --prefetch-multiplier=1` (фон: сверка, ротация, реестр, клубы) |
| `worker-five-verst-fresh` | `-Q five_verst_fresh --concurrency=1` (свежесть: latest) — отдельный контейнер, потому что порядок очередей в `-Q` kombu не гарантирует |
| `worker-s95` | `-Q s95_user,s95 --concurrency=1 --prefetch-multiplier=1 -O fair` |
| `worker-runpark` | `-Q runpark --concurrency=1 --max-tasks-per-child=1` |
| `worker-parkrun` | `-Q parkrun --max-tasks-per-child=100`: og_render (Chromium; пишет картинки в `./data`, их раздаёт nginx). Дома сам в parkrun не ходит (`PARKRUN_SERVER_FETCH_ENABLED=false`) |
| `bot` | Telegram long poll (вход + admin: /stats /status /sweep /sync) |
| `tg-proxy` | xray, выход к api.telegram.org (см. §10); через неё же дома ходят сверка каталога parkrun (`PARKRUN_CATALOG_PROXY_URL`) и прогноз Open-Meteo (`WEATHER_PROXY_URL` — api.open-meteo.com с домашнего адреса не отвечает) |

Профили parkrun разбирает очередь `pm-site-queue.timer` (копия кода
`~/saturday-runs-next`, VPN-выходы 127.0.0.1:10851–10878) — с маркером она
пишет в домашнюю базу.

**Логи** всех контейнеров — в journald и переживают пересоздание при выкате:
`journalctl -t srs_home-worker-s95-1 --since '2 days ago' -o cat` (тег — имя
контейнера). Строки длиннее 16 КБ лежат в журнале кусками, `docker compose logs`
склеивает их обратно, пока контейнер жив.

### Как работать с продом

```bash
cd ~/srs-prod
C="docker compose -p srs_home -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram"
$C ps                                                    # что работает (должно быть 17)
$C exec -T -e PYTHONPATH=/app api python - < script.py   # разовый скрипт на боевой базе
$C exec -T api python scripts/<скрипт>.py               # скрипт из репозитория
$C exec -T postgres psql -U saturday_runs -d saturday_runs_lk   # SQL
$C logs --since 30m worker                               # логи живого контейнера
```

С Мака и из других сессий — через tailnet: `ssh saturday-run '<команда>'`.
Только чтение — `report_ro` на `127.0.0.1:5433` или отчётный API (ниже).
**На VPS за данными не ходить**: его база закрыта на запись и застыла на 21.10.2026.

### Деплой

```bash
# На домашнем сервере (или ssh saturday-run 'bash ~/srs-prod/scripts/deploy_home.sh'):
bash ~/srs-prod/scripts/deploy_home.sh            # вершина origin/main
bash ~/srs-prod/scripts/deploy_home.sh <коммит>   # откат (коммит из main, см. ниже)
```

`deploy_home.sh` переводит `~/srs-prod` на коммит, собирает фронт (node:22-alpine)
и образы, прогоняет миграции, пересоздаёт стек, перезапускает nginx и
перечитывает край, проверяет `/health` и что все сервисы в running, и только
тогда пишет `~/srs-prod/.deployed_sha`. Без маркера `~/.srs-site-at-home` не
работает. Упал — что уже новое и как откатить, см. «Протокол деплоя» ниже.

`scripts/deploy_prod.sh` (выкат на VPS) после переезда отказывает: на VPS лежит
`.site-moved-home`. Осознанно (откат) — `ALLOW_VPS_DEPLOY=1`.

**Версия релиза.** Каждый деплой получает версию X.Y.Z (или X.Y.Z-fixN) по
протоколу docs/release_management.md: ПЕРЕД деплоем согласовать номер с
Дмитрием (`scripts/add_release.py --suggest` печатает кандидатов), ПОСЛЕ —
внести скрытую запись релиза на проде: `$C exec -T api python
scripts/add_release.py …` (правка — `scripts/update_release.py`). Публикация
и правки — в админке `/admin/releases`.

**Копия кода очереди профилей.** `~/saturday-runs-next` (очередь parkrun) обязана
совпадать с продом. Таймер `pm-home-sync` (`scripts/home_sync.sh`) раз в 5 минут
сверяет её с `~/srs-prod/.deployed_sha` и, если коммит сменился, дожидается
конца прогона очереди, копирует код и при изменении `pyproject.toml` обновляет
venv. Там же `scripts/home_workers_sync.sh` с маркером сторожит стек: не все 17
сервисов в running или `/health` не 200 два захода подряд — тревога админу в
Telegram (`scripts/lib/home_alert.sh`, повтор не чаще раза в 6 часов,
«восстановлено» — когда прошло). Сбой ночного бэкапа — тоже тревога.

**`git pull` в `~/srs-prod` не делать** — клон двигает только `deploy_home.sh`.

Предохранитель: `app/db/schema_guard.py` сверяет головную миграцию кода с
`alembic_version` в базе и останавливает разбор очереди с внятным сообщением,
если версии разъехались.

### Протокол деплоя

Деплой = «привести прод к вершине `origin/main`», а не «выкатить мою фичу»:
`deploy_home.sh` сам делает `git fetch` в `~/srs-prod` и берёт `origin/main`,
откуда бы его ни позвали. Незапушенное и несмерженное не уедет; один запуск
везёт все смерженные фичи, а каждый — это `npm ci`, сборка образов и
пересоздание стека: по разу на фичу не гонять. Просьбы поэтому две.

**«Закоммить и запушь»** — фичевая сессия, прод не трогаем:
1. Коммитить только своё — по путям (`git add <пути>`), не `-A`.
2. `git fetch origin && git rebase origin/main`, затем `git push origin HEAD:main`
   (отказ — снова fetch + rebase, без `--force`). Ветку `main` в worktree не
   переключить — её держит основная папка, и она там отстаёт: не ориентир.
3. Доложить: коммит в `origin/main`, деплой — отдельной просьбой.
   **`deploy_home.sh` не запускать**: соседняя сессия может быть посреди
   вливания, и выкат увезёт её полуфабрикат.

**«Задеплой»** — деплойная сессия:
1. Версию релиза согласовать с Дмитрием (абзац «Версия релиза» выше).
2. Что поедет: `git fetch origin && git log --oneline $(cat ~/srs-prod/.deployed_sha)..origin/main`.
   Коммит прода — маркер, не `HEAD` клона: после упавшего выката `HEAD` уже
   новый, маркер — прежний. Маркер записать: это коммит отката.
3. **Замка нет** (`/tmp/saturday-runs-deploy.lock` — только у `deploy_prod.sh`):
   два запуска подерутся за checkout, сборку и `up -d` одного стека. Перед
   запуском `pgrep -af '[d]eploy_home'` (без скобок найдёт свою же оболочку); идёт —
   дождаться и сверить маркер, твой коммит мог уехать с ним.
4. `bash ~/srs-prod/scripts/deploy_home.sh` и дочитать вывод. Успех — последняя
   строка `✓ health 200, маркер обновлён: <sha>`. Любой сбой (fetch, коммит,
   сборка фронта или образов, миграция, `up`, nginx/край, `/health` не 200,
   сервис не в running) — `ДЕПЛОЙ НЕ ПРОШЁЛ: …` и код 1. Перезапускать —
   только поняв причину.
5. После — скрытая запись релиза. Копию очереди профилей `pm-home-sync`
   подтянет сам (заход раз в 5 мин, идущий прогон очереди пережидает).

**Сбой и откат.**
- До `up -d` (фронт, образы, миграция) контейнеры старые, но на диске уже
  новое: **фронт** (nginx отдаёт `frontend/dist` с диска, его пересобирают
  первым шагом) и **код** (`./backend` смонтирован во все python-сервисы —
  перезапуск любого поднимет новый код). После `up -d` новые и контейнеры.
- Откат — `deploy_home.sh <коммит>`, коммит — маркер до выката (шаг 2; на
  сбое `.deployed_sha` не двигается, после успеха он уже новый). Скрипт не
  сверяет коммит с `origin/main` и не предупреждает: всё смерженное позже
  уедет с прода, а `origin/<ветка>` выкатит несмерженное. «Выкатить только моё» старым коммитом — нельзя.
- **Через новую миграцию так не откатиться**: старый код её не знает,
  `alembic upgrade head` падает `Can't locate revision`, скрипт встаёт на
  миграциях. Сначала, пока `~/srs-prod` на новом коммите (миграции alembic
  берёт с диска), `$C run --rm -T api alembic downgrade <ревизия>`, потом
  выкат; или чинить вперёд. Ревизия — `revision = "…"` головного файла
  `backend/alembic/versions` старого коммита (с именем файла совпадает не
  всегда: 101, 104). Откат уже упал — клон стоит на старом коммите, сначала
  `git -C ~/srs-prod checkout -q --detach <новый>`.
- Работает та копия скрипта, которую позвали: из `~/srs-prod` — версия
  прошлого выката (bash дочитывает старый файл и после checkout). Правка
  самого `deploy_home.sh` действует со следующего выката.
- Руками в `~/srs-prod` не править: `checkout --detach` (не `reset --hard`,
  как на VPS) понесёт правку в следующий выкат, а конфликтующую — уронит на
  checkout.

### Бэкапы

`srs-pg-backup.timer` (23:30) → `scripts/home_pg_backup.sh` → S3
`backup:backup-popov-servers/postgres/saturday_runs_lk/saturday_runs_lk_home_*.dump`
(локально — `~/srs-backups/pg`, 3 дня; в бакете — 30 дней). Без маркера — SKIP.
Восстановление — как шаг `db` сценария переезда: `pg_restore --no-owner
--no-acl`, затем `scripts/create_report_ro_role.sql`.

### VPS после переезда (точка отката до ~20.11.2026)

`195.58.34.112`, `/opt/saturday-runs-next`, ssh `viewer` по ключу с домашнего
сервера, root — веб-консоль Timeweb. Там:
- работают api, nginx и redis на заглушке `deploy/nginx/maintenance_on`, фон остановлен;
- база `saturday_runs_lk` — `default_transaction_read_only = on`;
- крон `pg_backup.sh` под root закомментирован.

Откат: `scripts/cutover_rollback.sh now` с домашнего сервера:
- снимает режим «только чтение»;
- поднимает VPS с чистым расписанием beat;
- возвращает `.env` и маркер, DNS — руками обратно.

Если дома уже писали — сначала `back-dump`. Он ставит дом на заглушку, переносит
Redis, кладёт на VPS дамп и скрипт `/tmp/home_back_*.sh`, который запускает root
на VPS; потом `now`.

Сценарий переезда — `scripts/cutover_to_home.sh` (`preflight → certs → media →
build → freeze → media → db → start → mark → start-full → warm → verify → DNS →
outside`). Выполнен 21.10.2026.

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
  Для роли закрыты `login_events`, `user_geo_pings`, `email_login_requests`,
  `user_notification_channels`, `auth_login_requests` (список — в скрипте);
  таблицы новых миграций ей открываются сами.
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
| `parkrun` | worker-parkrun | `parkrun_sync.*` (дома в parkrun не ходит: «Обновить» ставит заявку в `profile_fetch_pending`, разбор выключен `PARKRUN_SERVER_QUEUE_ENABLED=false`), `og_render.*` |
| `runpark` | worker-runpark | `runpark_sync.*`: latest, кросслинки, user_sync |
| `warm` | worker-warm, worker | `leaderboards.warm_cache`, `locations.warm_cache`, `challenge_ratings.rebuild` |
| `celery` (default) | worker | наблюдатель протоколов, прогрев главной, page_stats, погода, user_names, sync_runs.close_stale, дайджест |

У **каждой** записи beat есть `expires`: не взяли вовремя — задача умирает, а не
копится долгом после простоя воркера. Где срок не задан руками, он считается из
расписания (`schedule_interval_seconds` в `celery_app.py`, потолок 6 ч); тест
`test_expires_never_outlives_the_interval` не даст задать срок длиннее интервала.

### S95 user priority (cooperative yield)

Один worker, один поток fetch (Redis lock). Batch-задачи **не параллелят** запросы.

При появлении задачи в `s95_user` batch прерывается (`S95YieldForUserSync` в `app/s95/fetch/priority.py`), ставится обратно в очередь, user sync выполняется первым.

Код: `app/s95/fetch/` — `coordinator.py` (одна дверь для HTML и JSON), `priority.py`, `rate_limit.py`; `workers/s95_batch_yield.py`.

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
| reconcile | 22:10, **только пн–пт** (до 13.10.2026 — каждые 3 ч; 5 вёрст просили снизить нагрузку) |
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
**parkrun**: `process_pending_queue` `:07`/`:37` — только на VPS (дома выключено, очередь разбирает `pm-site-queue.timer`, §16); og_render — выходные и понедельник.
**Прогревы**: главная — выходные ежечасно `:15`, будни 4,9,14,19; рейтинги `:20`
и локации `:40` каждые 2 ч; рейтинги челленджей `:50` каждые 2 ч (§12a) — плюс
после каждого синка, записавшего протоколы. **Служебное**: page_stats `:35`, sync_runs.close_stale
`:05`, статусы локаций 21:10, дайджест 21:50, погода 03:05/03:20 (+ пятница 8,14,20),
напоминания планировщика `:00` ежечасно (шлют в 18–21 по поясу локации, §10c),
сон постоянных планов 10:40.

---

## 5. S95: блокировка адреса и охлаждение

**Адрес, с которого ходит сбор, может быть заблокирован s95.ru.** Так было с VPS `195.58.34.112`; с 17.09.2026 S95 собирается с домашнего `95.165.143.93`.

Транспорт — обычный httpx (`app/s95/fetch/http.py`), Playwright снят 17.07.2026,
образ `worker-s95` без Chromium. HTML (профили участников, описания и статус
локаций) и JSON API (`app/s95/api_client.py`: `pages.json`, события, активности)
идут через одну дверь `fetch/coordinator.py`: уступка пользователю →
охлаждение → пауза 15–30 с → Redis-замок → пауза ещё раз под замком → запрос.
До 29.08.2026 JSON ходил мимо координатора.

| Механизм | Где |
|----------|-----|
| Отказ = бан | 403/429 и `ConnectError` (с 25.08.2026 s95 рвёт TCP) — `fetch/http.py`; страница защиты в HTML — `app/s95/ban.py`. Таймаут — не бан |
| Охлаждение 1ч → 5ч → 24ч → 72ч → неделя | `fetch/ban_state.py`; Redis `s95:fetch:ban_cooldown_until` + `s95:fetch:ban_level`; `s95_ban_cooldown_steps_seconds`. Успешный запрос сбрасывает лестницу |
| Пропуск batch при охлаждении | `sync_task_reporting.py` (`platform_fetch/cooldown.py`) |
| Текст ошибки пользователю | `app/s95/messages.py`, `services/sync_error_format.py` |

Диагностика (`$C` — см. §3, «Как работать с продом»):

```bash
$C exec -T api python scripts/check_failed_sync_jobs.py      # полные тексты ошибок sync_jobs
$C exec -T redis redis-cli GET s95:fetch:ban_cooldown_until   # unix-время конца охлаждения
$C exec -T redis redis-cli GET s95:fetch:ban_level            # ступень лестницы
```

---

## 6. Синхронизация: два уровня

### 6.1 Bulk sync (Celery, автомат)

Latest, registry, reconcile, rotation — см. docs по платформам. Трекинг: таблица `sync_runs`.

### 6.2 User sync (по запросу пользователя)

`platform_links.last_user_sync_at` — кнопка «Обновить», OAuth-привязка, auto-sync.

Код: `app/sync/user_sync.py`, `s95_user_sync.py`, `parkrun_user_sync.py`.

Admin queue: `/admin/queue`, API `/api/admin/sync-queue`.

**Reconcile «задача не была обработана воркером»:** задача dequeued, но worker занят long batch; исправлено проверкой `task_is_worker_reserved` + S95 yield.

---

## 7. Личные рекорды (PR)

Три хранимых флага на `run_results` (`is_global_pr` и `is_location_pr` —
миграция 046, `is_pr` старше). Правило одно: **первый
результат в разрезе — база отсчёта, а не рекорд**; рекорд — только строго более
быстрый забег позже. Тестовые старты (`is_test_event`) и дубли «не в зачёте»
(вторичная сторона `event_crosslinks`) базу не задают и флагов не получают.

| Флаг | Разрез | Пересчёт | UI |
|------|--------|----------|----|
| `is_pr` | одна система (5 вёрст / S95 / parkrun / RunPark), по `Participant` | `recalculate_personal_records(db, platform, participant_id=…)`; у 5 вёрст метка протокола «Личный рекорд» (`achievement_labels`) OR-ится с расчётом | плашка **PR** (`.badge-pr`) |
| `is_global_pr` | все системы человека, по `User`; каждое улучшение лучшего времени; дебют в новой системе — рекорд, если быстрее всего прежнего | `recalculate_cross_platform_personal_records(db, user_id)` | **янтарный жирный** финиш (`GlobalPrFinishTime.tsx`) |
| `is_location_pr` | одна физическая локация сквозь системы, по `User`; ключ — `LocationCatalogIndex.canonical_identity_key` (`catalog:<id>`, без узла — `location:<id>`) | та же функция, что для `is_global_pr` | **фиолетовое жирное** название локации (`LocationPrLocationName.tsx`) |

**Витрина ≠ база** (решение Дмитрия 19.07.2026). В списке пробежек и на
странице рекордов (`dashboard_service.list_user_runs` /
`list_user_personal_records`) дебют в системе идёт с плашкой PR
(`is_pr OR is_first_run`), а самая первая пробежка с временем — как глобальный
рекорд (`_user_first_timed_run_id`); флаги в БД остаются False. Что попадает в
список PR и в счётчик плитки, решает один фильтр
`run_displayed_personal_record_sql_filter()`. Поменял критерий — увеличь
`ANALYTICS_VERSION`.

Пересчёт запускается сам: `sync/upsert.py` после записи протокола или профиля
(`recalculate_pr`) пересчитывает затронутым участникам флаги дебюта, потом
`is_pr`, потом кросс-системные флаги. Демон очереди parkrun копит участников в
`parkrun_pr_service.deferred_pr_recalc()` и считает их одним проходом в конце прогона.
С `participant_id` сбрасывается только этот участник, без него — вся
платформа (тяжело; так работает полный обход S95 API).

Не путать: `location_records_service.py` — рекорды трассы среди ВСЕХ бегунов
(«Рекорды локаций»), к этим флагам не относится. Вехи «Моей истории»
(`my_history_service.py`) считают те же три разреза на лету, одна веха на
пробежку: global_pr → pr → location_pr.

Бэкфилл (на проде — `$C exec -T api …`, §3):

```bash
python scripts/recalculate_personal_records.py --platform all          # is_pr; ещё --platform five_verst|s95|parkrun|runpark, --participant-id
python scripts/recalculate_cross_platform_personal_records.py          # is_global_pr + is_location_pr; --user-id
```

`backend/scripts/run_recalc_all_prs.sh` прошит на путь VPS
(`/opt/saturday-runs-next`) — дома не работает.

---

## 8. Location catalog (русские названия parkrun)

`data/location_catalog.json` + таблицы `location_catalog`, `location_catalog_links`.

Для parkrun-локаций с аналогом в 5v/s95 показывается `canonical_name` (русское).

**Slug mismatch:** parkrun parser даёт `readovsky-park`, каталог — `readovskypark`. Lookup нормализует slug (`normalize_location_slug` в `location_catalog_service.py`).

Import: `make location-catalog-import-docker`.

**id узла каталога — личность локации, его нельзя менять.** Ключ
`catalog:<id>` хранят ручные гранты кабинета организатора, выбранная руками
домашняя локация, оценки локаций и гео-пинги (`CATALOG_KEY_REFERENCES` в
`location_catalog_service.py`). До 29.09.2026 импорт делал DELETE + INSERT
всего каталога: 26.09.2026 правка одного Раменского выдала всем узлам новые id,
и организаторы молча потеряли доступ к кабинету (починено скриптом
`backend/scripts/archive/restore_catalog_keys_20260926.py` по дампу 25.09).
Теперь импорт обновляет узлы на месте (parkrun-слаг → связки → название), а
узел, на который кто-то ссылается, без `--force` не удаляет. Новая колонка с
таким ключом — в `CATALOG_KEY_REFERENCES` (уникальная в паре с другой — ещё и
в `CATALOG_KEY_UNIQUE_WITH`); сторож — `pytest tests/test_import_location_catalog.py`.

Локация без узла живёт под ключом `location:<id>`. Связали её в каталоге —
импорт сам переводит её оценки, гранты, дом и гео-пинги на `catalog:<id>`
(`rekey_linked_location_references`); до 25.10.2026 они оставались под
старым ключом и выпадали из витрин. На проде перед импортом —
`$C exec -T api python scripts/import_location_catalog.py --from-json --dry-run`
(тот же импорт в транзакции: какие узлы и связки появятся, уйдут, изменятся;
затем откат), потом то же с `--import-db`.

**Локация сменила систему** (Раменское — 5 вёрст → S95, 26.09.2026;
Черкизовский пруд — октябрь 2026): один узел, `active_platform` — новая
система, связки обеих. Старую строку гасит реестр системы или ручной статус
«не действует» (`/admin/location-contacts`). Строка новой системы голосует за
статус узла и до первого старта, поэтому пауза старой не гасит локацию целиком.

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

### Возврат на страницу после входа

Нажал «Войти» на любой странице — после входа любым способом вернулся туда же
(путь, строка запроса и якорь). Новичок без привязок сначала проходит
онбординг (`/welcome?next=…`), кнопка «Вернуться на страницу» ведёт дальше.

- **Носитель — `?next=` у `/login`.** Ссылки «Войти» в разметке остаются голым
  `/login` (пререндер и роботы не плодят вариантов), адрес текущей страницы
  дописывает один перехватчик `installLoginLinkReturn` (`lib/loginReturn.ts`,
  `window`, фаза перехвата — раньше роутера и окон поверх страницы) в момент
  нажатия. Программные переходы — `goToLogin()` / `withLoginReturn()`
  (RequireAuth, RequireAdmin, голос в бэклоге без входа). Выход из профиля
  ведёт на голый `/login`, ссылке с `data-login-plain` адрес не дописывается.
- **Сервер хранит адрес рядом со своим состоянием:** OAuth — `return_path` в
  `oauth_state:*`; бот — `login_req_next:{token}` (живёт как заявка, после
  подтверждения — как ссылка-страховка; claim его не стирает, ссылка ещё
  может пригодиться); виджет Telegram — `return_path` в `auth:tg:state:*`;
  почта — поле `next` в теле `/auth/email/verify`. Режим привязки (link)
  адрес не хранит.
- **Куда вести, решает одна функция** `post_login_redirect_target(db,
  user_id, return_path)`: новичок → `welcome?next=…`, остальные → страница,
  без адреса — как раньше (`welcome` / `dashboard`).
- **Открытый редирект закрыт проверкой** `core/return_path.py`
  (`sanitize_return_path`, при КАЖДОМ чтении из Redis): только путь от «/»,
  без схемы и хоста, без `\` и управляющих символов, не на `/login`,
  `/oauth`, `/auth`, `/api`, `/render`, `/welcome`; кавычки, `<>`, пробелы и
  кириллица кодируются процентами. Клиентская копия — `safeReturnPath`,
  менять обе вместе. Страница успеха OAuth (`_oauth_success_response`)
  экранирует адрес и в атрибуте, и в `<script>`.
- **Ошибки провайдеров.** VK, Яндекс и Telegram возвращают человека на свои
  служебные страницы, наши параметры туда не доезжают: страница входа кладёт
  адрес в sessionStorage вкладки (`sr_login_next`, 30 минут), и «вернуться ко
  входу» собирает `loginRetryHref()` уже с ним.
- В robots.txt `Clean-param: oauth_error&link_error&next /login`.

### Удаление профиля

Настройки → «Удаление профиля» (`DeleteAccountSection`, якорь
`/settings#delete-account`). `POST /api/account/delete` с телом
`{confirmation: "удалить", attempt_id}` (слово без учёта регистра), превью для окна —
`GET /api/account/deletion-preview` (`api/routes/account.py`). Админу удалить
себя нельзя: 403, раздел скрыт — админ определяется способом входа из
настроек сервера, и после удаления он вошёл бы снова тем же админом.

Что уходит и что остаётся (решения Дмитрия 12.10.2026) — в докстринге
`services/account_deletion_service.py`. Коротко: профиль, способы входа,
привязки, настройки, уведомления, цели, отзывы о локациях с фото, треки, гранты
организатора, планы стартов и отметки «Не бегаю» (§10c) уходят; протоколы остаются (человек снова виден под именем из
протокола); карточки и комментарии бэклога остаются с подписью «Профиль
удалён» (миграция 109: авторы `ON DELETE SET NULL`), его голоса снимаются с
пересчётом счётчиков (`backlog_service.detach_user_from_backlog`).

- **Порядок в `delete_account` важен.** Таблицы с FK на `users.id` без
  ON DELETE (`account_merge_service.BLOCKING_USER_REFERENCES`) чистятся явно.
  Сторож `tests/test_account_deletion.py` сверяет этот список со схемой, а
  сквозной тест требует, чтобы в каждой такой таблице до удаления была строка
  человека и после — ни одной. **Новая таблица со ссылкой на users** — либо
  ON DELETE CASCADE / SET NULL, либо строка в списке, строка в мире теста и
  шаг в сервисе. Сам профиль удаляется Core-запросом: `session.delete(user)`
  обнулил бы NOT NULL-ключи загруженных коллекций раньше каскада базы.
- **Очередь сбора профилей** (`profile_fetch_pending`): все незавершённые
  заявки человека закрываются как окончательный отказ с меткой
  `ACCOUNT_DELETED_ERROR`, а не удаляются — иначе после SET NULL перезапуск
  неудач превратил бы их в анонимный сбор чужого профиля, а батч, держащий
  строку, падал бы. `process_pending_row` по этой метке не возвращает в
  очередь заявку, закрытую, пока она была в работе.
- **Обрыв после коммита.** Браузер шлёт случайный `attempt_id`, сервер после
  коммита помнит его час (`account:deleted:*` в Redis), и при неясном исходе
  (обрыв, 5xx, 401, 404) окно спрашивает `GET /api/account/deletion-status?attempt=…`
  — без входа, ответ только «да/нет» по своей попытке. По одному 401 «удалила
  эта попытка» не отличить от «вышли на другой вкладке».
- **Паспорта трасс** пересобираются по ВСЕМ версиям
  (`location_course_service.refresh_profiles_after_track_removal`), без
  определения смены трассы: опустевшая версия снимается, линия из удалённых
  треков нигде не остаётся.
- **После ответа** (`finalize_account_deletion` фоном через BackgroundTasks —
  там сеть: бакет и прокси Telegram с таймаутом 30 с, а клиент ждёт столько
  же; каждый шаг сам по себе):
  файлы аватара и фото отзывов, OG-картинка `og/users/{serial}.png` (её nginx
  раздаёт напрямую — сама не исчезнет), Redis (последний IP по telegram_id,
  кэши организатора и рекордов, отложенные статусы бэклога), заметка админу.
  Бан по telegram_id остаётся.
- **Сессии других устройств** глобально не ищутся: `get_current_user` /
  `get_optional_user` (`api/deps.py`) удаляют сессию, профиля которой уже нет,
  на первом же запросе с неё.
- **Публичные кэши** (рейтинги 6 ч, страницы локаций 3 ч, главная сутки)
  дотухают сами — как после отвязки; окно подтверждения об этом говорит.
  Освободившийся `public_slug` не резервируется.
- **Браузер:** после удаления `forgetPersonalBrowserState()`
  (`lib/browserState.ts`) стирает личные ключи localStorage/sessionStorage,
  выход — `forgetSessionBrowserState()`. Новый личный ключ в хранилище
  браузера — вписать туда. Прощальное «Профиль удалён» на главной —
  `AccountDeletedNotice` по метке `sr_account_deleted` в sessionStorage.

### Особые награды

Награды, которые выдаются руками, а не считаются из протоколов. Первая —
«Тысячный участник»: тысячным привязал профиль участник №1571 (16.10.2026). Порядок считали по первой `platform_links.linked_at` среди тех,
у кого привязка есть сейчас, вместе с админом — так же, как «с привязками» в
`/admin/stats` (решение Дмитрия 20.10.2026).

- **Виды — в коде**, `app/award_kinds.py`, как и виды уведомлений: новая
  награда — новая запись, без миграции. Название, значок и текст общие для
  всех обладателей. `single_holder=True` — награда одна на весь сайт, вторую
  выдачу сервер отклоняет (409), пока первую не отзовут.
- **Хранение** — `user_awards` (миграция 113): `code`, `awarded_on` (дата
  события, её видит человек, — не дата выдачи), `granted_by_user_id`,
  `note` (только для админа, наружу не отдаётся). Вид пропал из кода — строка
  остаётся, но нигде не показывается.
- **Выдача** — `/admin/users` → «Действия» → «Награды»
  (`/api/admin/users/{id}/awards`, `services/user_award_service.py`).
- **Показ — тем же блоком, первыми, той же клеткой** (решения Дмитрия
  20.10.2026: отдельный раздел перегрузил бы интерфейс, а широкая плашка
  выбивалась из витрины). Поле `awards` в ответе `/achievements` и
  `/users/{serial}/profile/achievements`; в витрине «Мои достижения»
  (`AwardTile` в `AchievementsPage.tsx`) клетка того же размера, что медаль:
  название, подпись «Награда», дата. Выбивается значок — почтовая марка
  (`AwardStamp.tsx`; выбор Дмитрия 21.10.2026 из десяти вариантов): зубцы
  перфорации, гравированное поле, номер выпуска под «№» (`emblem`: «1000»,
  дальше «10 000») или символ (`icon`), подпись внизу (`caption`) и гашение.
  Цвет поля — `tone` (carmine / ultramarine / emerald), у каждой награды
  свой: награды складываются в серию марок. Описание — в подсказке
  (наведение / тап, атрибут `title`). Вкладки сложности и фильтр систем на
  неё не действуют, в «N из M» она не входит.
  Закрытый профиль закрывает и награды — как всю витрину.
- **Награда — о человеке, а не об аккаунте:** при объединении переезжает к
  выжившему (`account_merge_service._reassign_user_content`), при удалении
  профиля уходит каскадом. Номер в порядке привязок, посчитанный на лету,
  сдвигается от удалений и отвязок — поэтому юбиляр фиксируется строкой
  награды, а не запросом.
- **Заранее о рубеже не объявлять:** владение профилем при привязке не
  проверяется, объявленное «N-е место» можно занять подставными аккаунтами.
  Публичный пост о человеке — только с его согласия (согласие при входе
  покрывает лишь личный кабинет).

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

- **Новый вид уведомлений включается сам** у всех, у кого уведомления уже
  работают (правило Дмитрия 24.09.2026). Достаточно дописать запись в
  `app/notification_kinds.py`: в `user_notification_prefs.kinds` лежат только
  виды, тумблер которых человек трогал сам, остальные читают `default_enabled`
  из реестра. Миграций, проставляющих значения существующим людям, писать
  нельзя — они оставят новый вид выключенным у всех. `default_enabled=False`
  только для заведомо шумных видов (сейчас «Новые карточки в бэклоге»).
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
- **Призывы включить:** модалка в кабинете (`NotificationsPromptModal`;
  «Не сейчас» — молчим 7 дней, `nudge_snoozed_at`, на всех устройствах; клик
  мимо окна — до следующего захода (sessionStorage); «Больше не напоминать» —
  навсегда, `nudge_dismissed_at`; новый вид уведомлений отказ не сбрасывает —
  решения Дмитрия 09.10.2026. До них «Не сейчас» жило в sessionStorage, и окно
  всплывало на каждом открытии сайта)
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
- **Челленджи в сообщении о пробежке:** сначала взятые уровни (медаль), затем
  обычный прогресс «+1 → 18 сек, до серебра 7» по снимку счётчиков
  (`challenge_counts`, миграция 104) — человеку важен каждый шаг, а не только
  медаль (просьба Дмитрия 27.09.2026). Челлендж с новым уровнем в список
  прогресса не попадает, строк прогресса не больше `PROGRESS_LIMIT`.
- **Рейтинги отдельно** (`kind=ratings`): раз в неделю, воскресенье 14:00 МСК
  (`notifications.weekly_ratings`) — место против `ratings_snapshot` недельной
  давности, вверх и вниз, по всем рейтингам сайта КРОМЕ «Дальности от дома»
  (там место считается по километрам до площадок, и «поднялся» значит
  «съездил дальше», а не достижение). Список — `RATING_METRICS`. Сразу после
  пробежки не пишем: протоколы субботы догружаются до воскресенья, и место
  за день меняется. Снимок сеется при включении уведомлений. Уходит, только
  если сдвинулось хоть одно место, но перечисляет ВСЕ рейтинги человека
  (неподвижные — «(=)») и рядом прирост самого значения: «266-е место (▼7) ·
  +1 пробежка» — место считается против всех, и «▼7» без «+1» читается как
  «пробежку не учли» (20.10.2026). Снимок — `{метрика: {rank, total}}`;
  старые снимки с одним местом числом читаются.
- **Склонение подписей.** Единицы счётчиков (`unit` челленджей, `METRIC_META`
  рейтингов) записаны родительным множественного («дат», «номеров»); рядом с
  одним числом — только через `app/ru_plural.py` (`unit_form`), иначе
  «+1 → 191 дат». Новая подпись — строка в `UNIT_FORMS`, сторож —
  `pytest tests/test_ru_plural.py`.
- **Отмены стартов** (`kind=cancellations`,
  `services/cancellation_notification_service.py`): рассылаются ВСЕМ
  подписчикам по всей стране, а не только по «своим» площадкам — человек
  может собираться в другой город (решение Дмитрия 23.09.2026) — это
  умолчание. Сузить можно двумя фильтрами, отмена проходит оба: системы —
  `user_notification_prefs.cancellation_platforms` (пустой список = все,
  миграция 101; в интерфейсе только 5 вёрст и S95, у parkrun/RunPark
  недельных отмен нет) — и места: таблица `user_cancellation_places`
  (миграция 113, `services/cancellation_places_service.py`). Место —
  локация (ключ каталога `catalog:<id>`, поэтому таблица в
  `CATALOG_KEY_REFERENCES`) или целиком `city:`/`region:`/`country:` по
  свёрнутому имени; нет ни одного — вся страна. В настройках это поле с
  чипами (`CancellationPlacesPicker.tsx`), подсказки
  `GET /settings/notifications/cancellation-places?q=` — из каталога
  локаций в Redis, с сокращениями поиска по сайту. Москва и Петербург в
  каталоге и город, и регион — подсказка одна, `region:`. Пятничная сводка
  фильтруется так же и пишет «По местам: …». Весь набор изменений одного синка —
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
  Смена статуса уходит двумя текстами: автору — с благодарностью («Спасибо,
  что предложили»), остальным следящим — тот же факт без личного обращения
  (`_STATUS_TAIL_AUTHOR` / `_STATUS_TAIL_WATCHER`). Статусы Дмитрий проставляет
  пачкой, поэтому смены копятся в Redis по получателю
  (`services/backlog_status_digest.py`) и уходят ОДНИМ сообщением: beat
  `notifications.flush_backlog_statuses` раз в пять минут отдаёт группы, где тихо
  QUIET_SECONDS или которые ждут дольше MAX_WAIT_SECONDS. Одна карточка —
  прежний подробный текст, несколько — список строк. Redis недоступен —
  отправляем сразу по одной.
- **Планировщик — три вида** (решение Дмитрия 26.10.2026: «может, он хочет
  только планировать, а напоминания ему не нужны»): `plan_reminders`
  «Напоминание накануне», `plan_cancellations` «Отмена запланированного
  старта» и `plan_series` «Постоянный план на паузе», все включены по
  умолчанию (`services/plan_notification_service.py`, §10c). Тумблеры всех
  трёх — в «Моих планах» (`/plans#plan-notifications`, раздел «Уведомления
  о планах»), а не в `/settings`: там на месте раздела планов — только ссылка
  туда. У напоминания последняя строка тела — «Напоминания не нужны?
  [Выключить](…/plans#plan-notifications)», кнопка сообщения — «Мои планы»,
  подвал — общий. Время старта в напоминании осталось («старт в 9:00») —
  из интерфейса планирования его убрали, а в сообщении накануне оно нужно.
  Прежнего общего кода `plans` больше нет; оставшийся в
  `user_notification_prefs.kinds` ключ `plans` функции
  `kind_enabled`/`kinds_state` молча пропускают (неизвестный вид — выключен и
  не показывается).
- **Подвал сообщения.** В Telegram и VK — одна тихая строка
  «run5k.run · настроить уведомления»: домен первым и ссылкой на главную —
  уведомления пересылают в чаты (отмены, протоколы), и получатель должен
  видеть, откуда они; настройки рядом, без шестерёнки и черты — возможность
  есть, но не кричим о ней (решение Дмитрия 02.10.2026). Ведут в
  `/settings#notifications`, а не на отписку: чаще нужно донастроить, а не
  отрезать всё (24.09.2026). Мгновенная отписка
  `/api/notifications/unsubscribe?token=…` (HMAC на `app_secret_key`, внутри
  user_id и вид или `all`) остаётся в ПИСЬМАХ — её требуют почтовые
  провайдеры, там же `List-Unsubscribe`. Первый клик выключает вид,
  «выключить всё» гасит все каналы.
- **Ссылку-действие не дублировать в теле:** она уходит кнопкой уведомления
  (`url`/`url_label` в `notify_user`), и повтор в тексте читается как два
  одинаковых линка подряд. У отмен кнопки нет вовсе: название каждой площадки
  само по себе ссылка (`[**Имя**](url)` — разметка умеет жирную подпись).
- **Текстовые каналы:** в `to_plain` адрес уезжает СТРОКОЙ НИЖЕ подписи —
  длинная ссылка посреди фразы разрывает её пополам.
- **Копия админу** каждого доставленного сообщения («📨 Сообщение направлено
  @ник · канал» + тот же HTML) — пока `NOTIFICATIONS_ADMIN_COPY=true`, кроме
  видов из `ADMIN_COPY_SKIP_KINDS` (`notification_service.py`; сейчас пусто:
  копии напоминаний о планах Дмитрий оставил себе, 29.10.2026; если за
  пятничный вечер их сотни и они упрутся в лимит Telegram — вернуть
  `plan_reminders` в множество).
- Админка: `/admin/notifications` — подписчики по каналам, сводка по
  статусам/видам/каналам, лента доставок с ошибками.
- Под pytest отправители и проверки молчат (`is_test_run()`), тесты подменяют
  `SENDERS`, `CHECKERS` и `enqueue_delivery` — см. `tests/test_notification_service.py`.

---

## 10b. Бот: локации рядом по геопозиции

Человек присылает @weekend_runs_bot геопозицию (в том числе трансляцию или
точку, выбранную на карте) — бот отвечает локациями рядом (решение Дмитрия
02.10.2026). Код: `services/nearby_locations_service.py` (правило, карточки,
текст), `bot_app/nearby.py` (обработчики), ручки `POST /internal/bot/nearby`,
`POST /internal/bot/nearby/inline`, `GET /internal/bot/nearby/point`.

- **Правило:** до трёх локаций в радиусе `NEARBY_RADIUS_KM` = 15 км, ближние
  первыми; в радиусе пусто — «рядом стартов нет» и одна ближайшая с
  расстоянием, хоть за 600 км. «Не действует» не показываем; отменённая и
  «скоро открытие» остаются в списке, но места не занимают — к ним добавляется
  следующая, чтобы было куда пойти. Набор — тот же, что у карты
  (`list_catalog_map_locations`).
- **Карточка:** расстояние и система, ближайший старт (дата, время, прогноз
  номера «≈№» — как в попапе карты), погода из `start_weather_forecast`, адрес
  из «Как добраться». Если Telegram привязан к профилю — «бегали здесь N раз»
  или «станет вашей N-й локацией» и +1 в «Нумераторе»; не привязан — строка
  «Войдите через Telegram». Под каждой локацией кнопки: «📍 Имя» (бот
  присылает точку — из неё Telegram строит маршрут) и страница на сайте.
- **Inline-режим:** `@weekend_runs_bot` в любом чате — локации рядом или поиск
  по названию и городу; в чат уходит карточка без личного и без расстояния.
  Включается в BotFather: `/setinline` (подсказка в поле ввода) и
  `/setinlinegeo` — без второго Telegram не присылает геопозицию, и работает
  только поиск по названию. Пока inline выключен, приветствие о нём молчит
  (`start_message` смотрит `supports_inline_queries`).
- **Белые пятна:** каждая геопозиция — строка `nearby_query_log` (миграция
  106), анонимно, точка огрублена до клетки 0.05° (~5 км) ещё до записи.
  Клетки, где ближайшая локация дальше 30 км, подписывает местом задача
  `nearby.label_place` (Nominatim, очередь `celery`). Отчёт — админка
  «Старты рядом» (`/admin/nearby`, `GET /api/admin/nearby-log`). Поиск по
  названию в inline в журнал не пишется: каждая буква — отдельный вызов.

---

## 10c. Планировщик забегов

Человек отмечает «Планирую» на старт локации — планирует там пробежать;
локация показывает, кто к ней собирается (решения Дмитрия 24–25.10.2026;
разбор развилок — Artifact AXXoRFuVvJC1KvYLFyHk27). Код: `services/start_calendar_service.py` (какие даты
можно планировать), `services/run_plan_service.py` (планы, видимость, блок
локации, оргкалендарь, особые даты), `services/plan_notification_service.py`,
`api/routes/planner.py` (`/api/planner/*`), фронт — `features/planner/`.
Миграция `116_run_plans` (растёт от `115_challenge_ratings`). Решения
26.10.2026: «Не бегаю», строка о ближайшем
старте в профиле, два вида уведомлений, раздел оргкабинета «Кто у нас бежит».
Решения 27.10.2026: отдельного согласия на планы нет — публичность у каждого
плана, абзац «Планы на старты» в общем согласии, календарь «Планы» в профиле,
только дни стартов в «Моих планах». Решения 27.10.2026 (вечер): заметок нет
вовсе — ни к плану, ни на день («перегружают интерфейс, потом будем
планомерно добавлять»); детали дня — компактная панель; у субботы постоянного
«Убрать» спрашивает «только этот день / остановить постоянный план»; лимит дня
— одно правило во всех окнах. Правки 27.10.2026 по стенду: замок у
приватного плана и приватного «Не бегаю» в клетке «Моих планов» (и пункт
легенды), «Планирую пробежать» прямо в дне с «Не бегаю», заполненный день —
замена плана с предупреждением, вкладка «Планы» профиля без нажатий (локации
в клетках), «и ещё X» раскрывает список на месте, постоянные — значком ↻ в
общем списке, столбец «Челленджи» в «Кто у нас бежит», неактивные «Планы»
чужого профиля без публичных планов.
Постоянный план «каждую субботу» — `services/planned_starts_service.py`
(кто планирует на какие даты — ЕДИНСТВЕННОЕ место чтения планов) и
`services/plan_series_service.py` (запись, сон, подсказка), см. «Постоянные
планы» ниже.

- **План** — строка `run_plans`: человек × место (`location_key` = ключ
  идентичности `catalog:<id>|location:<id>` + опорный `location_id`) × дата.
  План — только «пробежать»: намерения нет ни в
  данных, ни в API, ни в текстах. **Волонтёров планировщик не ведёт** — они
  записываются в личных кабинетах систем и видны на тамошних страницах
  записавшихся волонтёров (решение Дмитрия 25.10.2026; до него было «бегу /
  волонтёрю»). Строка без локации — строка дня (одна на дату): отметка «Не
  бегаю» (`rest`, см. ниже). **Заметок нет** (решение Дмитрия 27.10.2026):
  колонки `note` нет, ручек `/planner/day-notes` нет, поле `note` в
  `POST /planner/plans` молча отбрасывается, `PlanItem.kind` строки дня —
  `"day"`. Будних дней в календаре планов нет. Кнопка одна — «Планирую», отметка ни к чему не
  обязывает, но «на всякий случай» её не ставят: организаторы ориентируются на
  число участников. Лишнее поле `intent` в
  `POST /planner/plans` молча отбрасывается, как любое лишнее поле схем
  проекта. Ключ в `CATALOG_KEY_REFERENCES`; планы ищутся по
  `location_key = :key OR location_id IN (строки идентичности)` — ключ дрейфует
  `location:`→`catalog:`. Ключ из запроса приводится к одной записи
  (`normalize_identity_key`).
- **Какие даты.** Только календарь локации на 365 дней: субботы + особые даты
  из `start_special_dates` (ведёт админ, `/admin/start-dates`; сид — 01.01.2027).
  Своих дат нет. Суббота есть всегда: особая дата рядом (перенос на пятницу
  или воскресенье) её не убирает, галочки «переносит субботу» нет (Дмитрий
  26.10.2026: «суббота всегда существует, вне зависимости от переносов»). Не планируются (`plannable_rows_with_reason`): parkrun, серии,
  `is_paused`/`is_upcoming`, регулярно-воскресный RunPark (мода дня по 10 стартам),
  строки, которые сайт считает «Не действует» (последний старт старше
  `INACTIVE_AFTER_DAYS` или стартов нет; ручное «Действует» сильнее), закрытый
  узел каталога. Лимит дня: 1 план, на особую дату — 3 (правка плана лимит не
  проверяет) — ОДНО правило везде: `run_plan_service.day_plan_limits` (по
  самой дате в `start_special_dates`) считают сохранение, `elsewhere.full`
  и `elsewhere.limit` окна от локации и `limit` окна от даты; детали дня в
  «Моих планах» держат то же число на фронте. **Заполненный день — замена, а не запрет** (правка
  Дмитрия 27.10.2026): в окне от локации дата с планом в другой локации
  выбирается, под датами «План на сб 14 ноября (Кусково) будет отменён»,
  кнопка — «Заменить план»; в окне от даты («Другая локация» в деталях дня)
  локация выбирается так же. `POST /planner/plans` с `replace_key` (ключ
  точки заменяемого плана) убирает тот план в той же транзакции и ставит
  новый (`upsert_plan` → `_plans_at_point`); плана на `replace_key` уже нет
  — обычный 409. На особую дату с тремя планами заменяется последний из
  `elsewhere` — окно его называет. Планов дня больше лимита (админ убрал
  особую дату, объединение профилей) — замена одного не уложит их в лимит,
  и `replace_key` отвечает 409 «у вас планов больше, чем можно на этот
  день: … Уберите лишние в «Моих планах»», ничего не трогая (SD-1).
  **План на дату, которой больше нет в календаре** (админ убрал, перенёс
  или отдал другим системам особую дату) — не план нигде (`still_on`): не
  напоминается, нет в строке и календаре профиля, во вкладке «Планы» его
  нет, «Обзор» не зовёт его ближайшим, страница локации, оргкалендарь,
  «Кто у нас бежит» и карточка участника его не показывают (SD-2…SD-4); у
  владельца он остаётся в «Моих планах» — убрать. Суббота постоянного не заменяется, а
  вытесняется поездкой, как раньше. ≈№ — последний номер строки + слоты до даты; отменённый старт
  номера не получает и следующие не сдвигает. Дата отменённого старта — одна
  функция `cancelled_start_date` (суббота, особая дата — только если она раньше
  и год назад старт был).
- **Публичность — у каждого плана, итог — при чтении** (решение Дмитрия
  27.10.2026: «никаких отдельных оферт, согласий, галочек»; «эту неделю
  публично, следующую приватно»). Выбор — `run_plans.is_public` (у строки
  дня — для отметки «Не бегаю») и
  `run_plan_series.is_public` (один на весь постоянный план; вхождения
  берут его, а разовый план на его же точку в его даты —
  `PlannedStart.series` — публичен, только если публичны и серия, и строка
  (`entry_public`): приватный план, поставленный, пока серия спала или её
  не было, не открывается ни «Продолжить», ни пробуждением протоколом, ни
  постоянным планом из подсказки (ревью 28.10.2026, PRIV-F1); правка
  публичности серии переносит выбор на такие строки — `_share_publicity`,
  иначе «Остановить» открыло бы их по старому выбору; правка публичности
  такого стоящего плана — 409 `SERIES_OWNS_PUBLICITY`, а новый план на
  точке бодрствующего постоянного (возврат домой после «Пропущу» или
  поездки) не публичнее его — без 409, CORE-2; окно показывает
  переключатель выключенным «как у постоянного плана», а окно постоянного
  предупреждает, что приватные разовые планы на его точке в его даты
  станут публичными вместе с ним). Переключатель «Публично | Приватно» — в окне плана; умолчание —
  как профиль: открыт — публично (`default_public`; новый разовый план на
  точке своего постоянного — как постоянный). Закрытый профиль публичного
  плана не даёт: `is_public=true` в обход выключенного переключателя
  сохраняется `false` (`clean_public`). «Не бегаю» на дату своего постоянного
  («Пропущу», снятая галочка окна) — по умолчанию как он (`series_public_on`):
  снятая суббота приватного плана не всплывает «не бегает» в профиле. Подсказка
  «9 из 10» ставит постоянный план одним нажатием, без окна, — снэкбар называет
  публичность и даёт «Сделать приватным». О показе планов — абзац «Планы на
  старты» в общем согласии: текст, который видит человек, — `/about#privacy`
  (`PortalAboutPage.tsx`, от второго лица); `CONSENT_WEB_FULL` — его копия от
  первого лица, сайт её не выводит, править обе; возраст в нём — «по
  протоколам беговых систем»: у S95 и у человека без привязок категорий нет,
  и такой ребёнок для сайта взрослый. Имя (как в профиле) видят все, гости тоже,
  только если: план публичный, профиль открыт и человек не ребёнок
  (`minor_user_ids`: последняя категория «<10»/«10–14»). Ребёнок с публичным
  планом и открытым профилем — числом «1 ребёнок» (переключатель у него есть,
  `forced="minor"` — «имя не покажем»); остальные — числом «и ещё N —
  приватно» (`plan_audience`, по `ref` каждого плана); имени не видит никто,
  организатор тоже. Приватный план ребёнка — в приватных: его возраст не
  раскрываем. `GET /settings/planner` → `{default_public, forced:
  "profile_private"|"minor"|null}` — всё, что окну плана нужно; сам выбор
  пишется в каждый план. `report_ro` планы не читает: `run_plans` и
  `run_plan_series` закрыты целиком (приватные планы, сон), а строки
  `notification_deliveries` видов `plan_reminders`, `plan_cancellations`,
  `plan_series` скрыты политикой row-level security (DO-блок миграции 116 +
  шаг 5b `scripts/create_report_ro_role.sql`; решено 28.10.2026). Вебвизор Метрики «Мои планы», свои
  планы на локации и на «Обзоре» не пишет (`ym-hide-content`).
  Блок локации — отдельный запрос `GET /api/planner/location?key=` с
  `Cache-Control: private, no-store`, в кэш страницы и пререндер не кладётся.
  У даты блока и будущего дня оргкалендаря — один список планирующих:
  `total`, `people` (все публичные; больше пробежек — выше, без привязок — в
  конце, при равенстве — `created_at`, затем `RunPlan.id`: «сортируй тех, у
  кого больше пробежек», Дмитрий 25.10.2026), `private`, `children` (у
  прошедшего дня оргкалендаря — пусто и 0); вид — `PlanPeople.tsx`, без рамок
  и колонок: имена через запятую, под ними строкой «2 ребёнка ⓘ · и ещё 4 —
  приватно ⓘ» — у детей и у приватных своя «i». **На странице локации у имени
  только число пробежек** — «Анна Смирнова (132)», без «впервые» и юбилеев
  (решение Дмитрия 25.10.2026: блок участников компактный, знаковые старты —
  дело оргкабинета), и высота ограничена: имён не больше трёх строк
  (`MAX_NAME_LINES`), что не влезло — ссылкой «и ещё X»: она раскрывает
  список на месте и становится «свернуть» (правка Дмитрия 27.10.2026 — окна
  со всем списком нет); раскрытие сбрасывается при смене вкладки даты;
  ровно одного за «и ещё 1» не прячем — он встаёт лишней строкой
  (замечание Дмитрия 26.10.2026). Разовые и постоянные — одним списком
  (правка 27.10.2026: строка «↻ Каждую субботу: N» была избыточна): у имени
  постоянного значок ↻ с подсказкой «Бегает здесь каждую субботу»
  (`PlanPerson.regular`), порядок прежний — по числу пробежек; приватные и
  дети постоянных — в общих числах, число во вкладке — все вместе. На странице локации заголовок «Планируют пробежать»,
  в той же строке даты вкладками «31 окт (12)» (до 6, особая — янтарная со ★;
  полная дата и название — в подсказке вкладки) и кнопка «Планирую»; ниже —
  имена выбранной даты и строка «Ваш план: …»: высота блока не растёт от
  числа дат (вариант 1, выбор Дмитрия 25.10.2026). Панели дат на широком
  блоке лежат стопкой в одной клетке сетки (видна выбранная, остальные —
  `visibility: hidden` + `inert`): высота — по самой людной дате, при смене
  вкладки блок не прыгает; на узком (< 34rem, телефон) скрытые места не
  занимают, высота — по выбранной дате. Выбранная дата переживает «Назад»
  (`useRestorableState`), пока ответа нет — место под блок держит заглушка.
- **Цифры у имени и «Кто у нас бежит»** (решение Дмитрия 25.10.2026: «на
  странице локации — вариант А, в оргкабинете — полноценный раздел»; вечером
  того же дня метки со страницы локации сняты — «юбилей или впервые у нас
  можно только в оргкабинете отмечать»; 26.10.2026 раздел «Кто к нам едет»
  переименован, заголовок страницы — «Кто у нас бежит · сб 31 октября», дата
  выбранной вкладки; адрес прежний, `/organizer/{slug}/guests`). Код —
  `services/planner_guests_service.py`; своих правил подсчёта там нет, только
  сборка. У публичного человека в `people` блока и будущего дня оргкалендаря
  ещё `runs` (всего пробежек, как плитка кабинета:
  `dashboard_service.total_runs_for_users` — годный `dashboard_cache`, иначе
  то же правило пачкой; через Redis `planner:runs:v1:*` на 10 минут; `null` —
  нет привязки с участником). Только в оргкабинете (`OrganizerPlanPerson`,
  `GuestRow`; в публичном ответе этих полей нет) — `first_here` (ни пробежки,
  ни волонтёрства здесь и это его первый план сюда), `jubilee` (номер по
  правилу «Календаря юбилеев» — `organizer_service.is_milestone`: 10, дальше
  кратные 25) и `marks` — знаковый старт `{kind, label}`: `first_ever`
  «первая пробежка», `jubilee` «250-я пробежка», `jubilee_here` «100-я у нас»,
  `jubilee_platform` «150-я в 5 вёрст» (счётчик системы — как
  `organizer_service.platform_result_counts`, но вторичная сторона
  кросслинка выпадает, только если у человека есть и первичная, —
  `_system_runs`: зеркало RunPark у точки S95 у человека только с RunPark в
  счёте; система — та строка точки, к которой у человека привязка, и так же
  для его более ранних планов — `_route_visits`), `first_here` «впервые у нас»
  (`planner_guests_service.Mark`, `_numbered_marks`). Номер, совпавший с
  «всего», второй раз не пишется; «первая пробежка» поглощает «впервые у
  нас». **Юбилеи и «первая пробежка» — только на три ближайших старта
  локации** (`JUBILEE_HORIZON_STARTS`, `jubilee_horizon`: даты календаря от
  сегодня по Москве; особая дата среди трёх — тоже старт; отменённый и
  сегодняшний с уже загруженным протоколом не считаются): дальше не
  поручиться, что человек пробежит всё до них. «Всего» для номера метки —
  большее из числа кабинета и счёта по протоколам (`_jubilee_runs`): кэш
  кабинета отстаёт только вниз, до прогрева после синка (5 вёрст latest —
  до полутора часов), и «250-я» не ждёт его; число у имени — из кэша. «Впервые у нас» — на любую
  дату: это не прогноз номера. Запланированная пробежка считается с поправкой
  на более ранние будущие ПУБЛИЧНЫЕ ПЛАНЫ человека (`plans_ahead`; приватная
  поездка сдвинула бы номер и выдала себя организатору), а не на «бегал бы
  каждую субботу»: две субботы подряд — вторая уже не 100-я, а 101-я; две
  субботы без планов и план на третью — третья станет runs + 1, а не runs +
  3. Поправка считает планы, а не даты: три плана особого дня —
  три пробежки, а «будет N-я» у нескольких планов одного дня не ставится ни на
  один (порядок стартов дня не известен). План на отменённый старт, на дату,
  которой больше нет в календаре, и на локацию, ушедшую на паузу (флаг
  `is_paused`/`is_upcoming` и ни одной планируемой строки — как у
  напоминания), в поправку не идёт, а сам такой план меток не получает. В
  день старта сегодняшний план — поправка, только пока человека нет в
  сегодняшнем протоколе (любая система, пробежка или волонтёрство) и
  протокол локации плана не загружен: появился — план снимается, номер
  следующих дат считается от «всего» по протоколам, а метки самой сегодняшней
  даты гаснут; протокол загружен без него — не пришёл, план тоже снимается. В
  особый день планы снимаются поштучно: человек есть в протоколе этой локации
  или её протокол уже загружен; протокола ещё нет — старт впереди.
  У приватных и детей цифры не считаются вовсе. Оргкабинет:
  `GET /organizer/{slug}/guests?weeks=12` (≤52) — даты с планами таблицей
  «Участник | Пробежек | У нас | Знаковый старт | Челленджи | Откуда»
  (столбец знакового старта рисуется из `marks`, юбилей виден без захода в
  карточку; на телефоне до ~430 в кратком виде столбец не влезает — метки
  строкой под именем). **«Челленджи»** (правка Дмитрия 27.10.2026) — что даст
  этот старт: `gains` `[{label, hint}]` у строки и `regulars.gains[handle]`,
  «новая локация (31-я)», «новый регион», «буква «К»» и номерные челленджи.
  Тот же расчёт, что «Что даст визит» карточки
  (`achievements_service.visit_gains`), с поправкой на более ранние публичные
  планы, но пакетом: история людей — одним запросом-сводкой
  (`visit_histories`), привязки — `_linked_systems`, `plans_ahead` — раз на
  запрос и для меток (`planner_guests_service.guest_gains`). Только три
  ближайших старта, как юбилеи: на двенадцати неделях Парк Горького выходил
  на границу 1,5 с холодным (замер 27.10.2026: без столбца 1,21–1,27 с, с ним
  на горизонте 1,38–1,45 с, на всех датах ~1,5 с; повтор 0,31 → 0,45 с).
  На телефоне столбец — в «Полно».
  **Номерные челленджи** (просьба Дмитрия 27.10.2026: ≈№ известен заранее
  из календаря локации) — рост с числами и взятые уровни у всех, кого
  двигает номер старта: «Нумератор», «Нумератор ПРО», «Совпадение номеров»
  (визит станет N-й пробежкой на старте №N; планов в этот день несколько —
  молчит), «Юбилейщик», «Числа Фибоначчи», «Клуб Нельсона», «Простые
  числа», «Индекс Уилсона» (`NUMBER_CHALLENGES`, `number_gains`,
  `number_gain_rows`). Правила счёта — те же, что у карточек челленджей
  (`number_challenge_counts`; сторож
  `test_number_gains_follow_the_challenge_cards` сверяет рост, уровни и
  «сколько осталось» с `_build_challenge_list`), тексты — как в сообщении
  о пробежке (`LEVEL_LABELS*` теперь в `achievements_service`, склонения —
  `unit_form`): «Нумератор ПРО: ≈№221 — новый номер» / «14 → 15 номеров,
  до золота 15», «Клуб Нельсона — 🥈 серебро» / «≈№222 — новый нельсон,
  1 → 2 нельсона, до золота 1». «Сколько осталось» — по тиру, который
  карточка покажет первым (первый без золота). Ранние публичные визиты
  считаются случившимися: их номера, юбилеи и места в счёте пробежек. В
  столбце номерные визита — ОДНОЙ строкой: медаль и название («🥈 Клуб
  Нельсона», уровней несколько — «… и ещё 1»), иначе рост одного
  («Нумератор ПРО: 21 → 22») или «рост в 3 челленджах»; полный текст — в
  подсказке по строке на челлендж. Только в горизонте трёх ближайших
  стартов (в карточке — тоже: дальний визит — `challenges_note` «Номерные
  челленджи считаем на три ближайших старта»). «Совпадение номеров» в
  сводке: номер пробежки — `rank()` по дате в запросе, дни с несколькими
  пробежками раскладываются по ключу локации, как в `_collect_run_rows`.
  Замер 27.10.2026 (Парк Горького, 12 недель, стенд с подменой опоры ≈№ на
  120 дней): холодно 1,28 → 1,33 с, повтор 0,46 → 0,51 с. У
  публичных ещё `runs_here` (дни на всех строках идентичности, как `run_count`
  детализации локаций профиля; `null`, как и `runs`, — нет привязки: «здесь: 0»
  было бы неправдой) и дом (`admin_home_location_service`, ручной
  выбор сильнее; Redis `planner:home:v2:*` на 30 минут; `here` — дом и есть
  эта локация, по ключу идентичности: слаги дома и страницы бывают разными);
  `GET /organizer/{slug}/guests/{handle}` — страница гостя: три числа шапки
  профиля (`dashboard_service.cabinet_headline_stats`: годный `dashboard_cache`,
  иначе те же функции, что считают кабинет, — без рекордов, погоды и
  дальности, ~50 мс вместо секунд; кэш не пишет), «здесь» (дни, последний визит, лучшее время),
  юбилеи — те же метки, что столбец списка, по каждой будущей дате сюда в
  горизонте (дат несколько — с подписью даты: «250-я пробежка» в списке
  бывает и на второй субботе; план сюда дальше трёх стартов — строка
  `jubilees_note` «Юбилеи считаем на три ближайших старта») и что даст
  ближайший визит, что ещё впереди (`challenges_date`, `challenges`), и
  следующие даты сюда в горизонте (`later_visits` `[{date, challenges}]`,
  только где визит что-то даст: страница, открытая из второй субботы,
  говорит то же, что её строка в таблице), челленджам
  (`achievements_service.planned_visit_gains` — история один раз на все
  визиты: «Коллекционер локаций», «Путешественник», «Международный турист»,
  «Хет-трик систем», «Алфавит», номерные челленджи по ≈№ календаря; более
  ранние планы — как уже случившиеся; система и ≈№ — той строки точки, к
  системе которой у человека привязка: у зеркала RunPark финиш придёт
  RunPark-строкой). Без
  привязок карточка цифр «здесь», юбилеев и челленджей не показывает. Без будущего публичного
  плана сюда — 404 «Участник не найден», один ответ на «нет человека»,
  «приватно», «ребёнок» и «закрытый профиль»; в карточке — только публичные
  планы сюда (приватная суббота того же человека — числом в списке, в
  карточке её нет). В аналитике страниц карточка
  копится к списку: `organizer_location` / `{slug}/guests`, хендла нет ни в
  `entity_key`, ни в `path` (`stored_page_path`; журнал визитов админки
  показывает path), ни в `ab_events.path`, ни в Метрике (`metrikaPath` в
  `lib/metrika.ts` и `srsMetrikaUrl` у init в `index.html`); список гостей и
  карточка закрыты от вебвизора классом `ym-hide-content`. Абзац «Планы на
  старты» общего согласия называет и эти цифры (всем — имя и число пробежек,
  организаторам — статистика открытого профиля, юбилеи и челленджи).
- **«Не бегаю»** (решение Дмитрия 26.10.2026: «чтобы человек мог этим
  пользоваться как личным планнером») — `run_plans.rest` на строке дня
  (CHECK: строка дня — только с отметкой; у плана с локацией `rest` не
  бывает). Только на день старта — суббота или особая
  дата любой системы (`start_calendar_service.start_day`) — от сегодня на
  365 дней: `PUT /planner/rest/{date}` `{replace}`, `DELETE
  /planner/rest/{date}` (строка дня уходит целиком). На день с
  планами пробежки — 409 `{detail, plans}`: `detail` — готовый вопрос окна
  («На сб 31 октября у вас в планах: Зеленоград. Убрать план и отметить
  «Не бегаю»?»), `replace=true` удаляет планы дня. `is_public` в теле — видна
  ли отметка в календаре профиля: у новой умолчание как у плана (профиль
  открыт — публично), повтор на стоящей отметке — правка публичности, без
  поля — без изменений. План пробежки на день с отметкой её снимает; лимиты
  дня её не считают. Строка дня без локации не попадает ни в блок локации, ни
  в оргкалендарь, ни в «Кто у нас бежит», ни в строку профиля, ни в
  напоминания (все они берут планы из `planned_starts`, а строк дня там
  нет); чужим публичная отметка видна только в календаре «Планы» профиля
  («не бегает», решение Дмитрия 27.10.2026). Прошедший день: пробежал где угодно — факт
  `ran`, иначе тихое `rested` (волонтёрство пробежкой не считается).
  Объединение: две отметки на дату — строка выжившего; план той же даты
  сильнее отметки. `report_ro` колонку
  `rest` читает (флаг без текста). Фронт — `features/planner/DayRest.tsx`
  (`useDayRest`: на дне с планами сначала окно с тем же вопросом, что в
  409, устаревшие данные — вопрос из 409): в «Планах» — строка действий дня
  в панели дня (см. «Детали дня»), в клетке — тихое «не бегаю», прошедший —
  «✓ Пробежали» или «не бегали». «Планирую пробежать» открывает окно плана от даты прямо на
  странице (`GET /planner/locations`, см. «Кабинет»), а не уводит на
  локацию. Якорь `/locations/{slug}#plan=YYYY-MM-DD` по-прежнему открывает
  окно от локации на этот день (`plannerLinks.ts`). Даты нет в календаре
  локации — окно ничего не выбирает и говорит об этом; локация не планируется
  (пауза, parkrun) — объясняет всплывающей подсказкой.
- **Профиль** (решение Дмитрия 27.10.2026: «если у человека есть план, то на
  его странице мы можем в его календарь провалиться, если это публично…
  если план приватный — в чужом профиле это будет просто пустота»). Строка в
  шапке «📅 Ближайший план: сб 31 октября — Зеленоград · все планы» — `GET
  /api/planner/profile?handle=` → `{date, kind, title, places: [{name, slug,
  cancelled, regular}], upcoming_count, only_me} | null`: ближайший
  ПУБЛИЧНЫЙ план на любую дату (не только ближайшая суббота), до трёх мест на
  особую дату; `upcoming_count` — дат с публичными планами впереди, считая
  эту. Хозяину — ближайший план любой, `only_me` — на эту дату чужим не видно
  ничего. Пункт «Планы» в колонке и полосе вкладок профиля неактивен, если
  показывать нечего (правка Дмитрия 27.10.2026): `GET
  /api/planner/profile/tab?handle=` → `{state: public|empty|closed}` —
  будущий публичный план (`entry_public`, имя показывается) или публичное
  «Не бегаю» есть / нет («Публичных планов нет») / профиль закрыт («Профиль
  закрыт»); хозяину — всегда `public`. Подсказка — `title` (наведение, тап),
  в выпадающем списке телефона — строкой под названием (`disabledHint` у
  пунктов `SiteSidebar` и `NavLink`). Вкладка «Планы» (`/users/{handle}/plans`) — `GET
  /api/planner/profile/calendar?handle=&month=YYYY-MM` → `{month, today,
  owner, days: [{date, kind, title, places, rest}]}`: публичные планы и
  публичные «Не бегаю» по дням месяца, только с сегодняшнего (прошедших и
  фактов нет), без заметок; хозяину — то же, что другим, плюс `owner`
  (ссылка «Все мои планы» на `/plans`). Месяц — не дальше года в обе
  стороны (иначе 400). Правило видимости одно — как у имени на странице
  локации (`entry_public` + открытый профиль + взрослый): ребёнку — `null` и
  пустой календарь; закрытый профиль — календарь 404 «Профиль скрыт
  пользователем» всем, кроме хозяина. Оба ответа — `Cache-Control: private,
  no-store`, мимо пререндера, OG и sitemap (вкладки профиля закрыты
  `Disallow: /users/*/`, в аналитике — просмотр профиля). Локация на паузе и
  дата, которой больше нет в календаре, — мимо, как у напоминания
  (`run_plan_service.still_on`). Фронт — `features/planner/ProfilePlanLine.tsx`
  под именем в шапке `PublicProfilePage` и вкладка «Планы». Дни вкладки не
  нажимаются и деталей под сеткой нет (правка Дмитрия 27.10.2026): в клетке
  на компьютере — все локации дня столбиком ссылками (1 января — до трёх), в
  узкой клетке — «Кусково +1» без ссылок, полный список — в подсказке клетки
  (`title`, на телефоне — тап). В «Ближайшем плане» на «Обзоре» у приватного
  плана — тихое «Приватный план»; описаний «как это видят другие» нет. В
  «Моих планах» у приватного плана и приватного «Не бегаю» впереди — замок в
  клетке (на узкой — в правом верхнем углу, напротив «↻») и пункт легенды
  «приватно — видите только вы»; в дне с «Не бегаю» рядом с «Отменить» —
  «Планирую пробежать» (окно от даты; сохранённый план снимает отметку сам).
- **Кабинет** — `/plans` (только владелец, полный календарь с приватным;
  НЕ вкладка профиля, в `CABINET_TAB_SEGMENTS` не добавлять —
  публичный календарь другим показывает вкладка профиля `/users/{handle}/plans`,
  `PROFILE_PLANS_SEGMENT`, см. «Профиль») и карточка «Ближайший план» на «Обзоре»
  (`GET /planner/next` → `{plan, day}`: ближайший план и ближайший день старта
  с `planned`/`rest` — нет плана на него, карточка предлагает «Планирую» и
  «Не бегаю»). **Два входа в планирование** (решение Дмитрия 26.10.2026:
  «два варианта, а не два, которые сводятся к одному»): от локации (её
  страница — локация задана, дата выбирается) и от даты (день в «Моих
  планах», «Планирую» на «Обзоре» — дата задана, сверху «Где бежите?»).
  Турист расписывает два месяца, не уходя из календаря. Окно от даты берёт
  локации из `GET /planner/locations?date=YYYY-MM-DD&q=` (вход обязателен,
  `Cache-Control: private, no-store`, `services/planner_locations_service.py`)
  → `{date, kind, title, limit, rest, planned, suggested, results,
  truncated}`, строка — `{key, slug, name, city, platforms, number_forecast,
  cancelled, mine, regular}`. `planned` — разовые планы дня (их и считает
  `limit`): набралось — окно заранее говорит, что новая локация заменит
  прежний план (на особую дату — последний), а не узнаёт о лимите по 409; `mine` (уже в планах дня, и суббота постоянного) — строка
  «уже в планах», не выбирается: окно добавляет локацию, правят «Изменить»; `truncated` — поиск упёрся в 20, окно просит уточнить
  запрос. Окну от локации то же даёт блок: `elsewhere` в
  `GET /planner/location` — даты с планами в других локациях (`full` —
  день заполнен: дата выбирается, план заменит тот), чип даты — «бегу в Битцу». Только то, что можно запланировать на дату, по правилам
  календаря локации (`plannable_rows_with_reason` + `start_dates`: будняя
  особая дата — только её системы, особая суббота чужой системы — обычная
  суббота). Без `q` — `suggested` (до 8): постоянная, домашняя
  (`home_location_brief_service.home_identity_key`), точки планов за 90 дней
  (сначала недавно отмеченные), частые по пробежкам за год; с `q` от двух
  знаков — `results` (до 20): выдача поиска по сайту без обрезки
  (`site_search_service.matching_location_entries`: название, город, регион,
  `_PLACE_ALIASES`, «е»=«ё»), явно мёртвое отсеивается по записи каталога, а
  остальное — точной проверкой (не больше 48 кандидатов; замер на стенде —
  ~160–190 мс на «москва»). Каталог в Redis не прогрет — поиск пуст, как у
  поиска по сайту. Сохранение — общее: `POST /planner/plans`, `PUT
  /planner/series`. На субботе постоянного — «Другая локация» в строке
  действий дня (на «Обзоре» — «Поеду в другую»): то же окно от даты, поездка
  вытесняет субботу. На особую дату с субботой постоянного — «+ ещё
  локация»: в окне (от даты и от локации) галочка «Оставить и постоянный
  план — …», по умолчанию стоит, и `POST /planner/plans` уходит с
  `keep_series=true`: сервер в той же транзакции закрепляет дом разовым
  планом на его точке (`upsert_plan` → `_series_occurrence_on`), и он
  считается в лимит три. Сняли галочку или обычная суббота — поездка
  вытесняет, как раньше («↻ … в этот день не считаем»). Окно плана кладёт запись в историю (`useOverlayHistory`):
  «Назад» на телефоне закрывает окно. В окне сверху дата и переключатель
  «Один раз | ↻ Каждую субботу», ниже локация и переключатель «Публично |
  Приватно» с подсказкой; окна согласия нет. У постоянного плана локация и переключатель — сразу под
  субботами, до абзаца о паузе (`SeriesOptions`, `afterDates`): на телефоне
  иначе переключатель уезжал под прокрутку. Публичность сохраняется полем `is_public` (`POST
  /planner/plans`, `PUT /planner/series`, `PUT /planner/rest/{date}`; без
  поля — у нового умолчание, у старого без изменений), у строк ответа —
  `PlanItem.is_public` и `PlanSeries.is_public`. «Уведомления о планах» —
  сворачиваемый раздел «Моих планов» (`#plan-notifications`, по якорю
  открыт), в `/settings#planner` — ссылка туда. Время старта в интерфейсе
  планирования не показывается (чипы дат, «Мои планы», «Ближайший план»,
  блок локации, оргкалендарь) — API его по-прежнему отдаёт.
  Прошедший план — факт по протоколам при показе (`plan_facts`): пробежал,
  был на старте волонтёром (`volunteered` — тоже «был», не «не получилось»)
  или тихая серая отметка, без счётчиков неявок. **Оргкабинет** —
  `/organizer/{slug}/calendar`: будущие старты с планирующими, прошедшие —
  «планировали N — пришли M» без имён (`came` — были на старте: пробежали или
  волонтёрили).
- **Уведомления** — три вида (§10a): `plan_reminders` — напоминание накануне
  в 18–21 по поясу локации (beat `notifications.plan_reminders` ежечасно; для
  01.01 это 31.12), одно на человека и дату: «Завтра, сб 31 октября —
  Кузьминки, старт в 9:00 (≈№214). Не опаздывайте!», несколько локаций —
  списком, последней строкой «Напоминания не нужны? Выключить» (ссылка на
  `/plans#plan-notifications` — тумблеры видов планировщика в разделе
  «Уведомления о планах» «Моих планов»: подраздел прокручивается и моргает, без
  каналов — «Уведомления выключены — включить»; «Способы входа» и «Мои планы»
  — разные страницы, каждая читает `/settings/notifications` сама);
  под стартом (решение Дмитрия 30.10.2026, `services/plan_reminder_extras.py`,
  своих правил нет) — погода на старт (`forecast_for_locations`, только
  прогноз на эту дату), одна личная веха из меток «Кто у нас бежит»
  (`people_stats`; первая пробежка → юбилей всего → в системе → здесь →
  «Здесь вы впервые — станет вашей N-й локацией»; поправка — только планы
  этого дня, свой приватный план тоже в счёт: сообщение — ему одному) и
  «👥 С вами планируют ещё N человек: имена…» (имена — по `plan_audience`,
  как на странице локации; число — ссылкой на локацию). Кнопки под
  сообщением — `MessageButton` (`notification_senders.py`, в payload
  доставки `buttons`): «🧭 Проложить маршрут» (Яндекс Карты `rtext=~lat,lon`)
  и «🏷 Мой штрихкод» по системе плана, если код из привязки собирается
  (`services/scan_code_service.py` — то же правило, что `platformScanCode`
  на сайте, QR тот же). В Telegram штрихкод — кнопка бота `scancode:<система>`:
  бот (`bot_app/scan_code.py`) берёт `GET /internal/bot/scan-code` по
  Telegram нажавшего и присылает картинку; в VK и письме — ссылка
  `/dashboard#scancode=<система>` (открывает QR в «Профилях беговых
  систем»). VK-кнопки — встроенная клавиатура `open_link`; не принял (у
  сообщества выключены «Возможности бота») — то же сообщение без неё, ссылки
  строками. У отменённого старта ни строк, ни кнопок. Копия админу — без
  кнопок. Сбой любой части — строки нет, напоминание уходит.
  `plan_cancellations` — «Старт отменён» планирующим из
  `location_cancellation_notify.notify_cancellation_changes`, мимо фильтров
  мест; дата старта — `cancelled_start_date` от сегодняшнего дня ПО МОСКВЕ,
  как у календаря (у Барнаула в сб 20:51 МСК местное уже воскресенье, и
  местный день отдавал флаг следующей субботе), а по поясу локации — только
  «старт уже прошёл». Нет даты в календаре или локация не планируется —
  напоминания нет.
- **Постоянные планы** («каждую субботу», решения Дмитрия 25.10.2026 по
  странице «Планы для постоянных»: V1 «беру», V3 «обсудить», Q01–Q10). Модель
  — **правило + исключения**, как повторяющаяся встреча в Outlook: строка
  `run_plan_series` (человек × точка × `starts_on`/`until`; `until = NULL` —
  «пока не остановит»), строк `run_plans` на каждую неделю НЕТ и ночного
  дописывания нет. Активная серия у человека одна (частичный уникальный
  индекс по `stopped_at IS NULL`), на любой точке по его выбору. Вхождения —
  даты календаря точки в сроке: ВСЕ субботы и ВСЕ особые даты систем точки,
  что завёл админ (Q08=A; 26.10.2026: «особые даты всегда проставляются
  вручную в админке, не надо никакой логики здесь городить» — проверки «год
  назад старт был» у серии нет), будущие — пока точка планируется;
  отменённый старт вхождение не снимает. **Все
  читатели планов** (блок локации, оргкалендарь, «Кто у нас бежит», карточка
  гостя, «Планы», «Обзор», профиль, лимит дня, напоминания, «Старт отменён»,
  поправка юбилеев `plans_ahead`, сон) берут их из
  `planned_starts_service.planned_starts` — сторож
  `test_plan_readers_use_planned_starts` не пускает `RunPlan.location_key.isnot(None)`
  и `RunPlanSeries` никуда больше (кроме писателей `upsert_plan`/`set_rest`,
  удаления и объединения). Исключения — обычные строки: «Не бегаю» на день
  («Пропущу» одним нажатием, Q09) убирает вхождение; разовый план в другой
  локации его вытесняет в ЛЮБОЙ день (поездка, Q07; убрал — суббота
  вернулась; `DELETE /planner/plans/{id}?rest=true` — «вообще не бегаю»);
  разовый план на свою точку поглощает вхождение (для чужих — постоянный).
  Вхождение в лимит дня не идёт (кроме закреплённого `keep_series` — оно
  уже разовый план). Серия сама строк планов не пишет. У вхождения в API `PlanItem.id = null`,
  ключ — `ref` (`series:<id>:<дата>`), `series {id}`; у поездки — `displaces`,
  только если она в этот день единственный разовый план: при второй поездке
  или плане на свою точку «вернётся, если уберёте» не обещаем.
  Остановка («Остановить»/замена) — `until = вчера` + `stopped_at`, прошлое
  остаётся в «Планах» и оргкалендаре; без прошлого — строка удаляется.
  **Сон** (Q03 + V1): три старта дома подряд без человека (суббота с
  протоколом его системы; не в счёт — «Не бегаю», поездка, отменённый
  старт, будние особые, сегодня, всё до создания и «Продолжить») → план
  засыпает (`sleeping_since`, `sleep_missed`), имя пропадает с будущих дат,
  одно «Ещё ходите?» вида `plan_series` (без дат и счёта неявок). Был на точке
  в любой день с первого старта окна (и в день не в счёт) — сна нет: задача и
  чтение решают одним правилом (`first_presence`). Кончившийся срок V1 не
  засыпает — у него «Продлить». Правило одно для всех (Дмитрий 26.10.2026:
  «3 пропуска — план засыпает, пробежка возобновляет, либо вручную на
  сайте»; правила «21 день не заходил» больше нет): без привязок к системам,
  что проводят на точке старты сейчас (`info_rows`; привязка к мёртвой строке
  каталога — parkrun-эпоха, старая S95 — не в счёт), считаются субботы с
  протоколом любой живой её системы — человека там не видно, и план уснёт
  через три старта; текст — «мы не видели вас в
  протоколах… Профиль системы не привязан к сайту… Привязать профиль»
  (`/dashboard#profiles`), `PlanSeries.wakes_by_presence = false` (у
  нового плана — тот же признак в `GET /planner/series/preview`). Привязал
  — протокол будит, как у всех (результат на «пропущенном» старте — сна не
  было). Просыпается сам — пробежал
  или поволонтёрил на точке после первого «пропуска» (лениво при чтении,
  сразу после протокола; результат за сам «пропуск» — сна не было) — или
  «Продолжить». Задача `notifications.plan_series_sleep` (beat 10:40 МСК,
  `expires` 3 ч) записывает сон и пробуждение, останавливает сроки V1,
  кончившиеся больше месяца назад; каждая серия — под `_lock_user`, своей
  транзакцией; на стенде — только с `user_ids`. Планы и сообщения о них `report_ro`
  не читает (DO-блок 116 + шаг 5b). **Подсказка** «Вы пробежали здесь 9 из 10
  последних стартов» (Q10): самая частая планируемая точка за 13 недель, ≥6
  из 10; блок своей локации, «Обзор», «Планы»; «Не сейчас»
  (`users.plans_hint_snoozed_until`, 30 дней), «Остановить» и «Отменить» после
  одного нажатия — тоже; Redis `planner:series-hint:v1:*` 6 ч. На странице
  локации постоянные — в общем списке имён со значком ↻ (`regular`; правка
  Дмитрия 27.10.2026), вкладки — ближайшая, даты разовых и особые;
  вхождения считаются только на даты-кандидаты вкладок (шесть ближайших
  суббот, все даты разовых и особые — без обрезки). Оргкалендарь, будущий
  день — «Планируют 41 · из них ↻ постоянных 9» (без «обычно ~N») и один
  список имён с метками, у постоянных ↻ (правка Дмитрия 27.10.2026; API
  по-прежнему отдаёт `people` и `regulars` раздельно, сливает страница),
  прошедший — `regulars.total` и `regulars_came`; «Кто у нас бежит» — состав
  постоянных один раз (`regulars_people`), у даты — `handles`, метки и
  челленджи горизонта, а на странице постоянные стоят в общей таблице со
  значком ↻ («Планируют 41 · из них ↻ постоянных 9»).
  Напоминание — как у любого плана (Q06), у вхождения строка «Не получится?
  Пропущу» (`/plans#skip=YYYY-MM-DD`), кроме отменённого старта. Копии `plan_reminders` админу идут
  (решение 29.10.2026, `ADMIN_COPY_SKIP_KINDS` пуст). API — `/planner/series`
  (GET/PUT/DELETE), `/series/resume`, `/series/preview`, `/series/hint/snooze`.
  Окно «каждую субботу» — только вариант V3 (выбор Дмитрия 26.10.2026):
  переключатель «Один раз | ↻ Каждую субботу» и галочки ближайших дат; срок
  `until` фронт больше не задаёт (поле API и старые сроки V1 остались). Дата,
  где у человека разовый план в другой локации, подписана «бегу в Битцу»
  (винительный падеж — хелпер на фронте).
- **Сторожа:** удаление профиля — CASCADE + счётчик `run_plans` в превью
  (планы и «Не бегаю» одним числом) и отдельно `run_plan_series`;
  объединение — `_reassign_run_plans` (конфликт по идентичности, остаётся план
  выжившего; `is_public` у каждой строки свой и переезжает с ней, у двух
  «Не бегаю» на одну дату — выжившего) и `_reassign_run_plan_series`
  (активные у обоих — у выжившего своя, поглощённая останавливается и
  переезжает историей; у выжившего срок кончился, а у поглощённого живой —
  остаётся живой); слияние двойников parkrun удаляет план на старом
  ключе, если на ту же дату есть план на новом (ключ серии переписывает общий
  цикл `CATALOG_KEY_REFERENCES`).
- **Детали дня в «Моих планах»** (отзыв Дмитрия 27.10.2026: «колхозно —
  локация, отдельным блоком «Не бегаю», ниже криво-косо заметки»; из трёх
  раскладок выбрана «строки», `DayPanel` в `PlansPage.tsx`, стили `.pday-*`).
  Сверху дата и у особой — плашка названия и системы; ниже планы дня
  строками без рамок: локация-ссылка, значок системы, тихие «↻ постоянный» и
  «приватно», под ними мелко ≈№ / отмена / «↻ … не считаем» / факт; справа
  «Изменить · Убрать» (на телефоне — значки карандаша и корзины). Внизу одна
  строка действий дня: «Планирую пробежать» (день пуст), «+ ещё локация»
  (особая дата, пока планов меньше трёх, суббота постоянного в счёт),
  «Другая локация» (суббота постоянного) и «Не бегаю». Стоит «Не бегаю» —
  одна строка «Не бегаете · Публично | Приватно · Отменить». «Изменить» у
  субботы постоянного — окно постоянного плана; «Убрать» у неё (и у
  закреплённой разовой строки дома, если она одна в дне) — окно
  `SeriesDayRemoveModal`: «Только эту субботу» (отметка «Не бегаю» с
  `replace`) или «Остановить постоянный план» (красным). «Остановить план» —
  красным контуром и в карточке постоянного плана над месяцем, и в подвале
  его окна (Дмитрий его не нашёл: была серая «призрачная» кнопка).
- **Админка «Планы»** — `/admin/plans` (`services/admin_plans_service.py`,
  `GET /api/admin/plans` и `/api/admin/plans/feed`): сводка, план → факт,
  новые планы и люди по дням, нагрузка на 8 недель стартов, «за сколько дней
  ставят», топ локаций, доставки `plan_*` и лента всех записей (планы, «Не
  бегаю», постоянные с остановкой и паузой) — приватные тоже, с пометкой.
  Нагрузка и факты — через `planned_starts`; строки читаются как записи
  (файл — в списке сторожа `test_plan_readers_use_planned_starts`).
  «План на дату» (`GET /api/admin/plans/day?date=`, якорь `#day=YYYY-MM-DD`;
  открывается кликом по дате нагрузки или полем даты, ‹ › — соседние старты):
  локации по убыванию людей, в каждой — люди (имя → публичный профиль
  `/users/{serial}`, всего пробежек из `cached_total_runs`, ↻ постоянный,
  🔒 приватный, «профиль закрыт») и внизу «Не бегают». Тоже `planned_starts`
  по людям с разовым на дату или постоянным, начатым не позже; не суббота и
  не особая дата — 400, прошлые даты открываются. Лента фильтруется по дате
  старта (`plan_date`: разовые и «Не бегаю» на дату, постоянные, действовавшие в неё).
- **Отложено** (бэклог /__plan, раздел «Планировщик забегов»): заметки к
  плану и на день (сняты 27.10.2026, вернуть планомерно), публичные метки, пейсер, ICS, уведомление организатору о новых планах, «план сбылся»
  в сообщении о пробежке. Запись в волонтёры — не здесь: она в кабинетах систем.

---

## 11. Fetch locks

| Платформа | Lock | Интервал |
|-----------|------|----------|
| 5 verst | Redis | 20–30 с |
| S95 | httpx + Redis `s95:fetch:global_lock` (TTL 180 с) | 15–30 с, пауза и до замка, и под ним; один запрос на кластер |
| parkrun | Redis `parkrun:fetch:global_lock` (`parkrun_fetch_lock`); у очереди профилей — в её Redis :6399 | очередь дома: httpx через VPN-выходы, `DELAY`=3 с ±30%; серверный путь и ручной `make parkrun` — 25–55 с (§16) |

---

## 12. Dashboard / API

- Cache: `dashboard_cache`, `ANALYTICS_VERSION` — инкрементировать при смене полей аналитики
- Admin: `/admin/stats`, `/admin/queue`, `/admin/users`, `/admin/page-analytics`
- Settings: `/api/settings/privacy`

### Навигация и поиск по сайту

С 23.09.2026 (вариант В): на компьютере — рельс разделов + колонка со всеми
страницами раздела (`SiteSidebar.tsx`; «О проекте» и аккаунт в рельс не
входят — `inRail: false`; настройки, админка и выход — в меню под именем в
шапке, `nav/AccountMenu.tsx`), на телефоне — одна нижняя панель на
весь сайт (её рисует `PortalHeader`), липкая полоса страниц раздела под шапкой
(до 6 страниц — вкладки, больше — «Группа · Страница ▾» с выпадающим списком;
`nav/SectionSubnav.tsx`) и шторка «Меню». Всё это строится из одного дерева
`features/portal/nav/siteNav.ts` — вручную пункты меню нигде больше не пишутся:
ссылки шапки, подвал, вкладки чужого профиля (`CABINET_TABS`), названия
рейтингов на хабе, в крошках и H1 (`RATING_GROUPS` → `leaderboards/ratingNames.ts`)
берутся оттуда же.

- **Одно имя на раздел везде** (рельс, колонка, панель, «Меню», шапка, подвал,
  меню аккаунта): «Кабинет» (гостю — «Войти»), «Оргкабинет», «Итоги»,
  «Локации», «Рейтинги», «О проекте». Названия рейтингов на сервере
  (`METRIC_META` в `leaderboard_service.py`) — те же слова, что `label` в
  `RATING_GROUPS`; `colLabel` — только короткая подпись для узкой колонки.
- **Ссылки разделов в шапке** есть только на страницах без рельса (главная,
  «О проекте», блог, обновления, вход, онбординг, 404, админка) и только
  шире 900px. Их прячет CSS по метке `html.has-site-rail`, которую ставит
  `SiteSidebar`.
- **Свёрнутая колонка** на компьютере (`html.site-col-collapsed`) — над
  контентом появляется та же полоса страниц раздела, что на телефоне
  (`nav/siteNavDesktop.css`); страницы раздела не пропадают.
- **Окна поверх страницы** («Меню», выпадающий список полосы, список раздела по
  повторному нажатию кнопки панели, поиск) кладут запись в историю: «Назад»
  закрывает окно, а не уводит со страницы. Подключение —
  `nav/useOverlayHistory.ts` (`dismiss`, `dismissThen`, `interceptLinks`) и
  `nav/useOverlayFocus.ts` (фокус внутрь, Tab по кругу, Esc).
- **Оргкабинет** вне раздела ведёт в последнюю открытую (или единственную)
  локацию организатора. Роль, последняя локация и список локаций хранятся в
  localStorage (`nav/organizerMemory.ts`) и стираются в `LogoutButton`.
- **«Локации»** в колонке и «Меню»: «Все локации», «Моя: …» (домашняя из
  `/auth/me` → `home_location {slug, name}`, `home_location_brief_service.py`,
  без тяжёлой детализации) и до трёх недавних (`lib/recentLocations.ts`,
  localStorage этого браузера).
- **Новый верхний адрес раздела** — вписать в `SECTION_PATH_PREFIXES`
  (`siteNav.ts`). Сторож — `pytest tests/test_site_nav_routes.py`: каждый роут
  `STATIC_ROUTES` из `App.tsx` обязан быть в дереве (ссылкой или под адресом
  раздела) либо в `NOT_IN_NAV` с объяснением.

Поиск: страницы ищутся на клиенте по дереву и его `keywords`
(`nav/pageSearch.ts`; служебные слова «как», «где», «мой» выбрасываются, слово,
в котором узнана страница, в поиск людей не уходит), локации и люди —
`GET /api/search` (`site_search_service.py`). Люди из протоколов без открытого
профиля на сайте отдаются только статистикой, без ссылок на их профили —
строка ведёт только в «Это вы?» (см. ниже).

- «е» и «ё» не различаются: имя сворачивается `translate(lower(name), 'ё', 'е')`
  с обеих сторон, под это выражение — триграммный индекс (миграция 103).
- Локации ищутся по названию, городу, региону и стране; сокращения и
  разговорные имена городов («спб», «питер», «мск», «екб», «подмосковье») —
  словарь `_PLACE_ALIASES` в `site_search_service.py`. Пополнять по `/admin/search`.
- Каталог `/locations` понимает `?q=` — туда ведёт строка «Все локации: Москва (N) →».

Журнал запросов — `search_query_log` (миграция 102), анонимный; смотреть в
`/admin/search`. Главный отчёт там — «Искали и никуда не перешли» (запрос
пишется и при уходе «Назад» или закрытии вкладки, `sendBeacon`), следом —
«Не нашлось ничего». Оба — список недостающих синонимов для `siteNav.ts` и
городов для `_PLACE_ALIASES`.

**«Это вы?» — привязка прямо из поиска** (решение Дмитрия 27.09.2026). Строка
человека из протоколов нажимается: подэкран «Это вы?» внутри окна поиска
(`nav/ProtocolClaimCard.tsx`; «Назад» на телефоне возвращает к выдаче —
`pushStep`/`popStep` в `nav/useOverlayHistory.ts`). Вошедший привязывает сразу,
гость жмёт «Войти и привязать» — намерение лежит в localStorage
(`nav/searchClaim.ts`, `sr_search_claim`, 2 часа), после входа любым способом
`nav/SearchClaimRunner.tsx` (глобальный, в `App.tsx`) показывает окно «Это вы?»
ровно с этим человеком; `NotificationsPromptModal` на это время молчит.

- Внутренних id людей в выдаче нет: у строки участника непрозрачный
  `claim_token` (`services/search_claim_service.py`: encrypt-then-MAC на stdlib,
  ключи выведены из `app_secret_key` с меткой `search-claim:v1`, срок 2 часа).
  Минтится в роуте `/api/search` через `Depends(get_settings)`, не в сервисе.
  Заглушкам («Неизвестный», `unknown:`/`anon:`) токен не выдаётся.
- `GET /api/search/claim?token=` — карточка и `viewer_state`. Гостю всегда
  `guest`: что профиль уже чей-то, гостю не раскрываем.
  `POST /api/profiles/link-by-search-token` — привязка, `link_method='site_search'`
  (в `/admin/stats` — «поиск по сайту»). `POST /api/search/claim/decline` — «Это не я».
- Воронка — `search_claim_events` (миграция 105), анонимная: только `ref` из
  токена, этап (`open`/`login`/`linked`/`failed`/`declined`), система, код.
  Отчёт — карточка «Это вы?» на `/admin/search`. Боты (в т.ч. HeadlessChrome —
  Playwright на стенде) и админ туда не пишутся.
- Ограничение: намерение живёт в браузере. Если вход завершился в другом
  (VK на телефоне вернул через приложение, ссылка-страховка бота открыта во
  встроенном браузере Telegram), окно после входа не появится — человек найдёт
  себя в онбординге.

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

## 12a. Рейтинги челленджей

`/ratings/p-index` … `/ratings/start-numbers` — рейтинги по челленджам «Моих
достижений» (p- и V-индекс, Уилсон, серия, ударный год, 60 секунд, бинго
позиций, круглый год, алфавит, Нумератор), по одному пункту меню на челлендж
(группа «Челленджи»). Код — `services/challenge_rating_service.py`, правила
подсчёта — `app/challenge_growth.py`: те же функции считают и карточку
челленджа, поэтому рейтинг и кабинет не расходятся. Новое правило челленджа —
только туда.

- **В зачёте все участники протоколов**, отсечки как у остальных рейтингов
  (тесты, дубли кросслинков, допуск parkrun) плюс безымянные заглушки
  (`participant_identity.is_anonymous_participant`). Системы склеиваются
  только у зарегистрированных.
- **Место**: значение ↓, у p/V-индекса — «осталось до следующего» ↑, затем
  день набора ↑ и дни двух предыдущих ступеней ↑ (ключ упакован в одно
  64-битное число, `_rank_key`). Медалей в таблицах нет.
- **Сетка снимков** «челлендж × вариант × система × активность» (156 на
  27.10.2026) лежит в `challenge_rating_snapshots`: топ-1000 и строки всех
  зарегистрированных (для «Вы» и «где добрать»). Считает её задача
  `challenge_ratings.rebuild` (очередь `warm`) — по beat `:50` каждые 2 ч и
  после синков (`schedule_challenge_ratings_rebuild` из
  `schedule_leaderboards_warm`, склейка и Redis-замок). Сам проход идёт
  **отдельным процессом** (`python -m app.services.challenge_rating_service`):
  ~3 мин и ~0,6 ГБ, которые процесс Celery иначе не отдал бы.
- **После выката с новой миграцией таблица пуста** до первого прохода —
  запустить руками: `$C exec -T worker-warm python -m app.services.challenge_rating_service`.
- **Новый рейтинг челленджа**: запись в `RATING_CHALLENGES` (+ значение в
  `_scope_metrics`), пункт в группе `challenges` (`siteNav.ts`) и иконка
  (`navIcons.tsx`), роут в `App.tsx`, мета в `seo_service.py` и `pageMeta.ts`,
  `_STATIC_PAGE_TYPES` и `pageTypeLabels.ts`. Сторожа:
  `test_challenge_rating_service.py` (меню, роуты, число снимков),
  `test_seo_service.py`, `test_page_analytics_service.py`.

## 13. Локальная разработка

### Домашний сервер (`saturday-run`, `~/dev/saturday-runs-next`)

У каждого worktree свой стенд `srs_<имя>` — `scripts/dev_stack.sh` (справка: `--help`):

```bash
scripts/dev_stack.sh up            # redis + api + nginx, свободные порты подбирает сам
scripts/dev_stack.sh up --own-db   # своя копия dev-базы — если в ветке есть миграции
scripts/dev_stack.sh up --front    # пересобрать фронт (node:22-alpine под своим uid)
scripts/dev_stack.sh url           # адрес; ещё: ls (все стенды), logs api, psql, down, rm, chown
```

- Порты: сайт `18080+N`, api `18000+N`; слот закреплён в
  `docker-compose.worktree-override.yml` (gitignored, правит только скрипт).
- База общая — postgres основного стека `srs_dev` на `15433`; без него `up`
  падает. Поднять в основном клоне:
  `docker compose -p srs_dev -f docker-compose.yml -f docker-compose.devserver.yml up -d postgres`
  (`devserver.yml` лежит только на сервере, в git его нет).
- `alembic upgrade head` в общую базу не катать — там соседние стенды. Ветка
  с миграцией → `up --own-db`.
- Воркеры и beat стенд не поднимает (дублировали бы задачи в общей базе);
  `--with-workers` — только вместе с `--own-db`.
- Compose стенда: `C="docker compose -p srs_<имя> -f docker-compose.yml -f docker-compose.worktree-override.yml"`
  (имя — в `scripts/dev_stack.sh ls`). Голый `docker compose` / `make up` в
  worktree поднимет другой проект: все воркеры, beat, свой postgres.
- **`127.0.0.1:5433` на сервере — БОЕВОЙ postgres `srs_home`.** Туда же
  смотрит дефолтный `DATABASE_URL` в `app/config.py`. Поэтому `make migrate`,
  `make test`, `make lint` (это `cd backend && alembic|pytest|ruff|mypy` прямо
  на хосте) здесь не запускать. node на хосте нет; venv с backend один —
  `~/queue-venv` очереди профилей (alembic есть, pytest/ruff/mypy нет), его
  не активировать.
- `make frontend-build` здесь не годится: без `--user` оставляет root-овые
  `node_modules`/`dist`. Фронт — `scripts/dev_stack.sh up --front`.

Проверки — те же, что в CI (`.github/workflows/ci.yml`):

```bash
# Образ api с 21.09.2026 — только рантайм, pytest/ruff/fakeredis в нём нет.
# Ставим в контейнер стенда (живут до его пересоздания), зовём через python -m:
$C exec api pip install --user -q pytest pytest-timeout fakeredis ruff
$C exec api python -m pytest -q tests/test_<что>.py   # база стенда, тест откатывается; нет базы — skip
$C exec api python -m ruff check app tests bot_app
# Фронт: lint = tsc --noEmit
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm \
  -v "$PWD/frontend:/app" -w /app node:22-alpine sh -c "npm ci --no-audit --no-fund && npm run lint"
```

`mypy` — не ворота: `mypy app` даёт 833 ошибки (21.10.2026), в CI шаг с
`continue-on-error`. Отсюда `make lint` (ruff && mypy) красный всегда.

### Отдельная машина (не сервер)

```bash
cp .env.example .env   # + PROJECT_HANDOFF.local.md для prod credentials
docker compose up --build            # = make up; погасить — make down
docker compose exec api alembic upgrade head
make frontend-build && docker compose restart nginx
```

| URL | Назначение |
|-----|------------|
| http://localhost:8080 | сайт |
| http://localhost:8080/about | О проекте |
| http://localhost/login | VK нужен порт 80 (`docker-compose.dev.yml`); на сервере 80/443 заняты краем прода |
| localhost:5433 | dev Postgres (DBeaver) — только не на сервере (там это прод) |

Тесты и линт — как на сервере (`docker compose exec api pip install …`,
`python -m pytest`, `python -m ruff check app tests bot_app`).
`make migrate/test/lint` идут на хосте и требуют venv с `pip install -e ".[dev]"`.

---

## 14. Типичные задачи агента

| Задача | Действие |
|--------|----------|
| S95 все sync failed | Проверить 403 с prod IP, cooldown Redis, писать админам S95 |
| Очередь admin — длинные ошибки | tooltip в QueuePage; полный текст в `error_message` |
| Parkrun English names | catalog link + norm slug; import catalog |
| PR не показывается | `is_pr` / `is_global_pr` / `is_location_pr`, метка протокола 5 вёрст, витрина (`is_first_run`); бэкфилл обоими скриптами §7 |
| Новая страница на сайте | Роут в `App.tsx` + раздел в аналитике: `_STATIC_PAGE_TYPES`, `PAGE_TYPE_LABELS`, `APP_ROUTES` (см. §12) |
| Новая страница: где её найдут | Пункт в `frontend/src/features/portal/nav/siteNav.ts` с синонимами (`keywords`) — из этого дерева рисуются рельс и колонка на компьютере, ссылки шапки и подвал, нижняя панель, полоса страниц раздела и «Меню» на телефоне и поиск по страницам. Новый верхний адрес раздела — ещё и в `SECTION_PATH_PREFIXES`; проверка — `pytest tests/test_site_nav_routes.py` (см. §12, «Навигация и поиск») |
| Новая особая награда | Запись в `backend/app/award_kinds.py` (код, название, значок, текст, `single_holder`, на марке — `emblem`/`caption`/`tone`), выдача — в `/admin/users` → «Награды» (см. §9, «Особые награды») |
| Новая таблица со ссылкой на `users` | Продумать ON DELETE (CASCADE / SET NULL) или добавить шаг в `account_deletion_service` и строку в `BLOCKING_USER_REFERENCES`; сторож — `pytest tests/test_account_deletion.py` (см. §9, «Удаление профиля») |
| Новый запрос статистики по `events` / `run_results` / `volunteer_results` | Тестовые старты (`events.is_test_event`, забег №0) не считаются нигде — фильтр `Event.is_test_event = false`, как в едином протоколе и рейтингах. Тест не бывает первичной стороной кросслинка: синк снимает такую связку (`upsert.forget_test_event_credits`, миграция 114), поэтому обычный дедуп вторичных событий остаётся верным |
| Сеть внутри синка или запроса | Перед походом в сеть закрыть транзакцию: `release_before_fetch` / `commit_step` (`app/sync/iteration_commit.py`), в API — `release_db_connection_for_network_io`. Postgres рвёт соединение после 60 с простоя в транзакции; сторож `app/db/tx_guard.py` пишет на проде предупреждение со стеком после 30 с, а в тестах роняет тест |
| Deploy | `bash ~/srs-prod/scripts/deploy_home.sh` на домашнем сервере (§3); версию — по протоколу релизов |
| API 502 | `$C logs api` в `~/srs-prod` (§3) — часто SyntaxError после деплоя |
| Prod DB query | на домашнем сервере: `$C exec -T -e PYTHONPATH=/app api python - < script.py` или psql в контейнере postgres (§3); НЕ на VPS |

---

## 15. Git и prod

- Коммит, пуш и деплой делает агент, но **только по явной просьбе** Дмитрия;
  «закоммить и запушь» и «задеплой» — разные просьбы (§3, «Протокол деплоя»).
  Сессии живут в worktree на ветках `claude/*`; в `main` — rebase на
  `origin/main` и `git push origin HEAD:main`.
- Prod `.env` не в git; локальный `.env` и `PROJECT_HANDOFF.local.md` — gitignored
- В `~/srs-prod` руками `git pull` не делать: прод переводится на вершину `origin/main`
  только деплоем (`scripts/deploy_home.sh`), иначе код на диске и в контейнерах разъедутся

---

## 16. Parkrun (отдельно)

Сайт сам в parkrun.org.uk не ходит: дома `PARKRUN_SERVER_FETCH_ENABLED=false`
и `PARKRUN_SERVER_QUEUE_ENABLED=false` (`docker-compose.home-site.yml`). Всё, где
нужна живая страница parkrun («Обновить», привязка или предпросмотр незнакомого
профиля, discovery), ложится строкой в `profile_fetch_pending`. Человек видит
«Операция добавлена в очередь».

**Кто разбирает очередь.**

- **Домашний сервер — основной.** `pm-site-queue.timer` (через 20 мин после
  конца прошлого прогона, `LIMIT=50 DELAY=3` в юните) → `scripts/home_queue_run.sh`
  → `backend/scripts/parkrun_queue_daemon.py --no-browser --solve-captcha`.
  Страницы качает httpx через VPN-выходы 127.0.0.1:10851–10878: каждый выход
  перед прогоном проверяется запросом к parkrun, живых нет — прогон пропускается,
  с домашнего адреса не ходим. Капчу решает CLIP из `~/parkrun-monitoring`; если
  выход упёрся в защиту, берём следующий. Порядок: очередь S95, затем ручные
  «Обновить» parkrun, строки с `user_id`, сид RunPark, discovery без пользователя
  (`list_pending_rows`), ресинк привязок в `error`. Код —
  `~/saturday-runs-next` (догоняет прод `home_sync.sh`), база — домашняя
  (маркер `~/.srs-site-at-home`), Redis — свой `pm-queue-redis` на :6399.
  Логи: `journalctl -u pm-site-queue.service`,
  `~/saturday-runs-next/backend/data/parkrun_daemon.log`.
- **`make parkrun`** — ручной запасной путь тем же демоном (Chromium, капча
  глазами). Базу берёт туннелем `scripts/prod_db_env.sh` на
  `TEMP_SSH_HOST:5432` и маркер `~/.srs-site-at-home` не смотрит: без правки
  `.env` ходит в замороженную с 21.10.2026 базу VPS (только чтение — запись упадёт).
- **`parkrun_sync.process_pending_queue`** (beat `:07`/`:37`, только строки с
  `user_id`, пачка `PARKRUN_SERVER_QUEUE_BATCH_SIZE`=5, при капче стоп) — путь
  VPS, дома отвечает `skipped: disabled`. Его проверка «демон жив» смотрит
  `parkrun:local_worker:heartbeat`, а эту метку пишет только
  `make parkrun-local-worker`, не демон очереди.

**Охлаждение после капчи** (`app/parkrun/fetch/captcha_state.py`). Лестница
`PARKRUN_BAN_COOLDOWN_STEPS_SECONDS`: 1ч → 5ч → 24ч → 72ч → неделя, дальше
снова неделя. Ключи `parkrun:fetch:ban_cooldown_until` и
`parkrun:fetch:ban_level`; уровень живёт ступень + 7 дней. Гейт —
`_check_ban_cooldown` в `fetch/coordinator.py`, только на серверном пути. После
ступени фетч идёт пробой: успех (`clear_captcha_pending`) обнуляет лестницу,
капча поднимает её на ступень. Заявка, пришедшая во время охлаждения, уходит в
очередь со сроком из текста `until <ts>`.

- Дома лестница сайта спит: `_check_server_fetch_allowed` отказывает раньше
  гейта, без эскалации и без срока.
- У демона очереди своя лестница в своём Redis, и `run_daemon` сбрасывает её в
  начале каждого прогона (`prepare_parkrun_cdp_fetch`). Защита на всех выходах
  останавливает пачку до следующего прогона; мёртвые выходы лестницу не двигают.
- Поэтому блок parkrun на `/admin/queue` (капча, охлаждение, «воркер жив»;
  `GET /api/sync/queue`) читает Redis сайта
  и о домашней очереди не говорит ничего. Смотреть — логи выше.

Прежняя схема (ручной демон с Chromium) — история: [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md).

**Каталог площадок parkrun — в боевой базе** (этап 2, 18.10.2026): таблицы
`parkrun_events` (слаг как у parkrun, id площадки у parkrun, имена, страна,
серия, точка, действует ли, когда появилась и закрылась), `parkrun_event_names`
(все имена, включая старые: «Ludlow» → mortimerforest), `parkrun_countries`
(домен страны; у России и Франции домена нет), `parkrun_catalog_runs` и
`parkrun_event_changes` (прогоны и журнал изменений).

Описания трасс, история забегов и протоколы parkrun всего мира в базу сайта
НЕ переносятся (решение Дмитрия 21.10.2026): они живут в базе мирового обхода
`parkrun_world` на домашнем сервере.

- **Сверка** — задача `parkrun_catalog.sync` (beat 09:20 МСК, очередь `celery`,
  включается `PARKRUN_CATALOG_SYNC_ENABLED`): скачать events.json (дома — только
  через `PARKRUN_CATALOG_PROXY_URL`), не применять урезанный ответ, записать
  журнал, полечить parkrun-локации сайта (`services/parkrun_catalog_heal.py`:
  транслитерация → слаг parkrun, слияние двойников, пустые точка и страна, ссылка
  на домен страны, перенос старта) и прислать одно сообщение в Telegram
  (`services/parkrun_catalog_notify.py`). Без изменений — тишина, сбой — одна
  тревога и напоминание раз в неделю; не смог полечить — тоже раз в неделю.
  Сводка не дошла (Telegram и ВК лежат) — уйдёт со следующим прогоном
  (`notified_at` у прогона «ok»). Руками — `scripts/parkrun_catalog_run.py`
  (`--heal-preview`, `--from-file`, `--force`, `--heal-no-limit`).
- **Предохранители:** урезанный ответ не применяется (`check_feed`); план
  лечения больше `MAX_MERGES`/`MAX_RENAMES` не выполняется (смотреть
  `--heal-preview`, применять `--heal-no-limit`); блокировка прогона — на своём
  соединении в AUTOCOMMIT (сессионный advisory-lock на соединении сессии уезжал
  бы с ним в пул). После слияний — флаги первого старта, рекорды и кабинеты
  (`key_plan.finish_location_changes`, общий со скриптом этапа 3).
- **Что лечение пишет и чего нет** (проверка предпросмотра на копии прода
  21.10.2026): имя локации меняется только по имени с пометкой `old`; точка
  (0, 0) — не точка (`event_slugs.real_point`); площадка без точки всё равно
  получает ссылку и страну; страна — по географии (`COUNTRY_BY_SLUG`: военные
  базы, Намибия, Эсватини), затем по точке через Nominatim (вне транзакции,
  первый отказ — стоп), затем страна каталога; **город не ставит** (Nominatim
  отдаёт районы и «Большой Лондон», рейтинг городов посчитал бы их новыми);
  протоколы на чужом домене перепривязывает, даже если ссылка локации верная;
  каждая локация — своя точка сохранения. Предпросмотр учитывает
  переименования, которые сам откатывает. Сид: `--prune-names` — только до
  первой живой сверки.
- **Кто читает:** импорт профиля переспрашивает каталог в базе по названию площадки
  (`app/parkrun/catalog_store.py`), ссылки и домены — оттуда же; парсер профиля
  и пустая база (тесты, стенд) работают по снимкам JSON в `app/parkrun/data/`.
- **Один слаг на площадку — как у parkrun** (строчные `[a-z0-9]`, дефис только в
  `-juniors`): в `locations.external_key`, связках каталога локаций, ключе и ссылке
  протокола и ключе результата. Переименование и слияние двойников — только через
  `app/parkrun/location_keys.py`; план — `app/parkrun/key_plan.py`. 16.10.2026
  `scripts/unify_parkrun_location_keys.py` слил 56 пар двойников и переименовал 37
  локаций; дальше то же делает сверка сама.
- parkrun-monitoring каталог больше не ведёт: его cron на VPS и сводки в ВК
  выключаются после запуска сверки сайта. Сид каталога —
  `scripts/export_parkrun_catalog_bundle.py` (дома) → `scripts/seed_parkrun_catalog.py`.

---

## 17. Полезные скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/check_failed_sync_jobs.py` | failed sync_jobs за 7 дней |
| `scripts/recalculate_personal_records.py` | backfill PR |
| `scripts/recalculate_cross_platform_personal_records.py` | backfill глобальных рекордов и рекордов локации |
| `scripts/import_location_catalog.py` | catalog → DB |
| `scripts/backfill_location_descriptions.py` | первый сбор описаний площадок (5 вёрст, S95) |
| `scripts/deploy_home.sh` | деплой прода на домашнем сервере (§3) |
| `scripts/deploy_prod.sh` | деплой на VPS — после 21.10.2026 только для отката (`ALLOW_VPS_DEPLOY=1`) |
| `scripts/cutover_rollback.sh` | откат на VPS: `now`, с данными — `back-dump` (§3) |
| `scripts/dev_stack.sh` | стенд под worktree на домашнем сервере: `up` (`--own-db`, `--front`), `ls`, `url`, `rm` (§13) |
| `scripts/dev_prod_db.sh` | локальный сайт на БД VPS (read-only tunnel) — после переезда смотрит в замороженную базу; на сервере не стартует (в .env нет TEMP_SSH_HOST), прод читать через report_ro (§3) |
| `scripts/add_release.py` / `update_release.py` | записи релизов для «Обновлений» |
| `scripts/prune_server_disk.sh` | чистка диска на сервере (образы без `-a`, кэш сборки, journald) |
| `make parkrun` | ручной демон очереди parkrun — запасной путь; база туннелем `prod_db_env.sh` на `TEMP_SSH_HOST` (с 21.10.2026 — замороженная база VPS). Дома — `scripts/home_queue_run.sh` по `pm-site-queue.timer` (§16) |
| `backend/scripts/archive/` | отработавшие бэкфилы — не запускать, см. README там |

---

## 18. Контакт

Автор: **Дмитрий ПОПОВ** (@Popov_Dmitry, popov.dmitii@yandex.ru).  
При блокерах в Claude Code — пользователь может вернуться в Cursor с вопросами.
