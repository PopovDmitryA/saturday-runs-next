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

# --- Deploy lock -------------------------------------------------------------
# Serialize concurrent deploys (two sessions / worktrees могут стартовать rsync +
# git-align на проде одновременно и затереть друг друга). Замок — в фиксированном
# месте вне проекта, чтобы разные worktree-папки контендили за ОДИН замок.
# macOS без flock, поэтому используем атомарный mkdir. Второй деплой ждёт первого.
LOCK_DIR="${DEPLOY_LOCK_DIR:-/tmp/saturday-runs-deploy.lock}"
lock_waited=0
while ! mkdir "$LOCK_DIR" 2>/dev/null; do
  holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo '?')"
  # Зависший замок от упавшего деплоя (процесса уже нет) — забираем себе.
  if [[ "$holder" =~ ^[0-9]+$ ]] && ! kill -0 "$holder" 2>/dev/null; then
    echo "deploy_prod: stale lock (dead pid $holder) — reclaiming" >&2
    rm -rf "$LOCK_DIR"
    continue
  fi
  if (( lock_waited == 0 )); then
    echo "deploy_prod: another deploy in progress (pid $holder) — waiting..." >&2
  fi
  sleep 5
  lock_waited=$(( lock_waited + 5 ))
  if (( lock_waited >= 1800 )); then
    echo "deploy_prod: waited 30m for deploy lock, giving up." >&2
    exit 1
  fi
done
echo "$$" > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT
# -----------------------------------------------------------------------------
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
rsync_to backend/alembic/ "${REMOTE}/backend/alembic/"
rsync_to backend/Dockerfile "${REMOTE}/backend/Dockerfile"
rsync_to backend/Dockerfile.parkrun "${REMOTE}/backend/Dockerfile.parkrun"
rsync_to backend/pyproject.toml "${REMOTE}/backend/pyproject.toml"
rsync_to deploy/ "${REMOTE}/deploy/"
rsync_to docker-compose.yml "${REMOTE}/docker-compose.yml"
rsync_to docker-compose.prod.yml "${REMOTE}/docker-compose.prod.yml"
rsync_to frontend/src/ "${REMOTE}/frontend/src/"
# index.html — шаблон Vite: inline-скрипты/меты попадают в собранный HTML,
# без rsync сборка на сервере идёт со старым шаблоном.
rsync_to frontend/index.html "${REMOTE}/frontend/index.html"
# public/ — рантайм-статики (geojson карт и т.п.): Vite копирует их в dist при
# сборке, без rsync новые файлы не попадут в бандл (git align идёт ПОСЛЕ сборки).
rsync_to frontend/public/ "${REMOTE}/frontend/public/"

REMOTE_QUOTED=$(printf '%q' "$REMOTE")
COMPOSE_QUOTED=$(printf '%q' "$COMPOSE")

# Keep prod git in sync with what we deploy. rsync only writes files to disk and
# never advances prod's git HEAD, so prod git silently drifts (stale HEAD + a pile
# of "modified"/untracked noise). To stop that, after the deploy we reset prod git
# to the exact commit we shipped — but ONLY when this deploy came from a clean tree
# that is already on origin/main. Otherwise (dirty/unpushed = hotfix testing) we
# skip and leave prod git alone, so the reset can never clobber un-pushed changes.
git fetch origin --quiet 2>/dev/null || true
LOCAL_SHA="$(git rev-parse HEAD)"
GIT_ALIGN=0
# Align only if HEAD is on origin/main AND the rsynced paths have no *tracked*
# modifications vs HEAD (untracked local files like backend/scripts/* are not
# deployed and must not block alignment — hence --quiet on the exact paths).
if git branch -r --contains "$LOCAL_SHA" 2>/dev/null | grep -q 'origin/main' \
  && git diff --quiet HEAD -- backend/app backend/vk_bot backend/alembic deploy \
       frontend/src frontend/index.html frontend/public docker-compose.yml docker-compose.prod.yml; then
  GIT_ALIGN=1
else
  echo "WARN: HEAD not on origin/main or deployed paths have tracked changes — prod git left as-is (no align)"
fi

echo "=== remote build & restart ==="
sshpass -e ssh -o StrictHostKeyChecking=no "${SSH_USER}@${SSH_HOST}" \
  "REMOTE=${REMOTE_QUOTED} COMPOSE=${COMPOSE_QUOTED} LOCAL_SHA=${LOCAL_SHA} GIT_ALIGN=${GIT_ALIGN} bash -s" <<'REMOTE_SCRIPT'
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
# --force-recreate guarantees containers restart with the freshly built code
# (a plain `up -d --build` can leave the old process running if Docker reuses
# the container), so backend changes always take effect after a deploy.
eval "$COMPOSE" up -d --build --force-recreate worker-s95 worker-five-verst worker-parkrun worker-runpark api nginx beat vk-bot
eval "$COMPOSE" restart nginx
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

echo "--- git align (keep prod git == deployed commit) ---"
if [ "${GIT_ALIGN:-0}" = "1" ]; then
  git fetch origin --quiet \
    && git reset --hard "${LOCAL_SHA}" \
    && echo "prod git aligned to ${LOCAL_SHA}" \
    || echo "WARN: git align failed — prod git left as-is"
else
  echo "skipped git align (deploy not from a clean origin/main tree); prod git unchanged"
fi

echo "--- smoke ---"
curl -sf http://127.0.0.1:8080/health | head -c 200 || true
echo
curl -sI 'https://run5k.run/d/de1hu8dabny80c/karta-turistov' 2>/dev/null | head -5 || true
REMOTE_SCRIPT

echo "=== deploy done ==="
