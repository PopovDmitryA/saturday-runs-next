#!/usr/bin/env bash
# Run a backend script locally against prod PostgreSQL (SSH tunnel).
#
# Examples:
#   ./scripts/run_prod_script.sh scripts/five_verst_sync_latest.py --dry-run -v --pretty
#   CONFIRM_PROD=1 ./scripts/run_prod_script.sh scripts/five_verst_sync_location.py --slug bitsa -v
#   make prod-run ARGS="scripts/s95_sync_latest.py --dry-run -v"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/prod_db_env.sh"
# shellcheck disable=SC1091
source "${ROOT}/scripts/local_python.sh"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <backend/scripts/SCRIPT.py> [args...]" >&2
  echo "       CONFIRM_PROD=1 — разрешить запись в prod (без --dry-run)" >&2
  exit 1
fi

SCRIPT="$1"
shift

if [[ "${SCRIPT}" != /* ]]; then
  SCRIPT="${ROOT}/backend/${SCRIPT#backend/}"
fi

if [[ ! -f "${SCRIPT}" ]]; then
  echo "Script not found: ${SCRIPT}" >&2
  exit 1
fi

dry_run=false
for arg in "$@"; do
  if [[ "${arg}" == "--dry-run" ]]; then
    dry_run=true
    break
  fi
done

if [[ "${dry_run}" == false && "${CONFIRM_PROD:-}" != "1" ]]; then
  echo "" >&2
  echo "⚠️  Запись в prod-БД через SSH-туннель." >&2
  echo "   Dry-run:  make prod-run ARGS=\"${SCRIPT#${ROOT}/backend/} --dry-run ...\"" >&2
  echo "   Запись:   CONFIRM_PROD=1 make prod-run ARGS=\"${SCRIPT#${ROOT}/backend/} ...\"" >&2
  echo "" >&2
  exit 2
fi

export_prod_database_url
export_local_redis_url
export SCRIPT_VERBOSE=1
export PYTHONPATH="${ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"

warn_prod_db_locks

cd "${ROOT}/backend"
exec "${LOCAL_PYTHON}" "${SCRIPT}" "$@"
