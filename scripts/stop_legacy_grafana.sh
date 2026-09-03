#!/usr/bin/env bash
# Останавливает legacy-контур: Grafana + сборщики, наполняющие старую БД
# five_verst_stats. Запускать НА СЕРВЕРЕ под sudo:
#   sudo bash /opt/saturday-runs-next/scripts/stop_legacy_grafana.sh
#
# Порядок важен: СНАЧАЛА должна стоять прощальная заглушка
# (scripts/install_grafana_farewell.sh) — она убирает proxy_pass на 127.0.0.1:9000.
# Если остановить Grafana раньше, домен будет отдавать 502 вместо страницы
# «дашборды переехали». Скрипт это проверяет и без заглушки не пойдёт (--force,
# если всё-таки надо).
#
# Что делает:
#   1. останавливает и выключает grafana-server.service;
#   2. кладёт снимок root-crontab рядом (/root/crontab.bak-…);
#   3. комментирует строки сборщиков 5 вёрст (/root/scripts/5_verst, date_load_protocol)
#      — данные и сама БД five_verst_stats НЕ трогаются;
#   4. показывает, что осталось работать.
# Откат: sudo crontab /root/crontab.bak-… && sudo systemctl enable --now grafana-server
set -euo pipefail

FORCE=0
KILL_MONITOR=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --with-pg-monitor) KILL_MONITOR=1 ;;
    *) echo "неизвестный ключ: $arg (есть --force, --with-pg-monitor)" >&2; exit 1 ;;
  esac
done

if [[ $EUID -ne 0 ]]; then
  echo "Нужен root: sudo bash $0" >&2
  exit 1
fi

VHOST=/etc/nginx/sites-available/grafana.run5k.run
STAMP=$(date +%Y%m%d-%H%M%S)

echo "=== проверка: стоит ли заглушка вместо прокси ==="
if grep -q "proxy_pass http://127.0.0.1:9000" "$VHOST" 2>/dev/null; then
  if [[ $FORCE -eq 0 ]]; then
    echo "СТОП: vhost всё ещё проксирует на Grafana — после остановки домен отдаст 502." >&2
    echo "  Сначала: sudo bash $(dirname "$0")/install_grafana_farewell.sh" >&2
    echo "  Или запусти с --force, если 502 не смущает." >&2
    exit 1
  fi
  echo "WARN: заглушки нет, идём дальше по --force"
else
  echo "  ок: прокси на 9000 в конфиге нет"
fi

echo "=== Grafana ==="
# Список юнитов читаем В ПЕРЕМЕННУЮ, а не через пайп в grep -q: под `set -o pipefail`
# ранний выход grep'а даёт systemctl SIGPIPE, статус пайплайна становится 141 — и
# условие ложно даже при живом юните (04.09.2026 из-за этого Grafana не остановилась).
UNITS="$(systemctl list-unit-files --no-pager --type=service 2>/dev/null || true)"
if grep -q '^grafana-server\.service' <<<"$UNITS"; then
  systemctl stop grafana-server
  systemctl disable grafana-server
  echo "  grafana-server остановлен и выключен из автозапуска"
else
  echo "  юнита grafana-server нет — пропускаю"
fi
# Бот легаси-контура: по памяти давно мёртв (не достаёт до api.telegram.org),
# но если юнит остался — гасим вместе со всем остальным.
if grep -q '^5verst_bot\.service' <<<"$UNITS"; then
  systemctl stop 5verst_bot || true
  systemctl disable 5verst_bot || true
  echo "  5verst_bot остановлен и выключен"
fi

echo "=== root-crontab: снимок /root/crontab.bak-${STAMP} ==="
crontab -l > "/root/crontab.bak-${STAMP}" 2>/dev/null || : > "/root/crontab.bak-${STAMP}"
wc -l < "/root/crontab.bak-${STAMP}" | sed 's/^/  строк в снимке: /'

echo "=== выключаю сборщики 5 вёрст ==="
# Комментируем, а не удаляем: строки останутся видны с датой и причиной.
PATTERN='(/root/scripts/5_verst|schedule_scripts[.]|date_load_protocol)'
if [[ $KILL_MONITOR -eq 1 ]]; then
  PATTERN="${PATTERN%)}|pg_monitor[.]py)"
fi
crontab -l 2>/dev/null \
  | awk -v pat="$PATTERN" -v stamp="$(date +%d.%m.%Y)" '
      $0 ~ /^[[:space:]]*#/ { print; next }
      $0 ~ pat { print "#ОТКЛЮЧЕНО " stamp " (легаси 5 вёрст остановлено) " $0; next }
      { print }
    ' \
  | crontab -
echo "  теперь отключены:"
crontab -l 2>/dev/null | grep -c "^#ОТКЛЮЧЕНО" | sed 's/^/    строк с меткой ОТКЛЮЧЕНО: /'

echo "=== что осталось в root-crontab (активные строки) ==="
crontab -l 2>/dev/null | grep -vE "^[[:space:]]*(#|$)" | sed 's/^/  /'

echo "=== добиваем уже запущенные процессы сборщиков ==="
pkill -f "schedule_scripts\." 2>/dev/null && echo "  остановлены работавшие schedule_scripts.*" || echo "  работающих schedule_scripts не было"
pkill -f "date_load_protocol" 2>/dev/null && echo "  остановлен date_load_protocol" || true

echo "=== проверка ==="
PORT_OPEN="$(ss -ltn 2>/dev/null | grep -c ":9000" || true)"
if [[ "$PORT_OPEN" != "0" ]]; then
  echo "  порт 9000: ещё слушается — Grafana не остановилась, смотри вывод выше"
else
  echo "  порт 9000: закрыт"
fi
echo -n "  grafana.run5k.run отвечает: "; curl -sS -o /dev/null -w "%{http_code}\n" https://grafana.run5k.run/ || true
echo "  БД five_verst_stats НЕ тронута — данные на месте, сносить отдельным решением."
