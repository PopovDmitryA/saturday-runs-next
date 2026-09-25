#!/usr/bin/env bash
# Разбор очереди profile_fetch_pending сайта run5k.run на домашнем сервере.
#
# Раньше это делалось руками на маке: поднять Chrome, запустить демон, проходить
# капчу глазами. Один исходящий адрес — и первый отказ WAF останавливал пачку,
# поэтому в очереди накопилось больше 1400 профилей.
#
# Здесь то же самое, но без человека: страницы качает httpx, капчу решает CLIP
# (код берётся из соседнего parkrun-monitoring), а ходим через пул VPN-выходов —
# упёрлись в защиту на одном, взяли следующий.
#
# КУДА ПИШЕМ. Пока сайт жил на VPS, базу давал SSH-туннель. После переезда сайта
# домой база — соседний контейнер, и ходить за море незачем. Развилку держит
# файл-маркер ~/.srs-site-at-home (кладёт шаг mark в scripts/cutover_to_home.sh,
# снимает откат — scripts/cutover_rollback.sh now): есть он — работаем с
# локальной базой, нет — по-старому через туннель. Без этой развилки
# очередь продолжила бы писать разобранные профили в БРОШЕННУЮ базу на VPS, и
# потеря была бы молчаливой: прогоны зелёные, а на сайте профилей нет.
# Redis — свой, локальный: в нём только состояние темпа и капчи текущего прогона,
# с продовым его смешивать не нужно.
#
# ГДЕ ЭТО ЖИВЁТ. Скрипт исполняется ТОЛЬКО на домашнем сервере (saturday-run) по
# таймеру `pm-site-queue.timer` — раз в 20 минут от окончания прошлого прогона,
# юнит в /etc/systemd/system/pm-site-queue.service. Код сайта он берёт из
# ~/saturday-runs-next, а тот rsync-ом догоняется до выкаченного на прод коммита
# (scripts/home_sync.sh). Значит, правка здесь доезжает до сервера ТОЛЬКО через
# деплой прода — как и любой другой код, который исполняет домашний сервер.
set -uo pipefail

ROOT="$HOME/saturday-runs-next"
VENV="$HOME/queue-venv"
DB_PORT=5434
SITE_AT_HOME_MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
# Порт локальной базы сайта (docker-compose.home-site.yml публикует её на 127.0.0.1).
HOME_DB_PORT="${HOME_DB_PORT:-5433}"
LIMIT="${LIMIT:-40}"
DELAY="${DELAY:-3}"

log() { echo "$(date '+%H:%M:%S') $*"; }

# --- туннель к базе сайта -------------------------------------------------
tunnel_pid() {
    ss -lntp 2>/dev/null | awk -v p="127.0.0.1:$DB_PORT" '$4==p{print $NF}' \
        | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1
}
open_tunnel() {
    [ -n "$(tunnel_pid)" ] && { log "туннель уже поднят"; return 0; }
    ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -f -N \
        -L "$DB_PORT:127.0.0.1:5432" viewer@195.58.34.112 || return 1
    sleep 2
    [ -n "$(tunnel_pid)" ]
}
close_tunnel() {
    local pid; pid=$(tunnel_pid)
    [ -n "$pid" ] && kill "$pid" 2>/dev/null
}
if [ -f "$SITE_AT_HOME_MARKER" ]; then
    SITE_AT_HOME=1
    log "сайт переехал домой — работаю с локальной базой, туннель не нужен"
else
    SITE_AT_HOME=0
    trap close_tunnel EXIT
    open_tunnel || { log "не поднялся туннель к базе сайта — выхожу"; exit 1; }
    log "туннель к базе сайта готов"
fi

# --- настройки ------------------------------------------------------------
set -a
# shellcheck disable=SC1090
source "$ROOT/.env.prod"
set +a
if [ "$SITE_AT_HOME" = "1" ]; then
    export DATABASE_URL="${DATABASE_URL//@172.17.0.1:5432/@127.0.0.1:$HOME_DB_PORT}"
else
    export DATABASE_URL="${DATABASE_URL//@172.17.0.1:5432/@127.0.0.1:$DB_PORT}"
fi
export REDIS_URL="redis://127.0.0.1:6399/0"
export PARKRUN_MONITORING_DIR="$HOME/parkrun-monitoring"

# Живые выходы: приватные (xray) + GoldmanVPN (sing-box и второй xray).
#
# Слушающего порта мало: нода VPN может быть мертва, а локальный инбаунд всё
# равно принимает коннект и только потом отвечает "General SOCKS server
# failure". Такой выход попадал в список, демон брал его и ложился на нём всей
# пачкой. Поэтому каждый выход проверяем настоящим запросом к parkrun: важен
# сам факт ответа, любой код (405 — это капча, её решает CLIP, выход рабочий).
# Проверки идут параллельно, весь опрос укладывается в таймаут одной.
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
probe_dir=$(mktemp -d)
for port in $(seq 10851 10878); do
    timeout 1 bash -c "</dev/tcp/127.0.0.1/$port" 2>/dev/null || continue
    (
        code=$(curl -s -o /dev/null -m 15 --socks5-hostname "127.0.0.1:$port" \
            -A "$UA" -w '%{http_code}' https://www.parkrun.org.uk/ 2>/dev/null)
        if [ -n "$code" ] && [ "$code" != "000" ]; then
            echo "socks5://127.0.0.1:$port" >"$probe_dir/ok.$port"
        else
            echo "$port" >"$probe_dir/dead.$port"
        fi
    ) &
done
wait
PROXIES=$(cat "$probe_dir"/ok.* 2>/dev/null | paste -sd,)
dead=$(cat "$probe_dir"/dead.* 2>/dev/null | paste -sd,)
rm -rf "$probe_dir"
export PARKRUN_FETCH_PROXIES="$PROXIES"
count=$(awk -F, '{print NF}' <<<"$PROXIES"); [ -z "$PROXIES" ] && count=0
log "исходящих выходов живых: $count${dead:+, не ответили: $dead}"

# Без выходов демон пошёл бы к parkrun с домашнего адреса — этого не хотим:
# один бан по нему аукнется всему, что тут ходит наружу.
if [ "$count" -eq 0 ]; then
    log "ни один выход не ответил — прогон пропускаю, лечи VPN"
    exit 1
fi

# --- прогон ---------------------------------------------------------------
cd "$ROOT/backend" || exit 1
log "запускаю разбор очереди: не больше $LIMIT профилей, задержка $DELAY с"
"$VENV/bin/python" scripts/parkrun_queue_daemon.py \
    --no-browser --solve-captcha \
    --limit "$LIMIT" --fast-delay "$DELAY"
rc=$?
log "готово, код возврата $rc"
exit $rc
