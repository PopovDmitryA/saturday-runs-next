#!/usr/bin/env bash
# Resolve Python 3.12+ with backend deps for local script runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="$ROOT/.conda-parkrun"

ensure_local_python() {
  if [[ -x "${CONDA_ENV}/bin/python" ]] \
    && "${CONDA_ENV}/bin/python" -c "import sqlalchemy, psycopg, pydantic_settings" 2>/dev/null; then
    LOCAL_PYTHON="${CONDA_ENV}/bin/python"
    return 0
  fi

  for candidate in python3.12 python3; do
    if command -v "${candidate}" >/dev/null 2>&1 \
      && "${candidate}" -c "import sqlalchemy, psycopg, pydantic_settings" 2>/dev/null; then
      LOCAL_PYTHON="$(command -v "${candidate}")"
      return 0
    fi
  done

  if command -v conda >/dev/null 2>&1; then
    if [[ ! -x "${CONDA_ENV}/bin/python" ]]; then
      echo "local_python: создаём conda env ${CONDA_ENV} (один раз)…" >&2
      conda create -y -p "${CONDA_ENV}" python=3.12 pip
    fi
    echo "local_python: pip install backend…" >&2
    "${CONDA_ENV}/bin/pip" install -q -e "${ROOT}/backend"
    LOCAL_PYTHON="${CONDA_ENV}/bin/python"
    return 0
  fi

  echo "local_python: нужен Python 3.12+ с зависимостями backend." >&2
  echo "  pip install -e backend   или   conda create -p .conda-parkrun python=3.12" >&2
  return 1
}

ensure_local_python
