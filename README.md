# Saturday Runs

Единый личный кабинет участника субботних парковых пробежек и global data core.

Идея проекта — собрать статистику из 5 вёрст, С95 и parkrun в одном месте и тем самым снять барьер у участников, которым непросто решиться начать вести учёт в новой беговой системе: когда путь уже виден целиком, первая пробежка перестаёт казаться «с нуля».

## Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Redis sessions
- **Bot:** aiogram 3 (отдельный контейнер, auth-only)
- **Frontend:** React 19, Vite, TypeScript
- **Infra:** PostgreSQL 16, Redis 7, Nginx, Docker Compose

## Quick start

```bash
cp .env.example .env
# Заполните TELEGRAM_BOT_TOKEN, TELEGRAM_BOT_USERNAME, TELEGRAM_BOT_INTERNAL_SECRET

cd frontend && npm install && npm run build && cd ..
docker compose up --build
docker compose exec api alembic upgrade head
```

- Web: http://localhost:8080  
- API health: http://localhost:8000/health  
- Auth API: `POST /api/auth/login-request`, `GET /api/auth/me`
- Dashboard API: `GET /api/dashboard`, `GET /api/runs`, `GET /api/volunteering`
- Sync API: `GET /api/sync/status`, `POST /api/sync/refresh` (не чаще раза в 30 минут на пользователя)
- PostgreSQL (DBeaver, с Mac): `localhost:5433`, user/db `saturday_runs`, database `saturday_runs_lk`

## Способы входа

Одна сессия на все способы: cookie `sr_session`, 30 суток скользящих — каждый
запрос продлевает срок и в Redis, и в куке (`session_ttl_seconds`).

1. **Telegram через бота** — `POST /api/auth/login-request` → deep link
   `t.me/<бот>?start=login_{token}` → бот показывает, откуда вход, и после
   «Подтвердить» вкладка сайта сама забирает сессию
   (`POST /api/auth/login-request/{token}/claim`); страховка — magic link в боте
   (5 мин, одноразовый).
2. **Telegram Login Widget** — запасной путь, когда бот молчит
   (`GET /api/auth/telegram/config` → `bot_login=false`); подпись виджета
   проверяется на сервере.
3. **Код на почту** — `POST /api/auth/email/request-code` → `POST /api/auth/email/verify`
   (шестизначный код, 10 минут, 5 попыток).
4. **VK ID / Яндекс ID** — `GET /api/auth/oauth/{provider}/start` → callback.

Подробности (журнал входов, лимиты, объединение профилей) — в [AGENTS.md](AGENTS.md).

## Parkrun (Mac)

Живые запросы к parkrun.org.uk не идут из API: нет данных в БД → очередь `profile_fetch_pending` → **`make parkrun`** на Mac (Chromium + капча). Есть данные в БД → предпросмотр в ЛК с датой обновления. Подробнее: [docs/parkrun_pipeline.md](docs/parkrun_pipeline.md).

## Development

```bash
# Backend
cd backend && pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload

# Bot (отдельный процесс)
python -m bot_app.main

# Frontend
cd frontend && npm run dev
```

## Agent / maintainer docs

**[AGENTS.md](AGENTS.md)** — единый справочник для AI-агентов: prod, Celery beat, VK-бот, dedup sync, user vs bulk sync, deploy, типичные задачи.

Планы по платформам: [docs/five_verst_sync_plan.md](docs/five_verst_sync_plan.md), [docs/s95_sync_plan.md](docs/s95_sync_plan.md); релизы — [docs/release_management.md](docs/release_management.md). Выполненный план переезда с легаси лежит в [docs/archive/](docs/archive/).

Домены (run5k.run, app.run5k.run, закрытая grafana.run5k.run): [deploy/DOMAINS.md](deploy/DOMAINS.md).
