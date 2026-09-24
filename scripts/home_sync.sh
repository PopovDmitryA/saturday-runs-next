#!/usr/bin/env bash
# Догоняет домашний сервер до коммита, выкаченного на прод.
#
# Зачем. Домашний сервер исполняет код сайта (разбор очереди профилей) против
# ПРОДОВОЙ базы, поэтому обязан совпадать с продом по схеме. 02.09.2026 они
# разошлись, и это проявилось как «column users.display_name_style does not
# exist» посреди разбора очереди.
#
# Почему сервер тянет сам, а не деплой его толкает: домашняя сеть за NAT, наружу
# открыты только 80 и 443, SSH намеренно не проброшен. Инициатива всегда изнутри —
# как и у туннеля к базе. Деплой при этом остаётся одним SSH-коннектом, каким и
# был задуман (см. комментарий в deploy_prod.sh про блокировку авторизаций).
#
# Источник правды — файл .deployed_sha, который пишет remote_deploy.sh ПОСЛЕ
# успешной пересборки и health-check. Не `git rev-parse HEAD` на проде: HEAD на
# диске и код в контейнерах расходятся, если кто-то сделал git pull без деплоя.
#
# Запускается по таймеру. Ничего не делает, когда коммит не менялся.
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
LOCAL_DIR="${HOME}/saturday-runs-next"
# Вторая копия кода на том же сервере: из неё работают docker-контейнеры
# воркеров сбора (5 вёрст, S95, RunPark) — docker-compose.home.yml, код
# примонтирован с диска. До 21.09.2026 её этот скрипт не трогал, и после
# выката 3.6.6 воркеры сутки собирали данные старым кодом против новой схемы:
# сайт уехал вперёд, а потолки времени, вынос фетчей из транзакций и пропуск
# неизменных протоколов до них не доехали.
WORKERS_DIR="${WORKERS_DIR:-${HOME}/srs-prod}"
WORKERS_SERVICES="worker-five-verst worker-five-verst-fresh worker-s95 worker-runpark"
VENV="${HOME}/queue-venv"
QUEUE_TIMER="pm-site-queue.timer"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

# Копия воркеров: своя git-копия, обновляется перемоткой (reset --hard тут не
# нужен — локальных правок в отслеживаемых файлах быть не должно, а если они
# появились, лучше остановиться и разобраться, чем затереть молча).
# Вызывается ВСЕГДА, а не только когда обновилась первая копия: они живут
# каждая своей жизнью, и очередь профилей может совпадать с продом, пока
# воркеры отстали (так и было после 3.6.6).
sync_workers_checkout() {
    local target="$1" workers_sha
    if [ ! -d "$WORKERS_DIR/.git" ]; then
        log "копии воркеров в $WORKERS_DIR нет — пропускаю"
        return 0
    fi
    workers_sha=$(git -C "$WORKERS_DIR" rev-parse HEAD 2>/dev/null | tr -d '\r\n')
    if [ "$workers_sha" = "$target" ]; then
        return 0
    fi
    if [ -n "$(git -C "$WORKERS_DIR" status --porcelain --untracked-files=no)" ]; then
        log "ВНИМАНИЕ: в $WORKERS_DIR есть незакоммиченные правки — не трогаю, воркеры остались на $workers_sha"
        return 0
    fi
    # Копия должна сидеть на ветке, а не в detached HEAD: иначе перемотка
    # двигает только HEAD, ветка остаётся позади, и следующий checkout молча
    # вернёт старый код (проверено 21.09.2026).
    if ! git -C "$WORKERS_DIR" symbolic-ref -q HEAD >/dev/null; then
        log "ВНИМАНИЕ: $WORKERS_DIR в detached HEAD — не трогаю, нужна ручная разборка"
        return 0
    fi
    git -C "$WORKERS_DIR" fetch origin --quiet 2>/dev/null
    if git -C "$WORKERS_DIR" merge --ff-only "$target" >/dev/null 2>&1; then
        log "воркеры: код обновлён до $target, перезапускаю контейнеры"
        # Код примонтирован с диска, поэтому достаточно перезапуска —
        # пересборка образа нужна только при смене зависимостей.
        (cd "$WORKERS_DIR" && docker compose -f docker-compose.yml -f docker-compose.home.yml \
            restart $WORKERS_SERVICES >/dev/null 2>&1) \
            || log "ВНИМАНИЕ: перезапуск контейнеров воркеров не прошёл"
    else
        log "ВНИМАНИЕ: $WORKERS_DIR не перематывается на $target — нужна ручная разборка"
    fi
}


# Источник правды переезжает вместе с сайтом: пока он на VPS — маркер там,
# после переезда — в домашнем боевом клоне (его пишет scripts/deploy_home.sh).
# Откат переезда снимает маркер «сайт дома», и сверка снова идёт с VPS.
if [ -f "${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}" ]; then
    remote_sha=$(cat "${HOME_PROD_DIR:-$HOME/srs-prod}/.deployed_sha" 2>/dev/null | tr -d '\r\n')
    log "сайт дома: сверяюсь с домашним маркером"
else
    remote_sha=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" \
        "cat $REMOTE_DIR/.deployed_sha 2>/dev/null" | tr -d '\r\n')
fi
if [ -z "$remote_sha" ]; then
    log "на проде нет маркера .deployed_sha — деплой ещё не писал его, пропускаю"
    exit 0
fi

local_sha=$(cat "$LOCAL_DIR/.deployed_sha" 2>/dev/null | tr -d '\r\n')
if [ "$remote_sha" = "$local_sha" ]; then
    # Очередь совпала — но воркеры могли отстать, проверяем их отдельно.
    sync_workers_checkout "$remote_sha"
    exit 0
fi

log "прод на $remote_sha, у нас $([ -n "$local_sha" ] && echo "$local_sha" || echo "неизвестно") — обновляюсь"

# Пока идёт обновление, разбор очереди должен стоять: иначе прогон начнётся на
# старом коде и продолжится на новом.
was_active=$(systemctl is-active "$QUEUE_TIMER" 2>/dev/null || true)
[ "$was_active" = "active" ] && sudo systemctl stop "$QUEUE_TIMER" 2>/dev/null

pyproject_before=$(md5sum "$LOCAL_DIR/backend/pyproject.toml" 2>/dev/null | cut -d' ' -f1)

# .env* и data/ — наши, локальные: настройки подключения к базе через туннель и
# каталог с картинками. Маска со звёздочкой не случайна: рядом с .env прод
# держит резервные копии вида .env.backup-ГГГГММДД-ЧЧММСС, они принадлежат root
# с правами 600, и rsync на них падает целиком (Permission denied).
SYNC_SRC="$VPS:$REMOTE_DIR/"
[ -f "${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}" ] && SYNC_SRC="${HOME_PROD_DIR:-$HOME/srs-prod}/"
rsync -a --delete \
    --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' \
    --exclude '.env*' --exclude 'data/' \
    -e "ssh -o BatchMode=yes" "$SYNC_SRC" "$LOCAL_DIR/" || {
        log "rsync не прошёл — оставляю как было"
        [ "$was_active" = "active" ] && sudo systemctl start "$QUEUE_TIMER" 2>/dev/null
        exit 1
    }

pyproject_after=$(md5sum "$LOCAL_DIR/backend/pyproject.toml" 2>/dev/null | cut -d' ' -f1)
if [ "$pyproject_before" != "$pyproject_after" ]; then
    log "зависимости изменились — обновляю окружение"
    "$VENV/bin/pip" install -q --disable-pip-version-check -e "$LOCAL_DIR/backend" \
        || log "ВНИМАНИЕ: обновление зависимостей не прошло"
fi

echo "$remote_sha" > "$LOCAL_DIR/.deployed_sha"
log "обновлено до $remote_sha"

sync_workers_checkout "$remote_sha"

[ "$was_active" = "active" ] && sudo systemctl start "$QUEUE_TIMER" 2>/dev/null
log "готово"
