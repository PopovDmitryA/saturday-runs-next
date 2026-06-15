#!/usr/bin/env bash
# Run the parkrun+s95 Mac daemon against prod DB and Redis via SSH tunnels.
# Tunnels open on start, close on exit (Ctrl+C or error).
#
# Usage:
#   make parkrun         (calls this script)
#   PENDING_ONLY=1 make parkrun   (skip scheduled parkrun syncs)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT}/scripts/prod_db_env.sh"

REDIS_TUNNEL_PORT="${PROD_REDIS_TUNNEL_PORT:-6379}"
REDIS_REMOTE_HOST="${PROD_REDIS_REMOTE_HOST:-127.0.0.1}"
REDIS_REMOTE_PORT="${PROD_REDIS_REMOTE_PORT:-6379}"

_redis_tunnel_running() {
  lsof -iTCP:"${REDIS_TUNNEL_PORT}" -sTCP:LISTEN >/dev/null 2>&1
}

start_redis_tunnel() {
  if _redis_tunnel_running; then
    echo "prod-redis tunnel: already listening on 127.0.0.1:${REDIS_TUNNEL_PORT}" >&2
    return 0
  fi

  echo "prod-redis tunnel: ${SSH_USER}@${SSH_HOST} → 127.0.0.1:${REDIS_TUNNEL_PORT}" >&2

  if [[ -n "${TEMP_SSH_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="${TEMP_SSH_PASSWORD}" sshpass -e ssh -f -N \
      -o StrictHostKeyChecking=no \
      -L "${REDIS_TUNNEL_PORT}:${REDIS_REMOTE_HOST}:${REDIS_REMOTE_PORT}" \
      "${SSH_USER}@${SSH_HOST}"
  else
    ssh -f -N \
      -L "${REDIS_TUNNEL_PORT}:${REDIS_REMOTE_HOST}:${REDIS_REMOTE_PORT}" \
      "${SSH_USER}@${SSH_HOST}"
  fi

  sleep 0.5
  if ! _redis_tunnel_running; then
    echo "parkrun_mac: redis tunnel failed to start" >&2
    exit 1
  fi
}

stop_redis_tunnel() {
  pids="$(lsof -t -iTCP:"${REDIS_TUNNEL_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    kill ${pids} 2>/dev/null || true
    echo "prod-redis tunnel: stopped" >&2
  fi
}

cleanup() {
  stop_prod_db_tunnel
  stop_redis_tunnel
}
trap cleanup EXIT

# Open both tunnels
export_prod_database_url
start_redis_tunnel

export REDIS_URL="redis://127.0.0.1:${REDIS_TUNNEL_PORT}/0"
export PYTHONPATH="${ROOT}/backend"
export PARKRUN_PLAYWRIGHT_STORAGE_STATE_PATH="${ROOT}/data/parkrun_playwright_state.json"
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
echo " Redis: redis://127.0.0.1:${REDIS_TUNNEL_PORT}/0  (prod, туннель)"
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

exec "${CONDA_ENV}/bin/python" "${ROOT}/backend/scripts/parkrun_queue_daemon.py" "${ARGS[@]}"
