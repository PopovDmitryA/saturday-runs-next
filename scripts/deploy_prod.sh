#!/usr/bin/env bash
# Deploy local changes to production (rsync + build + docker + host nginx).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

SSH_HOST="${TEMP_SSH_HOST:-${PROD_SSH_HOST:-195.58.34.112}}"
SSH_USER="${TEMP_SSH_USER:-${PROD_SSH_USER:-viewer}}"
REMOTE="${PROD_REMOTE_DIR:-/opt/saturday-runs-next}"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

if [[ -z "${TEMP_SSH_PASSWORD:-}" ]]; then
  echo "deploy_prod: TEMP_SSH_PASSWORD not set in .env" >&2
  exit 1
fi

if ! command -v sshpass >/dev/null 2>&1; then
  echo "deploy_prod: sshpass required" >&2
  exit 1
fi

export SSHPASS="${TEMP_SSH_PASSWORD}"
SSH_OPTS=(-o StrictHostKeyChecking=no)

rsync_to() {
  rsync -avz \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --no-perms --no-owner --no-group \
    -e "sshpass -e ssh ${SSH_OPTS[*]}" "$1" "${SSH_USER}@${SSH_HOST}:$2"
}

echo "=== rsync to ${SSH_USER}@${SSH_HOST}:${REMOTE} ==="
rsync_to backend/app/ "${REMOTE}/backend/app/"
rsync_to backend/vk_bot/ "${REMOTE}/backend/vk_bot/"
rsync_to deploy/ "${REMOTE}/deploy/"
rsync_to docker-compose.yml "${REMOTE}/docker-compose.yml"
rsync_to docker-compose.prod.yml "${REMOTE}/docker-compose.prod.yml"
rsync_to frontend/src/ "${REMOTE}/frontend/src/"

REMOTE_QUOTED=$(printf '%q' "$REMOTE")
COMPOSE_QUOTED=$(printf '%q' "$COMPOSE")

echo "=== remote build & restart ==="
sshpass -e ssh -o StrictHostKeyChecking=no "${SSH_USER}@${SSH_HOST}" \
  "REMOTE=${REMOTE_QUOTED} COMPOSE=${COMPOSE_QUOTED} bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$REMOTE"

# NB: `docker compose exec -T` still attaches the container to stdin. This script
# is fed to `bash -s` over the SSH stdin, so every exec MUST redirect stdin from
# /dev/null — otherwise it swallows the rest of the script and the deploy stops
# silently right here.
echo "--- queue lengths before ---"
eval "$COMPOSE" exec -T redis redis-cli LLEN five_verst </dev/null || true
eval "$COMPOSE" exec -T redis redis-cli LLEN five_verst_user </dev/null || true
eval "$COMPOSE" exec -T redis redis-cli LLEN s95 </dev/null || true
eval "$COMPOSE" exec -T redis redis-cli LLEN s95_user </dev/null || true

echo "--- frontend build ---"
docker run --rm -v "$PWD/frontend:/app" -w /app node:22-alpine sh -c "npm ci && npm run build"

echo "--- docker services ---"
eval "$COMPOSE" up -d --build worker-s95 worker-five-verst worker-parkrun api nginx beat vk-bot
eval "$COMPOSE" restart nginx api
eval "$COMPOSE" stop worker-s95-user worker-five-verst-user 2>/dev/null || true
eval "$COMPOSE" rm -f worker-s95-user worker-five-verst-user 2>/dev/null || true

echo "--- host nginx (run5k.run Grafana redirects) ---"
if sudo -n cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run 2>/dev/null; then
  sudo -n nginx -t
  sudo -n systemctl reload nginx
  echo "host nginx reloaded"
else
  echo "WARN: passwordless sudo unavailable — run on server:"
  echo "  sudo cp deploy/nginx/run5k.run.conf /etc/nginx/sites-available/run5k.run"
  echo "  sudo nginx -t && sudo systemctl reload nginx"
fi

echo "--- queue lengths after ---"
eval "$COMPOSE" exec -T redis redis-cli LLEN five_verst </dev/null || true
eval "$COMPOSE" exec -T redis redis-cli LLEN five_verst_user </dev/null || true

echo "--- smoke ---"
curl -sf http://127.0.0.1:8080/health | head -c 200 || true
echo
curl -sI 'https://run5k.run/d/de1hu8dabny80c/karta-turistov' 2>/dev/null | head -5 || true
REMOTE_SCRIPT

echo "=== deploy done ==="
