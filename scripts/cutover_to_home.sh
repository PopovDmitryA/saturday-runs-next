#!/usr/bin/env bash
# Переезд сайта с VPS на домашний сервер. Шаги — по одному, каждый идемпотентен.
#
#   bash scripts/cutover_to_home.sh preflight   # накануне: что готово, что нет
#   bash scripts/cutover_to_home.sh certs       # забрать сертификаты с прода
#   bash scripts/cutover_to_home.sh media       # первый прогон rsync (373 МБ)
#   bash scripts/cutover_to_home.sh build       # фронт и образы — ДО заморозки
#   bash scripts/cutover_to_home.sh freeze      # заглушка на проде, стоп записи (VPS и дом)
#   bash scripts/cutover_to_home.sh db          # финальный дамп → восстановление, Redis
#   bash scripts/cutover_to_home.sh start       # поднять ВЕБ-часть (безопасно)
#   bash scripts/cutover_to_home.sh mark        # маркер «сайт дома» + стоп старых воркеров
#   bash scripts/cutover_to_home.sh start-full  # фон: бот, beat, воркеры — ТОЛЬКО после mark
#   bash scripts/cutover_to_home.sh warm        # прогреть кэши ДО переключения
#   bash scripts/cutover_to_home.sh verify      # проверить дом до перевода DNS
#   ... перевод DNS руками: A run5k.run, www, app, grafana → домашний IP;
#       AAAA у run5k.run и www удалить (дома IPv6 нет) ...
#   bash scripts/cutover_to_home.sh outside     # проверить снаружи, с прода
#
# Откат: scripts/cutover_rollback.sh (поднять прод, отменить mark, DNS обратно).
#
# Шаги проверяют порядок сами: db, mark и start-full отказываются работать не
# вовремя (до freeze, после mark, без mark), потому что каждый из них в
# неурочный момент стоит данных или второго бота на боевом токене.
set -uo pipefail

VPS="${VPS_HOST:-viewer@195.58.34.112}"
REMOTE_DIR="${VPS_DIR:-/opt/saturday-runs-next}"
HOME_DIR="${HOME_PROD_DIR:-$HOME/srs-prod}"
DUMP_DIR="${HOME_DUMP_DIR:-$HOME/srs-prod-db}"
HOME_IP="${HOME_PUBLIC_IP:-95.165.143.93}"
MARKER="${SITE_AT_HOME_MARKER:-$HOME/.srs-site-at-home}"
COMPOSE=(docker compose -p "${HOME_PROJECT:-srs_home}" -f docker-compose.yml -f docker-compose.home-site.yml --profile telegram)
# Стек домашних воркеров сбора, живущий до переезда (docker-compose.home.yml).
WORKERS_COMPOSE=(docker compose -p srs-prod -f docker-compose.yml -f docker-compose.home.yml)
WORKERS="worker-s95 worker-five-verst worker-five-verst-fresh worker-runpark"
VPS_COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile telegram"
HOSTS="run5k.run www.run5k.run app.run5k.run grafana.run5k.run"
BUILD_STAMP="$HOME_DIR/.frontend-build-sha"

step="${1:-}"
say() { echo "$(date '+%H:%M:%S') $*"; }
die() { echo "СТОП: $*" >&2; exit 1; }
vps() { ssh -o BatchMode=yes -o ConnectTimeout=20 "$VPS" "$@"; }
# Ночной бэкап базы дома — scripts/home_pg_backup.sh по таймеру srs-pg-backup.
# Таймер ставится руками и один раз; до шага mark бэкап молчит (нет маркера),
# поэтому включать его можно заранее.
backup_timer() {
  systemctl is-active --quiet srs-pg-backup.timer &&
    say "✓ таймер домашнего бэкапа базы включён (srs-pg-backup.timer, 23:30)" ||
    say "✗ таймер домашнего бэкапа базы не включён: sudo cp $HOME_DIR/deploy/systemd/srs-pg-backup.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now srs-pg-backup.timer"
}
require_not_at_home() {
  [ -f "$MARKER" ] && die "сайт уже дома (есть $MARKER) — этот шаг снёс бы живые данные. Откат: scripts/cutover_rollback.sh"
  return 0
}
# Прод заморожен шагом freeze: на VPS лежит метка .site-moved-home.
require_frozen() {
  vps "test -f $REMOTE_DIR/.site-moved-home" ||
    die "прод не заморожен (нет $REMOTE_DIR/.site-moved-home на VPS, или ssh не ответил) — сначала freeze"
}
psql_home() {
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d "${POSTGRES_DB:-saturday_runs_lk}" "$@"
}
# Число строк в каждой таблице схемы public, по строке «таблица|число»:
# запросы собирает сам psql (\gexec), имена экранирует format(%I).
COUNTS_SQL="select format('select %L || ''|'' || count(*) from public.%I', tablename, tablename) from pg_tables where schemaname = 'public' order by tablename \\gexec"
cd "$HOME_DIR" 2>/dev/null || die "нет $HOME_DIR"

case "$step" in
preflight)
  say "== готовность =="
  [ -f "$HOME_DIR/.env" ] && say "✓ .env на месте" || say "✗ нет .env"
  # Конфиг прокси к Telegram в git не хранится (секреты) и едет отдельно.
  # Без него бот дома молчит: api.telegram.org из домашней сети недоступен.
  [ -f "$HOME_DIR/deploy/tg-proxy/config.json" ] && say "✓ конфиг tg-proxy на месте" ||
    say "✗ нет deploy/tg-proxy/config.json — забрать с прода"
  # Шаг db создаёт report_ro с паролем из REPORT_DATABASE_URL (или REPORT_RO_PASSWORD).
  grep -qE '^(REPORT_RO_PASSWORD=.|REPORT_DATABASE_URL=[^:]+://[^:@/]+:[^@]+@)' "$HOME_DIR/.env" &&
    say "✓ пароль report_ro есть в .env" ||
    say "✗ нет пароля report_ro в .env — отчёты и выборки для дайджеста дома не войдут"
  # Переезд и откат идут из этого клона: он обязан стоять ровно на коде VPS,
  # без правок (24.09.2026 в нём остались правки репетиции, и воркеры сбора
  # полтора суток не обновлялись).
  vps_sha=$(vps "cat $REMOTE_DIR/.deployed_sha 2>/dev/null || true" | tr -d '\r\n')
  head=$(git rev-parse HEAD)
  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    say "✗ в клоне незакоммиченные правки — git -C $HOME_DIR status"
  elif [ -z "$vps_sha" ]; then
    say "✗ не узнал коммит VPS (ssh или .deployed_sha)"
  elif [ "$head" = "$vps_sha" ]; then
    say "✓ клон на коммите прода ${head:0:7}"
  else
    say "✗ клон на ${head:0:7}, прод на ${vps_sha:0:7} — выкатить прод, pm-home-sync догонит клон за 5 минут"
  fi
  [ "$(cat "$BUILD_STAMP" 2>/dev/null)" = "$head" ] && say "✓ фронт собран под ${head:0:7}" ||
    say "✗ фронт не собран под ${head:0:7} — шаг build (иначе люди получат интерфейс со старой сборки)"
  sudo -n test -d /etc/letsencrypt/live/run5k.run || say "✗ сертификатов нет — шаг certs"
  for n in run5k.run app.run5k.run grafana.run5k.run; do
    end=$(sudo -n openssl x509 -enddate -noout -in "/etc/letsencrypt/live/$n/fullchain.pem" 2>/dev/null | cut -d= -f2)
    [ -n "$end" ] && say "✓ сертификат $n до $end" || say "✗ нет сертификата $n — шаг certs"
  done
  [ -d "$HOME_DIR/data/og" ] && say "✓ медиа: $(du -sh "$HOME_DIR/data" | cut -f1)" || say "✗ медиа не скопированы — шаг media"
  backup_timer
  # Край обязан слушать наружу: EDGE_BIND=127.0.0.1 остался бы от репетиции.
  [ "${EDGE_BIND:-0.0.0.0}" = "0.0.0.0" ] && say "✓ край будет слушать 0.0.0.0:80/443" ||
    say "✗ EDGE_BIND=$EDGE_BIND — снаружи сайта не будет: unset EDGE_BIND"
  busy=$(ss -tlnH 2>/dev/null | awk '{print $4}' | grep -E ':(80|443)$' | tr '\n' ' ')
  [ -z "$busy" ] && say "✓ порты 80/443 свободны" || say "! 80/443 уже слушают: $busy (свой край srs_home — это нормально)"
  # TTL — у самих DNS-серверов домена (у Timeweb их четыре), а не в кэше
  # резолвера: кэш показывает остаток, а зеркала .org отстают от .ru (25.09.2026
  # у них был serial 16 против 19, и TTL 600 против 60). AAAA проверяем тоже:
  # их удаляют в момент переключения, и с TTL 600 посетители с IPv6 ещё
  # 10 минут шли бы на VPS.
  nss=$(dig +short NS run5k.run | tr '\n' ' ')
  for h in $HOSTS; do
    for rr in A AAAA; do
      ttls=$(for ns in $nss; do dig -4 @"$ns" +noall +answer +time=4 +tries=1 "$rr" "$h" | awk '{print $2}'; done | sort -un | tr '\n' ' ')
      [ -n "$ttls" ] || continue
      min=$(echo $ttls | awk '{print $1}'); max=$(echo $ttls | awk '{print $NF}')
      addr=$(dig +short "$rr" "$h" | head -1)
      if [ "$max" -le 120 ]; then t="✓ TTL $max"
      elif [ "$min" -le 120 ]; then t="✗ серверы домена расходятся (TTL ${ttls% }) — зеркала ещё не догнали, проверить позже"
      else t="✗ TTL $max — снизить до 60 заранее (панель Timeweb)"; fi
      say "  $h $rr $addr: $t"
    done
  done
  say "  AAAA у run5k.run и www удаляются в момент переключения (дома IPv6 нет)"
  [ -f "$MARKER" ] && say "! маркер «сайт дома» уже стоит" || say "✓ маркера «сайт дома» нет"
  "${COMPOSE[@]}" config --quiet && say "✓ compose валиден"
  ;;

certs)
  say "забираю сертификаты"
  # На проде sudo у viewer с паролем, поэтому архив собирает Дмитрий одной
  # командой в своём root-шелле:
  #   tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz
  vps 'test -r /tmp/le.tgz' ||
    die "нет /tmp/le.tgz на проде — собери его root-ом: tar -czf /tmp/le.tgz -C /etc letsencrypt && chmod 644 /tmp/le.tgz"
  scp -o BatchMode=yes "$VPS:/tmp/le.tgz" /tmp/le.tgz || die "не скачался"
  sudo tar -xzf /tmp/le.tgz -C /etc || die "не распаковался"
  vps 'rm -f /tmp/le.tgz'; rm -f /tmp/le.tgz
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

build)
  # Всё, что долго, — до заморозки. Раньше фронт в сценарии не собирался вовсе:
  # на переезде люди получили бы сборку от 17.09 поверх нового API. Образы с
  # Chromium для parkrun собираются минутами — в окне простоя им не место.
  require_not_at_home
  sha=$(git rev-parse HEAD)
  say "собираю фронт под ${sha:0:7} (node:22-alpine, как на VPS)"
  # --user: под root node_modules и dist стали бы root-овыми, и git не смог бы их тронуть.
  docker run --rm --user "$(id -u):$(id -g)" -e npm_config_cache=/tmp/.npm -e HOME=/tmp \
    -v "$HOME_DIR/frontend:/app" -w /app node:22-alpine \
    sh -c "npm ci --no-audit --no-fund && npm run build" >/dev/null || die "сборка фронта упала"
  echo "$sha" > "$BUILD_STAMP"
  say "собираю образы"
  "${COMPOSE[@]}" build || die "образы не собрались"
  say "✓ фронт и образы готовы на ${sha:0:7}; если клон сдвинется — повторить build"
  ;;

freeze)
  say "== заморозка =="
  require_not_at_home
  # Плановое окно: пока на диске лежит maintenance_on, контейнерный nginx
  # отдаёт всем «обновляемся» (у нас есть обход по куке /__maint/bypass).
  # Без этого люди продолжали бы писать в базу, которую мы уже сняли дампом,
  # и эти записи потерялись бы при переезде. Метка .site-moved-home запрещает
  # деплой на VPS (deploy_prod.sh, remote_deploy.sh): иначе он поднял бы тут
  # второго бота и планировщик. Снимает её откат (cutover_rollback.sh now).
  vps "cd $REMOTE_DIR && touch deploy/nginx/maintenance_on && date -u '+%F %T UTC — переезд на домашний сервер' > .site-moved-home && $VPS_COMPOSE stop beat bot worker worker-warm worker-five-verst-user worker-parkrun" ||
    die "не удалось заморозить прод (если ssh отказал — выполнить команду руками на проде)"

  # В базу VPS пишет и дом: воркеры сбора (srs-prod) и очередь профилей. Их
  # записи после дампа пропали бы так же. Таймер синхронизации тоже на паузу:
  # посреди переезда он поднимал бы тревоги про остановленных воркеров.
  say "останавливаю домашних писателей в базу VPS: очередь профилей и воркеры сбора"
  sudo systemctl stop pm-home-sync.timer pm-site-queue.timer || die "не остановились таймеры pm-home-sync/pm-site-queue"
  for _ in $(seq 1 120); do
    systemctl is-active --quiet pm-site-queue.service || systemctl is-active --quiet pm-home-sync.service || break
    sleep 5
  done
  systemctl is-active --quiet pm-site-queue.service &&
    die "разбор очереди профилей всё ещё идёт (journalctl -u pm-site-queue) — дождаться конца и повторить freeze"
  "${WORKERS_COMPOSE[@]}" stop $WORKERS || die "воркеры сбора дома не остановились"
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://run5k.run/)
  say "фон остановлен и на VPS, и дома; сайт отдаёт $code (ждём 503 — «обновляемся»)"
  say "ВАЖНО: с этой минуты записи на проде нет — всё, что запишется, потеряется"
  ;;

db)
  require_not_at_home
  require_frozen
  t0=$(date +%s)
  stamp=$(date +%Y%m%d-%H%M%S)
  dump="saturday_runs_lk_cutover_${stamp}.dump"
  mkdir -p "$DUMP_DIR"

  # Дамп снимаем ОТСЮДА правами приложения через tailnet: root на проде не
  # нужен, а 335 МБ приезжают за ~40 с (замер 24.09.2026). Раньше здесь был
  # ssh + sudo -u postgres, и шаг упирался в пароль viewer.
  say "снимаю дамп прод-базы (правами приложения, по tailnet)"
  set -a; . "$HOME_DIR/.env"; set +a
  PROD_URL="${PROD_DATABASE_URL:-$DATABASE_URL}"
  PROD_URL="${PROD_URL/postgresql+psycopg/postgresql}"
  case "$PROD_URL" in *127.0.0.1*|*localhost*)
      die "PROD_DATABASE_URL смотрит на локальную базу — дамп снялся бы сам с себя" ;;
  esac
  pg_dump "$PROD_URL" -Fc -Z6 -f "$DUMP_DIR/$dump" || die "дамп не снялся"
  say "дамп: $(du -h "$DUMP_DIR/$dump" | cut -f1) за $(( $(date +%s) - t0 )) с"

  "${COMPOSE[@]}" up -d postgres || die "база не поднялась"
  for _ in $(seq 1 60); do "${COMPOSE[@]}" exec -T postgres pg_isready -U "${POSTGRES_USER:-saturday_runs}" >/dev/null 2>&1 && break; sleep 1; done

  # База пересоздаётся: на репетициях том уже наполнен, и pg_restore поверх
  # существующих строк сыплет конфликтами первичных ключей. Дома в этот момент
  # только копия прода — терять нечего (сайт не дома: проверено выше).
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d postgres \
    -c "drop database if exists ${POSTGRES_DB:-saturday_runs_lk} with (force)" >/dev/null || die "не удалось снести старую копию"
  "${COMPOSE[@]}" exec -T postgres psql -U "${POSTGRES_USER:-saturday_runs}" -d postgres \
    -c "create database ${POSTGRES_DB:-saturday_runs_lk} owner ${POSTGRES_USER:-saturday_runs}" >/dev/null || die "не создалась база"

  # Права из дампа не везём (--no-acl): в нём права по умолчанию висят на роли
  # postgres, которой в контейнерной базе нет, и из-за этого таблицы новых
  # миграций остались бы для report_ro закрыты, как на VPS. Права report_ro
  # целиком выдаёт скрипт роли сразу после восстановления.
  # Код возврата pg_restore не годится: он всегда ненулевой из-за двух ожидаемых
  # ошибок про pg_repack (расширения в postgres:16-alpine нет). Поэтому читаем
  # журнал: любая другая ошибка — стоп.
  say "восстанавливаю"
  rlog="$DUMP_DIR/${dump%.dump}.restore.log"
  "${COMPOSE[@]}" exec -T postgres pg_restore -U "${POSTGRES_USER:-saturday_runs}" \
    -d "${POSTGRES_DB:-saturday_runs_lk}" -j 6 --no-owner --no-acl "/dump/$dump" >"$rlog" 2>&1
  bad=$(grep '^pg_restore: error:' "$rlog" | grep -vc 'pg_repack')
  [ "$bad" = 0 ] || die "восстановление с ошибками ($bad, кроме ожидаемых про pg_repack) — см. $rlog"

  # Пароль report_ro — тот же, что в REPORT_DATABASE_URL: под ним после
  # переезда входят отчётный API и выборки для дайджеста.
  # Сайт от этой роли не зависит, поэтому сбой здесь шаг не валит.
  ro_pw="${REPORT_RO_PASSWORD:-$(python3 -c 'import os, urllib.parse as u; print(u.unquote(u.urlsplit(os.environ.get("REPORT_DATABASE_URL", "")).password or ""))')}"
  if [ -z "$ro_pw" ]; then
    say "✗ report_ro не создана: нет ни REPORT_RO_PASSWORD, ни пароля в REPORT_DATABASE_URL"
  elif ! psql_home -q -At -v ON_ERROR_STOP=1 -v report_password="$ro_pw" -f - <scripts/create_report_ro_role.sql | tail -2; then
    say "✗ report_ro не настроилась — повторить scripts/create_report_ro_role.sql руками (команда в его шапке)"
  fi

  # pg_restore статистику планировщика не везёт: без ANALYZE первые запросы
  # сайта пошли бы по плохим планам — медленная главная сразу после переезда.
  say "ANALYZE (статистика планировщика)"
  "${COMPOSE[@]}" exec -T postgres vacuumdb -U "${POSTGRES_USER:-saturday_runs}" \
    -d "${POSTGRES_DB:-saturday_runs_lk}" --analyze-only -j 6 -q || say "✗ ANALYZE не прошёл — повторить руками, сайт работать будет"

  # Прод заморожен, значит строки обязаны сойтись таблица в таблицу. Раньше
  # шаг печатал одно число и сравнивать его было не с чем.
  say "сверяю число строк с продом по всем таблицам"
  psql "$PROD_URL" -X -At -v ON_ERROR_STOP=1 <<<"$COUNTS_SQL" >"$DUMP_DIR/counts_prod_$stamp.txt" || die "не посчитал строки прода"
  psql_home -X -At -v ON_ERROR_STOP=1 <<<"$COUNTS_SQL" >"$DUMP_DIR/counts_home_$stamp.txt" || die "не посчитал строки дома"
  if ! diff -q "$DUMP_DIR/counts_prod_$stamp.txt" "$DUMP_DIR/counts_home_$stamp.txt" >/dev/null; then
    diff "$DUMP_DIR/counts_prod_$stamp.txt" "$DUMP_DIR/counts_home_$stamp.txt" | head -20 >&2
    die "строки дома не сходятся с продом (выше: < прод, > дом). Прод точно заморожен? Повторить db"
  fi
  say "✓ все $(wc -l <"$DUMP_DIR/counts_prod_$stamp.txt") таблиц сошлись, run_results: $(grep '^run_results|' "$DUMP_DIR/counts_home_$stamp.txt" | cut -d'|' -f2)"

  # Redis: в нём не только кэш. Сессии (иначе разлогинятся все), дневная
  # статистика сайта, водяной знак рассылки уведомлений, курсоры ротации —
  # переносим с VPS (backend/scripts/copy_redis_state.py). Том — с нуля: в нём
  # задачи и кэш репетиции, которые иначе выполнились и показались бы.
  say "Redis: чистый том и перенос состояния с VPS"
  src_redis="${PROD_REDIS_URL:-$REDIS_URL}"
  case "$src_redis" in *//redis:*|*@redis:*|*127.0.0.1*|*localhost*)
      die "в .env нет адреса Redis VPS (REDIS_URL=$src_redis) — задать PROD_REDIS_URL" ;;
  esac
  "${COMPOSE[@]}" rm -sf redis >/dev/null 2>&1
  docker volume rm "${HOME_PROJECT:-srs_home}_redis_data" >/dev/null 2>&1
  "${COMPOSE[@]}" up -d redis || die "redis не поднялся"
  for _ in $(seq 1 30); do "${COMPOSE[@]}" exec -T redis redis-cli ping 2>/dev/null | grep -q PONG && break; sleep 1; done
  "${COMPOSE[@]}" run --rm -T --no-deps -e SRC_REDIS_URL="$src_redis" -e DST_REDIS_URL=redis://redis:6379/0 \
    api python scripts/copy_redis_state.py </dev/null || die "состояние Redis не перенеслось"
  say "весь шаг занял $(( $(date +%s) - t0 )) с"
  ;;

start)
  # ТОЛЬКО веб: база, кэш, api, nginx, край. Ни бота, ни beat, ни воркеров —
  # у них боевые токены и общий Telegram. 24.09.2026 репетиция подняла всё
  # разом: бот-дубль три минуты дрался с боевым за getUpdates (18 конфликтов
  # в журнале прода), а beat успел поставить задачу. Обошлось — ни одного
  # апдейта дубль не обработал, наружу воркеры не сходили. Больше так нельзя:
  # фоновую часть поднимает отдельный шаг start-full, и только после mark.
  say "поднимаю ВЕБ-часть (без бота, планировщика и воркеров)"
  # Домашняя заглушка могла остаться от отката с данными (back-dump).
  rm -f "$HOME_DIR/deploy/nginx/maintenance_on"
  "${COMPOSE[@]}" up -d --build postgres redis api nginx edge certbot || die "стек не поднялся"
  mlog="$DUMP_DIR/alembic_$(date +%Y%m%d-%H%M%S).log"
  "${COMPOSE[@]}" run --rm -T api alembic upgrade head </dev/null >"$mlog" 2>&1 ||
    die "миграции упали — см. $mlog"
  tail -1 "$mlog"
  code=""
  for _ in $(seq 1 60); do
    code=$(curl -s -o /dev/null -m 5 --resolve run5k.run:443:127.0.0.1 -w '%{http_code}' https://run5k.run/health)
    [ "$code" = "200" ] && break
    sleep 2
  done
  [ "$code" = "200" ] || die "край не отдаёт health 200 (последний ответ: ${code:-нет}) — docker compose -p ${HOME_PROJECT:-srs_home} logs edge api"
  say "✓ health 200 через край"
  ;;

mark)
  # Маркер «сайт дома»: по нему разбор очереди профилей начинает писать в
  # локальную базу, а синхронизаторы перестают тянуть код с VPS. Без него
  # очередь молча продолжила бы писать в БРОШЕННУЮ базу на проде.
  # Всё, что делает этот шаг, отменяет откат: scripts/cutover_rollback.sh now
  # снимает маркер, возвращает .env, поднимает srs-prod обратно и напоминает
  # включить крон бэкапа на VPS.
  require_frozen
  ver=$(psql_home -X -At -c "select version_num from alembic_version" 2>/dev/null)
  [ -n "$ver" ] || die "домашняя база пустая или не поднята — сначала db и start"
  touch "$MARKER"
  say "маркер поставлен: $MARKER (база дома на миграции $ver)"

  # .env домашнего клона всё ещё указывает на базу VPS (по нему работали
  # воркеры до переезда). Сервисам compose адрес перекрывает, а вот ручной
  # скрипт внутри контейнера (docker compose exec api python scripts/…)
  # прочитал бы именно .env и ушёл писать на прод. Переписываем: боевой адрес
  # становится локальным, прежний остаётся под именем PROD_DATABASE_URL —
  # он нужен шагу db, чтобы снимать дамп.
  #
  # REPORT_DATABASE_URL compose НЕ перекрывает, api читает его прямо из .env.
  # На VPS это хостовый Postgres за мостом docker0 (172.17.0.1), а дома там
  # слушает только 127.0.0.1 — отчётный API упёрся бы в отказ соединения.
  # Дома база — сервис postgres в той же docker-сети.
  report_on_vps=$(grep -c '^REPORT_DATABASE_URL=.*@172\.17\.0\.1:5432/' "$HOME_DIR/.env")
  python3 - "$HOME_DIR/.env" <<'PYENV'
import pathlib, sys, re
p = pathlib.Path(sys.argv[1])
lines = p.read_text().splitlines()
out, prod_url, report_moved = [], None, False
for line in lines:
    if line.startswith("DATABASE_URL=") and "@100.93.200.8:" in line:
        prod_url = line.split("=", 1)[1]
        out.append(re.sub(r"@100\.93\.200\.8:5432", "@127.0.0.1:5433", line))
    elif line.startswith("REPORT_DATABASE_URL=") and "@172.17.0.1:5432/" in line:
        out.append(line.replace("@172.17.0.1:5432/", "@postgres:5432/"))
        report_moved = True
    else:
        out.append(line)
if prod_url and not any(l.startswith("PROD_DATABASE_URL=") for l in out):
    out.append("# Адрес прод-базы на VPS: нужен шагу db сценария переезда и откату.")
    out.append(f"PROD_DATABASE_URL={prod_url}")
p.write_text("\n".join(out) + "\n")
print("   .env: DATABASE_URL переключён на локальную базу" if prod_url else "   .env: боевой адрес уже локальный")
if report_moved:
    print("   .env: REPORT_DATABASE_URL смотрит на сервис postgres")
PYENV
  # Настройки api читает один раз при старте — без перезапуска адрес не подхватится.
  if [ "$report_on_vps" != 0 ]; then
    "${COMPOSE[@]}" restart api >/dev/null && say "api перезапущен: отчётный API ходит в домашнюю базу"
  fi
  # Старый стек воркеров (проект srs-prod) смотрит в базу на VPS по tailnet —
  # после переезда его работа уходила бы в никуда. freeze их уже остановил;
  # здесь — на случай, если их кто-то поднял.
  "${WORKERS_COMPOSE[@]}" stop 2>/dev/null | tail -2
  say "старый стек домашних воркеров остановлен — они теперь часть общего стека"

  # Очередь профилей пишет теперь в домашнюю базу (маркер), синхронизация кода
  # сверяется с домашним .deployed_sha — обоих таймеров на паузе с freeze.
  sudo systemctl start pm-site-queue.timer pm-home-sync.timer &&
    say "таймеры очереди профилей и синхронизации снова работают — уже с домашней базой" ||
    say "✗ не запустились таймеры: sudo systemctl start pm-site-queue.timer pm-home-sync.timer"

  # Бэкап базы переезжает вместе с сайтом: с маркером ночной бэкап снимает
  # домашнюю базу. Крон VPS с этой минуты лил бы в бакет дампы замороженной
  # базы; он в crontab root, выключается руками.
  backup_timer
  say "ВЫКЛЮЧИ бэкап на VPS (под root): crontab -e — закомментировать строку с pg_backup.sh"
  ;;

start-full)
  # Фоновая часть. Только после mark: без маркера очередь профилей писала бы в
  # базу VPS, а воркеры srs-prod могли бы работать параллельно с этими — два
  # сборщика с разными Redis-локами на 5verst.ru и s95.ru с одного адреса.
  [ -f "$MARKER" ] || die "нет маркера «сайт дома» — сначала mark"
  # Пока прод жив, второй бот и второй планировщик — это дубль боевых токенов и
  # двойные походы наружу. Не ответил ssh — не знаем, значит не поднимаем.
  running=$(vps "cd $REMOTE_DIR && docker compose ps --status running --services") ||
    die "не удалось спросить VPS, живы ли bot/beat — без ответа фон не поднимаю"
  grep -qxE "bot|beat" <<<"$running" &&
    die "на проде ещё живы bot/beat — сначала freeze, иначе получим два бота на один токен"
  # Файл расписания beat живёт в примонтированном backend/. От репетиции
  # 24.09.2026 там остались отметки «последний запуск» — со старым файлом beat
  # разом выполнил бы всё «пропущенное» за эти дни, включая еженедельную
  # рассылку «движение в рейтингах» посреди недели. Свежий файл = следующие
  # запуски по расписанию.
  rm -f "$HOME_DIR"/backend/celerybeat-schedule* && say "расписание beat — с чистого листа"
  say "поднимаю фоновую часть: планировщик, воркеры, бот"
  "${COMPOSE[@]}" up -d --build || die "фоновая часть не поднялась"
  "${COMPOSE[@]}" ps --format '{{.Service}}: {{.Status}}'
  ;;

warm)
  say "прогреваю кэши ДО перевода DNS (холодная главная считается ~34 с)"
  for u in /api/portal/home /api/locations/index /api/fastest /api/location-records /api/protocol/week; do
    printf '  %-26s' "$u"
    curl -s -o /dev/null -m 180 --resolve run5k.run:443:127.0.0.1 \
      -w '%{http_code} за %{time_total}s\n' "https://run5k.run$u"
  done
  ;;

verify)
  say "== дом до переключения DNS =="
  for u in / /health /api/locations/index /api/portal/home; do
    printf '  %-24s' "$u"
    curl -s -o /dev/null -m 120 --resolve run5k.run:443:127.0.0.1 \
      -w '%{http_code} за %{time_total}s\n' "https://run5k.run$u"
  done
  # Остальные имена сертификата: www и app — редирект на апекс, grafana — заглушка.
  for h in www.run5k.run app.run5k.run grafana.run5k.run; do
    printf '  %-24s' "https://$h/"
    curl -s -o /dev/null -m 20 --resolve "$h:443:127.0.0.1" -w '%{http_code} %{redirect_url}\n' "https://$h/"
  done
  # Край должен слушать все адреса, а не только 127.0.0.1 (так была репетиция).
  bind=$(docker port "$("${COMPOSE[@]}" ps -q edge)" 443/tcp 2>/dev/null | head -1)
  case "$bind" in 0.0.0.0:*|'[::]:'*) say "✓ край слушает наружу ($bind)" ;;
    *) say "✗ край слушает $bind — снаружи сайта не будет: unset EDGE_BIND и cutover_to_home.sh start" ;; esac
  say "теперь DNS: A у run5k.run, www, app и grafana → $HOME_IP; AAAA у run5k.run и www удалить (дома IPv6 нет)"
  say "  все четыре имени — обязательно: certbot продлевает их сертификаты отсюда"
  ;;

outside)
  say "== снаружи (глазами прода) =="
  for h in $HOSTS; do
    vps "curl -s -o /dev/null -m 15 -w 'https://$h/ %{http_code} за %{time_total}s\n' https://$h/ --resolve $h:443:$HOME_IP"
    printf '  A: %s  AAAA (должно быть пусто): %s\n' "$(dig +short A "$h" | tr '\n' ' ')" "$(dig +short AAAA "$h" | tr '\n' ' ')"
  done
  ;;

*)
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -24
  ;;
esac
