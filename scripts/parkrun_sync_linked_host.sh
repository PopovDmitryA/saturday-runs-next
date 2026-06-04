#!/usr/bin/env bash
# Import parkrun runs for already-linked profiles via Chrome CDP (Mac).
# Example: make parkrun-sync-linked-host ATHLETE_ID=3197430
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

ARGS=()
if [[ -n "${ATHLETE_ID:-}" ]]; then
  ARGS+=(--athlete-id "$ATHLETE_ID")
fi
if [[ -n "${USER_ID:-}" ]]; then
  ARGS+=(--user-id "$USER_ID")
fi
if [[ "${ALL:-}" == "1" ]]; then
  ARGS+=(--all)
fi

echo "Синхронизация пробежек parkrun через Chrome CDP ($PARKRUN_CDP_URL)…"
echo "(сбрасываем cooldown/captcha в Redis — после неудачных запросов из Docker)"
echo "Перед запуском: в ЭТОМ Chrome откройте страницу parkrunner (после капчи), не закрывайте окно."
if ((${#ARGS[@]} > 0)); then
  exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/parkrun_sync_linked_host.py" "${ARGS[@]}"
else
  exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/parkrun_sync_linked_host.py"
fi
