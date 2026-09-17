#!/usr/bin/env bash
# Переезд сайта с VPS на домашний сервер. Шаги — по одному, каждый идемпотентен.
#
#   bash scripts/cutover_to_home.sh preflight   # накануне: что готово, что нет
#   bash scripts/cutover_to_home.sh certs       # забрать сертификаты с прода
#   bash scripts/cutover_to_home.sh media       # первый прогон rsync (373 МБ)
#   bash scripts/cutover_to_home.sh freeze      # заглушка на проде, стоп записи
#   bash scripts/cutover_to_home.sh db          # финальный дамп → восстановление
#   bash scripts/cutover_to_home.sh start       # поднять домашний стек
#   bash scripts/cutover_to_home.sh warm        # прогреть кэши ДО переключения
#   bash scripts/cutover_to_home.sh verify      # проверить дом до перевода DNS
#   ... перевод DNS руками: A → домашний IP, AAAA удалить ...
#   bash scripts/cutover_to_home.sh outside     # проверить снаружи, с прода
#
# Откат: scripts/cutover_rollback.sh (DNS обратно + поднять прод).
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DUMP_DIR="${HOME_DUMP_DIR:-$HOME/srs-prod-db}"
HOME_IP="${HOME_PUBLIC_IP:-95.165.143.93}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)

step="${1:-}"
say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "СТОП: $*" >&2; exit 1; }
cd "$HOME_DIR" 2>/dev/null || die "нет $HOME_DIR"

case "$step" in

preflight)
  say "== готовность =="
  [ -f "$HOME_DIR/.env" ] && say "✓ .env на месте" || say "✗ нет .env"
  # Конфиг прокси к Telegram в git не хранится (секреты) и едет отдельно.
  # Без него бот дома молчит: api.telegram.org из домашней сети недоступен.
  [ -f "$HOME_DIR/deploy/tg-proxy/config.json" ] && say "✓ конфиг tg-proxy на месте" ||
    say "✗ нет deploy/tg-proxy/config.json — забрать с прода"
  sudo -n test -d /etc/letsencrypt/live/run5k.run &&
    say "✓ сертификаты run5k.run на месте" || say "✗ сертификатов нет — шаг certs"
  [ -d "$HOME_DIR/data/og" ] && say "✓ медиа: $(du -sh "$HOME_DIR/data" | cut -f1)" || say "✗ медиа не скопированы — шаг media"
  ttl=$(dig +noall +answer A run5k.run | head -1 | awk '{print $2}')
  [ "${ttl:-999}" -le 120 ] 2>/dev/null && say "✓ TTL $ttl" || say "✗ TTL ${ttl:-?} — снизить до 60 заранее"
  aaaa=$(dig +short AAAA run5k.run | head -1)
  [ -n "$aaaa" ] && say "! AAAA ещё есть ($aaaa) — удалить в момент переключения" || say "✓ AAAA нет"
  "${COMPOSE[@]}" config --quiet && say "✓ compose валиден"
  ;;

certs)
  say "забираю сертификаты"
  # На проде sudo у viewer с паролем, поэтому архив собирает Дмитрий одной
  # командой в своём root-шелле:
  #   tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz
  ssh -o BatchMode=yes "$VPS" 'test -r /tmp/le.tgz' ||
    die "нет /tmp/le.tgz на проде — собери его root-ом: tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz"
  scp -o BatchMode=yes "$VPS:/tmp/le.tgz" /tmp/le.tgz || die "не скачался"
  sudo tar -xzf /tmp/le.tgz -C /etc || die "не распаковался"
  ssh -o BatchMode=yes "$VPS" 'rm -f /tmp/le.tgz'; rm -f /tmp/le.tgz
  sudo ls /etc/letsencrypt/live/ | tr '\n' ' '; echo
  ;;

media)
  # --no-o --no-g: принимающая сторона не root, и попытка сохранить владельца
  # роняет весь прогон (код 23). Плюс сами каталоги дома могли достаться от
  # контейнера, который ходит root-ом, — тогда rsync не сможет в них писать.
  say "синхронизирую data/ (первый прогон — минуты, повторный — секунды)"
  sudo -n chown -R "$(id -u):$(id -g)" "$HOME_DIR/data" 2>/dev/null
  rsync -a --no-o --no-g --info=stats2 -e "ssh -o BatchMode=yes" \
    "$VPS:$REMOTE_DIR/data/" "$HOME_DIR/data/" | tail -4
  ;;

freeze)
  say "== заморозка прода =="
  ssh -o BatchMode=yes "$VPS" "cd $REMOTE_DIR && cp deploy/nginx/maintenance_planned.html deploy/nginx/maintenance_current.html && docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram stop beat bot worker worker-warm worker-five-verst-user worker-parkrun" ||
    die "не удалось остановить фон на проде"
  say "фон на проде остановлен, заглушка подложена; api и nginx ещё отвечают"
  say "ВАЖНО: с этого момента прод только читают — всё, что запишется, потеряется"
  ;;

db)
  t0=$(date +%s)
  stamp=$(date +%Y%m%d-%H%M%S)
  dump="saturday_runs_lk_cutover_${stamp}.dump"
  say "финальный дамп на проде"
  ssh -o BatchMode=yes "$VPS" "sudo -u postgres pg_dump -Fc -Z6 saturday_runs_lk -f /tmp/$dump && sudo chmod 644 /tmp/$dump" || die "дамп не снялся"
  mkdir -p "$DUMP_DIR"
  scp -q -o BatchMode=yes "$VPS:/tmp/$dump" "$DUMP_DIR/$dump" || die "дамп не скачался"
  ssh -o BatchMode=yes "$VPS" "sudo rm -f /tmp/$dump"
  say "дамп дома: $(du -h "$DUMP_DIR/$dump" | cut -f1)"

  "${COMPOSE[@]}" up -d postgres || die "база не поднялась"
  for _ in $(seq 1 60); do "${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-saturday_runs}" >/dev/null 2>&1 && break; sleep 1; done

  # Роль отчётов создаём ДО восстановления: иначе 40 GRANT'ов отваливаются и
  # внутренний отчётный доступ приезжает без прав (проверено на репетиции).
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d "${POSTGRES_DB:-saturday_runs_lk}" \
    -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname='report_ro') then create role report_ro login password '${REPORT_RO_PASSWORD:-change-me}'; end if; end \$\$;" >/dev/null

  say "восстанавливаю"
  "${COMPOSE[@]}" exec -T postgres pg_restore -U "${POSTGRES_USER:-saturday_runs}" \
    -d "${POSTGRES_DB:-saturday_runs_lk}" -j 6 --no-owner "/dump/$dump" 2>&1 | tail -2
  rows=$("${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d "${POSTGRES_DB:-saturday_runs_lk}" -At -c "select count(*) from run_results")
  say "run_results дома: $rows; весь шаг занял $(( $(date +%s) - t0 )) с"
  ;;

start)
  say "поднимаю домашний стек"
  "${COMPOSE[@]}" up -d --build || die "стек не поднялся"
  "${COMPOSE[@]}" run --rm -T api alembic upgrade head </dev/null | tail -2
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' -H 'Host: run5k.run' http://127.0.0.1/health)
    [ "$code" = "200" ] && { say "✓ health 200 через край"; break; }
    sleep 2
  done
  ;;

warm)
  say "прогреваю кэши ДО перевода DNS (холодная главная считается ~34 с)"
  for u in /api/portal/home /api/locations/index /api/fastest /api/location-records /api/protocol/week; do
    printf '  %-26s' "$u"
    curl -s -o /dev/null -m 120 -w '%{http_code} за %{time_total}s\n' -H 'Host: run5k.run' "http://127.0.0.1$u"
  done
  ;;

verify)
  say "== дом до переключения DNS =="
  for u in / /health /api/locations/index /api/portal/home; do
    printf '  %-24s' "$u"
    curl -s -o /dev/null -m 60 -w '%{http_code} за %{time_total}s\n' -H 'Host: run5k.run' "http://127.0.0.1$u"
  done
  say "теперь DNS: A run5k.run → $HOME_IP, AAAA удалить (дома IPv6 нет)"
  ;;

outside)
  say "== снаружи (глазами прода) =="
  ssh -o BatchMode=yes "$VPS" "curl -s -o /dev/null -m 15 -w 'https://run5k.run/ %{http_code} за %{time_total}s\n' https://run5k.run/ --resolve run5k.run:443:$HOME_IP"
  dig +short A run5k.run | sed 's/^/  A: /'
  dig +short AAAA run5k.run | sed 's/^/  AAAA (должно быть пусто): /'
  ;;

*)
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
  ;;
esac
