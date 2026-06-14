# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Полная архитектурная документация — **[AGENTS.md](AGENTS.md)**.  
Credentials, SSH, prod нюансы — **[PROJECT_HANDOFF.local.md](PROJECT_HANDOFF.local.md)** (gitignored).

---

## Стек

Python 3.12 · FastAPI · SQLAlchemy · Alembic · Celery · Redis · PostgreSQL 16 · React 19 · Vite · Nginx · Docker Compose.

---

## Команды разработки

```bash
# Поднять всё локально
docker compose up --build

# Миграции
docker compose exec api alembic upgrade head

# Сборка фронтенда (через Docker node, без локального node)
make frontend-build
docker compose restart nginx

# Тесты и линт
docker compose run --rm api pytest
cd backend && ruff check app tests && mypy app
cd frontend && npm run lint

# Короткие алиасы
make up / make down / make migrate / make test / make lint
```

### Локальный сайт на prod БД (read-only)

```bash
make dev-prod-db   # SSH-туннель + локальный Docker на prod PG
```

### Деплой на prod

```bash
bash scripts/deploy_prod.sh
# или интерактивно:
make sync
```

---

## Локальные URL

| URL | Назначение |
|-----|------------|
| http://localhost:8080 | сайт |
| http://localhost/login | VK OAuth (нужен `docker-compose.dev.yml`, порт 80) |
| localhost:5433 | Postgres (DBeaver) |

---

## Архитектура: ключевые точки

### Celery-очереди

Два отдельных воркера с `concurrency=1`:
- `worker-five-verst` → очереди `five_verst_user`, `five_verst`
- `worker-s95` → очереди `s95_user`, `s95`
- `worker-parkrun` → очередь `parkrun`

User-sync (очереди `*_user`) приоритетнее batch. Для S95 реализован cooperative yield: batch прерывается при появлении user-задачи (`app/s95/fetch/priority.py`, `coordinator.py`).

### S95: блокировка IP

Prod IP `195.58.34.112` может получить HTTP 403 от s95.ru. После 403 → cooldown 1 час в Redis (`s95:fetch:ban_cooldown_until`). Диагностика:

```bash
# Проверить cooldown
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T redis redis-cli GET s95:fetch:ban_cooldown_until
# Полные тексты ошибок sync_jobs
docker compose exec -T api python scripts/check_failed_sync_jobs.py
```

Код: `app/s95/ban.py`, `fetch/browser.py`, `fetch/coordinator.py`, `app/s95/messages.py`.

### Personal Records (PR)

Два уровня: PR на платформе (`is_pr`) и Global PR (лучшее время среди всех платформ). Backfill:

```bash
docker compose exec api python scripts/recalculate_personal_records.py --platform all
```

Код: `personal_record_service.py`, `dashboard_service.py`, `GlobalPrFinishTime.tsx`.

### Location catalog (parkrun)

`data/location_catalog.json` → таблицы `location_catalog`, `location_catalog_links`. Slug-несоответствия нормализуются через `normalize_location_slug()` в `location_catalog_service.py`.

```bash
make location-catalog-import-docker
```

### Dashboard cache

Поле `ANALYTICS_VERSION` — инкрементировать при изменении структуры аналитических данных (`dashboard_cache`, `dashboard_service.py`).

### Parkrun (только с Mac)

Prod-сервер **не** ходит на parkrun.org.uk. Очередь `profile_fetch_pending` обрабатывается только с Mac через Chromium:

```bash
make parkrun              # daemon
make parkrun-seed-queue   # заполнить 5 профилей из legacy
```

---

## Prod-сервер

| Параметр | Значение |
|----------|----------|
| IP | `195.58.34.112` |
| SSH user | `viewer` |
| Путь | `/opt/saturday-runs-next` |
| Compose | `docker compose -f docker-compose.yml -f docker-compose.prod.yml` |
| БД | Host PostgreSQL (не в Docker) |
| Nginx | два слоя: host nginx (TLS) → docker nginx :8080 |

Диагностика на prod:

```bash
$COMPOSE logs api --tail 100
$COMPOSE logs worker-s95 --since 1h
$COMPOSE exec -T api python -c "from app.db.session import SessionLocal; ..."
```

После деплоя API 502 → `docker compose logs api` (чаще всего SyntaxError/IndentationError в Python).

---

## Host nginx на prod

Passwordless sudo для `viewer` **не настроен**. `deploy_prod.sh` пробует `sudo -n` и при ошибке пишет WARN и продолжает — docker nginx (:8080) перезапустится, но host nginx (TLS, редиректы Grafana) — нет. Шаги вручную (SSH на сервер):

```bash
sudo cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run
sudo nginx -t && sudo systemctl reload nginx
```

**Важно:** live `/etc/nginx/sites-available/run5k.run` может отличаться от файла в репо — перед `cp` делать `diff`.

## git pull на prod

Prod на `474518e` — отстаёт от `origin/main` на 18 коммитов (на 14.06.2026). ~70 файлов изменены rsync без commit. Перед `git pull` на сервере:

```bash
git status -sb
git diff --stat HEAD origin/main
# stash rsync-путей если нужно сохранить
git stash push -m 'pre-pull prod drift' -- backend/app backend/vk_bot deploy docker-compose.yml docker-compose.prod.yml frontend/src
```

Основной workflow деплоя — `bash scripts/deploy_prod.sh` с Mac (rsync + remote build). `git pull` на prod только осознанно.

Junk на сервере (не коммитить, не трогать): `AboutPage.tsx` в корне, `backend/dashboard_service.py` (дубль), `data/migration_*.log`.

## Parkrun cookies

`data/parkrun_playwright_state.json` — `aws-waf-token` протух ~6 дней назад (14.06.2026). Перед запуском parkrun fetch нужно обновить сессию:

```bash
bash scripts/parkrun_save_session_mac.sh
# или
make parkrun  # запустит обновление автоматически
```

## Открытые задачи (июнь 2026)

1. **S95 IP whitelist** — sync S95 на prod не работает, ожидается разблокировка `195.58.34.112`
2. **Ryazan Central** — английское название parkrun, нет аналога в 5v/S95, оставляем как есть
3. **Parkrun cookies** — `aws-waf-token` протух, нужно обновить сессию на Mac

---

## Git и prod

- Коммиты только по явной просьбе пользователя
- `git pull` на prod — осторожно: часть правок заливалась rsync без commit; сверять `git diff` перед pull
- Prod `.env` только на сервере, локальный `.env` и `PROJECT_HANDOFF.local.md` — gitignored
