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
# Две копии кода, две части:
#   * ~/saturday-runs-next — разбор очереди профилей (pm-site-queue), rsync с прода;
#   * ~/srs-prod — воркеры сбора (5 вёрст, S95, RunPark) в docker-compose.home.yml.
#     Её ведёт home_workers_sync.sh; зовём его на КАЖДОМ заходе, а не только когда
#     обновилась первая копия: они живут каждая своей жизнью (после 3.6.6 очередь
#     совпадала с продом, а воркеры сутки собирали данные старым кодом).
#
# Заминки — тревогой админу в Telegram (scripts/lib/home_alert.sh). 24.09.2026
# воркеры полтора суток стояли на старом коде, а знал об этом только journal.
#
# Запускается по таймеру. Ничего не делает, когда коммит не менялся.
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
LOCAL_DIR="${HOME}/saturday-runs-next"
WORKERS_DIR="${WORKERS_DIR:-${HOME}/srs-prod}"
VENV="${HOME}/queue-venv"
QUEUE_TIMER="pm-site-queue.timer"
QUEUE_SERVICE="pm-site-queue.service"
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
# Отпечаток pyproject, под который venv очереди собран успешно. Лежит вне
# LOCAL_DIR: rsync --delete его бы стёр.
VENV_STAMP="${XDG_STATE_HOME:-$HOME/.local/state}/srs-home-sync/queue-venv.pyproject.md5"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
# shellcheck source=scripts/lib/home_alert.sh
. "$SCRIPT_DIR/lib/home_alert.sh"

sync_workers() {
    HOME_PROD_DIR="$WORKERS_DIR" bash "$SCRIPT_DIR/home_workers_sync.sh" --target "$1"
}

# Источник правды переезжает вместе с сайтом: пока он на VPS — маркер там,
# после переезда — в домашнем боевом клоне (его пишет scripts/deploy_home.sh).
# Откат переезда снимает маркер «сайт дома», и сверка снова идёт с VPS.
if [ -f "$MARKER" ]; then
    remote_sha=$(cat "$WORKERS_DIR/.deployed_sha" 2>/dev/null | tr -d '\r\n')
    log "сайт дома: сверяюсь с домашним маркером"
else
    # Удалённый cat не должен ронять ssh: код возврата — только про связь.
    if ! remote_sha=$(ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" \
        "cat $REMOTE_DIR/.deployed_sha 2>/dev/null || true"); then
        # ssh моргает (фильтр Timeweb, tailnet): тревога — только если связи нет
        # полчаса подряд, то есть шесть заходов.
        n=$(streak_inc vps-ssh)
        log "прод недоступен по ssh (заход $n подряд) — сверить не с чем"
        [ "$n" -ge 6 ] && alert vps-ssh "прод не отвечает по ssh уже $((n * 5)) минут — домашний сервер не может сверить код с продом"
        exit 0
    fi
    streak_reset vps-ssh
    alert_clear vps-ssh "связь с продом по ssh восстановилась"
    remote_sha=$(tr -d '\r\n' <<<"$remote_sha")
fi
if [ -z "$remote_sha" ]; then
    log "маркера .deployed_sha нет — деплой ещё не писал его, пропускаю"
    exit 0
fi

local_sha=$(cat "$LOCAL_DIR/.deployed_sha" 2>/dev/null | tr -d '\r\n')
if [ "$remote_sha" = "$local_sha" ]; then
    # Очередь совпала — но воркеры могли отстать, проверяем их отдельно.
    # Код возврата — от них: застрявшие воркеры видны и в systemctl --failed.
    sync_workers "$remote_sha"
    exit $?
fi

# Идёт прогон очереди профилей — ждём его конца: rsync и pip под работающим
# демоном поменяли бы ему код посреди прогона. Прогон — до четверти часа,
# следующий заход через пять минут; дольше часа — это уже зависание.
if systemctl is-active --quiet "$QUEUE_SERVICE" 2>/dev/null; then
    n=$(streak_inc queue-busy)
    log "идёт разбор очереди профилей — обновлю код в следующий заход"
    [ "$n" -ge 12 ] && alert queue-busy "разбор очереди профилей идёт дольше часа — код очереди не обновляется до ${remote_sha:0:7}"
    sync_workers "$remote_sha"
    exit $?
fi
streak_reset queue-busy

log "прод на $remote_sha, у нас $([ -n "$local_sha" ] && echo "$local_sha" || echo "неизвестно") — обновляюсь"

# Пока идёт обновление, разбор очереди должен стоять: иначе прогон начнётся на
# старом коде и продолжится на новом. Таймер возвращается при ЛЮБОМ выходе:
# раньше упавший посреди скрипт оставлял его выключенным, а следующие заходы
# видели «неактивен» и так и не включали обратно.
was_active=$(systemctl is-active "$QUEUE_TIMER" 2>/dev/null || true)
restore_queue_timer() {
    [ "$was_active" = "active" ] && sudo systemctl start "$QUEUE_TIMER" 2>/dev/null
    return 0
}
if [ "$was_active" = "active" ]; then
    sudo systemctl stop "$QUEUE_TIMER" 2>/dev/null
    trap restore_queue_timer EXIT
fi

# .env* и data/ — наши, локальные: настройки подключения к базе через туннель,
# каталог с картинками и журнал демона. Маска со звёздочкой у .env не случайна:
# рядом с .env прод держит резервные копии вида .env.backup-ГГГГММДД-ЧЧММСС, они
# принадлежат root с правами 600, и rsync на них падает целиком (Permission
# denied). data/ — от корня копии: без ведущего «/» шаблон ловил и каталог
# кода backend/app/parkrun/data/ (каталог площадок parkrun для разбора
# профилей), и его обновления до очереди не доезжали.
SYNC_SRC="$VPS:$REMOTE_DIR/"
[ -f "$MARKER" ] && SYNC_SRC="$WORKERS_DIR/"
if ! rsync -a --delete \
    --exclude '.git/' --exclude 'node_modules/' --exclude '__pycache__/' \
    --exclude '.env*' --exclude '/data/' --exclude '/backend/data/' \
    -e "ssh -o BatchMode=yes" "$SYNC_SRC" "$LOCAL_DIR/"; then
    alert queue-rsync "не удалось скопировать код прода в $LOCAL_DIR — очередь профилей остаётся на ${local_sha:0:7}"
    exit 1
fi

# venv сверяем не «до и после rsync», а с отпечатком последней УСПЕШНОЙ
# установки: иначе упавший pip на следующем заходе не повторялся бы — файл уже
# скопирован, разницы нет, — и сломанное окружение так и оставалось.
pyproject_now=$(md5sum "$LOCAL_DIR/backend/pyproject.toml" 2>/dev/null | cut -d' ' -f1)
if [ "$pyproject_now" != "$(cat "$VENV_STAMP" 2>/dev/null)" ]; then
    log "зависимости изменились — обновляю окружение"
    if ! "$VENV/bin/pip" install -q --disable-pip-version-check -e "$LOCAL_DIR/backend"; then
        # Маркер не пишем: следующий заход увидит расхождение и повторит.
        alert queue-pip "не обновилось окружение очереди профилей (pip) под ${remote_sha:0:7} — повторю через 5 минут"
        exit 1
    fi
    mkdir -p "$(dirname "$VENV_STAMP")" && echo "$pyproject_now" > "$VENV_STAMP"
fi

echo "$remote_sha" > "$LOCAL_DIR/.deployed_sha"
log "обновлено до $remote_sha"
alert_clear queue- "очередь профилей снова на коде прода (${remote_sha:0:7})"

sync_workers "$remote_sha"
rc=$?
log "готово"
exit "$rc"
