#!/usr/bin/env bash
# Интерактивное меню обновления данных (как legacy main.py).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "${ROOT}/scripts/local_python.sh"
exec "${LOCAL_PYTHON}" "${ROOT}/backend/scripts/sync_menu.py"
