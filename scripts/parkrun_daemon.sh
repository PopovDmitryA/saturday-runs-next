#!/usr/bin/env bash
# Единая точка входа: очередь parkrun из БД + Chrome + ожидание капчи.
# Запускайте раз в день:  make parkrun
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="$ROOT/.conda-parkrun"

if ! command -v conda >/dev/null 2>&1; then
  echo "Нужен conda."
  exit 1
fi
if [[ ! -x "$CONDA_ENV/bin/python" ]]; then
  echo "Создаём conda env (один раз)…"
  conda create -y -p "$CONDA_ENV" python=3.12 pip
fi
if ! "$CONDA_ENV/bin/python" -c "import playwright, sqlalchemy, psycopg" 2>/dev/null; then
  echo "Устанавливаем зависимости backend…"
  "$CONDA_ENV/bin/pip" install -e "$ROOT/backend"
  "$CONDA_ENV/bin/playwright" install chromium
fi

mkdir -p data

export PYTHONPATH="$ROOT/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="$ROOT/data/parkrun_playwright_state.json"
export PARKRUN_PLAYWRIGHT_HEADLESS=false
export PARKRUN_USE_CDP_FOR_FETCH="${PARKRUN_USE_CDP_FOR_FETCH:-false}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://saturday_runs:saturday_runs@127.0.0.1:${POSTGRES_PUBLISH_PORT:-5433}/saturday_runs_lk}"

echo "════════════════════════════════════════════════════════════"
echo " Parkrun: очередь из БД → Chromium (Playwright) → капча в окне"
echo " Сессия: $PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH"
echo " Ctrl+C — остановить"
echo "════════════════════════════════════════════════════════════"

ARGS=()
if [[ "${PARKRUN_USE_CDP_FOR_FETCH:-}" == "true" || "${PARKRUN_USE_CDP_FOR_FETCH:-}" == "1" ]]; then
  ARGS+=(--use-cdp)
  export PARKRUN_CDP_URL="${PARKRUN_CDP_URL:-http://127.0.0.1:9222}"
fi
if [[ "${NO_LAUNCH_CHROME:-}" == "1" ]]; then
  ARGS+=(--no-launch-chrome)
fi
if [[ -n "${LIMIT:-}" ]]; then
  ARGS+=(--limit "$LIMIT")
fi
if [[ "${PENDING_ONLY:-}" == "1" ]]; then
  ARGS+=(--pending-only)
fi

if ((${#ARGS[@]} > 0)); then
  exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/parkrun_queue_daemon.py" "${ARGS[@]}"
else
  exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/parkrun_queue_daemon.py"
fi
