#!/usr/bin/env bash
# Держит домашние воркеры на том же коммите, что выкачен на прод.
#
# Зачем отдельный клон (~/srs-prod), а не тот, что уже есть в ~/saturday-runs-next:
# второй живёт rsync-ом и обслуживает разбор очереди профилей, у него свой .env
# и своя жизнь. Мешать их — значит ловить чужие поломки.
#
# Источник правды тот же, что у home_sync.sh, — файл .deployed_sha на проде:
# его пишет remote_deploy.sh ПОСЛЕ успешной пересборки и health-check. Брать
# origin/main нельзя: main бывает впереди прода, а воркер обязан совпадать с
# ним по схеме базы.
#
# Запуск: bash scripts/home_workers_sync.sh [--force]
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
SERVICES="worker-s95 worker-five-verst worker-runpark"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.home.yml)

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

[ -d "$HOME_DIR/.git" ] || { log "нет клона $HOME_DIR — сначала git clone"; exit 1; }
[ -f "$HOME_DIR/.env" ] || { log "нет $HOME_DIR/.env — воркеру неоткуда взять настройки"; exit 1; }

remote_sha=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" "cat $REMOTE_DIR/.deployed_sha 2>/dev/null" | tr -d '\r\n')
[ -n "$remote_sha" ] || { log "на проде нет маркера .deployed_sha, пропускаю"; exit 0; }

cd "$HOME_DIR" || exit 1
local_sha=$(git rev-parse HEAD 2>/dev/null)
if [ "$remote_sha" = "$local_sha" ] && [ "${1:-}" != "--force" ]; then
    log "совпадаем с продом ($remote_sha), делать нечего"
    exit 0
fi

log "прод на $remote_sha, у нас $local_sha — обновляюсь"
git fetch -q origin || { log "git fetch не прошёл — оставляю как было"; exit 1; }
git checkout -q --detach "$remote_sha" || { log "нет такого коммита локально"; exit 1; }

# --build обязателен: образ собирается из backend/, и без пересборки воркер
# поедет на старом коде, сколько бы git ни переключался.
"${COMPOSE[@]}" up -d --build $SERVICES || { log "воркеры не поднялись"; exit 1; }
log "воркеры на $remote_sha: $("${COMPOSE[@]}" ps --status running --services | tr '\n' ' ')"
