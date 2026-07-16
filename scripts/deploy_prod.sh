#!/usr/bin/env bash
# Deploy to production: сервер сам подтягивает задеплоенный коммит из origin/main.
#
# Почему НЕ rsync (переделано 16.07.2026):
#   1. Старый скрипт слал ~12 отдельных rsync, каждый — свой SSH-коннект. Прод
#      начинал отвергать авторизацию со второго коннекта подряд (защита от
#      перебора пароля), деплой падал на полпути, а ретраи только продлевали
#      блокировку — вплоть до отказа даже одиночному ssh. См. память
#      project_deploy_rsync_auth_lockout. Теперь коннект РОВНО ОДИН.
#   2. rsync писал файлы мимо git, поэтому прод-git молча уползал (старый HEAD +
#      куча "modified"), и его приходилось «выравнивать» задним числом отдельным
#      шагом, который к тому же пропускался при грязном дереве. Теперь код
#      приезжает через git — прод-git правдив by design, выравнивать нечего.
#   3. На прод уезжало содержимое ДИСКА, включая незакоммиченные правки. Теперь
#      уезжает ровно то, что запушено в origin/main (правило Дмитрия: сначала
#      коммит+пуш, потом сервер подтягивает).
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
# Serialize concurrent deploys (две сессии / worktree могут стартовать деплой
# одновременно и затереть друг друга). Замок — в фиксированном месте вне проекта,
# чтобы разные worktree-папки контендили за ОДИН замок.
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

# --- Preflight: деплоим только то, что реально лежит в origin/main ------------
# Сервер тянет код из GitHub, поэтому незапушенный коммит физически невозможно
# задеплоить — ловим это здесь с внятным сообщением, а не ошибкой git на проде.
echo "=== preflight ==="
git fetch origin --quiet

LOCAL_SHA="$(git rev-parse HEAD)"

# Единственное жёсткое условие: коммит должен быть в origin/main.
if ! git branch -r --contains "$LOCAL_SHA" 2>/dev/null | grep -q 'origin/main'; then
  echo "deploy_prod: коммита ${LOCAL_SHA} нет в origin/main — сначала закоммить и запушь." >&2
  echo "  (сервер берёт код из GitHub, с диска на прод больше ничего не уезжает)" >&2
  exit 1
fi

# Грязное дерево деплою больше НЕ мешает: уезжает коммит, а не содержимое диска,
# поэтому чужой/свой WIP просто остаётся дома. Раньше (rsync) он бы уехал на прод —
# в общем дереве часто лежит WIP параллельных сессий, см. память
# feedback_parallel_sessions_shared_index. Но предупредить стоит: легко подумать,
# что правка уехала, хотя она не закоммичена.
if ! git diff --quiet HEAD; then
  echo "NOTE: в дереве есть незакоммиченные правки — они НЕ уедут на прод (деплоится коммит):"
  git status --short --untracked-files=no | sed 's/^/       /'
fi

# HEAD может быть предком origin/main (например, забыли смержить свежий main) —
# тогда деплоится СТАРЫЙ код, что почти всегда не то, чего ждёшь.
ORIGIN_MAIN_SHA="$(git rev-parse origin/main)"
if [[ "$LOCAL_SHA" != "$ORIGIN_MAIN_SHA" ]]; then
  echo "WARN: HEAD отстаёт от origin/main — деплоится ${LOCAL_SHA:0:7}, а в main уже ${ORIGIN_MAIN_SHA:0:7}."
  echo "      Если нужен свежий main: git merge origin/main (или git checkout main && git pull)."
fi

echo "deploy: ${LOCAL_SHA} ($(git log -1 --pretty=%s))"
echo "target: ${SSH_USER}@${SSH_HOST}:${REMOTE}"

REMOTE_QUOTED=$(printf '%q' "$REMOTE")

# --- Один SSH-коннект: код + сборка + миграции + рестарт ----------------------
echo "=== remote deploy (single ssh connection) ==="
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "${SSH_USER}@${SSH_HOST}" \
  "REMOTE=${REMOTE_QUOTED} LOCAL_SHA=${LOCAL_SHA} bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$REMOTE"

# Функция, а не eval: eval ломает кавычки внутри --format / python -c.
compose() { docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"; }

# Сервисы, которые пересоздаём. nginx собирать нельзя — он на готовом образе
# (nginx:1.27-alpine), build-контекст есть только у python-сервисов.
SERVICES="worker-s95 worker-five-verst worker-parkrun worker-runpark api nginx beat vk-bot"

# NB: `docker compose exec/run -T` всё равно цепляет контейнер к stdin. Скрипт
# приезжает в `bash -s` через SSH stdin, поэтому каждый exec/run ОБЯЗАН читать
# из /dev/null — иначе он сожрёт остаток скрипта и деплой молча оборвётся тут.
echo "--- queue lengths before ---"
compose exec -T redis redis-cli LLEN five_verst </dev/null || true
compose exec -T redis redis-cli LLEN five_verst_user </dev/null || true
compose exec -T redis redis-cli LLEN s95 </dev/null || true
compose exec -T redis redis-cli LLEN s95_user </dev/null || true

echo "--- sync code: git -> ${LOCAL_SHA} ---"
# В норме здесь пусто. Непусто = кто-то правил файлы на проде руками (или остатки
# оборвавшегося старого rsync-деплоя) — reset их затрёт, поэтому показываем что.
dirty="$(git status --porcelain --untracked-files=no)"
if [ -n "$dirty" ]; then
  echo "WARN: правки в отслеживаемых файлах на проде будут затёрты git reset:"
  echo "$dirty"
fi
git fetch origin --quiet
# Untracked-мусор (nginx/*.conf, grafana/, backend/scripts/, логи) reset не трогает.
git reset --hard "$LOCAL_SHA"
git log --oneline -1

echo "--- frontend build ---"
docker run --rm -v "$PWD/frontend:/app" -w /app node:22-alpine sh -c "npm ci && npm run build"

echo "--- build api image (нужен свежий образ для миграций) ---"
compose build api

echo "--- migrations (свежим образом, ДО recreate) ---"
# Порядок важен: миграцию накатываем НОВЫМ образом, но пока крутится СТАРЫЙ код.
# Если сделать наоборот (recreate раньше upgrade) — будет окно, где новый код
# бьётся в ещё не созданную таблицу и отдаёт 500. Старый код переживает
# аддитивную схему спокойно. Упадёт миграция — деплой встанет здесь (set -e),
# прод останется на старом рабочем коде.
compose run --rm -T api alembic upgrade head </dev/null

echo "--- recreate services ---"
# --force-recreate гарантирует, что контейнеры реально перезапустятся на новом
# коде (обычный up -d может переиспользовать старый контейнер).
compose up -d --build --force-recreate $SERVICES
compose restart nginx
compose stop worker-s95-user worker-five-verst-user 2>/dev/null || true
compose rm -f worker-s95-user worker-five-verst-user 2>/dev/null || true

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
compose exec -T redis redis-cli LLEN five_verst </dev/null || true
compose exec -T redis redis-cli LLEN five_verst_user </dev/null || true

echo "--- smoke ---"
compose ps --format '{{.Service}}: {{.Status}}'
curl -s -o /dev/null -w "health: %{http_code}\n" http://127.0.0.1:8080/health || true
curl -sI 'https://run5k.run/d/de1hu8dabny80c/karta-turistov' 2>/dev/null | head -1 || true

echo "--- prod git == deployed commit ---"
git log --oneline -1
REMOTE_SCRIPT

echo "=== deploy done: ${LOCAL_SHA} ==="
