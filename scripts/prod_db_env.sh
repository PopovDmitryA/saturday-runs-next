#!/usr/bin/env bash
# Source prod DATABASE_URL for local script runs (SSH tunnel to server Postgres).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

PROD_TUNNEL_LOCAL_PORT="${PROD_TUNNEL_LOCAL_PORT:-5434}"
PROD_TUNNEL_REMOTE_HOST="${PROD_TUNNEL_REMOTE_HOST:-127.0.0.1}"
PROD_TUNNEL_REMOTE_PORT="${PROD_TUNNEL_REMOTE_PORT:-5432}"

SSH_HOST="${TEMP_SSH_HOST:-${PROD_SSH_HOST:-}}"
SSH_USER="${TEMP_SSH_USER:-${PROD_SSH_USER:-viewer}}"

PG_USER="${TEMP_PROD_PG_USER:-${PROD_PG_USER:-saturday_runs}}"
PG_PASSWORD="${TEMP_PROD_PG_PASSWORD:-${PROD_PG_PASSWORD:-}}"
PG_DATABASE="${TEMP_PROD_PG_DATABASE:-${PROD_PG_DATABASE:-saturday_runs_lk}}"

if [[ -z "${PG_PASSWORD}" ]]; then
  echo "prod_db_env: задайте TEMP_PROD_PG_PASSWORD или PROD_PG_PASSWORD в .env" >&2
  exit 1
fi

if [[ -z "${SSH_HOST}" ]]; then
  echo "prod_db_env: задайте TEMP_SSH_HOST или PROD_SSH_HOST в .env" >&2
  exit 1
fi

_tunnel_running() {
  lsof -iTCP:"${PROD_TUNNEL_LOCAL_PORT}" -sTCP:LISTEN >/dev/null 2>&1
}

start_prod_db_tunnel() {
  if _tunnel_running; then
    echo "prod-db tunnel: already listening on 127.0.0.1:${PROD_TUNNEL_LOCAL_PORT}" >&2
    return 0
  fi

  echo "prod-db tunnel: ${SSH_USER}@${SSH_HOST} → 127.0.0.1:${PROD_TUNNEL_LOCAL_PORT}" >&2

  # Keepalive: длинные прогоны (parkrun daemon, ~1ч+) простаивают по 25-53s между
  # задачами — без этого сервер/NAT рвёт idle SSH-сессию и туннель отваливается
  # посреди работы ("Connection refused" на 5434). ExitOnForwardFailure — не давать
  # SSH висеть в фоне, если порт занять не удалось.
  local ka_opts=(
    -o ServerAliveInterval=15
    -o ServerAliveCountMax=4
    -o ExitOnForwardFailure=yes
    -o TCPKeepAlive=yes
  )

  if [[ -n "${TEMP_SSH_PASSWORD:-}" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="${TEMP_SSH_PASSWORD}" sshpass -e ssh -f -N \
      -o StrictHostKeyChecking=no \
      "${ka_opts[@]}" \
      -L "${PROD_TUNNEL_LOCAL_PORT}:${PROD_TUNNEL_REMOTE_HOST}:${PROD_TUNNEL_REMOTE_PORT}" \
      "${SSH_USER}@${SSH_HOST}"
  else
    ssh -f -N \
      "${ka_opts[@]}" \
      -L "${PROD_TUNNEL_LOCAL_PORT}:${PROD_TUNNEL_REMOTE_HOST}:${PROD_TUNNEL_REMOTE_PORT}" \
      "${SSH_USER}@${SSH_HOST}"
  fi

  sleep 0.5
  if ! _tunnel_running; then
    echo "prod_db_env: tunnel failed to start" >&2
    exit 1
  fi
}

stop_prod_db_tunnel() {
  pids="$(lsof -t -iTCP:"${PROD_TUNNEL_LOCAL_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    kill ${pids} 2>/dev/null || true
    echo "prod-db tunnel: stopped" >&2
  fi
}

export_prod_database_url() {
  start_prod_db_tunnel
  export DATABASE_URL="postgresql+psycopg://${PG_USER}:${PG_PASSWORD}@127.0.0.1:${PROD_TUNNEL_LOCAL_PORT}/${PG_DATABASE}"
  export PROD_DB_TARGET=1
  echo "prod-db DATABASE_URL → 127.0.0.1:${PROD_TUNNEL_LOCAL_PORT}/${PG_DATABASE}" >&2
}

# Local Redis for 5verst rate-limit (не prod Redis — чтобы не мешать воркерам на сервере)
export_local_redis_url() {
  export REDIS_URL="${LOCAL_REDIS_URL:-redis://127.0.0.1:6379/0}"
}

warn_prod_db_locks() {
  # shellcheck disable=SC1091
  source "${ROOT}/scripts/local_python.sh"
  "${LOCAL_PYTHON}" - <<'PY' 2>/dev/null || true
import os
from sqlalchemy import create_engine, text

url = os.environ.get("DATABASE_URL")
if not url:
    raise SystemExit(0)
engine = create_engine(url, connect_args={"connect_timeout": 5})
with engine.connect() as conn:
    waiting = conn.execute(
        text(
            "SELECT count(*) FROM pg_locks WHERE NOT granted AND pid <> pg_backend_pid()"
        )
    ).scalar()
    idle_tx = conn.execute(
        text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE state = 'idle in transaction' AND pid <> pg_backend_pid()"
        )
    ).scalar()
    if waiting or idle_tx:
        print(
            f"prod-db warning: waiting_locks={waiting} idle_in_transaction={idle_tx} "
            "(часто worker-five-verst на сервере; локальный скрипт может ждать до 30s)",
            flush=True,
        )
PY
}
