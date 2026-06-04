#!/usr/bin/env bash
# Run on Mac in a dedicated terminal; admin UI button queues jobs via Redis.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="$ROOT/.conda-parkrun"

if ! command -v conda >/dev/null 2>&1; then
  echo "Нужен conda."
  exit 1
fi
if [[ ! -x "$CONDA_ENV/bin/python" ]]; then
  echo "Создаём conda env…"
  conda create -y -p "$CONDA_ENV" python=3.12 pip
fi
if ! "$CONDA_ENV/bin/python" -c "import playwright, sqlalchemy, psycopg" 2>/dev/null; then
  echo "Устанавливаем зависимости backend…"
  "$CONDA_ENV/bin/pip" install -e "$ROOT/backend"
  "$CONDA_ENV/bin/playwright" install chromium
fi

export PYTHONPATH="$ROOT/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="$ROOT/data/parkrun_playwright_state.json"
export PARKRUN_USE_CDP_FOR_FETCH=true
export PARKRUN_CDP_URL="${PARKRUN_CDP_URL:-http://127.0.0.1:9222}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://saturday_runs:saturday_runs@127.0.0.1:${POSTGRES_PUBLISH_PORT:-5433}/saturday_runs_lk}"

echo "Воркер parkrun (CDP $PARKRUN_CDP_URL). Chrome с отладкой должен быть открыт."
exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/parkrun_local_worker.py"
