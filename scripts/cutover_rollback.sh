#!/usr/bin/env bash
# Откат переезда: вернуть сайт на VPS.
#
# Работает, пока прод цел — а он остаётся целым до 21 октября. Цена отката
# зависит от того, сколько сайт прожил дома: всё, что люди записали дома,
# на проде отсутствует. Поэтому в первые минуты откат бесплатный, через час
# он уже означает перенос свежих данных обратно (шаг back-dump).
#
#   bash scripts/cutover_rollback.sh now        # поднять прод как было
#   bash scripts/cutover_rollback.sh back-dump  # если дома уже писали
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DUMP_DIR="${HOME_DUMP_DIR:-$HOME/srs-prod-db}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
say() { echo "$(date '+%H:%M:%S') $*"; }

case "${1:-now}" in

now)
  say "гашу домашний стек (кроме базы — она понадобится для back-dump)"
  # Первым делом — край: пока он слушает 80/443, к нам продолжают приходить
  # те, у кого DNS ещё не обновился, и пишут в базу, которую мы бросаем.
  cd "$HOME_DIR" && "${COMPOSE[@]}" stop edge nginx api beat bot worker worker-warm \
    worker-s95 worker-five-verst worker-five-verst-fresh worker-five-verst-user \
    worker-parkrun worker-runpark
  say "поднимаю прод"
  ssh -o BatchMode=yes "$VPS" "cd $REMOTE_DIR && rm -f deploy/nginx/maintenance_on deploy/nginx/maintenance_current.html && docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram up -d"
  code=$(curl -s -o /dev/null -m 20 -w '%{http_code}' --resolve run5k.run:443:195.58.34.112 https://run5k.run/ || true)
  say "прод отвечает: $code (проверено в обход DNS)"
  say "ВЕРНИ DNS: A run5k.run → 195.58.34.112, AAAA 2a03:6f00:a::47dc"
  say "проверка: curl -s -o /dev/null -w '%{http_code}' https://run5k.run/"
  ;;

back-dump)
  say "снимаю домашнюю базу и заливаю обратно на прод"
  stamp=$(date +%Y%m%d-%H%M%S)
  dump="home_back_${stamp}.sql.gz"; ro_sql="home_back_${stamp}_report_ro.sql"
  cd "$HOME_DIR"
  # Обычный SQL, а не -Fc: архив pg_dump 16 (формат 1.15) pg_restore 14 на VPS
  # не читает — «unsupported version (1.15) in file header», причём уже после
  # dropdb. Владельцев и права не везём: объекты создаёт сама роль сайта
  # (SET ROLE ниже), права report_ro выдаёт её скрипт.
  "${COMPOSE[@]}" exec -T postgres pg_dump -U "${POSTGRES_USER:-saturday_runs}" \
    --no-owner --no-acl "${POSTGRES_DB:-saturday_runs_lk}" | gzip > "$DUMP_DIR/$dump" || exit 1
  scp -q -o BatchMode=yes "$DUMP_DIR/$dump" "$VPS:/tmp/$dump" || exit 1
  # Скрипт роли везём из домашнего клона: его список закрытых таблиц должен
  # совпадать со схемой этой базы, а на проде лежит код последнего деплоя.
  scp -q -o BatchMode=yes scripts/create_report_ro_role.sql "$VPS:/tmp/$ro_sql" || exit 1
  # Обрезанный файл psql может доиграть без единой ошибки (обрыв на границе
  # строки) и закоммитить полбазы — проверяем до того, как сносить базу прода.
  ssh -o BatchMode=yes "$VPS" "gzip -t /tmp/$dump" || { say "дамп на проде битый — не восстанавливать"; exit 1; }
  say "дамп на проде цел: $(du -h "$DUMP_DIR/$dump" | cut -f1)"
  # Без SET ROLE всё восстановленное досталось бы postgres, а права владельца
  # pg_dump не пишет: сайт получил бы permission denied на каждую таблицу,
  # миграции — must be owner. --force: после шага now api и воркеры прода
  # держат соединения, и простой dropdb отказывает. -1: упало — база пустая,
  # команду можно повторить.
  say "дальше НА ПРОДЕ, под root (база перезаписывается целиком):"
  say "  touch $REMOTE_DIR/deploy/nginx/maintenance_on"
  say "  sudo -u postgres dropdb --force saturday_runs_lk && sudo -u postgres createdb -O saturday_runs saturday_runs_lk"
  say "  zcat /tmp/$dump | sudo -u postgres psql -X -1 -v ON_ERROR_STOP=1 -d saturday_runs_lk -c 'SET ROLE saturday_runs' -f - >/dev/null"
  say "  sudo -u postgres psql -X -q -d saturday_runs_lk -v ON_ERROR_STOP=1 -f - < /tmp/$ro_sql"
  say "  rm -f $REMOTE_DIR/deploy/nginx/maintenance_on"
  ;;
esac
