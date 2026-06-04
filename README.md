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
- Sync API: `GET /api/sync/status`, `POST /api/sync/refresh` (1/min rate limit)
- PostgreSQL (DBeaver, с Mac): `localhost:5433`, user/db `saturday_runs`, database `saturday_runs_lk`

## Auth flow (Phase 1)

1. Пользователь нажимает «Войти через Telegram» → `POST /api/auth/login-request`
2. Открывается новый бот (`/start login_{token}`)
3. Бот вызывает `POST /api/auth/bot/confirm` и отправляет magic link (5 мин, one-time)
4. Пользователь переходит по ссылке → cookie `sr_session` (72ч) → `/dashboard`

Legacy Telegram-бот **не используется**.

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

## Project phases

See [architecture plan](.cursor/plans/lk_architecture_plan_f0bd90e1.plan.md).

- **Phase 0:** foundation  
- **Phase 1:** Telegram auth + new bot  
- **Phase 2:** 5verst adapter + profile linking  
- **Phase 3:** Global sync (5verst)  
- **Phase 5:** User sync + Dashboard API
