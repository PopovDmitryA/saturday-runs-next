#!/usr/bin/env bash
# One-time: open parkrun in a visible browser, pass captcha, save cookies for Docker.
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
  if ! "$CONDA_ENV/bin/python" -c "import playwright" 2>/dev/null; then
    echo "Installing playwright in conda env…"
    "$CONDA_ENV/bin/pip" install playwright pydantic pydantic-settings
    "$CONDA_ENV/bin/playwright" install chromium
  fi
  RUN_PY="$CONDA_ENV/bin/python"
}

setup_conda || {
  echo "Нужен conda (Anaconda/Miniconda) или исправьте brew python@3.12."
  echo "  conda create -y -p $CONDA_ENV python=3.12 pip"
  exit 1
}

echo "Using: $RUN_PY"
mkdir -p data

export PARKRUN_PLAYWRIGHT_HEADLESS=false
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="$ROOT/data/parkrun_playwright_state.json"
export PARKRUN_BASE_URL=https://www.parkrun.org.uk

"$RUN_PY" "$ROOT/backend/scripts/parkrun_save_browser_state.py"

echo ""
echo "Готово. Cookies: data/parkrun_playwright_state.json"
echo "Дальше выполните:"
echo "  cd $ROOT && docker compose restart api"
echo "  cd $ROOT && docker compose exec api python scripts/process_pending_profile_fetches.py --platform parkrun --limit 5"
