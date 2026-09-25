# shellcheck shell=bash
# Тревоги домашних скриптов (home_sync.sh, home_workers_sync.sh) админу в Telegram.
#
# Зачем. 24.09.2026 воркеры сбора застряли на старом коде на полтора суток:
# синхронизация каждые пять минут писала «не трогаю» в journal — и больше никуда.
# Рассылка отмен стартов из 3.7.1 всё это время не работала, заметили случайно.
#
# Как. Отправляем тем же notify_admin, что и сайт (Telegram, фолбэк в ВК), из
# контейнера воркера сбора: там настройки и прокси до Telegram, которого из
# домашней сети напрямую не видно. Один ключ — одна тревога: повтор не чаще
# ALERT_REPEAT_HOURS, пока проблема держится; когда прошла — одно сообщение
# «восстановлено» и снятие ключа.
#
# Требует функцию log() в вызывающем скрипте.

ALERT_STATE_DIR="${ALERT_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/srs-home-alerts}"
ALERT_REPEAT_HOURS="${ALERT_REPEAT_HOURS:-6}"

_alert_send() {
    local text="$1" dir svc
    local py='import sys; from app.services.admin_notify import notify_admin; sys.exit(0 if notify_admin(sys.stdin.read()) else 1)'
    local -a compose
    dir="${HOME_PROD_DIR:-$HOME/srs-prod}"
    # До переезда воркеры живут в стеке srs-prod, после — в общем srs_home.
    if [ -f "${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}" ]; then
        compose=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml)
        svc=worker
    else
        compose=(docker compose -f docker-compose.yml -f docker-compose.home.yml)
        svc=worker-s95
    fi
    # exec — в работающий контейнер; не вышло (воркер лежит) — разовый из того
    # же образа: тревога нужнее всего как раз тогда, когда воркеры не работают.
    (cd "$dir" 2>/dev/null &&
        { printf '%s' "$text" | "${compose[@]}" exec -T "$svc" python -c "$py" >/dev/null 2>&1 ||
          printf '%s' "$text" | "${compose[@]}" run --rm -T --no-deps "$svc" python -c "$py" >/dev/null 2>&1; })
}

# alert <ключ> <текст> — в журнал всегда, в Telegram — первый раз и потом не
# чаще раза в ALERT_REPEAT_HOURS.
alert() {
    local key="$1" text="$2" f now last
    log "ВНИМАНИЕ: $text"
    mkdir -p "$ALERT_STATE_DIR" 2>/dev/null || return 0
    f="$ALERT_STATE_DIR/$key"
    now=$(date +%s)
    last=$(cat "$f" 2>/dev/null || echo 0)
    [ $((now - last)) -ge $((ALERT_REPEAT_HOURS * 3600)) ] || return 0
    if _alert_send "⚠️ Домашний сервер: $text"; then
        echo "$now" >"$f"
    else
        log "   (тревога в Telegram не ушла — попробую в следующий заход)"
    fi
}

# alert_clear <префикс ключей> <текст> — проблема прошла: снять ключи и, если
# о ней успели сказать, сообщить, что всё в порядке.
alert_clear() {
    local prefix="$1" text="$2" f said=""
    for f in "$ALERT_STATE_DIR/$prefix"*; do
        [ -e "$f" ] || continue
        case "$f" in */streak.*) continue ;; esac
        said=1
        rm -f "$f"
    done
    [ -n "$said" ] || return 0
    log "$text"
    _alert_send "✅ Домашний сервер: $text" || true
}

# streak_inc <ключ> — сколько заходов подряд держится проблема. Для сбоев,
# которые сами проходят (ssh до VPS моргнул), тревога — только после серии.
streak_inc() {
    local f="$ALERT_STATE_DIR/streak.$1" n
    mkdir -p "$ALERT_STATE_DIR" 2>/dev/null
    n=$(($(cat "$f" 2>/dev/null || echo 0) + 1))
    echo "$n" >"$f" 2>/dev/null
    echo "$n"
}

streak_reset() {
    rm -f "$ALERT_STATE_DIR/streak.$1" 2>/dev/null
}
