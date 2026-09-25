#!/usr/bin/env bash
# Держит домашние воркеры сбора на том же коммите, что выкачен на прод.
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
# Это ЕДИНСТВЕННОЕ место, которое двигает клон: таймер pm-home-sync зовёт его из
# home_sync.sh (--target, маркер тот уже прочёл), руками — без аргументов.
# До 25.09.2026 механизмов было два, и они мешали друг другу: этот делал
# checkout --detach, а home_sync.sh с отвязанным HEAD отказывался работать и
# перематывал только ветку через merge --ff-only — а тот на коммит-предок
# отвечает «Already up to date» с кодом 0, и при клоне впереди прода воркеры
# перезапускались бы каждые пять минут.
#
# Теперь: клон всегда на ветке main, ветка ставится ровно на коммит прода
# (checkout -B — хоть вперёд, хоть назад при откате прода), затем
# `up -d --build`: пересборка образа нужна при смене зависимостей и compose, а
# без изменений она берёт кэш. Любая заминка — тревога админу в Telegram
# (scripts/lib/home_alert.sh), а не строчка в journal.
#
# Запуск:
#   bash scripts/home_workers_sync.sh                 # сверить с .deployed_sha VPS
#   bash scripts/home_workers_sync.sh --target <sha>  # так зовёт home_sync.sh
#   bash scripts/home_workers_sync.sh --force         # пересобрать, даже если совпадаем
#   bash scripts/home_workers_sync.sh --keep-code     # поднять на коде клона, git не трогая
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
# Список обязан совпадать с HOME_SERVICES в remote_deploy.sh: их прод гасит,
# потому что они работают здесь.
WORKERS="worker-s95 worker-five-verst worker-five-verst-fresh worker-runpark"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
# shellcheck source=scripts/lib/home_alert.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib/home_alert.sh"

compose() { (cd "$HOME_DIR" && docker compose -f docker-compose.yml -f docker-compose.home.yml "$@"); }

mode="sync" target="" force=""
while [ $# -gt 0 ]; do
    case "$1" in
        --keep-code) mode="keep-code" ;;
        --force) force=1 ;;
        --target) target="${2:-}"; shift ;;
        *) echo "неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
    shift
done

# После переезда сайта домой воркеры живут в общем стеке (docker-compose.home-site.yml)
# и ходят в локальную базу, а этот клон — живой сайт, его ведёт deploy_home.sh.
# Здесь делать нечего: этот скрипт поднял бы ВТОРОЙ комплект воркеров,
# смотрящий в брошенную базу на VPS. Откат переезда снимает маркер и зовёт
# этот скрипт с --keep-code.
if [ -f "$MARKER" ]; then
    log "сайт переехал домой — воркеры живут в общем стеке, синхронизировать нечего"
    exit 0
fi

[ -d "$HOME_DIR/.git" ] || { alert workers-clone "нет клона $HOME_DIR — воркеры сбора не обновляются"; exit 1; }
[ -f "$HOME_DIR/.env" ] || { alert workers-clone "нет $HOME_DIR/.env — воркерам сбора неоткуда взять настройки"; exit 1; }

# tg-proxy — не воркер, но без неё домашние воркеры не достучатся до
# api.telegram.org, и уведомления об отмене старта уйдут в ВК-фолбэк.
# Конфиг прокси gitignored и живёт только на серверах: без него docker
# подсунул бы вместо файла пустой каталог, и xray не поднялся бы.
proxy="tg-proxy"
if [ ! -f "$HOME_DIR/deploy/tg-proxy/config.json" ]; then
    log "нет deploy/tg-proxy/config.json — поднимаю без прокси, Telegram-уведомления уйдут в ВК"
    proxy=""
# Контейнер xray ходит под uid 65532, а не под хозяином файла: конфиг с правами
# 600 он не прочитает и уйдёт в крэш-луп. На VPS файл лежит с 664.
elif [ "$(stat -c '%A' "$HOME_DIR/deploy/tg-proxy/config.json" | cut -c8)" != "r" ]; then
    log "deploy/tg-proxy/config.json не читается чужим uid — выставляю 644"
    chmod 644 "$HOME_DIR/deploy/tg-proxy/config.json"
fi

# Все ли воркеры в состоянии running; пустой вывод — всё хорошо.
missing_workers() {
    local running svc out=""
    running=$(compose ps --status running --services 2>/dev/null)
    for svc in $WORKERS; do
        grep -qx "$svc" <<<"$running" || out="$out $svc"
    done
    echo "${out# }"
}

bring_up() {
    # --build обязателен: образ собирается из backend/, и без пересборки новая
    # зависимость или правка compose до воркеров не доедут (restart их не видит).
    if ! compose up -d --build $WORKERS $proxy; then
        alert workers-up "воркеры сбора не поднялись на $(git -C "$HOME_DIR" rev-parse --short HEAD) — docker compose up упал, см. journalctl -u pm-home-sync"
        return 1
    fi
    local missing
    missing=$(missing_workers)
    if [ -n "$missing" ]; then
        alert workers-up "после обновления не работают: $missing (docker compose -f docker-compose.yml -f docker-compose.home.yml ps в $HOME_DIR)"
        return 1
    fi
    return 0
}

# --keep-code: поднять воркеры на том коде, что сейчас в клоне, git не трогая.
# Так зовёт откат переезда (scripts/cutover_rollback.sh now): из этого клона
# идёт сам откат, а шаг back-dump везёт отсюда скрипт роли под схему домашней
# базы — сдвинуть клон на коммит VPS посреди отката значило бы подменить обоим
# код. С VPS клон сверит следующий заход таймера.
if [ "$mode" = "keep-code" ]; then
    bring_up || exit 1
    log "воркеры на $(git -C "$HOME_DIR" rev-parse --short HEAD), код не трогал: $(compose ps --status running --services | tr '\n' ' ')"
    exit 0
fi

if [ -z "$target" ]; then
    # Удалённый cat не должен ронять ssh: код возврата — только про связь.
    if ! target=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" "cat $REMOTE_DIR/.deployed_sha 2>/dev/null || true"); then
        log "прод недоступен по ssh — сверить не с чем, оставляю как есть"
        exit 1
    fi
    target=$(tr -d '\r\n' <<<"$target")
fi
[ -n "$target" ] || { log "на проде нет маркера .deployed_sha, пропускаю"; exit 0; }

head=$(git -C "$HOME_DIR" rev-parse HEAD 2>/dev/null)
on_branch=$(git -C "$HOME_DIR" symbolic-ref -q --short HEAD 2>/dev/null)

if [ "$head" = "$target" ] && [ "$on_branch" = "main" ] && [ -z "$force" ]; then
    # Код тот же. Проверяем только, что воркеры живы: сами мы их не поднимаем —
    # их могли остановить руками (разбор аварии), и таймер не должен с этим спорить.
    missing=$(missing_workers)
    if [ -n "$missing" ]; then
        alert workers-down "код воркеров сбора совпадает с продом, но не работают: $missing"
        exit 1
    fi
    alert_clear workers- "воркеры сбора снова на коде прода (${target:0:7}) и работают"
    exit 0
fi

log "прод на ${target:0:7}, воркеры на ${head:0:7}${on_branch:+ (ветка $on_branch)} — обновляю"

# Правки в отслеживаемых файлах затирать нельзя: это чья-то незаконченная
# работа (24.09.2026 так остались правки репетиции переезда). Остановиться и
# сказать — лучше, чем молча стереть.
if [ -n "$(git -C "$HOME_DIR" status --porcelain --untracked-files=no)" ]; then
    alert workers-dirty "в $HOME_DIR незакоммиченные правки — воркеры сбора остались на ${head:0:7}, прод на ${target:0:7}. Разобрать: git -C $HOME_DIR status (git pull НЕ делать)"
    exit 1
fi
if ! git -C "$HOME_DIR" fetch -q origin; then
    alert workers-fetch "в $HOME_DIR не прошёл git fetch — воркеры сбора остались на ${head:0:7}, прод на ${target:0:7}"
    exit 1
fi
if ! git -C "$HOME_DIR" cat-file -e "${target}^{commit}" 2>/dev/null; then
    alert workers-fetch "коммита прода ${target:0:7} нет в origin — воркеры сбора остались на ${head:0:7}"
    exit 1
fi
# Свои коммиты в клоне (не запушенные никуда) checkout -B унёс бы из ветки.
if [ -z "$(git -C "$HOME_DIR" branch -r --contains HEAD 2>/dev/null)" ]; then
    alert workers-dirty "в $HOME_DIR коммит ${head:0:7}, которого нет в origin — не трогаю, воркеры сбора отстают от прода (${target:0:7})"
    exit 1
fi

# Ветка main ставится ровно на коммит прода: вперёд — обычный выкат, назад —
# откат прода (DEPLOY_SHA=старый), из отвязанного HEAD — обратно на ветку.
if ! git -C "$HOME_DIR" checkout -q -B main "$target"; then
    alert workers-checkout "в $HOME_DIR не переключился на ${target:0:7} — воркеры сбора остались на ${head:0:7}"
    exit 1
fi
if [ "$(git -C "$HOME_DIR" rev-parse HEAD)" != "$target" ]; then
    alert workers-checkout "в $HOME_DIR HEAD не совпал с продом (${target:0:7}) после переключения"
    exit 1
fi

bring_up || exit 1
log "воркеры сбора на ${target:0:7}: $(compose ps --status running --services | tr '\n' ' ')"
alert_clear workers- "воркеры сбора снова на коде прода (${target:0:7}) и работают"
