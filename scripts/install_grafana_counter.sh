#!/usr/bin/env bash
# Ставит на прод счётчик посещений legacy-Grafana (grafana.run5k.run).
# Запускать НА СЕРВЕРЕ под sudo:
#   sudo bash scripts/install_grafana_counter.sh
# или, если файлы приехали отдельно (не через деплой репозитория):
#   sudo SRC=/tmp/grafana-counter bash /tmp/grafana-counter/install_grafana_counter.sh
#
# Что делает:
#   1. кладёт /var/www/grafana-counter/count.js;
#   2. подменяет vhost grafana.run5k.run (со снимком прежнего рядом);
#   3. проверяет конфиг, при ошибке откатывает и падает;
#   4. перезагружает nginx и проверяет, что счётчик реально вставился в HTML.
# Откат: sudo cp <снимок> /etc/nginx/sites-available/grafana.run5k.run && sudo nginx -s reload
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Нужен root: sudo bash $0" >&2
  exit 1
fi

SRC="${SRC:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONF_SRC="$SRC/deploy/nginx/grafana.run5k.run.conf"
JS_SRC="$SRC/deploy/grafana/counter/count.js"
# Плоская раскладка для случая «файлы закинуты в одну папку через scp».
[[ -f "$CONF_SRC" ]] || CONF_SRC="$SRC/grafana.run5k.run.conf"
[[ -f "$JS_SRC" ]] || JS_SRC="$SRC/count.js"

VHOST=/etc/nginx/sites-available/grafana.run5k.run
BACKUP="$VHOST.bak-$(date +%Y%m%d-%H%M%S)"
WWW=/var/www/grafana-counter
HITS=/var/log/nginx/grafana_hits.log

for f in "$CONF_SRC" "$JS_SRC"; do
  [[ -f "$f" ]] || { echo "не найден: $f (задай SRC=<папка с файлами>)" >&2; exit 1; }
done

echo "=== скрипт счётчика → $WWW/count.js ==="
install -d -m 755 "$WWW"
install -m 644 "$JS_SRC" "$WWW/count.js"

echo "=== лог событий → $HITS ==="
# Права как у остальных логов nginx: читает группа adm, пишет www-data.
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

echo "=== проверка: скрипт отдаётся ==="
curl -sS -o /dev/null -w "  /__count.js → %{http_code} %{content_type}\n" https://grafana.run5k.run/__count.js

echo "=== проверка: скрипт вставлен в HTML дашборда ==="
if curl -sS --compressed https://grafana.run5k.run/d/ce5xtszxy4074e/glavnaja | grep -q '/__count.js'; then
  echo "  OK: sub_filter сработал"
else
  echo "  !!! в HTML нет /__count.js — sub_filter не сработал, смотри Accept-Encoding и sub_filter в vhost" >&2
fi

echo "=== проверка: событие попадает в лог ==="
curl -sS -o /dev/null -w "  /__count/hit → %{http_code}\n" 'https://grafana.run5k.run/__count/hit?v=selftest&p=/install-check&t=selftest'
sleep 1
tail -n 1 "$HITS"

echo
echo "Готово. Отчёт: python3 scripts/grafana_usage_report.py (на сервере) или --remote с Мака."
