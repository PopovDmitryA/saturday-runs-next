#!/usr/bin/env bash
# Process parkrun profile_fetch_pending via your Chrome (CDP), not headless Docker.
# Requires: debug Chrome running, parkrun open without captcha, docker postgres+redis up.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="$ROOT/.conda-parkrun"
RUN_PY=""

setup_conda() {
  if ! command -v conda >/dev/null 2>&1; then
    return 1
  fi
  if [[ ! -x "$CONDA_ENV/bin/python" ]]; then
    echo "Creating conda env (Python 3.12)…"
    conda create -y -p "$CONDA_ENV" python=3.12 pip
  fi
  if ! "$CONDA_ENV/bin/python" -c "import playwright, sqlalchemy, psycopg" 2>/dev/null; then
    echo "Installing backend deps in conda env (one time)…"
    "$CONDA_ENV/bin/pip" install -e "$ROOT/backend"
    "$CONDA_ENV/bin/playwright" install chromium
  fi
  RUN_PY="$CONDA_ENV/bin/python"
}

setup_conda || {
  echo "Нужен conda."
  exit 1
}

mkdir -p data

export PYTHONPATH="$ROOT/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="$ROOT/data/parkrun_playwright_state.json"
export PARKRUN_USE_CDP_FOR_FETCH=true
export PARKRUN_CDP_URL="${PARKRUN_CDP_URL:-http://127.0.0.1:9222}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://saturday_runs:saturday_runs@127.0.0.1:${POSTGRES_PUBLISH_PORT:-5433}/saturday_runs_lk}"

echo "Processing parkrun pending queue via Chrome CDP ($PARKRUN_CDP_URL)…"
echo "Using: $RUN_PY"

if ! "$RUN_PY" "$ROOT/backend/scripts/process_pending_profile_fetches.py" \
  --platform parkrun \
  --clear-cooldown \
  --limit "${LIMIT:-5}"; then
  echo "Завершено с ошибками (см. cooldown/error выше)." >&2
  exit 1
fi

echo ""
echo "Готово. Проверьте привязку parkrun в настройках ЛК."
