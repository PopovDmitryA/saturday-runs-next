#!/bin/bash
# Free disk on prod: Docker junk, host Playwright cache, optional frontend node_modules.
# Safe to run weekly (cron). Full build cache wipe only with --full.
#
# Usage on server:
#   bash scripts/prune_server_disk.sh
#   bash scripts/prune_server_disk.sh --full   # also docker builder prune -af
set -euo pipefail

ROOT="${ROOT:-/opt/saturday-runs-next}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
FULL=false
if [[ "${1:-}" == "--full" ]]; then
  FULL=true
fi

echo "=== host Playwright cache (not used by Docker workers) ==="
if [[ -d /root/.cache/ms-playwright ]]; then
  rm -rf /root/.cache/ms-playwright
  echo "removed /root/.cache/ms-playwright"
fi

echo "=== unused Docker images (keep running containers) ==="
docker image prune -af

echo "=== dangling Docker build cache ==="
docker builder prune -f
if $FULL; then
  echo "=== full Docker build cache (--full) ==="
  docker builder prune -af
fi

if [[ -d "$ROOT/frontend/node_modules" && -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "=== frontend/node_modules on host (dist already built) ==="
  rm -rf "$ROOT/frontend/node_modules"
fi

echo "=== journald vacuum (100M cap) ==="
if command -v journalctl >/dev/null 2>&1; then
  journalctl --vacuum-size=100M || sudo journalctl --vacuum-size=100M
fi

echo "=== disk after ==="
df -h / | tail -1
docker system df
