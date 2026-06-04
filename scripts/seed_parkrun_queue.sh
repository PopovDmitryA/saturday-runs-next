#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CONDA_ENV="$ROOT/.conda-parkrun"
if [[ ! -x "$CONDA_ENV/bin/python" ]]; then
  echo "Сначала: make parkrun  (создаст conda env) или conda create…"
  exit 1
fi

export PYTHONPATH="$ROOT/backend"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://saturday_runs:saturday_runs@127.0.0.1:${POSTGRES_PUBLISH_PORT:-5433}/saturday_runs_lk}"
# LEGACY_DATABASE_URL=postgresql+psycopg://readonly_user:***@195.58.34.112:5432/five_verst_stats

exec "$CONDA_ENV/bin/python" "$ROOT/backend/scripts/seed_parkrun_queue_from_legacy.py" "$@"
