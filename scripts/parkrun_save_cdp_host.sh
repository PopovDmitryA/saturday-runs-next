#!/usr/bin/env bash
# Save parkrun cookies from Chrome CDP on Mac (127.0.0.1:9222).
# Docker --network host is not available on Docker Desktop for Mac.
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
  if ! "$CONDA_ENV/bin/python" -c "import playwright, redis" 2>/dev/null; then
    echo "Installing dependencies in conda env…"
    "$CONDA_ENV/bin/pip" install playwright pydantic pydantic-settings redis
    "$CONDA_ENV/bin/playwright" install chromium
  fi
  RUN_PY="$CONDA_ENV/bin/python"
}

setup_conda || {
  echo "Нужен conda (Anaconda/Miniconda). Или: brew install miniconda"
  exit 1
}

mkdir -p data

export PYTHONPATH="$ROOT/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="$ROOT/data/parkrun_playwright_state.json"
export PARKRUN_BASE_URL="${PARKRUN_BASE_URL:-https://www.parkrun.org.uk}"
# Redis published on host (docker compose redis 6379:6379)
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

echo "Saving session from Chrome at http://127.0.0.1:9222 …"
echo "Using: $RUN_PY"

"$RUN_PY" "$ROOT/backend/scripts/parkrun_save_cdp_session.py" --cdp-url http://127.0.0.1:9222

echo ""
echo "Готово: $ROOT/data/parkrun_playwright_state.json"
echo "В админке /admin/parkrun нажмите «Обновить» — должно быть «Файл сессии — есть»."
