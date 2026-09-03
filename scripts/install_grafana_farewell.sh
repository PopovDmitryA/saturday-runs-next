#!/usr/bin/env bash
# Закрывает legacy-Grafana (grafana.run5k.run) прощальной заглушкой.
# Запускать НА СЕРВЕРЕ под sudo:
#   sudo bash scripts/install_grafana_farewell.sh
# или, если файлы приехали отдельно (не через деплой репозитория):
#   sudo SRC=/tmp/grafana-farewell bash /tmp/grafana-farewell/install_grafana_farewell.sh
#
# Что делает:
#   1. кладёт /var/www/grafana-farewell/index.html;
#   2. подменяет vhost grafana.run5k.run (со снимком прежнего рядом);
#   3. проверяет конфиг, при ошибке откатывает и падает;
#   4. перезагружает nginx и проверяет, что заглушка отдаётся на всех адресах.
# Grafana при этом продолжает работать на 127.0.0.1:9000 — снаружи её просто нет.
# Откат: sudo cp <снимок> /etc/nginx/sites-available/grafana.run5k.run && sudo nginx -s reload
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Нужен root: sudo bash $0" >&2
  exit 1
fi

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONF_SRC="$SRC/deploy/nginx/grafana.run5k.run.farewell.conf"
HTML_SRC="$SRC/deploy/grafana/farewell/index.html"
# Плоская раскладка для случая «файлы закинуты в одну папку через scp».
[[ -f "$CONF_SRC" ]] || CONF_SRC="$SRC/grafana.run5k.run.farewell.conf"
[[ -f "$HTML_SRC" ]] || HTML_SRC="$SRC/index.html"

VHOST=/etc/nginx/sites-available/grafana.run5k.run
BACKUP="$VHOST.bak-$(date +%Y%m%d-%H%M%S)"
WWW=/var/www/grafana-farewell
HITS=/var/log/nginx/grafana_hits.log
MARKER="Здесь больше нет дашбордов"

for f in "$CONF_SRC" "$HTML_SRC"; do
  [[ -f "$f" ]] || { echo "не найден: $f (задай SRC=<папка с файлами>)" >&2; exit 1; }
done

echo "=== страница → $WWW/index.html ==="
install -d -m 755 "$WWW"
install -m 644 "$HTML_SRC" "$WWW/index.html"

echo "=== лог событий → $HITS ==="
# Счётчик живёт дальше: по нему видно, кто ещё стучится в закрытые адреса.
[[ -f "$HITS" ]] || install -o www-data -g adm -m 640 /dev/null "$HITS"

echo "=== vhost (снимок прежнего: $BACKUP) ==="
cp -a "$VHOST" "$BACKUP"
cp "$CONF_SRC" "$VHOST"

if ! nginx -t; then
  echo "!!! конфиг не прошёл проверку — откатываюсь" >&2
  cp -a "$BACKUP" "$VHOST"
  nginx -t
  exit 1
fi

systemctl reload nginx
echo "=== nginx перезагружен ==="

# reload возвращает управление раньше, чем новые воркеры подхватят конфиг.
echo "=== проверка: заглушка на корне ==="
for attempt in 1 2 3 4 5; do
  curl -sS --compressed https://grafana.run5k.run/ | grep -q "$MARKER" && break
  sleep 1
done
for url in / /d/ce5xtszxy4074e/glavnaja /api/search /dashboards; do
  if curl -sS --compressed "https://grafana.run5k.run$url" | grep -q "$MARKER"; then
    echo "  OK: $url → заглушка"
  else
    echo "  !!! $url отдаёт не заглушку" >&2
  fi
done

echo "=== проверка: счётчик ещё пишет ==="
curl -sS -o /dev/null -w "  /__count/hit → %{http_code}\n" 'https://grafana.run5k.run/__count/hit?v=selftest&p=/farewell-check&t=selftest'
sleep 1
tail -n 1 "$HITS"
