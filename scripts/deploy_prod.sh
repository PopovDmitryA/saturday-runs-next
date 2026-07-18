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

# --- Preflight: деплоится вершина origin/main, а не HEAD этой папки -----------
# Деплой git-based: прод делает `git reset --hard <SHA>`, т.е. это не «выкатить мою
# фичу», а «привести прод к состоянию коммита». Поэтому целевой коммит — ВСЕГДА
# вершина origin/main, независимо от того, из какой папки/ветки запущен скрипт.
#
# Раньше деплоился HEAD текущего worktree, и это откатывало прод: сессия A мержит
# свою ветку в main, следом мержатся B..E, потом A запускает деплой из своей папки —
# её HEAD всё ещё фичевый коммит, теперь предок main, проверка «содержится в
# origin/main» проходит, и reset --hard на этот SHA сносит с прода фичи B..E.
# Про это был только WARN, который легко проскочить глазами. Теперь так не выйдет.
#
# Побочный эффект (нужный): деплой идемпотентен и запускается откуда угодно —
# из любого worktree, любой сессии. Параллельные сессии могут не согласовывать,
# «чья очередь»: конкурентные запуски сериализует замок выше, а выкатывают они
# всё равно один и тот же актуальный main.
echo "=== preflight ==="
git fetch origin --quiet

ORIGIN_MAIN_SHA="$(git rev-parse origin/main)"

# Откат/хотфикс: DEPLOY_SHA=<sha> bash scripts/deploy_prod.sh — выкатить конкретный
# коммит. Всё равно обязан быть в origin/main: сервер берёт код из GitHub.
DEPLOY_SHA="${DEPLOY_SHA:-$ORIGIN_MAIN_SHA}"
DEPLOY_SHA="$(git rev-parse "$DEPLOY_SHA")"

if ! git branch -r --contains "$DEPLOY_SHA" 2>/dev/null | grep -q 'origin/main'; then
  echo "deploy_prod: коммита ${DEPLOY_SHA} нет в origin/main — сначала закоммить и запушь." >&2
  echo "  (сервер берёт код из GitHub, с диска на прод больше ничего не уезжает)" >&2
  exit 1
fi

if [[ "$DEPLOY_SHA" != "$ORIGIN_MAIN_SHA" ]]; then
  echo "WARN: DEPLOY_SHA=${DEPLOY_SHA:0:7} — это НЕ вершина origin/main (${ORIGIN_MAIN_SHA:0:7})."
  echo "      Всё, что смержено в main после этого коммита, уедет с прода."
fi

# Локальные коммиты, которых нет в origin/main, на прод НЕ уедут. Самая частая
# причина «задеплоил, а фичи нет» — забытый пуш или незамерженная ветка.
if ! git merge-base --is-ancestor HEAD "$ORIGIN_MAIN_SHA" 2>/dev/null; then
  echo "NOTE: HEAD этой папки ($(git rev-parse --abbrev-ref HEAD)) не влит в origin/main —"
  echo "      его коммиты в этот деплой не попадут:"
  git log --oneline "${ORIGIN_MAIN_SHA}..HEAD" 2>/dev/null | head -10 | sed 's/^/       /'
fi

# Грязное дерево деплою не мешает: уезжает коммит из GitHub, а не содержимое диска,
# поэтому чужой/свой WIP просто остаётся дома. Раньше (rsync) он бы уехал на прод —
# в общем дереве часто лежит WIP параллельных сессий, см. память
# feedback_parallel_sessions_shared_index. Но предупредить стоит: легко подумать,
# что правка уехала, хотя она не закоммичена.
if ! git diff --quiet HEAD; then
  echo "NOTE: в дереве есть незакоммиченные правки — они НЕ уедут на прод (деплоится коммит):"
  git status --short --untracked-files=no | sed 's/^/       /'
fi

LOCAL_SHA="$DEPLOY_SHA"
echo "deploy: ${LOCAL_SHA} ($(git log -1 --pretty=%s "$LOCAL_SHA"))"
echo "target: ${SSH_USER}@${SSH_HOST}:${REMOTE}"

REMOTE_QUOTED=$(printf '%q' "$REMOTE")

# --- Один SSH-коннект: код + сборка + миграции + рестарт ----------------------
# Этот heredoc — ТОЛЬКО загрузчик: он приезжает с диска текущей папки, поэтому в
# нём не должно быть ничего, что меняется от релиза к релизу. Сам сценарий деплоя
# лежит в scripts/remote_deploy.sh и запускается уже ИЗ ЗАДЕПЛОЕННОГО КОММИТА —
# так сценарий всегда соответствует коду, даже если папка, откуда запущен деплой,
# отстала от origin/main (18.07.2026 из-за этого не поднялся новый сервис worker).
echo "=== remote deploy (single ssh connection) ==="
sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "${SSH_USER}@${SSH_HOST}" \
  "REMOTE=${REMOTE_QUOTED} LOCAL_SHA=${LOCAL_SHA} bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail
cd "$REMOTE"

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

# Дальше — сценарий из ЗАДЕПЛОЕННОГО коммита, а не с диска запускающей папки.
if [ ! -f scripts/remote_deploy.sh ]; then
  echo "DEPLOY FAILED: в коммите ${LOCAL_SHA} нет scripts/remote_deploy.sh." >&2
  echo "  Такое бывает при откате на коммит старше 18.07.2026 (тогда сценарий" >&2
  echo "  деплоя ещё жил внутри deploy_prod.sh). Откатывай на коммит новее." >&2
  exit 1
fi
# </dev/null: остаток этого heredoc'а приезжает через SSH stdin — дочерний скрипт
# не должен его вычитать.
bash scripts/remote_deploy.sh </dev/null

REMOTE_SCRIPT

echo "=== deploy done: ${LOCAL_SHA} ==="
