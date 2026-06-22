#!/usr/bin/env bash
# Point local Docker API at prod PostgreSQL (SSH tunnel on host).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/prod_db_env.sh"

start_prod_db_tunnel

export PROD_DB_USER="${TEMP_PROD_PG_USER:-${PROD_PG_USER:-saturday_runs}}"
export PROD_DB_PASSWORD="${TEMP_PROD_PG_PASSWORD:-${PROD_PG_PASSWORD:-}}"
export PROD_DB_DATABASE="${TEMP_PROD_PG_DATABASE:-${PROD_PG_DATABASE:-saturday_runs_lk}}"
export PROD_TUNNEL_LOCAL_PORT="${PROD_TUNNEL_LOCAL_PORT:-5434}"

if [[ -z "${PROD_DB_PASSWORD}" ]]; then
  echo "dev_prod_db: задайте TEMP_PROD_PG_PASSWORD или PROD_PG_PASSWORD в .env" >&2
  exit 1
fi

cd "${ROOT}"
docker compose -f docker-compose.yml -f docker-compose.prod-db.yml up -d --force-recreate api

echo "" >&2
echo "✓ API → prod DB (read-only): host.docker.internal:${PROD_TUNNEL_LOCAL_PORT}/${PROD_DB_DATABASE}" >&2
echo "  Site: http://localhost:8080" >&2
echo "  Rating: http://localhost:8080/insights/rejting-kolichestva-probezhek" >&2
echo "  Back to local DB: make dev-local-db" >&2
