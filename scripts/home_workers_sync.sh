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
# Запуск: bash scripts/home_workers_sync.sh [--force | --keep-code]
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
# tg-proxy — не воркер, но без неё домашние воркеры не достучатся до
# api.telegram.org, и уведомления об отмене старта уйдут в ВК-фолбэк.
SERVICES="worker-s95 worker-five-verst worker-five-verst-fresh worker-runpark tg-proxy"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.home.yml)

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# После переезда сайта домой воркеры живут в общем стеке (docker-compose.home-site.yml)
# и ходят в локальную базу. Этот скрипт поднял бы ВТОРОЙ комплект воркеров,
# смотрящий в брошенную базу на VPS, — поэтому он просто отходит в сторону.
# Откат переезда снимает маркер и зовёт этот скрипт с --keep-code.
if [ -f "${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}" ]; then
    log "сайт переехал домой — воркеры живут в общем стеке, синхронизировать нечего"
    exit 0
fi

[ -d "$HOME_DIR/.git" ] || { log "нет клона $HOME_DIR — сначала git clone"; exit 1; }
[ -f "$HOME_DIR/.env" ] || { log "нет $HOME_DIR/.env — воркеру неоткуда взять настройки"; exit 1; }
# Конфиг прокси gitignored и живёт только на серверах: без него docker
# подсунул бы вместо файла пустой каталог, и xray не поднялся бы.
if [ ! -f "$HOME_DIR/deploy/tg-proxy/config.json" ]; then
    log "нет deploy/tg-proxy/config.json — поднимаю без прокси, Telegram-уведомления уйдут в ВК"
    SERVICES="worker-s95 worker-five-verst worker-five-verst-fresh worker-runpark"
# Контейнер xray ходит под uid 65532, а не под хозяином файла: конфиг с правами
# 600 он не прочитает и уйдёт в крэш-луп. На VPS файл лежит с 664.
elif [ "$(stat -c '%A' "$HOME_DIR/deploy/tg-proxy/config.json" | cut -c8)" != "r" ]; then
    log "deploy/tg-proxy/config.json не читается чужим uid — выставляю 644"
    chmod 644 "$HOME_DIR/deploy/tg-proxy/config.json"
fi

# --keep-code: поднять воркеры на том коде, что сейчас в клоне, git не трогая.
# Так зовёт откат переезда (scripts/cutover_rollback.sh now): из этого клона
# идёт сам откат, а шаг back-dump везёт отсюда скрипт роли под схему домашней
# базы — сдвинуть клон на коммит VPS посреди отката значило бы подменить обоим
# код. С VPS клон сверит обычный запуск.
if [ "${1:-}" = "--keep-code" ]; then
    cd "$HOME_DIR" || exit 1
    "${COMPOSE[@]}" up -d --build $SERVICES || { log "воркеры не поднялись"; exit 1; }
    log "воркеры на $(git rev-parse --short HEAD), код не трогал: $("${COMPOSE[@]}" ps --status running --services | tr '\n' ' ')"
    exit 0
fi

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
