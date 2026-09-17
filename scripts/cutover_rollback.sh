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
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
say() { echo "$(date '+%H:%M:%S') $*"; }

case "${1:-now}" in

now)
  say "гашу домашний стек (кроме базы — она понадобится для back-dump)"
  cd "$HOME_DIR" && "${COMPOSE[@]}" stop edge nginx api beat bot worker worker-warm \
    worker-s95 worker-five-verst worker-five-verst-user worker-parkrun worker-runpark
  say "поднимаю прод"
  ssh -o BatchMode=yes "$VPS" "cd $REMOTE_DIR && rm -f deploy/nginx/maintenance_current.html && docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram up -d"
  say "ВЕРНИ DNS: A run5k.run → 195.58.34.112, AAAA 2a03:6f00:a::47dc"
  say "проверка: curl -s -o /dev/null -w '%{http_code}' https://run5k.run/"
  ;;

back-dump)
  say "снимаю домашнюю базу и заливаю обратно на прод"
  stamp=$(date +%Y%m%d-%H%M%S); dump="home_back_${stamp}.dump"
  cd "$HOME_DIR"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U "${POSTGRES_USER:-saturday_runs}" \
    -Fc -Z6 "${POSTGRES_DB:-saturday_runs_lk}" > "$DUMP_DIR/$dump" || exit 1
  scp -q -o BatchMode=yes "$DUMP_DIR/$dump" "$VPS:/tmp/$dump" || exit 1
  say "дальше НА ПРОДЕ, под root (база перезаписывается целиком):"
  say "  sudo -u postgres dropdb saturday_runs_lk && sudo -u postgres createdb -O saturday_runs saturday_runs_lk"
  say "  sudo -u postgres pg_restore -d saturday_runs_lk -j 4 --no-owner /tmp/$dump"
  ;;
esac
