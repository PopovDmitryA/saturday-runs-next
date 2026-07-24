#!/usr/bin/env bash
# Run the parkrun+s95 Mac daemon against prod DB and Redis via SSH tunnels.
# Tunnels open on start, close on exit (Ctrl+C or error).
#
# Usage:
#   make parkrun         (calls this script)
#   PENDING_ONLY=1 make parkrun   (skip scheduled parkrun syncs)
#   QUIET=1 make parkrun          (hide DB/HTTP/page-load noise, keep only
#                                   the N/total progress line and summary)
#   NO_BROWSER=1 LIMIT=40 make parkrun   (EXPERIMENTAL: plain httpx, no
#                                          Chromium, no captcha window — real
#                                          ban risk, keep --limit small;
#                                          FAST_DELAY=N tunes the pace, jittered
#                                          ±30%, applies both profile<->profile
#                                          and profile<->location gaps)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/scripts/prod_db_env.sh"

LOCAL_REDIS_PORT="${LOCAL_REDIS_PORT:-6399}"

_local_redis_running() {
  lsof -iTCP:"${LOCAL_REDIS_PORT}" -sTCP:LISTEN >/dev/null 2>&1
}

start_local_redis() {
  if _local_redis_running; then
    echo "local redis: already running on 127.0.0.1:${LOCAL_REDIS_PORT}" >&2
    return 0
  fi
  echo "local redis: starting on 127.0.0.1:${LOCAL_REDIS_PORT}…" >&2
  docker run -d --rm --name parkrun-mac-redis \
    -p "127.0.0.1:${LOCAL_REDIS_PORT}:6379" \
    redis:7-alpine --save "" --appendonly no >/dev/null
  sleep 1
  if ! _local_redis_running; then
    echo "parkrun_mac: local redis failed to start" >&2
    exit 1
  fi
}

stop_local_redis() {
  docker stop parkrun-mac-redis 2>/dev/null || true
}

cleanup() {
  stop_prod_db_tunnel
  stop_local_redis
}
trap cleanup EXIT

# Open DB tunnel + local Redis
export_prod_database_url
start_local_redis

export REDIS_URL="redis://127.0.0.1:${LOCAL_REDIS_PORT}/0"
# Демон держит транзакцию открытой во время 60-90s браузерного фетча — отключаем
# idle_in_transaction_session_timeout, иначе Postgres рвёт коннект на записи
# результата (см. app/db/session.py).
export DB_DISABLE_IDLE_TX_TIMEOUT=1
export PYTHONPATH="${ROOT}/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="${ROOT}/data/parkrun_playwright_state.json"
# Полный лог прогона (трейсбэки, библиотечный шум) — рядом со скриптом в data/.
# В терминал идут только справочные строки; сюда — всё.
export PARKRUN_DAEMON_LOG="${ROOT}/data/parkrun_daemon.log"
# В режиме --no-browser задержку между ДВУМЯ страницами профиля (summary и /all)
# тоже тянем к FAST_DELAY — иначе она осталась бы дефолтными 10с и обнуляла смысл
# ускорения. В браузерном режиме между-страничная пауза остаётся штатной.
if [[ "${NO_BROWSER:-}" == "1" ]]; then
  export PARKRUN_FETCH_BETWEEN_PAGES_DELAY_SECONDS="${FAST_DELAY:-3}"
fi
# Соседний проект parkrun-monitoring: демон чередует очередь сайта с его
# задачами (статистика стран + eventhistory-саммари) и пушит их на сервер.
export PARKRUN_MONITORING_DIR="${PARKRUN_MONITORING_DIR:-$HOME/Projects/parkrun-monitoring}"
export PARKRUN_PLAYWRIGHT_HEADLESS=false
export PARKRUN_USE_CDP_FOR_FETCH="${PARKRUN_USE_CDP_FOR_FETCH:-false}"

CONDA_ENV="${ROOT}/.conda-parkrun"
if [[ ! -x "${CONDA_ENV}/bin/python" ]]; then
  echo "conda env not found: run 'make parkrun' once to set it up, or check scripts/parkrun_daemon.sh" >&2
  exit 1
fi
if ! "${CONDA_ENV}/bin/python" -c "import playwright, sqlalchemy, psycopg" 2>/dev/null; then
  echo "Installing backend dependencies in conda env…"
  "${CONDA_ENV}/bin/pip" install -e "${ROOT}/backend"
  "${CONDA_ENV}/bin/playwright" install chromium
fi

mkdir -p "${ROOT}/data"

echo "════════════════════════════════════════════════════════════"
echo " Parkrun + S95: очередь из prod БД → Chromium (Playwright)"
echo " DB:    ${DATABASE_URL%@*}@…  (prod)"
echo " Redis: redis://127.0.0.1:${LOCAL_REDIS_PORT}/0  (локальный, только для cooldown)"
echo " Сессия: ${PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH}"
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
if [[ "${QUIET:-}" == "1" ]]; then
  ARGS+=(--quiet)
fi
if [[ "${NO_BROWSER:-}" == "1" ]]; then
  ARGS+=(--no-browser)
fi
if [[ -n "${FAST_DELAY:-}" ]]; then
  ARGS+=(--fast-delay "$FAST_DELAY")
fi

exec "${CONDA_ENV}/bin/python" "${ROOT}/backend/scripts/parkrun_queue_daemon.py" ${ARGS[@]+"${ARGS[@]}"}
