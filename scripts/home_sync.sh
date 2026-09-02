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
VENV="${HOME}/queue-venv"
QUEUE_TIMER="pm-site-queue.timer"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

remote_sha=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" \
    "cat $REMOTE_DIR/.deployed_sha 2>/dev/null" | tr -d '\r\n')
if [ -z "$remote_sha" ]; then
    log "на проде нет маркера .deployed_sha — деплой ещё не писал его, пропускаю"
    exit 0
fi

local_sha=$(cat "$LOCAL_DIR/.deployed_sha" 2>/dev/null | tr -d '\r\n')
if [ "$remote_sha" = "$local_sha" ]; then
    log "совпадаем с продом ($remote_sha), делать нечего"
    exit 0
fi

log "прод на $remote_sha, у нас $([ -n "$local_sha" ] && echo "$local_sha" || echo "неизвестно") — обновляюсь"

# Пока идёт обновление, разбор очереди должен стоять: иначе прогон начнётся на
# старом коде и продолжится на новом.
was_active=$(systemctl is-active "$QUEUE_TIMER" 2>/dev/null || true)
[ "$was_active" = "active" ] && sudo systemctl stop "$QUEUE_TIMER" 2>/dev/null

pyproject_before=$(md5sum "$LOCAL_DIR/backend/pyproject.toml" 2>/dev/null | cut -d' ' -f1)

# .env и data/ — наши, локальные: настройки подключения к базе через туннель и
# каталог с картинками. Их с прода не тянем.
rsync -a --delete \
    --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' \
    --exclude '.env' --exclude '.env.prod' --exclude 'data/' \
    -e "ssh -o BatchMode=yes" "$VPS:$REMOTE_DIR/" "$LOCAL_DIR/" || {
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

[ "$was_active" = "active" ] && sudo systemctl start "$QUEUE_TIMER" 2>/dev/null
log "готово"
